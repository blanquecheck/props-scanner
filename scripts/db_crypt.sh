#!/usr/bin/env bash
# Encrypted round-trip of props.db through the `scanner-data` branch.
#
#   scripts/db_crypt.sh restore   fetch scanner-data, decrypt props.db.enc -> props.db
#   scripts/db_crypt.sh save      encrypt props.db -> props.db.enc, force-push as one commit
#
# Key: $DB_KEY, or the local .db_key file. Encryption: AES-256-CBC, PBKDF2 (200k iterations), salted.
set -euo pipefail
cd "$(dirname "$0")/.."

key="${DB_KEY:-}"
if [ -z "$key" ] && [ -f .db_key ]; then key="$(cat .db_key)"; fi
if [ -z "$key" ]; then echo "no DB_KEY and no .db_key file; refusing" >&2; exit 1; fi
export DB_KEY="$key"
CIPHER=(-aes-256-cbc -pbkdf2 -iter 200000)

case "${1:-}" in
  restore)
    if ! git fetch -q --depth=1 origin scanner-data 2>/dev/null; then
      echo "no scanner-data branch yet; starting a fresh database"; exit 0
    fi
    if git cat-file -e FETCH_HEAD:props.db.enc 2>/dev/null; then
      git show FETCH_HEAD:props.db.enc | openssl enc -d "${CIPHER[@]}" -pass env:DB_KEY -out props.db
    elif git cat-file -e FETCH_HEAD:props.db 2>/dev/null; then
      git show FETCH_HEAD:props.db > props.db   # one-time migration from the old plaintext format
      echo "migrated legacy plaintext props.db (next save is encrypted)"
    else
      echo "scanner-data has no database; starting fresh"; exit 0
    fi
    echo "restored props.db ($(du -h props.db | cut -f1))"
    ;;
  save)
    [ -f props.db ] || { echo "no props.db to save"; exit 0; }
    dir="$(mktemp -d)"
    openssl enc -e "${CIPHER[@]}" -salt -pass env:DB_KEY -in props.db -out "$dir/props.db.enc"
    remote="$(git remote get-url origin)"
    if [ -n "${GH_TOKEN:-}" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
      remote="https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
    fi
    (
      cd "$dir"
      git init -q -b scanner-data
      git add props.db.enc
      git -c user.name="props-scanner" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
          -c commit.gpgsign=false commit -q -m "encrypted props.db snapshot $(date -u +%FT%TZ)"
      git push -q -f "$remote" scanner-data
    )
    echo "saved props.db.enc ($(du -h "$dir/props.db.enc" | cut -f1))"
    rm -rf "$dir"
    ;;
  *)
    echo "usage: $0 restore|save" >&2; exit 2 ;;
esac
