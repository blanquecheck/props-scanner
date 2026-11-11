"""Interactive Telegram setup: verify the bot token, find your chat id, write .env, send a test.

Run:  .venv/bin/python -m scanner setup-telegram
The token is typed into your terminal (hidden) and written only to the local, gitignored .env.
"""
from __future__ import annotations

import getpass
import re
import time
from pathlib import Path

import httpx

from .config import ROOT

API = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


def set_env_values(env_path: Path, values: dict[str, str], template: Path | None = None) -> None:
    """Set KEY=value lines in .env, creating it from .env.example if missing. Other lines are kept."""
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    elif template and template.exists():
        lines = template.read_text().splitlines()
    else:
        lines = []
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={values[key]}"
            done.add(key)
    lines += [f"{k}={v}" for k, v in values.items() if k not in done]
    env_path.write_text("\n".join(lines) + "\n")
    env_path.chmod(0o600)


def find_chat_id(client: httpx.Client, token: str, timeout_s: float = 180, poll_s: float = 3) -> tuple[str, str] | None:
    """Wait for any private message to the bot; return (chat_id, first name)."""
    deadline = time.monotonic() + timeout_s
    offset = None
    while time.monotonic() < deadline:
        params = {"timeout": 0}
        if offset is not None:
            params["offset"] = offset
        r = client.get(f"{API}/bot{token}/getUpdates", params=params).json()
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("edited_message") or {}
            chat = msg.get("chat") or {}
            if chat.get("type") == "private" and "id" in chat:
                return str(chat["id"]), chat.get("first_name", "")
        time.sleep(poll_s)
    return None


def run(client: httpx.Client | None = None, env_path: Path | None = None, input_fn=getpass.getpass) -> int:
    client = client or httpx.Client(timeout=15)
    env_path = env_path or ROOT / ".env"
    print("Paste the bot token from @BotFather (input is hidden):")
    token = input_fn("token: ").strip()
    if not TOKEN_RE.match(token):
        print("That doesn't look like a bot token (expected something like 123456789:AAH...). Nothing saved.")
        return 1
    me = client.get(f"{API}/bot{token}/getMe").json()
    if not me.get("ok"):
        print(f"Telegram rejected the token: {me.get('description', 'unknown error')}. Nothing saved.")
        return 1
    username = me["result"].get("username", "")
    # Clear any webhook so getUpdates works (harmless for a fresh bot).
    client.get(f"{API}/bot{token}/deleteWebhook")
    print(f"\nToken OK: @{username}.")
    print(f"Now open Telegram, find @{username} (https://t.me/{username}), press Start or send it any message.")
    print("Waiting up to 3 minutes...")
    found = find_chat_id(client, token)
    if not found:
        print("No message received. Run this again and message the bot while it's waiting. Nothing saved.")
        return 1
    chat_id, name = found
    set_env_values(env_path, {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id},
                   template=ROOT / ".env.example")
    print(f"Got your chat{f' ({name})' if name else ''}. Saved TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to {env_path}.")
    r = client.post(f"{API}/bot{token}/sendMessage",
                    data={"chat_id": chat_id, "text": "✅ Props scanner connected. You'll get prop alerts here. "
                                                      "Read-only: it never places orders."}).json()
    if r.get("ok"):
        print("Test message sent. Check Telegram.")
        return 0
    print(f"Saved, but the test message failed: {r.get('description')}")
    return 1
