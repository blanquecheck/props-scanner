#!/usr/bin/env bash
# Download and decrypt the latest database GitHub Actions saved, then browse it in the local UI:
#   scripts/pull_db.sh && .venv/bin/python -m scanner ui
# Needs the .db_key file (same value as the DB_KEY repo secret). This overwrites the local props.db.
# Read-only copy: trades you mark in the local UI are NOT sent back. Log trades with /took in Telegram.
set -euo pipefail
cd "$(dirname "$0")/.."
exec scripts/db_crypt.sh restore
