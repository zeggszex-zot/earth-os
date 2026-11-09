"""The installer, run for real into a scratch home.

One defect motivates this file. The launcher runs the Claude Code workspace
(``~/.local/share/earth-os/src``) in preference to the install, and nothing
ever updated that workspace — so once the CLAUDE CODE screen had been used,
every reinstall changed ``$PREFIX/lib`` and the launcher kept running the old
copy. An upgrade did nothing at all, even across a reboot, and the only clue
was that nothing had changed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipIf(shutil.which("sha256sum") is None, "needs coreutils")
class TestAReinstallTakesEffect(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("XDG_")}
        self.env.update(HOME=str(self.home), PYTHON=sys.executable,
                        PATH=f"{self.home / '.local' / 'bin'}{os.pathsep}"
                             f"{os.environ.get('PATH', '')}")
        done = subprocess.run(["sh", str(ROOT / "install.sh"), "--user"],
                              env=self.env, capture_output=True, text=True,
                              timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.launcher = self.home / ".local" / "bin" / "earth-os"
        self.workspace = self.home / ".local" / "share" / "earth-os" / "src"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def run_launcher(self, *args):
        return subprocess.run([str(self.launcher), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def make_workspace(self, build: str):
        """A workspace that runs, says so, and carries *build*."""
        package = self.workspace / "earth_os"
        package.mkdir(parents=True)
        (self.workspace / ".git").mkdir()
        (package / "__init__.py").write_text("")
        (package / "cli.py").write_text(
            "def main():\n    print('WORKSPACE CODE')\n    return 0\n")
        if build:
            (self.workspace / "BUILD").write_text(build + "\n")

    def installed_build(self) -> str:
        return (self.home / ".local" / "lib" / "earth-os" / "BUILD").read_text().strip()

    def test_the_install_is_stamped_and_says_where_it_runs_from(self):
        self.assertTrue(self.installed_build())
        source = self.home / ".local" / "share" / "earth-os" / "source" / "BUILD"
        self.assertEqual(source.read_text().strip(), self.installed_build(),
                         "a workspace made from this template must match it")
        out = self.run_launcher("--version").stdout
        self.assertIn("running from", out)
        self.assertIn(str(self.home / ".local" / "lib" / "earth-os"), out)
        self.assertIn(self.installed_build(), out)

    def test_a_workspace_from_an_older_install_is_set_aside_not_run(self):
        self.make_workspace(build="")          # 2.3.0 and earlier had no stamp
        result = self.run_launcher("--version")
        self.assertNotIn("WORKSPACE CODE", result.stdout)
        self.assertIn("older install", result.stderr)
        self.assertFalse(self.workspace.exists())
        kept = list(self.workspace.parent.glob("src.before-*"))
        self.assertEqual(len(kept), 1, "moved aside, never deleted")
        self.assertTrue((kept[0] / "earth_os" / "cli.py").exists())

    def test_a_workspace_from_this_install_still_runs(self):
        """Self-modification keeps working: accepted changes are run."""
        self.make_workspace(build=self.installed_build())
        self.assertIn("WORKSPACE CODE", self.run_launcher().stdout)
        self.assertTrue(self.workspace.exists())


if __name__ == "__main__":
    unittest.main()
