from pathlib import Path

from btk.dumps.status import parse_game_status

FIXTURE = Path(__file__).parent / "fixtures" / "game_status.html"


def test_parse_game_status() -> None:
    status = parse_game_status(FIXTURE.read_text())
    assert status.round_number == 118
    assert status.round_name == "Lost SoulS"
    assert status.current_tick == 0
    assert status.tick_speed == "1 hour"
    assert status.ticking is False
    assert status.game_open is True
    assert status.signups_open is True
