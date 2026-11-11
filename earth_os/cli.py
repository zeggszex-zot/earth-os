"""Command line entry point.

Deliberately imports nothing from GTK at module level.  The most confusing way
for this program to fail is for the toolkit import to explode inside a desktop
menu launch, where there is no terminal to print the traceback to and the user
simply sees nothing happen.  So argument parsing and ``--doctor`` work even on
a machine where GTK is missing entirely, and any failure is written to a log
file and surfaced through whatever notification mechanism exists.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import List, Optional


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "earth-os"


def log_path() -> Path:
    return cache_dir() / "last-error.log"


def report_failure(message: str, detail: str = "") -> None:
    """Make a failure visible however we can: stderr, a log, and a notification.

    A menu launch has no terminal, so stderr alone is not enough.
    """
    text = message if not detail else f"{message}\n\n{detail}"
    print(f"earth-os: {message}", file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)

    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        log_path().write_text(text + "\n", encoding="utf-8")
        print(f"earth-os: written to {log_path()}", file=sys.stderr)
    except OSError:
        pass

    # Best effort desktop notification, so a menu launch says *something*.
    import shutil
    import subprocess
    summary = "EARTH OS failed to start"
    try:
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", "-u", "critical", summary, message],
                           timeout=5, check=False)
        elif shutil.which("zenity"):
            subprocess.run(["zenity", "--error", "--title", summary,
                            "--text", text], timeout=5, check=False)
        elif shutil.which("kdialog"):
            subprocess.run(["kdialog", "--error", text], timeout=5, check=False)
    except Exception:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earth-os", description="EARTH OS shell")
    parser.add_argument("-c", "--config", help="path to config.toml")
    parser.add_argument("-w", "--windowed", action="store_true",
                        help="run in a window instead of fullscreen")
    parser.add_argument("-s", "--screen", help="screen to start on")
    parser.add_argument("--no-voice", action="store_true",
                        help="disable the voice subsystem")
    parser.add_argument("--palette", help="colour palette name")
    parser.add_argument("--doctor", action="store_true",
                        help="check every prerequisite and report what is "
                             "missing, then exit")
    parser.add_argument("--version", action="store_true",
                        help="print the version and exit")
    parser.add_argument("--learned", action="store_true",
                        help="print the learned aliases and rules, then exit")
    parser.add_argument("--forget", metavar="TERM",
                        help="remove one learned term, then exit")
    parser.add_argument("--teach", nargs=2, metavar=("TERM", "MEANS"),
                        help="teach a relationship by hand, then exit")
    parser.add_argument("--explain", metavar="PHRASE",
                        help="show how a phrase would be resolved, then exit")
    parser.add_argument("--screenshot", action="store_true",
                        help="save a picture of the whole screen to "
                             "Pictures/EARTH OS Screenshots, print where, and exit")
    parser.add_argument("--switch", action="store_true",
                        help="move focus to the next program, or the home "
                             "screen after the last, and exit (Alt+Tab)")
    parser.add_argument("--switch-back", action="store_true",
                        help="move focus to the previous program, and exit "
                             "(Alt+Shift+Tab)")
    return parser


def semantics_commands(args) -> Optional[int]:
    """Handle the knowledge-base flags. Returns an exit code, or None.

    These are deliberately available without starting the GUI: a learned
    table you can only inspect from inside a fullscreen shell is one you will
    never actually audit.
    """
    from .semantics import SemanticStore, seed as seed_semantics

    if args.learned:
        store = SemanticStore.load()
        rows = store.table()
        if not rows:
            print("Nothing learned yet.")
            print(f"({store.path})")
            return 0
        print(f"{'TERM':<18} {'MEANS':<18} {'CONF':>6}  STATE      HITS")
        for term, canonical, confidence, confirmed, hits in rows:
            state = "confirmed" if confirmed else "provisional"
            print(f"{term:<18} {canonical:<18} {confidence:>5.0%}  "
                  f"{state:<11} {hits}")
        if store.rules:
            print()
            print("RULES")
            for rule in store.rules.values():
                print(f"  {rule.name:<24} "
                      f"{' + '.join(rule.when_terms)} → {rule.then_canonical} "
                      f"({rule.confidence:.0%})")
        print()
        print(store.describe())
        print(f"({store.path})")
        return 0

    if args.forget:
        store = SemanticStore.load()
        if store.forget(args.forget):
            store.save()
            print(f"forgotten: {args.forget}")
            return 0
        print(f"not found: {args.forget}")
        return 1

    if args.teach:
        term, means = args.teach
        store = SemanticStore.load()
        alias = store.learn_alias(term, means, source="user", seed=0.9)
        if alias is None:
            print(f"cannot teach '{term}' → '{means}'")
            return 1
        alias.confirmed = True
        store.save()
        print(f"learned: {alias.term} → {alias.canonical} "
              f"({alias.confidence:.0%}, confirmed)")
        return 0

    if args.explain:
        from .commands import Dispatcher, Vocabulary, apply_alias
        store = SemanticStore.load()
        if not store.aliases:
            seed_semantics(store)
        dispatcher = Dispatcher(Vocabulary.load(), semantics=store)
        phrase = args.explain
        print(f"utterance   {phrase}")
        direct = dispatcher.vocabulary.match(phrase)
        if direct:
            print(f"resolved    {direct}")
            print("via         fixed vocabulary (no model, no learning)")
            return 0
        resolution = store.resolve(phrase)
        if not resolution.found:
            print("resolved    nothing")
            print("via         would go to the model, and be learned from")
            return 0
        rewritten = apply_alias(phrase, resolution.term, resolution.canonical)
        print(f"rewritten   {rewritten}")
        print(f"resolved    {dispatcher.vocabulary.match(rewritten) or '(no action)'}")
        print(f"via         {resolution.via} — {resolution.explanation}")
        print(f"confidence  {resolution.confidence:.0%}")
        print(f"effectful   {'yes — always asks' if resolution.effectful else 'no'}")
        return 0

    return None


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        from . import __version__
        # Where it is running from, not only what number it carries: an
        # installed launcher can run the Claude Code workspace instead of the
        # install, and "I installed it and nothing changed" is answered by
        # this line and nothing else.
        here = Path(__file__).resolve().parent.parent
        print(f"earth-os {__version__}")
        print(f"running from {here}")
        build = here / "BUILD"
        if build.is_file():
            print(f"build {build.read_text(encoding='utf-8').strip()}")
        return 0

    if args.doctor:
        from .diagnostics import run
        return run()

    if args.screenshot:
        from .screengrab import capture_from_command_line
        return capture_from_command_line()

    if args.switch or args.switch_back:
        from .windows import switch_from_command_line
        return switch_from_command_line(-1 if args.switch_back else 1)

    code = semantics_commands(args)
    if code is not None:
        return code

    # --- import the toolkit, loudly if it fails ---------------------------
    try:
        from .app import EarthApplication
    except Exception as exc:
        report_failure(
            "the GTK 4 toolkit could not be loaded, so no window can be opened.",
            f"{type(exc).__name__}: {exc}\n\n"
            f"Run 'earth-os --doctor' for a full check.\n\n"
            + traceback.format_exc())
        return 1

    from .config import Config

    try:
        config = Config.load(args.config)
    except Exception as exc:
        report_failure(f"the configuration could not be loaded: {exc}")
        return 1

    if args.windowed:
        config.values.setdefault("shell", {})["fullscreen"] = False
    if args.screen:
        config.values.setdefault("shell", {})["start_screen"] = args.screen
    if args.palette:
        config.values.setdefault("shell", {})["palette"] = args.palette
    if args.no_voice:
        config.values.setdefault("voice", {})["enabled"] = False

    try:
        application = EarthApplication(config)
        status = application.run([sys.argv[0] if sys.argv else "earth-os"])
    except Exception as exc:
        report_failure(
            f"the shell stopped unexpectedly: {type(exc).__name__}: {exc}",
            "Run 'earth-os --doctor' for a full check.\n\n"
            + traceback.format_exc())
        return 1

    if status != 0:
        report_failure(
            f"the shell exited with status {status} without opening a window.",
            "This usually means GTK could not reach your display.\n"
            "Run 'earth-os --doctor' for a full check.")
    return status


if __name__ == "__main__":
    sys.exit(main())
