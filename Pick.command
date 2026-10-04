#!/bin/bash
# Double-click this to put Pick in the menu bar.
#
# The menu bar app starts the server itself and stops it on quit, so this
# script's whole job is to find the interpreter and get out of the way.
cd "$(dirname "$0")" || exit 1

PY="${PICK_PYTHON:-$HOME/.local/venvs/spotdl/bin/python}"
if [ ! -x "$PY" ]; then
  osascript -e 'display alert "Pick" message "Chybí prostředí serveru.\n\nVytvoř ho:\n  uv venv --python 3.12 ~/.local/venvs/spotdl\n  VIRTUAL_ENV=~/.local/venvs/spotdl uv pip install -r requirements.txt"'
  exit 1
fi

exec "$PY" menubar.py
