from pathlib import Path
import time

from forza_tracker.config.settings import RecordingSettings
from forza_tracker.models.screen_type import ScreenType
from forza_tracker.recording.lifecycle import apply_recording_boundary
from forza_tracker.recording.recorder import (
    EncoderChoice,
    build_recording_command,
    enforce_storage_limit,
    recording_stem,
    safe_filename_component,
)
from forza_tracker.recording.recorder import MatchRecorder
from forza_tracker.storage.cleanup import enforce_image_storage_limit


def test_nvenc_command_records_1080p_with_constant_quality() -> None:
    command = build_recording_command(
        Path("ffmpeg.exe"),
        Path("race.mkv"),
        RecordingSettings(enabled=True),
        EncoderChoice("NVIDIA NVENC", "h264_nvenc"),
        monitor_index=1,
    )
    joined = " ".join(str(value) for value in command)
    assert "gfxcapture=" in joined
    assert "window_exe='(?i)(forzahorizon6|forzahorizon5)\\.exe'" in joined
    assert "max_framerate=60:width=1920:height=1080" in joined
    assert "resize_mode=scale_aspect:scale_mode=bicubic" in joined
    assert "hwdownload" not in joined
    assert "-c:v h264_nvenc" in joined
    assert "-cq 20" in joined
    assert "-g 120" in joined
    assert "-multipass qres" in joined


def test_amd_command_uses_amf_cqp() -> None:
    command = build_recording_command(
        Path("ffmpeg.exe"),
        Path("race.mkv"),
        RecordingSettings(enabled=True),
        EncoderChoice("AMD AMF", "h264_amf"),
        monitor_index=0,
    )
    joined = " ".join(str(value) for value in command)
    assert "-c:v h264_amf" in joined
    assert "-rc cqp" in joined
    assert "-qp_i 20" in joined
    assert "-quality quality" in joined


def test_storage_limit_deletes_oldest_video_first(tmp_path: Path) -> None:
    oldest = tmp_path / "oldest.mkv"
    newest = tmp_path / "newest.mkv"
    oldest.write_bytes(b"x" * 700)
    newest.write_bytes(b"x" * 700)
    oldest.touch()
    newest.touch()
    oldest_stat = oldest.stat()
    newest_stat = newest.stat()
    oldest.touch()
    newest.touch()
    import os

    os.utime(oldest, (oldest_stat.st_atime - 20, oldest_stat.st_mtime - 20))
    os.utime(newest, (newest_stat.st_atime, newest_stat.st_mtime))

    deleted = enforce_storage_limit(tmp_path, max_storage_gb=1000 / 1024**3)

    assert deleted == [oldest]
    assert not oldest.exists()
    assert newest.exists()


def test_storage_limit_never_deletes_the_only_recording(tmp_path: Path) -> None:
    recording = tmp_path / "current.mkv"
    recording.write_bytes(b"x" * 2000)

    deleted = enforce_storage_limit(tmp_path, max_storage_gb=1 / 1024**3)

    assert deleted == []
    assert recording.exists()


def test_capture_storage_limit_deletes_oldest_images_recursively(tmp_path: Path) -> None:
    nested = tmp_path / "class_crops"
    nested.mkdir()
    oldest = tmp_path / "oldest.png"
    middle = nested / "middle.png"
    newest = tmp_path / "newest.png"
    note = tmp_path / "history.jsonl"
    oldest.write_bytes(b"x" * 500)
    middle.write_bytes(b"x" * 500)
    newest.write_bytes(b"x" * 500)
    note.write_text("keep", encoding="utf-8")

    import os

    now = time.time()
    os.utime(oldest, (now - 30, now - 30))
    os.utime(middle, (now - 20, now - 20))
    os.utime(newest, (now - 10, now - 10))

    deleted = enforce_image_storage_limit(tmp_path, max_storage_gb=900 / 1024**3)

    assert deleted == [oldest, middle]
    assert not oldest.exists()
    assert not middle.exists()
    assert newest.exists()
    assert note.exists()


class FakeRecorder:
    def __init__(self) -> None:
        self.is_recording = False
        self.actions: list[tuple[str, int | None, str | None, str | None, int | None]] = []

    def start(
        self,
        monitor_index: int = 0,
        car_name: str | None = None,
        car_class: str | None = None,
    ) -> None:
        self.is_recording = True
        self.actions.append(("start", monitor_index, car_name, car_class, None))

    def stop(
        self,
        final_car_name: str | None = None,
        final_car_class: str | None = None,
        finish_position: int | None = None,
    ) -> None:
        self.is_recording = False
        self.actions.append(("stop", None, final_car_name, final_car_class, finish_position))


def test_three_race_event_creates_three_independent_recordings() -> None:
    recorder = FakeRecorder()
    sequence = [
        ScreenType.EVENT_REGISTRATION,
        ScreenType.STARTING_GRID,
        ScreenType.RACE_RESULT,
        ScreenType.CHAMPIONSHIP_RESULT,
        ScreenType.STARTING_GRID,
        ScreenType.RACE_RESULT,
        ScreenType.CHAMPIONSHIP_RESULT,
        ScreenType.STARTING_GRID,
        ScreenType.RACE_RESULT,
        ScreenType.CHAMPIONSHIP_RESULT,
    ]
    for screen_type in sequence:
        apply_recording_boundary(
            recorder,
            screen_type,
            monitor_index=1,
            car_name="Shelby Daytona",
            car_class="A 700",
        )

    assert recorder.actions == [
        ("start", 1, "Shelby Daytona", "A 700", None),
        ("stop", None, "Shelby Daytona", "A 700", None),
        ("start", 1, "Shelby Daytona", "A 700", None),
        ("stop", None, "Shelby Daytona", "A 700", None),
        ("start", 1, "Shelby Daytona", "A 700", None),
        ("stop", None, "Shelby Daytona", "A 700", None),
    ]


def test_new_grid_recovers_when_previous_result_was_missed() -> None:
    recorder = FakeRecorder()
    apply_recording_boundary(recorder, ScreenType.STARTING_GRID, monitor_index=0)
    apply_recording_boundary(recorder, ScreenType.STARTING_GRID, monitor_index=0)

    assert recorder.actions == [
        ("start", 0, None, None, None),
        ("stop", None, None, None, None),
        ("start", 0, None, None, None),
    ]


def test_car_name_is_safe_for_recording_filename() -> None:
    assert safe_filename_component('SUBARU WRX "22" / A 700') == "SUBARU-WRX-22-A-700"
    assert safe_filename_component("  ") == "Coche-desconocido"
    assert (
        recording_stem("Shelby Daytona", "A 700", "2026-06-21_18-30-00")
        == "2026-06-21_18-30-00_Shelby-Daytona_A-700"
    )
    assert (
        recording_stem("1984 Honda Civic CRX Mugen", "B 600", "2026-06-21_18-30-00", finish_position=1)
        == "2026-06-21_18-30-00_1984-Honda-Civic-CRX-Mugen_B-600"
    )


def test_result_boundary_stops_recording_with_final_telemetry_metadata() -> None:
    recorder = FakeRecorder()
    apply_recording_boundary(
        recorder,
        ScreenType.STARTING_GRID,
        monitor_index=0,
        car_name="OCR Unknown",
        car_class="S1 900",
    )
    apply_recording_boundary(
        recorder,
        ScreenType.RACE_RESULT,
        monitor_index=0,
        car_name="1984 Honda Civic CRX Mugen",
        car_class="B 600",
        finish_position=1,
    )

    assert recorder.actions == [
        ("start", 0, "OCR Unknown", "S1 900", None),
        ("stop", None, "1984 Honda Civic CRX Mugen", "B 600", 1),
    ]


class SlowProcess:
    def __init__(self) -> None:
        self.stdin = self
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def write(self, _value: str) -> None:
        return None

    def flush(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        time.sleep(0.25)
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = 0


def test_stopping_recording_does_not_block_capture_loop(tmp_path: Path) -> None:
    recorder = MatchRecorder(RecordingSettings(output_dir=tmp_path))
    video = tmp_path / ".race.video.mkv"
    video.write_bytes(b"video")
    recorder._process = SlowProcess()  # type: ignore[assignment]
    recorder._output_path = tmp_path / "race.mkv"
    recorder._video_path = video
    recorder._timestamp = "2026-06-21_18-30-00"

    started = time.monotonic()
    recorder.stop(
        final_car_name="1984 Honda Civic CRX Mugen",
        final_car_class="B 600",
        finish_position=1,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert not recorder.is_recording
    recorder.stop(wait=True)
    assert (tmp_path / "2026-06-21_18-30-00_1984-Honda-Civic-CRX-Mugen_B-600.mkv").exists()
