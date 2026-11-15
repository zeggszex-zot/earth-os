"""Wikidata from the API (computer/acquire/wikidata.py, parsers.parse_wikidata_snapshot), against a fake Wikidata:
nothing here touches the network.  The collection asks one batch at a time, at most `batch` ids, with maxlag and the
approved User-Agent; waits as long as the server says; resumes without asking again; and its snapshot is approved and
parsed like any download.  Rows name things (D3), and an item linked one to one to a staged GeoNames place carries
that place's ENTITY=."""

import gzip
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from computer.acquire import AcquireError, registry, wikidata                                 # noqa: E402
from computer.acquire.fetch import FetchInterrupted                                          # noqa: E402
from computer.acquire.parsers import parse_wikidata_snapshot                                 # noqa: E402
from computer.acquire.pipeline import Acquirer                                               # noqa: E402

T = "\t"
API, SPARQL = "https://www.wikidata.org/w/api.php", "https://query.wikidata.org/sparql"
UA = "CDOS-acquire/1 (https://example.org/test)"


def item(q):
    return {"value": {"entity-type": "item", "id": q}, "type": "wikibase-entityid"}


def stmt(value, rank="normal", **quals):
    snak = {"snaktype": "value", "datavalue": value} if value else {"snaktype": "somevalue"}
    return {"mainsnak": snak, "rank": rank,
            "qualifiers": {p: [{"snaktype": "value", "datavalue": {"value": v}}] for p, v in quals.items()}}


def qty(amount, unit="1"):
    return {"value": {"amount": amount, "unit": unit if unit == "1" else f"http://www.wikidata.org/entity/{unit}"},
            "type": "quantity"}


def time_(t, precision=11, calendar="Q1985727"):
    return {"value": {"time": t, "precision": precision,
                      "calendarmodel": f"http://www.wikidata.org/entity/{calendar}"}, "type": "time"}


def year(y):
    return {"time": f"+{y}-00-00T00:00:00Z", "precision": 9}


def coord(lat, lon, globe="Q2"):
    return {"value": {"latitude": lat, "longitude": lon, "globe": f"http://www.wikidata.org/entity/{globe}"},
            "type": "globecoordinate"}


def ent(q, label, claims, desc="", sitelinks=0, rev=1000):
    return {"id": q, "lastrevid": rev, "modified": "2026-11-01T00:00:00Z",
            "labels": {"en": {"language": "en", "value": label}} if label else {},
            "descriptions": {"en": {"language": "en", "value": desc}} if desc else {},
            "claims": claims, "sitelinks": {f"w{i}": {} for i in range(sitelinks)}}


WORLD = {
    "Q142": ent("Q142", "France", {
        "P31": [stmt(item("Q6256"))],
        "P36": [stmt(item("Q90"), "preferred", P580=year(1944)), stmt(item("Q1"), "deprecated")],
        "P1082": [stmt(qty("+68000000"), P585=year(2024))],
        "P2046": [stmt(qty("+643801", "Q712226"))],
        "P17": [stmt(item("Q142"))],                                    # itself: not a row
        "P1566": [stmt({"value": "3017382", "type": "string"})],
        "P2044": [stmt(None)],                                          # an unknown value
    }, desc="country in Western Europe", sitelinks=3),
    "Q90": ent("Q90", "Paris", {"P17": [stmt(item("Q142"))], "P625": [stmt(coord(48.8566, 2.3522))],
                                "P1566": [stmt({"value": "2988507", "type": "string"})]}),
    "Q405": ent("Q405", "Moon", {"P625": [stmt(coord(0, 0, "Q405"))]}),
    "Q556": ent("Q556", "hydrogen", {"P2067": [stmt(qty("+1.008", "Q483261"))],
                                     "P2101": [stmt(qty("-259.14", "Q25267"))],
                                     "P2054": [stmt(qty("+0.08987", "Q99999"))]}),     # an unknown unit
    "Q937": ent("Q937", "Albert Einstein", {
        "P569": [stmt(time_("+1879-03-14T00:00:00Z"))],
        "P27": [stmt(item("Q183"), P582=year(1896))],
        "P106": [stmt(item("Q777"))],                                   # an item with no English label
        "P571": [stmt(time_("-0753-00-00T00:00:00Z", 9))],
        "P576": [stmt(time_("+1500-03-01T00:00:00Z", 11, "Q1985786"))]}),
    "Q999": ent("Q999", "", {"P31": [stmt(item("Q6256"))]}),
    "Q78": ent("Q78", "Basel", {"P31": [stmt(item("Q515"))], "P1566": [stmt({"value": "5555", "type": "string"})]}),
    "Q79": ent("Q79", "Basle", {"P1566": [stmt({"value": "5555", "type": "string"})]}),
    # labels only
    "Q6256": ent("Q6256", "country", {}), "Q515": ent("Q515", "city", {}), "Q777": ent("Q777", "", {}), "Q1": ent("Q1", "universe", {}), "Q183": ent("Q183", "Germany", {}),
    "Q712226": ent("Q712226", "square kilometre", {}), "Q483261": ent("Q483261", "dalton", {}),
    "Q25267": ent("Q25267", "degree Celsius", {}), "Q99999": ent("Q99999", "furlong per fortnight", {}),
}
REDIRECTS = {"Q77": "Q78"}
LINKS = {"3017382": ["Q142"], "2988507": ["Q90"], "5555": ["Q78", "Q79"]}

MASTER_ROWS = [
    ("France", "CAPITAL", "S", "Q90", "Q142/P36"), ("hydrogen", "ATOMIC_MASS", "N", "1.008", "Q556/P2067"),
    ("Q937", "BORN", "D", "1879-03-14", "Q937/P569"), ("Moon", "LOCATED_AT", "S", "0,0", "Q405/P625"),
    ("Q999", "IS_A", "S", "Q6256", "Q999/P31"), ("Basel", "IS_A", "S", "Q515", "Q77/P31"),
    ("Earth", "DEFINITION", "S", "third planet", "Q2/description"),
]
RELATIONS = ('{ P31 = "IS_A", P36 = "CAPITAL", P1082 = "POPULATION", P2046 = "AREA", P17 = "LOCATED_IN", '
             'P625 = "LOCATED_AT", P2067 = "MASS", P2067_atomic = "ATOMIC_MASS", P2101 = "MELTING_POINT", '
             'P2054 = "DENSITY", P569 = "BORN", P27 = "CITIZENSHIP", P571 = "FOUNDED", P576 = "DISSOLVED", '
             'P2044 = "ELEVATION", P106 = "OCCUPATION", description = "DEFINITION", sitelinks = "SITELINKS" }')


class Resp:
    def __init__(self, body: bytes, headers=None, gz=True):
        self.headers = dict(headers or {})
        if gz:
            body = gzip.compress(body)
            self.headers["Content-Encoding"] = "gzip"
        self.body, self.status = io.BytesIO(body), 200

    def read(self, n=-1):
        return self.body.read(n)

    def close(self):
        pass


class FakeWikidata:
    """Answers wbgetentities and the P1566 query; `script` is a list of failures to give first."""

    def __init__(self):
        self.asked, self.headers, self.script, self.starts = [], [], [], []
        self.clock = 0.0

    def __call__(self, req):
        self.starts.append(self.clock)
        self.headers.append(dict(req.header_items()))
        if self.script:
            f = self.script.pop(0)
            if f == "maxlag":
                return Resp(json.dumps({"error": {"code": "maxlag", "info": "lagged"}}).encode(), {"Retry-After": "7"})
            if f == "429":
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
            if f == "reset":
                raise ConnectionResetError("reset by peer")
        if req.full_url.startswith(SPARQL):
            q = urllib.parse.parse_qs(req.data.decode())["query"][0]
            gns = [g.strip('"') for g in q.split("VALUES ?gn {")[1].split("}")[0].split()]
            self.asked.append(("sparql", gns))
            b = [{"item": {"value": f"http://www.wikidata.org/entity/{x}"}, "gn": {"value": g}}
                 for g in gns for x in LINKS.get(g, [])]
            return Resp(json.dumps({"results": {"bindings": b}}).encode())
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))
        ids = q["ids"].split("|")
        self.asked.append((q["props"], ids, q.get("maxlag")))
        out = {}
        for i in ids:
            j = REDIRECTS.get(i, i)
            if j in WORLD:
                e = dict(WORLD[j])
                if j != i:
                    e["redirects"] = {"from": i, "to": j}
                if q["props"] == "labels":
                    e = {"id": j, "labels": e["labels"]}
                out[j] = e
            else:
                out[i] = {"id": i, "missing": ""}
        return Resp(json.dumps({"entities": out}).encode())


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.root, self.master = d / "acquire", d / "corpus.pf"
        self.root.mkdir()
        self.master.write_text("# master\n" + "".join(T.join([s, r, k, v, "", "0", "0", "WIKIDATA", sid, "1.0", ""])
                                                      + "\n" for s, r, k, v, sid in MASTER_ROWS)
                               + T.join(["Paris", "TWINNED_WITH", "S", "Rome", "", "0", "0", "WIKIDATA", "manual-3",
                                         "1.0", ""]) + "\n")
        run = self.root / "staging" / "geonames" / "20261115T000000Z-0001"
        run.mkdir(parents=True)
        (run / "report.json").write_text(json.dumps({"run": run.name}))
        (run / "delta.pf").write_text("France\tIS_A\tS\tcountry\t\t0\t0\tGEONAMES\t3017382\t1.0\tENTITY=gn:3017382\n"
                                      "Paris\tIS_A\tS\tcity\t\t0\t0\tGEONAMES\t2988507\t1.0\tENTITY=gn:2988507\n")
        (run / "held.pf").write_text("Basel\tIS_A\tS\tcity\t\t0\t0\tGEONAMES\t5555\t1.0\tENTITY=gn:5555\n")
        self.fake = FakeWikidata()
        self.slept = []
        self.write_registry()
        self._links = wikidata.LINKS_PER_QUERY
        wikidata.LINKS_PER_QUERY = 2

    def tearDown(self):
        wikidata.LINKS_PER_QUERY = self._links
        self.tmp.cleanup()

    def write_registry(self, sha="", ua=UA, batch=2):
        (self.root / "sources.toml").write_text(
            "[source.wikidata]\n"
            'source_type = "WIKIDATA"\nlicence = "CC0-1.0"\nattribution = "Wikidata (test)"\n'
            'parser = "wikidata_snapshot"\nmax_bytes = 10_000_000\n'
            f'inputs = {{ snapshot = {{ url = "{(self.root / "collect" / "wikidata" / "snapshot.jsonl.gz").as_uri()}", '
            f'sha256 = "{sha}" }} }}\n'
            f"relations = {RELATIONS}\n"
            'replaces = { source_type = "WIKIDATA", source_id = "^Q[0-9]+/" }\n'
            f'collect = {{ api = "{API}", sparql = "{SPARQL}", user_agent = "{ua}", batch = {batch}, maxlag = 5, '
            'geonames = "geonames", legacy = { source_type = "WIKIDATA", source_id = "^Q[0-9]+/" } }\n'
            'approved_by = "test"\napproved_at = "2026-11-15"\n')

    def sleep(self, s):
        self.slept.append(s)
        self.fake.clock += s

    def collector(self):
        src = registry.get(self.root / "sources.toml", "wikidata")
        acq = Acquirer(self.root, self.master)
        return wikidata.Collector(self.root, src, self.master, acq.runs, acq.journal, acq.fetcher.keep, self.fake,
                                  clock=lambda: self.fake.clock, sleep=self.sleep, log=lambda m: None)

    def rows(self, snapshot):
        out = list(parse_wikidata_snapshot({"snapshot": snapshot}))
        return [c for _, c, _, _ in out if c], [(why.split(":")[0], raw) for _, c, why, raw in out if c is None]


class TestRequests(Case):
    def test_one_batch_at_a_time_within_the_limits(self):
        out = self.collector().collect()
        ents = [a for a in self.fake.asked if a[0] != "sparql"]
        self.assertTrue(ents)
        for a in ents:
            self.assertLessEqual(len(a[1]), 2)                  # the configured batch
            self.assertEqual(a[2], "5")                         # maxlag on every API request
        for h in self.fake.headers:
            self.assertEqual(h["User-agent"], UA)
        starts = self.fake.starts
        self.assertTrue(all(b - a >= wikidata.MIN_INTERVAL for a, b in zip(starts, starts[1:])), starts)
        self.assertEqual(out["counts"]["geonames_ids_with_several_items"], 1)

    def test_the_selection(self):
        c = self.collector()
        plan = c.plan()
        self.assertEqual(plan["legacy"], ["Q2", "Q77", "Q142", "Q405", "Q556", "Q937", "Q999"])   # not manual-3
        self.assertEqual(plan["geonames"], ["5555", "2988507", "3017382"])                         # held ones too
        links = c.links(plan)
        self.assertEqual(c.entity_ids(plan, links), ["Q2", "Q77", "Q90", "Q142", "Q405", "Q556", "Q937", "Q999"])

    def test_maxlag_waits_as_told_and_failures_back_off(self):
        c = self.collector()
        plan = c.plan()
        ids = c.entity_ids(plan, c.links(plan))
        self.slept.clear()
        n = len(self.fake.asked)
        self.fake.script = ["maxlag", "429", "reset"]
        c.entities(ids)
        self.assertEqual([s for s in self.slept if s > wikidata.MIN_INTERVAL][:3], [7.0, 10.0, 20.0])
        self.assertEqual(self.fake.asked[n][1], ids[:2])        # the same batch, asked again until answered
        self.assertEqual(len(self.fake.asked) - n, (len(ids) + 1) // 2)

    def test_too_many_failures_stop_and_the_next_collect_resumes(self):
        c = self.collector()
        plan = c.plan()
        ids = c.entity_ids(plan, c.links(plan))
        c.entities(ids[:2])                                      # the first batch is on disk
        done = len(self.fake.asked)
        self.fake.script = ["429"] * wikidata.MAX_TRIES
        with self.assertRaises(FetchInterrupted):
            self.collector().collect()
        self.fake.script = []
        before = len(self.fake.asked)
        out = self.collector().collect()
        asked_again = [a for a in self.fake.asked[before:] if a[0] == "sparql" or a[1][:2] == ["Q2", "Q77"]]
        self.assertEqual(asked_again, [])                       # finished batches aren't asked again
        self.assertGreater(done, 0)
        self.assertTrue(Path(out["snapshot"]).exists())

    def test_a_changed_master_needs_a_new_collection(self):
        self.collector().plan()
        with open(self.master, "a") as fh:
            fh.write(T.join(["Rome", "IS_A", "S", "Q515", "", "0", "0", "WIKIDATA", "Q220/P31", "1.0", ""]) + "\n")
        with self.assertRaisesRegex(AcquireError, "planned from other inputs"):
            self.collector().plan()
        self.assertIn("Q220", self.collector().plan(new=True)["legacy"])

    def test_the_same_answers_give_the_same_snapshot(self):
        a = self.collector().collect()
        self.setUp_again()
        b = self.collector().collect()
        self.assertEqual(a["sha256"], b["sha256"])

    def setUp_again(self):
        self.tearDown()
        self.setUp()


class TestRows(Case):
    def setUp(self):
        super().setUp()
        self.out = self.collector().collect()
        self.good, self.bad = self.rows(self.out["snapshot"])
        self.by = {(c[0], c[1]): c for c in self.good}

    def test_names_not_ids(self):
        self.assertEqual(self.by[("France", "P36")][3], "Paris")
        self.assertEqual(self.by[("France", "P31")][3], "country")
        self.assertEqual(self.by[("Albert Einstein", "P27")][3:7], ["Germany", "", "0", "1896"])
        for c in self.good:
            self.assertNotRegex(c[0], r"^Q[0-9]+$")
            self.assertNotRegex(c[3], r"^Q[0-9]+$")
            self.assertNotIn("WD_Q", c[4])

    def test_values_in_the_legacy_shape(self):
        self.assertEqual(self.by[("France", "P2046")][2:5], ["N", "643801", "KM2"])
        self.assertEqual(self.by[("France", "P1082")][3:6], ["68000000", "", "2024"])
        self.assertEqual(self.by[("France", "P36")][5], "1944")
        self.assertEqual(self.by[("hydrogen", "P2067_atomic")][3:5], ["1.008", "U"])
        self.assertEqual(self.by[("hydrogen", "P2101")][3:5], ["-259.14", "C"])
        self.assertEqual(self.by[("Albert Einstein", "P569")][2:4], ["D", "1879-03-14"])
        self.assertEqual(self.by[("Paris", "P625")][3], "48.8566,2.3522")
        self.assertEqual(self.by[("France", "description")][3], "country in Western Europe")
        self.assertEqual(self.by[("France", "sitelinks")][3], "3")
        self.assertEqual(self.by[("France", "P36")][8], "Q142/P36")

    def test_what_is_refused_and_why(self):
        why = {w for w, _ in self.bad}
        for w in ("deprecated", "self-reference", "no-value", "globe", "unit", "no-label", "time-bce", "time-julian"):
            self.assertIn(w, why)
        self.assertNotIn(("France", "P17"), self.by)
        self.assertNotIn(("Albert Einstein", "P106"), self.by)            # Q777 has no English label
        self.assertFalse([c for c in self.good if c[0] == "Moon"])

    def test_entities_linked_to_geonames(self):
        self.assertEqual(self.by[("France", "P36")][10], "ENTITY=gn:3017382")
        self.assertEqual(self.by[("Paris", "P625")][10], "ENTITY=gn:2988507")
        self.assertEqual(self.by[("hydrogen", "P2101")][10], "ENTITY=wd:Q556")
        basel = [c for c in self.good if c[0] == "Basel"]                  # 5555 names two items: no link
        self.assertTrue(basel and all(c[10] == "ENTITY=wd:Q78" for c in basel))

    def test_each_entity_says_its_revision(self):
        with gzip.open(self.out["snapshot"], "rt") as fh:
            ents = [json.loads(x) for x in fh if '"type": "entity"' in x]
        self.assertTrue(ents and all(e["rev"] == 1000 for e in ents))

    def test_a_wrong_unit_id_refuses_the_snapshot(self):
        from computer.acquire import parsers
        saved = dict(parsers.WIKIDATA_UNITS)
        parsers.WIKIDATA_UNITS["Q712226"] = ("M2", "square metre")
        try:
            with self.assertRaisesRegex(AcquireError, "WIKIDATA_UNITS is wrong"):
                self.rows(self.out["snapshot"])
        finally:
            parsers.WIKIDATA_UNITS.clear()
            parsers.WIKIDATA_UNITS.update(saved)


class TestApprovedLikeAnyDownload(Case):
    def test_pin_approve_run(self):
        self.collector().collect()
        acq = Acquirer(self.root, self.master)
        with self.assertRaisesRegex(AcquireError, "not pinned"):
            acq.run("wikidata")
        pinned = acq.fetcher.pin(registry.get(self.root / "sources.toml", "wikidata"))
        self.write_registry(pinned["snapshot"]["sha256"])
        rep = Acquirer(self.root, self.master).run("wikidata")
        d = self.root / "staging" / "wikidata" / rep["run"]
        rows = [x.split(T) for x in (d / "delta.pf").read_text().splitlines() if not x.startswith("#")]
        self.assertIn(["France", "CAPITAL", "S", "Paris"], [r[:4] for r in rows])
        self.assertIn(["hydrogen", "ATOMIC_MASS", "N", "1.008"], [r[:4] for r in rows])
        self.assertFalse([r for r in rows if r[1] in ("P1566",) or r[3].startswith("Q")])
        before = self.master.read_bytes()
        self.assertEqual(self.master.read_bytes(), before)


class TestWhereItWrites(Case):
    def test_only_inside_its_collection(self):
        src = registry.get(self.root / "sources.toml", "wikidata")
        keep = Acquirer(self.root, self.master).fetcher.keep
        for rel in ("../../corpus.pf", "/etc/x", "../wikidata2/plan.json"):
            with self.subTest(rel), self.assertRaises(AcquireError):
                keep(src, rel, b"x")
        self.assertTrue(keep(src, "links/00000.json.gz", b"x").is_file())


class TestTheCollectTable(unittest.TestCase):
    def check(self, **change):
        col = {"api": API, "sparql": SPARQL, "user_agent": UA, "batch": 50, "maxlag": 5, "geonames": "geonames",
               "legacy": {"source_type": "WIKIDATA", "source_id": "^Q[0-9]+/"}}
        col.update(change)
        d = {"source_type": "WIKIDATA", "licence": "CC0-1.0", "attribution": "x", "parser": "wikidata_snapshot",
             "relations": {"P31": "IS_A"}, "max_bytes": 1, "approved_by": "x", "approved_at": "x",
             "inputs": {"snapshot": {"url": "file:///x", "sha256": ""}}, "collect": col}
        return registry._check("wikidata", d)

    def test_limits(self):
        self.assertEqual(self.check().collect["batch"], 50)
        for bad in ({"batch": 51}, {"batch": 0}, {"maxlag": 30}, {"api": "http://x"},
                    {"user_agent": "bot (someone@example.org)"}, {"user_agent": " "}):
            with self.subTest(bad), self.assertRaises(AcquireError):
                self.check(**bad)


if __name__ == "__main__":
    unittest.main()
