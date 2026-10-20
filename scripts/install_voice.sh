#!/usr/bin/env bash
# Install voice dependencies into .venv and fetch the wake-word and TTS models.
# openwakeword hard-requires tflite-runtime (no wheel for Python >= 3.12), but Minty
# runs it on onnxruntime, so it is installed without that dependency.
set -euo pipefail
cd "$(dirname "$0")/.."
PIP=.venv/bin/pip
$PIP install -e '.[voice]'
$PIP install tqdm scipy scikit-learn requests
$PIP install --no-deps openwakeword
.venv/bin/python -c "import openwakeword.utils as u; u.download_models()"
dir="${XDG_DATA_HOME:-$HOME/.local/share}/minty/voices"
mkdir -p "$dir"
base=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium
for f in en_US-lessac-medium.onnx en_US-lessac-medium.onnx.json; do
  [ -s "$dir/$f" ] || curl -fL -o "$dir/$f" "$base/$f"
done
kokoro=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  [ -s "$dir/$f" ] || curl -fL -o "$dir/$f" "$kokoro/$f"
done
echo "Voice ready (Kokoro is the default engine). Set tts.voice_model to: $dir/en_US-lessac-medium.onnx"
