from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS prop_contracts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    venue         TEXT NOT NULL,              -- 'kalshi' | 'polymarket'
    market_id     TEXT NOT NULL,              -- kalshi ticker | polymarket market slug
    event_id      TEXT,                       -- kalshi event ticker | polymarket event slug
    sport         TEXT NOT NULL,
    game_key      TEXT NOT NULL,              -- sport:YYYY-MM-DD:TEAM1-TEAM2 (sorted)
    player_raw    TEXT NOT NULL,
    player_norm   TEXT NOT NULL,
    player_ext_id TEXT,
    stat          TEXT NOT NULL,
    line          REAL NOT NULL,              -- YES means stat > line (e.g. 24.5 = 25+)
    title         TEXT,
    url           TEXT,
    start_time    TEXT,                       -- ISO UTC, when known
    token_yes     TEXT,
    token_no      TEXT,
    fee_rate      REAL,
    fee_exponent  REAL,
    fee_mult      REAL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    UNIQUE (venue, market_id)
);
CREATE INDEX IF NOT EXISTS ix_pc_key ON prop_contracts (venue, game_key, stat, line);

CREATE TABLE IF NOT EXISTS quotes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id   INTEGER NOT NULL REFERENCES prop_contracts(id),
    ts            TEXT NOT NULL,
    yes_bid       REAL,
    yes_ask       REAL,
    yes_bid_size  REAL,
    yes_ask_size  REAL,
    source        TEXT                        -- 'list' | 'book'
);
CREATE INDEX IF NOT EXISTS ix_quotes_contract ON quotes (contract_id, ts);

CREATE TABLE IF NOT EXISTS matches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kalshi_id     INTEGER NOT NULL REFERENCES prop_contracts(id),
    poly_id       INTEGER NOT NULL REFERENCES prop_contracts(id),
    confidence    REAL NOT NULL,
    method        TEXT NOT NULL,              -- exact | alias | approved | fuzzy
    status        TEXT NOT NULL,              -- auto | review | approved | rejected
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (kalshi_id, poly_id)
);

CREATE TABLE IF NOT EXISTS opportunities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id      INTEGER NOT NULL REFERENCES matches(id),
    ts            TEXT NOT NULL,
    buy_venue     TEXT NOT NULL,              -- where you would buy
    side          TEXT NOT NULL,              -- 'yes' (over) | 'no' (under)
    ask           REAL NOT NULL,
    ask_size      REAL,
    fee_per       REAL NOT NULL,              -- buy-venue taker fee per contract
    ref_price     REAL NOT NULL,              -- other venue mid for the same side
    ref_spread    REAL,
    buy_spread    REAL,
    net_edge      REAL NOT NULL,              -- ref - ask - fee_per
    arb_net       REAL,                       -- locked profit/contract buying both sides, after both fees
    warnings      TEXT,
    suppressed    TEXT,                       -- reason an alert was withheld (null = eligible)
    alerted       INTEGER NOT NULL DEFAULT 0,
    paper_contracts REAL,
    paper_cost    REAL
);
CREATE INDEX IF NOT EXISTS ix_opp_match ON opportunities (match_id, ts);

CREATE TABLE IF NOT EXISTS trades_taken (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER REFERENCES opportunities(id),
    match_id      INTEGER NOT NULL REFERENCES matches(id),
    venue         TEXT NOT NULL,
    side          TEXT NOT NULL,
    price         REAL NOT NULL,
    contracts     REAL NOT NULL,
    fee           REAL NOT NULL DEFAULT 0,
    ts            TEXT NOT NULL,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS closes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id      INTEGER NOT NULL UNIQUE REFERENCES matches(id),
    ts            TEXT NOT NULL,
    kalshi_yes_bid REAL, kalshi_yes_ask REAL,
    poly_yes_bid  REAL,  poly_yes_ask  REAL,
    status        TEXT NOT NULL               -- ok | missed
);

-- dedupe state for Telegram
CREATE TABLE IF NOT EXISTS alert_state (
    key           TEXT PRIMARY KEY,           -- match_id:buy_venue:side
    last_edge     REAL NOT NULL,
    last_ts       TEXT NOT NULL
);

-- player-name aliases approved by Jaraad (in addition to data/player_aliases.csv)
CREATE TABLE IF NOT EXISTS aliases (
    sport         TEXT NOT NULL,
    kalshi_norm   TEXT NOT NULL,
    poly_norm     TEXT NOT NULL,
    PRIMARY KEY (sport, kalshi_norm, poly_norm)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn
