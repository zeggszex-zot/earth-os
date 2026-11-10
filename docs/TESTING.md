# Testing report — Earth OS 2.0

Everything in this report was run. Where something could not be tested, it is
listed under "Not tested" with the reason, and nothing elsewhere in this
archive claims it works.

## Where and how

| | |
|---|---|
| Date | 10 November 2026 |
| Machine | Linux 6.18 container, x86-64, 2 CPUs, 7.8 GB, no GPU, no display, no audio, no systemd |
| Python / GTK | `/usr/bin/python3.12` 3.12.3, GTK 4.14.5, PyGObject 3.48.2, pycairo 1.25.1 |
| Display for GUI tests | `xvfb-run` (a virtual X display) |
| Language model | **None.** No Ollama, and no network route to fetch a model |
| Command | `/usr/bin/python3.12 -m unittest discover -s tests` from the repository root |

Because no model could be run, every model-calling path was tested with the
real Ollama client (`earth_os/companion/model.py`) speaking HTTP to a local
stand-in for Ollama (`tests/mind_helpers.py: FakeOllama`), or with a recording
fake. Those tests establish what Earth OS sends a model and what it does with
the reply. They establish nothing about what a real model says.

## Result

```
Ran 892 tests in 43.204s

OK (skipped=5)
```

887 passed, 0 failed, 5 skipped. The suite was run three times in a row with
the same result. Before this work the same suite, at the commit Earth OS 1.0.0
arrived as, was 619 tests passing with the same 5 skipped; all 619 still pass.

The 5 skipped are not about this release and skip for a stated reason: 3 need
the `cdos` and `cdos-bridge` binaries built (`tests/test_cdos.py`), 2 need
`sway` installed (`tests/test_session.py`).

CDOS Computer's own suite, unchanged and run from `cdos-computer/`:
`Ran 512 tests … OK (skipped=95)`. Its skips are its own: 91 need `numpy`
(the neural planner's training), 4 need a corpus `data/` folder.

## The thirteen tests in the specification

`tests/test_acceptance.py`, one test each, numbered the same way.

| # | Asked for | Result | What was actually done |
|---|---|---|---|
| 1 | Existing Earth OS GUI launches | **Pass** | `tools/bootcheck.py` boots the real application on a virtual display: one window throughout, the frame drawn before the shell, the shell arriving. Output ended *"boot contract holds"*. Separately, all ten screens were rendered to images without error (`tools/screenshot.py`), and the main and terminal screens were looked at |
| 2 | Existing terminal functions | **Pass** | Translator ("where am I" → `pwd`), `cd`, creating a file, interrupting `sleep 30`, history recall, `help` — with no model call made |
| 3 | Companion conversations work inside the terminal | **Pass (stand-in model)** | Two turns typed into the terminal reach the real Ollama client; one HTTP call per turn; the second call carries the first turn; header reads `COMPANION` |
| 4 | CDOS system queries return real information | **Pass** | The programs listed for "what is using the most RAM?" are processes in this machine's `/proc`; the memory total in a diagnosis is within 6% of `/proc/meminfo`; the core count equals `os.cpu_count()`; "12 times 9" is answered 108 by the embedded CDOS Computer; no model call for any of it |
| 5 | Companion can explain CDOS findings | **Pass (stand-in model)** | Every finding is in the prompt sent to the model; a faithful explanation is passed on, marked checked; an explanation with an invented figure ("99.9% full", "37 viruses") is discarded and the readings are said instead |
| 6 | Linux commands still execute | **Pass** | `uname`, redirection, `sort`, a pipe, exit codes, environment variables, `!command` — no model call |
| 7 | Permission controls work | **Pass** | "shut down" and "delete the file…" each wait for a typed `yes` and do nothing on `no`; a model reply containing commands runs nothing; an effectful capability is refused as a "reading"; inside a game, "shut down" and "delete…" are said to the character and raise no approval; a character's memory view is refused `user` and `system`; a typed password does not reach the model |
| 8 | Memory persists across sessions | **Pass** | Two separate Python processes sharing one home directory: user memory ("my sister is called Mira"), the world's calendar, who met whom, the event history, a character's memory and a system diagnosis are all there in the second |
| 9 | Different personalities maintain separate identities | **Pass (stand-in model)** | Companion, Technical Assistant and Robot Companion send different system prompts; neither later personality's prompt contains the first one's conversation; three namespaces; one model client; switching in the terminal does not move the voice |
| 10 | Two NPCs can have independent conversations | **Pass (stand-in model)** | Elara and Brann, interleaved: each prompt names its own character; neither contains what was said to the other; Elara's second turn carries her first; one model client |
| 11 | NPC memories remain separate | **Pass (stand-in model)** | A private memory given to Vex and an event only Brann witnessed do not appear in any prompt sent for Elara, even when she is asked about them directly; her view is refused the other three characters' namespaces; Vex is still given his own |
| 12 | A simulated world can retain event history | **Pass** | Three events over four world days, in order, with the day; state and relationship changes applied; all present after the store is closed and a new shell opens it |
| 13 | Ollama being offline does not break the operating system | **Pass** | With the model's address refusing connections: Earth Mind builds; commands, the translator, system questions, a diagnosis, a sum, the shutdown confirmation, game characters (answering from written lines and memories) and the voice path all work; conversation says *"my language model isn't running"*; no traceback |

Tests 3, 5, 9, 10 and 11 passed against the stand-in. They should be read as
"the plumbing is right", not "the conversation is good".

## What else is covered

| File | Tests | What |
|---|---|---|
| `tests/test_mind.py` | 76 | Router (the specification's examples, in-game routing, distress outranking everything); model management (sharing, a smaller model for games, redaction, queueing, usage, offline); memory (every access rule, both directions; persistence; thread safety); personalities; the coordinator (never raises, diagnosis checked, operations proposed and never performed, the import ban) |
| `tests/test_mind_game.py` | 32 | Separate conversations and memories; witnesses; relationships; character-to-character exchange; 120 characters on one model; no-model answers; restart; two worlds in one store; the sandbox, including a world definition that asks for system access |
| `tests/test_mind_terminal.py` | 30 | Commands untouched by Earth Mind; each route from the terminal; approvals; `!` and `?`; model offline; Earth Mind disabled; a mind that cannot be built |
| `tests/test_install.py` | 5 (2 new) | The installer run for real into a scratch home: Earth Mind and the embedded computer answer from the installed tree alone; uninstall leaves `memory.db` |
| `tests/test_companion.py`, `test_computer_link.py`, `test_world_model.py` | 120 | Ported with the code they test; changed only in the package name and where the computer's source is found |
| The other 19 files | 616 | Earth OS 1.0's suite (with the 3 original installer tests, its 619), unedited apart from one test in `test_port.py` that now waits for a worker thread |

## Found by testing, and fixed

These were defects in this release's own new code, found by the tests above
before the release was packaged:

1. **File paths were redacted as secrets.** Any path longer than 32
   characters reached the model as `[REDACTED]`, which would have gutted the
   explanation of a process list.
2. **A character forgot what had happened to it.** With a world as detailed
   as the shipped one, the prompt budget trimmed the character's memories
   first; Elara was told nothing about being helped. Memories and world facts
   are now the part of the prompt that is never trimmed.
3. **"I helped this character carry her stock"** was recorded as *"carry
   Elara stock"*.
4. **A character asked about another by name** was not told its own opinion
   of them.
5. **`install.sh` did not install CDOS Computer**, so an installed Earth OS
   had no knowledge half and nothing said so.
6. **`install.sh --user --uninstall` deleted the data directory** — in 1.0
   that was learned meanings; in 2.0 it would have been every memory. This one
   was in 1.0.

One thing found and *not* changed: typing `reboot` in the terminal runs the
program of that name immediately, as in 1.0, because a line that starts with a
program on the machine is left to the shell. "Restart the computer" asks
first. An early version of the tests typed `reboot`; it was harmless only
because this container has no init system. The tests now never type a power
command, and the behaviour is documented in EARTH_MIND.md §6.

## Rollback, carried out

In a scratch home: installed 2.0 with `./install.sh --user`; used it (a user
memory, a sum, a diagnosis, a game note); then installed the tagged 1.0 over
it. `earth-os --doctor` reported `earth-os 1.0.0`; the 1.0 shell built and
answered a system question with 2.0's configuration file and `mind/memory.db`
in place; nothing in the data directory was removed. Of the configuration
switches in [ROLLBACK.md](ROLLBACK.md) §1, `terminal = false` and
`enabled = false` are tests in `test_mind_terminal.py`; Earth Mind and the
Companion both off was checked by building the shell that way, using the voice
path and the terminal, and listing which modules had loaded (none of
`earth_os.mind` or `earth_os.companion`).

## Measured

`tools/mind_bench.py`, on the machine above; full table in
[EARTH_MIND.md](EARTH_MIND.md) §10. Earth Mind builds in 48 ms and 8 MB,
routes in 1 ms, and adds about 4 ms to a character's turn. Model latency and
memory: not measured.

## Not tested

| What | Why | What would test it |
|---|---|---|
| Conversation with a real language model: quality, staying in character, how often explanations pass the evidence check | No model could be run | `ollama pull llama3.2:3b`, then use it; `tools/mind_bench.py` for the timings |
| Model latency, loading time and memory on real hardware; GPU | Same | `tools/mind_bench.py` |
| A remote model host (`@http://…`) across a real network | Only loopback was available | Point `[mind] model` at another machine |
| Factual answers from a CDOS corpus | No corpus was in either source project | Build one (`cdos-computer/tools/build_shards.py`) into `cdos-computer/data/` |
| The `cdos-api` service installed system-wide, with the Governor | Needs root, systemd and a `cdos` group. The socket path itself is exercised by `test_computer_link.py` against the service run as a plain process | `sudo cdos-computer/tools/install_services.sh` on a real machine |
| The GUI on a real display, on Wayland, under sway/cage, at the login screen | Only a virtual X display; sway is not installed | `make bootcheck` and a login on a real machine |
| Typing in the real window (GTK key events → terminal) | The terminal was driven through its own `submit()` and `on_key()`, as 1.0's tests do, not through GTK events | Use it |
| Voice: speech recognition and synthesis with Earth Mind | No audio devices, no whisper or piper. The voice path was driven with text through `Shell.handle_utterance` | A machine with a microphone |
| "Open Firefox" starting a real application | No desktop applications are installed here. Tested with the launcher stubbed, and with a name that is not installed | A desktop |
| A system-wide install by root (`sudo ./install.sh`) | Tested as a staged install (`DESTDIR`) and as `--user` | Run it |
| Other distributions, Python 3.11 and 3.13 | One container. The project's own rule is 3.12 or newer for the GTK tests | `tools/multi-distro.sh` |
| Long-running use: weeks of memories, thousands of characters | Measured to 10,000 records in one namespace and 200 characters; not beyond | Time |
| Concurrency from several real interfaces at once | Tested with threads against a fake model, and with `asyncio`; not with voice and terminal in use together by a person | Use |
