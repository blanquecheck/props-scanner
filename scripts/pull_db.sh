#!/usr/bin/env bash
# Download the latest props.db that GitHub Actions saved, so you can browse it in the local UI:
#   scripts/pull_db.sh && .venv/bin/python -m scanner ui
# Read-only copy: trades you mark in the local UI are NOT sent back. Log trades with /took in Telegram.
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch -q --depth=1 origin scanner-data
git show FETCH_HEAD:props.db > props.db
echo "pulled props.db ($(du -h props.db | cut -f1)) from the scanner-data branch"
