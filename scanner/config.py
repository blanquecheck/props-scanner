from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLY_GAMMA_BASE = "https://gamma-api.polymarket.com"
POLY_CLOB_BASE = "https://clob.polymarket.com"

# Kalshi series ticker -> (sport, normalized stat). Only plain "N+ stat" player
# props. Ladders / escalators / protected / precise / H2H are skipped on purpose.
KALSHI_SERIES: dict[str, tuple[str, str]] = {
    "KXNBAPTS": ("nba", "points"),
    "KXNBAREB": ("nba", "rebounds"),
    "KXNBAAST": ("nba", "assists"),
    "KXNBA3PT": ("nba", "threes"),
    "KXNBASTL": ("nba", "steals"),
    "KXNBABLK": ("nba", "blocks"),
    "KXNBAPRA": ("nba", "pts_reb_ast"),
    "KXNBAPR": ("nba", "pts_reb"),
    "KXNBAPA": ("nba", "pts_ast"),
    "KXNBARA": ("nba", "reb_ast"),
    "KXNFLPASSYDS": ("nfl", "passing_yards"),
    "KXNFLRSHYDS": ("nfl", "rushing_yards"),
    "KXNFLRECYDS": ("nfl", "receiving_yards"),
    "KXNFLREC": ("nfl", "receptions"),
    "KXNFLPASSTDS": ("nfl", "passing_tds"),
    "KXNFLRSHATT": ("nfl", "rushing_attempts"),
    "KXNFLPASSATT": ("nfl", "passing_attempts"),
    "KXNFLRRYDS": ("nfl", "rush_rec_yards"),
}

# Polymarket sportsMarketType -> normalized stat. NBA types were seen live on
# 2026-11-11. NFL player-prop types were NOT present on Polymarket at build time;
# the NFL names below are best guesses and simply won't match if they differ.
POLY_STAT_TYPES: dict[str, str] = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "threes": "threes",
    "steals": "steals",
    "blocks": "blocks",
    "points_rebounds_assists": "pts_reb_ast",
    "points_rebounds": "pts_reb",
    "points_assists": "pts_ast",
    "rebounds_assists": "reb_ast",
    "passing_yards": "passing_yards",
    "rushing_yards": "rushing_yards",
    "receiving_yards": "receiving_yards",
    "receptions": "receptions",
    "passing_touchdowns": "passing_tds",
    "rushing_attempts": "rushing_attempts",
    "passing_attempts": "passing_attempts",
    "rushing_receiving_yards": "rush_rec_yards",
}

# Polymarket Gamma series ids for game events (from GET /sports, 2026-11-11).
POLY_SERIES: dict[str, str] = {"nba": "10345", "nfl": "12185"}


def _f(name: str, default: float) -> float:
    v = os.getenv(name, "").split("#")[0].strip()
    return float(v) if v else default


def _i(name: str, default: int) -> int:
    return int(_f(name, default))


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name, "").split("#")[0].strip().lower()
    if not v:
        return default
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "props.db").split("#")[0].strip() or "props.db")
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "").strip())
    # Optional members channel (e.g. -1001234567890). Gets member-friendly alerts; never takes commands.
    telegram_channel_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHANNEL_ID", "").strip())
    # Channel only gets alerts with at least this much size at the price (members can't all fill 10).
    channel_min_depth: float = field(default_factory=lambda: _f("CHANNEL_MIN_DEPTH", 50))
    min_net_edge: float = field(default_factory=lambda: _f("MIN_NET_EDGE", 0.03))
    alert_contracts: int = field(default_factory=lambda: _i("ALERT_CONTRACTS", 100))
    thin_depth_contracts: float = field(default_factory=lambda: _f("THIN_DEPTH_CONTRACTS", 50))
    max_ref_spread: float = field(default_factory=lambda: _f("MAX_REF_SPREAD", 0.05))
    wide_spread_warn: float = field(default_factory=lambda: _f("WIDE_SPREAD_WARN", 0.05))
    min_alert_confidence: float = field(default_factory=lambda: _f("MIN_ALERT_CONFIDENCE", 0.99))
    alert_cooldown_min: float = field(default_factory=lambda: _f("ALERT_COOLDOWN_MIN", 60))
    alert_edge_bump: float = field(default_factory=lambda: _f("ALERT_EDGE_BUMP", 0.02))
    hours_ahead: float = field(default_factory=lambda: _f("HOURS_AHEAD", 36))
    paper_stake_usd: float = field(default_factory=lambda: _f("PAPER_STAKE_USD", 100))
    close_lead_min: float = field(default_factory=lambda: _f("CLOSE_LEAD_MIN", 2))
    # DNP guard: Polymarket settles NO if the player sits, Kalshi settles at a "fair price".
    # When the venues disagree on one player across lines, it's almost always lineup news.
    dnp_gap: float = field(default_factory=lambda: _f("DNP_GAP", 0.05))
    max_sane_edge: float = field(default_factory=lambda: _f("MAX_SANE_EDGE", 0.15))
    # Hold Telegram while the player is Questionable or Doubtful. The opportunity is still
    # saved. A later scan alerts again once the report says probable, active, available,
    # or the player is no longer listed. Out is always withheld, whether this is on or off.
    suppress_unconfirmed_injury: bool = field(
        default_factory=lambda: _b("SUPPRESS_UNCONFIRMED_INJURY", True))
    ui_host: str = field(default_factory=lambda: os.getenv("UI_HOST", "127.0.0.1").split("#")[0].strip() or "127.0.0.1")
    ui_port: int = field(default_factory=lambda: _i("UI_PORT", 8765))

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else ROOT / p
