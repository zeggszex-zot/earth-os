"""The Companion Engine: conversation, with CDOS underneath it (docs/COMPANION.md).

This package talks. It does not act: nothing here imports the capability
layer, the agent, compositions or the CDOS bridge, and
``tests/test_companion.py`` reads the imports to keep it that way.
"""

from .engine import CompanionEngine, Reply
from .intent import CASUAL, DEEP, REFLECTIVE, Intent, classify, is_conversational
from .profile import GROUNDED, OPEN, Profile

__all__ = ["CompanionEngine", "Reply", "Intent", "classify", "is_conversational",
           "CASUAL", "REFLECTIVE", "DEEP", "build_engine", "Profile", "GROUNDED", "OPEN"]


def build_engine(config, link=None, ledger=None, godcore=None, reasoner=None):
    """The engine from the shell's ``[companion]`` section (and ``[ollama]`` for the host)."""
    from .model import DEFAULT_MODEL, CompanionModel, ReasonerModel
    from .profile import from_block
    block = config.section("companion")
    profile, num_ctx, timeout = from_block(block)
    backend = str(block.get("backend", "ollama"))
    model = None
    if backend == "ollama":
        model = CompanionModel(
            host=str(block.get("host") or config.get("ollama", "host", "http://127.0.0.1:11434")),
            model=str(block.get("model", DEFAULT_MODEL)),
            timeout=timeout, num_ctx=num_ctx,
            keep_alive=str(block.get("keep_alive", "10m")))
    elif backend == "reasoner" and reasoner is not None:
        model = ReasonerModel(reasoner)
    return CompanionEngine(
        model=model, link=link, ledger=ledger, godcore=godcore,
        name=str(config.get("shell", "computer_name", "CDOS") or "CDOS"),
        user=str(config.get("shell", "operator", "") or ""),
        observe=bool(block.get("observe", True)) and bool(config.get("world", "observe", True)),
        casual_facts=str(block.get("casual_facts", "worded")),
        free_answers=bool(config.get("computer", "free_answers", False)),
        idle_minutes=float(block.get("idle_minutes", 30)),
        profile=profile)
