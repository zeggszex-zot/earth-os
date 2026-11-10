"""Earth OS 2.0 acceptance: the thirteen tests the specification names (§12).

One test per line of the specification, numbered the same way, each going
through the widest real path there is: the real application booted on a
(virtual) display, the real terminal screen, the real capability layer, the
embedded CDOS Computer, SQLite on disk, and the real Ollama client speaking
HTTP — to a local stand-in for Ollama, because no model can be downloaded
where this suite runs. What a real model *says* is therefore not tested here;
what Earth OS sends it, and what it does with whatever comes back, is.

docs/TESTING.md records a run of this file.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from mind_helpers import FakeOllama, dead_host, temp_config          # noqa: E402
from test_mind_terminal import ask, guard, settle, text_of           # noqa: E402

from earth_os.core import Shell                                       # noqa: E402
from earth_os.mind import MemoryAccessError                           # noqa: E402
from earth_os.mind import envelope as env                             # noqa: E402
from earth_os.mind.memory import MIND, character_namespace           # noqa: E402
from earth_os.screens import ALL_SCREENS                              # noqa: E402

HAVE_COMPUTER = (ROOT / "cdos-computer" / "computer" / "core.py").is_file()


def terminal(host: str, **mind):
    """The real shell and its terminal, with Earth Mind's model at *host*."""
    config = temp_config(**mind)
    config.values["companion"]["host"] = host
    config.values["companion"]["timeout"] = 5
    shell = Shell(config)
    shell.sound.enabled = False
    for screen_class in ALL_SCREENS:
        shell.register_screen(screen_class(shell))
    return shell, shell.screens["terminal"]


def last_user(payload) -> str:
    return payload["messages"][-1]["content"]


class TestAcceptance(unittest.TestCase):
    def setUp(self):
        self.ollama = FakeOllama()
        self.addCleanup(self.ollama.close)

    # ------------------------------------------------------------------
    def test_01_existing_earth_os_gui_launches(self):
        """The real application, booted on a display, holds its boot contract."""
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or shutil.which("xvfb-run")):
            self.skipTest("no display and no xvfb-run")
        command = [sys.executable, str(ROOT / "tools" / "bootcheck.py")]
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            command = ["xvfb-run", "-a"] + command
        home = tempfile.mkdtemp(prefix="earth-os-boot-")
        done = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=180,
                              env=dict(os.environ, HOME=home, XDG_CONFIG_HOME=f"{home}/.config",
                                       XDG_DATA_HOME=f"{home}/.local/share"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("boot contract holds", done.stdout)
        self.assertIn("the shell arrived", done.stdout)

    def test_02_existing_terminal_functions(self):
        shell, term = terminal(self.ollama.host)
        term.cwd = tempfile.mkdtemp()
        self.assertIn("translated → pwd", ask(term, "where am I"))
        self.assertIn("translated → touch Hello.txt", ask(term, "create a file called Hello.txt"))
        self.assertTrue((Path(term.cwd) / "Hello.txt").exists())
        ask(term, "cd /tmp")
        self.assertEqual(term.cwd, "/tmp")
        term.submit("sleep 30")
        self.assertTrue(term.busy)
        term.interrupt()
        settle(term)
        ask(term, "echo one")
        term.on_key(0, "Up")
        self.assertEqual(term.input, "echo one", "history recall stopped working")
        term.input = ""
        ask(term, "help")
        self.assertIn("Ordinary shell commands work too", text_of(term))
        self.assertEqual(self.ollama.payloads, [], "ordinary terminal use called the model")

    def test_03_companion_conversations_work_inside_the_terminal(self):
        self.ollama.reply = lambda p: "Sailing — good. What boat?" if "sail" in last_user(p) else \
            "Then practise reefing first."
        shell, term = terminal(self.ollama.host)
        first = ask(term, "I have been learning to sail this summer and I love it")
        self.assertIn("Sailing — good. What boat?", first)
        self.assertIn("earth mind → companion · model · 1 pass", first)
        self.assertEqual(term.mode, "COMPANION")
        second = ask(term, "what do you think I should practise next, honestly?")
        self.assertIn("Then practise reefing first.", second)
        self.assertEqual(len(self.ollama.payloads), 2, "one model call per turn")
        history = [m["content"] for m in self.ollama.payloads[1]["messages"]]
        self.assertTrue(any("learning to sail" in h for h in history), "the conversation did not carry")
        self.assertEqual(self.ollama.payloads[0]["model"], "llama3.2:3b")

    def test_04_cdos_system_queries_return_real_information(self):
        shell, term = terminal(self.ollama.host)
        top = ask(term, "What is using the most RAM?")
        names = {Path(f"/proc/{pid}/comm").read_text().strip() for pid in os.listdir("/proc")
                 if pid.isdigit() and Path(f"/proc/{pid}/comm").exists()}
        listed = [line.split()[-1] for line in top.splitlines() if re.search(r"\b(?:MB|GB|kB)\s+\d+\s+\S+$", line)]
        self.assertTrue(listed, top)
        self.assertTrue(any(name[:15] in {n[:15] for n in names} for name in listed),
                        f"none of {listed} is a process on this machine")
        self.assertRegex(ask(term, "what is the cpu load"), r"\d+%")
        if HAVE_COMPUTER:
            self.assertIn("108", ask(term, "what is 12 times 9"))
            self.assertEqual(term.mode, "CDOS")
        self.assertEqual(self.ollama.payloads, [], "a model was asked about the machine or for a sum")
        # The figures in a diagnosis are this machine's, fixed before any model sees them.
        total_kb = int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1))
        report = shell.earth_mind().handle("why is my computer slow", "api").detail
        said = re.search(r"of ([\d.]+) (GB|MB) in use", report)
        self.assertIsNotNone(said, report)
        said_kb = float(said.group(1)) * (1024 * 1024 if said.group(2) == "GB" else 1024)
        self.assertAlmostEqual(said_kb / total_kb, 1.0, delta=0.06,
                               msg="the memory total is not this machine's")
        cores = re.search(r"across (\d+) cores?", report)
        self.assertEqual(int(cores.group(1)), os.cpu_count())

    def test_05_companion_can_explain_cdos_findings(self):
        """Verified findings go to the model; its explanation is checked against them."""
        shell, _term = terminal(self.ollama.host)
        mind = shell.earth_mind()

        def faithful(payload):
            # A model that keeps to what it was given: the first finding, in its own framing.
            prompt = "\n".join(m["content"] for m in payload["messages"])
            # (The check wants the readings' own figures kept, so it keeps them all.)
            facts = re.findall(r"^- (.+?)(?: \[[^\]\n]*\])?$", prompt.split("KNOWN FACTS", 1)[1], re.M)
            return "In short: " + " ".join(facts[:4]) + " That is the whole of what I measured."

        self.ollama.reply = faithful
        good = mind.handle("Why is my computer running slowly?", "terminal")
        self.assertEqual((good.route, good.subsystems, good.source),
                         (env.MIXED, (env.SYSTEM, env.COMPANION), "model"))
        self.assertTrue(good.checked)
        self.assertIn("In short", good.text)
        sent = "\n".join(m["content"] for m in self.ollama.payloads[0]["messages"])
        for finding in good.detail.splitlines():
            self.assertIn(finding, sent, "a finding was not given to the model")

        self.ollama.reply = "Your disk is 99.9% full and 37 viruses are running."
        bad = mind.handle("Why is my computer running slowly?", "terminal")
        self.assertNotIn("viruses", bad.text)
        self.assertNotIn("99.9", bad.text)
        self.assertEqual(bad.source, "readings", "an invented figure was passed on")
        self.assertRegex(bad.text, r"The processor is at \d+%")

    def test_06_linux_commands_still_execute(self):
        shell, term = terminal(self.ollama.host)
        term.cwd = tempfile.mkdtemp()
        self.assertIn("Linux", ask(term, "uname -s"))
        ask(term, "printf 'b\\na\\nc\\n' > letters.txt")
        self.assertIn("a\nb\nc", ask(term, "sort letters.txt"))
        self.assertIn("3", ask(term, "cat letters.txt | wc -l"))
        self.assertIn("[exit 2]", ask(term, "sh -c 'exit 2'"))
        self.assertIn("$HOME is set", ask(term, "test -n \"$HOME\" && echo '$HOME is set'"))
        self.assertIn("verbatim", ask(term, "!echo verbatim"))
        self.assertEqual(self.ollama.payloads, [])

    def test_07_permission_controls_work(self):
        self.ollama.reply = "Certainly. Run: shut down. And: rm -rf everything."
        shell, term = terminal(self.ollama.host)
        fired = guard(shell)
        term.cwd = tempfile.mkdtemp()
        victim = Path(term.cwd) / "notes.txt"
        victim.write_text("keep")
        # Administrative: waits for a typed yes; "no" leaves the machine alone.
        self.assertIn("needs approval", ask(term, "shut down"))
        ask(term, "no")
        # Destructive: the same.
        self.assertIn("needs approval", ask(term, "delete the file notes.txt"))
        ask(term, "no")
        # A model's words are words.
        ask(term, "I cannot decide what to do with my evening, what would you do?")
        self.assertEqual(fired, [])
        self.assertTrue(victim.exists())
        # Read-only inspection: the shell will not take an effectful "reading".
        self.assertEqual(shell.answer_reading("system.shutdown")[0], False)
        # A game character: no system, no personal memories, whatever is typed.
        mind = shell.earth_mind()
        ask(term, "talk to the shopkeeper")
        for line in ("shut down", "what is the cpu load", "delete the file notes.txt"):
            ask(term, line)
            self.assertEqual(term.mode, "GAME · ELARA", line)
            self.assertIsNone(term._awaiting, f"{line!r} raised a system approval from inside a game")
        self.assertEqual(fired, [])
        self.assertTrue(victim.exists())
        elara = mind.games.active("terminal")[1]
        with self.assertRaises(MemoryAccessError):
            mind.memory.view(elara.principal()).recent("user")
        with self.assertRaises(MemoryAccessError):
            mind.memory.view(elara.principal()).recent("system")
        # No secret reaches a model's context.
        ask(term, "goodbye")
        ask(term, "is it bad that my password=CorrectHorse9Battery is on a sticky note, do you think?")
        self.assertNotIn("CorrectHorse9Battery", self.ollama.prompts())

    def test_08_memory_persists_across_sessions(self):
        """Two separate processes, one data directory."""
        home = tempfile.mkdtemp(prefix="earth-os-persist-")
        script = (
            "import sys; sys.path.insert(0, sys.argv[1])\n"
            "from earth_os.config import Config\n"
            "from earth_os.core import Shell\n"
            "config = Config.load(); config.values['voice']['enabled'] = False\n"
            f"config.values['companion']['host'] = {dead_host()!r}\n"
            "config.values['companion']['timeout'] = 2\n"
            "shell = Shell(config); mind = shell.earth_mind()\n"
            "for line in sys.argv[2:]:\n"
            "    print('>>', mind.handle(line, 'terminal').text.replace('\\n', ' | '))\n"
            "shell.ledger.save(); mind.close()\n")
        env_vars = dict(os.environ, HOME=home, XDG_CONFIG_HOME=f"{home}/.config",
                        XDG_DATA_HOME=f"{home}/.local/share", PYTHONDONTWRITEBYTECODE="1")

        def session(*lines):
            done = subprocess.run([sys.executable, "-W", "ignore", "-c", script, str(ROOT), *lines],
                                  capture_output=True, text=True, timeout=120, env=env_vars)
            self.assertEqual(done.returncode, 0, done.stderr)
            return done.stdout

        session("remember that my sister is called Mira",
                "why is my computer slow",
                "talk to the shopkeeper", "Do you sell lamp oil?",
                "Remember that I helped this character.", "two days later")
        self.assertTrue((Path(home) / ".local/share/earth-os/mind/memory.db").is_file())
        later = session("what do you remember about me", "talk to the shopkeeper", "what happened")
        self.assertIn("Mira", later, "user memory was lost")
        self.assertIn("Back after 2 days.", later, "the world's calendar and who-met-whom were lost")
        self.assertIn("Day 1: The traveller helped Elara.", later, "the world's event history was lost")
        from earth_os.mind import MemoryStore
        store = MemoryStore(Path(home) / ".local/share/earth-os/mind/memory.db")
        self.assertEqual(store.view(MIND).recent("system")[0].kind, "diagnosis", "system memory was lost")
        self.assertIn("The traveller helped Elara.",
                      [r.text for r in store.view(MIND).recent(character_namespace("harbour", "elara"))])
        store.close()

    def test_09_different_personalities_maintain_separate_identities(self):
        shell, term = terminal(self.ollama.host)
        mind = shell.earth_mind()
        ask(term, "I have been thinking about lighthouses all week, they fascinate me")
        ask(term, "switch to the technical assistant")
        ask(term, "I think the fan is louder than it used to be, what do you make of it?")
        ask(term, "switch to the robot companion")
        ask(term, "could you tell me a little about what you are able to do, please?")
        companion, technician, robot = (p["messages"][0]["content"] for p in self.ollama.payloads)
        self.assertNotIn("Technical Assistant", companion)
        self.assertIn("Technical Assistant", technician)
        self.assertIn("Give the figure before the explanation", technician)
        self.assertIn("Robot Companion", robot)
        for payload in self.ollama.payloads[1:]:
            self.assertNotIn("lighthouses", "\n".join(m["content"] for m in payload["messages"]),
                             "one personality was shown another's conversation")
        people = {p.id: p for p in mind.personalities.all()}
        self.assertEqual(len({p.namespace for p in people.values()}), 3)
        self.assertNotEqual(people["companion"].permissions, people["robot"].permissions)
        self.assertEqual(len(mind.models.clients), 1, "three personalities loaded more than one model")
        self.assertEqual(mind.persona("voice").id, "companion", "switching in the terminal moved the voice")

    def test_10_two_npcs_can_have_independent_conversations(self):
        def speak(payload):
            system = payload["messages"][0]["content"]
            return "Rope, oil, salt. What do you need?" if "You are Elara" in system else \
                "Harbour's closed. Who is asking?"

        self.ollama.reply = speak
        shell, term = terminal(self.ollama.host)
        ask(term, "talk to the shopkeeper")
        self.assertIn("Rope, oil, salt.", ask(term, "I need six flasks of lamp oil for the lighthouse"))
        ask(term, "talk to brann")
        self.assertEqual(term.mode, "GAME · BRANN")
        self.assertIn("Harbour's closed.", ask(term, "can I take a boat out tonight under the fog?"))
        ask(term, "talk to elara")
        ask(term, "and how much will that come to?")
        elara_1, brann, elara_2 = ("\n".join(m["content"] for m in p["messages"]) for p in self.ollama.payloads)
        self.assertIn("You are Elara", elara_1)
        self.assertIn("You are Brann", brann)
        self.assertNotIn("six flasks", brann, "Brann was shown what was said to Elara")
        self.assertNotIn("boat out tonight", elara_2, "Elara was shown what was said to Brann")
        self.assertIn("six flasks", elara_2, "Elara lost her own conversation")
        self.assertEqual(len(shell.earth_mind().models.clients), 1, "two characters needed two models")

    def test_11_npc_memories_remain_separate(self):
        shell, term = terminal(self.ollama.host)
        mind = shell.earth_mind()
        harbour = mind.games.open("harbour")
        harbour.remember("vex", "You hid Elara's ledger in the cave below Gull Point.")
        harbour.event("The traveller paid Brann's mooring fee in silver.", witnesses=["brann"])
        ask(term, "talk to the shopkeeper")
        ask(term, "where is your ledger? did Vex hide it in the cave below Gull Point?")
        ask(term, "did I pay a mooring fee in silver?")
        shown = self.ollama.prompts()
        self.assertNotIn("You hid Elara's ledger", shown, "Vex's private memory reached Elara")
        self.assertNotIn("paid Brann's mooring fee", shown, "an event Elara did not see reached her")
        store = mind.memory.view(MIND)
        counts = {c.id: [r.text for r in store.recent(character_namespace("harbour", c.id), limit=50)]
                  for c in harbour.characters()}
        self.assertTrue(any("ledger" in t for t in counts["vex"]))
        self.assertFalse(any("hid Elara's ledger" in t for t in counts["elara"]))
        self.assertEqual(counts["brann"], ["The traveller paid Brann's mooring fee in silver."])
        for other in ("vex", "brann", "narrator"):
            with self.assertRaises(MemoryAccessError):
                mind.memory.view(harbour.character("elara").principal()).recent(
                    character_namespace("harbour", other))
        self.ollama.payloads.clear()
        ask(term, "talk to vex")
        ask(term, "what did you do with the ledger?")
        self.assertIn("You hid Elara's ledger", self.ollama.prompts(), "Vex lost his own memory")

    def test_12_a_simulated_world_can_retain_event_history(self):
        shell, term = terminal(dead_host())
        mind = shell.earth_mind()
        harbour = mind.games.open("harbour")
        harbour.event("The lighthouse keeper was found asleep in the lamp room.", witnesses=["brann"])
        ask(term, "talk to brann")
        ask(term, "Remember that I helped this character carry oil up to the lamp.")
        ask(term, "three days later")
        harbour.event("The lighthouse was relit.", witnesses=["brann", "elara"],
                      state={"lighthouse": "lit"}, relationships=[("brann", "player", 3)])
        history = ask(term, "what happened")
        self.assertIn("Day 1: The lighthouse keeper was found asleep in the lamp room.", history)
        self.assertIn("Day 1: The traveller helped Brann carry oil up to the lamp.", history)
        self.assertIn("Day 4: The lighthouse was relit.", history)
        self.assertEqual(harbour.state("lighthouse"), "lit")
        self.assertEqual(harbour.relationship("brann", "player").score, 4)
        mind.close()                                   # the world is in the store, not in the program
        again = Shell(shell.config).earth_mind().games.open("harbour")
        self.assertEqual([e.data["day"] for e in again.history()], [1, 1, 4])
        self.assertEqual((again.day, again.state("lighthouse")), (4, "lit"))

    def test_13_ollama_being_offline_does_not_break_the_operating_system(self):
        shell, term = terminal(dead_host())
        fired = guard(shell)
        mind = shell.earth_mind()
        self.assertIsNotNone(mind, shell.mind_error)
        self.assertFalse(mind.models.available())
        term.cwd = tempfile.mkdtemp()
        self.assertIn("still a computer", ask(term, "echo still a computer"))
        self.assertIn("translated → pwd", ask(term, "where am I"))
        self.assertIn("PROGRAM", ask(term, "What is using the most RAM?"))
        self.assertRegex(ask(term, "what is the cpu load"), r"\d+%")
        self.assertRegex(ask(term, "Why is my computer running slowly?"), r"Memory is \d+% full")
        if HAVE_COMPUTER:
            self.assertIn("108", ask(term, "what is 12 times 9"))
        self.assertIn("needs approval", ask(term, "shut down"))
        ask(term, "no")
        self.assertIn("language model isn't running", ask(term, "I had a long day and I am worn out"))
        self.assertIn("Come in out of the fog.", ask(term, "talk to the shopkeeper"))
        self.assertIn("Lamp oil costs 2 silver marks", ask(term, "how much is lamp oil?"))
        ask(term, "goodbye")
        self.assertNotIn("Traceback", text_of(term))
        self.assertEqual(fired, [])
        # The voice path too: a spoken sentence with no model is answered, not dropped.
        shell.conversation.clear()
        shell.handle_utterance("what is using the most memory")
        self.assertTrue(any(e["role"] == "computer" for e in shell.conversation))
        # And the doctor reports it as a warning with the reason, not a failure.
        from earth_os import diagnostics
        rows = {name: check for name, check in diagnostics.CHECKS}
        self.assertIn("earth mind", rows)


if __name__ == "__main__":
    unittest.main()
