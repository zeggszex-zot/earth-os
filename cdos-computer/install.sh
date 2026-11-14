#!/usr/bin/env bash
# CDOS Computer installer for a Raspberry Pi Zero 2 W running Raspberry Pi OS Lite (64-bit, Bookworm or later).
#
#   ./install.sh                 computer + voice (Piper) + ears (Vosk) + service
#   ./install.sh --with-llm      also build llama.cpp and fetch a mini LM (slow: the build can take an hour)
#   ./install.sh --llm-360m      same, with the bigger SmolLM2-360M (better words, slower, ~270 MB)
#
# Run it from the cdos-computer folder, as your normal user (it uses sudo where it must).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WITH_LLM=0
LLM_REPO="bartowski/SmolLM2-135M-Instruct-GGUF"
LLM_FILE="SmolLM2-135M-Instruct-Q4_K_M.gguf"
for a in "$@"; do
  case "$a" in
    --with-llm) WITH_LLM=1 ;;
    --llm-360m) WITH_LLM=1; LLM_REPO="bartowski/SmolLM2-360M-Instruct-GGUF"; LLM_FILE="SmolLM2-360M-Instruct-Q4_K_M.gguf" ;;
    *) echo "unknown option $a"; exit 1 ;;
  esac
done

say() { printf '\n\033[1;32m[CDOS] %s\033[0m\n' "$*"; }

say "System packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-gpiozero python3-lgpio alsa-utils espeak-ng unzip curl git

say "Python environment (.venv, with the system gpiozero)"
python3 -m venv --system-site-packages "$HERE/.venv"
"$HERE/.venv/bin/pip" install --upgrade pip
"$HERE/.venv/bin/pip" install vosk

say "Ears: Vosk small English model (~40 MB)"
mkdir -p "$HERE/models"
if [ ! -d "$HERE/models/vosk-model-small-en-us-0.15" ]; then
  curl -L -o /tmp/vosk.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
  unzip -q /tmp/vosk.zip -d "$HERE/models" && rm /tmp/vosk.zip
fi

say "Voice: Piper + the 'lessac low' voice"
if [ ! -x /opt/piper/piper ]; then
  curl -L -o /tmp/piper.tgz https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz
  sudo tar -xzf /tmp/piper.tgz -C /opt && rm /tmp/piper.tgz
  printf '#!/bin/sh\nexec /opt/piper/piper "$@"\n' | sudo tee /usr/local/bin/piper >/dev/null
  sudo chmod +x /usr/local/bin/piper
fi
mkdir -p "$HERE/voices"
V=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/low
[ -f "$HERE/voices/en_US-lessac-low.onnx" ] || curl -L -o "$HERE/voices/en_US-lessac-low.onnx" "$V/en_US-lessac-low.onnx"
[ -f "$HERE/voices/en_US-lessac-low.onnx.json" ] || curl -L -o "$HERE/voices/en_US-lessac-low.onnx.json" "$V/en_US-lessac-low.onnx.json"

if [ ! -f "$HERE/data/meta.json" ]; then
  say "No corpus yet: copy the data/ folder from cdos-web into $HERE/data (the computer still runs without it)"
fi
[ -f "$HERE/config.toml" ] || cp "$HERE/config.example.toml" "$HERE/config.toml"

if [ "$WITH_LLM" = 1 ]; then
  say "Mini LM: building llama.cpp's llama-server (be patient)"
  # The Zero 2 W has 512 MB of RAM; the compiler needs swap.
  if [ -f /etc/dphys-swapfile ]; then
    sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
    sudo systemctl restart dphys-swapfile || true
  fi
  sudo apt-get install -y build-essential cmake
  [ -d "$HERE/llama.cpp" ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp "$HERE/llama.cpp"
  cmake -S "$HERE/llama.cpp" -B "$HERE/llama.cpp/build" -DLLAMA_CURL=OFF -DGGML_NATIVE=ON
  cmake --build "$HERE/llama.cpp/build" --config Release -j 2 --target llama-server
  [ -f "$HERE/models/$LLM_FILE" ] || curl -L -o "$HERE/models/$LLM_FILE" "https://huggingface.co/$LLM_REPO/resolve/main/$LLM_FILE"
  sed -e "s#@HERE@#$HERE#g" -e "s#@USER@#$USER#g" -e "s#@MODEL@#$HERE/models/$LLM_FILE#g" \
    "$HERE/systemd/computer-llm.service" | sudo tee /etc/systemd/system/computer-llm.service >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now computer-llm.service
else
  sed -i 's#^llm_mode *=.*#llm_mode = "off"#' "$HERE/config.toml"
fi

say "Audio: enable the I2S mic + amp overlay? (see README, 'Wiring')"
CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
if ! grep -q "googlevoicehat-soundcard" "$CFG"; then
  echo "  To use the INMP441 mic + MAX98357A amp, add these lines to $CFG and reboot:"
  echo "    dtparam=i2s=on"
  echo "    dtoverlay=googlevoicehat-soundcard"
  echo "  (Skip this if you use a USB sound card or an audio HAT.)"
fi

say "Service: the computer starts at boot"
# An install of the old owl build runs the same hardware: stop its services so the two don't compete.
for old in owl.service owl-llm.service; do
  if [ -f "/etc/systemd/system/$old" ]; then
    sudo systemctl disable --now "$old" || true
  fi
done
sudo usermod -aG gpio,audio "$USER" || true
sed -e "s#@HERE@#$HERE#g" -e "s#@USER@#$USER#g" "$HERE/systemd/computer.service" | sudo tee /etc/systemd/system/computer.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable computer.service

say "Done.  Check it:   $HERE/.venv/bin/python3 -m computer --check"
say "Terminal session:  $HERE/.venv/bin/python3 -m computer --text      (or reboot and hold the button)"
