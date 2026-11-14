#!/bin/sh
# Put whisper in an environment of its own, and prove it works.
#
#   ./tools/setup-whisper.sh              openai-whisper in a private venv
#   ./tools/setup-whisper.sh --cpp        build whisper.cpp instead
#   ./tools/setup-whisper.sh --model small.en
#
# Why a venv rather than `pip install`:
#
#   * Ubuntu 24.04 and newer refuse to pip into the system Python (PEP 668),
#     and --break-system-packages is a bad trade for a 2.5GB torch install.
#   * The shell is normally started by a display manager, which gives it a
#     minimal PATH that usually excludes ~/.local/bin. A binary that works
#     perfectly in a terminal can be invisible to the GUI. An absolute path
#     into a known directory is immune to that.
#
# Nothing here touches the system Python or anything outside
# ~/.local/share/earth-os.
set -eu

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/earth-os"
VENV="$DATA/whisper-env"
MODELS="$DATA/models"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/earth-os/config.toml"

FLAVOUR="openai"
MODEL="base.en"

while [ $# -gt 0 ]; do
    case "$1" in
        --cpp)     FLAVOUR="cpp" ;;
        --openai)  FLAVOUR="openai" ;;
        --model)   shift; MODEL="${1:-base.en}" ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '\033[96m%s\033[0m\n' "$*"; }
ok()   { printf '\033[92m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[93m  !!\033[0m %s\n' "$*"; }
die()  { printf '\033[91mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

mkdir -p "$DATA" "$MODELS"

# --------------------------------------------------------------- ffmpeg ----
# openai-whisper shells out to ffmpeg to decode audio, even when handed a
# plain 16kHz WAV. Without it transcription returns an empty string and the
# shell simply never hears anything, which is a miserable thing to debug.
if [ "$FLAVOUR" = "openai" ]; then
    if command -v ffmpeg >/dev/null 2>&1; then
        ok "ffmpeg present"
    else
        warn "ffmpeg is missing and openai-whisper cannot decode audio without it"
        echo
        echo "    sudo apt install ffmpeg"
        echo
        die "install ffmpeg, then run this again"
    fi
fi

# ===========================================================================
if [ "$FLAVOUR" = "cpp" ]; then
    say "Building whisper.cpp"
    command -v git   >/dev/null 2>&1 || die "git is not installed"
    command -v cmake >/dev/null 2>&1 || die "cmake is not installed (sudo apt install cmake build-essential)"

    SRC="$DATA/whisper.cpp"
    if [ -d "$SRC/.git" ]; then
        ok "source already present at $SRC"
    else
        git clone --depth 1 https://github.com/ggerganov/whisper.cpp "$SRC"
    fi
    cmake -B "$SRC/build" -S "$SRC" >/dev/null
    cmake --build "$SRC/build" -j --config Release >/dev/null
    BINARY="$SRC/build/bin/whisper-cli"
    [ -x "$BINARY" ] || die "build finished but $BINARY is not there"
    ok "built $BINARY"

    MODEL_FILE="$MODELS/ggml-$MODEL.bin"
    if [ -s "$MODEL_FILE" ]; then
        ok "model already present"
    else
        say "Fetching ggml-$MODEL.bin"
        URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL.bin"
        if command -v curl >/dev/null 2>&1; then
            curl -fL --progress-bar -o "$MODEL_FILE.part" "$URL"
        else
            wget -q --show-progress -O "$MODEL_FILE.part" "$URL"
        fi
        mv "$MODEL_FILE.part" "$MODEL_FILE"
        ok "model at $MODEL_FILE"
    fi
    CONFIG_MODEL="$MODEL_FILE"
else
    # =======================================================================
    say "Creating a private environment for whisper"
    PY="$(command -v python3)" || die "python3 is not installed"
    "$PY" -c 'import venv' 2>/dev/null \
        || die "python3-venv is missing (sudo apt install python3-venv)"

    if [ -x "$VENV/bin/python" ]; then
        ok "environment already exists at $VENV"
    else
        "$PY" -m venv "$VENV"
        ok "created $VENV"
    fi

    say "Installing openai-whisper (this pulls PyTorch — expect a long wait)"
    "$VENV/bin/pip" install --upgrade pip >/dev/null
    "$VENV/bin/pip" install openai-whisper

    BINARY="$VENV/bin/whisper"
    [ -x "$BINARY" ] || die "install finished but $BINARY is not there"
    ok "installed $BINARY"
    CONFIG_MODEL="$MODEL"
fi

# ------------------------------------------------------------- verify ------
# Prove the whole chain rather than declaring victory: generate a WAV of
# silence and confirm the binary runs to completion on it. A setup script
# that only checks a file exists is how you end up back here tomorrow.
say "Verifying"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
python3 - "$TMP/test.wav" <<'PYEOF'
import struct, sys, wave
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(struct.pack("<16000h", *([0] * 16000)))
PYEOF

if [ "$FLAVOUR" = "cpp" ]; then
    if "$BINARY" -m "$CONFIG_MODEL" -f "$TMP/test.wav" -otxt \
            -of "$TMP/out" -nt --no-prints >/dev/null 2>&1; then
        ok "whisper.cpp ran on a test clip"
    else
        warn "the binary exists but did not run cleanly on a test clip"
    fi
else
    if "$BINARY" "$TMP/test.wav" --model "$CONFIG_MODEL" --language en \
            --output_format txt --output_dir "$TMP" --fp16 False \
            --verbose False >/dev/null 2>&1; then
        ok "whisper ran on a test clip (the model is now cached)"
    else
        warn "the binary exists but did not run cleanly on a test clip"
    fi
fi

# ------------------------------------------------------------- config ------
echo
say "Point the shell at it"
echo
echo "  [whisper]"
echo "  binary  = \"$BINARY\""
echo "  model   = \"$CONFIG_MODEL\""
echo
if [ -f "$CONFIG" ]; then
    echo "Your config is at:"
    echo "  $CONFIG"
    echo
    echo "The shell also finds $VENV/bin automatically,"
    echo "so editing the config is optional — it is only needed if you keep"
    echo "whisper somewhere else."
else
    echo "No config file yet; the shell writes one on first run."
fi
echo
echo "Then check it:"
echo "  earth-os --doctor"
