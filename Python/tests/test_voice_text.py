from datetime import datetime

from voice.shape_context import (money_to_words, rank_nearby, sanitize_for_voice, spoken_clock,
                                 spoken_hours, spoken_med, spoken_strength)


def _place(pid, dist, is_open, price, close="2026-11-07T23:00:00Z", h24=False):
    close_ev = {"type": "24hr"} if h24 else {"type": "close", "datetime": close}
    return {"id": pid, "name": pid, "distance": dist, "isOpen": is_open, "price": price,
            "nextEvents": {"open": {"type": "open", "datetime": "2026-11-08T14:00:00Z"}, "close": close_ev}}


def test_rank_nearby_balances_open_price_and_hours():
    ranked = rank_nearby([
        _place("closed_closest", 0.1, False, 5.0),
        _place("open_near", 0.3, True, 15.0),
        _place("open_cheap", 0.9, True, 9.0),
        _place("cheap_but_far", 3.0, True, 1.0),
        _place("all_night", 1.4, True, 30.0, h24=True),
    ])
    assert [(p["id"], p.get("why")) for p in ranked[:3]] == [
        ("open_near", "the closest of the ones open now"),
        ("open_cheap", "the cheapest of the ones open now"),
        ("all_night", "the latest-closing of the ones open now"),
    ]
    assert ranked[3]["id"] == "closed_closest"


def test_one_store_winning_everything_reads_cleanly():
    ranked = rank_nearby([_place("a", 0.2, True, 5.0, h24=True), _place("b", 0.5, True, 9.0)])
    assert ranked[0]["why"] == "the closest and cheapest and latest-closing of the ones open now"
    assert "why" not in ranked[1]


def test_nothing_open_falls_back_to_closest():
    ranked = rank_nearby([_place("b", 0.5, False, 5.0), _place("a", 0.1, False, 5.0)])
    assert [p["id"] for p in ranked] == ["a", "b"] and "why" not in ranked[0]


def test_closed_store_says_when_it_opens():
    assert spoken_hours(_place("x", 0.1, False, 5.0)) == "closed now, opens nine in the morning"

REAL_FAQ = ("If the prescription has been confirmed, you need to contact Photon Support directly "
            "via text at [513-866-3212](sms:513-866-3212) for assistance.")


def test_faq_markdown_link_becomes_spoken_digits():
    out = sanitize_for_voice(REAL_FAQ)
    assert "[" not in out and "sms:" not in out
    assert "five one three, eight six six, three two one two" in out


def test_urls_money_and_markup():
    assert sanitize_for_voice("Visit https://orders.neutron.health/?token=x") == "Visit orders dot neutron dot health"
    assert sanitize_for_voice("**about $3.76**") == "about three dollars seventy-six"
    assert sanitize_for_voice("- one\n- two") == "one two"


def test_expressive_tags_survive():
    assert sanitize_for_voice('<emotion value="calm"/>Hi there') == '<emotion value="calm"/>Hi there'


def test_money_words():
    assert money_to_words(12.04) == "twelve dollars four"
    assert money_to_words(1) == "one dollar"
    assert money_to_words(129.99) == "one hundred twenty-nine dollars ninety-nine"


def test_clock_and_strength():
    assert spoken_clock(datetime(2026, 1, 1, 17, 0)) == "five tonight"
    assert spoken_clock(datetime(2026, 1, 1, 9, 30)) == "nine thirty in the morning"
    assert spoken_strength("Azithromycin 100 MG/5ML") == "Azithromycin 100 milligrams per 5 milliliters"


def test_spoken_med_strips_catalog_noise():
    assert spoken_med("metFORMIN HCl ER (MOD) Oral Tablet Extended Release 24 Hour 1000 MG") == (
        "metformin extended-release, one thousand milligrams, tablets", "metformin")
    assert spoken_med("Azithromycin Oral Suspension Reconstituted 100 MG/5ML") == (
        "azithromycin, one hundred milligrams per five milliliters, suspension", "azithromycin")
    assert spoken_med(None) == ("your prescription", "prescription")
