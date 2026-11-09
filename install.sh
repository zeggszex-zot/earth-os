#!/bin/sh
# Install the EARTH OS shell.
#
#   sudo ./install.sh                 system-wide, with a login-screen session
#   ./install.sh --user               just for you, no root needed
#   sudo ./install.sh --no-session    system-wide, no login-screen entry
#   sudo ./install.sh --uninstall     remove it again
#   ./install.sh --user --uninstall   remove a --user install
#   PREFIX=/usr/local sudo ./install.sh
#
# Runtime dependencies are checked, not installed; see docs/INSTALL.md for the
# per-distro package names.
set -eu

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_SESSION=1
UNINSTALL=0
USER_INSTALL=0

for arg in "$@"; do
    case "$arg" in
        --user)       USER_INSTALL=1 ;;
        --no-session) INSTALL_SESSION=0 ;;
        --uninstall)  UNINSTALL=1 ;;
        -h|--help)    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

# A --user install lives entirely under $HOME and needs no privileges.  Display
# managers only read /usr/share/wayland-sessions, so a user install cannot add
# the login-screen entry; it still gets the app launcher and the command.
if [ "$USER_INSTALL" = 1 ]; then
    PREFIX="${PREFIX:-$HOME/.local}"
    INSTALL_SESSION=0
else
    PREFIX="${PREFIX:-/usr}"
fi

DESTDIR="${DESTDIR:-}"
BIN="$DESTDIR$PREFIX/bin"
LIB="$DESTDIR$PREFIX/lib/earth-os"
SHARE="$DESTDIR$PREFIX/share"
WAYLAND_SESSIONS="$SHARE/wayland-sessions"
APPLICATIONS="$SHARE/applications"

# ------------------------------------------------------------ privileges ----
# Fail here with something actionable rather than part-way through `install`.
need_root() {
    # Staged installs (DESTDIR) and anything under $HOME never need root.
    [ -n "$DESTDIR" ] && return 1
    case "$PREFIX" in
        "$HOME"/*) return 1 ;;
    esac
    [ "$(id -u)" = 0 ] && return 1
    return 0
}

if need_root; then
    echo "This installs to $PREFIX and needs root." >&2
    echo "" >&2
    echo "  sudo $0 $*" >&2
    echo "" >&2
    echo "Or install just for yourself, no root required:" >&2
    echo "" >&2
    echo "  $0 --user" >&2
    echo "" >&2
    echo "(--user skips the login-screen session entry, since display managers" >&2
    echo " only read /usr/share/wayland-sessions.)" >&2
    exit 1
fi

if [ "$UNINSTALL" = 1 ]; then
    rm -rf "$LIB"
    rm -f "$BIN/earth-os" "$BIN/earth-os-session"
    rm -f "$BIN/earth-os-setup-whisper" "$BIN/earth-os-fetch-models"
    rm -f "$WAYLAND_SESSIONS/earth-os.desktop"
    rm -f "$APPLICATIONS/earth-os.desktop"
    rm -f "$DESTDIR$PREFIX/lib/systemd/user/earth-os.service"
    rm -rf "$SHARE/earth-os"
    echo "earth-os removed."
    exit 0
fi

# ---------------------------------------------------------------- checks ----
# PYTHON lets you point at a specific interpreter when the distro's `python3`
# is not the one carrying the GTK bindings.
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || {
    echo "$PYTHON not found; set PYTHON=/path/to/python3" >&2
    exit 1
}

# Newline-separated so multi-word package names survive.
missing=""
check_python_module() {
    "$PYTHON" - "$1" <<'PY' >/dev/null 2>&1 && return 0
import importlib, sys
name = sys.argv[1]
if name == "gi.gtk4":
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk           # noqa: F401
else:
    importlib.import_module(name)
PY
    missing="$missing$2
"
}

check_python_module cairo "python3-cairo (pycairo)"
check_python_module gi "python3-gi (PyGObject)"
check_python_module gi.gtk4 "gir1.2-gtk-4.0 (GTK 4 typelib)"

if [ -n "$missing" ]; then
    echo "Missing required packages:" >&2
    printf '%s' "$missing" | while IFS= read -r item; do
        [ -n "$item" ] && echo "  - $item" >&2
    done
    echo "" >&2
    echo "Interpreter checked: $($PYTHON -c 'import sys;print(sys.executable, sys.version.split()[0])')" >&2
    echo "See docs/INSTALL.md for your distro's package names." >&2
    exit 1
fi

# Optional pieces: report but do not block.
for tool in pw-play paplay aplay; do
    if command -v "$tool" >/dev/null 2>&1; then audio_ok=1; break; fi
done
[ "${audio_ok:-0}" = 1 ] || echo "note: no audio player found, sound effects will be silent"

for tool in pw-record parecord arecord; do
    if command -v "$tool" >/dev/null 2>&1; then mic_ok=1; break; fi
done
[ "${mic_ok:-0}" = 1 ] || echo "note: no recorder found, voice input will be unavailable"

# A session with no terminal is a session you cannot fix from the inside.
for tool in x-terminal-emulator gnome-terminal konsole xfce4-terminal foot \
            alacritty kitty xterm; do
    if command -v "$tool" >/dev/null 2>&1; then term_ok=1; break; fi
done
[ "${term_ok:-0}" = 1 ] || echo "WARNING: no terminal emulator found. Install one (sudo apt install gnome-terminal) before using the session, or you will have no way to fix anything from inside it."

# Without a session bus, D-Bus activated programs — GNOME Terminal, Files, the
# file dialogs, notifications — do not start and say nothing about why.
command -v dbus-run-session >/dev/null 2>&1 || echo "WARNING: dbus-run-session not found. Without it the session has no D-Bus bus, and GNOME Terminal, Files and anything else D-Bus activated will silently fail to open. Install it: sudo apt install dbus"

# Whole-screen screenshots need a capture tool the session can use. Without
# one the SCREEN SHOT button can only picture the LCARS screen itself.
if ! command -v grim >/dev/null 2>&1 && ! command -v scrot >/dev/null 2>&1 \
        && ! command -v maim >/dev/null 2>&1; then
    echo "note: no screen capture tool; for whole-screen screenshots install grim (CDOS/sway session) and scrot (X11): sudo apt install grim scrot"
fi

# Checked again in the summary at the end, where there is a command to fix it.
command -v whisper-cli >/dev/null 2>&1 || command -v whisper >/dev/null 2>&1 \
    || echo "note: no whisper found, speech recognition disabled until installed"
command -v piper      >/dev/null 2>&1 || echo "note: piper not found, will fall back to espeak-ng for speech"
command -v ollama     >/dev/null 2>&1 || echo "note: ollama not found, the computer will answer commands but not questions"

# --------------------------------------------------------------- install ----
echo "Installing to $PREFIX ..."
install -d "$LIB" "$BIN"
# Remove any previous copy first, or a reinstall nests earth_os inside itself.
rm -rf "$LIB/earth_os"
cp -r "$SELF_DIR/earth_os" "$LIB/"
find "$LIB" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# A stamp for this build, from the code itself: the same source always gets the
# same stamp, and any change to it gets a new one. The launcher compares it with
# the workspace's own copy to tell whether the workspace was made from this
# install or an older one.
BUILD="$(cd "$SELF_DIR" && find earth_os -type f ! -path '*__pycache__*' \
          | LC_ALL=C sort | xargs cat 2>/dev/null | sha256sum 2>/dev/null \
          | cut -c1-16)"
[ -n "$BUILD" ] || BUILD="$(date +%Y%m%d%H%M%S)"
printf '%s\n' "$BUILD" > "$LIB/BUILD"

# The launcher prefers the Claude Code workspace, ~/.local/share/earth-os/src,
# when one exists: that is where accepted changes to the interface live, and a
# change that is kept but never run is not a change. It falls back to this
# stock install if the workspace will not import, and EARTH_OS_STOCK=1 skips
# it entirely. Deleting the workspace returns the machine to the stock shell.
#
# A workspace made from an *older* install is moved aside rather than run.
# Before this check, the first use of the CLAUDE CODE screen froze the machine
# on that day's code: every later reinstall updated $PREFIX/lib and the
# launcher went on running the old copy, so an upgrade appeared to do nothing
# at all, even across a reboot. The old tree is kept, history and all, beside
# the new one; nothing is deleted.
cat > "$LIB/launch.py" <<EOF
import os, sys, time
STOCK = "$PREFIX/lib/earth-os"
data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
workspace = os.path.join(data, "earth-os", "src")
use = (os.environ.get("EARTH_OS_STOCK") != "1"
       and os.path.isdir(os.path.join(workspace, ".git"))
       and os.path.isfile(os.path.join(workspace, "earth_os", "cli.py")))

def build_of(root):
    try:
        with open(os.path.join(root, "BUILD"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""

if use and build_of(STOCK) and build_of(workspace) != build_of(STOCK):
    aside = workspace + ".before-" + time.strftime("%Y%m%d-%H%M%S")
    try:
        os.rename(workspace, aside)
        print(f"earth-os: the Claude Code workspace was made from an older "
              f"install; moved it to {aside} and running the installed "
              f"version. Changes Claude made there are kept in its git "
              f"history.", file=sys.stderr)
    except OSError as exc:
        print(f"earth-os: the Claude Code workspace {workspace} is from an "
              f"older install and could not be moved aside ({exc}); running "
              f"the installed version.", file=sys.stderr)
    use = False

sys.path.insert(0, workspace if use else STOCK)
try:
    from earth_os.cli import main
except Exception as exc:
    if not use:
        raise
    print(f"earth-os: workspace {workspace} failed to load ({exc}); "
          "running the stock install", file=sys.stderr)
    sys.path.remove(workspace)
    for name in [m for m in sys.modules if m.split(".")[0] == "earth_os"]:
        del sys.modules[name]
    sys.path.insert(0, STOCK)
    from earth_os.cli import main
sys.exit(main())
EOF
cat > "$BIN/earth-os" <<EOF
#!/bin/sh
exec $PYTHON "$PREFIX/lib/earth-os/launch.py" "\$@"
EOF
chmod 0755 "$BIN/earth-os"

install -m 0755 "$SELF_DIR/session/earth-os-session" "$BIN/earth-os-session"

# The helper scripts. These were previously left out of the install entirely,
# so every instruction of the form "run tools/fetch-models.sh" pointed at a
# path that had never existed on an installed system — the scripts only worked
# if you still had the source tree lying around. They are the things that make
# voice work at all, so they ship.
if [ -d "$SELF_DIR/tools" ]; then
    rm -rf "$LIB/tools"
    install -d "$LIB/tools"
    for helper in "$SELF_DIR"/tools/*.sh; do
        [ -f "$helper" ] || continue
        install -m 0755 "$helper" "$LIB/tools/$(basename "$helper")"
    done
    # Reachable as a command, because a path under lib/ is not something
    # anyone should have to remember or type.
    if [ -f "$LIB/tools/setup-whisper.sh" ]; then
        cat > "$BIN/earth-os-setup-whisper" <<EOF
#!/bin/sh
exec "$PREFIX/lib/earth-os/tools/setup-whisper.sh" "\$@"
EOF
        chmod 0755 "$BIN/earth-os-setup-whisper"
    fi
    if [ -f "$LIB/tools/fetch-models.sh" ]; then
        cat > "$BIN/earth-os-fetch-models" <<EOF
#!/bin/sh
exec "$PREFIX/lib/earth-os/tools/fetch-models.sh" "\$@"
EOF
        chmod 0755 "$BIN/earth-os-fetch-models"
    fi
fi

# The sway config template: without it sway tiles the shell into half a screen
# as soon as a program opens.
install -d "$SHARE/earth-os"
install -m 0644 "$SELF_DIR/session/sway.conf" "$SHARE/earth-os/sway.conf"

# The full source, tests and rules included: the template Claude Code's
# editable workspace is made from on first use. Without the tests beside the
# code there would be nothing to verify a change with, and no change is kept
# unverified.
rm -rf "$SHARE/earth-os/source"
install -d "$SHARE/earth-os/source"
for part in earth_os tests tools docs session AGENTS.md CLAUDE.md README.md \
            Makefile pyproject.toml install.sh .gitignore; do
    [ -e "$SELF_DIR/$part" ] && cp -r "$SELF_DIR/$part" "$SHARE/earth-os/source/"
done
# A workspace provisioned from this template carries this build's stamp, which
# is how the launcher knows it matches the install.
cp "$LIB/BUILD" "$SHARE/earth-os/source/BUILD"
find "$SHARE/earth-os/source" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

install -d "$APPLICATIONS"
# Absolute Exec/TryExec: a bare command name silently does nothing when the
# desktop session's PATH does not include this prefix (common for ~/.local).
sed -e "s|^Exec=earth-os|Exec=$PREFIX/bin/earth-os|" \
    -e "s|^TryExec=earth-os|TryExec=$PREFIX/bin/earth-os|" \
    "$SELF_DIR/session/earth-os-app.desktop" > "$APPLICATIONS/earth-os.desktop"
chmod 0644 "$APPLICATIONS/earth-os.desktop"

if [ "$INSTALL_SESSION" = 1 ]; then
    install -d "$WAYLAND_SESSIONS"
    # Rewrite the hard-coded /usr paths so a non-default PREFIX still works.
    sed "s|/usr/bin/|$PREFIX/bin/|g" "$SELF_DIR/session/earth-os.desktop" \
        > "$WAYLAND_SESSIONS/earth-os.desktop"
    chmod 0644 "$WAYLAND_SESSIONS/earth-os.desktop"

    install -d "$DESTDIR$PREFIX/lib/systemd/user"
    sed "s|/usr/bin/|$PREFIX/bin/|g" "$SELF_DIR/session/earth-os.service" \
        > "$DESTDIR$PREFIX/lib/systemd/user/earth-os.service"
    chmod 0644 "$DESTDIR$PREFIX/lib/systemd/user/earth-os.service"
    echo "Session entry installed; pick 'EARTH OS' at the login screen."
fi

echo ""
echo "Done.  Try it without taking over the session:"
echo ""
echo "    earth-os --windowed"
echo ""

# Say what is still missing and exactly what fixes it. Finishing with a bare
# "Done." while voice silently does not work is how someone ends up restarting
# the machine wondering what changed.
voice_todo=""
if ! command -v ffmpeg >/dev/null 2>&1; then
    voice_todo="${voice_todo}    sudo apt install ffmpeg        (whisper cannot decode audio without it)
"
fi
if ! command -v whisper-cli >/dev/null 2>&1 \
        && ! command -v whisper >/dev/null 2>&1 \
        && [ ! -x "$HOME/.local/share/earth-os/whisper-env/bin/whisper" ]; then
    voice_todo="${voice_todo}    earth-os-setup-whisper       (installs speech recognition)
"
fi
if [ ! -s "$HOME/.local/share/earth-os/voices/en_US-amy-medium.onnx" ]; then
    voice_todo="${voice_todo}    earth-os-fetch-models        (better voice than espeak)
"
fi

if [ -n "$voice_todo" ]; then
    echo "Voice is not working yet.  To finish setting it up:"
    echo ""
    printf '%s' "$voice_todo"
    echo ""
    echo "Then check everything with:"
    echo ""
    echo "    earth-os --doctor"
    echo ""
else
    echo "Voice looks ready.  Confirm with: earth-os --doctor"
    echo ""
fi

# A system install under sudo can still lose to an older per-user one: the
# session finds ~/.local/bin/earth-os first on PATH, and the upgrade looks
# like it did nothing. Say so while the person is still at the terminal.
if [ "$USER_INSTALL" = 0 ] && [ -n "${SUDO_USER:-}" ] && command -v getent >/dev/null 2>&1; then
    user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    if [ -n "$user_home" ] && [ -x "$user_home/.local/bin/earth-os" ]; then
        echo "WARNING: $user_home/.local/bin/earth-os is an older per-user install,"
        echo "and it is found before this one. Remove it (as $SUDO_USER, no sudo):"
        echo ""
        echo "    ./install.sh --user --uninstall"
        echo ""
    fi
fi

# A --user install is useless if ~/.local/bin is not on PATH; say so.
case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *)
        echo "WARNING: $PREFIX/bin is not on your PATH."
        echo "Add it, or run it directly as $PREFIX/bin/earth-os"
        echo ""
        ;;
esac
