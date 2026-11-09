"""Tests for the conformance harness.

A harness that always reports success is worse than no harness, because it
launders a claim it never checked.  The first version of this one did exactly
that in reverse — it reported six *failures* that were all its own doing, by
hard-coding ``name="lo"`` and then being surprised that a robot has no
loopback interface.

So these tests check the tool against both mistakes:

* it **must** flag a genuine divergence (a capability answering with a
  different shape on two machines);
* it **must not** flag the differences that are the experiment working —
  a machine that has no battery, an emptier process list, an ``Optional``
  answering ``None``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import conformance                                            # noqa: E402
from earth_os.platform import reset_platform                # noqa: E402
from earth_os.platform.capabilities import CATALOGUE        # noqa: E402
from earth_os.platform.robot_stub import MARKER             # noqa: E402


class TestShapes(unittest.TestCase):
    """Shape describes the form of an answer, never its content."""

    def test_two_machines_may_report_different_numbers(self):
        self.assertEqual(conformance.shape_of(41.0),
                         conformance.shape_of(57.0))

    def test_a_list_is_a_list_however_full(self):
        agree, why = conformance.shapes_agree("list[]", "list[ProcessInfo]")
        self.assertTrue(agree)
        self.assertTrue(why)

    def test_none_is_within_an_optional_contract(self):
        """A machine with no battery answering None is the hardware
        differing, which is what the experiment measures."""
        agree, why = conformance.shapes_agree("none", "list[number|text]")
        self.assertTrue(agree)
        self.assertIn("absent", why)

    def test_a_real_change_of_type_does_not_agree(self):
        self.assertFalse(conformance.shapes_agree("number", "text")[0])
        self.assertFalse(conformance.shapes_agree("text", "list[text]")[0])
        self.assertFalse(conformance.shapes_agree("mapping", "number")[0])


class TestTheHarnessMeasuresSomething(unittest.TestCase):

    def tearDown(self):
        os.environ.pop(MARKER, None)
        reset_platform()

    def test_it_covers_the_whole_catalogue(self):
        data = conformance.snapshot()
        measured = {row["capability"] for row in data["capabilities"]}
        self.assertEqual(measured, set(CATALOGUE))

    def test_it_never_invokes_an_effectful_capability(self):
        """A conformance run must not reboot the machine it is measuring."""
        data = conformance.snapshot()
        for row in data["capabilities"]:
            if CATALOGUE[str(row["capability"])].effectful:
                self.assertEqual(row["verdict"], "skipped", row["capability"])

    def test_it_reports_the_mechanisms(self):
        data = conformance.snapshot()
        mechanisms = data["machine"]["mechanisms"]
        self.assertTrue(mechanisms, "no mechanisms reported")
        self.assertIn("power", mechanisms)

    def test_an_honest_refusal_is_not_a_failure(self):
        """Absence is a pass. A container has no backlight, and saying so is
        the behaviour the audit found four adapters getting wrong."""
        data = conformance.snapshot()
        verdicts = {row["verdict"] for row in data["capabilities"]}
        self.assertNotIn("broken", verdicts,
                         "something genuinely broken on this machine")

    def test_arguments_come_from_the_machine_not_the_harness(self):
        """`lo` exists on Linux and not on a robot. An argument that names a
        thing has to be asked of the machine being measured."""
        for name in ("network.status", "process.inspect", "filesystem.read"):
            self.assertIn(name, conformance.DERIVED_ARGUMENTS)
        for arguments in conformance.SAFE_ARGUMENTS.values():
            self.assertNotIn("lo", arguments.values())


class TestComparison(unittest.TestCase):
    """The experiment itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.pop(MARKER, None)
        reset_platform()
        self.linux = conformance.snapshot()
        self.robot = conformance.snapshot(robot=True)
        os.environ.pop(MARKER, None)
        reset_platform()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, data, name):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(data))
        return str(path)

    def test_two_real_platforms_do_not_disagree(self):
        text = conformance.compare(self.linux, self.robot)
        self.assertIn("no capability disagreed", text)
        self.assertIn("The computer changed. CDOS didn't.", text)

    def test_the_mechanisms_really_do_differ(self):
        """Half the claim. Matching answers on two machines running identical
        tooling is one computer measured twice."""
        a = self.linux["machine"]["mechanisms"]
        b = self.robot["machine"]["mechanisms"]
        differing = [k for k in a if a[k] != b.get(k)]
        self.assertGreaterEqual(len(differing), 5, differing)

    def test_a_divergence_is_caught(self):
        """The test that makes the harness worth trusting."""
        broken = json.loads(json.dumps(self.robot))
        for row in broken["capabilities"]:
            if row["capability"] == "hardware.cpu_count":
                row["shape"] = "text"          # answers "four" instead of 4
        text = conformance.compare(self.linux, broken)
        self.assertIn("SEMANTIC DISAGREEMENT", text)
        self.assertIn("hardware.cpu_count", text)
        self.assertIn("CDOS changed with the computer", text)

    def test_a_divergence_fails_the_exit_code(self):
        """CI has to be able to fail on this."""
        broken = json.loads(json.dumps(self.robot))
        for row in broken["capabilities"]:
            if row["capability"] == "system.hostname":
                row["shape"] = "list[text]"
        left, right = self._write(self.linux, "a.json"), self._write(broken, "b.json")
        self.assertEqual(conformance.main(["--compare", left, right]), 1)

    def test_agreement_passes_the_exit_code(self):
        left = self._write(self.linux, "a.json")
        right = self._write(self.robot, "b.json")
        self.assertEqual(conformance.main(["--compare", left, right]), 0)

    def test_identical_tooling_is_reported_as_no_proof(self):
        """Comparing a machine with itself must not read as a success.

        Every answer matches and every mechanism matches, which is exactly
        what one computer measured twice looks like — and if the harness
        called that a proof, the whole exercise could be passed by doing
        nothing.
        """
        text = conformance.compare(self.linux, json.loads(json.dumps(self.linux)))
        self.assertIn("one computer measured twice", text)
        self.assertNotIn("The computer changed. CDOS didn't.", text)



class TestItRunsAwayFromTheRepository(unittest.TestCase):
    """The harness is a single-file diagnostic, so it gets treated as one.

    It was handed over as a loose file, copied to ~/Downloads, run, and died
    with a bare ``ModuleNotFoundError`` — because it took its own parent's
    parent as the repository root, which from a home directory is the home
    directory. That is the obvious thing to do with a file like this, so it
    has to either work or explain itself.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.away = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, script, *args, env=None):
        import subprocess
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop(MARKER, None)
        environment.update(env or {})
        return subprocess.run([sys.executable, str(script), *args],
                              capture_output=True, text=True, cwd=self.away,
                              env=environment, timeout=120)

    def test_a_loose_copy_explains_itself(self):
        loose = self.away / "conformance.py"
        loose.write_text((ROOT / "tools" / "conformance.py").read_text())
        done = self._run(loose)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("cannot find the earth_os package", done.stderr)
        self.assertIn("--bundle", done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    def test_cdos_root_points_it_at_the_repository(self):
        loose = self.away / "conformance.py"
        loose.write_text((ROOT / "tools" / "conformance.py").read_text())
        done = self._run(loose, "--quiet", env={"CDOS_ROOT": str(ROOT)})
        self.assertEqual(done.returncode, 0, done.stderr[-400:])

    def test_a_bundle_needs_nothing_beside_it(self):
        bundle = self.away / "cdos-conformance.py"
        count, digest = conformance.build_bundle(bundle)
        self.assertGreater(count, 5)
        self.assertEqual(sorted(p.name for p in self.away.iterdir()),
                         ["cdos-conformance.py"])
        done = self._run(bundle, "--quiet", "--json", "out.json")
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        self.assertTrue((self.away / "out.json").is_file())

    def test_the_bundle_writes_nothing_to_the_machine(self):
        """A conformance run that leaves files behind changed its subject."""
        bundle = self.away / "cdos-conformance.py"
        conformance.build_bundle(bundle)
        self._run(bundle, "--quiet")
        self.assertEqual(sorted(p.name for p in self.away.iterdir()),
                         ["cdos-conformance.py"])

    def test_the_digest_is_reproducible(self):
        first = conformance.build_bundle(self.away / "a.py")[1]
        second = conformance.build_bundle(self.away / "b.py")[1]
        self.assertEqual(first, second)

    def test_the_digest_travels_in_the_report(self):
        """Otherwise two JSON files cannot be checked for being the same code."""
        bundle = self.away / "cdos-conformance.py"
        _count, digest = conformance.build_bundle(bundle)
        self._run(bundle, "--quiet", "--json", "out.json")
        data = json.loads((self.away / "out.json").read_text())
        self.assertEqual(data["machine"]["bundle"], digest)

    def test_comparing_two_different_builds_warns(self):
        left = conformance.snapshot()
        right = json.loads(json.dumps(left))
        left["machine"]["bundle"] = "1111111111111111"
        right["machine"]["bundle"] = "2222222222222222"
        text = conformance.compare(left, right)
        self.assertIn("different code", text)

    def test_comparing_matching_builds_says_so(self):
        left = conformance.snapshot()
        right = json.loads(json.dumps(left))
        left["machine"]["bundle"] = right["machine"]["bundle"] = "abc123"
        self.assertIn("Both machines ran bundle abc123",
                      conformance.compare(left, right))


class TestItDoesNotDisturbTheMachine(unittest.TestCase):
    """A measuring instrument must not change what it measures.

    The first real run of this harness, on a live sway desktop, invoked
    ``system.lock`` and locked its owner's screen. That capability is
    correctly *not* effectful — locking is trivially undone and making people
    confirm it would teach them to confirm without reading — so the fix was
    not to mark it dangerous. It was to notice that the harness had a
    different question to answer than the catalogue does.
    """

    def tearDown(self):
        os.environ.pop(MARKER, None)
        reset_platform()

    def test_the_two_lists_are_not_the_same_question(self):
        """`effectful` is about safety. `DISTURBS` is about politeness."""
        self.assertFalse(CATALOGUE["system.lock"].effectful,
                         "locking should not require a confirmation press")
        self.assertIn("system.lock", conformance.DISTURBS)

    def test_nothing_that_disturbs_is_invoked(self):
        data = conformance.snapshot()
        for row in data["capabilities"]:
            if str(row["capability"]) in conformance.DISTURBS:
                self.assertEqual(row["verdict"], "declined", row["capability"])

    def test_everything_named_is_a_real_capability(self):
        self.assertEqual(sorted(set(conformance.DISTURBS) - set(CATALOGUE)), [])

    def test_declining_is_visibly_not_the_same_as_effectful(self):
        """Sharing a marker in the report would undo the distinction."""
        data = conformance.snapshot()
        verdicts = {str(r["capability"]): r["verdict"]
                    for r in data["capabilities"]}
        self.assertEqual(verdicts["system.lock"], "declined")
        self.assertEqual(verdicts["system.reboot"], "skipped")

    def test_the_reason_is_recorded_not_just_the_name(self):
        """A skip with no reason becomes a skip nobody revisits."""
        for name, reason in conformance.DISTURBS.items():
            self.assertTrue(reason.strip(), name)

    def test_focus_is_on_the_list(self):
        """Deriving a window handle was the obvious next improvement to this
        harness, and would have yanked focus away from whoever ran it."""
        self.assertIn("window.focus", conformance.DISTURBS)
        self.assertNotIn("window.focus", conformance.DERIVED_ARGUMENTS)


class TestNothingReportsFabricatedSuccess(unittest.TestCase):
    """An unimplemented capability must refuse, not answer emptily.

    ``LinuxAudio.listen`` returned ``None`` with a comment calling itself a
    placeholder. The capability layer read that as success with an empty
    answer — indistinguishable from having listened and heard silence — and
    the first real conformance run duly recorded speech recognition as
    working on a machine where it is not implemented at all.
    """

    def tearDown(self):
        reset_platform()

    def test_unimplemented_speech_recognition_refuses(self):
        from earth_os.platform import PlatformError, get_platform
        reset_platform()
        with self.assertRaises(PlatformError):
            get_platform().audio.listen(timeout=0.1)

    def test_a_robot_with_no_microphone_says_so(self):
        from earth_os.platform import PlatformError
        from earth_os.platform.robot_stub import RobotPlatform
        with self.assertRaises(PlatformError):
            RobotPlatform().audio.listen()


if __name__ == "__main__":
    unittest.main()
