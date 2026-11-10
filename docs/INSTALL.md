# Installing EARTH OS

## Required

The shell itself needs Python 3.11+, GTK 4 and the Cairo/GObject bindings.
Install these from the distro, not from PyPI — PyGObject built in a venv links
against headers that may not match the running GTK.

**Debian / Ubuntu**
```sh
sudo apt install python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 libgtk-4-1
```

**Arch**
```sh
sudo pacman -S python python-gobject python-cairo gtk4
```

**Fedora**
```sh
sudo dnf install python3 python3-gobject python3-cairo gtk4
```

**Alpine**
```sh
apk add python3 py3-gobject3 py3-cairo gtk4.0
```

Then, for a system-wide install with a login-screen session entry:

```sh
sudo ./install.sh
```

Or, without root, just for your own account:

```sh
./install.sh --user
```

A `--user` install lives entirely under `~/.local`, so it gives you the
`earth-os` command and an entry in your application menu, but **not** the
login-screen session — display managers only read `/usr/share/wayland-sessions`,
which needs root. Use it to try the shell out; switch to `sudo ./install.sh`
when you want it to be the session.

If `~/.local/bin` is not on your `PATH`, the installer says so.

`install.sh` checks every dependency before touching the filesystem and refuses
rather than half-installing. Reverse either form with `--uninstall`
(`./install.sh --user --uninstall` for a user install).

Other options:

```sh
sudo ./install.sh --no-session      # system-wide, no login-screen entry
PREFIX=/usr/local sudo ./install.sh # somewhere other than /usr
PYTHON=/usr/bin/python3.12 sudo ./install.sh   # a specific interpreter
```

## Recommended

| For | Package | Without it |
|---|---|---|
| Sound effects | `pipewire-utils` / `pulseaudio-utils` / `alsa-utils` | Silent, everything else works |
| Voice input | same packages (provide `pw-record`/`parecord`/`arecord`) | LISTEN reports no recorder |
| Kiosk session | `cage` (or `labwc`, `sway`, `weston`) | Session script falls back to running directly |
| Opening files | `xdg-utils` | The browser lists files but cannot open them |
| Wireless networks | `network-manager` (NetworkManager, running) | COMMUNICATIONS shows *wireless unavailable* and why |
| Bluetooth | `bluez` (bluetoothd running), plus `pipewire-pulse`/`libspa-0.2-bluetooth` or `pulseaudio-module-bluetooth` for audio devices | COMMUNICATIONS shows *bluetooth unavailable*; headphones pair but refuse to connect without the audio module |
| Whole-screen screenshots | Wayland/sway: `grim` · X11: `scrot` or `maim` · GNOME: `gnome-screenshot` · KDE: `spectacle` | SCREEN SHOT saves the LCARS screen only, and says so |

## Wireless and Bluetooth

The **COMMUNICATIONS** screen (`F9`, the main column, or *"Computer,
communications"*) lists wireless networks in range and Bluetooth devices,
and joins, pairs, connects, disconnects and forgets them.

* Wireless goes through NetworkManager, so it must be the thing managing your
  network — it is on Ubuntu, Fedora, Debian desktop and most others. A secured
  network you have not joined before asks for its password; type it, or use
  **ON-SCREEN KEYBOARD** on a touch panel.
* Bluetooth goes through BlueZ. **PAIR + CONNECT** pairs, trusts and connects
  in one press. Put the device in pairing mode first. Pairing uses
  *just works* (no code to compare), which is how a phone pairs a headset;
  an old device that insists on a PIN is offered `0000`.
* **SWITCH OFF** and **FORGET** ask for CONFIRM first: switching Bluetooth off
  can take your keyboard and mouse with it, and forgetting a network throws
  away its password. Joining a different network asks too, because it drops
  the one you are on.

`earth-os --doctor` reports whether each is ready, and why not.

## Screenshots

**SCREEN SHOT** is on every screen (and in the main column), and `Print`,
`Ctrl+Shift+S` or *"take a screenshot"* do the same. Pictures go to
`~/Pictures/EARTH OS Screenshots/` (your XDG pictures folder, if it is somewhere
else), and the screen shows where.

The whole screen is captured — other programs included — with whichever tool
your session can use: `grim` under the CDOS session (sway) and other wlroots
compositors, and `maim`, `scrot` or ImageMagick's `import` on X11.
`gnome-screenshot` and `spectacle` are used only inside GNOME and KDE: anywhere
else gnome-screenshot opens a window of its own instead of saving the picture.
With none of these, the shell draws itself into the file instead and tells you
it did — which shows the LCARS screens but not the programs inside the frame.
`sudo apt install grim scrot` covers both kinds of session.

`earth-os --screenshot` does the same from a terminal or a key binding,
printing the path. The sway session binds `Print` and `Super+Shift+S` to it,
because the compositor takes `Print` before any program sees it.

## The AI half

None of this is required. Without it the LCARS interface is fully usable by
hand; you just drive it with the pointer instead of your voice.

### Earth Mind (2.0)

Earth Mind is part of the shell and is installed with it: `./install.sh`
copies `earth_os/` (which holds `mind/` and `companion/`) and, beside it,
`cdos-computer/` — the deterministic half, run inside the shell's own process.
There is nothing further to install for it to work, and it adds no package
dependency: its memory is SQLite and its model client is `urllib`, both in
Python's standard library.

What each further piece buys:

| With | You get |
|---|---|
| nothing else | System questions from live readings, sums and policy from CDOS, the terminal's English, "remember that…", game characters answering from their written lines and memories |
| **Ollama and a model** | Open conversation with the Companion; findings explained in plain words; characters that converse |
| **a CDOS corpus** in `cdos-computer/data/` | Facts answered from the corpus, with what they rest on |
| **the `cdos-api` service** (`cdos-computer/tools/install_services.sh`) | The same computer in its own sandboxed process, shared with other programs, with the Governor deciding who may ask |

```sh
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b                 # about 2 GB; the model [companion] names by default
earth-os --doctor                       # the rows to read: computer, companion, earth mind
```

On a machine with little memory, `[companion] model = "llama3.2:1b"`; to give
game characters a smaller model than the Companion,
`[mind] game_model = "ollama:llama3.2:1b"`. A model on another machine is
`[mind] model = "ollama:llama3.2:3b@http://192.168.1.20:11434"`.

Then open the terminal screen and type *"what is using the most RAM?"*,
*"I had a long day"* or *"talk to the shopkeeper"*. The header line shows who
answered. [EARTH_MIND.md](EARTH_MIND.md) has the whole of it;
[ROLLBACK.md](ROLLBACK.md) has the way back.

**Upgrading from 1.0:** install over it (`./install.sh --user` or
`sudo ./install.sh`). Your configuration is kept and gains no required
setting. Do not run 1.0's `install.sh --user --uninstall` first: it deletes
`~/.local/share/earth-os`, which is also where learned meanings are kept.

### Speech recognition — whisper.cpp

```sh
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp && cmake -B build && cmake --build build -j --config Release
sudo install -m755 build/bin/whisper-cli /usr/local/bin/
```

### Speech synthesis — Piper

Download a release from <https://github.com/rhasspy/piper/releases> and put
`piper` on your PATH. If Piper is missing the shell falls back to `espeak-ng`
automatically, which sounds appropriately synthetic.

### Models

```sh
./tools/fetch-models.sh          # base.en whisper model + the Amy voice
./tools/fetch-models.sh small.en # larger and more accurate
```

They land in `~/.local/share/earth-os/`, where `config.toml` already points.

### Reasoning — CDOS (recommended)

CDOS is the preferred backend and the default. Build it and put the two
binaries on PATH:

```sh
cd /path/to/cdos/pkg
make && make test
sudo install -m755 cdos cdos-bridge /usr/local/bin/
```

CDOS resolves most questions deterministically with no model at all. A model is
optional and only used for wording — see [CDOS.md](CDOS.md).

### Reasoning — Ollama

```sh
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2
```

Then `[voice] llm = "ollama"` (the default). On a modest machine try
`llama3.2:1b` or `qwen2.5:3b`.

### Reasoning — cloud instead

```toml
[voice]
llm = "cloud"

[cloud]
provider    = "anthropic"          # or "openai" for any OpenAI-compatible API
model       = "claude-sonnet-4-5"
api_key_env = "ANTHROPIC_API_KEY"
```

The key is read from the named environment variable, never stored in the
config file. For a session that starts at boot, put it in
`~/.config/environment.d/cdos.conf`.

## Programs inside the interface (frame mode)

By default the shell does what the original did: a program you open is placed
**inside** the LCARS chrome — the button column, the bars and the elbows stay
around it — rather than covering the screen. The panel header takes the
program's name, a live strip of running programs sits along the **top of every
screen**, and that strip also carries the reminder of which keys get you back.

The strip is chrome, not a panel: it is in the same place on the main screen,
the program index, settings and the rest, and it stays there with nothing
running rather than appearing and disappearing. Pressing a program in it puts
that program in the frame and goes to the main screen, which is where the
frame is. The framed program is the one drawn in the highlight colour.

A program is framed **as soon as it opens**, however it was started — from the
LCARS launcher, by opening a file, or from a terminal the shell knows nothing
about. The shell watches for new windows on its own clock rather than hooking
the launch path, so nothing arrives loose — including a program started from
the PROGRAM INDEX while that screen is still up, which lands in the frame and
brings the main screen with it.

On the **RUNNING PROGRAMS** screen (`F6`) each program has a **HOME** button
beside **CLOSE**. A program taken to true fullscreen covers the shell
completely, and HOME is the click that undoes that: it puts the program back
inside the frame, raises the shell and lands on the main screen. It is a
click, so it needs the shell to be reachable — from inside a fullscreen
program the key is still `Super`+`F1`, which the compositor catches.

| Key | | Works when |
|---|---|---|
| `F11` | Framed program to true fullscreen | the shell has focus |
| `F6` | The RUNNING PROGRAMS screen | the shell has focus |
| **`Super`+`F1`** | **Back to LCARS** | **always** |
| `Super`+`F11` | Leave fullscreen and return to LCARS | always |

> The `Super` keys matter. Once a program has the keyboard, a bare `F1` goes to
> *that program* — Firefox opens its help — and the shell never sees it. Only
> the compositor can catch a key regardless of what is focused, so those two are
> bound in `sway.conf`, not in the shell.

Frame mode needs a backend that can place a window at an exact rectangle:

| Session | Frame mode |
|---|---|
| **sway / i3** | Yes — nothing to install |
| **X11 + `wmctrl`** | Yes |
| **labwc + `wlrctl`** | No — switching only; `wlrctl` cannot set geometry |
| **GNOME/KDE on Wayland** | No window control at all |

> **`wmctrl` under Wayland does not count.** XWayland leaves `DISPLAY` set, so
> `wmctrl` runs and even answers — but it only sees X11 windows. Firefox and
> most modern apps are native Wayland clients and are invisible to it, so the
> program list comes back empty and nothing moves. The shell detects this and
> reports window control as unavailable rather than pretending.

When placement is unavailable the shell falls back to `mode = "screen"`: the
program goes fullscreen and `F1` brings the shell back. It never minimises
itself, because a shell that vanishes leaves you hunting for Alt+Tab.

```toml
[windows]
mode = "frame"    # frame | screen | none
```

### Under sway

sway tiles by default, which is wrong here: the moment a program opens, sway
splits the screen and the LCARS interface shrinks into half of it. The session
ships a sway config that floats everything so the shell can place programs
inside its own frame, and the shell also claims the whole output at startup.

The config is copied to `~/.config/earth-os/sway.conf` on first run — edit
that copy. It carries a version line, and when a newer one ships, your copy is
moved to `sway.conf.bak` and replaced, so a fixed keybinding actually reaches
you instead of sitting unread in `/usr/share`.

It binds the keys sway has to provide, because there is no desktop panel and
because a key pressed inside a program never reaches the shell:
`Super+F1` or `Super+Home` back to the shell, `Super+F11` to drop a program out
of fullscreen and return, `Super+Return` a terminal, `Super+q` close,
`Super+Shift+E` exit, `Print` or `Super+Shift+S` a screenshot (through
`earth-os --screenshot`, so it works with no `~/Pictures` and says where the
picture went).

> A bare `F1` cannot work for "back to the shell". Once Firefox has the
> keyboard, `F1` opens Firefox's help — the shell never sees it. Only the
> compositor can catch a key regardless of who is focused, which is why these
> are `Super+`.

If you started sway yourself rather than through the session entry, either use
that config (`sway -c ~/.config/earth-os/sway.conf`) or add the two lines
that matter:

```
for_window [app_id=".*"] floating enable, border none
for_window [class=".*"]  floating enable, border none
```

### The session bus

A full desktop starts a D-Bus **session bus** for you. A bare compositor does
not, and without one a surprising amount of a Linux desktop silently does
nothing at all.

GNOME Terminal is the clearest case. Running `gnome-terminal` does not start a
terminal — it asks `gnome-terminal-server`, over the session bus, to open one.
With no bus, that request goes nowhere: no window, no error, no exit code worth
reading. The same is true of Files, the desktop portals (file dialogs), and
notifications.

The session script now starts one itself, wrapping the compositor in
`dbus-run-session` when `DBUS_SESSION_BUS_ADDRESS` is not already set, and sway
hands the environment on to systemd and the portals once it is up. Install
`dbus-run-session` if it is somehow missing — it is part of `dbus-daemon` on
every distribution that ships D-Bus:

```
sudo apt install dbus          # Debian/Ubuntu
sudo dnf install dbus-daemon   # Fedora
```

`earth-os --doctor` reports the bus on its `session bus` line, and the
**PROGRAM INDEX** screen now prints why a launch failed underneath the grid
rather than leaving you to guess.

## Reaching your other programs

A fullscreen shell that cannot get back to the programs it launched is a trap,
so there is a **RUNNING PROGRAMS** screen (`F6`, or say "computer, switch").
It lists other windows, switches to one — fullscreening it — and can close it.

This is the one capability that is not the shell's to have. **Only the
compositor can see another program's windows**, and they differ:

| Your session | What to install | Result |
|---|---|---|
| **sway** or **i3** | nothing | Full support: list, focus, fullscreen, close |
| **labwc**, **river**, **Wayfire** | `wlrctl` | List and focus |
| **Any X11 session** (incl. GNOME/KDE on Xorg) | `sudo apt install wmctrl` | Full support |
| **GNOME or KDE on Wayland** | — | **Not possible.** They expose no window-control protocol to applications, by design. |

`earth-os --doctor` reports which one you have on its "window control" line,
and the RUNNING PROGRAMS screen explains the situation rather than showing an
empty list.

If you are on GNOME-on-Wayland and want this to work, either log into the
"GNOME on Xorg" session and install `wmctrl`, or use the LCARS session the
installer provides — the session script runs labwc or sway, both of which
support it.

```toml
[windows]
backend             = "auto"   # auto | sway | wlrctl | wmctrl | none
fullscreen_launched = true     # fullscreen a program when switching to it
step_aside          = true     # leave fullscreen when handing over
```

### Opening files

The file browser hands files to the desktop's handler. It needs one:

```sh
sudo apt install xdg-utils
```

With `step_aside = true` the shell gets out of the way once the opened program
appears, and `F1` or "computer, back to earth os" brings it back.

## Making it the session

After `install.sh`, "EARTH OS" appears in any display manager's session list
(GDM, SDDM, LightDM, greetd). Pick it at the login screen.

For autologin straight into the shell, with greetd:

```
[terminal]
vt = 1

[default_session]
command = "/usr/bin/earth-os-session"
user = "yourname"
```

To make DEACTIVATE power the machine off rather than exit the shell:

```toml
[shell]
shell_mode = true
```

## Running it without committing to it

```sh
earth-os --windowed          # a window on your existing desktop
earth-os --no-voice          # skip the voice subsystem entirely
earth-os --palette Nemesis   # try another palette
earth-os --screen engineering
```

## Keys

| Key | Action |
|---|---|
| `Esc` | Back |
| `F1` / `Home` | Main screen |
| `F2` | Program index |
| `F3` | File browser |
| `F4` | Engineering |
| `F5` | Settings |
| `F6` | Running programs |
| `F7` | Terminal |
| `F8` | Claude Code |
| `F9` | Communications — wireless and Bluetooth |
| `Print` / `Ctrl+Shift+S` | Screenshot |
| `Super`+`F1` | Back to LCARS — works even when a program has focus |
| `F11` | Fullscreen the framed program (or the shell) |
| `F12` | Toggle listening |
| `Ctrl+Q` | Quit |

## If you end up at a black screen

The shell is a program, and a program can stop. When it does, the session ends
and returns you to the login screen — that is what `swaymsg exit` in the sway
config is for. If you ever do land on a black screen with only a cursor:

**Press `Ctrl` + `Alt` + `F3`.** That is a text console, always there, behind
whatever the graphical session is doing. Log in normally, then:

```sh
sudo rm -f /usr/share/wayland-sessions/earth-os.desktop
sudo sed -i 's/^Session=.*/Session=ubuntu/' /var/lib/AccountsService/users/$USER
sudo systemctl restart gdm3
```

The first line removes the session so it cannot be chosen. The second resets
which session you are remembered as having used — without it, the login screen
keeps putting you back into the one that failed. The third restarts the login
screen.

Nothing is lost doing this: the shell is an application, and your desktop,
files and settings are untouched. Put the session back with
`sudo ./install.sh` when you want to try again.

The crash log, if there is one, is at `~/.cache/earth-os/last-error.log`.

## Troubleshooting

**Start here: `earth-os --doctor`.**

It checks every prerequisite in order — Python, pycairo, PyGObject, GTK 4,
whether a display is reachable, whether a window can actually be created, PATH,
assets, audio in and out, and each voice backend — and prints a line per check
saying exactly what is wrong. It works even when GTK is missing entirely.

**"I ran it and nothing happened."** Almost always one of three things:

1. *Launched from a menu or file manager, and it failed invisibly.* There is no
   terminal for the error to print to. Run it from a terminal instead:
   `earth-os --windowed` — the reason will be on screen. Failures are also
   written to `~/.cache/earth-os/last-error.log`, and the shell tries to raise
   a desktop notification.
2. *`earth-os` is not on the session's PATH.* Common after `--user`, since not
   every desktop puts `~/.local/bin` on the PATH that menu entries inherit. The
   installer now writes absolute paths into the `.desktop` file, so reinstalling
   fixes it. `--doctor` reports this on its "installed as" line.
3. *No graphical session.* Over plain ssh or from a TTY there is no display to
   open a window on. `--doctor` says so explicitly.


**A program is in PROGRAM INDEX but pressing it does nothing.** Look at the
line under the grid — the shell now waits three seconds after a launch and
reports what happened there. If it mentions the session bus, see
[The session bus](#the-session-bus) above: the program is D-Bus activated and
there is no bus for it to ask. `earth-os --doctor` says the same thing.

**No terminal inside the session.** Two ways out that need nothing from the
session itself:

- `Ctrl+Alt+F3` switches to a text console. Log in there; `sudo` works
  normally. `Ctrl+Alt+F1` (or `F2`) goes back to the graphical session.
- `./install.sh --user` installs entirely under `~/.local` and needs no `sudo`
  at all, so it can be run from a file manager's "Run in terminal" or from any
  terminal you can reach.

Inside the session, `Super+Return` opens a terminal. It tries
`x-terminal-emulator`, GNOME Terminal, Konsole, xfce4-terminal, foot,
alacritty, kitty and xterm in that order, so it works on a machine that has any
one of them.

**Blank window, no LCARS font.** The bundled font is registered through
fontconfig at startup; if that fails the shell falls back to a condensed sans.
Installing it properly fixes it:
`mkdir -p ~/.local/share/fonts && cp earth_os/assets/fonts/lcars.ttf ~/.local/share/fonts/ && fc-cache -f`

**LISTEN does nothing.** Open Settings → VOICE. Every row reports its own
status: missing binary, missing model, or no recorder. It will tell you which.

**The computer runs commands but will not answer questions.** That is the
reasoning backend being unavailable; Settings → VOICE shows whether Ollama is
reachable.

**It is slow on old hardware.** Lower `[shell] fps` to 15 — the UI is redrawn
on a timer for the live gauges, and 15 is plenty.
