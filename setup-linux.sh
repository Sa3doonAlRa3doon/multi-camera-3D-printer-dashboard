#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required." >&2
  exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  echo "Installing Raspberry Pi OS/Debian camera and Python prerequisites..."
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip python3-opencv
fi

exec python3 setup.py
