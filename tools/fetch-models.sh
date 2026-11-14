#!/bin/sh
# Download the offline speech models the shell expects.
#
#   ./tools/fetch-models.sh              base.en whisper model + Amy voice
#   ./tools/fetch-models.sh small.en     a larger, more accurate whisper model
#
# Models land in ~/.local/share/earth-os, which is where config.toml points
# by default.  Nothing here is required to run the shell — without them the
# LCARS interface works fine, you just drive it by hand instead of by voice.
set -eu

WHISPER_SIZE="${1:-base.en}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/earth-os"
MODELS="$DATA/models"
VOICES="$DATA/voices"

mkdir -p "$MODELS" "$VOICES"

fetch() {
    url="$1"; dest="$2"
    if [ -s "$dest" ]; then
        echo "already present: $dest"
        return
    fi
    echo "fetching $(basename "$dest") ..."
    if command -v curl >/dev/null 2>&1; then
        curl -fL --progress-bar -o "$dest.part" "$url"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --show-progress -O "$dest.part" "$url"
    else
        echo "need curl or wget" >&2
        exit 1
    fi
    mv "$dest.part" "$dest"
}

# --- whisper.cpp speech recognition ----------------------------------------
WHISPER_BASE="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
fetch "$WHISPER_BASE/ggml-$WHISPER_SIZE.bin" "$MODELS/ggml-$WHISPER_SIZE.bin"

# --- piper text to speech ---------------------------------------------------
PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium"
fetch "$PIPER_BASE/en_US-amy-medium.onnx"      "$VOICES/en_US-amy-medium.onnx"
fetch "$PIPER_BASE/en_US-amy-medium.onnx.json" "$VOICES/en_US-amy-medium.onnx.json"

cat <<EOF

Done.

  whisper model : $MODELS/ggml-$WHISPER_SIZE.bin
  piper voice   : $VOICES/en_US-amy-medium.onnx

If you chose a size other than base.en, point config.toml at it:

  [whisper]
  model = "$MODELS/ggml-$WHISPER_SIZE.bin"

For the reasoning half, install Ollama and pull a model:

  ollama pull llama3.2

EOF
