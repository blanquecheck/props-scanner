"""QC #6 (alerts + no duplicate spam), QC #7 (CLV calc), DNP guard, UI localhost."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import kalshi_contract, poly_contract
from scanner.alerts import format_alert, record_alert, should_alert
from scanner.clv import clv, flag_rows, scoreboard, side_mid, trade_rows
from scanner.config import Settings
from scanner.db import connect
from scanner.matching import match_contracts
from scanner.pipeline import player_gaps, run_scan, suppression_reason


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "t.db")


# ------------------------------------------------------------------ dedupe

def test_dedupe_suppresses_repeats(conn):
    s = Settings()
    t0 = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)
    assert should_alert(conn, "1:kalshi:yes", 0.04, s, t0)
    record_alert(conn, "1:kalshi:yes", 0.04, t0.isoformat())
    assert not should_alert(conn, "1:kalshi:yes", 0.04, s, t0 + timedelta(minutes=2))   # same edge, repeat poll
    assert not should_alert(conn, "1:kalshi:yes", 0.05, s, t0 + timedelta(minutes=2))   # +1c only
    assert should_alert(conn, "1:kalshi:yes", 0.06, s, t0 + timedelta(minutes=2))       # +2c bump
    assert should_alert(conn, "1:kalshi:yes", 0.04, s, t0 + timedelta(minutes=61))      # cooldown passed
    assert should_alert(conn, "1:kalshi:no", 0.04, s, t0)                               # other side is separate


class FakeTG:
    def __init__(self):
        self.sent = []
        self.configured = True

        self.channel = []
        self.channel_configured = True

    def send(self, text, buttons=None, chat_id=None):
        self.sent.append(text)
        self.buttons = buttons
        return True

    def send_channel(self, text, buttons=None):
        self.channel.append(text)
        return True


class FakeKalshi:
    def __init__(self, ks):
        self.ks = ks

    def contracts(self, sports):
        return list(self.ks), {}


class FakePoly:
    def __init__(self, ps, book):
        self.ps, self._book = ps, book

    def contracts(self, sports, hours):
        return list(self.ps), {}

    def book(self, token):
        return self._book


def _mispriced_pair(k_bid=0.44, k_ask=0.45, p_bid=0.51, p_ask=0.53, ask_size=400):
    k = kalshi_contract("nba", "points", "KXNBAPTS-26NOV11WASIND", "AJ Dybantsa: 20+ points", 19.5,
                        yes_bid=k_bid, yes_ask=k_ask, ask_size=ask_size, ticker="KXNBAPTS-26NOV11WASIND-X-20")
    p = poly_contract("nba", "points", "nba-was-ind-2026-11-11", "AJ Dybantsa: Points O/U 19.5", 19.5,
                      yes_bid=p_bid, yes_ask=p_ask)
    book = {"yes_bid": p_bid, "yes_ask": p_ask, "yes_bid_size": 300, "yes_ask_size": 300}
    return k, p, book


def test_scan_alerts_once_across_repeat_polls(conn):
    s = Settings()
    k, p, book = _mispriced_pair()
    tg = FakeTG()
    run_scan(s, conn, FakeKalshi([k]), FakePoly([p], book), tg, ["nba"])
    first = len(tg.sent)
    assert first >= 1
    for _ in range(2):
        run_scan(s, conn, FakeKalshi([k]), FakePoly([p], book), tg, ["nba"])
    assert len(tg.sent) == first                       # no duplicate spam on repeat polls
    msg = next(m for m in tg.sent if "👉 Kalshi:" in m)
    for needle in ("AJ Dybantsa", "OVER 19.5 points", "“20+ points”", "Edge:", "after fees", "/took", "If he sits"):
        assert needle in msg
    urls = [b["url"] for b in tg.buttons[0]]
    assert any(u.startswith("https://kalshi.com/markets/") for u in urls) and any("polymarket.com/event/" in u for u in urls)
    assert conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 3 * first   # logged every poll


def test_thin_book_shows_in_alert(conn):
    k, p, book = _mispriced_pair(ask_size=6)
    tg = FakeTG()
    run_scan(Settings(), conn, FakeKalshi([k]), FakePoly([p], book), tg, ["nba"])
    kalshi_msgs = [m for m in tg.sent if "👉 Kalshi:" in m]
    assert kalshi_msgs and all("Thin market: only 6 contracts" in m for m in kalshi_msgs)
    assert not any("👉 Kalshi:" in m for m in tg.channel)   # members never get thin markets


def test_dnp_gap_suppresses_alert(conn):
    # Kalshi 55c vs Poly 23c on the same line: Polymarket is pricing in a DNP.
    k, p, book = _mispriced_pair(k_bid=0.55, k_ask=0.58, p_bid=0.22, p_ask=0.25)
    tg = FakeTG()
    summary = run_scan(Settings(), conn, FakeKalshi([k]), FakePoly([p], book), tg, ["nba"])
    assert tg.sent == []
    assert summary["suppressed"] >= 1
    assert conn.execute("SELECT COUNT(*) FROM opportunities WHERE suppressed IS NOT NULL").fetchone()[0] >= 1


def test_suppression_rules():
    s = Settings()
    assert suppression_reason(0.04, (0.30, 2), s)          # player-wide gap
    assert suppression_reason(0.20, None, s)               # absurd single edge
    assert suppression_reason(0.04, (0.01, 3), s) is None  # normal
    assert suppression_reason(0.07, (-0.075, 1), s) is None  # Poly above Kalshi: not a DNP pattern
    assert suppression_reason(0.07, (0.07, 1), s) is None    # one line, modest gap: allowed
    assert suppression_reason(0.07, (0.07, 2), s)            # two lines agree: suppressed


def test_player_gaps_median():
    k1, p1, _ = _mispriced_pair(0.55, 0.57, 0.22, 0.24)
    res = match_contracts([k1], [p1])
    g = player_gaps(res)
    assert list(g.values())[0][0] == pytest.approx(0.33)


def test_alert_format_fields():
    msg = format_alert({"player": "LeBron James", "sport": "nba", "stat": "points", "line": 24.5, "side": "yes",
                        "buy_venue": "kalshi", "ask": 0.45, "ask_size": 12, "fee_per": 0.0174, "ref_price": 0.52,
                        "net_edge": 0.0526, "arb_net": None, "kalshi_yes_bid": 0.44, "kalshi_yes_ask": 0.45,
                        "poly_yes_bid": 0.51, "poly_yes_ask": 0.53, "warnings": ["THIN BOOK: only 12 at ask"],
                        "kalshi_url": "https://kalshi.com/markets/x", "poly_url": "https://polymarket.com/event/y",
                        "start_time": None, "opp_id": 7})
    for needle in ("LeBron James · OVER 24.5 points", "“25+ points” at <b>45¢</b>", "52¢", "+5.3¢",
                   "Thin market: only 12 contracts", "/took"):
        assert needle in msg


# ------------------------------------------------------------------ CLV

def test_clv_math():
    assert side_mid(0.50, 0.54, "yes") == pytest.approx(0.52)
    assert side_mid(0.50, 0.54, "no") == pytest.approx(0.48)
    assert clv(0.45, 0.52, fee_per=0.0174) == (pytest.approx(0.07), pytest.approx(0.0526))
    assert clv(0.45, None) == (None, None)


def test_clv_scoreboard_on_sample_data(conn):
    k, p, book = _mispriced_pair()
    run_scan(Settings(), conn, FakeKalshi([k]), FakePoly([p], book), FakeTG(), ["nba"])
    mid = conn.execute("SELECT id FROM matches").fetchone()[0]
    opp = conn.execute("SELECT id FROM opportunities WHERE buy_venue='kalshi' AND side='yes'").fetchone()[0]
    # two hand-placed trades: one beats the close, one doesn't
    conn.execute("INSERT INTO trades_taken (opportunity_id, match_id, venue, side, price, contracts, fee, ts) "
                 "VALUES (?,?,?,?,?,?,?,?)", (opp, mid, "kalshi", "yes", 0.45, 100, 1.74, "2026-11-11T20:00:00+00:00"))
    conn.execute("INSERT INTO trades_taken (opportunity_id, match_id, venue, side, price, contracts, fee, ts) "
                 "VALUES (?,?,?,?,?,?,?,?)", (opp, mid, "kalshi", "no", 0.50, 100, 1.75, "2026-11-11T20:05:00+00:00"))
    # close: Kalshi YES 0.51/0.53 -> yes mid 0.52, no mid 0.48
    conn.execute("INSERT INTO closes (match_id, ts, kalshi_yes_bid, kalshi_yes_ask, poly_yes_bid, poly_yes_ask, status) "
                 "VALUES (?,?,?,?,?,?, 'ok')", (mid, "2026-11-12T00:00:00+00:00", 0.51, 0.53, 0.52, 0.54))
    conn.commit()
    trades = {t["side"]: t for t in trade_rows(conn)}
    assert trades["yes"]["clv"] == pytest.approx(0.07)
    assert trades["yes"]["clv_net"] == pytest.approx(0.07 - 0.0174)
    assert trades["no"]["clv"] == pytest.approx(-0.02)
    board = {(b["dim"], b["value"]): b for b in scoreboard(trade_rows(conn))}
    allrow = board[("all", "all")]
    assert allrow["n"] == 2
    assert allrow["avg_clv"] == pytest.approx((0.07 - 0.02) / 2)
    assert allrow["pct_positive"] == 0.5
    assert ("sport", "nba") in board and ("stat", "points") in board and ("venue", "kalshi") in board
    flags = flag_rows(conn)
    assert flags and all(f["paper_pnl_at_close"] is not None for f in flags)


# ------------------------------------------------------------------ UI

def test_ui_rejects_non_local_clients(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import scanner.web as web
    monkeypatch.setattr(web.S, "db_path", str(tmp_path / "ui.db"))
    ok = TestClient(web.app, client=("127.0.0.1", 5555))
    assert ok.get("/").status_code == 200
    assert ok.get("/review").status_code == 200
    assert ok.get("/clv").status_code == 200
    remote = TestClient(web.app, client=("203.0.113.9", 5555))
    assert remote.get("/").status_code == 403


def test_cli_refuses_public_bind(monkeypatch):
    from scanner import cli
    monkeypatch.setenv("UI_HOST", "0.0.0.0")
    assert cli.main(["ui"]) == 2


# ------------------------------------------------------------------ members channel

def test_channel_gets_member_version_without_commands(conn):
    k, p, book = _mispriced_pair(ask_size=400)
    tg = FakeTG()
    summary = run_scan(Settings(), conn, FakeKalshi([k]), FakePoly([p], book), tg, ["nba"])
    kal = [m for m in tg.channel if "👉 Kalshi:" in m]
    assert kal and summary.get("channel_posts", 0) >= 1
    assert all("/took" not in m and "not betting advice" in m for m in tg.channel)
    assert all("🔁" not in m for m in tg.channel)                 # no both-sides talk for members
    assert any("/took" in m for m in tg.sent)                       # owner still gets it


def test_under_wording_matches_each_venue():
    base = {"player": "X Y", "sport": "nba", "stat": "threes", "line": 2.5, "side": "no", "ask": 0.6,
            "ask_size": 100, "fee_per": 0.01, "ref_price": 0.66, "net_edge": 0.05, "arb_net": None,
            "warnings": [], "kalshi_url": "https://kalshi.com/a", "poly_url": "https://polymarket.com/b"}
    k = format_alert(dict(base, buy_venue="kalshi"), "channel")
    p = format_alert(dict(base, buy_venue="polymarket"), "channel")
    assert "UNDER 2.5 threes" in k and "buy <b>NO</b> on “3+ threes”" in k
    assert "buy <b>No</b> on “3-pointers made O/U 2.5”" in p
