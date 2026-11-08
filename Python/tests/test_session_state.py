import pytest

from voice.session_state import Candidate, ResolveError, SessionState


def _state() -> SessionState:
    s = SessionState(order_id="ord_1", patient_id="pat_1", patient_token="t", org_name="NYU")
    s.add([
        Candidate(pharmacy_id="p1", name="Avenue O Pharmacy", source="nearby", street="26 AVENUE O"),
        Candidate(pharmacy_id="p2", name="Sky Pharmacy", source="nearby"),
        Candidate(pharmacy_id="p3", name="CVS Pharmacy", source="nearby", street="1 KINGS HWY"),
        Candidate(pharmacy_id="p4", name="Amazon Pharmacy", source="mailorder"),
    ], spoken=["p1", "p2", "p3"])
    return s


@pytest.mark.parametrize("ref,expected", [
    ("the first one", "p1"), ("option two", "p2"), ("the third", "p3"), ("the last one", "p3"),
])
def test_ordinals_use_last_spoken_options(ref, expected):
    assert _state().resolve(ref).pharmacy_id == expected


@pytest.mark.parametrize("ref,expected", [
    ("Avenue O Pharmacy", "p1"), ("sky", "p2"), ("amazon", "p4"), ("Avenue Oh pharmacy", "p1"),
])
def test_exact_and_fuzzy_names(ref, expected):
    assert _state().resolve(ref).pharmacy_id == expected


def test_new_search_keeps_old_pharmacies_but_moves_ordinals():
    s = _state()
    s.add([Candidate(pharmacy_id="p9", name="Walgreens", source="nearby")], spoken=["p9"])
    assert s.resolve("option one").pharmacy_id == "p9"
    assert s.resolve("Sky Pharmacy").pharmacy_id == "p2"


def test_two_cvs_locations_are_ambiguous():
    s = _state()
    s.add([Candidate(pharmacy_id="p5", name="CVS Pharmacy", source="nearby", street="9 OCEAN PKWY")])
    with pytest.raises(ResolveError, match="KINGS HWY.*OCEAN PKWY|OCEAN PKWY.*KINGS HWY"):
        s.resolve("CVS")


def test_unknown_name_not_found():
    with pytest.raises(ResolveError, match="Couldn't find"):
        _state().resolve("Duane Reade")


def test_current_pharmacy_stays_current_after_merge():
    s = _state()
    s.add([Candidate(pharmacy_id="p7", name="Optumrx", source="current")])
    s.add([Candidate(pharmacy_id="p7", name="Optumrx", source="nearby")])
    assert s.candidates["p7"].source == "current"
