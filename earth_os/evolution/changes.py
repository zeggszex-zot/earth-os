"""The one actuator: a bounded list of settings, changed only once approved.

Everything else under ``earth_os/evolution/`` looks. This module is the only
place the System Evolution Center can change anything outside its own
records, and what it can change is deliberately small:

* **one file** — the shell's own ``config.toml``;
* **one kind of edit** — setting a single key to a single value;
* **a fixed list of keys** — :data:`TUNABLES`, each with its type and bounds.

It cannot install a package, run a command, edit a source file, delete
anything or write anywhere else, because none of those things is in it. That
is the same argument ``earth_os/compositions.py`` makes about its store: the
safe way to have no convenience runner is to not write one.

The order of an application is fixed, and each step refuses on its own:

1. the proposal must be ``APPROVED`` — a status only a press can give it;
2. the change must name a key in :data:`TUNABLES`, within its bounds;
3. the file is copied to ``backups/`` — if that fails, nothing is changed;
4. the one line is edited, the rest of the file is left byte for byte;
5. the change, the previous value and the backup are written to the history.

**Do not widen** :data:`TUNABLES` **to be thorough.** Every entry is a thing
the center may change on a press without anyone reading a diff. A setting
that can leave the machine unusable, or that names a program to run, does
not belong here however useful it would be.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .model import APPROVED
from .store import Store


def _sha256(text: str) -> str:
    """The digest that tells whether a file was edited after a change.

    ``hashlib`` is imported here, not at the top: it loads OpenSSL, which
    measured 3.5–4.5 MB of resident memory — most of what importing the
    whole engine cost — for something needed only on an APPLY or a ROLL
    BACK press. docs/EVOLUTION_CENTER.md §5.
    """
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ChangeRefused(Exception):
    """The change was not made, and this says why."""


@dataclass(frozen=True)
class Tunable:
    section: str
    key: str
    kind: str                         # "float" | "int" | "bool" | "choice"
    low: float = 0.0
    high: float = 0.0
    choices: Tuple[str, ...] = ()
    #: Whether the new value only takes effect when Earth OS is restarted.
    #: False means a running Earth OS puts it into effect at once
    #: (``Shell.apply_setting``) — and ``tests/test_evolution.py`` checks that
    #: claim against a real window for every entry that makes it. Whether a
    #: particular change *did* take effect is recorded with the change, as
    #: ``in_effect_now``: no window, or voice switched off, means it did not.
    restart: bool = False
    what: str = ""

    @property
    def name(self) -> str:
        return f"{self.section}.{self.key}"


def _t(section: str, key: str, kind: str, **kw) -> Tuple[str, Tunable]:
    return (f"{section}.{key}", Tunable(section, key, kind, **kw))


#: Everything the center may change. Nothing outside this table can be
#: applied, whatever a proposal says.
TUNABLES: Dict[str, Tunable] = dict([
    _t("shell", "adaptive", "bool", what="draw frames on change rather than on a clock"),
    _t("shell", "frame_cache", "bool", what="keep the last composited frame"),
    _t("shell", "phosphor", "bool", what="the scanline-and-glow effect"),
    _t("shell", "fps", "int", low=10, high=60, what="the frame-rate ceiling"),
    _t("shell", "idle_fps", "int", low=0, high=2, what="frames drawn with nothing changing"),
    _t("shell", "telemetry_interval", "float", low=0.5, high=10.0,
       what="seconds between hardware samples"),
    _t("shell", "tick_interval", "float", low=0.05, high=1.0,
       what="seconds between screens checking for their own news"),
    _t("windows", "poll_interval", "float", low=0.5, high=10.0,
       what="seconds between looks at the window list"),
    _t("voice", "continuous", "bool", what="listen without the wake word"),
    _t("companion", "keep_alive", "choice", choices=("0", "2m", "5m", "10m", "30m"),
       restart=True,                # read when the model client is built
       what="how long the companion model stays loaded after use"),
    _t("evolution", "poll_interval", "float", low=1.0, high=30.0,
       what="seconds between the Evolution Center's own samples"),
    _t("evolution", "process_interval", "float", low=2.0, high=120.0,
       what="seconds between process-list readings"),
    _t("evolution", "background_interval", "float", low=0.0, high=3600.0,
       what="seconds between trend samples while the center is hidden"),
])


def validate(change: Optional[Dict[str, object]]) -> Tuple[Tunable, object]:
    """The tunable *change* names and its value coerced, or :class:`ChangeRefused`."""
    if not isinstance(change, dict):
        raise ChangeRefused("this proposal carries no change the center can apply")
    name = f"{change.get('section')}.{change.get('key')}"
    tunable = TUNABLES.get(name)
    if tunable is None:
        raise ChangeRefused(f"{name} is not a setting the center may change")
    value = change.get("value")
    if tunable.kind == "bool":
        if not isinstance(value, bool):
            raise ChangeRefused(f"{name} must be true or false")
        return tunable, value
    if tunable.kind == "choice":
        if str(value) not in tunable.choices:
            raise ChangeRefused(f"{name} must be one of {', '.join(tunable.choices)}")
        return tunable, str(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChangeRefused(f"{name} must be a number")
    if not tunable.low <= float(value) <= tunable.high:
        raise ChangeRefused(f"{name} must be between {tunable.low:g} and {tunable.high:g}")
    return tunable, (int(value) if tunable.kind == "int" else float(value))


# -- editing one key in a TOML file, leaving the rest alone ------------------------
def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)                      # 2.0 stays "2.0", not "2"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


_SECTION = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(?:#.*)?$")


def _find(lines, section: str, key: str) -> Tuple[int, int, int]:
    """``(section_line, key_line, section_end)``; -1 for what is not there."""
    start = found = -1
    end = len(lines)
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        match = _SECTION.match(line)
        if match or line.lstrip().startswith("[["):
            if start >= 0:
                end = i
                break
            if match and match.group(1) == section:
                start = i
            continue
        if start >= 0 and found < 0 and pattern.match(line):
            found = i
    return start, found, end


def set_value(text: str, section: str, key: str, value: object) -> str:
    """*text* with ``[section] key`` set to *value*; every other line untouched.

    A trailing comment on the line being replaced is kept: it is the person's
    note about what the setting is for, and it is still true.
    """
    lines = text.splitlines()
    start, found, end = _find(lines, section, key)
    literal = _literal(value)
    if found >= 0:
        old = lines[found]
        indent = old[:len(old) - len(old.lstrip())]
        body = old.split("=", 1)[1]
        comment = ""
        # A '#' outside a quoted string begins the comment.
        quoted = False
        for i, char in enumerate(body):
            if char == '"':
                quoted = not quoted
            elif char == "#" and not quoted:
                spaces = len(body[:i]) - len(body[:i].rstrip())
                comment = body[i - spaces:] if spaces else " " + body[i:]
                break
        lines[found] = f"{indent}{key} = {literal}{comment}"
    elif start >= 0:
        at = end
        while at - 1 > start and not lines[at - 1].strip():
            at -= 1
        lines.insert(at, f"{key} = {literal}")
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([f"[{section}]", f"{key} = {literal}"])
    return "\n".join(lines) + "\n"


def remove_value(text: str, section: str, key: str) -> str:
    """*text* without ``[section] key``, for undoing a key that was added."""
    lines = text.splitlines()
    _start, found, _end = _find(lines, section, key)
    if found >= 0:
        del lines[found]
    return "\n".join(lines) + "\n"


# -- applying and reverting ----------------------------------------------------
class ChangeApplier:
    """Applies an approved change to the configuration file, and undoes one.

    *config* is the shell's live :class:`~earth_os.config.Config`: its
    ``path`` is the file, and its ``values`` are updated too. Then
    *on_change* is told, so a running shell puts the value into effect, and
    the record says whether it did.
    """

    def __init__(self, config, store: Store,
                 on_change: Optional[Callable[[str, str], bool]] = None):
        self.config = config
        self.store = store
        #: Told ``(section, key)`` after the value changes; returns whether
        #: the running shell put it into effect. The shell's
        #: ``apply_setting`` — nothing that can change anything else.
        self.on_change = on_change

    def _take_effect(self, tunable: Tunable) -> bool:
        if not callable(self.on_change):
            return False
        try:
            return bool(self.on_change(tunable.section, tunable.key))
        except Exception:
            return False

    def current(self, tunable: Tunable) -> Tuple[bool, object]:
        """``(explicitly_set, value)`` for *tunable* as configured now."""
        block = getattr(self.config, "values", {}).get(tunable.section)
        explicit = isinstance(block, dict) and tunable.key in block
        return explicit, self.config.get(tunable.section, tunable.key)

    def in_file(self, tunable: Tunable) -> bool:
        """Whether the file itself sets *tunable* (rather than a default doing so)."""
        try:
            lines = Path(self.config.path).read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        return _find(lines, tunable.section, tunable.key)[1] >= 0

    def apply(self, proposal, by: str = "") -> Dict[str, object]:
        """Make *proposal*'s change. Raises :class:`ChangeRefused` otherwise.

        Returns the record that was written to the history: what changed,
        from what, on whose press (*by*), where the backup is, and how to put
        it back.
        """
        if getattr(proposal, "status", "") != APPROVED:
            raise ChangeRefused(
                f"{getattr(proposal, 'id', 'this proposal')} is "
                f"{getattr(proposal, 'status', 'unknown')}, not APPROVED: "
                "nothing is changed without approval")
        tunable, value = validate(getattr(proposal, "change", None))
        path = Path(self.config.path)
        was_in_file = self.in_file(tunable)
        _explicit, previous = self.current(tunable)
        try:
            text = path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError as exc:
            raise ChangeRefused(f"cannot read {path}: {exc}") from exc
        try:
            backup = self.store.back_up(path, f"{proposal.id}-{tunable.name}")
        except OSError as exc:
            raise ChangeRefused(f"could not back up {path}, so it was left alone: {exc}") from exc
        written = set_value(text, tunable.section, tunable.key, value)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(written, encoding="utf-8")
        except OSError as exc:
            raise ChangeRefused(f"cannot write {path}: {exc}") from exc
        self.config.values.setdefault(tunable.section, {})[tunable.key] = value
        live = self._take_effect(tunable)

        record = {
            "event": "change-applied", "proposal": proposal.id, "title": proposal.title,
            "by": str(by or "").strip(), "file": str(path), "setting": tunable.name,
            "previous": previous, "was_in_file": was_in_file, "value": value,
            "backup": str(backup) if backup else "",
            # What the file held the moment after the change, so a rollback
            # can tell whether anybody has edited it since.
            "written_sha256": _sha256(written),
            "restart": tunable.restart, "in_effect_now": live,
            "at": time.time(),
            "rollback": (f"Press ROLL BACK on {proposal.id}, or set [{tunable.section}] "
                         f"{tunable.key} = {_literal(previous)} in {path}"
                         + (f", or copy {backup} back over it." if backup else ".")),
        }
        self.store.append_history(record)
        return record

    def revert(self, proposal, by: str = "") -> Dict[str, object]:
        """Put back what :meth:`apply` changed, and only that.

        If the file is exactly as the change left it, the backup is copied
        back and the file is byte for byte what it was. If anyone has edited
        it since, the backup is **not** copied over it — that would undo
        their edits too, a change nobody approved. Then only the one key is
        restored: to its previous value, or removed if the file did not set
        it before. The backup stays where it is either way.
        """
        applied = getattr(proposal, "applied", None)
        if not isinstance(applied, dict) or not applied.get("setting"):
            raise ChangeRefused(f"{getattr(proposal, 'id', 'this proposal')} "
                                "has no applied change to roll back")
        tunable = TUNABLES.get(str(applied["setting"]))
        if tunable is None:
            raise ChangeRefused(f"{applied['setting']} is not a setting the center may change")
        path = Path(self.config.path)
        exact = False
        try:
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            backup = self.store.back_up(path, f"{proposal.id}-rollback-{tunable.name}")
            original = Path(str(applied.get("backup") or ""))
            untouched = _sha256(text) == applied.get("written_sha256")
            if untouched and applied.get("backup") and original.is_file():
                text = original.read_text(encoding="utf-8")
                exact = True
            elif applied.get("was_in_file"):
                text = set_value(text, tunable.section, tunable.key, applied.get("previous"))
            else:
                text = remove_value(text, tunable.section, tunable.key)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise ChangeRefused(f"could not roll back {path}: {exc}") from exc
        # The live value goes back to what it was, whether that came from
        # the file or from a default.
        self.config.values.setdefault(tunable.section, {})[tunable.key] = applied.get("previous")
        live = self._take_effect(tunable)
        record = {
            "event": "change-rolled-back", "proposal": proposal.id, "title": proposal.title,
            "by": str(by or "").strip(), "file": str(path), "setting": tunable.name,
            "restored": applied.get("previous"), "backup": str(backup) if backup else "",
            "whole_file_restored": exact, "restart": tunable.restart,
            "in_effect_now": live,
        }
        self.store.append_history(record)
        return record
