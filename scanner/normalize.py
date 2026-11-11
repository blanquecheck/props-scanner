"""Parsing and normalization: player names, teams, games, prop titles."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

NBA_TEAMS = {
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND",
    "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX",
    "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
}
NFL_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
    "HOU", "IND", "JAX", "KC", "LV", "LAC", "LA", "MIA", "MIN", "NE", "NO", "NYG", "NYJ",
    "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS",
}
# Alternate codes either venue may use -> canonical code above.
TEAM_ALIASES = {
    "nba": {"BRK": "BKN", "GS": "GSW", "NO": "NOP", "NOR": "NOP", "NY": "NYK", "SA": "SAS",
            "UTAH": "UTA", "UTH": "UTA", "WSH": "WAS", "PHO": "PHX", "CHO": "CHA"},
    "nfl": {"JAC": "JAX", "LAR": "LA", "LVR": "LV", "OAK": "LV", "WSH": "WAS", "GNB": "GB",
            "KAN": "KC", "NWE": "NE", "NOR": "NO", "SFO": "SF", "TAM": "TB"},
}
TEAMS = {"nba": NBA_TEAMS, "nfl": NFL_TEAMS}
MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def norm_name(name: str) -> str:
    """'Nikola Jokić Jr.' -> 'nikola jokic'. Keeps word order; strips accents, punctuation, suffixes."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("’", "'").replace("'", "").replace(".", "").replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    parts = [p for p in s.split() if p not in SUFFIXES]
    return " ".join(parts)


def canon_team(sport: str, code: str) -> str:
    c = code.upper()
    return TEAM_ALIASES.get(sport, {}).get(c, c)


def game_key(sport: str, d: date | str, team_a: str, team_b: str) -> str:
    ds = d.isoformat() if isinstance(d, date) else d
    a, b = sorted([canon_team(sport, team_a), canon_team(sport, team_b)])
    return f"{sport}:{ds}:{a}-{b}"


def split_teams(sport: str, s: str) -> tuple[str, str] | None:
    """Split Kalshi's concatenated codes, e.g. 'KCATL' -> ('KC', 'ATL')."""
    s = s.upper()
    known = TEAMS[sport] | set(TEAM_ALIASES.get(sport, {}))
    hits = [(s[:i], s[i:]) for i in range(2, len(s) - 1) if s[:i] in known and s[i:] in known]
    return hits[0] if len(hits) == 1 else None


_KALSHI_EVENT = re.compile(r"^[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$")


def parse_kalshi_event(sport: str, event_ticker: str) -> str | None:
    """'KXNBAPTS-26NOV11WASIND' -> 'nba:2026-11-11:IND-WAS'."""
    m = _KALSHI_EVENT.match(event_ticker)
    if not m:
        return None
    yy, mon, dd, teams = m.groups()
    if mon not in MONTHS:
        return None
    pair = split_teams(sport, teams)
    if not pair:
        return None
    return game_key(sport, date(2000 + int(yy), MONTHS[mon], int(dd)), *pair)


_POLY_SLUG = re.compile(r"^(nba|nfl)-([a-z]+)-([a-z]+)-(\d{4})-(\d{2})-(\d{2})$")


def parse_poly_event(slug: str) -> str | None:
    """'nba-was-ind-2026-11-11' -> 'nba:2026-11-11:IND-WAS'."""
    m = _POLY_SLUG.match(slug)
    if not m:
        return None
    sport, a, b, y, mo, d = m.groups()
    if canon_team(sport, a) not in TEAMS[sport] or canon_team(sport, b) not in TEAMS[sport]:
        return None
    return game_key(sport, f"{y}-{mo}-{d}", a, b)


_KALSHI_TITLE = re.compile(r"^(?P<name>.+?):\s*(?P<n>\d+)\+\s*(?P<stat>.+?)\s*$")


def parse_kalshi_title(title: str, floor_strike, strike_type: str | None):
    """'AJ Dybantsa: 25+ points' with floor 24.5/greater -> ('AJ Dybantsa', 24.5).
    Returns None unless title and strike agree exactly."""
    m = _KALSHI_TITLE.match(title or "")
    if not m or strike_type != "greater" or floor_strike is None:
        return None
    n = int(m.group("n"))
    if abs(float(floor_strike) - (n - 0.5)) > 1e-9:
        return None
    return m.group("name").strip(), float(floor_strike)


_POLY_Q = re.compile(r"^(?P<name>.+?):\s*(?P<stat>.+?)\s+O/U\s+(?P<line>\d+(?:\.\d+)?)\s*$")


def parse_poly_question(question: str, line) -> tuple[str, float] | None:
    """'AJ Dybantsa: Points O/U 19.5' (line=19.5) -> ('AJ Dybantsa', 19.5)."""
    m = _POLY_Q.match(question or "")
    if not m or line is None:
        return None
    ql = float(m.group("line"))
    if abs(ql - float(line)) > 1e-9:
        return None
    return m.group("name").strip(), ql
