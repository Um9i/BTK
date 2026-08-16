from scripts.verify_rosters import FUND_MAX_VALUE, pairs_at_tick, singles_at_tick, verify


def _by_key(planets: dict[str, tuple[int, int, int]]) -> dict:
    """planets: ext -> (size, xp, value), indexed as the script indexes them."""
    out: dict = {}
    for ext, (size, xp, value) in planets.items():
        out.setdefault((size, xp), []).append((ext, value))
    return out


def _target(size: int, xp: int, value_cap: int) -> dict:
    return {"size": size, "xp": xp, "value_cap": value_cap}


def test_singles_matches_on_size_and_xp() -> None:
    by_key = _by_key({"aaa": (10, 5, 90), "bbb": (20, 7, 180)})
    assert singles_at_tick(by_key, _target(20, 7, 180)) == {"bbb"}
    assert singles_at_tick(by_key, _target(30, 9, 270)) == set()


def test_singles_accepts_a_non_zero_alliance_fund() -> None:
    # total_value exceeds the member's own value because the alliance fund
    # holds resources; the old (size, score, value) triple match missed this
    by_key = _by_key({"aaa": (20, 7, 180)})
    assert singles_at_tick(by_key, _target(20, 7, 180 + 12_345)) == {"aaa"}


def test_singles_rejects_an_impossible_fund() -> None:
    by_key = _by_key({"aaa": (20, 7, 180)})
    # negative fund: members hold more value than the alliance reports
    assert singles_at_tick(by_key, _target(20, 7, 100)) == set()
    # fund above the 75,000,000-resource cap
    assert singles_at_tick(by_key, _target(20, 7, 180 + FUND_MAX_VALUE + 1)) == set()


def test_singles_reports_every_tied_candidate() -> None:
    # idle planets share stats; the tie must be surfaced, not silently picked
    by_key = _by_key({"aaa": (0, 0, 380), "bbb": (0, 0, 380)})
    assert singles_at_tick(by_key, _target(0, 0, 380)) == {"aaa", "bbb"}


def test_pairs_finds_complementary_planets() -> None:
    by_key = _by_key({"aaa": (10, 5, 90), "bbb": (20, 7, 180), "ccc": (5, 1, 45)})
    assert pairs_at_tick(by_key, _target(30, 12, 270)) == {("aaa", "bbb")}


def test_pairs_accepts_a_non_zero_alliance_fund() -> None:
    by_key = _by_key({"aaa": (10, 5, 90), "bbb": (20, 7, 180)})
    assert pairs_at_tick(by_key, _target(30, 12, 270 + 50_000)) == {("aaa", "bbb")}


def test_pairs_rejects_an_impossible_fund() -> None:
    by_key = _by_key({"aaa": (10, 5, 90), "bbb": (20, 7, 180)})
    assert pairs_at_tick(by_key, _target(30, 12, 269)) == set()


def test_pairs_handles_two_stat_identical_planets() -> None:
    # the partner of a planet can be its own twin -- needs two distinct planets
    by_key = _by_key({"aaa": (0, 0, 380), "bbb": (0, 0, 380)})
    assert pairs_at_tick(by_key, _target(0, 0, 760)) == {("aaa", "bbb")}


def test_pairs_needs_two_planets_not_one_counted_twice() -> None:
    by_key = _by_key({"aaa": (0, 0, 380)})
    assert pairs_at_tick(by_key, _target(0, 0, 760)) == set()


def _data(by_tick: dict, history: dict) -> dict:
    return {"by_tick": by_tick, "history": history, "round_number": 1, "round_id": 1}


def _row(tick: int, members: int, size: int, xp: int, value_cap: int) -> dict:
    return {"tick": tick, "members": members, "size": size, "xp": xp, "value_cap": value_cap}


def test_verify_intersects_across_ticks_to_break_a_tie() -> None:
    # at tick 1 two planets tie; at tick 2 they diverge, so only one survives both
    by_tick = {
        1: _by_key({"aaa": (5, 1, 45), "bbb": (5, 1, 45)}),
        2: _by_key({"aaa": (6, 2, 55), "bbb": (7, 3, 65)}),
    }
    history = {"Solo": [_row(1, 1, 5, 1, 45), _row(2, 1, 6, 2, 55)]}
    (result,) = verify(_data(by_tick, history), 1)
    assert result["surviving"] == {"aaa"}
    assert result["n_ticks"] == 2
    assert result["widest"] == 2  # the tick-1 tie is still reported


def test_verify_tracks_a_fund_that_changes_between_ticks() -> None:
    # the fund varies tick to tick; the member is still identified at both
    by_tick = {
        1: _by_key({"aaa": (5, 1, 45)}),
        2: _by_key({"aaa": (6, 2, 55)}),
    }
    history = {"Solo": [_row(1, 1, 5, 1, 45 + 1_000), _row(2, 1, 6, 2, 55 + 9_999)]}
    (result,) = verify(_data(by_tick, history), 1)
    assert result["surviving"] == {"aaa"}


def test_verify_reports_contradiction_as_empty() -> None:
    by_tick = {1: _by_key({"aaa": (5, 1, 45)}), 2: _by_key({"aaa": (6, 2, 55)})}
    history = {"Solo": [_row(1, 1, 5, 1, 45), _row(2, 1, 99, 99, 99)]}
    (result,) = verify(_data(by_tick, history), 1)
    assert result["surviving"] == set()


def test_verify_ignores_ticks_with_a_different_member_count() -> None:
    by_tick = {
        1: _by_key({"aaa": (5, 1, 45)}),
        2: _by_key({"aaa": (5, 1, 45), "bbb": (1, 0, 9)}),
    }
    history = {"Grew": [_row(1, 1, 5, 1, 45), _row(2, 2, 6, 1, 54)]}
    (result,) = verify(_data(by_tick, history), 1)
    assert result["n_ticks"] == 1
    assert result["surviving"] == {"aaa"}


def test_verify_skips_ticks_absent_from_the_planet_snapshot() -> None:
    # guards the snapshot-skew trap: an alliance tick with no matching planet
    # tick must be ignored, not treated as a contradiction
    by_tick = {1: _by_key({"aaa": (5, 1, 45)})}
    history = {"Solo": [_row(1, 1, 5, 1, 45), _row(2, 1, 6, 2, 55)]}
    (result,) = verify(_data(by_tick, history), 1)
    assert result["n_ticks"] == 1
    assert result["surviving"] == {"aaa"}


def test_verify_skips_alliances_that_no_longer_exist() -> None:
    # a defunct alliance's members are usually in a current one too, and
    # planet_intel holds one alliance per planet -- so writing the old roster
    # would clobber the live one
    by_tick = {1: _by_key({"aaa": (5, 1, 45)}), 2: _by_key({"aaa": (6, 2, 55)})}
    history = {
        "Defunct": [_row(1, 1, 5, 1, 45)],
        "Current": [_row(2, 1, 6, 2, 55)],
    }
    names = [r["alliance"] for r in verify(_data(by_tick, history), 1)]
    assert names == ["Current"]

    both = [r["alliance"] for r in verify(_data(by_tick, history), 1, historical=True)]
    assert sorted(both) == ["Current", "Defunct"]
