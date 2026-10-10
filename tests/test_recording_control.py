from pathlib import Path

from forza_tracker.recording.control import (
    consume_recording_command,
    read_recording_status,
    send_recording_command,
    write_recording_status,
)


def test_recording_command_is_consumed_once(tmp_path: Path) -> None:
    path = tmp_path / "command.json"

    send_recording_command("start_session", path)

    assert consume_recording_command(path)["command"] == "start_session"
    assert consume_recording_command(path) is None


def test_recording_status_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "status.json"

    write_recording_status(
        recording=True,
        mode="session",
        output_path="session.mkv",
        message="Grabando",
        path=path,
    )

    status = read_recording_status(path)
    assert status["recording"] is True
    assert status["mode"] == "session"
    assert status["output_path"] == "session.mkv"
