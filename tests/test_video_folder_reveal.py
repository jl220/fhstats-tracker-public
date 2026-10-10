from pathlib import Path

from forza_tracker.ui.app import _explorer_reveal_command


def test_explorer_reveal_command_selects_current_video(tmp_path: Path) -> None:
    video = tmp_path / "2026-07-13_21-42-10_Honda-Civic_B-600.mkv"
    video.write_bytes(b"video")

    command = _explorer_reveal_command(tmp_path, video)

    assert command == ["explorer.exe", f"/select,{video}"]


def test_explorer_reveal_command_falls_back_without_selected_video(tmp_path: Path) -> None:
    assert _explorer_reveal_command(tmp_path, None) is None


def test_explorer_reveal_command_ignores_files_outside_recordings(tmp_path: Path) -> None:
    recordings = tmp_path / "recordings"
    outside = tmp_path / "outside" / "clip.mkv"
    recordings.mkdir()
    outside.parent.mkdir()
    outside.write_bytes(b"video")

    assert _explorer_reveal_command(recordings, outside) is None
