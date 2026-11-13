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


def report(*entries, fail=False, sport="nba"):
    """entries: (name, team_abbr, status, comment[, returnDate])"""
    teams = {}
    for e in entries:
        name, team, status, comment = e[:4]
        inj = {"status": status, "shortComment": comment, "athlete": {"displayName": name, "team": {"abbreviation": team}},
               "details": {"type": "Hamstring", **({"returnDate": e[4]} if len(e) > 4 else {})}}
        teams.setdefault(team, []).append(inj)
    payload = {"injuries": [{"displayName": t, "injuries": v} for t, v in teams.items()]}
    return InjuryReport([sport], client=FakeESPN(payload, fail))


@pytest.mark.parametrize("raw,comment,want", [
    ("Out", "", "out"),
    ("Day-To-Day", "Quickley (hamstring) is questionable for Wednesday's game.", "questionable"),
    ("Day-To-Day", "Smith has been ruled out for Wednesday.", "out"),
    ("Day-To-Day", "Jones is doubtful to play.", "doubtful"),
    ("Day-To-Day", "Brown is probable for Wednesday.", "probable"),
    ("Injured Reserve", "", "out"),
    ("Active", "", "active"),
    ("Available", "Cleared and available.", "active"),
    ("Active", "He has been ruled out.", "out"),
    ("Day-To-Day", "Mitchell is available for tonight.", "active"),
    ("Day-To-Day", "He is not available.", "questionable"),
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
               ("C", "IND", "Day-To-Day", "C is probable."), ("D", "WAS", "Doubtful", "D is doubtful."),
               ("E", "IND", "Active", "E is active."))
    assert check(r, "nba", "a", GAME).withhold.startswith("listed OUT")
    q = check(r, "nba", "b", GAME)
    assert q.withhold == "Questionable on injury report: waiting for status"
    assert not q.channel_ok and "Questionable" in q.card_line
    assert check(r, "nba", "b", GAME, hold_unconfirmed=False).withhold is None
    d = check(r, "nba", "d", GAME)
    assert d.withhold == "Doubtful on injury report: waiting for status"
    assert check(r, "nba", "d", GAME, hold_unconfirmed=False).withhold.startswith("listed DOUBTFUL")
    p = check(r, "nba", "c", GAME)
    assert p.channel_ok and not p.withhold and "Probable" in p.card_line
    assert check(r, "nba", "e", GAME).channel_ok and not check(r, "nba", "e", GAME).withhold
    h = check(r, "nba", "z", GAME)
    assert h.channel_ok and h.card_line == "🩺 Not on the injury report"
    down = check(report(fail=True), "nba", "d", GAME)
    assert not down.withhold and not down.channel_ok and "unavailable" in down.card_line
    assert check(None, "nba", "d", GAME).channel_ok        # check disabled (tests)


def test_nfl_questionable_uses_the_same_hold():
    r = report(("Patrick Mahomes", "KC", "Questionable", "ankle"), sport="nfl")
    game = "nfl:2026-11-15:KC-LV"
    held = check(r, "nfl", "patrick mahomes", game)
    assert held.withhold == "Questionable on injury report: waiting for status"
    assert not held.channel_ok


def _scan(conn, injuries):
    k, p, book = _mispriced_pair(ask_size=400)
    tg = FakeTG()
    summary = run_scan(Settings(), conn, FakeKalshi([k]), FakePoly([p], book), tg, ["nba"], injuries)
    return tg, summary


def test_out_player_is_withheld_everywhere(tmp_path):
    tg, summary = _scan(connect(tmp_path / "a.db"), report(("AJ Dybantsa", "WAS", "Out", "AJ is out (ankle).")))
    assert tg.sent == [] and tg.channel == [] and summary["suppressed"] >= 1


def _reasons(conn):
    return [r["suppressed"] for r in conn.execute("SELECT suppressed FROM opportunities")]


def test_questionable_is_suppressed_and_still_recorded(tmp_path):
    conn = connect(tmp_path / "b.db")
    tg, summary = _scan(conn, report(("AJ Dybantsa", "WAS", "Day-To-Day",
                                      "AJ (ankle) is questionable for Wednesday.")))
    assert tg.sent == [] and tg.channel == [] and summary["suppressed"] >= 1 and summary["alerted"] == 0
    reasons = _reasons(conn)
    assert reasons and all(r == "Questionable on injury report: waiting for status" for r in reasons)
    # The injury note stays on the row; paper size is unchanged by the hold.
    row = conn.execute("SELECT warnings, paper_contracts, paper_cost, alerted FROM opportunities").fetchone()
    assert "Questionable (hamstring) on injury report" in row["warnings"]
    assert row["alerted"] == 0 and row["paper_contracts"] and row["paper_cost"]


def test_doubtful_is_suppressed(tmp_path):
    conn = connect(tmp_path / "b2.db")
    tg, summary = _scan(conn, report(("AJ Dybantsa", "WAS", "Doubtful", "AJ (ankle) is doubtful.")))
    assert tg.sent == [] and tg.channel == [] and summary["suppressed"] >= 1 and summary["alerted"] == 0
    reasons = _reasons(conn)
    assert reasons and all(r == "Doubtful on injury report: waiting for status" for r in reasons)


@pytest.mark.parametrize("later", ["probable", "active", "unlisted"])
def test_questionable_alerts_again_after_status_clears(tmp_path, later):
    conn = connect(tmp_path / "b3.db")
    k, p, book = _mispriced_pair(ask_size=400)
    tg = FakeTG()
    s = Settings()
    kalshi, poly = FakeKalshi([k]), FakePoly([p], book)
    held = run_scan(s, conn, kalshi, poly, tg, ["nba"],
                    report(("AJ Dybantsa", "WAS", "Day-To-Day", "AJ (ankle) is questionable.")))
    assert tg.sent == [] and held["alerted"] == 0 and held["suppressed"] >= 1
    assert conn.execute("SELECT COUNT(*) FROM alert_state").fetchone()[0] == 0
    if later == "probable":
        nxt = report(("AJ Dybantsa", "WAS", "Probable", "AJ is probable."))
    elif later == "active":
        nxt = report(("AJ Dybantsa", "WAS", "Active", "AJ is active."))
    else:
        nxt = report(("Someone Else", "IND", "Out", "x"))  # no longer on the report
    cleared = run_scan(s, conn, kalshi, poly, tg, ["nba"], nxt)
    assert tg.sent and tg.channel and cleared["alerted"] >= 1
    fresh = conn.execute(
        "SELECT suppressed, alerted FROM opportunities ORDER BY id DESC LIMIT 1").fetchone()
    assert fresh["suppressed"] is None and fresh["alerted"] == 1
    # The held row from the first scan is left as-is.
    assert conn.execute(
        "SELECT COUNT(*) FROM opportunities WHERE suppressed=?",
        ("Questionable on injury report: waiting for status",)).fetchone()[0] >= 1


def test_questionable_alerts_owner_when_hold_is_off(tmp_path):
    conn = connect(tmp_path / "b4.db")
    k, p, book = _mispriced_pair(ask_size=400)
    tg = FakeTG()
    s = Settings()
    s.suppress_unconfirmed_injury = False
    summary = run_scan(s, conn, FakeKalshi([k]), FakePoly([p], book), tg, ["nba"],
                       report(("AJ Dybantsa", "WAS", "Day-To-Day",
                               "AJ (ankle) is questionable for Wednesday.")))
    assert tg.sent and tg.channel == [] and summary["alerted"] >= 1
    assert all("🚑 Questionable (hamstring)" in m for m in tg.sent)
    assert all(r is None for r in _reasons(conn))


def test_healthy_player_still_alerts(tmp_path):
    conn = connect(tmp_path / "c.db")
    tg, summary = _scan(conn, report(("Someone Else", "IND", "Out", "x")))
    assert summary["alerted"] >= 1 and tg.sent and tg.channel
    assert all("🩺 Not on the injury report" in m for m in tg.sent + tg.channel)
    assert all(r is None for r in _reasons(conn))


def test_feed_down_keeps_owner_alerts_but_not_channel(tmp_path):
    tg, _ = _scan(connect(tmp_path / "d.db"), report(fail=True))
    assert tg.sent and tg.channel == []
    assert all("Injury report unavailable" in m for m in tg.sent)
