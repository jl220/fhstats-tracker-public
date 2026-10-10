from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from .models import ExportDocument, TuneExportRecord


def append_unique(document: ExportDocument, record: TuneExportRecord) -> bool:
    if any(item.share_code == record.share_code for item in document.tunes):
        return False
    document.tunes.append(record)
    document.updated_at = datetime.now(timezone.utc)
    return True


def save_document_atomic(path: Path, document: ExportDocument) -> None:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "game": "Forza Horizon 6",
        "creator": document.creator,
        "started_at": _utc_text(document.started_at),
        "updated_at": _utc_text(document.updated_at),
        "status": document.status,
        "batch_size": document.batch_size,
        "tunes": [record.to_dict() for record in document.tunes],
        "error": document.error,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_document(path: Path) -> ExportDocument:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("game") != "Forza Horizon 6":
        raise ValueError("Documento de tuneos incompatible.")
    document = ExportDocument(
        creator=str(data["creator"]),
        batch_size=int(data["batch_size"]),
        started_at=_parse_datetime(data["started_at"]),
        updated_at=_parse_datetime(data["updated_at"]),
        status=str(data["status"]),
        error=data.get("error"),
    )
    seen: set[str] = set()
    for item in data["tunes"]:
        record = TuneExportRecord(
            creator=str(item["creator"]),
            car_id=int(item["car_id"]),
            car=str(item["car"]) if item.get("car") is not None else None,
            tune_name=str(item["tune_name"]),
            share_code=str(item["share_code"]),
            created_at=_parse_datetime(item["created_at"]),
            performance_index=int(item["performance_index"]),
            tune_key=str(item["tune_key"]),
        )
        if item.get("class") != record.performance_class:
            raise ValueError("Clase y PI no coinciden en el documento guardado.")
        if record.share_code in seen:
            raise ValueError("El documento contiene tuneos duplicados.")
        seen.add(record.share_code)
        document.tunes.append(record)
    return document


def _parse_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("La fecha guardada no incluye zona horaria.")
    return parsed


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
