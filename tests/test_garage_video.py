from __future__ import annotations

from pathlib import Path

from forza_tracker.garage.video import copy_finalized_recording


def test_copy_finalized_recording_waits_for_temporary_mux_files(tmp_path: Path) -> None:
    source = tmp_path / "scan.mkv"
    source.write_bytes(b"partial")
    temporary_video = tmp_path / ".scan.video.mkv"
    temporary_audio = tmp_path / ".scan.audio.wav"
    temporary_video.write_bytes(b"video")
    temporary_audio.write_bytes(b"audio")
    destination = tmp_path / "snapshot" / "video" / "garage.mkv"
    sleep_calls: list[float] = []

    def finish_mux(seconds: float) -> None:
        sleep_calls.append(seconds)
        temporary_video.unlink()
        temporary_audio.unlink()
        source.write_bytes(b"complete-video")

    copied = copy_finalized_recording(
        source,
        destination,
        status_reader=lambda: {"recording": False},
        sleep=finish_mux,
        monotonic=lambda: 0.0,
        timeout=1.0,
        poll_interval=0.25,
    )

    assert copied == destination
    assert destination.read_bytes() == b"complete-video"
    assert source.read_bytes() == b"complete-video"
    assert sleep_calls == [0.25]
