# CDOS Governor (Neural Platform 0.3): design note

The 0.2 platform could learn, reason and drive a simulated robot. Its authority checks were split across six places
(router policy, capability registry, promotion gates and `--approve`, growth approval, the robot envelope, systemd).
None of them knew who was acting, and the emergency stop was a variable in memory. `computer/governor.py` puts that
authority in one place. The neural core, the reasoning engine and the safety envelope are unchanged.

**Result in one line.** The stop now survives a SIGKILL and restart, and it fails safe whenever its record can't be
verified. Only a named operator with a reason can clear it, promote, roll back or deactivate a model. Every
privileged decision and every denial goes into a tamper-evident log that is checked at start-up. **489 tests pass**
(the 459 from 0.2, with two of them updated for the intended behaviour change, plus 30 new), 4 skipped (production
corpus).

## 1. Requirements and evidence

| # | requirement | how | evidence (`tests/test_governor.py` unless noted) | status |
|---|---|---|---|---|
| 1 | the stop survives restarts | `Latch`: `governor_dir/estop.json`, fsynced atomic writes; the controller reads it through `latch=` | `TestRestart` (real API process: engage, **SIGKILL**, restart, motion refused; clear, kill, restart, motion allowed), `test_the_0_2_defect_is_fixed` | **met** |
| 1 | engaged when integrity can't be verified | "cleared" counts only if the file is intact, its hash matches, **and** the audit log (verified) has the operator's `estop.clear` entry with that seq and hash | `test_every_unverifiable_record_means_engaged` (9 kinds of damage), `test_a_forged_clear_record_doesnt_release_the_robot`, `test_in_memory_starts_engaged` | **met** |
| 1 | ordinary clients can't clear it | `estop.clear` needs the operator role, a verified identity, a reason and a healthy audit log; the controller itself can only engage | `test_anyone_engages_only_an_operator_clears`, `test_the_controller_can_engage_but_never_clear` | **met** |
| 2 | one decision point | `Governor.authorize(identity, permission)` for every API request, every privileged command and the latch | `TestApiPermissions`, `TestApprovals` | **met** |
| 2 | explicit permission levels | 12 permissions, 6 roles (`read`, `ask`, `robot`, `service`, `teacher`, `operator`), from a root-owned policy | `TestPolicy` | **met** |
| 2 | deny when authorization can't be verified | TCP callers are unverified (read and ask only); no policy or a broken one means no privileged role; a degraded log refuses privilege and motion | `test_motion_needs_the_robot_role`, `test_no_policy_means_no_operator`, `test_a_broken_or_missing_policy_means_no_privilege`, `TestDegraded` | **met** |
| 2 | identity from the system, not the client | `SO_PEERCRED` on the Unix socket; `os.getuid()` for commands (SUDO_USER kept as an unverified hint) | `TestPeerCredentials`: a process running as `nobody` is identified as uid 65534 and can't clear the stop | **met** (needs root to test) |
| 3 | approver, action, target, reason, outcome, time, artifact hash | decision line (who, roles, permission, target, reason, policy fingerprint) + result line (outcome, model/eval/manifest sha256) linked by seq and hash; the registry's `promotions.jsonl` carries `approved_by` pointing into the log | `test_only_a_named_operator_with_a_reason_changes_the_active_model`, `test_a_refused_promotion_is_recorded_as_refused` | **met** |
| 3 | protected against silent modification, verified at start-up | sha256 chain + separate head record (catches truncation); `flock` for several writers; fsync; verified at start-up **and** before each privileged decision | `TestAuditLog` (edit, removed middle and last line, reorder, head removed or edited, torn write, emptied; three processes × 40 appends), `test_an_edit_while_running_stops_the_next_privileged_decision` | **met**, with the limit in §5 |
| 4 | proposals separate from authority | growth `answer` needs teacher; `run`/`auto` need service (stage and evaluate only); `promote`/`rollback`/`deactivate` need operator; dry runs need nothing | `test_growth_answers_need_a_teacher`, `TestApprovals` | **met** for models. Rules and code have no promotion path yet (§6) |

## 2. Design

```
 caller ──► identity (kernel: SO_PEERCRED / getuid; TCP = unverified)
              │
              ▼
 Governor.authorize(identity, permission, target, reason)
   policy  (/etc/cdos-computer/governor.json, root-owned) ──► roles ──► permission granted?
   privileged? ──► verified identity · policy loaded · reason · audit log re-verified and writable
   │ denied ──► audit line "denied" ──► 403 FORBIDDEN / CLI exit 4
   ▼ allowed
 audit line "allowed" ──► the action ──► audit line with the outcome and artifact hashes
                                         (promotions.jsonl: approved_by → governor seq + hash)

 E-stop:  engage (anyone)  ──► latch in memory NOW ──► audit ──► estop.json
          clear (operator) ──► audit FIRST ──► estop.json ──► latch in memory
          start-up         ──► estop.json + verified audit entry ? cleared : ENGAGED
```

| permission | read | ask | robot | service | teacher | operator |
|---|---|---|---|---|---|---|
| `api.read`, `estop.engage` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `api.ask` (ask, call a component) | | ✓ | ✓ | ✓ | ✓ | ✓ |
| `robot.observe`, `robot.command` | | | ✓ | | | ✓ |
| `growth.train` (stage + evaluate) | | | | ✓ | | ✓ |
| `growth.answer` (becomes training data) | | | | | ✓ | ✓ |
| `estop.clear`, `model.promote/rollback/deactivate`, `audit.reseal` | | | | | | ✓ |

`estop.engage` is open to every caller, verified or not. A caller the policy doesn't name gets `default` (at most
read and ask); an unverified caller gets `unverified` (the same at most). The policy file refuses anything more for
either, so privilege always needs a name.

The installer writes `/etc/cdos-computer/governor.json`: operator `root`; robot and service the service user
(`cdos`), so the simulated robot and the growth timer keep working. A fresh install starts with the stop engaged.
The installer says so and prints the command to clear it.

**Overhead** (build machine): a motion's permission check takes 2.7 µs, with nothing written. Engaging the stop takes
5.8 ms (two fsynced writes). Clearing it re-verifies the whole log: 22 ms at 1,200 lines. The start-up check takes
29 ms at 1,200 lines and grows linearly, about 18 µs per line.

## 3. Behaviour changes (intended)

1. **The stop starts engaged** on a new install, after a damaged record, and always when `governor_dir` is unset. An
   operator clears it once: `sudo python3 -m computer.governor estop clear --reason "..."`.
2. **`POST /v1/robot/e_stop {"on": false}`** needs an operator and a `reason`. Robot observations and actions need the
   robot role. Any denial is a 403 `FORBIDDEN`.
3. **`--approve` needs `--reason`**, an operator, and a configured `governor_policy`, for `computer.neural
   promote|rollback|deactivate` and `computer.robotics promote-policy`. Refusals exit with 4. Dry runs are unchanged.
4. **`computer.growth answer`** needs teacher or operator. **`run`/`auto`** need service or operator. `discover`,
   `tasks` and `show` are unchanged.
5. **Library calls** to `registry.promote()` and the others still work without a Governor. They are recorded as
   `"approved_by": {"unattributed": ...}`, never silently.

Two existing tests encoded the old behaviour and were updated. Nothing else in them changed:
- `test_platform.TestApi`: the server gets a policy that makes the test's user an operator, and its two e-stop clears
  give a reason.
- `test_platform.TestInstallAndRestart`: it asserts that the fresh install starts engaged, clears the stop through
  `python3 -m computer.governor estop clear` (root, as the installer's policy names), and after the SIGKILL and
  restart asserts that the clear persisted and the Governor's audit log verifies.

## 4. Changes to existing files

| file | change |
|---|---|
| `computer/robotics/controller.py` | `latch=`: `e_stop` reads it; `set_e_stop(False)` raises `PermissionError` with a latch; `note_e_stop()`; an `e_stop_restored` journal event at start. The latch is passed in, not imported, so this deterministic module still reads no clock or file |
| `computer/services/platform.py` | builds the Governor; hands its latch to the controller; `estop()`; `health()["governor"]` |
| `computer/services/api.py` | every request authorized with kernel peer credentials; `/v1/governor`, `/v1/governor/whoami`; e-stop through the Governor; refused bodies drained so a keep-alive connection survives a 403; 413 closes the connection |
| `computer/neural/registry.py` | `approver=` on promote, rollback and deactivate, written as `approved_by`; result hashes returned |
| `computer/neural/__main__.py`, `computer/robotics/__main__.py`, `computer/robotics/learn.py` | `--reason`, `--config`; the Governor's decision before `--approve` acts, and the outcome after |
| `computer/growth/__main__.py`, `computer/growth/engine.py` | answer, run and auto authorized; an answer stores the decision (`authorized`) beside the free-text `--by` |
| `computer/capabilities.py` | declares `governor.authority` (writes state; on `WRITERS`) |
| `computer/config.py`, `config.example.toml` | `governor_policy`, `governor_dir` |
| `computer/__main__.py` | `--check` prints the policy, the audit state and the latch |
| `tools/install_services.sh` | writes `governor.json` (root-owned, once), `governor_dir`, and a note for upgraded configs that lack them |

**New:** `computer/governor.py`, `governor.example.json`, `tests/test_governor.py`, `docs/GOVERNOR.md`.

## 5. Limits

- **This is not a physical interlock.** The latch makes the controller command STOP. It can't remove motor power, and
  a bug below the API (a driver that ignores STOP) bypasses it. Before any actuator is connected, the robot needs an
  independent emergency-stop circuit or a microcontroller that enforces its own limits. **No software here, the
  Governor included, may be able to override it.**
- **The service account can still forge consistently.** The audit log, its head and the latch live in
  `/var/lib/cdos/governor`, which the API process (`cdos`) must write. The chain, the head record and the latch-audit
  cross-check catch accidents, truncation and single-file edits. They don't catch a process running as `cdos` that
  rewrites all three consistently. The model registry has the same limit: `cdos` can write `ACTIVE.json` directly.
  The strong part is the policy file, which belongs to root. The next step is to run the Governor as its own process
  under its own user, and to anchor the log's head somewhere the service can't write (journald, or a second machine).
- **A torn write after a power cut degrades the Governor**, which then needs an operator's reseal. That's
  deliberate: a stop that can't prove it was cleared stays engaged. A head record one line behind the log (power lost
  between the two fsyncs) is accepted and noted.
- **After `audit reseal`, restart `cdos-api`.** The running service keeps its degraded state until it restarts.
- **Verification is linear in the log's length** (about 18 µs a line). The log never rotates. Denials are recorded,
  so a client spamming refused requests grows it; the socket is 0660, which limits that to the service group and
  root.
- **TCP callers are unverified.** Robot control and every privileged action need the Unix socket.
- **Root is an operator** in the installer's policy. Name individual operators (and remove root) if more than one
  person administers the machine. The audit then shows who acted, not just `root` with a SUDO_USER hint.
- **Measured here, not on the NUC or the Pi.**

## 6. Next steps

1. **A hardware interlock design** for the first real actuator: an e-stop loop that cuts drive power independently,
   and a microcontroller with its own speed and current limits. CDOS should only be able to *request* motion.
2. **The Governor as its own service** (its own user, socket and state directory), with the API asking it over a
   socket. The service account then can't forge the audit log or the latch, or write `ACTIVE.json`.
3. **Promotion paths for rules and code.** `rules_file` changes and plugin installs would follow the same pattern:
   proposals in staging, tests and gates, then an operator's decision with the artifact's hash.
4. **Then the research question:** train and evaluate on the production corpus with your own held-out questions, and
   show that the measured performance improves while every test in this file still passes.

## 7. Commands

```sh
python3 -m computer.governor whoami | status | audit verify
python3 -m computer.governor policy check /etc/cdos-computer/governor.json
python3 -m computer.governor estop engage --reason "..."                 # anyone, via the running service
sudo python3 -m computer.governor estop clear --reason "..."             # an operator
sudo python3 -m computer.governor audit reseal --reason "..."            # an operator; then restart cdos-api
sudo python3 -m computer.neural promote --model <id> --approve --reason "..."
curl --unix-socket /run/cdos/api.sock http://x/v1/governor
python3 -m unittest tests.test_governor -v                               # about 15 s
```
