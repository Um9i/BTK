from scripts.verify_rosters import pairs_at_tick, singles_at_tick, verify


def _by_triple(planets: dict[str, tuple[int, int, int]]) -> dict:
    out: dict = {}
    for ext, tri in planets.items():
        out.setdefault(tri, []).append(ext)
    return out


def test_singles_picks_exact_triple_match() -> None:
    by_triple = _by_triple({"aaa": (10, 100, 90), "bbb": (20, 200, 180)})
    assert singles_at_tick(by_triple, (20, 200, 180)) == {"bbb"}
    assert singles_at_tick(by_triple, (30, 300, 270)) == set()


def test_singles_reports_every_tied_candidate() -> None:
    # idle planets share stat triples; the tie must be surfaced, not silently picked
    by_triple = _by_triple({"aaa": (0, 380, 380), "bbb": (0, 380, 380)})
    assert singles_at_tick(by_triple, (0, 380, 380)) == {"aaa", "bbb"}


def test_pairs_finds_complementary_planets() -> None:
    by_triple = _by_triple({"aaa": (10, 100, 90), "bbb": (20, 200, 180), "ccc": (5, 50, 45)})
    assert pairs_at_tick(by_triple, (30, 300, 270)) == {("aaa", "bbb")}


def test_pairs_handles_two_stat_identical_planets() -> None:
    # the partner of a planet can be its own twin -- needs two distinct planets
    by_triple = _by_triple({"aaa": (0, 380, 380), "bbb": (0, 380, 380)})
    assert pairs_at_tick(by_triple, (0, 760, 760)) == {("aaa", "bbb")}


def test_pairs_needs_two_planets_not_one_counted_twice() -> None:
    by_triple = _by_triple({"aaa": (0, 380, 380)})
    assert pairs_at_tick(by_triple, (0, 760, 760)) == set()


def _data(by_tick: dict, history: dict) -> dict:
    return {"by_tick": by_tick, "history": history, "round_number": 1, "round_id": 1}


def test_verify_intersects_across_ticks_to_break_a_tie() -> None:
    # at tick 1 two planets tie; at tick 2 they diverge, so only one survives both
    by_tick = {
        1: _by_triple({"aaa": (5, 50, 45), "bbb": (5, 50, 45)}),
        2: _by_triple({"aaa": (6, 60, 55), "bbb": (7, 70, 65)}),
    }
    history = {
        "Solo": [
            {"tick": 1, "members": 1, "target": (5, 50, 45)},
            {"tick": 2, "members": 1, "target": (6, 60, 55)},
        ]
    }
    (result,) = verify(_data(by_tick, history), 1)
    assert result["surviving"] == {"aaa"}
    assert result["n_ticks"] == 2
    assert result["widest"] == 2  # the tick-1 tie is still reported


def test_verify_reports_contradiction_as_empty() -> None:
    by_tick = {
        1: _by_triple({"aaa": (5, 50, 45)}),
        2: _by_triple({"aaa": (6, 60, 55)}),
    }
    history = {
        "Solo": [
            {"tick": 1, "members": 1, "target": (5, 50, 45)},
            {"tick": 2, "members": 1, "target": (99, 99, 99)},
        ]
    }
    (result,) = verify(_data(by_tick, history), 1)
    assert result["surviving"] == set()


def test_verify_ignores_ticks_with_a_different_member_count() -> None:
    # an alliance that grew must only be verified over ticks matching the size
    by_tick = {
        1: _by_triple({"aaa": (5, 50, 45)}),
        2: _by_triple({"aaa": (5, 50, 45), "bbb": (1, 10, 9)}),
    }
    history = {
        "Grew": [
            {"tick": 1, "members": 1, "target": (5, 50, 45)},
            {"tick": 2, "members": 2, "target": (6, 60, 54)},
        ]
    }
    (result,) = verify(_data(by_tick, history), 1)
    assert result["n_ticks"] == 1
    assert result["surviving"] == {"aaa"}


def test_verify_skips_ticks_absent_from_the_planet_snapshot() -> None:
    # guards the snapshot-skew trap: an alliance tick with no matching planet
    # tick must be ignored, not treated as a contradiction
    by_tick = {1: _by_triple({"aaa": (5, 50, 45)})}
    history = {
        "Solo": [
            {"tick": 1, "members": 1, "target": (5, 50, 45)},
            {"tick": 2, "members": 1, "target": (6, 60, 55)},
        ]
    }
    (result,) = verify(_data(by_tick, history), 1)
    assert result["n_ticks"] == 1
    assert result["surviving"] == {"aaa"}


def test_verify_skips_alliances_that_no_longer_exist() -> None:
    # a defunct alliance's members are usually in a current one too, and
    # planet_intel holds one alliance per planet -- so writing the old roster
    # would clobber the live one
    by_tick = {
        1: _by_triple({"aaa": (5, 50, 45)}),
        2: _by_triple({"aaa": (6, 60, 55)}),
    }
    history = {
        "Defunct": [{"tick": 1, "members": 1, "target": (5, 50, 45)}],
        "Current": [{"tick": 2, "members": 1, "target": (6, 60, 55)}],
    }
    names = [r["alliance"] for r in verify(_data(by_tick, history), 1)]
    assert names == ["Current"]

    both = [r["alliance"] for r in verify(_data(by_tick, history), 1, historical=True)]
    assert sorted(both) == ["Current", "Defunct"]
