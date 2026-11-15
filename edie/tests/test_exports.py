import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import FIXTURE, FIXTURE_DIR, ROOT

from edie import SCHEMA
from edie.export.markdown import to_markdown
from edie.export.report import to_report
from edie.latex.parser import parse_file
from edie.model import dumps

DOC = parse_file(FIXTURE)


def run(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "edie", *args], cwd=cwd, capture_output=True, text=True)


class Json(unittest.TestCase):
    def test_schema_and_required_keys(self):
        schema = json.loads((ROOT / "edie" / "schema" / "edie-document-1.schema.json").read_text())
        self.assertEqual(DOC["format"], SCHEMA)
        for k in schema["required"]:
            self.assertIn(k, DOC)
        for b in DOC["blocks"]:
            self.assertIn(b["type"], schema["properties"]["blocks"]["items"]["properties"]["type"]["enum"])

    def test_json_keeps_original_latex(self):
        j = json.loads(dumps(DOC))
        maths = [b["latex"] for b in j["blocks"] if b["type"] == "math"]
        self.assertIn("\\begin{align*}", maths[2])
        self.assertIn("$$\\int_0^1 x\\,dx = \\tfrac{1}{2}$$", maths)

    def test_no_absolute_paths_or_times(self):
        out = dumps(DOC)
        self.assertNotIn(str(ROOT), out)
        self.assertNotIn(str(Path.home()), out)


class Reproducible(unittest.TestCase):
    def test_two_parses_are_byte_identical(self):
        self.assertEqual(dumps(parse_file(FIXTURE)), dumps(parse_file(FIXTURE)))

    def test_cli_twice_gives_identical_files_and_leaves_the_source_alone(self):
        before = (hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), FIXTURE.stat().st_mtime_ns,
                  sorted(p.name for p in FIXTURE_DIR.rglob("*")))
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for d in (a, b):
                r = run("parse", str(FIXTURE), "--out", d, "--image-base", "images")
                self.assertEqual(r.returncode, 0, r.stderr)
            for name in ("mini.edie.json", "mini.md", "mini.report.md"):
                self.assertEqual((Path(a) / name).read_bytes(), (Path(b) / name).read_bytes(), name)
        after = (hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), FIXTURE.stat().st_mtime_ns,
                 sorted(p.name for p in FIXTURE_DIR.rglob("*")))
        self.assertEqual(before, after)


class Markdown(unittest.TestCase):
    md = to_markdown(DOC, "images")

    def test_headings_and_running_heads(self):
        self.assertIn("\n# II. On Different Degrees of Smallness.\n", self.md)
        self.assertIn("<!-- running head: Relative Growing -->", self.md)

    def test_equations_readable(self):
        self.assertIn("$\\int dx$", self.md)
        self.assertIn("$$\n\\begin{aligned}\n  y + dy &= (x + dx)^2 \\\\", self.md)
        self.assertIn("$$\n  \\frac{d(\\sin\\theta)}{d\\theta} = \\cos\\theta\n$$", self.md)

    def test_figures_linked_and_missing_ones_marked(self):
        self.assertIn("](images/images/fig1.png)", self.md)
        self.assertIn("![Figure 4: Fig. 4. A curve and its slope.](images/images/fig4.png)", self.md)
        self.assertIn("**[missing image:", self.md)
        self.assertIn("`$\\Graphic{0.3in}{fig3} = \\dfrac{dy}{dx}$`", self.md)

    def test_every_block_has_a_source_anchor(self):
        for b in DOC["blocks"]:
            self.assertIn(f"<!-- {b['id']} L{b['lines'][0]}-{b['lines'][1]} -->", self.md)


class Report(unittest.TestCase):
    def test_report_names_lines_and_problems(self):
        r = to_report(DOC)
        self.assertIn("| 96 | MISSING_IMAGE |", r)
        self.assertIn("`\\Chapter` (3×) | heading | `omm` | preamble line 24", r)
        self.assertIn("| 69 | III | On Relative Growings. | Relative Growing |", r)


class Cli(unittest.TestCase):
    def test_strict_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run("parse", str(FIXTURE), "--out", d, "--strict").returncode, 2)

    def test_missing_source(self):
        with tempfile.TemporaryDirectory() as d:
            r = run("parse", str(Path(d) / "nope.tex"), "--out", d)
            self.assertEqual(r.returncode, 1)
            self.assertIn("cannot read", r.stderr)

    def test_refuses_to_overwrite_the_source(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "book.md"
            shutil.copy(FIXTURE, src)
            before = src.read_bytes()
            r = run("parse", str(src), "--out", d)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(src.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
