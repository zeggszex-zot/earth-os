# EARTH OS

A green-phosphor desktop shell for Linux, with a voice-driven computer behind
it. Green on black, glowing, scanlined — the look of a late-80s dashboard CRT
touchscreen.

- **Earth OS palette** — eleven phosphor greens, with amber for alerts.
- **VT323** — a CRT terminal face (SIL OFL, licence beside it in
  `earth_os/assets/fonts/`).
- **The tube** — soft glow around lit things, scanlines and darker corners
  (`earth_os/phosphor.py`). About 9 ms a 1080p frame;
  `[shell] phosphor = false` turns it off.
- **Its own words** — DIAGNOSTICS, ALERT, SHUTDOWN TIMER, NETWORK, BACK TO
  EARTH OS.
- **Readable on any palette** — a label on a fill too dark to knock black
  text out of is drawn lit instead, and captions in too-dark colours are
  lifted.
- **The CDOS engine inside** — the deterministic capability layer and phrase
  table that decide what the computer can be asked to do.
- **Earth Mind (2.0)** — one coordinated intelligence over CDOS (knowledge),
  the Companion (conversation), the system (real readings) and game worlds
  (characters with their own memories), all sharing one model. Type plain
  English in the terminal. It talks, reads and proposes; it never acts on its
  own. See [docs/EARTH_MIND.md](docs/EARTH_MIND.md).

## Origins

EARTH OS began as a copy of **CDOS LCARS 2.4.2** and is its own project now:
its own package (`earth_os`), command (`earth-os`), application id
(`org.earthos.shell`), login session (`EARTHOS`), configuration
(`~/.config/earth-os`), data (`~/.local/share/earth-os`), environment
variables (`EARTH_OS_*`) and screenshots folder. The two install side by side
and do not read or write each other's files.

For now the layout is still the LCARS one it started from — elbows, side
column, bar runs — and the LCARS x32 data formats still work, so the classic
palettes (Standard Colors, Nemesis, Old School, Monochrome), the original face
(`font = "LCARS"`) and the Standard and TNG language files remain available in
Settings. Underneath is the same engine: a native reimplementation of
**LCARS x32** (build 2010-09-09) carrying over its data formats, control
vocabulary and command set, with a pluggable AI subsystem in place of its SAPI
speech plumbing.

```
python3 -m earth_os --windowed   # try it without installing anything
./install.sh --user              # install for your account, no root
sudo ./install.sh                # install system-wide, with a login session
earth-os --doctor                # if anything misbehaves, start here
```

## What it is

- **A real shell, not a theme.** Installs a Wayland session entry, so "EARTH
  OS" appears at the login screen and EARTH OS is the session.
- **Resolution-independent.** Every panel is laid out as a function of the
  screen size. Correct at 1024×600 and at 4K; the original was pinned to a
  2010 screen.
- **Actually live.** The engineering panel and the main screen's gauges read
  `/proc` and `/sys` — real CPU, memory, swap, disk, network, thermal and
  battery, not decorative bars.
- **CDOS is the brain.** Speech resolves through CDOS's deterministic phrase
  table first — no model, and every entry in it is read-only. Ambiguity becomes
  buttons on screen instead of a guess. A command that would change the machine
  waits for a deliberate press. A model is used only to put CDOS's bare facts
  into sentences, and anything it proposes goes back through CDOS's own
  validator. See **[docs/CDOS.md](docs/CDOS.md)**.
- **Answers with no model at all.** CDOS reports NORMAL, FILLING, IDLE rather
  than bare numbers, so the shell can speak without one: *"Storage filling,
  91.0 percent."* Add a model and it gets conversational; take it away and it
  still answers.
- **GODCORE on the panel.** The persistent computer self — mood, energy,
  attention, and the emotional weather with its pulse and I-Zene position —
  reads live on the engineering screen.
- **Offline by default.** whisper.cpp, Piper and Ollama, all local. A cloud
  backend is one config key away if you want a smarter computer.
- **Programs live inside the interface.** Like the original, an opened program
  is placed inside the LCARS chrome rather than covering it — column, bars and
  elbows stay around it, the header takes its name, and `F11` pops it out to
  true fullscreen. A live strip of running programs runs along the **top of
  every screen** — always there, whether or not anything is running — and
  carries the reminder of which key brings you back, so the shell is never a
  trap. Needs X11 with `wmctrl`, or sway; it falls back gracefully and says
  which you have.
- **Wireless and Bluetooth.** The COMMUNICATIONS screen (`F9`) scans for
  networks and devices, joins with a password (typed, or on an on-screen
  keyboard for touch panels), and pairs and connects Bluetooth in one press.
- **Screenshots from anywhere.** SCREEN SHOT on every screen, `Print`, or
  *"take a screenshot"* — saved to `~/Pictures/EARTH OS Screenshots/`.
- **Claude Code built in.** The CLAUDE CODE screen (`F8`, or *"Claude, …"*)
  lets a coding agent drive the shell and change its own interface. Every
  change must pass the full suite before it is kept, anything that changes the
  machine waits for a press, and the rules files stay human-only. See
  **[docs/CLAUDE_CODE.md](docs/CLAUDE_CODE.md)**.
- **No heavyweight dependencies.** Python 3.11+, GTK 4, Cairo. Everything else
  is a subprocess or an HTTP call, and every piece degrades honestly when it is
  missing.

## Screens

| Screen | What it does |
|---|---|
| **Main** | The computer conversation, the original's button column, live gauges |
| **Program index** | Launcher built from XDG `.desktop` entries |
| **Computer core** | File browser; opens through `gio` or `xdg-open`, and says why when nothing can |
| **Engineering** | Live system telemetry, per-core load, system identification — beside the main screen's button column |
| **Parameters** | Palette, language, voice backends, audio, diagnostics |
| **Red alert** | Pulsing klaxon screen, because of course |
| **Running programs** | Switch to, fullscreen or close another window |
| **Communications** | Wireless networks and Bluetooth devices: scan, join, pair, connect, forget (`F9`) |
| **Claude Code** | A coding agent that drives the shell and changes its interface, behind the test gate |
| **Auto destruct** | A shutdown timer with a countdown and a two-step arm |

The square at the head of the side column holds four spots for icons. The
first is **system volume**: it shows the level and opens a slider across the
band under the title bar (`-` / `+` step five percent). It goes through
`audio.set_volume`, the same capability as *"computer, louder"*, and when the
level cannot be read it says why rather than showing a number.

## The original's files still work

Drop your own `.lxcp` palettes and `.lng` language files in beside the shipped
ones and they appear in Settings. `vCommands.ini` still defines the voice
vocabulary, `commands.xml` still defines the wake word. The bundled `lcars.ttf`
and the four original sound effects are the ones from the 2010 archive.

Palettes ship: **Earth OS** (the default), **Monochrome**, **Standard
Colors**, **Nemesis**, **Old School** and **test colors**. Three language
files: **Earth OS** (the default), **Standard** and **TNG** (which renames MY
COMPUTER to COMPUTER CORE, SETTINGS to PARAMETERS, and so on).

## Talking to it

```
"Computer, red alert."                  → runs the command
"Computer, engineering."                → switches screen
"Computer, continuous commands."        → stop needing the wake word
"Computer, how much disk have I got?"   → CDOS reads storage and answers
"Computer, is it running out of room?"  → ambiguous; CDOS offers the readings
                                          as buttons rather than guessing
"Computer, Claude, add a clock to        → Claude Code edits the interface; the
 the engineering screen"                   suite decides whether it is kept
```

Everything in the original `vCommands.ini` is wired up — all 31 phrases, their
aliases, and `go away q`. The shell's own test suite asserts that no action in
the vocabulary is left without a handler.

## Configuration

`~/.config/earth-os/config.toml`, written with comments on first run.

```toml
[shell]
palette       = "Earth OS"
font          = "VT323"        # or "LCARS" for the original face
phosphor      = true           # glow and scanlines
language      = "Earth OS"
computer_name = "EARTH OS"
shell_mode    = false        # true: DEACTIVATE powers the machine off

[voice]
stt = "whisper"              # whisper | vosk | none
tts = "piper"                # piper | espeak | none
llm = "cdos"                 # cdos | ollama | cloud | none

[cdos]
root    = "~/.local/share/earth-os/cdos"
wording = "ollama"           # who puts CDOS's facts into sentences; "none" is fine

[claude]
command     = "claude"       # Claude Code CLI; sign in once from a terminal
workspace   = ""             # blank = this checkout or ~/.local/share/earth-os/src
python      = "/usr/bin/python3.12"   # what the gate verifies changes with

[[programs]]                 # extra buttons on the program index
label = "TERMINAL"
exec  = "foot"
```

## Layout of the source

```
earth_os/
  brand.py         the name on the glass (EARTH OS) and the interface version
  theme.py         .lxcp palettes, .lng strings, faces, legible-ink rules
  phosphor.py      the tube: glow, scanlines, falloff over every frame
  commands.py      vCommands.ini + commands.xml, matching and dispatch
  config.py        defaults, TOML, environment overrides
  sysinfo.py       /proc and /sys telemetry
  windows.py       sway / wlrctl / wmctrl window control
  comms.py         wireless and Bluetooth state for the COMMUNICATIONS screen
  screengrab.py    screenshot folder, --screenshot, and the draw-itself fallback
  audio.py         sound effects through whatever player exists
  core.py          shell state, navigation, actions — no GTK
  cli.py           argument parsing and failure reporting, no GTK import
  diagnostics.py   the --doctor checks
  app.py           GTK4 window, frame clock, input
  widgets/
    shapes.py      Cairo paths: pill, elbow, arrow, pie
    base.py        scene graph, text, hit testing
    controls.py    the LCARS control set
  screens/         one module per screen
  mind/            Earth Mind: the one coordination layer (docs/EARTH_MIND.md)
    mind.py        EarthMind.handle(text, interface) → Response
    router.py      six kinds of request, decided with no model
    models.py      one model client per model, shared by every voice
    memory.py      one SQLite store, namespaced, a principal on every access
    personas.py    personalities as data; a game character may only converse
    characters.py  a character's turn, on the Companion's own machinery
    system.py      readings through the capability layer; operations are proposals
    game/          worlds, events, relationships, per-character memory
    redact.py      keeping secrets out of a model's context
  companion/       the Companion Engine: conversation (docs/COMPANION.md)
  world_model.py   what the person let the computer remember (docs/WORLD_MODEL.md)
  agent/           Claude Code, built in (docs/CLAUDE_CODE.md)
    policy.py      what it may never touch, what runs unasked (human-only)
    gate.py        the suite decides what is kept (human-only)
    workspace.py   the editable git tree it works in
    session.py     claude -p, stream-json, resumable
    bridge.py      the shell's end of the control socket; gated paths only
    mcp_server.py  the agent's end: stdio MCP, stdlib only
    controller.py  the above as one object the screen and voice use
  voice/
    capture.py     microphone and the energy gate
    backends.py    whisper / vosk / piper / espeak / ollama / cloud
    cdos_backend.py  the CDOS bridge, GODCORE, and the propose-never-dispose path
    engine.py      wake word, recognition, dispatch, reasoning
tools/
  screenshot.py    render every screen headlessly
  gallery.py       render every control, for visual regression
  fetch-models.sh  download the offline models
session/           Wayland session, .desktop entries, systemd unit
tests/             540 tests, no display server required
```

`core.py` and everything below it is toolkit-agnostic, which is why
`tools/screenshot.py` can render the whole UI to a PNG with no display server —
and why the test suite renders every screen at four resolutions on every run.

```sh
make test     # 540 tests
make shots    # every screen to /tmp/cdos-shots
make gallery  # every control, for eyeballing changes
```

## If it ever leaves you at a black screen

`Ctrl`+`Alt`+`F3` gives you a text console from anywhere. Log in and run:

```sh
sudo rm -f /usr/share/wayland-sessions/earth-os.desktop
sudo sed -i 's/^Session=.*/Session=ubuntu/' /var/lib/AccountsService/users/$USER
sudo systemctl restart gdm3
```

That returns you to your normal desktop. The shell is only an application —
nothing else is touched. Full detail in [docs/INSTALL.md](docs/INSTALL.md).

## Documentation

- **[docs/EARTH_MIND.md](docs/EARTH_MIND.md)** — Earth OS 2.0's architecture:
  the component map, the router, model management, memory, personalities,
  game intelligence, security, configuration and measurements
- **[docs/RELEASE_2_0.md](docs/RELEASE_2_0.md)** — what 2.0 implements, phase
  by phase, and what remains planned
- **[docs/TESTING.md](docs/TESTING.md)** — what was tested, the results, and
  what was not
- **[docs/ROLLBACK.md](docs/ROLLBACK.md)** — switching Earth Mind off, and
  going back to 1.0
- **[docs/COMPANION.md](docs/COMPANION.md)**, **[docs/COMPUTER.md](docs/COMPUTER.md)**,
  **[docs/WORLD_MODEL.md](docs/WORLD_MODEL.md)** — the subsystems Earth Mind
  coordinates; `cdos-computer/README.md` for CDOS Computer itself
- **[docs/INSTALL.md](docs/INSTALL.md)** — per-distro packages, the AI stack,
  making it the session, keyboard shortcuts, troubleshooting
- **[docs/PORTING.md](docs/PORTING.md)** — what was in the 2010 archive, why a
  mechanical port was impossible, and the control-by-control and
  screen-by-screen mapping
- **[docs/CDOS.md](docs/CDOS.md)** — how CDOS drives the voice path, why the
  model never decides anything, and what GODCORE contributes
- **[docs/CLAUDE_CODE.md](docs/CLAUDE_CODE.md)** — the built-in coding agent:
  the gate, the bridge, what needs a press, and where changes go

## Credit and licensing

LCARS x32 was written by its own author in VB.NET; this shares none of its
code, only the plain-text config files, font and sound effects from the archive.
LCARS is Paramount's design language. This is a personal, non-commercial
reimplementation for your own machine — the same footing as every LCARS desktop
theme since the 1990s. Worth thinking about before publishing it anywhere.

The Python here is GPL-3.0-or-later.
