"""GitHub Actions mode: ET windows, /took via Telegram, quote dedupe, pruning, WAL checkpoint."""
from datetime import datetime

import pytest

from conftest import kalshi_contract, poly_contract
from scanner.alerts import parse_took, process_commands
from scanner.cli import ET, active_sports
from scanner.config import Settings
from scanner.db import connect
from scanner.pipeline import run_scan
from scanner.store import insert_quote, prune, upsert_contract
from test_alerts_clv import FakeKalshi, FakePoly, _mispriced_pair


def et(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


def test_active_sports_windows():
    assert active_sports(et(2026, 11, 11, 15, 59)) == []          # Wed before window
    assert active_sports(et(2026, 11, 11, 16, 0)) == ["nba"]      # Wed 4pm
    assert active_sports(et(2026, 11, 11, 23, 50)) == ["nba"]
    assert active_sports(et(2026, 11, 12, 0, 10)) == []           # after midnight
    assert active_sports(et(2026, 11, 15, 13, 0)) == ["nfl"]      # Sunday 1pm
    assert active_sports(et(2026, 11, 15, 19, 0)) == ["nba", "nfl"]
    assert active_sports(et(2026, 11, 16, 20, 0)) == ["nba", "nfl"]  # Monday night
    assert active_sports(et(2026, 11, 17, 20, 0)) == ["nba"]      # Tuesday


def test_parse_took():
    assert parse_took("/took 12 45 10") == (12, 0.45, 10.0, "")
    assert parse_took("/took 12 0.45 10 fill at open") == (12, 0.45, 10.0, "fill at open")
    assert parse_took("/took@my_props_bot 3 7 100") == (3, 0.07, 100.0, "")
    assert parse_took("/took twelve 45 10") is None


class TG:
    configured = True

    def __init__(self, updates):
        self.updates, self.sent, self.offsets = updates, [], []

    def get_updates(self, offset):
        self.offsets.append(offset)
        out = [u for u in self.updates if offset is None or u["update_id"] >= offset]
        return out

    def send(self, text):
        self.sent.append(text)
        return True


def test_took_logs_trade_only_from_my_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    s = Settings()
    conn = connect(tmp_path / "t.db")
    k, p, book = _mispriced_pair()
    run_scan(s, conn, FakeKalshi([k]), FakePoly([p], book), TG([]), ["nba"])
    opp = conn.execute("SELECT id FROM opportunities WHERE buy_venue='kalshi' ORDER BY id LIMIT 1").fetchone()[0]
    msg = lambda uid, chat, text: {"update_id": uid, "message": {"chat": {"id": chat}, "text": text}}
    tg = TG([msg(1, 999, f"/took {opp} 45 10"),          # stranger: ignored
             msg(2, 111, f"/took {opp} 45 10"),          # me: logged
             msg(3, 111, "/took 99999 45 10")])          # bad id: error reply
    assert process_commands(conn, s, tg) == 2
    rows = conn.execute("SELECT price, contracts, fee FROM trades_taken").fetchall()
    assert [tuple(r)[:2] for r in rows] == [(0.45, 10.0)]
    assert rows[0]["fee"] > 0
    assert any("Logged" in t for t in tg.sent) and any("Couldn't" in t for t in tg.sent)
    # offset persisted: a second pass doesn't re-log
    assert process_commands(conn, s, tg) == 0
    assert conn.execute("SELECT COUNT(*) FROM trades_taken").fetchone()[0] == 1


def test_quotes_only_stored_on_change(tmp_path):
    conn = connect(tmp_path / "t.db")
    k = kalshi_contract("nba", "points", "KXNBAPTS-26NOV11WASIND", "AJ Dybantsa: 20+ points", 19.5)
    cid = upsert_contract(conn, k)
    assert insert_quote(conn, cid, 0.4, 0.42, 10, 10, "list")
    assert not insert_quote(conn, cid, 0.4, 0.42, 10, 10, "list")
    assert insert_quote(conn, cid, 0.41, 0.42, 10, 10, "list")
    assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 2


def test_prune_keeps_flagged_and_drops_stale(tmp_path):
    conn = connect(tmp_path / "t.db")
    k, p, book = _mispriced_pair()
    run_scan(Settings(), conn, FakeKalshi([k]), FakePoly([p], book), TG([]), ["nba"])
    stale = kalshi_contract("nba", "points", "KXNBAPTS-26NOV01WASIND", "Old Guy: 10+ points", 9.5)
    sid = upsert_contract(conn, stale)
    insert_quote(conn, sid, 0.1, 0.2, 1, 1, "list")
    old = "2026-01-01T00:00:00+00:00"
    conn.execute("UPDATE quotes SET ts=?", (old,))
    conn.execute("UPDATE prop_contracts SET last_seen=?", (old,))
    conn.execute("UPDATE matches SET updated_at=?", (old,))
    conn.commit()
    prune(conn, keep_days=3)
    assert conn.execute("SELECT COUNT(*) FROM prop_contracts WHERE id=?", (sid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1   # backs a flag: kept
    assert conn.execute("SELECT COUNT(*) FROM quotes q JOIN matches m ON q.contract_id IN (m.kalshi_id, m.poly_id)"
                        ).fetchone()[0] > 0                                   # its quotes kept


def test_cli_checkpoints_wal(tmp_path, monkeypatch):
    """After the CLI exits, props.db alone must hold all data (Actions copies only that file)."""
    import sqlite3
    db = tmp_path / "p.db"
    monkeypatch.setenv("DB_PATH", str(db))
    from scanner import cli
    import scanner.clv as clv
    monkeypatch.setattr(clv, "snapshot_closes", lambda *a, **k: {"closes": 0})
    conn = connect(db)
    conn.execute("INSERT INTO kv(key, value) VALUES('probe', '1')")
    conn.commit()
    conn.close()
    assert cli.main(["closes"]) == 0
    assert not (tmp_path / "p.db-wal").exists() or (tmp_path / "p.db-wal").stat().st_size == 0
    copy = tmp_path / "copy.db"
    copy.write_bytes(db.read_bytes())
    assert sqlite3.connect(copy).execute("SELECT value FROM kv WHERE key='probe'").fetchone() == ("1",)
