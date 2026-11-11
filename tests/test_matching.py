"""QC #3: >=20 known pairs match 100%; >=10 known non-pairs auto-match 0%."""
from pathlib import Path

from conftest import kalshi_contract, poly_contract
from scanner.matching import load_alias_csv, match_contracts
from scanner.normalize import norm_name, parse_kalshi_event, parse_poly_event, split_teams

ALIASES = load_alias_csv(Path(__file__).parent.parent / "data" / "player_aliases.csv")


def auto(results):
    return {(r.kalshi.market_id, r.poly.market_id) for r in results if r.status == "auto"}


def test_known_pairs_match_100pct(real_pairs):
    assert len(real_pairs) >= 20
    assert all(k and p for k, p in real_pairs), "fixture failed to parse"
    ks, ps = [k for k, _ in real_pairs], [p for _, p in real_pairs]
    got = auto(match_contracts(ks, ps, ALIASES))
    want = {(k.market_id, p.market_id) for k, p in real_pairs}
    assert got == want


def _nonpairs():
    ev_k, ev_p = "KXNBAPTS-26NOV11WASIND", "nba-was-ind-2026-11-11"
    base_k = lambda title="AJ Dybantsa: 20+ points", floor=19.5, ev=ev_k, stat="points", **kw: \
        kalshi_contract("nba", stat, ev, title, floor, **kw)
    base_p = lambda q="AJ Dybantsa: Points O/U 19.5", line=19.5, ev=ev_p, typ="points": \
        poly_contract("nba", typ, ev, q, line)
    return [
        ("different threshold", base_k(), base_p("AJ Dybantsa: Points O/U 20.5", 20.5)),
        ("different threshold (below)", base_k(), base_p("AJ Dybantsa: Points O/U 18.5", 18.5)),
        ("different stat", base_k(), base_p("AJ Dybantsa: Rebounds O/U 19.5", 19.5, typ="rebounds")),
        ("different stat threes vs points", base_k("AJ Dybantsa: 1+ points", 0.5),
         base_p("AJ Dybantsa: 3-Pointers Made O/U 0.5", 0.5, typ="threes")),
        ("different player same game", base_k(), base_p("Trae Young: Points O/U 19.5", 19.5)),
        ("similar name, different player", base_k("Jalen Williams: 20+ points", 19.5, ev="KXNBAPTS-26NOV11OKCPOR"),
         base_p("Jaylin Williams: Points O/U 19.5", 19.5, ev="nba-okc-por-2026-11-11")),
        ("same last name, different first", base_k("Jalen Johnson: 20+ points", 19.5, ev="KXNBAPTS-26NOV11ATLCHI"),
         base_p("Keldon Johnson: Points O/U 19.5", 19.5, ev="nba-atl-chi-2026-11-11")),
        ("different date", base_k(), base_p(ev="nba-was-ind-2026-11-13")),
        ("different game", base_k(), base_p(ev="nba-was-det-2026-11-11")),
        ("kalshi non-'greater' strike", base_k(strike_type="less"), base_p()),
        ("title/strike disagree", base_k("AJ Dybantsa: 25+ points", 19.5), base_p()),
        ("poly question/line disagree", base_k(), base_p("AJ Dybantsa: Points O/U 20.5", 19.5)),
    ]


def test_known_nonpairs_match_0pct():
    cases = _nonpairs()
    assert len(cases) >= 10
    for label, k, p in cases:
        if k is None or p is None:
            continue  # parser rejected it: that's a correct non-match
        assert not auto(match_contracts([k], [p], ALIASES)), label


def test_similar_names_go_to_review_not_auto():
    k = kalshi_contract("nba", "points", "KXNBAPTS-26NOV11WASIND", "Alex Sarr: 10+ points", 9.5)
    p = poly_contract("nba", "points", "nba-was-ind-2026-11-11", "Alexandre Sarr: Points O/U 9.5", 9.5)
    res = match_contracts([k], [p], ALIASES)
    assert [r.status for r in res] == ["review"]
    assert res[0].confidence < 0.99


def test_rejected_pair_stays_rejected():
    k = kalshi_contract("nba", "points", "KXNBAPTS-26NOV11WASIND", "Alex Sarr: 10+ points", 9.5)
    p = poly_contract("nba", "points", "nba-was-ind-2026-11-11", "Alexandre Sarr: Points O/U 9.5", 9.5)
    assert match_contracts([k], [p], ALIASES, rejected={(k.market_id, p.market_id)}) == []


def test_ambiguous_duplicate_never_auto():
    k = kalshi_contract("nba", "points", "KXNBAPTS-26NOV11WASIND", "Tre Johnson: 10+ points", 9.5)
    p1 = poly_contract("nba", "points", "nba-was-ind-2026-11-11", "Tre Johnson: Points O/U 9.5", 9.5)
    p2 = poly_contract("nba", "points", "nba-was-ind-2026-11-11", "Tre Johnson III: Points O/U 9.5", 9.5)
    assert not auto(match_contracts([k], [p1, p2], ALIASES))


def test_alias_and_normalization():
    assert norm_name("Alperen Sengün") == norm_name("Alperen Sengun")
    assert norm_name("Mikel Brown Jr.") == "mikel brown"
    assert norm_name("P.J. Washington") == norm_name("PJ Washington")
    assert norm_name("De'Aaron Fox") == "deaaron fox"
    k = kalshi_contract("nba", "points", "KXNBAPTS-26NOV11SASCHI", "Nic Claxton: 15+ points", 14.5)
    p = poly_contract("nba", "points", "nba-sas-chi-2026-11-11", "Nicolas Claxton: Points O/U 14.5", 14.5)
    assert [r.method for r in match_contracts([k], [p], ALIASES)] == ["alias"]


def test_game_keys_line_up():
    assert parse_kalshi_event("nba", "KXNBAPTS-26NOV11WASIND") == parse_poly_event("nba-was-ind-2026-11-11")
    assert parse_kalshi_event("nfl", "KXNFLRECYDS-26NOV15KCATL") == "nfl:2026-11-15:ATL-KC"
    assert split_teams("nfl", "NONYG") == ("NO", "NYG")
    assert split_teams("nba", "LACLAL") == ("LAC", "LAL")
