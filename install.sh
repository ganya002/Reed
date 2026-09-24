#!/bin/bash
set -euo pipefail

REPO="https://github.com/ganya002/Reed.git"
DEST="${REED_DIR:-$HOME/Reed}"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Reed runs on Apple silicon Macs."
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Install Homebrew first: https://brew.sh"
  exit 1
fi

echo "Installing uv, ffmpeg, and Node.js if they are missing…"
brew install uv ffmpeg node

if [[ -d "$DEST/.git" ]]; then
  echo "Updating $DEST"
  git -C "$DEST" pull --ff-only
else
  echo "Cloning Reed into $DEST"
  git clone "$REPO" "$DEST"
fi

cd "$DEST"
chmod +x run install.sh
exec ./run
