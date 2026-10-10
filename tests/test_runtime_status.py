from __future__ import annotations

from forza_tracker.runtime_status import read_status, sanitize_error_message, write_status


def test_runtime_status_is_written_and_updated(tmp_path, monkeypatch) -> None:
    status_path = tmp_path / "capture_status.json"
    monkeypatch.setattr("forza_tracker.runtime_status.STATUS_PATH", status_path)

    write_status("running", frames_captured=1)
    write_status("running", frames_captured=2, detections=1)

    status = read_status(status_path)
    assert status["state"] == "running"
    assert status["frames_captured"] == 2
    assert status["detections"] == 1
    assert status["pid"] > 0
    assert status["updated_at"]


def test_error_message_removes_local_paths() -> None:
    message = sanitize_error_message(
        r"Permission denied: C:\Users\Tester\Desktop\FHStats\captures\screen.png"
    )

    assert "Tester" not in message
    assert "[local-path]" in message
