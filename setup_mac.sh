#!/usr/bin/env bash
# One-time setup for Licksmith on an Apple Silicon Mac.
# Run from the project folder:  bash setup_mac.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "== 1/5 Homebrew tools (ffmpeg, uv, fluid-synth) =="
if ! command -v brew >/dev/null; then
  echo "Homebrew is missing. Install it from https://brew.sh and re-run." >&2
  exit 1
fi
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg
command -v uv >/dev/null || brew install uv
brew list fluid-synth >/dev/null 2>&1 || brew install fluid-synth   # realistic playback (SoundFonts)

echo "== 2/5 Python environment (.venv, Python 3.12) =="
uv venv --python 3.12 .venv
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install -e ".[audio]"

echo "== 3/5 MuseScore 4 (optional: PDFs and editing) =="
if [ -d "/Applications/MuseScore 4.app" ]; then
  echo "MuseScore 4 found."
else
  echo "MuseScore 4 not found. Install it with:  brew install --cask musescore"
fi

echo "== 4/5 Hugging Face login for MuScriptor weights =="
echo "Accept the licence once at https://huggingface.co/MuScriptor/muscriptor-medium (and -large if you want it),"
echo "then log in (paste a read token from https://huggingface.co/settings/tokens):"
if [ -z "${HF_TOKEN:-}" ]; then
  uvx hf auth login || echo "Skipped. Set HF_TOKEN later, or run: uvx hf auth login"
fi

echo "== 5/5 Self-test (no audio needed) =="
python -m lick4myhorn.selftest

echo
echo "Done. Activate the environment in new terminals with:  source .venv/bin/activate"
echo "Then follow README.md, starting with the Blue Train benchmark."
