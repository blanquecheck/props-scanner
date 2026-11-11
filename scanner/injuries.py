"""Injury status from ESPN's free public injury feed (JSON; no key, no scraping).

Used as a safety filter, never as a betting signal:
  out / doubtful  -> alert withheld
  questionable    -> owner alert with a warning; members channel skips it
  probable        -> shown on the card
  not listed      -> "not on the injury report"
If the feed can't be read, alerts still reach the owner (flagged) but the channel skips them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from .http import ReadOnlyClient
from .normalize import canon_team, norm_name

log = logging.getLogger(__name__)

ESPN_BASE = "https://site.api.espn.com"
ESPN_PATHS = {"nba": "/apis/site/v2/sports/basketball/nba/injuries",
              "nfl": "/apis/site/v2/sports/football/nfl/injuries"}


@dataclass
class Injury:
    status: str          # out | doubtful | questionable | probable
    raw: str             # ESPN status, e.g. "Day-To-Day"
    comment: str
    team: str            # canonical team code
    body_part: str = ""
    return_date: str | None = None

    def label(self) -> str:
        part = f" ({self.body_part.lower()})" if self.body_part else ""
        return f"{self.status.capitalize()}{part}"


def classify(raw: str, comment: str) -> str:
    r, c = (raw or "").lower(), (comment or "").lower()
    if r in ("out", "injured reserve", "suspension", "ir") or "injured reserve" in r:
        return "out"
    if r == "doubtful":
        return "doubtful"
    if r == "probable":
        return "probable"
    # Day-To-Day / Questionable: the comment usually says which way it's leaning.
    if re.search(r"\bruled out\b|\bwill not play\b|\bwon't play\b|\bwill miss\b", c):
        return "out"
    if "doubtful" in c:
        return "doubtful"
    if "probable" in c or "expected to play" in c or "will play" in c:
        return "probable"
    return "questionable"


class InjuryReport:
    def __init__(self, sports: list[str], client: ReadOnlyClient | None = None):
        self.c = client or ReadOnlyClient(ESPN_BASE, min_interval=0.1)
        self.by_name: dict[tuple[str, str], list[Injury]] = {}
        self.ok: dict[str, bool] = {}
        for sport in sports:
            try:
                self._load(sport)
                self.ok[sport] = True
            except Exception as e:  # feed down or changed: fail safe, don't crash the scan
                log.warning("injury report for %s unavailable: %s", sport, e)
                self.ok[sport] = False

    def _load(self, sport: str) -> None:
        d = self.c.get(ESPN_PATHS[sport])
        for team in d.get("injuries", []):
            for i in team.get("injuries", []):
                ath = i.get("athlete") or {}
                name = ath.get("displayName")
                if not name:
                    continue
                details = i.get("details") or {}
                comment = i.get("shortComment") or ""
                code = canon_team(sport, (ath.get("team") or {}).get("abbreviation", ""))
                inj = Injury(status=classify(i.get("status", ""), comment), raw=i.get("status", ""),
                             comment=comment, team=code, body_part=details.get("type") or "",
                             return_date=details.get("returnDate"))
                self.by_name.setdefault((sport, norm_name(name)), []).append(inj)

    def lookup(self, sport: str, player_norm: str, game_key: str) -> Injury | None:
        """Entry for this player on one of this game's two teams (avoids same-name mix-ups)."""
        entries = self.by_name.get((sport, player_norm), [])
        teams = set(game_key.split(":")[-1].split("-"))
        on_team = [e for e in entries if e.team in teams]
        pick = on_team[0] if on_team else (entries[0] if len(entries) == 1 and not entries[0].team else None)
        if pick and pick.status == "out" and pick.return_date:
            game_day = game_key.split(":")[1]
            try:
                if date.fromisoformat(pick.return_date[:10]) <= date.fromisoformat(game_day):
                    pick = Injury("questionable", pick.raw, pick.comment + " (expected back by this game)",
                                  pick.team, pick.body_part, pick.return_date)
            except ValueError:
                pass
        return pick


@dataclass
class InjuryCheck:
    withhold: str | None       # reason to withhold the alert entirely
    channel_ok: bool           # can members see it?
    card_line: str             # what the alert card says
    warning: str | None = None


def check(report: InjuryReport | None, sport: str, player_norm: str, game_key: str) -> InjuryCheck:
    if report is None:
        return InjuryCheck(None, True, "")
    if not report.ok.get(sport, False):
        return InjuryCheck(None, False, "🩺 Injury report unavailable, so check his status",
                           "injury report unavailable")
    inj = report.lookup(sport, player_norm, game_key)
    if inj is None:
        return InjuryCheck(None, True, "🩺 Not on the injury report")
    short = inj.comment[:140]
    if inj.status in ("out", "doubtful"):
        return InjuryCheck(f"listed {inj.status.upper()} on the injury report: {short}", False,
                           f"🩺 {inj.label()}: {short}")
    if inj.status == "questionable":
        return InjuryCheck(None, False, f"🚑 {inj.label()}: {short}", f"{inj.label()} on injury report")
    return InjuryCheck(None, True, f"🩺 {inj.label()}: {short}")
