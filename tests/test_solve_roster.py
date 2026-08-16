from ortools.sat.python import cp_model

from scripts.solve_roster import (
    build_model,
    extend_check,
    find_idle_candidates,
    find_idle_members,
    find_single_change,
    reconcile,
    solve,
)


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


def test_build_model_extra_exclude_removes_idle_candidates_only() -> None:
    # "bbb" is not claimed elsewhere, but a caller (the idle-member re-solve) can still
    # exclude it directly without touching the claimed-elsewhere exclusion
    stats = {
        "aaa": {1: (5, 1, 45)},
        "bbb": {1: (7, 2, 65)},
    }
    history = [_row(1, 2, 12, 3, 110)]
    model, _x, _, cands = build_model(
        _data(stats, history), force_in=set(), exclude_claimed=True, extra_exclude={"bbb"}
    )
    assert "bbb" not in cands
    _solver, status = solve(model, time_limit=10.0)
    assert status == cp_model.INFEASIBLE


def test_find_idle_members_flags_size_zero_xp_zero_for_the_whole_window() -> None:
    # "aaa" is idle at every tick in [1, 2]; "bbb" is idle at tick 1 but active by tick 2
    data = _data(
        {
            "aaa": {1: (0, 0, 100), 2: (0, 0, 120)},
            "bbb": {1: (0, 0, 50), 2: (3, 1, 60)},
        },
        [],
    )
    assert find_idle_members(data, {"aaa", "bbb"}, 1, 2) == {"aaa"}


def test_find_idle_candidates_scans_the_whole_pool_not_just_the_roster() -> None:
    data = _data(
        {
            "aaa": {1: (0, 0, 100)},
            "bbb": {1: (5, 2, 200)},
            "ccc": {1: (0, 0, 300)},
        },
        [],
    )
    assert find_idle_candidates(data, 1, 1) == {"aaa", "ccc"}
    assert find_idle_candidates(data, 1, 1, exclude={"ccc"}) == {"aaa"}


def test_reconcile_checks_member_count_size_xp_and_fund_bound() -> None:
    stats = {"aaa": {1: (5, 1, 45)}, "bbb": {1: (7, 2, 65)}}
    target = _row(1, 2, 12, 3, 110)
    assert reconcile(stats, {"aaa", "bbb"}, target) == (True, 0)
    # wrong member count
    assert reconcile(stats, {"aaa"}, target) == (False, None)
    # a planet missing at this tick
    assert reconcile(stats, {"aaa", "ccc"}, _row(1, 2, 5, 1, 45)) == (False, None)


def test_reconcile_allows_a_bounded_fund_not_an_exact_match() -> None:
    stats = {"aaa": {1: (5, 1, 45)}}
    ok, fund = reconcile(stats, {"aaa"}, _row(1, 1, 5, 1, 45 + 9_999))
    assert ok
    assert fund == 9_999


def test_extend_check_reports_every_tick_ok_or_not() -> None:
    full = {
        "stats": {"aaa": {1: (5, 1, 45), 2: (6, 2, 55), 3: (6, 2, 55)}},
        "history": [_row(1, 1, 5, 1, 45), _row(2, 1, 6, 2, 55), _row(3, 1, 99, 99, 99)],
    }
    results = extend_check(full, {"aaa"})
    assert [r["ok"] for r in results] == [True, True, False]
    assert [r["tick"] for r in results] == [1, 2, 3]


def test_find_single_change_identifies_a_leaver() -> None:
    # roster of 2 fits ticks with members=2; at tick 3 the alliance dropped to 1 member
    full = {
        "stats": {
            "aaa": {1: (5, 1, 45), 2: (5, 1, 45), 3: (5, 1, 45)},
            "bbb": {1: (7, 2, 65), 2: (7, 2, 65)},
        },
        "history": [_row(3, 1, 5, 1, 45)],
    }
    changed = find_single_change(full, {"aaa", "bbb"}, tick=3)
    assert changed == {"bbb"}


def test_find_single_change_identifies_a_joiner() -> None:
    full = {
        "stats": {
            "aaa": {1: (5, 1, 45)},
            "bbb": {1: (7, 2, 65)},
        },
        "history": [_row(1, 2, 12, 3, 110)],
    }
    changed = find_single_change(full, {"aaa"}, tick=1)
    assert changed == {"bbb"}


def test_find_single_change_returns_empty_for_a_bigger_jump() -> None:
    # member count moved by 2, not 1 -- not explainable by a single-planet change
    full = {"stats": {"aaa": {1: (5, 1, 45)}}, "history": [_row(1, 3, 5, 1, 45)]}
    assert find_single_change(full, {"aaa"}, tick=1) == set()
