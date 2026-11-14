"""The shell and CDOS Computer, connected — tested against the real thing.

The service tests start ``python3 -m computer.services.api`` from the
``cdos-computer/`` tree inside this one, on a private Unix socket, with the
computer's own test corpus. That is the deployment shape: a separate process,
an identity the kernel vouches for, and HTTP with JSON bodies. Nothing about
the computer is faked in them.

What they hold the integration to:

* an answer from the corpus is spoken exactly as the computer gave it;
* NOT FOUND stays NOT FOUND — no model fills the gap unless the person turned
  ``free_answers`` on, and then the model's words are labelled unverified;
* a NOTICE (health, money, safety) never reaches a model at all;
* an unreachable computer is reported, never papered over;
* the embedded path serves only what the Governor's default roles allow.

The classification tests at the top need neither tree and run everywhere.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earth_os.voice.cdos_backend import CdosBridge, CdosReasoner   # noqa: E402
from earth_os.voice.computer_link import (                          # noqa: E402
    Answer, ComputerLink, LinkError, find_source, health_rows,
)
from earth_os.voice.backends import Reasoner                        # noqa: E402

SOURCE = ROOT / "cdos-computer"
HAVE_SOURCE = (SOURCE / "computer" / "services" / "api.py").is_file()


def build_mini_corpus(dest: Path) -> Path:
    """The computer's own test corpus, written by its own builder."""
    spec = importlib.util.spec_from_file_location(
        "cdos_mini_corpus", SOURCE / "tests" / "mini_corpus.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.build(dest)


def write_computer_config(tmp: Path, corpus: Path) -> Path:
    cfg = tmp / "computer.toml"
    cfg.write_text(
        f'corpus = "{corpus}"\n'
        'llm_mode = "off"\nllm_endpoint = ""\n'
        f'log_file = "{tmp / "computer.log.jsonl"}"\n',
        encoding="utf-8")
    return cfg


class _NoBridge(CdosBridge):
    """cdos-bridge as it is on most machines: not installed."""

    def __init__(self):
        super().__init__(root=tempfile.mkdtemp(), bridge="cdos-bridge-absent-for-test",
                         cli="cdos-absent-for-test")

    @property
    def available(self):
        return False


class _InventingModel(Reasoner):
    """A model that answers anything, confidently, and records being asked."""

    name = "inventing"

    def __init__(self):
        self.asked = []

    @property
    def available(self):
        return True

    def ask(self, prompt, system, context, history):
        self.asked.append(prompt)
        return "The capital of Narnia is Cair Paravel, population 40,000."


class _FixedLink(ComputerLink):
    """A link that returns one canned reply, for the decision tests."""

    def __init__(self, answer: Answer):
        super().__init__(mode="off")
        self._answer = answer
        self.asked = []

    @property
    def available(self):
        return True

    def ask(self, question):
        self.asked.append(question)
        return self._answer


def _reply(text, faculty="knowledge", status="answered", certainty="ESTABLISHED", evidence=()):
    return Answer(text=text, faculty=faculty, status=status, certainty=certainty,
                  envelope={"evidence": list(evidence)}, via="socket", reached=True)


# ---------------------------------------------------------------------------
# What may answer in the computer's place
# ---------------------------------------------------------------------------

class TestWhatIsFinal(unittest.TestCase):

    def test_a_fact_from_the_corpus_is_final(self):
        self.assertTrue(_reply("Jupiter's number of moons is 95.").final)

    def test_a_calculation_is_final(self):
        self.assertTrue(_reply("12 times 7 is 84.", faculty="logic").final)

    def test_not_found_is_not_final(self):
        a = _reply("NOT FOUND: no matching fact in the corpus.", status="unsupported",
                   certainty="UNKNOWN")
        self.assertTrue(a.not_found)
        self.assertFalse(a.final)

    def test_unsupported_is_not_final_even_when_answered(self):
        # The computer files "turn off the lights" under answered/logic, but
        # what it says is that it cannot.
        a = _reply("UNSUPPORTED: this system has no device control or internet access.",
                   faculty="logic", certainty="UNKNOWN")
        self.assertFalse(a.final)

    def test_a_notice_is_final_whatever_its_status(self):
        a = _reply("NOTICE: for questions about your own body, consult a doctor.",
                   status="unsupported", certainty="UNKNOWN")
        self.assertTrue(a.notice)
        self.assertTrue(a.final)

    def test_an_unreached_computer_is_never_final(self):
        a = Answer.failed("connection refused")
        self.assertFalse(a.final)
        self.assertFalse(a.reached)
        self.assertEqual(a.text, "")

    def test_an_unavailable_corpus_is_not_final(self):
        a = _reply("ERROR: corpus unavailable. No answer generated; facts are not guessed.",
                   status="unavailable", certainty="UNKNOWN")
        self.assertFalse(a.final)


class TestTheModelNeverAnswersInTheComputersPlace(unittest.TestCase):
    """The decision table, with the computer's replies held fixed."""

    def reasoner(self, answer, free=False, model=True):
        inner = _InventingModel() if model else None
        link = _FixedLink(answer)
        return CdosReasoner(_NoBridge(), inner=inner, computer=link, free_answers=free), inner, link

    def test_a_final_answer_is_spoken_verbatim_and_no_model_is_asked(self):
        text = "Jupiter's number of moons is 95 (since 2023)."
        r, model, _ = self.reasoner(_reply(text))
        reply, pending = r.handle("how many moons does jupiter have")
        self.assertEqual(reply, text)
        self.assertIsNone(pending)
        self.assertEqual(model.asked, [])

    def test_not_found_stays_not_found(self):
        text = "NOT FOUND: no matching fact in the corpus. No answer generated; facts are not guessed."
        r, model, _ = self.reasoner(_reply(text, status="unsupported", certainty="UNKNOWN"))
        reply, _ = r.handle("what is the capital of narnia")
        self.assertEqual(reply, text)
        self.assertNotIn("Cair Paravel", reply)
        # With no cdos-bridge there is no validator for a command either, so
        # there is nothing the model could contribute: it is not even asked.
        self.assertEqual(model.asked, [])

    def test_free_answers_are_labelled_unverified(self):
        r, model, _ = self.reasoner(
            _reply("NOT FOUND: no matching fact in the corpus.", status="unsupported",
                   certainty="UNKNOWN"), free=True)
        reply, _ = r.handle("what is the capital of narnia")
        self.assertEqual(len(model.asked), 1)
        self.assertTrue(reply.startswith("Not in the corpus. Unverified model answer:"), reply)

    def test_a_notice_never_reaches_a_model_even_with_free_answers(self):
        text = "NOTICE: for questions about your own body, consult a doctor, nurse or pharmacist."
        r, model, _ = self.reasoner(_reply(text, status="unsupported", certainty="UNKNOWN"),
                                    free=True)
        reply, _ = r.handle("should i stop taking my medicine")
        self.assertEqual(reply, text)
        self.assertEqual(model.asked, [])

    def test_the_computer_is_asked_when_the_bridge_is_absent(self):
        r, _, link = self.reasoner(_reply("12 times 7 is 84.", faculty="logic"), model=False)
        self.assertTrue(r.available)              # used to be False with no bridge
        reply, _ = r.handle("what is 12 times 7")
        self.assertEqual(link.asked, ["what is 12 times 7"])
        self.assertEqual(reply, "12 times 7 is 84.")
        self.assertIs(r.last_answer.final, True)

    def test_an_unreachable_computer_falls_back_as_before(self):
        r, _, _ = self.reasoner(Answer.failed("/run/cdos/api.sock is not open"), model=False)
        reply, _ = r.handle("what is the capital of narnia")
        # With no model, the failure is the answer — and it says why.
        self.assertEqual(reply, "Knowledge core unreachable. /run/cdos/api.sock is not open")
        self.assertIsNone(r.last_answer)

    def test_an_unreachable_computer_lets_a_model_try_as_before(self):
        r, model, _ = self.reasoner(Answer.failed("connection refused"), model=True)
        r.handle("what is the capital of narnia")
        self.assertEqual(len(model.asked), 1)


class TestHealthRows(unittest.TestCase):

    def test_rows_from_a_real_shaped_health_reply(self):
        rows = dict(health_rows({
            "via": "socket",
            "corpus": {"ready": True, "version": "sha256:0123456789abcdef", "integrity": "verified"},
            "neural": {"model_id": None},
            "plugins": [{"name": "tmp117"}],
            "capabilities_off": {"planner.lm": "no endpoint"},
            "governor": {"degraded": False, "policy": {"loaded": True},
                         "latch": {"engaged": True}},
        }))
        self.assertEqual(rows["CORPUS"], "ready")
        self.assertEqual(rows["VERSION"], "0123456789ab")
        self.assertEqual(rows["NEURAL"], "rules only")
        self.assertEqual(rows["GOVERNOR"], "ok")
        self.assertEqual(rows["E-STOP"], "ENGAGED")
        self.assertEqual(rows["OFF"], "1")

    def test_missing_fields_are_said_not_guessed(self):
        rows = dict(health_rows({"via": "socket"}))
        self.assertEqual(rows["CORPUS"], "unavailable")
        self.assertEqual(rows["GOVERNOR"], "unknown")
        self.assertEqual(rows["E-STOP"], "unknown")


class TestLinkWithoutAComputer(unittest.TestCase):

    def test_off_is_off(self):
        link = ComputerLink(mode="off")
        self.assertFalse(link.available)
        self.assertFalse(link.ask("hello").reached)

    def test_a_missing_socket_is_unreachable_and_says_where(self):
        link = ComputerLink(mode="socket", socket_path="/nonexistent/cdos/api.sock")
        answer = link.ask("hello")
        self.assertFalse(answer.reached)
        self.assertIn("/nonexistent/cdos/api.sock", answer.error)
        with self.assertRaises(LinkError):
            link.health()

    def test_a_file_that_is_not_a_socket_is_not_mistaken_for_one(self):
        tmp = Path(tempfile.mkdtemp())
        fake = tmp / "api.sock"
        fake.write_text("not a socket")
        link = ComputerLink(mode="auto", socket_path=str(fake), source=str(tmp / "nowhere"),
                            service_config="")
        self.assertIsNot(link.transport(), link.socket)

    def test_a_machine_with_the_service_never_quietly_embeds(self):
        tmp = Path(tempfile.mkdtemp())
        marker = tmp / "config.toml"
        marker.write_text("")
        link = ComputerLink(mode="auto", socket_path=str(tmp / "api.sock"),
                            source=str(SOURCE), service_config=str(marker))
        self.assertIs(link.transport(), link.socket)
        self.assertIn("is cdos-api.service running", link.status())
        answer = link.ask("what is 2 plus 2")
        self.assertFalse(answer.reached)
        self.assertIn("is cdos-api.service running", answer.error)

    def test_an_empty_question_is_not_sent(self):
        link = ComputerLink(mode="socket", socket_path="/nonexistent")
        self.assertEqual(link.ask("   ").error, "empty question")


# ---------------------------------------------------------------------------
# The real service
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_SOURCE, "cdos-computer/ is not in this tree")
class TestAgainstTheRealService(unittest.TestCase):
    """cdos-api in its own process, on a private socket, with the test corpus."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cdos-link-"))
        corpus = build_mini_corpus(cls.tmp / "data")
        cls.config = write_computer_config(cls.tmp, corpus)
        cls.sock = cls.tmp / "api.sock"
        cls.server = cls.start(cls.config, cls.sock)

    @classmethod
    def start(cls, config: Path, sock: Path) -> subprocess.Popen:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.Popen(
            [sys.executable, "-m", "computer.services.api", "--config", str(config),
             "--socket", str(sock)],
            cwd=str(SOURCE), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if sock.exists():
                return proc
            if proc.poll() is not None:
                raise RuntimeError("cdos-api exited: " + proc.stderr.read().decode(errors="replace"))
            time.sleep(0.05)
        proc.kill()
        raise RuntimeError("cdos-api did not open its socket within 30 s")

    @classmethod
    def tearDownClass(cls):
        if cls.server.poll() is None:
            cls.server.send_signal(signal.SIGINT)
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()
        cls.server.stderr.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def link(self, **kw) -> ComputerLink:
        kw.setdefault("mode", "socket")
        return ComputerLink(socket_path=str(self.sock), **kw)

    # -- the link itself -----------------------------------------------------
    def test_a_fact_comes_back_with_its_evidence(self):
        a = self.link().ask("how many moons does jupiter have")
        self.assertTrue(a.reached, a.error)
        self.assertEqual(a.via, "socket")
        self.assertEqual(a.faculty, "knowledge")
        self.assertEqual(a.certainty, "ESTABLISHED")
        self.assertTrue(a.final)
        self.assertIn("95", a.text)
        self.assertTrue(any(e.get("subject") == "JUPITER" for e in a.evidence), a.evidence)

    def test_an_inference_says_it_is_one(self):
        a = self.link().ask("is a whale a mammal")
        self.assertEqual(a.truth, "TRUE")
        self.assertEqual(a.certainty, "INFERRED")

    def test_arithmetic_is_the_calculators(self):
        a = self.link().ask("what is 12 times 7")
        self.assertEqual(a.faculty, "logic")
        self.assertIn("84", a.text)

    def test_a_gap_in_the_corpus_is_not_found(self):
        a = self.link().ask("what is the capital of narnia")
        self.assertTrue(a.reached)
        self.assertTrue(a.not_found, a.text)
        self.assertFalse(a.final)

    def test_health_policy_answers_with_a_notice(self):
        a = self.link().ask("should i stop taking my medicine")
        self.assertTrue(a.notice, a.text)
        self.assertTrue(a.final)

    def test_health_reports_the_corpus(self):
        health = self.link().health()
        rows = dict(health_rows(health))
        self.assertEqual(rows["LINK"], "socket")
        self.assertEqual(rows["CORPUS"], "ready")
        # No policy in this config: the Governor says so rather than pretending.
        self.assertEqual(rows["GOVERNOR"], "no policy")

    def test_the_kernel_vouches_for_who_is_asking(self):
        me = self.link().whoami()
        self.assertTrue(me["identity"]["verified"])
        self.assertEqual(me["identity"]["uid"], os.getuid())
        self.assertIn("api.ask", me["may"])

    def test_auto_prefers_the_service_to_embedding(self):
        link = self.link(mode="auto", source=str(SOURCE), service_config="")
        self.assertIs(link.transport(), link.socket)
        self.assertEqual(link.ask("what is 2 plus 2").via, "socket")

    # -- through the reasoner ------------------------------------------------
    def test_reasoner_speaks_the_computers_answer_and_not_the_models(self):
        model = _InventingModel()
        r = CdosReasoner(_NoBridge(), inner=model, computer=self.link())
        reply, _ = r.handle("how many moons does jupiter have")
        self.assertIn("95", reply)
        reply, _ = r.handle("what is the capital of narnia")
        self.assertTrue(reply.startswith("NOT FOUND"), reply)
        self.assertEqual(model.asked, [])

    # -- through the whole shell --------------------------------------------
    def test_the_shell_answers_a_spoken_question_from_the_corpus(self):
        from earth_os.config import Config
        from earth_os.core import Shell
        from earth_os.voice.engine import VoiceEngine

        config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
        config.values["voice"].update({"enabled": False, "stt": "none", "tts": "none", "llm": "cdos"})
        config.values["cdos"].update({"wording": "none", "bridge": "cdos-bridge-absent-for-test"})
        config.values["computer"].update({"mode": "socket", "socket": str(self.sock)})
        # The pre-companion path: tier 4 straight to CdosReasoner. The
        # companion's own use of the computer is in test_companion.py.
        config.values["companion"]["enabled"] = False
        shell = Shell(config)
        shell.voice = VoiceEngine(shell)
        self.assertTrue(shell.voice.can_reason)

        shell.handle_utterance("how many moons does jupiter have")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and (
                shell.thinking or shell.conversation[-1]["role"] != "computer"):
            time.sleep(0.05)
        last = shell.conversation[-1]
        self.assertEqual(last["role"], "computer")
        self.assertIn("95", last["text"])
        self.assertTrue(shell.status_message.startswith("computer · knowledge · ESTABLISHED"),
                        shell.status_message)
        self.assertIn(("COMPUTER", shell.voice.computer.status()), shell.voice.status_rows())

    def test_engineering_panel_reads_the_service(self):
        from earth_os.config import Config
        from earth_os.core import Shell
        from earth_os.voice.engine import VoiceEngine

        config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
        config.values["voice"].update({"enabled": False, "stt": "none", "tts": "none", "llm": "cdos"})
        config.values["cdos"].update({"wording": "none", "bridge": "cdos-bridge-absent-for-test"})
        config.values["computer"].update({"mode": "socket", "socket": str(self.sock)})
        shell = Shell(config)
        shell.voice = VoiceEngine(shell)
        shell.refresh_cdos(interval=0)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and "computer" not in shell.cdos_snapshot:
            time.sleep(0.05)
        self.assertEqual(shell.cdos_snapshot["computer"]["via"], "socket")
        self.assertNotIn("error", shell.cdos_snapshot)

    # -- when the service goes away -----------------------------------------
    def test_a_service_that_dies_is_reported_not_papered_over(self):
        tmp = Path(tempfile.mkdtemp(prefix="cdos-link-die-"))
        sock = tmp / "api.sock"
        proc = self.start(self.config, sock)
        try:
            link = ComputerLink(mode="socket", socket_path=str(sock))
            self.assertTrue(link.ask("what is 2 plus 2").reached)
            proc.kill()
            proc.wait(timeout=10)
            answer = link.ask("what is 2 plus 2")
            self.assertFalse(answer.reached)
            self.assertTrue(answer.error)
            self.assertIn("last error", link.status())
            r = CdosReasoner(_NoBridge(), inner=None, computer=link)
            reply, _ = r.handle("what is 2 plus 2")
            # Nothing answered it, so nothing claims to: the failure is the reply.
            self.assertTrue(reply.startswith("Knowledge core unreachable."), reply)
            self.assertIn("is not open", reply)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stderr.close()
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(HAVE_SOURCE, "cdos-computer/ is not in this tree")
class TestEmbedded(unittest.TestCase):
    """The computer's code in the shell's process, for a checkout with no service."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cdos-embed-"))
        corpus = build_mini_corpus(cls.tmp / "data")
        cls.config = write_computer_config(cls.tmp, corpus)
        cls.link = ComputerLink(mode="embedded", source=str(SOURCE), config=str(cls.config))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_source_beside_this_tree_is_found(self):
        self.assertEqual(find_source(), SOURCE.resolve())

    def test_it_starts_on_first_question_not_before(self):
        link = ComputerLink(mode="embedded", source=str(SOURCE), config=str(self.config))
        self.assertTrue(link.available)
        self.assertFalse(link.embedded.built)
        self.assertIn("starts on first question", link.status())

    def test_it_answers_from_the_corpus(self):
        a = self.link.ask("how many moons does jupiter have")
        self.assertTrue(a.reached, a.error)
        self.assertEqual(a.via, "embedded")
        self.assertIn("95", a.text)
        self.assertTrue(a.final)

    def test_it_reports_health(self):
        self.assertEqual(dict(health_rows(self.link.health()))["CORPUS"], "ready")

    def test_nothing_privileged_is_served_in_process(self):
        with self.assertRaises(LinkError) as ctx:
            self.link.whoami()
        self.assertIn("needs the cdos-api service", ctx.exception.why)

    def test_the_shells_modules_are_not_shadowed(self):
        # A fresh interpreter, because the computer's own test helper (used to
        # build the corpus above) puts its tree at the front of sys.path.
        probe = (
            "import sys; sys.path.insert(0, %r)\n"
            "from earth_os.voice.computer_link import ComputerLink\n"
            "link = ComputerLink(mode='embedded', source=%r, config=%r)\n"
            "assert link.ask('what is 2 plus 2').reached\n"
            "import earth_os\n"
            "print(earth_os.__file__)\n"
            "print(sys.path[-1])\n" % (str(ROOT), str(SOURCE), str(self.config)))
        out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True,
                             text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        shell_file, last = out.stdout.strip().splitlines()
        self.assertEqual(Path(shell_file).resolve().parent, ROOT / "earth_os")
        self.assertEqual(Path(last), SOURCE.resolve())     # appended, never prepended

    def test_a_broken_source_is_an_error_not_a_crash(self):
        bad = Path(tempfile.mkdtemp())
        (bad / "computer" / "services").mkdir(parents=True)
        (bad / "computer" / "core.py").write_text("")
        (bad / "computer" / "services" / "platform.py").write_text("raise RuntimeError('broken')\n")
        (bad / "computer" / "__init__.py").write_text("")
        (bad / "computer" / "services" / "__init__.py").write_text("")
        (bad / "computer" / "config.py").write_text("def load(p=None):\n    return object()\n")
        # A fresh interpreter: in this one the real computer is already
        # imported, and the first import of a package wins.
        probe = (
            "import sys; sys.path.insert(0, %r)\n"
            "from earth_os.voice.computer_link import ComputerLink\n"
            "link = ComputerLink(mode='embedded', source=%r)\n"
            "a = link.ask('hello')\n"
            "print(a.reached, '|', a.error)\n"
            "print(link.available, '|', link.status())\n" % (str(ROOT), str(bad)))
        out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        first, second = out.stdout.strip().splitlines()
        self.assertTrue(first.startswith("False |"), first)
        self.assertIn("broken", first)
        # Once failed, it stays failed and says why, rather than retrying the
        # import on every utterance.
        self.assertTrue(second.startswith("False |"), second)
        self.assertIn("embedded computer failed to start", second)


class TestConfig(unittest.TestCase):

    def test_the_default_file_carries_the_computer_section(self):
        from earth_os.config import DEFAULT_TOML
        import tomllib
        block = tomllib.loads(DEFAULT_TOML)["computer"]
        self.assertEqual(block["mode"], "auto")
        self.assertIs(block["free_answers"], False)

    def test_the_built_in_default_matches_the_written_file(self):
        """The first launch, before the file exists, uses the same brain as every later one."""
        from earth_os.config import DEFAULTS, DEFAULT_TOML
        import tomllib
        self.assertEqual(DEFAULTS["voice"]["llm"], tomllib.loads(DEFAULT_TOML)["voice"]["llm"])
        self.assertEqual(DEFAULTS["voice"]["llm"], "cdos")

    def test_disabled_in_config_means_off(self):
        from earth_os.config import Config
        config = Config.load(Path(tempfile.mkdtemp()) / "config.toml")
        config.values["computer"]["enabled"] = False
        self.assertEqual(ComputerLink.from_config(config).mode, "off")


if __name__ == "__main__":
    unittest.main()
