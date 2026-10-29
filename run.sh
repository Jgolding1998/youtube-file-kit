#!/bin/sh
# Creates .venv on first run (exact pinned, hash-checked packages), then starts the app.
set -eu
cd "$(dirname "$0")"
created_environment=0
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    created_environment=1
fi
requirements_hash=$(.venv/bin/python -c 'import hashlib, pathlib; print(hashlib.sha256(pathlib.Path("requirements.txt").read_bytes()).hexdigest())')
installed_hash=
if [ -f .venv/.installed ]; then
    installed_hash=$(cat .venv/.installed)
fi
if [ "$created_environment" = 1 ] || [ "$installed_hash" != "$requirements_hash" ]; then
    .venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
    printf '%s\n' "$requirements_hash" > .venv/.installed
fi
exec .venv/bin/python -m youtube_file_kit "$@"
