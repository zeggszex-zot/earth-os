"""CDOS as the reasoning backend.

CDOS ships its own boundary for exactly this job, and the point of that
boundary is that the model proposes and never disposes.  This module is written
to preserve that property rather than to work around it:

* The deterministic path runs first.  ``cdos-bridge listen`` maps a spoken
  phrase to a command with a fixed table, no model and no network.  Every entry
  in that table is a ``READ``, so a misheard sentence can only ever cause CDOS
  to look at the wrong subsystem and say so.
* Ambiguity is reported, not resolved.  When a phrase matches more than one
  subsystem the bridge says so, and the shell puts the alternatives on screen
  as buttons for the user to pick.  Guessing here would throw away the reason
  for running a fixed table instead of a model.
* Effectful commands never run by themselves.  ``cdos-bridge`` refuses to emit
  them, so the shell shows the command and requires a deliberate press before
  running it through ``cdos command`` — the person at the keyboard that the
  bridge's contract assumes.
* The model only ever sees narration facts and GODCORE context, and its own
  suggestions go back through ``cdos-bridge propose`` — the same parser and the
  same validator a person at the CLI goes through.

Nothing here pipes model output into a shell.  Commands are run with an argv
list and no shell at all.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .backends import Reasoner

# `PROPOSAL  READ MEMORY` / `REFUSED  AMBIGUOUS`
_HEAD = re.compile(r"^(PROPOSAL|REFUSED)\s+(.*)$")
# `  means       check the memory — nothing on the machine changes`
_FIELD = re.compile(r"^\s{2}(means|risk|history|target|intent)\s{2,}(.*)$")
_ALT = re.compile(r"^\s*could have meant\s+(.*)$")
_HUMAN_CMD = re.compile(r"^\s+(cdos command .*)$")
_STATUS = re.compile(r"^\s*status\s{2,}(.*)$")

EFFECT_MARKER = "This would change the machine"


@dataclass
class Proposal:
    """A parsed ``cdos-bridge`` proposal."""

    verdict: str = "NO_MATCH"          # ACCEPTED | AMBIGUOUS | NO_MATCH | ...
    command: str = ""                  # "READ MEMORY"
    means: str = ""
    risk: str = ""
    history: str = ""
    refusal: str = ""
    alternatives: List[str] = field(default_factory=list)
    needs_human: bool = False
    human_command: str = ""
    raw: str = ""

    @property
    def accepted(self) -> bool:
        return self.verdict == "ACCEPTED"

    @property
    def target(self) -> str:
        parts = self.command.split()
        return parts[1] if len(parts) > 1 else ""

    @property
    def intent(self) -> str:
        parts = self.command.split()
        return parts[0] if parts else ""


def parse_proposal(text: str) -> Proposal:
    """Parse the human-readable proposal the bridge prints.

    The bridge has ``--json`` for its vocabulary but not for a proposal, so
    this reads the printed form.  It is deliberately tolerant: an unrecognised
    line is ignored rather than treated as an error, because a parser that
    hard-fails on a wording change would take the whole voice path down with
    it.
    """
    proposal = Proposal(raw=text)
    for line in text.splitlines():
        head = _HEAD.match(line)
        if head:
            kind, rest = head.group(1), head.group(2).strip()
            if kind == "PROPOSAL":
                proposal.verdict = "ACCEPTED"
                proposal.command = rest
            else:
                proposal.verdict = rest or "REFUSED"
            continue

        alt = _ALT.match(line)
        if alt:
            proposal.alternatives.append(alt.group(1).strip())
            continue

        field_match = _FIELD.match(line)
        if field_match:
            setattr(proposal, field_match.group(1), field_match.group(2).strip())
            continue

        if EFFECT_MARKER in line:
            proposal.needs_human = True
            continue

        human = _HUMAN_CMD.match(line)
        if human:
            proposal.human_command = human.group(1).strip()
            continue

        stripped = line.strip()
        if (stripped and not proposal.refusal and proposal.verdict
                not in ("ACCEPTED",) and not stripped.startswith("Nothing was run")
                and not _HEAD.match(line)):
            proposal.refusal = stripped

    return proposal


class CdosBridge:
    """Thin, safe wrapper around the ``cdos`` and ``cdos-bridge`` binaries.

    Every call is ``subprocess.run`` with an argv list.  There is no shell
    anywhere in this class, which is the same property ``make check-bridge``
    enforces on the binary itself.
    """

    def __init__(self, root: str = "", bridge: str = "cdos-bridge",
                 cli: str = "cdos", timeout: float = 20.0):
        self.root = Path(os.path.expanduser(root or "~/.local/share/earth-os/cdos"))
        self.bridge_name = bridge
        self.cli_name = cli
        self.timeout = float(timeout)
        self.last_error = ""

    # -- discovery ---------------------------------------------------------
    def _which(self, name: str) -> Optional[str]:
        """Find a binary on PATH, or beside the configured root."""
        found = shutil.which(name)
        if found:
            return found
        for candidate in (self.root / name, self.root / "pkg" / name):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    @property
    def bridge_path(self) -> Optional[str]:
        return self._which(self.bridge_name)

    @property
    def cli_path(self) -> Optional[str]:
        return self._which(self.cli_name)

    @property
    def available(self) -> bool:
        return self.bridge_path is not None

    def status(self) -> str:
        bridge, cli = self.bridge_path, self.cli_path
        if not bridge:
            return f"'{self.bridge_name}' not found (PATH or {self.root})"
        pieces = [f"bridge {bridge}"]
        pieces.append(f"cli {cli}" if cli else "cli missing (read-only)")
        pieces.append(f"root {self.root}")
        return " · ".join(pieces)

    # -- running -----------------------------------------------------------
    def _run(self, path: str, args: List[str]) -> Tuple[int, str, str]:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            done = subprocess.run(
                [path, *args], cwd=str(self.root), timeout=self.timeout,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, errors="replace", shell=False,
            )
            return (done.returncode, done.stdout, done.stderr)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return (-1, "", str(exc))

    def bridge(self, args: List[str]) -> Tuple[int, str, str]:
        path = self.bridge_path
        if not path:
            return (-1, "", "cdos-bridge not found")
        return self._run(path, args)

    def cli(self, args: List[str]) -> Tuple[int, str, str]:
        path = self.cli_path
        if not path:
            return (-1, "", "cdos not found")
        return self._run(path, args)

    # -- the bridge's four verbs ------------------------------------------
    def listen(self, phrase: str) -> Proposal:
        """Deterministic phrase to command. No model, and READ-only by design."""
        _, out, _ = self.bridge(["listen", phrase])
        return parse_proposal(out)

    def propose(self, command_text: str) -> Proposal:
        """Put a model's suggestion through CDOS's own parser and validator."""
        _, out, _ = self.bridge(["propose", command_text])
        return parse_proposal(out)

    def vocabulary(self) -> Dict:
        """The closed vocabulary, for constraining a model rather than trusting it."""
        _, out, _ = self.bridge(["vocab", "--json"])
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {}

    def narrate(self, query: str, safest: bool = False,
                guide: bool = False) -> str:
        """Bare facts for a model to put into sentences."""
        args = ["narrate", "--for", query]
        if safest:
            args.append("--safest")
        if guide:
            args.append("--guide")
        _, out, _ = self.bridge(args)
        return out.strip()

    # -- the acting side (never reached by a model) ------------------------
    def run_command(self, intent: str, target: str,
                    params: Optional[List[str]] = None) -> Tuple[bool, str]:
        """Run a semantic command through the ``cdos`` CLI.

        Only ever called for a command the deterministic table produced, or
        one a person explicitly confirmed in the interface.
        """
        args = ["command", intent, target, *(params or [])]
        code, out, err = self.cli(args)
        if code != 0:
            return (False, (err or out).strip() or "command failed")
        status = ""
        for line in out.splitlines():
            match = _STATUS.match(line)
            if match:
                status = match.group(1).strip()
                break
        return (True, status or out.strip())

    # -- readings ----------------------------------------------------------
    def system(self) -> str:
        _, out, _ = self.cli(["system"])
        return out.strip()

    def system_rows(self) -> Dict[str, Tuple[str, str]]:
        """The SYSTEM block as ``{subsystem: (condition, value)}``.

        CDOS reports an interpretation — NORMAL, FILLING, IDLE — rather than a
        bare number, which is exactly what a spoken answer wants.
        """
        rows: Dict[str, Tuple[str, str]] = {}
        block = None
        for line in self.system().splitlines():
            if not line.startswith(" "):
                block = line.strip().upper()
                continue
            if block != "SYSTEM":
                continue
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) >= 2:
                name = parts[0].strip().lower()
                condition = parts[1].strip()
                value = parts[2].strip() if len(parts) > 2 else ""
                rows[name] = (condition, value)
        return rows

    def signals(self) -> List[str]:
        """Anything CDOS considers worth raising, in its own words."""
        found: List[str] = []
        block = None
        for line in self.system().splitlines():
            if not line.startswith(" "):
                block = line.strip().upper()
                continue
            if block != "SIGNALS":
                continue
            text = line.strip()
            if text and not text.startswith("("):
                found.append(text)
        return found

    def godcore_context(self, about: str = "") -> str:
        args = ["godcore", "--context"]
        if about:
            args += ["--about", about]
        _, out, _ = self.cli(args)
        return out.strip()

    def godcore_weather(self) -> Dict[str, str]:
        _, out, _ = self.cli(["godcore", "--weather"])
        return _parse_indented(out)

    def godcore(self) -> Dict[str, str]:
        _, out, _ = self.cli(["godcore"])
        return _parse_indented(out)

    def godcore_event(self, event: str) -> None:
        """Fold one occasion into GODCORE. Advances the pulse; sets nothing."""
        self.cli(["godcore", "--event", event])


def _parse_indented(text: str) -> Dict[str, str]:
    """Read ``  Key    Value`` blocks into a flat dict, lowercased keys."""
    values: Dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith(" "):
            continue
        parts = re.split(r"\s{2,}", line.strip(), maxsplit=1)
        if len(parts) == 2 and parts[0] and parts[1]:
            values[parts[0].strip().lower().replace(" ", "_")] = parts[1].strip()
    return values


# --------------------------------------------------------------------------
# The Reasoner
# --------------------------------------------------------------------------

NARRATION_RULES = (
    "You are the voice of EARTH OS, an onboard computer. You will be given a "
    "block of bare facts produced by CDOS. Reword them for the listener in one "
    "or two short sentences, in a clipped factual register. You may not add "
    "any fact that is not in the block, you may not suggest anything not "
    "listed, and if a field says unknown you say unknown."
)


class CdosReasoner(Reasoner):
    """Routes an utterance through CDOS first, and a model only for wording.

    ``inner`` is an optional second reasoner (Ollama or cloud) used for two
    narrow jobs: turning CDOS's narration facts into a spoken sentence, and
    answering questions the deterministic table did not match.  With no inner
    reasoner the shell still works, answering from CDOS's own output.
    """

    name = "cdos"

    def __init__(self, bridge: CdosBridge, inner: Optional[Reasoner] = None,
                 narrate: bool = True, use_godcore: bool = True):
        self.bridge = bridge
        self.inner = inner
        self.narrate = narrate
        self.use_godcore = use_godcore
        #: Set when the last utterance needs the user to choose or confirm.
        self.pending: Optional[Proposal] = None
        #: The last proposal CDOS *accepted*, for the semantic layer to learn
        #: from. Only ever set after the validator passed, so nothing can be
        #: learned that the fixed table would have refused.
        self.last_command: Optional[Proposal] = None

    @property
    def available(self) -> bool:
        return self.bridge.available

    def status(self) -> str:
        detail = self.bridge.status()
        if self.inner is not None:
            detail += f" · wording via {self.inner.name}"
        return detail

    # ------------------------------------------------------------------
    def handle(self, utterance: str) -> Tuple[str, Optional[Proposal]]:
        """Run the CDOS pipeline for one utterance.

        Returns ``(reply, pending)``.  A non-None *pending* means the shell
        must ask the user something — to choose between readings, or to
        confirm a command that would change the machine.
        """
        self.pending = None
        self.last_command = None
        proposal = self.bridge.listen(utterance)

        if proposal.verdict == "AMBIGUOUS":
            self.pending = proposal
            options = ", ".join(proposal.alternatives)
            return (f"That could mean more than one thing: {options}. "
                    f"Which did you mean?", proposal)

        if proposal.accepted:
            if proposal.needs_human:
                # Cannot happen from `listen` (the table is READ-only), but a
                # future table could change and this must not become a hole.
                self.pending = proposal
                return (f"{proposal.command} would change the machine. "
                        f"Confirm to proceed.", proposal)
            return (self.execute(proposal), None)

        # Nothing in the fixed table matched. Now, and only now, a model.
        return (self.ask_model(utterance), None)

    def execute(self, proposal: Proposal) -> str:
        """Run an accepted command and say what came of it."""
        self.last_command = proposal
        ok, status = self.bridge.run_command(proposal.intent, proposal.target)
        if not ok:
            return f"{proposal.command} could not be carried out. {status}"

        if self.narrate and self.inner is not None and self.inner.available:
            facts = self.bridge.narrate(proposal.target)
            reading = self.bridge.system()
            block = f"{facts}\n\ncommand_result: {status}\n\nsystem_reading:\n{reading}"
            try:
                spoken = self.inner.ask(
                    "Say this to the listener.", NARRATION_RULES, block, [])
                if spoken:
                    return spoken
            except Exception:
                pass                      # fall through to the bare facts
        return self.speak_reading(proposal.target, status)

    def speak_reading(self, target: str, status: str = "") -> str:
        """A sentence built from CDOS's own interpretation, with no model.

        This is what keeps the shell worth using fully offline: CDOS already
        says NORMAL or FILLING rather than a bare percentage, so a readable
        answer needs wording, not reasoning.
        """
        rows = self.bridge.system_rows()
        key = target.lower()
        phrases = {
            "memory": "Memory", "cpu": "Processor", "storage": "Storage",
            "network": "Network", "process": "Processes",
            "thermal": "Thermal", "system": "All systems",
        }

        if key in rows:
            condition, value = rows[key]
            name = phrases.get(key, target.title())
            reading = f"{name} {condition.lower().replace('_', ' ')}"
            if value:
                reading += f", {value.replace('%', 'percent').strip()}"
            return reading.rstrip(".") + "."

        if key in ("system", "all") and rows:
            parts = [
                f"{phrases.get(n, n.title())} {c.lower().replace('_', ' ')}"
                for n, (c, _v) in rows.items() if c and c != "UNKNOWN"
            ]
            if parts:
                summary = "; ".join(parts) + "."
                for signal in self.bridge.signals():
                    summary += f" {signal}"
                return summary

        return status or f"{target or 'Command'} complete."

    def confirm(self, proposal: Proposal) -> str:
        """Carry out a proposal the user explicitly confirmed in the interface."""
        self.pending = None
        return self.execute(proposal)

    def choose(self, command_text: str) -> str:
        """Resolve an ambiguity with the reading the user picked."""
        self.pending = None
        checked = self.bridge.propose(command_text)
        if not checked.accepted:
            return f"CDOS refused that reading. {checked.refusal}".strip()
        if checked.needs_human:
            self.pending = checked
            return (f"{checked.command} would change the machine. "
                    f"Confirm to proceed.")
        return self.execute(checked)

    # ------------------------------------------------------------------
    def ask_model(self, prompt: str) -> str:
        """Let a model try, constrained by CDOS's closed vocabulary.

        Anything it emits goes back through ``cdos-bridge propose``, so its
        authority is exactly that of a person at the keyboard with no hands.
        """
        if self.inner is None or not self.inner.available:
            return ("I did not recognise that as something I can check. "
                    "Try naming one thing: memory, storage, network, "
                    "temperature.")

        vocabulary = self.bridge.vocabulary()
        context_parts = [f"closed_vocabulary: {json.dumps(vocabulary)}"]
        if self.use_godcore:
            godcore = self.bridge.godcore_context()
            if godcore:
                context_parts.append(godcore)
        context_parts.append(self.bridge.system())

        system = (
            "You are the voice of EARTH OS, an onboard computer running CDOS. "
            "You do not act; you propose. If the request maps onto the closed "
            "vocabulary you were given, reply with exactly one line of the "
            "form 'COMMAND: INTENT TARGET' and nothing else. If it does not, "
            "answer the question directly in one or two short sentences, in a "
            "clipped factual register, using only the system reading you were "
            "given. Never invent a reading."
        )
        try:
            answer = self.inner.ask(prompt, system, "\n\n".join(context_parts), [])
        except Exception as exc:
            return f"Reasoning subsystem unreachable. {exc}"

        answer = (answer or "").strip()
        if not answer:
            return "No response."

        # If the model proposed a command, it must survive CDOS's validator.
        for line in answer.splitlines():
            if line.strip().upper().startswith("COMMAND:"):
                suggestion = line.split(":", 1)[1].strip()
                checked = self.bridge.propose(suggestion)
                if not checked.accepted:
                    return (f"I considered {suggestion}, but CDOS refused it. "
                            f"{checked.refusal}").strip()
                if checked.needs_human:
                    self.pending = checked
                    return (f"{checked.command} would change the machine. "
                            f"Confirm to proceed.")
                return self.execute(checked)
        return answer

    # ------------------------------------------------------------------
    def ask(self, prompt: str, system: str, context: str,
            history: List[Dict[str, str]]) -> str:
        """Reasoner interface, so CDOS can be dropped in as ``llm = "cdos"``."""
        reply, _pending = self.handle(prompt)
        return reply
