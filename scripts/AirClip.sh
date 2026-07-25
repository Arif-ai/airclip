#!/usr/bin/env bash
# AirClip Linux / macOS Launcher
# Installs dependencies if missing and starts AirClip

echo "⚡ Starting AirClip Engine..."
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is required to run AirClip."
    exit 1
fi

pip3 install -r requirements.txt --quiet
python3 main.py
