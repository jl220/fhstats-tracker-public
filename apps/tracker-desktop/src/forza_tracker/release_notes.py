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
RELEASE_NOTES_VERSION = "1.0.3"

_INSTALLED_NOTES = {
    "en": (
        f"FHStats Tracker {RELEASE_NOTES_VERSION}\n\n"
        "• Removed garage scanning and its automatic scan recording. Existing garage files and backups are preserved.\n"
        "• PC event participation uses the tracker’s existing native capture and automatic API upload. Xbox uses its separate direct Data Out connection.\n"
        "• Removed the optional UDP event relay introduced in 1.0.2. Previous relay settings no longer send packets; local telemetry and result upload remain available.\n\n"
        "Previous 1.0.2 release, relay withdrawn:\n"
        "• Added optional event Data Out forwarding in Settings. Keep local tracking while sending telemetry to your personal event IP and UDP port.\n"
        "• Configure Forza to send to 127.0.0.1 and the tracker local port, normally 9999. Enable event forwarding, save and restart the tracker.\n"
        "• Forwarding errors do not stop local tracking. Check Last reception on your FHStats Player page to confirm server reception.\n\n"
        "Previous 1.0.1 release:\n"
        "• Added Off-Road Capabilities Test, code 219994682, with complete clean Cross Country runs per car and class.\n"
        "• Recovered EventLab discovery when an active blueprint creates references after an idle scan, with bounded refreshes.\n"
        "• Retained native tests discard collisions, rewinds and aborted runs across repeated restarts.\n"
        "• The community API accepts four route bundles while preserving earlier three route editions.\n\n"
        "Previous 1.0.0 release:\n"
        "• Added Matsuri Netsu Road sprint, code 107181279, and Kinkaku Ji S 4L Dirt circuit, code 437239865, to Home.\n"
        "• Community sprint finishes save the complete clean run. Dirt circuits save your best clean lap per car and class, without waiting for four laps.\n"
        "• Repeated EventLab restarts establish a new observed start after a sustained full timing reset. Aborted and invalid attempts never replace clean bests.\n"
        "• Improved coherent memory reads during racing and retained strict identity, timing and invalidation checks.\n"
        "• Community uploads use your observed Forza gamertag. A claimed player card and review remain required.\n\n"
        "Previous tester release:\n"
        "• A simpler Home introduces Creator Events and the Sportsland Sugawara practice track, code 477632104.\n"
        "• Choose your own car and tune. Community capture reads event, car, class, PI and clean laps from memory.\n"
        "• Community bests now live on Home. Results use the game gamertag automatically, without account login. A claimed card and identity review are required to compete.\n"
        "• Online and Rivals live details are optional on Home. Garage navigation has been removed; existing local data is preserved.\n"
        "• This is a tester release. Competition dates have not been announced and submitted times require review.\n\n"
        "Earlier release history:\n"
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
        "• Retirado el escaneo de garaje y su grabación automática. Se conservan los archivos y las copias de garaje existentes.\n"
        "• En PC, los eventos utilizan la captura nativa y la subida automática por API del tracker. Xbox utiliza su conexión directa de Data Out por separado.\n"
        "• Retirado el reenvío UDP opcional introducido en 1.0.2. Los ajustes antiguos ya no envían paquetes; se mantienen la telemetría local y la subida de resultados.\n\n"
        "Versión 1.0.2 anterior, reenvío retirado:\n"
        "• Reenvío opcional de Data Out al evento desde Ajustes. Conserva el seguimiento local y envía telemetría a tu IP y puerto UDP personales.\n"
        "• Configura Forza con 127.0.0.1 y el puerto local del tracker, normalmente 9999. Activa el reenvío, guarda y reinicia el tracker.\n"
        "• Los errores de reenvío no detienen el seguimiento local. Comprueba Última recepción en tu página de Jugador para confirmar la llegada al servidor.\n\n"
        "Versión 1.0.1 anterior:\n"
        "• Añadido Off-Road Capabilities Test, código 219994682, con recorridos completos limpios de Cross Country por coche y clase.\n"
        "• Recuperada la detección de EventLab si el blueprint activo crea referencias después de una búsqueda en reposo, con nuevas búsquedas limitadas.\n"
        "• Las pruebas nativas conservadas descartan choques, rebobinados e intentos abortados tras varios reinicios.\n"
        "• La API comunitaria admite conjuntos de cuatro pistas y conserva las ediciones anteriores de tres.\n\n"
        "Versión 1.0.0 anterior:\n"
        "• Inicio incorpora el sprint Road Matsuri Netsu, código 107181279, y el circuito Dirt Kinkaku Ji S 4L, código 437239865.\n"
        "• Los sprints comunitarios guardan el recorrido limpio completo. En Dirt se guarda la mejor vuelta limpia por coche y clase, sin esperar cuatro vueltas.\n"
        "• Los reinicios de EventLab establecen una nueva salida tras observar el restablecimiento completo y sostenido de los relojes. Los intentos abortados o inválidos no sustituyen mejores limpias.\n"
        "• Mejorada la lectura coherente de memoria durante la carrera, manteniendo los controles de identidad, tiempos e invalidación.\n"
        "• Los envíos usan el gamertag observado en Forza. Para competir siguen siendo necesarias la ficha reclamada y la revisión.\n\n"
        "Versión de prueba anterior:\n"
        "• Inicio simplificado con Creator Events y la pista de prueba Sportsland Sugawara, código 477632104.\n"
        "• Elige tu coche y tuneo. El evento, coche, clase, PI y vueltas limpias se leen por memoria.\n"
        "• Las mejores comunitarias están en Inicio. Los tiempos se envían con el gamertag del juego, sin iniciar sesión. Para competir necesitas la tarjeta reclamada y la revisión de identidad.\n"
        "• El seguimiento Online y Rivals se puede desplegar en Inicio. Se retira el garaje de la navegación y se conservan sus datos locales.\n"
        "• Versión de prueba. No hay fechas anunciadas para la competición; los tiempos enviados requieren revisión.\n\n"
        "Historial de versiones anteriores:\n"
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
