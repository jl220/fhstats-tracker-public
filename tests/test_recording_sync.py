from __future__ import annotations

import subprocess
from pathlib import Path

from forza_tracker.config.settings import RecordingSettings
from forza_tracker.recording.recorder import MatchRecorder, RecordingSession


class _Result:
    returncode = 0
    stderr = ""


def test_finalize_delays_video_when_audio_started_first(tmp_path, monkeypatch) -> None:
    video = tmp_path / "race.video.mkv"
    audio = tmp_path / "race.audio.wav"
    output = tmp_path / "race.mkv"
    video.write_bytes(b"video")
    audio.write_bytes(b"0" * 128)
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        output.write_bytes(b"muxed")
        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)

    ffmpeg = tmp_path / "ffmpeg.exe"
    settings = RecordingSettings(
        ffmpeg_path=ffmpeg,
        output_dir=tmp_path,
        video_sync_offset_seconds=0.03,
    )
    session = RecordingSession(
        process=None,  # type: ignore[arg-type]
        output_path=output,
        video_path=video,
        audio_process=None,
        audio_stop_event=None,
        audio_path=audio,
        audio_started_at=10.00,
        video_started_at=10.05,
    )

    MatchRecorder(settings)._finalize_recording(session)

    command = calls[0]
    assert command[:6] == [str(ffmpeg), "-hide_banner", "-loglevel", "warning", "-y", "-itsoffset"]
    assert command[6] == "0.080"
    assert command[7:9] == ["-i", str(video)]


def test_finalize_delays_audio_when_audio_started_late(tmp_path, monkeypatch) -> None:
    video = tmp_path / "race.video.mkv"
    audio = tmp_path / "race.audio.wav"
    output = tmp_path / "race.mkv"
    video.write_bytes(b"video")
    audio.write_bytes(b"0" * 128)
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        output.write_bytes(b"muxed")
        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)

    ffmpeg = tmp_path / "ffmpeg.exe"
    settings = RecordingSettings(
        ffmpeg_path=ffmpeg,
        output_dir=tmp_path,
        video_sync_offset_seconds=0,
    )
    session = RecordingSession(
        process=None,  # type: ignore[arg-type]
        output_path=output,
        video_path=video,
        audio_process=None,
        audio_stop_event=None,
        audio_path=audio,
        audio_started_at=10.08,
        video_started_at=10.00,
    )

    MatchRecorder(settings)._finalize_recording(session)

    command = calls[0]
    assert command[:6] == [str(ffmpeg), "-hide_banner", "-loglevel", "warning", "-y", "-i"]
    assert command[6] == str(video)
    assert command[7:10] == ["-itsoffset", "0.080", "-i"]
