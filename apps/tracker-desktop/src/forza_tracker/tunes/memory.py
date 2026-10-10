from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import re
import struct
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from .models import OrderedTuneCard, TuneExportRecord, TuneMetadata


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
READABLE_PAGE_TYPES = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
MAX_ADDRESS = 0x7FFFFFFFFFFF
MAX_REGION_SIZE = 256 * 1024 * 1024
SCAN_CHUNK_SIZE = 8 * 1024 * 1024
SCAN_OVERLAP = 1024
ORDERED_CARD_STRIDE = 952
ORDERED_CREATOR_OFFSET = 80

DATE = struct.Struct("<8H")
FORMATTED_CODE = re.compile(rb"(?<!\d)(\d{3})[ ](\d{3})[ ](\d{3})(?!\d)")
COMPACT_CODE = re.compile(rb"(?<!\d)(\d{9})(?!\d)")
ORDERED_UTF16_CODE = re.compile(rb"(?:[0-9]\x00){9}")


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


@dataclass(frozen=True)
class MemoryRegion:
    base: int
    size: int
    state: int
    protect: int


@dataclass(frozen=True)
class OrderedTuneIdentity:
    creator: str
    car_id: int
    created_at: datetime
    share_code: str


def find_process_id(executable_name: str = "forzahorizon6.exe") -> int | None:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        while True:
            if entry.szExeFile.casefold() == executable_name.casefold():
                return int(entry.th32ProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return None
    finally:
        kernel32.CloseHandle(snapshot)


def extract_share_codes(data: bytes) -> set[str]:
    codes = _formatted_codes(data)
    codes.update(match.group(1).decode("ascii") for match in COMPACT_CODE.finditer(data))
    for alignment in (0, 1):
        utf16 = data[alignment::2]
        codes.update(_formatted_codes(utf16))
        codes.update(match.group(1).decode("ascii") for match in COMPACT_CODE.finditer(utf16))
    return codes


def decode_tune_candidates(
    data: bytes,
    creator: str,
    car_lookup: Callable[[int], str | None],
) -> list[TuneMetadata]:
    marker = creator.encode("utf-16-le")
    found: list[tuple[int, TuneMetadata]] = []
    seen_keys: set[str] = set()
    search_from = 0
    while True:
        creator_offset = data.find(marker, search_from)
        if creator_offset < 0:
            break
        search_from = creator_offset + 2
        date_offset = creator_offset - 32
        if date_offset < 2 or date_offset + DATE.size > len(data):
            continue
        created_at = _valid_date(DATE.unpack_from(data, date_offset))
        if created_at is None:
            continue
        secondary_start = _title_start(data, date_offset)
        title = _primary_title(data, secondary_start, date_offset)
        if title is None:
            try:
                title = data[secondary_start:date_offset].decode("utf-16-le").strip("\0 ")
            except UnicodeDecodeError:
                continue
        if not title or len(title) > 128 or any(not character.isprintable() for character in title):
            continue
        key_raw = data[creator_offset + 32 : creator_offset + 48]
        if len(key_raw) != 16 or key_raw == bytes(16):
            continue
        tune_key = key_raw.hex()
        if tune_key in seen_keys:
            continue
        if creator_offset + 59 > len(data):
            continue
        car_id = struct.unpack_from("<H", data, creator_offset + 57)[0]
        if car_id == 0:
            continue
        seen_keys.add(tune_key)
        found.append(
            (
                creator_offset,
                TuneMetadata(
                    creator=creator,
                    car_id=car_id,
                    car=car_lookup(car_id),
                    tune_name=title,
                    created_at=created_at,
                    tune_key=tune_key,
                ),
            )
        )
    return [metadata for _offset, metadata in sorted(found, key=lambda item: item[0])]


def decode_ordered_cards(
    data: bytes,
    creator: str,
    metadata: Iterable[TuneMetadata],
    *,
    stride: int = 952,
) -> list[OrderedTuneCard]:
    if stride < 128 or len(data) < DATE.size + stride:
        return []
    marker = creator.encode("utf-16-le")
    metadata_by_identity: dict[datetime, list[TuneMetadata]] = {}
    for item in metadata:
        if item.creator.casefold() != creator.casefold():
            continue
        metadata_by_identity.setdefault(item.created_at, []).append(item)

    count = (len(data) - DATE.size) // stride
    cards: list[OrderedTuneCard] = []
    for index in range(count):
        car_offset = DATE.size + index * stride
        if car_offset + ORDERED_CREATOR_OFFSET + len(marker) > len(data):
            return []
        created_at = _valid_date(DATE.unpack_from(data, car_offset - DATE.size))
        if created_at is None:
            return []
        car_id = struct.unpack_from("<I", data, car_offset)[0]
        try:
            share_code = data[car_offset + 20 : car_offset + 38].decode("utf-16-le")
        except UnicodeDecodeError:
            return []
        if (
            len(share_code) != 9
            or not share_code.isascii()
            or not share_code.isdigit()
            or data[
                car_offset + ORDERED_CREATOR_OFFSET :
                car_offset + ORDERED_CREATOR_OFFSET + len(marker)
            ] != marker
        ):
            return []
        matches = metadata_by_identity.get(created_at, [])
        if len(matches) != 1:
            return []
        selected = matches[0]
        cards.append(
            OrderedTuneCard(
                metadata=TuneMetadata(
                    creator=creator,
                    car_id=car_id,
                    car=selected.car if selected.car_id == car_id else None,
                    tune_name=selected.tune_name,
                    created_at=selected.created_at,
                    tune_key=selected.tune_key,
                ),
                share_code=share_code,
            )
        )
    return cards


def decode_ordered_identities(
    data: bytes,
    creator: str,
    *,
    stride: int = ORDERED_CARD_STRIDE,
) -> list[OrderedTuneIdentity]:
    if stride < 128 or len(data) < DATE.size + stride:
        return []
    marker = creator.encode("utf-16-le")
    identities: list[OrderedTuneIdentity] = []
    index = 0
    while True:
        car_offset = DATE.size + index * stride
        if car_offset + ORDERED_CREATOR_OFFSET + len(marker) > len(data):
            break
        created_at = _valid_date(DATE.unpack_from(data, car_offset - DATE.size))
        if created_at is None:
            break
        car_id = struct.unpack_from("<I", data, car_offset)[0]
        try:
            share_code = data[car_offset + 20 : car_offset + 38].decode("utf-16-le")
        except UnicodeDecodeError:
            break
        lazy_blank_share_code = share_code == "\0" * 9
        if lazy_blank_share_code:
            share_code = ""
        creator_copy = data[
            car_offset + ORDERED_CREATOR_OFFSET :
            car_offset + ORDERED_CREATOR_OFFSET + len(marker)
        ]
        terminal_blank_creator = (
            index > 0
            and creator_copy == b"\0" * len(marker)
        )
        if (
            car_id == 0
            or (
                not lazy_blank_share_code
                and (
                    len(share_code) != 9
                    or not share_code.isascii()
                    or not share_code.isdigit()
                )
            )
            or (creator_copy != marker and not terminal_blank_creator)
        ):
            break
        identities.append(
            OrderedTuneIdentity(
                creator=creator,
                car_id=car_id,
                created_at=created_at,
                share_code=share_code,
            )
        )
        if terminal_blank_creator:
            break
        index += 1
    return identities


def discover_ordered_creator(
    data: bytes,
    *,
    stride: int = ORDERED_CARD_STRIDE,
) -> tuple[str, list[OrderedTuneIdentity]] | None:
    if stride < 128 or len(data) < DATE.size + stride:
        return None
    best: tuple[str, list[OrderedTuneIdentity]] | None = None
    seen_date_offsets: set[int] = set()
    for match in ORDERED_UTF16_CODE.finditer(data):
        car_offset = match.start() - 20
        date_offset = car_offset - DATE.size
        if date_offset < 0 or date_offset in seen_date_offsets:
            continue
        seen_date_offsets.add(date_offset)
        if car_offset + ORDERED_CREATOR_OFFSET >= len(data):
            continue
        if _valid_date(DATE.unpack_from(data, date_offset)) is None:
            continue
        if struct.unpack_from("<I", data, car_offset)[0] == 0:
            continue
        creator = _decode_utf16_text_at(
            data,
            car_offset + ORDERED_CREATOR_OFFSET,
            max_characters=64,
        )
        if creator is None:
            continue
        identities = decode_ordered_identities(
            data[date_offset:],
            creator,
            stride=stride,
        )
        if len(identities) < 2:
            continue
        if best is None or len(identities) > len(best[1]):
            best = creator, identities
            continue
        if (
            len(identities) == len(best[1])
            and creator.casefold() != best[0].casefold()
        ):
            raise RuntimeError(
                "Se encontraron varios tuneadores con catálogos igual de válidos en memoria."
            )
    return best


class ProcessMemory:
    def __init__(self, executable_name: str = "forzahorizon6.exe") -> None:
        self.executable_name = executable_name
        self._handle: wintypes.HANDLE | None = None

    def __enter__(self) -> "ProcessMemory":
        pid = find_process_id(self.executable_name)
        if pid is None:
            raise RuntimeError("Forza Horizon 6 no está abierto.")
        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None

    def iter_chunks(self, priority_addresses: Iterable[int] = ()) -> Iterator[tuple[int, bytes]]:
        regions = [region for region in self._iter_regions() if _is_readable(region)]
        priorities = set(priority_addresses)
        preferred = [
            region
            for region in regions
            if any(region.base <= address < region.base + region.size for address in priorities)
        ]
        preferred_ids = {(region.base, region.size) for region in preferred}
        for region in [*preferred, *(r for r in regions if (r.base, r.size) not in preferred_ids)]:
            yield from self._read_region(region)

    def read(self, address: int, size: int) -> bytes:
        if not self._handle:
            raise RuntimeError("El proceso de Forza no está abierto.")
        if address < 0 or size <= 0:
            raise ValueError("La lectura directa requiere dirección y tamaño positivos.")
        buffer = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        ok = kernel32.ReadProcessMemory(
            self._handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(count),
        )
        if not ok or count.value != size:
            raise RuntimeError(
                f"No se pudieron leer {size} bytes de memoria en {address:#x}."
            )
        return buffer.raw[: count.value]

    def _iter_regions(self) -> Iterator[MemoryRegion]:
        if not self._handle:
            raise RuntimeError("El proceso de Forza no está abierto.")
        address = 0
        info = MEMORY_BASIC_INFORMATION()
        while address < MAX_ADDRESS:
            queried = kernel32.VirtualQueryEx(
                self._handle,
                ctypes.c_void_p(address),
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not queried:
                break
            base = int(info.BaseAddress or 0)
            size = int(info.RegionSize)
            yield MemoryRegion(base=base, size=size, state=int(info.State), protect=int(info.Protect))
            next_address = base + size
            if next_address <= address:
                break
            address = next_address

    def _read_region(self, region: MemoryRegion) -> Iterator[tuple[int, bytes]]:
        if not self._handle:
            raise RuntimeError("El proceso de Forza no está abierto.")
        offset = 0
        previous = b""
        while offset < region.size:
            requested = min(SCAN_CHUNK_SIZE, region.size - offset)
            buffer = ctypes.create_string_buffer(requested)
            count = ctypes.c_size_t()
            ok = kernel32.ReadProcessMemory(
                self._handle,
                ctypes.c_void_p(region.base + offset),
                buffer,
                requested,
                ctypes.byref(count),
            )
            if ok and count.value:
                current = buffer.raw[: count.value]
                yield region.base + offset - len(previous), previous + current
                previous = current[-SCAN_OVERLAP:]
            else:
                previous = b""
            offset += requested


class TuneMemoryReader:
    def __init__(
        self,
        car_lookup: Callable[[int], str | None],
        executable_name: str = "forzahorizon6.exe",
        *,
        chunk_source: Callable[[], Iterable[tuple[int, bytes]]] | None = None,
        direct_reader: Callable[[int, int], bytes] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._car_lookup = car_lookup
        self._executable_name = executable_name
        self._chunk_source = chunk_source
        self._direct_reader = direct_reader
        self._clock = clock
        self._sleep = sleep
        self._priority_addresses: list[int] = []
        self._creator: str | None = None
        self._excluded_keys: set[str] = set()
        self._last_formatted: set[str] = set()
        self._visible_address: int | None = None
        self._visible_size = 0
        self._visible_count = 0
        self._metadata_slices: list[tuple[int, int]] = []
        self._visible_creator: str | None = None
        self._catalog_address: int | None = None
        self._catalog_size = 0
        self._catalog_creator: str | None = None
        self._catalog_identities: list[OrderedTuneIdentity] = []
        self._catalog_metadata_slices: list[tuple[int, int]] = []
        self._known_metadata: list[TuneMetadata] = []

    def set_known_tunes(self, records: Iterable[TuneExportRecord]) -> None:
        self._known_metadata = [
            TuneMetadata(
                creator=record.creator,
                car_id=record.car_id,
                car=record.car,
                tune_name=record.tune_name,
                created_at=record.created_at,
                tune_key=record.tune_key,
            )
            for record in records
        ]

    def discover_creator(self) -> str:
        best: tuple[str, int, int] | None = None
        for address, data in self._iter_chunks():
            found = discover_ordered_creator(data)
            if found is None:
                continue
            creator, identities = found
            count = len(identities)
            if best is None or count > best[1]:
                best = creator, count, address
                continue
            if count == best[1] and creator.casefold() != best[0].casefold():
                raise RuntimeError(
                    "Se encontraron varios tuneadores con catálogos igual de válidos en memoria."
                )
        if best is None:
            raise RuntimeError("No se pudo detectar el tuneador activo en memoria.")
        self._remember_address(best[2])
        return best[0]

    def read_catalog_size(self, creator: str) -> int:
        normalized_creator = self._normalize_creator(creator)
        if (
            self._catalog_creator is None
            or self._catalog_creator.casefold() != normalized_creator.casefold()
            or self._catalog_address is None
        ):
            self._discover_catalog(normalized_creator)
        else:
            self._refresh_catalog(normalized_creator)
        return len(self._catalog_identities)

    def read_catalog_identity(
        self,
        creator: str,
        index: int,
    ) -> OrderedTuneIdentity:
        normalized_creator = self._normalize_creator(creator)
        self.read_catalog_size(normalized_creator)
        if not 0 <= index < len(self._catalog_identities):
            raise IndexError("El índice queda fuera del catálogo ordenado.")
        return self._catalog_identities[index]

    def read_selected_metadata(
        self,
        creator: str,
        index: int,
    ) -> TuneMetadata:
        normalized_creator = self._normalize_creator(creator)
        identity = self.read_catalog_identity(normalized_creator, index)
        matches = [
            item
            for item in self._read_live_catalog_metadata(normalized_creator)
            if item.created_at == identity.created_at
        ]
        if len(matches) != 1:
            matches = [
                item
                for item in self._rediscover_live_catalog_metadata(
                    normalized_creator
                )
                if item.created_at == identity.created_at
            ]
        if len(matches) != 1:
            raise RuntimeError(
                "No se encontró metadata única para el tuneo seleccionado."
            )
        selected = matches[0]
        return TuneMetadata(
            creator=identity.creator,
            car_id=identity.car_id,
            car=self._car_lookup(identity.car_id),
            tune_name=selected.tune_name,
            created_at=selected.created_at,
            tune_key=selected.tune_key,
        )

    def read_catalog_window(
        self,
        creator: str,
        *,
        start: int,
        count: int,
    ) -> list[OrderedTuneCard]:
        normalized_creator = self._normalize_creator(creator)
        if start < 0:
            raise ValueError("El inicio de la ventana no puede ser negativo.")
        if count < 1:
            raise ValueError("La ventana debe contener al menos un tuneo.")
        if (
            self._catalog_creator is None
            or self._catalog_creator.casefold() != normalized_creator.casefold()
            or self._catalog_address is None
        ):
            self._discover_catalog(normalized_creator)
        else:
            self._refresh_catalog(normalized_creator)
        end = start + count
        if end > len(self._catalog_identities):
            raise RuntimeError(
                f"La ventana {start}:{end} excede el catálogo de "
                f"{len(self._catalog_identities)} tuneos."
            )
        metadata = self._read_catalog_metadata(normalized_creator)
        return self._canonicalize_cards(
            _join_ordered_identities(
                self._catalog_identities[start:end],
                metadata,
            )
        )

    def read_visible_block(
        self,
        creator: str,
        visible_count: int = 10,
    ) -> list[OrderedTuneCard]:
        if visible_count < 1:
            raise ValueError("El bloque visible debe contener al menos un tuneo.")
        normalized_creator = creator.strip()
        if not normalized_creator:
            raise ValueError("El creador no puede estar vacío.")
        if (
            self._visible_creator is not None
            and self._visible_creator.casefold() != normalized_creator.casefold()
        ):
            self._reset_visible_cache()
        if self._visible_address is None or self._visible_count != visible_count:
            self._reset_visible_cache()
            return self._discover_visible_block(normalized_creator, visible_count)
        try:
            return self._read_cached_visible_block(normalized_creator)
        except RuntimeError:
            self._reset_visible_cache()
            return self._discover_visible_block(normalized_creator, visible_count)

    def read_batch(
        self,
        creator: str,
        excluded_keys: set[str],
        limit: int = 8,
    ) -> list[TuneMetadata]:
        self._creator = creator
        self._excluded_keys = set(excluded_keys)
        for address, data in self._iter_chunks():
            candidates = decode_tune_candidates(data, creator, self._car_lookup)
            unseen = [item for item in candidates if item.tune_key not in excluded_keys]
            if unseen:
                self._remember_address(address)
                return unseen[:limit]
        return []

    def read_selected_card(
        self,
        creator: str,
        index: int,
    ) -> OrderedTuneCard:
        normalized_creator = self._normalize_creator(creator)
        if index < 0:
            raise ValueError("El índice seleccionado no puede ser negativo.")
        if (
            self._catalog_creator is None
            or self._catalog_creator.casefold() != normalized_creator.casefold()
            or self._catalog_address is None
        ):
            self._discover_catalog(normalized_creator)
        else:
            self._refresh_catalog(normalized_creator)
        if index >= len(self._catalog_identities):
            raise IndexError("El índice queda fuera del catálogo ordenado.")
        identity = self._catalog_identities[index]
        if not identity.share_code:
            self._refresh_catalog(normalized_creator)
            if index >= len(self._catalog_identities):
                raise IndexError("El índice queda fuera del catálogo ordenado.")
            identity = self._catalog_identities[index]
        matches = [
            item
            for item in self._read_catalog_metadata(normalized_creator)
            if item.created_at == identity.created_at
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "No se encontró metadata única para el tuneo seleccionado."
            )
        metadata = matches[0]
        return OrderedTuneCard(
            metadata=TuneMetadata(
                creator=identity.creator,
                car_id=identity.car_id,
                car=self._car_lookup(identity.car_id),
                tune_name=metadata.tune_name,
                created_at=metadata.created_at,
                tune_key=metadata.tune_key,
            ),
            share_code=identity.share_code,
        )

    def read_share_code(self, creator: str, index: int) -> str:
        normalized_creator = self._normalize_creator(creator)
        if index < 0:
            raise ValueError("El índice seleccionado no puede ser negativo.")
        if (
            self._catalog_creator is None
            or self._catalog_creator.casefold() != normalized_creator.casefold()
            or self._catalog_address is None
        ):
            self._discover_catalog(normalized_creator)
        else:
            self._refresh_catalog(normalized_creator)
        if index >= len(self._catalog_identities):
            raise IndexError("El índice queda fuera del catálogo ordenado.")
        return self._catalog_identities[index].share_code

    def current_tune_key(self) -> str:
        if self._creator is None:
            raise RuntimeError("Todavía no se ha leído ningún bloque de tuneos.")
        batch = self.read_batch(self._creator, self._excluded_keys, limit=1)
        if not batch:
            raise RuntimeError("No aparece ningún tuneo nuevo seleccionado en memoria.")
        return batch[0].tune_key

    def snapshot_share_codes(self) -> set[str]:
        return set(self._last_formatted)

    def wait_for_share_code(
        self,
        previous: set[str],
        car_id: int,
        timeout: float,
    ) -> str:
        deadline = self._clock() + timeout
        while self._clock() <= deadline:
            formatted: set[str] = set()
            evidence: dict[str, set[int]] = {}
            for address, data in self._iter_chunks():
                formatted.update(_formatted_codes_all_encodings(data))
                for code, evidence_car in _compact_code_evidence(data):
                    evidence.setdefault(code, set()).add(evidence_car)
                candidates = {
                    code
                    for code in formatted - previous
                    if car_id in evidence.get(code, set())
                }
                if len(candidates) == 1:
                    self._last_formatted = formatted
                    self._remember_address(address)
                    return candidates.pop()
                if len(candidates) > 1:
                    raise RuntimeError("Aparecieron varios códigos compartidos compatibles.")
            self._sleep(0.1)
        raise TimeoutError("No apareció un código compartido nuevo para el coche seleccionado.")

    def wait_for_selection_change(self, previous_key: str, timeout: float) -> str:
        self._excluded_keys.add(previous_key)
        deadline = self._clock() + timeout
        while self._clock() <= deadline:
            try:
                current = self.current_tune_key()
            except RuntimeError:
                current = previous_key
            if current != previous_key:
                return current
            self._sleep(0.1)
        raise TimeoutError("La rueda no mostró una identidad de tuneo nueva.")

    def _iter_chunks(self) -> Iterator[tuple[int, bytes]]:
        if self._chunk_source is not None:
            yield from self._chunk_source()
            return
        with ProcessMemory(self._executable_name) as process:
            yield from process.iter_chunks(self._priority_addresses)

    def _remember_address(self, address: int) -> None:
        self._priority_addresses = [
            address,
            *(item for item in self._priority_addresses if item != address),
        ][:8]

    def _discover_visible_block(
        self,
        creator: str,
        visible_count: int,
    ) -> list[OrderedTuneCard]:
        metadata: list[TuneMetadata] = []
        seen_keys: set[str] = set()
        metadata_slices: list[tuple[int, int]] = []
        for address, data in self._iter_chunks():
            decoded = decode_tune_candidates(data, creator, self._car_lookup)
            if decoded:
                metadata_slices.append((address, len(data)))
                for item in decoded:
                    if item.tune_key not in seen_keys:
                        seen_keys.add(item.tune_key)
                        metadata.append(item)
            found = _find_ordered_block(data, creator, metadata, visible_count)
            if found is None:
                continue
            offset, cards = found
            self._visible_address = address + offset
            self._visible_size = DATE.size + ORDERED_CARD_STRIDE * visible_count
            self._visible_count = visible_count
            self._visible_creator = creator
            self._metadata_slices = metadata_slices
            return self._canonicalize_cards(cards)
        raise RuntimeError(
            f"No se encontró un carrusel ordenado de {visible_count} tuneos para {creator}."
        )

    def _discover_catalog(self, creator: str) -> None:
        metadata_slices: list[tuple[int, int]] = []
        best: tuple[int, int, list[OrderedTuneIdentity]] | None = None
        for address, data in self._iter_chunks():
            if decode_tune_candidates(data, creator, self._car_lookup):
                metadata_slices.append((address, len(data)))
            found = _find_ordered_catalog(data, creator)
            if found is None:
                continue
            offset, identities = found
            if best is None or len(identities) > len(best[2]):
                best = (address + offset, len(data) - offset, identities)
        if best is None:
            raise RuntimeError(f"No se encontró el catálogo ordenado de tuneos para {creator}.")
        self._catalog_address, self._catalog_size, self._catalog_identities = best
        self._catalog_creator = creator
        self._catalog_metadata_slices = metadata_slices

    def _refresh_catalog(self, creator: str) -> None:
        if self._catalog_address is None or self._catalog_size <= 0:
            raise RuntimeError("No existe una dirección de catálogo en caché.")
        data = self._read_direct(self._catalog_address, self._catalog_size)
        identities = decode_ordered_identities(data, creator)
        if len(identities) < 2:
            raise RuntimeError(
                "El catálogo ordenado cambió o dejó de ser legible; se conserva el JSON parcial."
            )
        self._catalog_identities = identities

    def _read_catalog_metadata(self, creator: str) -> list[TuneMetadata]:
        metadata = [
            item
            for item in self._known_metadata
            if item.creator.casefold() == creator.casefold()
        ]
        seen_keys = {item.tune_key for item in metadata}
        seen_dates = {item.created_at for item in metadata}
        for item in self._read_live_catalog_metadata(creator):
            if item.tune_key not in seen_keys and item.created_at not in seen_dates:
                seen_keys.add(item.tune_key)
                seen_dates.add(item.created_at)
                metadata.append(item)
        return metadata

    def _read_live_catalog_metadata(self, creator: str) -> list[TuneMetadata]:
        metadata: list[TuneMetadata] = []
        seen_keys: set[str] = set()
        for address, size in self._catalog_metadata_slices:
            data = self._read_direct(address, size)
            for item in decode_tune_candidates(data, creator, self._car_lookup):
                if item.tune_key not in seen_keys:
                    seen_keys.add(item.tune_key)
                    metadata.append(item)
        return metadata

    def _rediscover_live_catalog_metadata(
        self,
        creator: str,
    ) -> list[TuneMetadata]:
        metadata: list[TuneMetadata] = []
        metadata_slices: list[tuple[int, int]] = []
        seen_keys: set[str] = set()
        for address, data in self._iter_chunks():
            decoded = decode_tune_candidates(data, creator, self._car_lookup)
            if not decoded:
                continue
            metadata_slices.append((address, len(data)))
            for item in decoded:
                if item.tune_key not in seen_keys:
                    seen_keys.add(item.tune_key)
                    metadata.append(item)
        self._catalog_metadata_slices = metadata_slices
        return metadata

    def _read_cached_visible_block(self, creator: str) -> list[OrderedTuneCard]:
        if self._visible_address is None:
            raise RuntimeError("No existe una dirección de carrusel en caché.")
        metadata: list[TuneMetadata] = []
        seen_keys: set[str] = set()
        for address, size in self._metadata_slices:
            data = self._read_direct(address, size)
            for item in decode_tune_candidates(data, creator, self._car_lookup):
                if item.tune_key not in seen_keys:
                    seen_keys.add(item.tune_key)
                    metadata.append(item)
        visible = self._read_direct(self._visible_address, self._visible_size)
        cards = decode_ordered_cards(visible, creator, metadata)
        if len(cards) != self._visible_count:
            raise RuntimeError(
                "El bloque ordenado cambió sin metadatos coincidentes; se conserva el JSON parcial."
            )
        return self._canonicalize_cards(cards)

    def _read_direct(self, address: int, size: int) -> bytes:
        if self._direct_reader is not None:
            return self._direct_reader(address, size)
        with ProcessMemory(self._executable_name) as process:
            return process.read(address, size)

    def _canonicalize_cards(
        self,
        cards: Iterable[OrderedTuneCard],
    ) -> list[OrderedTuneCard]:
        return [
            OrderedTuneCard(
                metadata=TuneMetadata(
                    creator=card.metadata.creator,
                    car_id=card.metadata.car_id,
                    car=self._car_lookup(card.metadata.car_id),
                    tune_name=card.metadata.tune_name,
                    created_at=card.metadata.created_at,
                    tune_key=card.metadata.tune_key,
                ),
                share_code=card.share_code,
            )
            for card in cards
        ]

    def _reset_visible_cache(self) -> None:
        self._visible_address = None
        self._visible_size = 0
        self._visible_count = 0
        self._metadata_slices = []
        self._visible_creator = None

    @staticmethod
    def _normalize_creator(creator: str) -> str:
        normalized = creator.strip()
        if not normalized:
            raise ValueError("El creador no puede estar vacío.")
        return normalized


def _find_ordered_block(
    data: bytes,
    creator: str,
    metadata: Iterable[TuneMetadata],
    visible_count: int,
) -> tuple[int, list[OrderedTuneCard]] | None:
    marker = creator.encode("utf-16-le")
    search_from = 0
    required_size = DATE.size + ORDERED_CARD_STRIDE * visible_count
    while True:
        creator_offset = data.find(marker, search_from)
        if creator_offset < 0:
            return None
        search_from = creator_offset + 2
        date_offset = creator_offset - ORDERED_CREATOR_OFFSET - DATE.size
        if date_offset < 0 or date_offset + required_size > len(data):
            continue
        if visible_count > 1 and data[
            creator_offset + ORDERED_CARD_STRIDE :
            creator_offset + ORDERED_CARD_STRIDE + len(marker)
        ] != marker:
            continue
        block = data[date_offset : date_offset + required_size]
        cards = decode_ordered_cards(
            block,
            creator,
            metadata,
            stride=ORDERED_CARD_STRIDE,
        )
        if len(cards) == visible_count:
            return date_offset, cards


def _find_ordered_catalog(
    data: bytes,
    creator: str,
) -> tuple[int, list[OrderedTuneIdentity]] | None:
    marker = creator.encode("utf-16-le")
    search_from = 0
    best: tuple[int, list[OrderedTuneIdentity]] | None = None
    while True:
        creator_offset = data.find(marker, search_from)
        if creator_offset < 0:
            return best
        search_from = creator_offset + 2
        date_offset = creator_offset - ORDERED_CREATOR_OFFSET - DATE.size
        if date_offset < 0:
            continue
        identities = decode_ordered_identities(data[date_offset:], creator)
        if len(identities) < 2:
            continue
        if best is None or len(identities) > len(best[1]):
            best = (date_offset, identities)


def _join_ordered_identities(
    identities: Iterable[OrderedTuneIdentity],
    metadata: Iterable[TuneMetadata],
) -> list[OrderedTuneCard]:
    metadata_by_identity: dict[datetime, list[TuneMetadata]] = {}
    for item in metadata:
        metadata_by_identity.setdefault(item.created_at, []).append(item)
    cards: list[OrderedTuneCard] = []
    for identity in identities:
        matches = metadata_by_identity.get(identity.created_at, [])
        if len(matches) != 1:
            raise RuntimeError(
                "Faltan metadatos únicos para la ventana visible; se conserva el JSON parcial."
            )
        selected = matches[0]
        cards.append(
            OrderedTuneCard(
                metadata=TuneMetadata(
                    creator=identity.creator,
                    car_id=identity.car_id,
                    car=(
                        selected.car
                        if selected.car_id == identity.car_id
                        else None
                    ),
                    tune_name=selected.tune_name,
                    created_at=selected.created_at,
                    tune_key=selected.tune_key,
                ),
                share_code=identity.share_code,
            )
        )
    return cards


def _decode_utf16_text_at(
    data: bytes,
    offset: int,
    *,
    max_characters: int,
) -> str | None:
    if offset < 0 or max_characters < 1:
        return None
    end_limit = min(len(data), offset + max_characters * 2 + 2)
    end = offset
    while end + 2 <= end_limit:
        if data[end : end + 2] == b"\0\0":
            raw = data[offset:end]
            if not raw:
                return None
            try:
                value = raw.decode("utf-16-le").strip()
            except UnicodeDecodeError:
                return None
            if not value or any(not character.isprintable() for character in value):
                return None
            return value
        end += 2
    return None


def _title_start(data: bytes, date_offset: int) -> int:
    minimum = max(0, date_offset - 256)
    cursor = date_offset - 2
    while cursor >= minimum:
        if data[cursor : cursor + 2] == b"\0\0":
            return cursor + 2
        cursor -= 2
    return minimum + ((date_offset - minimum) % 2)


def _valid_date(values: tuple[int, ...]) -> datetime | None:
    year, month, reserved, day, hour, minute, second, milliseconds = values
    if reserved != 0 or not 2025 <= year <= 2035 or not 0 <= milliseconds <= 999:
        return None
    try:
        return datetime(
            year,
            month,
            day,
            hour,
            minute,
            second,
            milliseconds * 1000,
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def _primary_title(data: bytes, secondary_start: int, date_offset: int) -> str | None:
    if secondary_start < 4:
        return None
    secondary_length = (date_offset - secondary_start) // 2
    if struct.unpack_from("<I", data, secondary_start - 4)[0] != secondary_length:
        return None
    primary_end = secondary_start - 4
    for length in range(1, 129):
        primary_start = primary_end - length * 2
        if primary_start < 4:
            break
        if struct.unpack_from("<I", data, primary_start - 4)[0] != length:
            continue
        try:
            title = data[primary_start:primary_end].decode("utf-16-le").strip("\0 ")
        except UnicodeDecodeError:
            return None
        return title or None
    return None


def _formatted_codes(data: bytes) -> set[str]:
    return {b"".join(match.groups()).decode("ascii") for match in FORMATTED_CODE.finditer(data)}


def _formatted_codes_all_encodings(data: bytes) -> set[str]:
    codes = _formatted_codes(data)
    codes.update(_formatted_codes(data[0::2]))
    codes.update(_formatted_codes(data[1::2]))
    return codes


def _compact_code_evidence(data: bytes) -> Iterator[tuple[str, int]]:
    for match in COMPACT_CODE.finditer(data):
        if match.start(1) < 45:
            continue
        car_id = struct.unpack_from(">H", data, match.start(1) - 45)[0]
        if car_id:
            yield match.group(1).decode("ascii"), car_id


def _is_readable(region: MemoryRegion) -> bool:
    return (
        region.state == MEM_COMMIT
        and not region.protect & (PAGE_GUARD | PAGE_NOACCESS)
        and region.protect & 0xFF in READABLE_PAGE_TYPES
        and 1 <= region.size <= MAX_REGION_SIZE
    )
