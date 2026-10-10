from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from forza_tracker.rivals_circuit_translations import RIVALS_CIRCUIT_TRANSLATIONS


_FILES = {
    "road": "road-rivals.json",
    "street": "street-rivals.json",
    "dirt": "dirt-rivals.json",
    "cross-country": "cross-country-rivals.json",
}
_BASE_URL = "https://fhstats.com/api/catalog"
_MANIFEST = "rivals-snapshot-manifest.json"


class SnapshotCatalog:
    """Read Rivals snapshots without discarding capture and coverage metadata."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._payloads: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
        self._payload_hashes: dict[str, str] = {}
        self._verified: dict[str, dict[str, Any]] = self._read_manifest()

    def events(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for discipline in _FILES:
            payload = self._load(discipline)
            if payload is None:
                continue
            for event in payload.get("events", []):
                if not isinstance(event, dict) or not event.get("slug"):
                    continue
                name_en = _text(event.get("name_en"))
                name_es = _text(event.get("name_es"))
                event_id = f"{discipline}:{event['slug']}"
                translations = RIVALS_CIRCUIT_TRANSLATIONS.get(event_id, {})
                result.append({
                    "event_id": event_id,
                    "event_name": name_es or name_en or str(event["slug"]),
                    "name_en": name_en,
                    "name_es": name_es,
                    "name_fr": _text(event.get("name_fr")) or translations.get("name_fr", ""),
                    "name_ru": _text(event.get("name_ru")) or translations.get("name_ru", ""),
                    "event_kind": _event_kind(event),
                    "discipline": discipline,
                    "slug": str(event["slug"]),
                })
        return result

    def board(self, event_id: str, car_class: str) -> dict[str, Any] | None:
        discipline, separator, slug = str(event_id).partition(":")
        if not separator or discipline not in _FILES:
            return None
        payload = self._load(discipline)
        if payload is None:
            return None
        wanted_class = _normalise_class(car_class)
        for event in payload.get("events", []):
            if not isinstance(event, dict) or str(event.get("slug")) != slug:
                continue
            for group in event.get("classes", []):
                if not isinstance(group, dict) or _normalise_class(group.get("class") or group.get("label")) != wanted_class:
                    continue
                return self._adapt_board(discipline, event, group)
        return None

    def refresh(self, discipline: str) -> dict[str, Any]:
        downloaded_at = datetime.now(timezone.utc).isoformat()
        if discipline not in _FILES:
            return {"status": "error", "discipline": discipline, "downloaded_at": downloaded_at, "error": "unknown discipline"}
        filename = _FILES[discipline]
        path = self.root / filename
        previous_verification = self._verified.get(discipline)
        try:
            response = requests.get(
                f"{_BASE_URL}/{filename}", timeout=8.0,
                headers={"Accept": "application/json,text/plain,*/*"},
            )
            response.raise_for_status()
            payload = json.loads(str(response.text).lstrip("\ufeff"))
            _validate_payload(payload, discipline)
            new_hash = _content_hash(payload)
            old_payload = self._load(discipline)
            old_hash = _content_hash(old_payload) if old_payload is not None else None
            status = "unchanged" if old_hash == new_hash else "changed"
            self._verified[discipline] = {"hash": new_hash, "downloaded_at": downloaded_at}
            self._write_manifest()
            if status == "changed":
                _write_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))
                self._payloads.pop(discipline, None)
            return {
                "status": status, "discipline": discipline,
                "downloaded_at": downloaded_at, "snapshot_id": new_hash,
            }
        except Exception as error:
            if previous_verification is None:
                self._verified.pop(discipline, None)
            else:
                self._verified[discipline] = previous_verification
            return {
                "status": "error", "discipline": discipline,
                "downloaded_at": downloaded_at, "error": str(error),
            }

    def _load(self, discipline: str) -> dict[str, Any] | None:
        path = self.root / _FILES[discipline]
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            cached = self._payloads.get(discipline)
            if cached and cached[0] == signature:
                return cached[1]
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                return None
            self._payloads[discipline] = (signature, payload)
            self._payload_hashes[discipline] = _content_hash(payload)
            return payload
        except (OSError, json.JSONDecodeError):
            return None

    def _adapt_board(self, discipline: str, event: dict[str, Any], group: dict[str, Any]) -> dict[str, Any]:
        entries, valid = _adapt_entries(group.get("entries"))
        coverage = _coverage(entries) if valid else "invalid"
        identity = {
            str(entry["player_id"]): entry["rank"]
            for entry in entries if entry.get("player_id")
        }
        stable = {
            "discipline": discipline,
            "slug": str(event.get("slug")),
            "class": _normalise_class(group.get("class") or group.get("label")),
            "captured_at": group.get("captured_at"),
            "entries": entries,
            "source": group.get("capture_method") or event.get("capture_method"),
        }
        snapshot_id = _content_hash(stable)
        verification = self._verified.get(discipline, {})
        verified = verification.get("hash") == self._payload_hashes.get(discipline)
        references = _references(entries)
        return {
            "event_id": f"{discipline}:{event.get('slug')}",
            "event_name": _text(event.get("name_es")) or _text(event.get("name_en")) or str(event.get("slug")),
            "name_en": _text(event.get("name_en")), "name_es": _text(event.get("name_es")),
            "event_kind": _event_kind(event), "discipline": discipline, "slug": str(event.get("slug")),
            "car_class": stable["class"], "class_label": _text(group.get("label")) or stable["class"],
            "captured_at": group.get("captured_at"), "downloaded_at": verification.get("downloaded_at") if verified else None,
            "source": stable["source"], "entries": entries, "identity": identity,
            "coverage": coverage, "references": references,
            "snapshot_id": snapshot_id, "hash": snapshot_id,
            "cache_status": "verified" if verified else "unchecked",
        }

    def _read_manifest(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads((self.root / _MANIFEST).read_text(encoding="utf-8"))
            return payload.get("disciplines", {}) if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_manifest(self) -> None:
        _write_atomic(self.root / _MANIFEST, json.dumps({"disciplines": self._verified}, ensure_ascii=False, indent=2))


def snapshot_summary(board: dict[str, Any] | None, profile_id: str) -> dict[str, Any] | None:
    """Published board context, independent of locally observed attempts."""
    if board is None:
        return None
    result = {key: board.get(key) for key in ('event_id', 'car_class', 'captured_at',
              'downloaded_at', 'snapshot_id', 'coverage', 'references', 'cache_status')}
    wanted = str(profile_id or '').strip().casefold()
    matches = [entry for entry in board.get('entries', [])
               if wanted and str(entry.get('player') or '').strip().casefold() == wanted]
    result['player_result'] = dict(matches[0]) if (len(matches) == 1
            and board.get('coverage') != 'invalid' and _valid_capture_date(board.get('captured_at'))) else None
    return result


def compare_attempt(attempt: dict[str, Any], board: dict[str, Any] | None) -> dict[str, Any]:
    base = {
        "status": "no_snapshot" if board is None else "not_comparable",
        "estimated_rank": None, "gap_top50_ms": None, "cutoff_ms": None,
        "rank_uncertain": False,
        "captured_at": board.get("captured_at") if board else None,
        "downloaded_at": board.get("downloaded_at") if board else None,
        "snapshot_id": board.get("snapshot_id") if board else None,
        "coverage": board.get("coverage") if board else "none",
        "references": dict(board.get("references") or {}) if board else {},
    }
    if board is None:
        return base
    time_ms = attempt.get("time_ms")
    if attempt.get("completed") is not True or attempt.get("validity") != "clean" or not _positive_number(time_ms):
        return base
    if attempt.get("event_id") is not None and attempt.get("event_id") != board.get("event_id"):
        return base
    if attempt.get("car_class") is not None and _normalise_class(attempt.get("car_class")) != _normalise_class(board.get("car_class")):
        return base
    if not _valid_capture_date(board.get("captured_at")):
        base["status"] = "malformed_snapshot"
        return base
    if board.get("coverage") == "invalid":
        base["status"] = "malformed_snapshot"
        return base
    entries = board.get("entries")
    if not isinstance(entries, list) or not entries:
        base["status"] = "malformed_snapshot"
        return base
    try:
        ranks = [entry["rank"] for entry in entries]
        times = [float(entry["time_ms"]) for entry in entries]
    except (KeyError, TypeError, ValueError):
        base["status"] = "malformed_snapshot"
        return base
    if ranks != list(range(1, len(entries) + 1)) or times != sorted(times):
        base["status"] = "malformed_snapshot"
        return base

    comparable = list(entries)
    own_id = attempt.get("player_id") or attempt.get("profile_identity")
    if own_id is not None:
        comparable = [entry for entry in comparable if entry.get("player_id") != str(own_id)]
    value = float(time_ms)
    if board.get("coverage") != "complete_top50":
        base["status"] = "partial"
        return base
    if len(entries) < 50:
        base["status"] = "malformed_snapshot"
        return base

    cutoff = float(entries[49]["time_ms"])
    base["cutoff_ms"] = int(cutoff) if cutoff.is_integer() else cutoff
    base["gap_top50_ms"] = value - cutoff
    if abs(value - cutoff) < 1.0:
        base["status"] = "tied_cutoff"
        return base
    if value > cutoff:
        base["status"] = "outside_top50"
        return base
    base["status"] = "estimated_rank"
    base["estimated_rank"] = 1 + sum(float(entry["time_ms"]) < value for entry in comparable)
    base["rank_uncertain"] = any(abs(float(entry["time_ms"]) - value) < 1.0 for entry in comparable)
    return base


def _adapt_entries(raw: Any) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(raw, list):
        return [], False
    result: list[dict[str, Any]] = []
    valid = True
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("rank"), int):
            valid = False
            continue
        time_ms = _parse_time_ms(item.get("time"))
        if time_ms is None:
            valid = False
            continue
        entry = dict(item)
        entry["time_ms"] = time_ms
        if item.get("player_id") is not None:
            entry["player_id"] = str(item["player_id"])
        result.append(entry)
    ranks = [entry["rank"] for entry in result]
    times = [entry["time_ms"] for entry in result]
    if ranks != list(range(1, len(result) + 1)) or times != sorted(times):
        valid = False
    return result, valid


def _coverage(entries: list[dict[str, Any]]) -> str:
    return "complete_top50" if len(entries) >= 50 and [e["rank"] for e in entries[:50]] == list(range(1, 51)) else "partial"


def _references(entries: list[dict[str, Any]]) -> dict[str, int | float]:
    by_rank = {entry["rank"]: entry["time_ms"] for entry in entries}
    return {
        f"top_{rank}_ms": by_rank[rank]
        for rank in (1, 10, 25, 50) if rank in by_rank
    }


def _parse_time_ms(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(?:(\d+):)?(\d+)[,.](\d{3})\s*", value)
    if not match:
        return None
    minutes = int(match.group(1) or 0)
    seconds = int(match.group(2))
    if seconds >= 60:
        return None
    return (minutes * 60 + seconds) * 1000 + int(match.group(3))


def _normalise_class(value: Any) -> str:
    match = re.match(r"\s*(S[12]|[ABCDR])", str(value or "").upper())
    return match.group(1) if match else str(value or "").strip().upper()


def _event_kind(event: dict[str, Any]) -> str:
    explicit = str(event.get("event_kind") or "").lower()
    if explicit in {"circuit", "sprint"}:
        return explicit
    text = " ".join(str(event.get(key) or "").lower() for key in ("slug", "name_en", "name_es"))
    if any(marker in text for marker in ("circuit", "circuito", "scramble")):
        return "circuit"
    if "sprint" in text:
        return "sprint"
    return "unknown"


def _validate_payload(payload: Any, discipline: str) -> None:
    if not isinstance(payload, dict) or payload.get("mode") != "rivals" or payload.get("discipline") != discipline:
        raise ValueError("download is not the requested Rivals catalog")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("catalog events must be a list")
    for event in events:
        if not isinstance(event, dict) or not event.get("slug") or not isinstance(event.get("classes"), list):
            raise ValueError("catalog contains a malformed event")
        for group in event["classes"]:
            if not isinstance(group, dict) or not _valid_capture_date(group.get("captured_at")):
                raise ValueError("catalog contains a malformed class board")
            _entries, valid = _adapt_entries(group.get("entries"))
            if not valid:
                raise ValueError("catalog contains invalid ranks or times")


def _content_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.stem, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _valid_capture_date(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
