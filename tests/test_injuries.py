import pytest

from scanner.config import Settings
from scanner.db import connect
from scanner.injuries import Injury, InjuryReport, check, classify
from scanner.pipeline import run_scan
from test_alerts_clv import FakeKalshi, FakePoly, FakeTG, _mispriced_pair

GAME = "nba:2026-11-11:IND-WAS"


class FakeESPN:
    def __init__(self, payload=None, fail=False):
        self.payload, self.fail = payload, fail

    def get(self, path, params=None):
        if self.fail:
            raise RuntimeError("feed down")
        return self.payload


def report(*entries, fail=False):
    """entries: (name, team_abbr, status, comment[, returnDate])"""
    teams = {}
    for e in entries:
        name, team, status, comment = e[:4]
        inj = {"status": status, "shortComment": comment, "athlete": {"displayName": name, "team": {"abbreviation": team}},
               "details": {"type": "Hamstring", **({"returnDate": e[4]} if len(e) > 4 else {})}}
        teams.setdefault(team, []).append(inj)
    payload = {"injuries": [{"displayName": t, "injuries": v} for t, v in teams.items()]}
    return InjuryReport(["nba"], client=FakeESPN(payload, fail))


@pytest.mark.parametrize("raw,comment,want", [
    ("Out", "", "out"),
    ("Day-To-Day", "Quickley (hamstring) is questionable for Wednesday's game.", "questionable"),
    ("Day-To-Day", "Smith has been ruled out for Wednesday.", "out"),
    ("Day-To-Day", "Jones is doubtful to play.", "doubtful"),
    ("Day-To-Day", "Brown is probable for Wednesday.", "probable"),
    ("Injured Reserve", "", "out"),
])
def test_classify(raw, comment, want):
    assert classify(raw, comment) == want


def test_lookup_uses_team_to_avoid_same_name_mixups():
    r = report(("AJ Dybantsa", "WSH", "Out", "out"), ("Jalen Williams", "OKC", "Out", "out"))
    assert r.lookup("nba", "aj dybantsa", GAME).status == "out"        # ESPN 'WSH' == our 'WAS'
    assert r.lookup("nba", "jalen williams", GAME) is None             # OKC player, not in IND-WAS


def test_out_with_return_date_by_game_day_becomes_questionable():
    r = report(("AJ Dybantsa", "WAS", "Out", "toe", "2026-11-11"))
    assert r.lookup("nba", "aj dybantsa", GAME).status == "questionable"
    r2 = report(("AJ Dybantsa", "WAS", "Out", "toe", "2026-11-20"))
    assert r2.lookup("nba", "aj dybantsa", GAME).status == "out"


def test_check_outcomes():
    r = report(("A", "WAS", "Out", "A is out."), ("B", "WAS", "Day-To-Day", "B is questionable."),
               ("C", "IND", "Day-To-Day", "C is probable."))
    assert check(r, "nba", "a", GAME).withhold
    q = check(r, "nba", "b", GAME)
    assert not q.withhold and not q.channel_ok and "Questionable" in q.card_line
    p = check(r, "nba", "c", GAME)
    assert p.channel_ok and "Probable" in p.card_line
    h = check(r, "nba", "d", GAME)
    assert h.channel_ok and h.card_line == "🩺 Not on the injury report"
    down = check(report(fail=True), "nba", "d", GAME)
    assert not down.withhold and not down.channel_ok and "unavailable" in down.card_line
    assert check(None, "nba", "d", GAME).channel_ok        # check disabled (tests)


def _scan(conn, injuries):
    k, p, book = _mispriced_pair(ask_size=400)
    tg = FakeTG()
    summary = run_scan(Settings(), conn, FakeKalshi([k]), FakePoly([p], book), tg, ["nba"], injuries)
    return tg, summary


def test_out_player_is_withheld_everywhere(tmp_path):
    tg, summary = _scan(connect(tmp_path / "a.db"), report(("AJ Dybantsa", "WAS", "Out", "AJ is out (ankle).")))
    assert tg.sent == [] and tg.channel == [] and summary["suppressed"] >= 1


def test_questionable_player_goes_to_owner_only(tmp_path):
    tg, _ = _scan(connect(tmp_path / "b.db"),
                  report(("AJ Dybantsa", "WAS", "Day-To-Day", "AJ (ankle) is questionable for Wednesday.")))
    assert tg.sent and tg.channel == []
    assert all("🚑 Questionable (hamstring)" in m for m in tg.sent)


def test_healthy_player_reaches_channel_with_status_line(tmp_path):
    tg, _ = _scan(connect(tmp_path / "c.db"), report(("Someone Else", "IND", "Out", "x")))
    assert tg.channel and all("🩺 Not on the injury report" in m for m in tg.channel)


def test_feed_down_keeps_owner_alerts_but_not_channel(tmp_path):
    tg, _ = _scan(connect(tmp_path / "d.db"), report(fail=True))
    assert tg.sent and tg.channel == []
    assert all("Injury report unavailable" in m for m in tg.sent)
