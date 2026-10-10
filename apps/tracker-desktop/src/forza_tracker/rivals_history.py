from __future__ import annotations

import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ATTEMPT_FIELDS = (
    "attempt_id",
    "session_id",
    "sequence",
    "profile_id",
    "event_id",
    "event_name",
    "event_kind",
    "event_source",
    "car_ordinal",
    "car_name",
    "car_class",
    "performance_index",
    "raw_seconds",
    "time_ms",
    "completed",
    "observed_partial",
    "validity",
    "validity_source",
    "captured_at",
    "time_source",
    "raw_unit",
    "normalization",
)

OPTIONAL_ATTEMPT_DEFAULTS = {
    "time_source": "udp",
    "raw_unit": "seconds",
    "normalization": "round_ms_provisional",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RivalsStore:
    """Small SQLite store for locally observed Rivals attempts."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    profile_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    event_source TEXT NOT NULL,
                    car_ordinal INTEGER NOT NULL,
                    car_name TEXT NOT NULL,
                    car_class TEXT NOT NULL,
                    performance_index INTEGER NOT NULL,
                    raw_seconds REAL,
                    time_ms INTEGER,
                    completed INTEGER NOT NULL,
                    observed_partial INTEGER NOT NULL,
                    validity TEXT NOT NULL,
                    validity_source TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    time_source TEXT NOT NULL DEFAULT 'udp',
                    raw_unit TEXT NOT NULL DEFAULT 'seconds',
                    normalization TEXT NOT NULL DEFAULT 'round_ms_provisional'
                );
                CREATE INDEX IF NOT EXISTS attempts_profile_group
                    ON attempts(profile_id, event_id, car_ordinal, car_class, time_ms);
                CREATE TABLE IF NOT EXISTS validity_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id TEXT NOT NULL,
                    validity TEXT NOT NULL,
                    validity_source TEXT NOT NULL,
                    changed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS comparison_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id TEXT NOT NULL,
                    evaluation_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(attempts)").fetchall()
            }
            for name, default in OPTIONAL_ATTEMPT_DEFAULTS.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE attempts ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'"
                    )

    def save_attempt(self, attempt: dict[str, Any]) -> None:
        required_fields = [field for field in ATTEMPT_FIELDS if field not in OPTIONAL_ATTEMPT_DEFAULTS]
        missing = [field for field in required_fields if field not in attempt]
        if missing:
            raise ValueError(f"Missing attempt fields: {', '.join(missing)}")
        initial = {
            **OPTIONAL_ATTEMPT_DEFAULTS,
            **attempt,
            "validity": "clean",
            "validity_source": "no_invalid_detected",
        }
        if attempt["validity_source"] == "memory_sampled" and (
            attempt["validity"] == "invalid"
            or (attempt["validity"] == "clean" and attempt["completed"] is True
                and attempt["observed_partial"] is False
                and attempt["event_kind"] == "circuit"
                and LapTracker._positive_finite(attempt["raw_seconds"]) is not None
                and LapTracker._positive_finite(attempt["time_ms"]) is not None)
        ):
            initial["validity"] = attempt["validity"]
            initial["validity_source"] = "memory_sampled"
        values = [initial[field] for field in ATTEMPT_FIELDS]
        values[ATTEMPT_FIELDS.index("completed")] = int(bool(attempt["completed"]))
        values[ATTEMPT_FIELDS.index("observed_partial")] = int(bool(attempt["observed_partial"]))
        placeholders = ", ".join("?" for _ in ATTEMPT_FIELDS)
        with self._connect() as connection:
            cursor = connection.execute(
                f"INSERT OR IGNORE INTO attempts ({', '.join(ATTEMPT_FIELDS)}) VALUES ({placeholders})",
                values,
            )
            if cursor.rowcount:
                connection.execute(
                    "INSERT INTO validity_audit (attempt_id, validity, validity_source, changed_at) VALUES (?, ?, ?, ?)",
                    (
                        attempt["attempt_id"],
                        initial["validity"],
                        initial["validity_source"],
                        attempt["captured_at"],
                    ),
                )

    def set_validity(self, attempt_id: str, validity: str) -> None:
        if validity not in {"clean", "invalid"}:
            raise ValueError(f"Unsupported validity: {validity}")
        source = "manual"
        changed_at = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE attempts SET validity = ?, validity_source = ? WHERE attempt_id = ?",
                (validity, source, attempt_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(attempt_id)
            connection.execute(
                "INSERT INTO validity_audit (attempt_id, validity, validity_source, changed_at) VALUES (?, ?, ?, ?)",
                (attempt_id, validity, source, changed_at),
            )

    def classify_pending(self, profile_id: str, *, source: str = "no_invalid_detected") -> int:
        """Apply the binary validity policy without losing the previous audit."""
        if source not in {"no_invalid_detected", "manual"}:
            raise ValueError(source)
        changed_at = _now()
        changed = 0
        with self._connect() as connection:
            rows = connection.execute("SELECT attempt_id FROM attempts WHERE profile_id=? AND validity='unknown'",
                                      (profile_id,)).fetchall()
            for row in rows:
                cursor = connection.execute("UPDATE attempts SET validity='clean', validity_source=? WHERE attempt_id=? AND profile_id=? AND validity='unknown'",
                                            (source, row[0], profile_id))
                if not cursor.rowcount:
                    continue
                connection.execute("INSERT INTO validity_audit (attempt_id, validity, validity_source, changed_at) VALUES (?, 'clean', ?, ?)",
                                   (row[0], source, changed_at))
                changed += 1
        return changed

    def record_comparison(self, attempt_id: str, evaluation: dict[str, Any]) -> None:
        encoded = json.dumps(evaluation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(attempt_id)
            connection.execute(
                "INSERT INTO comparison_audit (attempt_id, evaluation_json, recorded_at) VALUES (?, ?, ?)",
                (attempt_id, encoded, _now()),
            )

    def list_attempts(self, profile_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM attempts WHERE profile_id = ? ORDER BY captured_at DESC, rowid DESC",
                (profile_id,),
            ).fetchall()
            return [self._hydrate(connection, row) for row in rows]

    def best_attempts(self, profile_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.* FROM attempts AS a
                WHERE a.profile_id = ?
                  AND a.completed = 1
                      AND a.validity = 'clean'
                  AND a.time_ms IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM attempts AS faster
                    WHERE faster.profile_id = a.profile_id
                      AND faster.event_id = a.event_id
                      AND faster.car_ordinal = a.car_ordinal
                      AND faster.car_class = a.car_class
                      AND faster.completed = 1
                      AND faster.validity = 'clean'
                      AND faster.time_ms IS NOT NULL
                      AND (faster.time_ms < a.time_ms OR (faster.time_ms = a.time_ms AND faster.rowid < a.rowid))
                  )
                ORDER BY a.event_name, a.car_class, a.car_name
                """,
                (profile_id,),
            ).fetchall()
            return [self._hydrate(connection, row) for row in rows]

    def _hydrate(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        attempt = {field: row[field] for field in ATTEMPT_FIELDS}
        attempt["completed"] = bool(attempt["completed"])
        attempt["observed_partial"] = bool(attempt["observed_partial"])
        validity_rows = connection.execute(
            "SELECT validity, validity_source, changed_at FROM validity_audit WHERE attempt_id = ? ORDER BY audit_id",
            (row["attempt_id"],),
        ).fetchall()
        comparison_rows = connection.execute(
            "SELECT evaluation_json, recorded_at FROM comparison_audit WHERE attempt_id = ? ORDER BY audit_id",
            (row["attempt_id"],),
        ).fetchall()
        attempt["validity_history"] = [dict(item) for item in validity_rows]
        attempt["comparison_history"] = [
            {"evaluation": json.loads(item["evaluation_json"]), "recorded_at": item["recorded_at"]}
            for item in comparison_rows
        ]
        attempt["original_comparison"] = (
            attempt["comparison_history"][0]["evaluation"] if attempt["comparison_history"] else None
        )
        attempt["latest_comparison"] = (
            attempt["comparison_history"][-1]["evaluation"] if attempt["comparison_history"] else None
        )
        return attempt


def validated_memory_observation(value: Any, telemetry: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(value, dict) or type(value.get("dirty")) is not bool:
        return None
    if not isinstance(telemetry, dict):
        return None
    if any(type(telemetry.get(field)) not in (int, float)
           or not math.isfinite(telemetry[field]) or telemetry[field] < 0
           for field in ("current_race_time_seconds", "current_lap_seconds")):
        return None
    for field in ("race_clock", "lap_start_clock"):
        number = value.get(field)
        if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
            return None
    if any(not isinstance(value.get(field), str) or not value[field].strip()
           for field in ("record_id", "car_id")):
        return None
    elapsed = value["race_clock"] - value["lap_start_clock"]
    if elapsed < 0 or abs(value["race_clock"] - float(telemetry["current_race_time_seconds"])) > 0.20 + 1e-9:
        return None
    if abs(elapsed - float(telemetry["current_lap_seconds"])) > 0.20 + 1e-9:
        return None
    return {**value, "udp_race_clock": float(telemetry["current_race_time_seconds"])}


def sector_memory_observation(value, packet):
    """Permit the measured finish when UDP is still on its outgoing frame."""
    if not isinstance(packet, dict):
        return None
    matched = validated_memory_observation(value, packet)
    if matched is not None:
        return matched
    if not isinstance(value, dict) or not isinstance(value.get('checkpoints'), dict):
        return None
    cp = value['checkpoints']
    numbers = [cp.get('crossing_race_clock'), cp.get('crossing_lap_clock'),
               value.get('lap_start_clock'), value.get('race_clock'), packet.get('current_lap_seconds')]
    if any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in numbers):
        return None
    if (type(cp.get('lap_index')) is not int or type(packet.get('lap_number')) is not int
            or cp['lap_index'] != packet['lap_number'] + 1
            or abs(cp['crossing_race_clock'] - value['lap_start_clock']) > .001
            or abs(cp['crossing_lap_clock'] - packet['current_lap_seconds']) > .20):
        return None
    return validated_memory_observation(value, {**packet, 'current_lap_seconds': value['race_clock'] - value['lap_start_clock']})


class LapTracker:
    """Conservatively turns circuit telemetry boundaries into local attempts."""

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        self._event: dict[str, str] | None = None
        self._session: dict[str, Any] | None = None
        self._pending: list[dict[str, Any]] = []
        self._sequence = 0
        self._last_packet_key: tuple[int | None, int] | None = None
        self._needs_rebaseline = False
        self._lap_number: int | None = None
        self._progress_observed = False
        self._observed_partial = False
        self._current_lap_seconds = 0.0
        self._current_race_time_seconds = 0.0
        self._reset_validity()

    def select_event(self, event_id: str, event_name: str, event_kind: str = "circuit", *, event_source: str = "manual") -> None:
        new_event = {
            "event_id": event_id,
            "event_name": event_name,
            "event_kind": event_kind,
            "event_source": event_source,
        }
        if self._event != new_event:
            self._queue_incomplete()
            self._clear_session()
            self._event = new_event

    @property
    def current(self) -> dict[str, Any] | None:
        if self._session is None or self._event is None:
            return None
        return {
            "profile_id": self.profile_id,
            "session_id": self._session["session_id"],
            **self._event,
            "process_id": self._session["process_id"],
            "car_ordinal": self._session["car_ordinal"],
            "car_name": self._session["car_name"],
            "car_class": self._session["car_class"],
            "performance_index": self._session["performance_index"],
            "current_lap_seconds": self._current_lap_seconds,
            "validity": "invalid" if self._dirty else "clean",
            "validity_source": "memory_sampled" if self._dirty or self._clean_coverage else "no_invalid_detected",
        }

    def update(
        self,
        context: bool | None,
        telemetry: dict[str, Any] | None,
        process_id: int | None = None,
        *,
        memory_validity: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        emitted = self._take_pending()
        if context is not True or telemetry is None:
            self._clean_coverage = False
            if context is False:
                attempt = self._make_incomplete()
                if attempt is not None:
                    emitted.append(attempt)
                self._clear_session()
            elif self._session is not None:
                self._needs_rebaseline = True
            return emitted

        # Memory can lead UDP by one frame at the finish. Defer that mixed
        # pair instead of breaking the outgoing lap's coverage. The next
        # aligned sample still has to satisfy the normal boundary gap guard.
        if (telemetry.get("is_race_on")
                and validated_memory_observation(memory_validity, telemetry) is None
                and sector_memory_observation(memory_validity, telemetry) is not None):
            return emitted

        timestamp = telemetry.get("timestamp_ms")
        packet_key = (process_id, timestamp) if isinstance(timestamp, int) else None
        if packet_key is None or packet_key == self._last_packet_key:
            return emitted
        self._last_packet_key = packet_key
        if not telemetry.get("is_race_on"):
            self._clean_coverage = False
            if self._session is not None:
                self._needs_rebaseline = True
            return emitted
        if self._event is None or not self._valid_telemetry(telemetry):
            self._clean_coverage = False
            return emitted

        sample = validated_memory_observation(memory_validity, telemetry)
        identity = self._identity(telemetry, process_id)
        timing_is_zero = all(
            float(telemetry[field]) == 0.0
            for field in ("current_lap_seconds", "last_lap_seconds", "current_race_time_seconds")
        )
        if self._session is None:
            self._start_session(identity, telemetry)
            self._needs_rebaseline = timing_is_zero
            self._observe_validity(sample, newly_started=not timing_is_zero)
            return emitted
        if self._identity_changed(identity):
            attempt = self._make_incomplete()
            if attempt is not None:
                emitted.append(attempt)
            self._start_session(identity, telemetry)
            self._needs_rebaseline = timing_is_zero
            self._observe_validity(sample, newly_started=not timing_is_zero)
            return emitted
        if timing_is_zero:
            self._clean_coverage = False
            self._needs_rebaseline = True
            return emitted

        lap_number = int(telemetry["lap_number"])
        current_seconds = self._seconds(telemetry["current_lap_seconds"])
        race_seconds = self._seconds(telemetry["current_race_time_seconds"])
        if self._needs_rebaseline:
            if lap_number != self._lap_number:
                if self._dirty:
                    attempt = self._make_incomplete()
                    if attempt is not None:
                        emitted.append(attempt)
                self._reset_validity()
            self._clean_coverage = False
            self._observe_validity(sample)
            self._lap_number = lap_number
            self._current_lap_seconds = current_seconds
            self._current_race_time_seconds = race_seconds
            self._observed_partial = True
            self._progress_observed = self._progress_observed or current_seconds > 0
            self._needs_rebaseline = False
            return emitted

        if self._lap_number is not None and lap_number < self._lap_number:
            attempt = self._make_incomplete()
            if attempt is not None:
                emitted.append(attempt)
            self._start_session(identity, telemetry)
            self._observe_validity(sample)
            return emitted

        if (
            (self._lap_number == lap_number
             and (current_seconds < self._current_lap_seconds or race_seconds < self._current_race_time_seconds))
            or (self._memory_previous is not None and race_seconds < self._current_race_time_seconds)
        ):
            attempt = self._make_incomplete()
            if attempt is not None:
                emitted.append(attempt)
            self._start_session(identity, telemetry)
            self._observe_validity(sample)
            self._needs_rebaseline = True
            return emitted

        if self._lap_number is not None and lap_number == self._lap_number + 1:
            last_seconds = self._positive_finite(telemetry["last_lap_seconds"])
            # The boundary sample belongs to the NEW lap. Only its timing and
            # identity can close coverage; never apply its dirty flag backwards.
            self._clean_coverage = self._boundary_covered(sample, last_seconds)
            if self._event["event_kind"] == "circuit" and last_seconds is not None:
                emitted.append(self._make_completed(last_seconds))
                self._progress_observed = current_seconds > 0
                self._observed_partial = False
            else:
                self._progress_observed = self._progress_observed or current_seconds > 0
                self._observed_partial = True
            self._lap_number = lap_number
            self._current_lap_seconds = current_seconds
            self._reset_validity()
            self._observe_validity(sample, newly_started=True)
        elif self._lap_number is not None and lap_number > self._lap_number + 1:
            if self._dirty:
                attempt = self._make_incomplete()
                if attempt is not None:
                    emitted.append(attempt)
            self._lap_number = lap_number
            self._observed_partial = True
            self._reset_validity()
            self._observe_validity(sample)
        else:
            self._observe_validity(sample)
        self._current_lap_seconds = current_seconds
        self._current_race_time_seconds = race_seconds
        self._progress_observed = self._progress_observed or current_seconds > 0
        return emitted

    def finish(self) -> list[dict[str, Any]]:
        emitted = self._take_pending()
        attempt = self._make_incomplete()
        if attempt is not None:
            emitted.append(attempt)
        self._clear_session()
        return emitted

    def _start_session(self, identity: dict[str, Any], telemetry: dict[str, Any]) -> None:
        self._reset_validity()
        self._session = {"session_id": uuid.uuid4().hex, **identity}
        self._sequence = 0
        self._lap_number = int(telemetry["lap_number"])
        self._current_lap_seconds = self._seconds(telemetry["current_lap_seconds"])
        self._current_race_time_seconds = self._seconds(telemetry["current_race_time_seconds"])
        self._progress_observed = self._current_lap_seconds > 0
        self._observed_partial = self._current_lap_seconds > 0 or self._lap_number > 0
        self._needs_rebaseline = False

    def _clear_session(self) -> None:
        self._reset_validity()
        self._session = None
        self._sequence = 0
        self._lap_number = None
        self._current_lap_seconds = 0.0
        self._current_race_time_seconds = 0.0
        self._progress_observed = False
        self._observed_partial = False
        self._needs_rebaseline = False
        self._last_packet_key = None

    def _queue_incomplete(self) -> None:
        attempt = self._make_incomplete()
        if attempt is not None:
            self._pending.append(attempt)

    def _take_pending(self) -> list[dict[str, Any]]:
        pending, self._pending = self._pending, []
        return pending

    def _make_completed(self, raw_seconds: float) -> dict[str, Any]:
        return self._make_attempt(True, raw_seconds)

    def _make_incomplete(self) -> dict[str, Any] | None:
        if self._session is None or not self._progress_observed:
            return None
        attempt = self._make_attempt(False, None)
        self._progress_observed = False
        return attempt

    def _make_attempt(self, completed: bool, raw_seconds: float | None) -> dict[str, Any]:
        assert self._session is not None and self._event is not None
        self._sequence += 1
        validity = "invalid" if self._dirty else "clean"
        return {
            "attempt_id": uuid.uuid4().hex,
            "session_id": self._session["session_id"],
            "sequence": self._sequence,
            "profile_id": self.profile_id,
            **self._event,
            "car_ordinal": self._session["car_ordinal"],
            "car_name": self._session["car_name"],
            "car_class": self._session["car_class"],
            "performance_index": self._session["performance_index"],
            "raw_seconds": raw_seconds,
            "time_ms": round(raw_seconds * 1000) if raw_seconds is not None else None,
            "completed": completed,
            "observed_partial": self._observed_partial,
            "validity": validity,
            "validity_source": "memory_sampled" if self._dirty or completed and self._clean_coverage else "no_invalid_detected",
            "captured_at": _now(),
            **OPTIONAL_ATTEMPT_DEFAULTS,
        }

    def _reset_validity(self) -> None:
        self._dirty = False
        self._clean_coverage = False
        self._memory_previous: dict[str, Any] | None = None

    @staticmethod
    def _same_memory_source(previous: dict[str, Any], sample: dict[str, Any]) -> bool:
        return all(previous[field] == sample[field] for field in ("record_id", "car_id"))

    def _observe_validity(self, sample: dict[str, Any] | None, *, newly_started: bool = False) -> None:
        previous = self._memory_previous
        if sample is None:
            self._clean_coverage = False
            return
        self._dirty = self._dirty or sample["dirty"]
        if newly_started:
            self._clean_coverage = (
                self._current_lap_seconds <= 0.20 + 1e-9
                and sample["race_clock"] - sample["lap_start_clock"] <= 0.20 + 1e-9
            )
            if self._clean_coverage:
                self._observed_partial = False
        elif previous is None or not (
            self._same_memory_source(previous, sample)
            and previous["lap_start_clock"] == sample["lap_start_clock"]
            and 0 <= sample["race_clock"] - previous["race_clock"] <= 0.25 + 1e-9
            and 0 <= sample["udp_race_clock"] - previous["udp_race_clock"] <= 0.25 + 1e-9
        ):
            self._clean_coverage = False
        self._memory_previous = sample

    def _boundary_covered(self, sample: dict[str, Any] | None, duration: float | None) -> bool:
        previous = self._memory_previous
        return bool(self._clean_coverage and previous is not None and sample is not None
                    and duration is not None and self._same_memory_source(previous, sample)
                    and 0 <= sample["race_clock"] - previous["race_clock"] <= 0.25 + 1e-9
                    and 0 <= sample["udp_race_clock"] - previous["udp_race_clock"] <= 0.25 + 1e-9
                    and sample["lap_start_clock"] > previous["lap_start_clock"]
                    and abs(sample["lap_start_clock"] - previous["lap_start_clock"] - duration) <= 0.20 + 1e-9)

    @staticmethod
    def _seconds(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) and number >= 0 else 0.0

    @staticmethod
    def _positive_finite(value: Any) -> float | None:
        number = LapTracker._seconds(value)
        return number if number > 0 else None

    @staticmethod
    def _valid_telemetry(telemetry: dict[str, Any]) -> bool:
        required = {
            "timestamp_ms",
            "is_race_on",
            "car_ordinal",
            "car_class",
            "performance_index",
            "car_display_name",
            "current_lap_seconds",
            "last_lap_seconds",
            "lap_number",
            "current_race_time_seconds",
        }
        if not required - {"car_display_name"} <= telemetry.keys():
            return False
        lap_number = telemetry.get("lap_number")
        if not isinstance(lap_number, int) or lap_number < 0:
            return False
        try:
            if int(telemetry["car_ordinal"]) <= 0 or int(telemetry["performance_index"]) <= 0:
                return False
        except (TypeError, ValueError):
            return False
        clocks = (
            telemetry["current_lap_seconds"],
            telemetry["last_lap_seconds"],
            telemetry["current_race_time_seconds"],
        )
        try:
            return all(math.isfinite(float(value)) and float(value) >= 0 for value in clocks)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _identity(telemetry: dict[str, Any], process_id: int | None) -> dict[str, Any]:
        return {
            "process_id": process_id,
            "car_ordinal": int(telemetry["car_ordinal"]),
            "car_name": str(telemetry.get("car_display_name") or f"Car {int(telemetry['car_ordinal'])}"),
            "car_class": str(telemetry["car_class"]),
            "performance_index": int(telemetry["performance_index"]),
        }

    def _identity_changed(self, identity: dict[str, Any]) -> bool:
        assert self._session is not None
        return any(self._session[key] != value for key, value in identity.items())
