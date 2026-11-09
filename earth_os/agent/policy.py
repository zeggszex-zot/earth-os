"""What the built-in agent may never touch, and what it may do unasked.

**This file is human-write-only.** It is part of the immune system described
in ``docs/SELF_MODIFICATION.md`` §2, rung 4, and it lists itself: an agent
that could edit the list of files it may not edit would have no list.

Three separate answers live here, because they are three separate questions:

``PROTECTED``
    Files no agent change may include, however green the suite is. The gate
    (:mod:`.gate`) rejects a change that touches one *before* running the
    tests, so a passing suite cannot launder it.

``ALLOWED_TOOLS``
    What Claude Code may do without anybody pressing anything. Reading,
    searching, editing inside the workspace (every edit is judged by the gate
    before it can be kept), asking for a screenshot, running the suite.

Everything else
    Goes to the LCARS confirmation strip through the permission-prompt tool
    and waits for a person. That includes every shell command not listed
    below — the same rule the catalogue follows: *no confidence score is ever
    high enough*.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple

#: Rung 4 of docs/SELF_MODIFICATION.md, plus the agent's own immune system.
#: Paths are relative to the workspace root, POSIX separators.
PROTECTED: Tuple[str, ...] = (
    # The catalogue and the gate, the ratchet, the gate's own tests, the rules.
    "earth_os/platform/capabilities.py",
    "tests/test_boundary.py",
    "tests/test_capabilities.py",
    "AGENTS.md",
    # The agent's own gate, this policy, and the tests that hold both to it.
    "earth_os/agent/policy.py",
    "earth_os/agent/gate.py",
    "tests/test_agent.py",
)

#: AGENTS.md §7.3: never change both sides of the boundary in one change.
#: Adding a capability needs both, and that is rung 3 — a person's decision.
BOUNDARY_PAIR: Tuple[str, str] = ("earth_os/platform/",
                                  "earth_os/capability_corpus.py")

#: The name the MCP server is registered under. Tool names Claude Code sees
#: are ``mcp__cdos__<tool>``.
MCP_NAME = "cdos"

#: The tool that answers Claude Code's permission prompts by asking a person.
PERMISSION_TOOL = f"mcp__{MCP_NAME}__approve"

#: Runs with no press. Edits are here because the gate judges every one of
#: them before it can be kept; commands are not, except the handful that only
#: read or that *are* the verification.
ALLOWED_TOOLS: Tuple[str, ...] = (
    "Read", "Glob", "Grep", "LS", "Edit", "MultiEdit", "Write",
    "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    f"mcp__{MCP_NAME}__status",
    f"mcp__{MCP_NAME}__screens",
    f"mcp__{MCP_NAME}__go",
    f"mcp__{MCP_NAME}__say",
    f"mcp__{MCP_NAME}__capabilities",
    f"mcp__{MCP_NAME}__invoke",          # effectful ones still wait for a press
    f"mcp__{MCP_NAME}__palettes",
    f"mcp__{MCP_NAME}__set_palette",
    f"mcp__{MCP_NAME}__screenshot",
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
    "Bash(git show:*)", "Bash(ls:*)",
    "Bash(/usr/bin/python3.12 -m unittest:*)",
    "Bash(python3.12 -m unittest:*)",
    "Bash(/usr/bin/python3.12 tools/screenshot.py:*)",
    "Bash(python3.12 tools/screenshot.py:*)",
    "Bash(/usr/bin/python3.12 tools/gallery.py:*)",
    "Bash(python3.12 tools/gallery.py:*)",
)

#: Refused outright, never even offered. The gate is the only thing that
#: commits in the workspace; an agent that could commit, reset or stash could
#: decide what the gate gets to see.
DENIED_TOOLS: Tuple[str, ...] = (
    "Bash(git commit:*)", "Bash(git push:*)", "Bash(git reset:*)",
    "Bash(git stash:*)", "Bash(git checkout:*)", "Bash(git rebase:*)",
    "Bash(git revert:*)", "Bash(git restore:*)", "Bash(git clean:*)",
    "Bash(git config:*)", "Bash(git update-ref:*)",
)


def normalise(path: str, root: Path) -> Optional[str]:
    """*path* relative to *root* in POSIX form, or None if it is outside."""
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        rel = p.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return None
    return PurePosixPath(*rel.parts).as_posix()


def is_protected(path: str, root: Path) -> bool:
    rel = normalise(path, root)
    return rel is not None and rel in PROTECTED


def protected_in(paths: Iterable[str]) -> List[str]:
    """Which of *paths* (workspace-relative) are human-write-only."""
    return sorted({p for p in paths if p in PROTECTED})


def crosses_boundary(paths: Iterable[str]) -> bool:
    """True when a change edits both sides of the boundary at once."""
    below, corpus = BOUNDARY_PAIR
    paths = list(paths)
    return (any(p.startswith(below) for p in paths)
            and any(p == corpus for p in paths))


def settings(root: Path) -> Dict[str, object]:
    """The Claude Code settings the agent runs under.

    Deny rules for protected files are belt and braces: the gate rejects any
    change that includes one regardless. Two locks, because the first one is
    a setting in a program we did not write.
    """
    deny = [f"Edit(./{p})" for p in PROTECTED]
    deny += [f"Write(./{p})" for p in PROTECTED]
    deny += list(DENIED_TOOLS)
    return {
        "permissions": {
            "allow": list(ALLOWED_TOOLS),
            "deny": deny,
            "defaultMode": "acceptEdits",
        },
    }


def settings_json(root: Path) -> str:
    return json.dumps(settings(root), indent=2)


#: Keys in a tool's input that name a file it will change.
_PATH_KEYS = ("file_path", "notebook_path", "path")


def review(tool_name: str, tool_input: Dict[str, object],
           root: Path) -> Tuple[Optional[str], str]:
    """First look at a permission request, before any person sees it.

    Returns ``("deny", reason)`` for something no press may allow, or
    ``(None, description)`` for something a person should decide. There is no
    automatic *allow* here on purpose: anything Claude Code asks about has
    already fallen outside :data:`ALLOWED_TOOLS`, which is the whole of what
    may happen unasked.
    """
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            rel = normalise(value, root)
            if rel is not None and rel in PROTECTED:
                return ("deny", f"{rel} is human-write-only "
                                "(docs/SELF_MODIFICATION.md, rung 4)")

    if tool_name == "Bash":
        command = str(tool_input.get("command", "")).strip()
        for rule in DENIED_TOOLS:
            prefix = rule[len("Bash("):-len(":*)")]
            if command == prefix or command.startswith(prefix + " "):
                return ("deny", f"'{prefix}' is the gate's job, not the agent's")
        return (None, command or "(empty command)")

    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return (None, value)
    return (None, json.dumps(tool_input, sort_keys=True)[:160])
