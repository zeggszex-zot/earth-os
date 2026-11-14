"""The editable tree the agent works in.

CDOS changes itself by changing its source and restarting into it, so the
agent needs a real working tree: the package, the tests that judge it, and
the documents that brief it (``AGENTS.md``, ``CLAUDE.md``). And it needs git,
because every change ends as a commit carrying its verification output — the
audit trail *is* the history.

Where that tree is, in order:

1. ``[claude] workspace`` in the config, if set;
2. the tree this package was imported from, when that is a git checkout with
   its tests beside it — the developer case, ``python3 -m earth_os``;
3. ``~/.local/share/earth-os/src``, created on first use from the source
   template ``install.sh`` ships. The installed launcher prefers this tree
   when it exists, which is what makes an installed CDOS self-modifying.
   Deleting it returns the machine to the stock install.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from ..config import data_dir

PACKAGE_DIR = Path(__file__).resolve().parent.parent

#: Identity for commits the gate makes, so a machine with no git config of its
#: own can still keep an audit trail.
COMMITTER = ("CDOS gate", "cdos-gate@localhost")

#: Copied from a template; everything else in one is build or cache noise.
_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".cache", "*.egg-info", "build", "dist")


class WorkspaceError(RuntimeError):
    """The tree cannot be used, with the reason."""


@dataclass
class Workspace:
    root: Path

    # ------------------------------------------------------------------
    def git(self, *args: str, check: bool = False,
            timeout: float = 60.0) -> subprocess.CompletedProcess:
        name, email = COMMITTER
        argv = ["git", "-c", f"user.name={name}", "-c", f"user.email={email}",
                *args]
        try:
            result = subprocess.run(
                argv, cwd=self.root, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as exc:
            raise WorkspaceError("git is not installed") from exc
        if check and result.returncode != 0:
            raise WorkspaceError(
                f"git {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
        return result

    @property
    def is_repo(self) -> bool:
        return (self.root / ".git").exists()

    @property
    def has_tests(self) -> bool:
        return (self.root / "tests").is_dir()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD", check=True).stdout.strip()

    def changed_since(self, base: str) -> List[str]:
        """Every path that differs from *base*: commits, edits, new files."""
        tracked = self.git("diff", "--name-only", base, check=True).stdout
        untracked = self.git("ls-files", "--others", "--exclude-standard",
                             check=True).stdout
        paths = {line.strip() for line in (tracked + "\n" + untracked).splitlines()
                 if line.strip()}
        return sorted(paths)

    def dirty(self) -> bool:
        return bool(self.git("status", "--porcelain", check=True).stdout.strip())

    def log(self, count: int = 10) -> List[str]:
        out = self.git("log", f"-{count}", "--pretty=%h %s").stdout
        return [line for line in out.splitlines() if line.strip()]

    def describe(self) -> str:
        home = str(Path.home())
        where = str(self.root)
        return "~" + where[len(home):] if where.startswith(home) else where


# ----------------------------------------------------------------------
def _is_source_tree(path: Path) -> bool:
    return (path / "earth_os").is_dir() and (path / "tests").is_dir()


def templates() -> List[Path]:
    """Places a pristine copy of the full source may be found."""
    found: List[Path] = []
    env = os.environ.get("EARTH_OS_SOURCE")
    if env:
        found.append(Path(env).expanduser())
    # $PREFIX/lib/earth-os/earth_os  ->  $PREFIX/share/earth-os/source
    prefix = PACKAGE_DIR.parent.parent.parent
    found.append(prefix / "share" / "earth-os" / "source")
    found.append(PACKAGE_DIR.parent)
    return found


def user_tree() -> Path:
    return data_dir() / "src"


def locate(config=None) -> Optional[Workspace]:
    """The workspace to use, if one exists yet. Never creates anything."""
    configured = ""
    if config is not None:
        configured = str(config.get("claude", "workspace", "") or "").strip()
    if configured:
        return Workspace(Path(configured).expanduser())
    here = PACKAGE_DIR.parent
    if (here / ".git").exists() and _is_source_tree(here):
        return Workspace(here)
    tree = user_tree()
    if (tree / ".git").exists():
        return Workspace(tree)
    return None


def provision(sources: Optional[Sequence[Path]] = None,
              target: Optional[Path] = None) -> Workspace:
    """Create the user tree from a template and commit it as the baseline."""
    target = target or user_tree()
    if (target / ".git").exists():
        return Workspace(target)
    for source in (sources if sources is not None else templates()):
        if _is_source_tree(source):
            break
    else:
        raise WorkspaceError(
            "no CDOS source template found — reinstall with ./install.sh, "
            "or set [claude] workspace to a git checkout of earth-os")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=_IGNORE)
    ws = Workspace(target)
    ws.git("init", "-q", check=True)
    ws.git("add", "-A", check=True)
    ws.git("commit", "-q", "-m", "CDOS baseline (provisioned for Claude Code)",
           check=True)
    return ws
