"""Earth Mind: the central intelligence architecture of Earth OS (docs/EARTH_MIND.md).

One coordination layer over four subsystems — CDOS (knowledge), the Companion
(conversation), the system (the real machine) and game worlds — so that the
computer is one mind with specialised capabilities rather than four programs.

Everything an interface needs is here::

    mind = build_mind(config, shell=shell, link=link, ledger=ledger)
    response = mind.handle("why is my computer running slowly?", interface="terminal")
    response.text, response.mode(), response.source

Earth Mind talks, reads and proposes. It does not act: see :mod:`.mind`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Optional

from .envelope import Proposal, Request, Response
from .memory import MemoryAccessError, MemoryStore, Principal
from .mind import EarthMind
from .models import ModelManager, ModelSpec
from .personas import Personality, PersonalityError, PersonalityRegistry
from .router import Route, Router
from .system import SystemSubsystem

__all__ = ["EarthMind", "build_mind", "Request", "Response", "Proposal", "Route", "Router",
           "ModelManager", "ModelSpec", "MemoryStore", "MemoryAccessError", "Principal",
           "Personality", "PersonalityError", "PersonalityRegistry", "SystemSubsystem"]


def build_mind(config, shell=None, link=None, ledger=None, reasoner=None,
               godcore: Optional[Callable[[], Dict]] = None) -> EarthMind:
    """Earth Mind from the shell's configuration (``[mind]``, ``[companion]``, ``[computer]``).

    *shell* is the system subsystem's port: the object with the capability
    corpus, ``answer_reading``, ``answer_analysis`` and ``find_program``. With
    no shell there is no system subsystem, and everything else still works.
    Building costs no model call, loads no corpus and takes no reading.
    """
    from ..config import config_dir, data_dir
    from .game.world import BUILTIN_WORLDS
    from .personas import BUILTIN_DIR

    block = config.section("mind")
    companion = config.section("companion")
    where = str(block.get("memory") or "").strip()
    if where == ":memory:":
        path: Optional[Path] = None
    else:
        path = Path(where).expanduser() if where else data_dir() / "mind" / "memory.db"

    world_requests = answer = None
    if ledger is not None:
        from ..world_model import parse_request
        world_requests = parse_request
        handler = getattr(shell, "handle_world_request", None)
        if handler is not None:
            answer = lambda text: handler(parse_request(text))      # noqa: E731

    return EarthMind(
        models=ModelManager.from_config(config, reasoner=reasoner),
        store=MemoryStore(path, ledger=ledger),
        link=link, ledger=ledger,
        system=SystemSubsystem(shell) if shell is not None else None,
        personalities=PersonalityRegistry([BUILTIN_DIR, config_dir() / "personalities"]),
        godcore=godcore, world_requests=world_requests, answer_world_request=answer,
        persona=str(block.get("persona", "companion") or "companion"),
        name=str(config.get("shell", "computer_name", "EARTH OS") or "EARTH OS"),
        user=str(config.get("shell", "operator", "") or ""),
        companion_options={
            "observe": bool(companion.get("observe", True)) and bool(config.get("world", "observe", True)),
            "casual_facts": str(companion.get("casual_facts", "worded")),
            "free_answers": bool(config.get("computer", "free_answers", False)),
            "idle_minutes": float(companion.get("idle_minutes", 30)),
        },
        explain=bool(block.get("explain", True)),
        world_dirs=[BUILTIN_WORLDS, data_dir() / "mind" / "worlds"],
        default_world=str(block.get("world", "harbour") or ""))
