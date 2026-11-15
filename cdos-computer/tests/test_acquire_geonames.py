"""GeoNames through the pipeline: a source of several files, pinned before use (GeoNames publishes no checksums and
regenerates its files daily), codes resolved to names through the other files, places that share a name kept apart
by ENTITY=gn:, and populations left undated.  Fixture files only, fetched from file:// URLs; nothing writes the
master."""

import hashlib
import json
import sys
import tempfile
import unittest
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from computer.acquire import AcquireError                                                    # noqa: E402
from computer.acquire.fetch import FetchRejected                                             # noqa: E402
from computer.acquire.parsers import parse_geonames                                          # noqa: E402
from computer.acquire.pipeline import Acquirer                                               # noqa: E402

T = "\t"


def country(iso, name, capital, area, pop, cont, cur, gid, neighbours):
    c = [iso, iso + "X", "1", iso, name, capital, area, pop, cont, "." + iso.lower(), "CUR", cur, "1", "", "",
         "xx", gid, neighbours, ""]
    return T.join(c)


COUNTRIES = "\n".join([
    "#ISO\tISO3\tISO-Numeric\tfips\tCountry\tCapital\tArea(in sq km)\tPopulation\tContinent\t...",
    country("FR", "France", "Paris", "547030", "66987244", "EU", "Euro", "3017382", "DE,XX"),
    country("DE", "Germany", "Berlin", "357021", "83000000", "EU", "Euro", "2921044", "FR"),
    country("US", "United States", "Washington", "9629091", "327167434", "NA", "Dollar", "6252001", ""),
    country("ZZ", "Nowhere", "", "0", "0", "QQ", "", "1", ""),
]) + "\n"
ADMIN1 = "\n".join(["FR.11\tÎle-de-France\tIle-de-France\t3012874", "US.IL\tIllinois\tIllinois\t4896861",
                    "US.MA\tMassachusetts\tMassachusetts\t6254926", "DE.16\tBerlin\tBerlin\t2950157"]) + "\n"


def city(gid, name, lat, lon, fclass, cc, adm1, pop, elev, tz):
    return T.join([gid, name, name, "", lat, lon, fclass, "PPL", cc, "", adm1, "", "", "", pop, elev, "", tz,
                   "2024-01-01"])


CITIES = "\n".join([
    city("2988507", "Paris", "48.85341", "2.3488", "P", "FR", "11", "2138551", "", "Europe/Paris"),
    city("2950159", "Berlin", "52.52437", "13.41053", "P", "DE", "16", "3426354", "74", "Europe/Berlin"),
    city("4250542", "Springfield", "39.80172", "-89.64371", "P", "US", "IL", "116565", "180", "America/Chicago"),
    city("4951788", "Springfield", "42.10148", "-72.58981", "P", "US", "MA", "155929", "21", "America/New_York"),
    city("2996944", "Lyon", "45.74846", "4.84671", "P", "FR", "84", "522969", "", "Europe/Paris"),   # admin1 FR.84?
    city("9999999", "Ghosttown", "0", "0", "P", "XX", "", "10", "", ""),                            # country XX?
    city("2988506", "Paris airport", "49.0", "2.5", "S", "FR", "11", "0", "", "Europe/Paris"),     # a spot, not P
    city("1234567", "Bigtown", "1", "1", "P", "US", "IL", "lots", "", ""),                          # population?
    "1\tbroken line",                                                                                # 2 columns
]) + "\n"
RELATIONS = ('{ is_a = "IS_A", capital = "CAPITAL", area = "AREA", population = "POPULATION", continent = '
             '"CONTINENT", currency = "CURRENCY", shares_border_with = "SHARES_BORDER_WITH", located_in = '
             '"LOCATED_IN", elevation = "ELEVATION", located_at = "LOCATED_AT", timezone = "TIMEZONE" }')


def row(*c):
    return T.join((list(c) + [""] * 11)[:11])


MASTER = "\n".join([
    "# fixture master",
    row("AREA", "IS_FUNCTIONAL", "S", "TRUE", "", "0", "0", "MANUAL", "schema", "1.0"),
    row("CAPITAL", "IS_FUNCTIONAL", "S", "TRUE", "", "0", "0", "MANUAL", "schema", "1.0"),
    row("POPULATION", "IS_FUNCTIONAL", "S", "TRUE", "", "0", "0", "MANUAL", "schema", "1.0"),
    row("France", "CAPITAL", "S", "Paris", "", "1789", "0", "WIKIDATA", "Q142/P36", "1.0"),
    row("France", "CAPITAL", "S", "Paris", "", "0", "0", "WIKIDATA", "Q142/P36", "1.0"),
    row("France", "AREA", "N", "643801", "KM2", "0", "0", "WIKIDATA", "Q142/P2046", "1.0"),
    row("Germany", "POPULATION", "N", "83200000", "", "2023", "0", "WIKIDATA", "Q183/P1082", "1.0"),
]) + "\n"


def sha(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


class Counting:
    """urllib for file:// URLs, counting what is opened."""

    def __init__(self):
        self.opened = []

    def __call__(self, req):
        self.opened.append(req.full_url)
        return urllib.request.urlopen(req, timeout=10)


class GeoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        src = self.dir / "upstream"
        src.mkdir()
        (src / "countryInfo.txt").write_text(COUNTRIES, encoding="utf-8")
        (src / "admin1CodesASCII.txt").write_text(ADMIN1, encoding="utf-8")
        with zipfile.ZipFile(src / "cities15000.zip", "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("cities15000.txt", CITIES)
        self.files = {"countries": src / "countryInfo.txt", "admin1": src / "admin1CodesASCII.txt",
                      "cities": src / "cities15000.zip"}
        self.master = self.dir / "corpus" / "corpus.pf"
        self.master.parent.mkdir()
        self.master.write_text(MASTER)
        self.root = self.dir / "acquire"
        self.root.mkdir()
        self.opener = Counting()

    def tearDown(self):
        self.tmp.cleanup()

    def write_registry(self, shas: dict | None = None):
        shas = shas or {}
        inputs = ", ".join(f'{n} = {{ url = "{p.as_uri()}", sha256 = "{shas.get(n, "")}" }}'
                           for n, p in self.files.items())
        (self.root / "sources.toml").write_text(
            "[source.geonames]\n"
            'source_type = "GEONAMES"\nlicence = "CC-BY-4.0"\nattribution = "GeoNames (test fixture)"\n'
            f"inputs = {{ {inputs} }}\nparser = \"geonames\"\nmax_bytes = 1000000\nrelations = {RELATIONS}\n"
            'approved_by = "tester"\napproved_at = "2026-11-15"\n')

    def acq(self):
        return Acquirer(self.root, self.master, opener=self.opener)

    def pin_and_approve(self):
        self.write_registry()
        pinned = self.acq().fetcher.pin(self.acq_source())
        self.write_registry({n: r["sha256"] for n, r in pinned.items()})
        return pinned

    def acq_source(self):
        from computer.acquire import registry
        return registry.get(self.root / "sources.toml", "geonames")

    def staged(self, r):
        d = self.root / "staging" / "geonames" / r["run"]
        return [x.split(T) for x in (d / "delta.pf").read_text().splitlines() if not x.startswith("#")]

    def jsonl(self, r, name):
        d = self.root / "staging" / "geonames" / r["run"]
        return [json.loads(x) for x in (d / name).read_text().splitlines() if x.strip()]


class TestPinning(GeoCase):
    def test_an_unpinned_source_is_refused_and_nothing_is_fetched(self):
        self.write_registry()
        with self.assertRaisesRegex(FetchRejected, "not pinned"):
            self.acq().run("geonames")
        self.assertEqual(self.opener.opened, [])

    def test_pin_fetches_without_parsing_and_the_approved_files_are_the_ones_used(self):
        pinned = self.pin_and_approve()
        self.assertEqual({n: r["sha256"] for n, r in pinned.items()}, {n: sha(p) for n, p in self.files.items()})
        self.assertFalse((self.root / "staging").exists())               # pin parses nothing
        fetched = len(self.opener.opened)
        self.files["cities"].write_bytes(b"changed upstream the next day")  # GeoNames regenerates daily
        r = self.acq().run("geonames")
        self.assertEqual(len(self.opener.opened), fetched)                # the pinned files, not fetched again
        self.assertEqual({n: f["sha256"] for n, f in r["input"]["files"].items()},
                         {n: x["sha256"] for n, x in pinned.items()})
        self.assertIn("pinned", [e["event"] for e in self.acq().journal.records()])

    def test_a_wrong_approval_is_quarantined(self):
        self.write_registry()
        self.acq().fetcher.pin(self.acq_source())
        self.write_registry({"countries": sha(self.files["countries"]), "admin1": sha(self.files["admin1"]),
                             "cities": "sha256:" + "0" * 64})
        with self.assertRaisesRegex(FetchRejected, "doesn't match its sha256"):
            self.acq().run("geonames")
        self.assertTrue(list((self.root / "quarantine" / "geonames" / "cities").glob("cities15000.zip.*")))


class TestRows(GeoCase):
    def setUp(self):
        super().setUp()
        self.pin_and_approve()
        self.report = self.acq().run("geonames")
        self.rows = {(s[0], s[1], s[3], s[4], s[5], s[10]) for s in self.staged(self.report)}

    def test_the_counts_reconcile_and_every_rejection_has_its_reason(self):
        r = self.report
        self.assertTrue(r["reconciled"])
        self.assertEqual(r["rejected_by"], {
            "parse: columns": 1, "parse: not-a-place": 1, "parse: unknown-admin1": 1, "parse: unknown-continent": 1,
            "parse: unknown-country": 2, "validate: N value isn't a number": 1})

    def test_countries(self):
        self.assertIn(("Germany", "SHARES_BORDER_WITH", "France", "", "0", "ENTITY=gn:2921044"), self.rows)
        self.assertIn(("France", "AREA", "547030", "KM2", "0", "ENTITY=gn:3017382"), self.rows)
        self.assertIn(("Germany", "CONTINENT", "Europe", "", "0", "ENTITY=gn:2921044"), self.rows)
        self.assertIn(("Germany", "CURRENCY", "Euro", "", "0", "ENTITY=gn:2921044"), self.rows)
        self.assertNotIn("Nowhere", {s[0] for s in self.rows if s[1] in ("AREA", "POPULATION")})   # 0 = unknown

    def test_cities_resolve_their_codes_to_names(self):
        self.assertIn(("Paris", "LOCATED_IN", "France", "", "0", "ENTITY=gn:2988507"), self.rows)
        self.assertIn(("Paris", "LOCATED_IN", "Île-de-France", "", "0", "ENTITY=gn:2988507"), self.rows)
        self.assertIn(("Paris", "LOCATED_AT", "48.85341,2.3488", "", "0", "ENTITY=gn:2988507"), self.rows)
        self.assertIn(("Berlin", "ELEVATION", "74", "M", "0", "ENTITY=gn:2950159"), self.rows)
        self.assertIn(("Paris", "TIMEZONE", "Europe/Paris", "", "0", "ENTITY=gn:2988507"), self.rows)
        self.assertFalse(any(s[0] == "Paris" and s[1] == "ELEVATION" for s in self.rows))    # none given, none made

    def test_populations_are_undated(self):
        pops = [s for s in self.rows if s[1] == "POPULATION"]
        self.assertTrue(pops)
        self.assertEqual({s[4] for s in pops}, {"0"})

    def test_two_places_with_one_name_stay_two(self):
        spring = {s[5] for s in self.rows if s[0] == "Springfield" and s[1] == "POPULATION"}
        self.assertEqual(spring, {"ENTITY=gn:4250542", "ENTITY=gn:4951788"})
        self.assertEqual(self.report["names_with_several_entities"], 1)
        self.assertEqual(self.report["names_with_several_entities_examples"], [["SPRINGFIELD", 2]])

    def test_what_wikidata_already_says_is_a_duplicate_and_a_different_area_is_a_conflict(self):
        self.assertNotIn(("France", "CAPITAL", "Paris", "", "0", "ENTITY=gn:3017382"), self.rows)
        self.assertGreaterEqual(self.report["duplicates_of_rows_without_entity"], 1)
        conflicts = {(c["subject"], c["relation"], c["value"]) for c in self.jsonl(self.report, "conflicts.jsonl")}
        self.assertIn(("FRANCE", "AREA", "547030"), conflicts)
        self.assertNotIn("POPULATION", {c[1] for c in conflicts if c[0] == "GERMANY"})   # master's is dated

    def test_a_rerun_is_a_no_op_and_the_master_is_untouched(self):
        before = self.master.read_bytes()
        self.assertTrue(self.acq().run("geonames").get("unchanged"))
        self.assertEqual(self.master.read_bytes(), before)
        with self.assertRaisesRegex(AcquireError, "corpus.integrate is off"):
            self.acq().integrate("geonames")


class TestLimits(GeoCase):
    def test_a_zip_that_expands_too_far_is_refused(self):
        big = self.dir / "big.zip"
        with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("cities15000.txt", "x" * 2_000_000)
        with self.assertRaisesRegex(AcquireError, "decompression bomb"):
            list(parse_geonames({**self.files, "cities": big}, cap=100_000))

    def test_a_zip_with_other_contents_is_refused(self):
        odd = self.dir / "odd.zip"
        with zipfile.ZipFile(odd, "w") as z:
            z.writestr("a.txt", "x")
            z.writestr("b.txt", "y")
        with self.assertRaisesRegex(AcquireError, "exactly one .txt"):
            list(parse_geonames({**self.files, "cities": odd}))

    def test_all_three_inputs_are_needed(self):
        with self.assertRaisesRegex(AcquireError, "missing \\['admin1'\\]"):
            list(parse_geonames({"countries": self.files["countries"], "cities": self.files["cities"]}))


if __name__ == "__main__":
    unittest.main()
