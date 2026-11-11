import pytest

from scanner import odds
from scanner.alerts import format_alert, sportsbook_lines


@pytest.mark.parametrize("p,want", [(0.5, "+100"), (0.45, "+122"), (0.60, "−150"), (0.75, "−300"),
                                    (1 / 3, "+200"), (0.52, "−108"), (0.20, "+400")])
def test_cents_to_american(p, want):
    assert odds.fmt_p(p) == want


@pytest.mark.parametrize("o,want", [(-108, -105), (-105, 100), (-110, -105), (-100, 105), (100, 105),
                                    (122, 125), (125, 130), (-300, -295)])
def test_next_better_pays_more(o, want):
    assert odds.next_better(o) == want
    # "pays more" check via implied probability: lower implied prob = better for the bettor
    implied = lambda x: (-x / (-x + 100)) if x < 0 else (100 / (x + 100))
    assert implied(odds.next_better(o)) < implied(o)


def test_sportsbook_section_wording():
    a = {"stat": "points", "line": 19.5, "side": "no", "ref_price": 0.48}
    s = "\n".join(sportsbook_lines(a))
    assert "Under 19.5 points" in s and "same line only" in s and "+108" in s and "+110 or better" in s
    assert sportsbook_lines(dict(a, ref_price=0.98)) == []   # silly longshot odds skipped


def test_card_shows_american_odds_for_everyone():
    a = {"player": "A B", "sport": "nba", "stat": "points", "line": 19.5, "side": "yes", "buy_venue": "kalshi",
         "ask": 0.45, "ask_size": 100, "fee_per": 0.0174, "ref_price": 0.52, "net_edge": 0.0526, "arb_net": None,
         "warnings": [], "kalshi_url": "https://kalshi.com/x", "poly_url": "https://polymarket.com/y"}
    for audience in ("owner", "channel"):
        m = format_alert(a, audience)
        assert "45¢</b> (+122)" in m and "FanDuel / Fanatics" in m and "Fair odds ≈ <b>−108</b>" in m
