# Props Scanner MVP (Path A)

Read-only scanner for **NFL + NBA player props** priced on both **Kalshi** and **Polymarket**.
It matches the same player / stat / line on both venues, computes edge **after taker fees**,
pings Telegram, and keeps a personal CLV scoreboard. Personal use, $0 spend.

**It never places orders.** It doesn't have any order code at all. Every HTTP call to a venue
goes through `scanner/http.py`, which only exposes `GET` and never sends auth headers. You place
every trade yourself.

## What it does each scan

1. **Kalshi:** reads open markets for the plain "N+ stat" player-prop series (`KXNBAPTS`,
   `KXNBAREB`, `KXNBAAST`, `KXNBA3PT`, `KXNBASTL`, `KXNBABLK`, combos, and NFL yardage /
   receptions / TDs). Ladders, escalators, "protected", H2H and range markets are skipped.
   Kalshi market data is public, so **no API key is needed**.
2. **Polymarket:** reads public Gamma game events for NBA/NFL starting in the next 36h and
   their player-prop markets ("AJ Dybantsa: Points O/U 19.5").
3. **Match:** same game (date + teams), same stat, **identical line** (Kalshi `25+` = floor 24.5
   = Polymarket `O/U 24.5`), same side meaning (YES = over). The player name must be an exact
   normalized match, a row in `data/player_aliases.csv`, or a pair you approved in the UI.
   Anything fuzzier goes to the **Review** page and never alerts.
4. **Edge:** `net = other venue's mid - ask - taker fee per contract`, for over and under, buying
   on either venue. The other venue's mid is only trusted if its spread is ≤ 5¢. Fees:
   Kalshi `ceil(0.07 × C × P × (1−P))` per order; Polymarket sports `C × 0.05 × P × (1−P)`
   (read from each market's `feeSchedule`).
5. **Confirm:** before flagging, the Polymarket side is re-read from the live CLOB book.
6. **Alert** on Telegram when net edge ≥ 3¢, with both prices, fee, depth at the ask, a
   **THIN BOOK** warning under 50 contracts, spread warnings, and Kalshi/Polymarket links.
   Dedupe: the same market + side alerts again only after 60 min or if the edge grows by 2¢.

### DNP guard

The two venues settle a player who doesn't play differently:

| | Player inactive / never takes the court |
|---|---|
| Polymarket | resolves **NO** |
| Kalshi | settles at a **fair price** |

So when a player is questionable, Polymarket's Over drops and Kalshi's doesn't. That looks like a
huge edge but isn't one, and buying both sides is **not** a lock. On the first live run
(2026-11-11), every 25–34¢ "edge" was this pattern. The scanner withholds alerts when Kalshi sits
above Polymarket on the same player across ≥2 lines by ≥5¢ (or one line by ≥10¢), and withholds any
single edge above 15¢. Withheld flags still show on the Opportunities page with the reason.
Every alert also carries a one-line DNP reminder.

## Setup

```bash
cd ~/props-scanner
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then fill in the Telegram bot token and chat id
```

Telegram (free): in Telegram, message `@BotFather`, send `/newbot`, and pick a name and username.
Then run the guided setup. It checks the token, waits for you to message your bot, saves both values
to `.env` (mode 600), and sends a test message:

```bash
.venv/bin/python -m scanner setup-telegram
```

Re-test any time with `.venv/bin/python -m scanner test-alert`.

Without Telegram configured, alerts print to the terminal instead.

## Run

```bash
.venv/bin/python -m scanner scan                 # one pass, NBA + NFL
.venv/bin/python -m scanner scan --sports nba    # one sport
.venv/bin/python -m scanner closes               # snapshot closes near tip-off
.venv/bin/python -m scanner ui                   # http://127.0.0.1:8765
.venv/bin/python -m pytest -q                    # tests
```

The UI binds to `127.0.0.1` only. It refuses to start on any other host, and it returns 403 to
any non-local client. Pages:

- **Opportunities:** the latest flag per market and side, with a **Mark taken** form to log a
  trade you placed by hand (price and quantity; the fee is computed for you).
- **Review matches:** low-confidence name pairs. "Same player" saves an alias, so future games
  match automatically. "Different" blocks the pair.
- **CLV scoreboard:** CLV for your taken trades by sport, stat and venue (raw and net of the
  entry fee), progress toward the 30-sample go/no-go, and a paper "would have bet $100" P&L
  across all non-withheld flags.

CLV = the close mid on the same venue and side (snapshotted `CLOSE_LEAD_MIN` = 2 minutes before
the start) minus your entry price. If the close job misses the window, the last stored quote
before the start is used and marked `last_quote`.

## Running on GitHub Actions (stopgap until the Pi)

The repo is **public** so Actions minutes are free and unlimited. `.github/workflows/scan.yml`
runs `python -m scanner auto` every 5 minutes (GitHub's minimum) in the pregame windows. `auto`
checks the US-Eastern clock and scans NBA 4pm–midnight, NFL Sunday 10am–9pm and Monday/Thursday
6–9pm, so daylight saving is handled. It also snapshots closes and reads your Telegram commands.

**Your data stays private.** After every run the database is encrypted (AES-256, key in the
`DB_KEY` secret) and saved to the `scanner-data` branch as `props.db.enc`, as a single
force-pushed commit. If `DB_KEY` is missing, the run stops before scanning, so a readable
database is never published. Telegram secrets are hidden by GitHub and masked in logs. Old,
unflagged data is pruned after 3 days.

One-time setup (the repo name is explicit, so these work from any folder):

```bash
.venv/bin/python -m scanner setup-telegram                      # writes .env
gh secret set TELEGRAM_BOT_TOKEN --repo blanquecheck/props-scanner   # paste the token
gh secret set TELEGRAM_CHAT_ID   --repo blanquecheck/props-scanner   # paste the chat id
gh secret set DB_KEY --repo blanquecheck/props-scanner < .db_key     # encryption key
gh workflow run scan -f force=nba                                # first run now, outside the window
```

`.db_key` is a random key in this folder (gitignored). Keep it: `pull_db.sh` needs it to decrypt.

**Logging trades:** each alert ends with its flag number. When you place the trade, reply to
the bot with `/took <id> <price> <qty>`, for example `/took 42 45 10` for 10 contracts at 45¢.
The next run (within about 5 minutes) logs it and confirms. Only messages from your
`TELEGRAM_CHAT_ID` count.

**Viewing the UI:** `scripts/pull_db.sh && .venv/bin/python -m scanner ui`. That's a read-only
copy; anything you change in the local UI isn't sent back.

Limits of the stopgap:
- **Timing:** GitHub starts scheduled runs late or skips them when busy. The close window opens
  7 minutes before the start (`CLOSE_LEAD_MIN=7`), so closes are a bit earlier than on the Pi.
- **Public code:** anyone can read the code and the run logs (scan counts only, no prices you
  traded). Nobody else can trigger runs or read the secrets.
- **60-day rule:** GitHub pauses scheduled workflows in public repos after 60 days without
  repository activity. Re-enable it on the Actions tab if that happens.

## Members channel (optional)

Your private chat with the bot keeps getting every alert plus the `/took` line. A Telegram
**channel** can also get a member-friendly copy:

- **What members see:** the same card in plain language, with **Open on Kalshi / Compare on
  Polymarket** buttons and a short "info only" note.
- **What they don't:** `/took` lines, the "both sides" line, and thin markets. A market needs at
  least `CHANNEL_MIN_DEPTH` (50) contracts at the price to go to the channel.
- **Commands:** they only work in your private chat. Channel members can't control the bot.

Setup:

```bash
.venv/bin/python -m scanner preview-alert     # sends sample cards to YOUR chat to check the look
.venv/bin/python -m scanner setup-channel     # add the bot as channel admin; finds the id, can pin the guide
gh secret set TELEGRAM_CHANNEL_ID --repo blanquecheck/props-scanner --body "$(grep ^TELEGRAM_CHANNEL_ID= .env | cut -d= -f2-)"
```

`python -m scanner channel-intro` re-posts and pins the "How to read these alerts" guide.

## Cron windows

Scans only run in pregame windows. The times below are **US Eastern**, so set your Mac's time
zone or adjust. The ready-made file is `cron/props.cron`. Install it with `crontab cron/props.cron`
(this replaces your whole crontab), or paste these lines into `crontab -e`:

```cron
# NBA nightly: scan every 3 min 4pm-11:30pm ET, closes every minute 6:30pm-midnight
*/3 16-23 * * *  cd $HOME/props-scanner && .venv/bin/python -m scanner scan --sports nba >> logs/scan.log 2>&1
* 18-23 * * *    cd $HOME/props-scanner && .venv/bin/python -m scanner closes >> logs/closes.log 2>&1

# NFL gamedays: Sunday 10am-9pm, Monday + Thursday 6pm-9pm (add Saturday late season)
*/3 10-20 * * 0  cd $HOME/props-scanner && .venv/bin/python -m scanner scan --sports nfl >> logs/scan.log 2>&1
*/3 18-20 * * 1,4 cd $HOME/props-scanner && .venv/bin/python -m scanner scan --sports nfl >> logs/scan.log 2>&1
* 12-21 * * 0    cd $HOME/props-scanner && .venv/bin/python -m scanner closes >> logs/closes.log 2>&1
* 19-21 * * 1,4  cd $HOME/props-scanner && .venv/bin/python -m scanner closes >> logs/closes.log 2>&1
```

Create the log folder once: `mkdir -p logs`. A full NBA+NFL scan takes about 10 seconds and
stays well under both venues' public rate limits (requests are throttled and retried with backoff
on 429/5xx).

## Tuning

Everything is in `.env` (see `.env.example`): `MIN_NET_EDGE`, `THIN_DEPTH_CONTRACTS`,
`MAX_REF_SPREAD`, `ALERT_COOLDOWN_MIN`, `DNP_GAP`, `MAX_SANE_EDGE`, `PAPER_STAKE_USD`, and so on.

## Known limits (as of 2026-11-11)

- **NFL:** Polymarket NFL game events had **no player props** at build time, only team and game
  lines. NFL matching is wired up, but it will find nothing until Polymarket lists NFL props.
  The NFL `sportsMarketType` names in `scanner/config.py` are guesses.
- **Polymarket prop books are usually wide.** Only about 3% of matched pairs had both spreads
  ≤ 5¢, so most pairs can't produce a trustworthy reference price.
- **Kalshi links** use the `kalshi.com/markets/<series>/<title>/<event>` format. Kalshi's site
  blocks automated checks, so the link format isn't verified.
- **Polymarket access** depends on where you are. The scanner only reads its public data.
