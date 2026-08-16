from ortools.sat.python import cp_model

from scripts.solve_roster import build_model, solve


def _data(
    stats: dict[str, dict[int, tuple[int, int, int]]],
    history: list[dict],
    claimed_elsewhere: set[str] | None = None,
) -> dict:
    return {"stats": stats, "history": history, "claimed_elsewhere": claimed_elsewhere or set()}


def _row(tick: int, members: int, size: int, xp: int, value_cap: int) -> dict:
    return {"tick": tick, "members": members, "size": size, "xp": xp, "value_cap": value_cap}


def _solved(model, x, cands) -> set[str]:
    solver, status = solve(model, time_limit=10.0)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    return {c for c in cands if solver.Value(x[c])}


def test_build_model_finds_the_unique_two_planet_subset() -> None:
    stats = {
        "aaa": {1: (5, 1, 45)},
        "bbb": {1: (7, 2, 65)},
        "ccc": {1: (100, 100, 100)},  # decoy, doesn't fit any combination
    }
    history = [_row(1, 2, 12, 3, 110)]
    model, x, _, cands = build_model(_data(stats, history), force_in=set(), exclude_claimed=True)
    assert _solved(model, x, cands) == {"aaa", "bbb"}


def test_build_model_accepts_a_non_zero_alliance_fund() -> None:
    stats = {"aaa": {1: (5, 1, 45)}}
    history = [_row(1, 1, 5, 1, 45 + 12_345)]
    model, x, _, cands = build_model(_data(stats, history), force_in=set(), exclude_claimed=True)
    assert _solved(model, x, cands) == {"aaa"}


def test_build_model_excludes_planets_claimed_by_another_alliance() -> None:
    # only "bbb" completes the sum with "aaa", but it's already confirmed
    # elsewhere -- the closure property says it can't also be here
    stats = {
        "aaa": {1: (5, 1, 45)},
        "bbb": {1: (7, 2, 65)},
    }
    history = [_row(1, 2, 12, 3, 110)]
    model, _x, _, cands = build_model(
        _data(stats, history, claimed_elsewhere={"bbb"}), force_in=set(), exclude_claimed=True
    )
    assert "bbb" not in cands
    _solver, status = solve(model, time_limit=10.0)
    assert status == cp_model.INFEASIBLE


def test_no_exclude_claimed_lets_a_claimed_planet_back_in() -> None:
    stats = {
        "aaa": {1: (5, 1, 45)},
        "bbb": {1: (7, 2, 65)},
    }
    history = [_row(1, 2, 12, 3, 110)]
    model, x, _, cands = build_model(
        _data(stats, history, claimed_elsewhere={"bbb"}), force_in=set(), exclude_claimed=False
    )
    assert "bbb" in cands
    assert _solved(model, x, cands) == {"aaa", "bbb"}


def test_force_in_requires_a_specific_planet_in_the_solution() -> None:
    # two disjoint pairs both sum correctly; force-in must pick the one asked for
    stats = {
        "aaa": {1: (5, 1, 45)},
        "bbb": {1: (7, 2, 65)},
        "ccc": {1: (5, 1, 45)},
        "ddd": {1: (7, 2, 65)},
    }
    history = [_row(1, 2, 12, 3, 110)]
    model, x, _, cands = build_model(_data(stats, history), force_in={"ccc"}, exclude_claimed=True)
    solution = _solved(model, x, cands)
    assert "ccc" in solution
    assert "ddd" in solution


def test_build_model_rejects_a_window_with_changing_membership() -> None:
    stats = {"aaa": {1: (5, 1, 45), 2: (5, 1, 45)}}
    history = [_row(1, 1, 5, 1, 45), _row(2, 2, 5, 1, 45)]
    try:
        build_model(_data(stats, history), force_in=set(), exclude_claimed=True)
    except SystemExit as e:
        assert "member count is not constant" in str(e)
    else:
        raise AssertionError("expected SystemExit")


def test_multiple_ticks_narrow_a_single_tick_ambiguity() -> None:
    # at tick 1 two disjoint pairs both sum correctly; tick 2 only one still does
    stats = {
        "aaa": {1: (5, 1, 45), 2: (6, 2, 55)},
        "bbb": {1: (7, 2, 65), 2: (7, 2, 65)},
        "ccc": {1: (5, 1, 45), 2: (5, 1, 45)},
        "ddd": {1: (7, 2, 65), 2: (9, 5, 95)},
    }
    history = [_row(1, 2, 12, 3, 110), _row(2, 2, 13, 4, 120)]
    model, x, _, cands = build_model(_data(stats, history), force_in=set(), exclude_claimed=True)
    assert _solved(model, x, cands) == {"aaa", "bbb"}
