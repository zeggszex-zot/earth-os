#!/bin/sh
# Capture everything about this machine that a reinstall would destroy.
#
#   sh cdos-rescue.sh                    writes ~/cdos-rescue-<host>-<date>.tar.gz
#   sh cdos-rescue.sh /media/usb         writes it somewhere else
#
# Run this BEFORE wiping the disk. It writes one small archive — a few
# megabytes, not gigabytes — because everything large is rebuildable and
# everything here is not.
#
# What it takes, and why each one cannot be recreated afterwards:
#
#   the git repository      as a bundle: every commit, verifiable, one file
#   ~/.config/earth-os    your config and your sway edits
#   learned data            semantics.json, compositions.json, recent —
#                           what this machine worked out by being used
#   a conformance report    THE reason to run this before the wipe, see below
#   a doctor report         what this machine looked like when it worked
#
# What it deliberately skips: whisper-env (2.5 GB of torch, rebuildable),
# models/ and voices/ (refetchable), __pycache__, and anything in the tree
# that git already has.
#
# ---------------------------------------------------------------------------
# The conformance report is the time-critical one
#
# tools/conformance.py exists to prove one claim: the same CDOS capability
# gives the same semantic answer on two different Linuxes, by two different
# mechanisms. Proving it needs measurements from two machines.
#
# Right now this is an Ubuntu machine. In an hour it will be a Debian machine,
# and the Ubuntu measurement will be gone for good. Taking it now costs four
# seconds and turns the reinstall into the experiment the harness was built
# for. Skipping it means the proof stays synthetic — Ubuntu against a robot
# stub — when a real one was available and got thrown away.
set -eu

DEST="${1:-$HOME}"
HOST="$(hostname 2>/dev/null || echo unknown)"
STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
OUT="$DEST/cdos-rescue-$HOST-$STAMP.tar.gz"
BOX="$WORK/cdos-rescue"

trap 'rm -rf "$WORK"' EXIT
mkdir -p "$BOX"

say()  { printf '\033[96m%s\033[0m\n' "$*"; }
ok()   { printf '\033[92m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[93m  --\033[0m %s\n' "$*"; }

TREE=""
for candidate in "$HOME/earth-os" "$PWD"; do
    if [ -d "$candidate/.git" ] && [ -f "$candidate/earth_os/__init__.py" ]; then
        TREE="$candidate"
        break
    fi
done

say "CDOS rescue — $HOST"
echo

# ------------------------------------------------------------- the code ----
# A bundle is the whole repository in one file: all commits, all branches,
# cryptographically verifiable, and restored with a plain `git clone`. Far
# better than copying the tree, which loses nothing visible and everything
# that makes it a repository.
if [ -n "$TREE" ]; then
    if git -C "$TREE" rev-parse HEAD >/dev/null 2>&1; then
        git -C "$TREE" bundle create "$BOX/earth-os.bundle" --all >/dev/null 2>&1 \
            && ok "git bundle — $(git -C "$TREE" rev-list --count --all) commits"
    else
        warn "the tree has no commits yet"
    fi

    # Anything committed is in the bundle. Anything NOT committed would be
    # lost, so it is saved separately rather than assumed unimportant.
    if ! git -C "$TREE" diff --quiet HEAD 2>/dev/null \
       || [ -n "$(git -C "$TREE" status --porcelain 2>/dev/null)" ]; then
        git -C "$TREE" status --porcelain > "$BOX/uncommitted-files.txt" 2>/dev/null || true
        git -C "$TREE" diff HEAD > "$BOX/uncommitted.patch" 2>/dev/null || true
        ( cd "$TREE" && git status --porcelain | awk '$1 == "??" {print $2}' \
            | tar -czf "$BOX/untracked.tar.gz" -T - 2>/dev/null ) || true
        warn "uncommitted work saved — commit before you wipe if you can"
    else
        ok "working tree is clean"
    fi
else
    warn "no CDOS git tree found (looked in ~/earth-os)"
fi

# --------------------------------------------------------- what it learnt --
if [ -d "$HOME/.config/earth-os" ]; then
    tar -czf "$BOX/config.tar.gz" -C "$HOME/.config" earth-os 2>/dev/null \
        && ok "config — $(ls "$HOME/.config/earth-os" | tr '\n' ' ')"
else
    warn "no ~/.config/earth-os"
fi

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/earth-os"
if [ -d "$DATA" ]; then
    # Exclude the big rebuildable things by name. A rescue archive nobody can
    # be bothered to copy onto a USB stick is not a rescue archive.
    tar -czf "$BOX/learned.tar.gz" -C "$(dirname "$DATA")" \
        --exclude=earth-os/whisper-env \
        --exclude=earth-os/models \
        --exclude=earth-os/voices \
        --exclude='*.tmp' \
        earth-os 2>/dev/null && ok "learned data (semantics, compositions, recent)"
else
    warn "no learned data at $DATA"
fi

# ------------------------------------------------- the measurement, once ----
if [ -n "$TREE" ] && [ -f "$TREE/tools/conformance.py" ]; then
    PY=/usr/bin/python3.12
    [ -x "$PY" ] || PY="$(command -v python3 || echo python3)"
    say "measuring this machine before it stops existing..."
    if "$PY" "$TREE/tools/conformance.py" --json "$BOX/conformance-ubuntu.json" \
            >"$BOX/conformance-ubuntu.txt" 2>&1; then
        ok "conformance report captured"
    else
        # A partial report is still a measurement; keep whatever came out.
        warn "conformance exited non-zero — keeping the output anyway"
    fi
    grep -E "capabilities:|reachable by words" "$BOX/conformance-ubuntu.txt" \
        2>/dev/null | sed 's/^/     /' || true
fi

# ------------------------------------------------------ what it looked like -
if command -v earth-os >/dev/null 2>&1; then
    earth-os --doctor > "$BOX/doctor-ubuntu.txt" 2>&1 || true
    ok "doctor report"
fi

{
    echo "host:      $HOST"
    echo "captured:  $(date -Is)"
    echo "tree:      ${TREE:-none}"
    echo
    echo "--- os-release ---"; cat /etc/os-release 2>/dev/null
    echo; echo "--- kernel ---"; uname -a
    echo; echo "--- python ---"
    for p in python3 python3.11 python3.12 python3.13; do
        command -v "$p" >/dev/null 2>&1 && echo "$p  $("$p" -V 2>&1)"
    done
    echo; echo "--- session ---"
    echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-}"
    echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-}"
    echo; echo "--- CDOS-relevant packages ---"
    dpkg -l 2>/dev/null | awk '/python3-gi|gir1.2-gtk-4|libgtk-4|sway|wmctrl|wlrctl|piper|espeak|ffmpeg|pipewire|pulseaudio|dbus/ {print $2, $3}'
    echo; echo "--- disk ---"; df -h / "$HOME" 2>/dev/null
} > "$BOX/machine.txt" 2>&1
ok "machine description"

tar -czf "$OUT" -C "$WORK" cdos-rescue
echo
say "written: $OUT"
echo "  $(du -h "$OUT" | cut -f1)"
echo
echo "Copy it OFF this disk before you reinstall — a USB stick, another"
echo "machine, anywhere that is not about to be formatted."
