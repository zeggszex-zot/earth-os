"""Screens of the EARTH OS shell."""

from .base import Frame, Screen
from .browser import BrowserScreen
from .claude import ClaudeScreen
from .comms import CommsScreen
from .engineering import EngineeringScreen
from .main import MainScreen
from .programs import ProgramsScreen
from .recent import RecentScreen
from .settings import SettingsScreen
from .tasks import TasksScreen
from .terminal import TerminalScreen

ALL_SCREENS = (
    MainScreen, ProgramsScreen, RecentScreen, BrowserScreen,
    EngineeringScreen, SettingsScreen, TasksScreen, TerminalScreen,
    ClaudeScreen, CommsScreen,
)

__all__ = [
    "Frame", "Screen", "MainScreen", "ProgramsScreen", "RecentScreen",
    "BrowserScreen", "EngineeringScreen", "SettingsScreen",
    "TasksScreen", "TerminalScreen", "ClaudeScreen", "CommsScreen",
    "ALL_SCREENS",
]
