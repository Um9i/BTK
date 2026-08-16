from scripts.find_joiners import classify, gap_events


def _rows(*triples: tuple[int, int, int]) -> list[dict]:
    return [{"tick": t, "members": m, "gap": g} for t, m, g in triples]


def test_gap_events_ignores_a_constant_gap() -> None:
    # a flat gap means nobody visibly joined or left, however long the span
    rows = _rows((1, 5, 1000), (2, 5, 1000), (3, 5, 1000))
    assert gap_events(rows) == []


def test_gap_events_reports_the_rise_and_the_prior_tick() -> None:
    # the prior tick matters: carried-in score is the joiner's score at T-1
    rows = _rows((1, 5, 1000), (2, 6, 1500))
    (ev,) = gap_events(rows)
    assert ev["tick"] == 2
    assert ev["prev_tick"] == 1
    assert ev["delta"] == 500
    assert classify(ev) == "join"


def test_gap_fall_with_shrinking_membership_is_a_leave() -> None:
    rows = _rows((1, 6, 1500), (2, 5, 1000))
    (ev,) = gap_events(rows)
    assert ev["delta"] == -500
    assert classify(ev) == "leave"


def test_gap_rise_without_a_headcount_change_is_a_swap() -> None:
    # the case a member count alone cannot see
    rows = _rows((1, 8, 1000), (2, 8, 1200))
    (ev,) = gap_events(rows)
    assert classify(ev) == "join+leave (count unchanged)"


def test_multiple_events_are_each_reported() -> None:
    rows = _rows((1, 5, 0), (2, 6, 300), (3, 6, 300), (4, 7, 900))
    evs = gap_events(rows)
    assert [e["tick"] for e in evs] == [2, 4]
    assert [e["delta"] for e in evs] == [300, 600]
    assert [e["prev_tick"] for e in evs] == [1, 3]


def test_zero_carry_join_is_invisible() -> None:
    # documents the known blind spot: a member joining with no score moves
    # neither the gap nor (on a swap) the headcount
    rows = _rows((1, 5, 1000), (2, 5, 1000))
    assert gap_events(rows) == []
