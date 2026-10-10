from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

from .models import ExportDocument, OrderedTuneCard, TuneExportRecord
from .storage import append_unique, load_document, save_document_atomic


class MemoryReader(Protocol):
    def set_known_tunes(self, records: Iterable[TuneExportRecord]) -> None: ...
    def read_selected_card(
        self,
        creator: str,
        index: int,
    ) -> OrderedTuneCard: ...
    def read_share_code(self, creator: str, index: int) -> str: ...


class PiReader(Protocol):
    def read(self, tune_key: str, identity: Callable[[], str]) -> int: ...


class WindowController(Protocol):
    def ensure_ready(self) -> None: ...
    def cancel_requested(self) -> bool: ...
    def scroll_tunes(self, step: int = -120) -> None: ...
    def selection_fingerprint(self) -> bytes: ...
    def selection_matches(self, expected: bytes) -> bool: ...
    def wait_for_selection_change(self, previous: bytes, timeout: float) -> bytes: ...


class TuneCollector:
    def __init__(
        self,
        *,
        memory: MemoryReader,
        pi_reader: PiReader,
        window: WindowController,
        save: Callable[[Path, ExportDocument], None] = save_document_atomic,
        load: Callable[[Path], ExportDocument] = load_document,
        batch_size: int = 8,
        wheel_step: int = -120,
        advance_attempts: int = 3,
        advance_timeout: float = 2.0,
        progress: Callable[[int], None] | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("El tamaño de bloque debe ser positivo.")
        if advance_attempts < 1:
            raise ValueError("El avance debe intentarse al menos una vez.")
        if advance_timeout < 0:
            raise ValueError("La espera de avance no puede ser negativa.")
        self.memory = memory
        self.pi_reader = pi_reader
        self.window = window
        self._save = save
        self._load = load
        self._batch_size = batch_size
        self._wheel_step = wheel_step
        self._advance_attempts = advance_attempts
        self._advance_timeout = advance_timeout
        self._progress = progress

    def run(self, creator: str, output: Path, limit: int = 0) -> ExportDocument:
        if limit < 0:
            raise ValueError("El límite no puede ser negativo.")
        document = self._load(output) if output.exists() else ExportDocument.start(
            creator, self._batch_size
        )
        self._validate_resume(document, creator)
        self.memory.set_known_tunes(document.tunes)
        document.status = "running"
        document.error = None
        seen_codes = {record.share_code for record in document.tunes}
        pending: dict[int, tuple[OrderedTuneCard, int]] = {}
        selection_index = 0
        self.window.ensure_ready()
        try:
            self._report_progress(document)
            while True:
                self._flush_pending(
                    creator,
                    pending,
                    document,
                    seen_codes,
                    output,
                )
                if self.window.cancel_requested():
                    document.status = "cancelled"
                    break

                selected_fingerprint = self.window.selection_fingerprint()
                card = self.memory.read_selected_card(creator, selection_index)
                metadata = card.metadata
                if card.share_code not in seen_codes and selection_index not in pending:
                    performance_index = self.pi_reader.read(
                        metadata.tune_key,
                        lambda: (
                            metadata.tune_key
                            if self.window.selection_matches(selected_fingerprint)
                            else ""
                        ),
                    )
                    if card.share_code:
                        self._append_record(
                            card,
                            performance_index,
                            document,
                            seen_codes,
                            output,
                        )
                    else:
                        pending[selection_index] = (card, performance_index)

                if limit and len(document.tunes) >= limit:
                    document.status = "complete"
                    break
                if not self._advance(selected_fingerprint):
                    self._flush_pending(
                        creator,
                        pending,
                        document,
                        seen_codes,
                        output,
                    )
                    if pending:
                        raise RuntimeError(
                            f"Forza dejó {len(pending)} códigos pendientes de cargar."
                        )
                    document.status = "complete"
                    break
                selection_index += 1
        except KeyboardInterrupt:
            document.status = "cancelled"
            document.error = None
        except Exception as error:
            document.status = "error"
            document.error = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            self._save(output, document)
        return document

    def _flush_pending(
        self,
        creator: str,
        pending: dict[int, tuple[OrderedTuneCard, int]],
        document: ExportDocument,
        seen_codes: set[str],
        output: Path,
    ) -> None:
        for index in sorted(pending):
            card, performance_index = pending[index]
            share_code = card.share_code or self.memory.read_share_code(creator, index)
            if not share_code:
                continue
            loaded_card = OrderedTuneCard(
                metadata=card.metadata,
                share_code=share_code,
            )
            self._append_record(
                loaded_card,
                performance_index,
                document,
                seen_codes,
                output,
            )
            pending.pop(index)

    def _append_record(
        self,
        card: OrderedTuneCard,
        performance_index: int,
        document: ExportDocument,
        seen_codes: set[str],
        output: Path,
    ) -> None:
        if card.share_code in seen_codes:
            return
        metadata = card.metadata
        record = TuneExportRecord(
            creator=metadata.creator,
            car_id=metadata.car_id,
            car=metadata.car,
            tune_name=metadata.tune_name,
            share_code=card.share_code,
            created_at=metadata.created_at,
            performance_index=performance_index,
            tune_key=metadata.tune_key,
        )
        if append_unique(document, record):
            seen_codes.add(card.share_code)
            self._save(output, document)
            self._report_progress(document)

    def _advance(self, previous_fingerprint: bytes) -> bool:
        for _attempt in range(self._advance_attempts):
            if self.window.cancel_requested():
                raise KeyboardInterrupt
            self.window.scroll_tunes(step=self._wheel_step)
            try:
                self.window.wait_for_selection_change(
                    previous_fingerprint,
                    timeout=self._advance_timeout,
                )
            except TimeoutError:
                continue
            return True
        return False

    def _report_progress(self, document: ExportDocument) -> None:
        if self._progress is not None:
            self._progress(len(document.tunes))

    def _validate_resume(self, document: ExportDocument, creator: str) -> None:
        if document.creator.casefold() != creator.strip().casefold():
            raise ValueError("El JSON existente pertenece a otro creador.")
        if document.batch_size != self._batch_size:
            raise ValueError("El JSON existente usa otro tamaño de bloque.")
