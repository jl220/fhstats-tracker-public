from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


MAX_CACHE_BYTES = 1024 * 1024
MAX_NOTES_CHARS = 128 * 1024
MAX_VERSION_CHARS = 100
MAX_TIMESTAMP_CHARS = 100
RELEASE_NOTES_VERSION = "0.4.12"

_INSTALLED_NOTES = {
    "en": (
        f"FHStats Tracker {RELEASE_NOTES_VERSION}\n\n"
        "• Home shows saved Rivals bests for the detected event while waiting for car telemetry, with each car and class identified. It switches to the current car's best once detected.\n"
        "• History is now Online, keeping Rivals personal history separate.\n"
        "• Rivals history shows each time's approximate snapshot rank or Outside top 50 directly in the list.\n"
        "• Meta Cars refreshes its images and supports current car artwork. Missing screenshots fall back to the identified car image, also available offline.\n"
        "• Rivals uses Clean when no invalidation is detected and Invalid when detected. The Unconfirmed state and manual confirmation requirement are removed.\n"
        "• Existing pending attempts become Clean with their audit retained; incomplete attempts never count as bests.\n"
        "• Your published Rivals time, car and snapshot rank are visible even before a clean lap is recorded locally.\n"
        "• The telemetry receiver stays alive if Windows temporarily blocks a status-file write.\n"
        "• Large snapshot catalogs no longer block checkpoint sampling after each completed lap.\n"
        "• Fixed interrupted Rivals observation caused by temporary Windows telemetry file locks and queued UDP frames.\n"
        "• Preserved clean-lap coverage when memory and telemetry cross the finish one frame apart.\n"
        "• Smoother live lap and sector clocks; recorded times remain measured game values.\n"
        "• Home now opens directly on the active Online or Rivals event.\n"
        "• Automatic Rivals event recognition from read-only memory, with UDP lap times.\n"
        "• Personal Rivals history and clean bests for each event, car and class.\n"
        "• Approximate top 50 comparison against dated snapshots; invalid laps never replace clean bests.\n"
        "• Lap validity is automatic; manual Clean/Invalid controls have been removed from Home and Rivals history.\n"
        "• Live sectors learn each circuit's checkpoint count and compare three sections with your last recorded clean lap. Green is faster; red is slower.\n"
        "• Invalid laps hide the live clock and discard sector progress until the next clean lap.\n"
        "• Sekibe and Hirosaki use validated route IDs; other events use exact catalog names when available. Point-to-point finishes are not recorded automatically yet.\n"
        "• Renovated desktop layout with stable navigation between every section.\n"
        "• New What's new page with installed notes available offline.\n"
        "• Update status now stays independent of race capture status.\n"
        "• Repaired the pause when moving from Videos to My Garage.\n"
        "• Automatic race capture and local race history.\n"
        "• Live telemetry support with OCR fallback.\n"
        "• Race recordings and a local video library.\n"
        "• Rivals and Meta Cars catalogs for competitive reference.\n"
        "• My Garage scanning and management tools.\n"
        "• Complete English and Spanish interface."
    ),
    "es": (
        f"FHStats Tracker {RELEASE_NOTES_VERSION}\n\n"
        "• Inicio muestra las mejores de Rivals guardadas para el evento detectado mientras espera telemetría, indicando coche y clase. Al detectar el coche, muestra su mejor marca.\n"
        "• Historial ahora se llama Online, separado del historial personal de Rivals.\n"
        "• El historial de Rivals muestra el puesto aproximado de cada tiempo en el snapshot o Fuera del top 50 directamente en el listado.\n"
        "• Meta Cars actualiza sus imágenes y reconoce las fotos actuales. Si falta una captura, muestra la imagen del coche identificado, disponible también sin conexión.\n"
        "• Rivals usa Limpia mientras no se detecte invalidación e Inválida cuando se detecta. Se eliminan el estado pendiente y la confirmación manual obligatoria.\n"
        "• Los intentos pendientes pasan a Limpia conservando su historial; los incompletos no cuentan como récords.\n"
        "• Tu marca publicada de Rivals, coche y puesto en el snapshot aparecen aunque todavía no tengas una vuelta limpia local.\n"
        "• El receptor de telemetría continúa activo si Windows bloquea temporalmente la escritura del estado.\n"
        "• Los catálogos grandes ya no bloquean la lectura de checkpoints al completar cada vuelta.\n"
        "• Corregidos los cortes de observación de Rivals por bloqueos temporales del archivo de telemetría en Windows y paquetes UDP acumulados.\n"
        "• Conservada la observación de vueltas limpias cuando memoria y telemetría cruzan meta con un fotograma de diferencia.\n"
        "• Cronómetros de vuelta y sector más fluidos; los tiempos guardados siguen siendo los medidos por el juego.\n"
        "• Inicio abre directamente con el evento activo de Online o Rivals.\n"
        "• Reconocimiento automático del evento de Rivals por memoria de solo lectura, con tiempos UDP.\n"
        "• Historial personal y mejores limpias por evento, coche y clase.\n"
        "• Comparación aproximada con el top 50 de snapshots fechados; las inválidas no sustituyen récords limpios.\n"
        "• La validez es automática; eliminados los controles manuales Limpia/Inválida de Inicio y del historial de Rivals.\n"
        "• Los sectores aprenden los checkpoints de cada circuito y comparan tres tramos con la última vuelta limpia registrada. Verde al mejorar y rojo al empeorar.\n"
        "• Las vueltas inválidas ocultan el cronómetro y descartan los parciales hasta la siguiente vuelta limpia.\n"
        "• Sekibe e Hirosaki usan identificadores validados; otros eventos usan nombres exactos del catálogo cuando están disponibles. Las llegadas de tramos aún no se guardan automáticamente.\n"
        "• Interfaz renovada con navegación estable entre todas las secciones.\n"
        "• Nueva página Novedades con las notas instaladas disponibles sin conexión.\n"
        "• El estado de actualización ya es independiente del estado de captura.\n"
        "• Corregida la pausa al pasar de Vídeos a Mi garaje.\n"
        "• Captura automática de carreras e historial local.\n"
        "• Telemetría en directo con OCR como alternativa.\n"
        "• Grabaciones de carreras y biblioteca local de vídeos.\n"
        "• Catálogos de Rivals y Meta Cars como referencia competitiva.\n"
        "• Escaneo y gestión de Mi garaje.\n"
        "• Interfaz completa en español e inglés."
    ),
}


def installed_release_notes(language: str) -> str:
    return _INSTALLED_NOTES.get(str(language).strip().lower(), _INSTALLED_NOTES["en"])


def load_cached_release(path: Path) -> dict[str, str] | None:
    try:
        if path.stat().st_size > MAX_CACHE_BYTES:
            return None
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return _validated_payload(payload)


def cache_release(path: Path, *, version: str, notes: str) -> dict[str, str]:
    payload = {
        "version": version,
        "notes": notes,
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    validated = _validated_payload(payload)
    if validated is None:
        if not isinstance(notes, str) or len(notes) > MAX_NOTES_CHARS:
            raise ValueError("release notes are invalid or too large")
        raise ValueError("release cache fields are invalid")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(validated, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return validated


def _validated_payload(payload: object) -> dict[str, str] | None:
    if not isinstance(payload, dict) or set(payload) != {"version", "notes", "checked_at"}:
        return None
    version = payload.get("version")
    notes = payload.get("notes")
    checked_at = payload.get("checked_at")
    if not all(isinstance(value, str) for value in (version, notes, checked_at)):
        return None
    if not version or len(version) > MAX_VERSION_CHARS:
        return None
    if len(notes) > MAX_NOTES_CHARS:
        return None
    if not checked_at or len(checked_at) > MAX_TIMESTAMP_CHARS:
        return None
    try:
        parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return {"version": version, "notes": notes, "checked_at": checked_at}


__all__ = [
    "RELEASE_NOTES_VERSION",
    "cache_release",
    "installed_release_notes",
    "load_cached_release",
]
