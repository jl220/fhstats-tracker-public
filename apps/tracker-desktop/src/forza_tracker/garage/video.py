from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Callable, Mapping


StatusReader = Callable[[], Mapping[str, Any]]


def copy_finalized_recording(
    source: Path,
    destination: Path,
    *,
    status_reader: StatusReader,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    timeout: float = 70.0,
    poll_interval: float = 0.25,
) -> Path:
    deadline = monotonic() + timeout
    temporary_video = source.with_name(f".{source.stem}.video.mkv")
    temporary_audio = source.with_name(f".{source.stem}.audio.wav")
    while monotonic() < deadline:
        status = status_reader()
        if (
            not bool(status.get("recording"))
            and source.is_file()
            and source.stat().st_size > 0
            and not temporary_video.exists()
            and not temporary_audio.exists()
        ):
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_destination = destination.with_suffix(destination.suffix + ".tmp")
            shutil.copy2(source, temporary_destination)
            if temporary_destination.stat().st_size != source.stat().st_size:
                temporary_destination.unlink(missing_ok=True)
                raise OSError("La copia del video del garaje quedo incompleta.")
            temporary_destination.replace(destination)
            return destination
        sleep(poll_interval)
    raise TimeoutError("La finalizacion del video supero el tiempo permitido.")
