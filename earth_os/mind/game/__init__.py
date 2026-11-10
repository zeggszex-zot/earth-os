"""Game intelligence: worlds hosted by Earth Mind (docs/EARTH_MIND.md, §8).

A game supplies personalities and world context; Earth Mind supplies the rest
— memory, dialogue, the shared model, event tracking. Nothing in this package
can reach the operating system: it imports no capability layer, no shell, no
agent and no process control, and ``tests/test_mind_game.py`` reads the
imports to keep it that way.
"""

from .environment import GameEnvironment, Relationship
from .host import GameHost
from .world import BUILTIN_WORLDS, WorldDefinition, WorldError

__all__ = ["GameEnvironment", "GameHost", "Relationship", "WorldDefinition", "WorldError",
           "BUILTIN_WORLDS"]
