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


def _get_updates(client: httpx.Client, token: str, params: dict) -> dict | None:
    """getUpdates that survives dropped connections and bad responses (returns None to retry)."""
    try:
        return client.get(f"{API}/bot{token}/getUpdates", params=params).json()
    except (httpx.HTTPError, ValueError):
        return None


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
        r = _get_updates(client, token, params)
        if r is None:
            time.sleep(poll_s)
            continue
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("edited_message") or {}
            chat = msg.get("chat") or {}
            if chat.get("type") == "private" and "id" in chat:
                return str(chat["id"]), chat.get("first_name", "")
        time.sleep(poll_s)
    return None


CHANNEL_INTRO = """📌 <b>How to read these alerts</b>

Each alert is a player prop priced differently on <b>Kalshi</b> and <b>Polymarket</b>. When one site is cheaper than the other site's price, even after fees, you get an alert.

<b>Example</b>
🏀 NBA · Wizards vs. Pacers · 7:00 PM ET
<b>AJ Dybantsa · OVER 19.5 points</b>
👉 Kalshi: buy YES on “20+ points” at 45¢
✅ Edge: +5.3¢ per contract after fees (≈11% on cost)

<b>What each line means</b>
• <b>OVER 19.5</b> means he needs 20 or more. <b>UNDER</b> means 19 or fewer.
• <b>👉</b> is exactly what to buy, worded the way that site words it. Kalshi only lists “20+” markets, so an UNDER there means buying <b>NO</b>.
• <b>✅ Edge</b> is how much cheaper this is than the other site's price, <b>after</b> trading fees. 5¢ on a 45¢ contract is about 11%.
• <b>💧</b> is how many contracts are available at that price. Bigger is better.
• <b>⚠️</b> warnings mean the price could be hard to get or may already be moving.

<b>Before you buy</b>
• Prices move fast. Open the market with the button and check the price is still there.
• Check the player is playing. If he sits, Polymarket pays <b>No</b> but Kalshi settles at a fair price, so the two bets aren't the same.
• An edge is an average advantage, not a sure win. Size small.

Alerts come during pregame windows (NBA nights, NFL gamedays). Most nights have zero to a few.
<i>Info only, not betting advice.</i>"""


def find_channel_id(client: httpx.Client, token: str, timeout_s: float = 300, poll_s: float = 3) -> tuple[str, str] | None:
    """Wait until the bot is made admin of a channel (or sees a post there); return (chat_id, title)."""
    deadline = time.monotonic() + timeout_s
    offset = None
    while time.monotonic() < deadline:
        params = {"timeout": 0, "allowed_updates": '["my_chat_member","channel_post"]'}
        if offset is not None:
            params["offset"] = offset
        r = _get_updates(client, token, params)
        if r is None:
            time.sleep(poll_s)
            continue
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            ev = u.get("my_chat_member") or u.get("channel_post") or {}
            chat = ev.get("chat") or {}
            status = ((u.get("my_chat_member") or {}).get("new_chat_member") or {}).get("status")
            if chat.get("type") == "channel" and (status in (None, "administrator")):
                return str(chat["id"]), chat.get("title", "")
        time.sleep(poll_s)
    return None


def post_intro(client: httpx.Client, token: str, chat_id: str) -> bool:
    r = client.post(f"{API}/bot{token}/sendMessage",
                    data={"chat_id": chat_id, "text": CHANNEL_INTRO, "parse_mode": "HTML",
                          "disable_web_page_preview": "true"}).json()
    if not r.get("ok"):
        print(f"Couldn't post the guide: {r.get('description')}")
        return False
    p = client.post(f"{API}/bot{token}/pinChatMessage",
                    data={"chat_id": chat_id, "message_id": r["result"]["message_id"],
                          "disable_notification": "true"}).json()
    print("Posted and pinned the guide." if p.get("ok") else
          f"Posted the guide, but couldn't pin it ({p.get('description')}). Give the bot 'Edit messages' and pin it by hand.")
    return True


def run_channel(client: httpx.Client | None = None, env_path: Path | None = None, input_fn=input) -> int:
    from .config import Settings
    s = Settings()
    if not s.telegram_token:
        print("Run setup-telegram first; no bot token in .env.")
        return 1
    client = client or httpx.Client(timeout=15)
    env_path = env_path or ROOT / ".env"
    username = client.get(f"{API}/bot{s.telegram_token}/getMe").json().get("result", {}).get("username", "your bot")
    print(f"""In Telegram:
  1. Create the channel (New Channel), public or private, your choice.
  2. Channel → Administrators → Add Admin → search @{username}.
     Leave 'Post messages' and 'Edit messages of others' ON (needed to post and pin).
Waiting up to 5 minutes for the bot to be added...""")
    found = find_channel_id(client, s.telegram_token)
    if not found:
        print("Didn't see the bot added to a channel. Run this again; if it's already an admin, post anything in the channel while this waits.")
        return 1
    chat_id, title = found
    set_env_values(env_path, {"TELEGRAM_CHANNEL_ID": chat_id}, template=ROOT / ".env.example")
    print(f"Found channel “{title}”. Saved TELEGRAM_CHANNEL_ID to {env_path}.")
    if input_fn("Post and pin the 'How to read these alerts' guide in the channel now? [y/N] ").strip().lower() == "y":
        post_intro(client, s.telegram_token, chat_id)
    print("\nLast step for GitHub:\n  gh secret set TELEGRAM_CHANNEL_ID --repo blanquecheck/props-scanner "
          "--body \"$(grep ^TELEGRAM_CHANNEL_ID= .env | cut -d= -f2-)\"")
    return 0


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
