"""EDIE stays separate: standard library only, no Earth OS or CDOS imports, no network, no model clients,
and nothing in Earth OS or CDOS imports it.  Read from the source, not trusted from a docstring.

The repository half runs only when EDIE sits inside the Earth OS repository (edie/ next to earth_os/).  A copy
unpacked anywhere else reports those tests as skipped, never as a pass over folders that are not there.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

from helpers import ROOT

REPO = ROOT.parent
IN_REPO = (REPO / "earth_os").is_dir() and (REPO / "AGENTS.md").is_file()
FORBIDDEN = {"earth_os", "computer", "socket", "urllib", "http", "requests", "subprocess", "ssl", "ftplib",
             "smtplib", "asyncio", "multiprocessing", "ctypes", "importlib", "openai", "anthropic", "llama_cpp",
             "ollama"}
DYNAMIC = re.compile(r"""(?:import_module|__import__)\(\s*['"]edie\b|['"]edie\.(?:latex|export|model|source)""")


def imports(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.add(n.module.split(".")[0])
        elif isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) in ("__import__",
                                                                                               "import_module"):
            out.add("<dynamic import>")
    return out


def outside_edie():
    for p in sorted(REPO.rglob("*.py")):
        rel = p.relative_to(REPO)
        if rel.parts[0] in ("edie", ".git") or "__pycache__" in rel.parts:
            continue
        yield p, rel


class EdieImportsNothingOfEarthOS(unittest.TestCase):
    def test_edie_imports_only_the_standard_library(self):
        stdlib = set(sys.stdlib_module_names)
        files = sorted((ROOT / "edie").rglob("*.py"))
        self.assertGreater(len(files), 5)
        for p in files:
            mods = imports(p)
            self.assertFalse(mods & (FORBIDDEN | {"<dynamic import>"}),
                             f"{p.relative_to(ROOT)} imports {sorted(mods & (FORBIDDEN | {'<dynamic import>'}))}")
            self.assertTrue(mods <= stdlib | {"edie"}, f"{p.relative_to(ROOT)}: {sorted(mods - stdlib)}")

    def test_edie_does_not_reach_into_other_folders(self):
        for p in sorted((ROOT / "edie").rglob("*.py")):
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("sys.path", text, str(p.relative_to(ROOT)))
            for name in ("earth_os", "cdos-computer", "corpus.pf"):
                self.assertNotIn(name, text, f"{p.relative_to(ROOT)} names {name}")


@unittest.skipUnless(IN_REPO, f"not inside the Earth OS repository ({REPO} has no earth_os/ and AGENTS.md)")
class EarthOSImportsNothingOfEdie(unittest.TestCase):
    def test_no_python_file_outside_edie_imports_it(self):
        checked = 0
        for p, rel in outside_edie():
            try:
                mods = imports(p)
            except SyntaxError:
                continue
            checked += 1
            self.assertNotIn("edie", mods, str(rel))
            self.assertIsNone(DYNAMIC.search(p.read_text(encoding="utf-8")), f"{rel} loads edie dynamically")
        self.assertGreater(checked, 100)              # the repository was really read

    def test_edie_is_not_installed_as_part_of_earth_os(self):
        """EDIE is a separate tool: not one of the earth_os packages, not copied by the installer."""
        self.assertNotIn("edie", (REPO / "pyproject.toml").read_text(encoding="utf-8"))
        for script in ("install.sh",):
            if (REPO / script).is_file():
                self.assertNotRegex((REPO / script).read_text(encoding="utf-8"), r"\bedie\b")


if __name__ == "__main__":
    unittest.main()
