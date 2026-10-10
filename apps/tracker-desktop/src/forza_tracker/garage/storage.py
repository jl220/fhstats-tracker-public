from __future__ import annotations

import json
import math
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from forza_tracker.garage.models import GarageSnapshot, ScanOptions


SlotName = Literal["draft", "current", "backup"]


class GarageStoreError(RuntimeError):
    pass


class GarageInvalidDraftError(GarageStoreError):
    pass


class GarageCountDropWarning(GarageStoreError):
    def __init__(self, current_count: int, draft_count: int) -> None:
        self.current_count = current_count
        self.draft_count = draft_count
        super().__init__(
            f"El garaje ha bajado de {current_count} a {draft_count} coches; confirma para sustituir la copia actual."
        )


class GarageStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.draft_path = self._child("draft")
        self.current_path = self._child("current")
        self.backup_path = self._child("backup")
        self.retiring_path = self._child(".retiring")
        self.journal_path = self._child(".rotation.json")
        self.recover_rotation()

    def _child(self, name: str) -> Path:
        path = (self.root / name).resolve()
        if path.parent != self.root:
            raise ValueError("Ruta de garaje fuera del directorio permitido.")
        return path

    def create_draft(self, options: ScanOptions) -> GarageSnapshot:
        if self.draft_path.exists():
            raise GarageInvalidDraftError(
                "Ya existe un escaneo provisional. Debe terminarse o descartarse antes de iniciar otro."
            )
        snapshot = GarageSnapshot(
            scan_id=uuid.uuid4().hex,
            created_at=datetime.now(UTC).isoformat(),
            completed_at=None,
            completed=False,
            options=options,
        )
        self.draft_path.mkdir(parents=True)
        self.write_draft(snapshot)
        return snapshot

    def write_draft(self, snapshot: GarageSnapshot) -> None:
        self.draft_path.mkdir(parents=True, exist_ok=True)
        existing = self.load_slot("draft")
        if existing is not None and existing.scan_id != snapshot.scan_id:
            raise GarageInvalidDraftError("El manifiesto no pertenece al escaneo provisional activo.")
        self._write_manifest(self.draft_path, snapshot)

    def load_slot(self, slot: SlotName) -> GarageSnapshot | None:
        path = {
            "draft": self.draft_path,
            "current": self.current_path,
            "backup": self.backup_path,
        }[slot]
        return self._load_path(path)

    def discard_draft(self) -> None:
        if self.draft_path.exists():
            shutil.rmtree(self.draft_path)

    def write_current(self, snapshot: GarageSnapshot) -> None:
        current = self.load_slot("current")
        if current is None or current.scan_id != snapshot.scan_id:
            raise GarageStoreError("La copia actual cambio antes de guardar la correccion.")
        self._write_manifest(self.current_path, snapshot)

    def promote_draft(self, *, confirm_count_drop: bool = False) -> GarageSnapshot:
        draft = self.load_slot("draft")
        if draft is None or not draft.completed or not draft.completed_at:
            raise GarageInvalidDraftError("El escaneo provisional no esta completo.")
        if draft.observed_count != sum(car.quantity for car in draft.cars):
            raise GarageInvalidDraftError("El recuento del escaneo no coincide con su manifiesto.")

        current = self.load_slot("current")
        if current is not None and not confirm_count_drop:
            decrease = current.observed_count - draft.observed_count
            threshold = max(10, math.ceil(current.observed_count * 0.20))
            if decrease >= threshold:
                raise GarageCountDropWarning(current.observed_count, draft.observed_count)

        self._write_journal("prepared")
        if self.retiring_path.exists():
            raise GarageStoreError("Existe una rotacion anterior sin recuperar.")
        if self.backup_path.exists():
            self.backup_path.replace(self.retiring_path)
        self._write_journal("backup_retired")
        if self.current_path.exists():
            self.current_path.replace(self.backup_path)
        self._write_journal("current_backed_up")
        self.draft_path.replace(self.current_path)
        self._write_journal("draft_promoted")

        promoted = self._load_path(self.current_path)
        if promoted is None or promoted.scan_id != draft.scan_id:
            raise GarageStoreError("No se pudo validar la nueva copia del garaje.")
        if self.retiring_path.exists():
            shutil.rmtree(self.retiring_path)
        self.journal_path.unlink(missing_ok=True)
        return promoted

    def recover_rotation(self) -> None:
        if not self.journal_path.exists():
            return

        draft = self._load_path(self.draft_path)
        current = self._load_path(self.current_path)
        backup = self._load_path(self.backup_path)
        retiring = self._load_path(self.retiring_path)

        if current is None and draft is not None:
            self.draft_path.replace(self.current_path)
            current = draft
            draft = None
        elif current is None and backup is not None:
            self.backup_path.replace(self.current_path)
            current = backup
            backup = None

        if backup is None and retiring is not None:
            self.retiring_path.replace(self.backup_path)
            retiring = None

        if current is None and backup is None and retiring is None and draft is None:
            raise GarageStoreError("No queda ningun manifiesto valido para recuperar la rotacion.")
        if retiring is not None and current is not None and backup is not None:
            shutil.rmtree(self.retiring_path)
        self.journal_path.unlink(missing_ok=True)

    def _load_path(self, directory: Path) -> GarageSnapshot | None:
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            return None
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            return GarageSnapshot.from_dict(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_manifest(directory: Path, snapshot: GarageSnapshot) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "manifest.json"
        temporary = directory / "manifest.json.tmp"
        temporary.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)

    def _write_journal(self, phase: str) -> None:
        temporary = self._child(".rotation.json.tmp")
        temporary.write_text(
            json.dumps({"phase": phase, "updated_at": datetime.now(UTC).isoformat()}, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.journal_path)
