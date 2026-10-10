"""Guarded, read-only detection of the FH6 Rivals context.

The addresses in this module are RVAs for one explicitly supported executable
build.  Discovery deliberately fails closed when any identifying evidence no
longer matches.
"""

from __future__ import annotations

import ctypes
import math
import os
import re
import struct
import threading
import time
from ctypes import wintypes
from pathlib import Path, PureWindowsPath
from typing import Iterable, Protocol


PROCESS_ACCESS = 0x0410  # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
EXPECTED_BUILD = "3.440.853.0"
PROCESS_NAME = "forzahorizon6.exe"

GETTER_RVA = 0x01EFDC70
GETTER_SIGNATURE = bytes.fromhex("4883b940040000000f95c0c3")
PROPERTY_RVA = 0x069E61A8
PROPERTY_NAME = b"IsInRivalsMenuEvent"
CONSTRUCTOR_LEA_RVAS = (0x01EF8BDF, 0x01EF8BE9, 0x01EF8BF4)
EXPECTED_VTABLE_RVAS = (0x069E67C8, 0x069E68A8, 0x069E68F8)
VTABLE_MEMBER_OFFSETS = (0, 8, 0x38)
CONTEXT_OFFSET = 0x440

# Independently validated detail paths for build 3.440.853.0.
EVENT_VTABLE_RVA = 0x063E8E90
EVENT_SIZE = 0xD0
EVENT_ROUTE_OFFSET = 0x18
EVENT_NAME_OBJECT_OFFSET = 0xB8
MAX_EVENT_NAME_BYTES = 512

RACE_MANAGER_INSTRUCTION_RVA = 0x040845F5
RACE_MANAGER_INSTRUCTION = bytes.fromhex("488b0d44488206")
RECORD_CLEAN_GETTER_RVA = 0x028FFAD0
RECORD_CLEAN_GETTER_SIGNATURE = bytes.fromhex(
    "488b411880b8a490000000752780b8a790000000751e80b8a8900000007515"
    "80b8a990000000750c80b8aa900000007503b001c332c0c3"
)
RACE_VECTOR_MAX_RECORDS = 100
VALIDITY_CLOCK_MAX_ADVANCE = 0.2
DIRTY_FLAG_INDICES = (0, 3, 4, 5, 6)

# Exact checkpoint code evidence for the supported image; never execute getters.
CHECKPOINT_SIGNATURES = (
    (0x28f41b0, bytes.fromhex("488b010f28c1f20f5c4170f20f1189a0000000f20f5ac9f20f1181a800000048ff6078")),
    (0x28f5c20, bytes.fromhex("ff81e00000004c8bc98b91d80000000f57c0448b81d0000000440381d4000000418bc02bc2443bc2f30f5ac1440f4dc0498b81c80000004963c8f20f1104c8418b91d8000000418b81d40000003bc27d0affc0418981d4000000c3418b89d000000033c0ffc13bca0f4cc1418981d0000000c3")),
    (0x2a242a0, bytes.fromhex("488b81f08f0000c3")),
    (0x28f5f47, bytes.fromhex("488b070f28cf488bcfff9098000000")),
    (0x28f5e68, bytes.fromhex("e833e4120033d2488bf0488b88a0000000e8a21a9e02488b8ea000000033d2f30f10b698000000e830139e02f20f10bf38010000488b4f18f2410f5cf80f5ac6f20f59f8f2410f58f8e84a681300")),
    (0x28f6e29, bytes.fromhex("488b070f28cf488bcfff9098000000")),
    (0x28f2639, bytes.fromhex("4c89aed0000000c786e0000000ffffffff")),
)

_SCAN_CHUNK = 1024 * 1024
_SCAN_LIMIT = 16 * 1024 * 1024 * 1024
_SCAN_SECONDS = 30.0
_RETRY_SECONDS = 30.0
_PACKAGE_DIRECTORY = re.compile(
    r"^Microsoft\.ForteBaseGame_(\d+\.\d+\.\d+\.\d+)_x64__8wekyb3d8bbwe$",
    re.IGNORECASE,
)


def _package_version_from_image_path(path: str) -> str | None:
    image = PureWindowsPath(path)
    if image.name.lower() != PROCESS_NAME or len(image.parts) < 3:
        return None
    if image.parts[-3].lower() != "windowsapps":
        return None
    match = _PACKAGE_DIRECTORY.fullmatch(image.parent.name)
    return match.group(1) if match else None


def _configure_kernel32(api) -> None:
    """Declare pointer-sized Win32 signatures before any process access."""
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW", "Module32FirstW"):
        function = getattr(api, name)
        function.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        function.restype = wintypes.BOOL
    api.VirtualQueryEx.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t
    ]
    api.VirtualQueryEx.restype = ctypes.c_size_t
    api.ReadProcessMemory.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    api.ReadProcessMemory.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.GetExitCodeProcess.restype = wintypes.BOOL


def _configure_version(api) -> None:
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.GetFileVersionInfoW.restype = wintypes.BOOL
    api.VerQueryValueW.argtypes = [
        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    api.VerQueryValueW.restype = wintypes.BOOL


class _Backend(Protocol):
    def find_process(self) -> tuple[int, int, str] | None: ...
    def file_version(self, path: str) -> str | None: ...
    def open_process(self, pid: int, access: int): ...
    def process_alive(self, handle) -> bool: ...
    def close_handle(self, handle) -> None: ...
    def read(self, handle, address: int, size: int) -> bytes: ...
    def writable_private_regions(self, handle) -> Iterable[tuple[int, int, bool]]: ...


def _derive_vtables(backend: _Backend, handle, module_base: int) -> tuple[int, int, int] | None:
    targets: list[int] = []
    for rva in CONSTRUCTOR_LEA_RVAS:
        instruction = backend.read(handle, module_base + rva, 7)
        if len(instruction) != 7 or instruction[:3] != b"\x48\x8d\x05":
            return None
        displacement = struct.unpack("<i", instruction[3:])[0]
        target = module_base + rva + 7 + displacement
        expected = module_base + EXPECTED_VTABLE_RVAS[len(targets)]
        if target != expected:
            return None
        targets.append(target)
    return tuple(targets)  # type: ignore[return-value]


class RivalsMemoryReader:
    """Read the supported FH6 process without writing or injecting code."""

    def __init__(
        self,
        *,
        backend: _Backend | None = None,
        scan_byte_limit: int = _SCAN_LIMIT,
        scan_seconds: float = _SCAN_SECONDS,
        retry_seconds: float = _RETRY_SECONDS,
    ) -> None:
        self._backend = backend or _WindowsBackend()
        self._scan_byte_limit = scan_byte_limit
        self._scan_seconds = scan_seconds
        self._retry_seconds = retry_seconds
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self._handle = None
        self._pid: int | None = None
        self._base: int | None = None
        self._instance: int | None = None
        self._vtables: tuple[int, int, int] | None = None
        self._retry_at = 0.0
        self._failure_status = "scan_failed"

    def sample(self) -> dict:
        if self._cancel.is_set():
            return self._result(None, None, "closed")
        with self._lock:
            if self._cancel.is_set():
                return self._result(None, None, "closed")
            if self._handle is None and self._pid is not None and time.monotonic() < self._retry_at:
                return self._result(None, self._pid, self._failure_status)
            try:
                if self._handle is not None and self._pid is not None and self._base is not None:
                    if not self._backend.process_alive(self._handle):
                        self._release_locked()
                        return self._result(None, None, "process_exit")
                    process = (self._pid, self._base, "")
                else:
                    process = self._backend.find_process()
            except OSError:
                return self._unknown("read_error")
            if process is None:
                self._release_locked()
                return self._result(None, None, "process_exit")

            pid, base, image_path = process
            if pid != self._pid or base != self._base or self._handle is None:
                self._release_locked()
                self._pid, self._base = pid, base
                self._retry_at = 0.0
                try:
                    if self._backend.file_version(image_path) != EXPECTED_BUILD:
                        self._failure_status = "unsupported"
                        self._retry_at = time.monotonic() + self._retry_seconds
                        return self._unknown("unsupported", pid)
                    self._handle = self._backend.open_process(pid, PROCESS_ACCESS)
                    if not self._handle:
                        return self._unknown("read_error", pid)
                    if not self._validate_image_locked():
                        self._failure_status = "unsupported"
                        self._retry_at = time.monotonic() + self._retry_seconds
                        self._release_locked(keep_process=True)
                        return self._result(None, pid, "unsupported")
                except OSError:
                    self._release_locked(keep_process=True)
                    return self._unknown("read_error", pid)

            if self._handle is None:
                return self._unknown("read_error", pid)

            try:
                if self._instance is not None and self._validate_instance_locked(self._instance):
                    return self._read_context_locked(self._instance)
                self._instance = None
                if time.monotonic() < self._retry_at:
                    return self._unknown("scan_failed", pid)
                instance = self._scan_locked()
                if instance is None:
                    self._failure_status = "scan_failed"
                    self._retry_at = time.monotonic() + self._retry_seconds
                    status = "closed" if self._cancel.is_set() else "scan_failed"
                    return self._unknown(status, pid if status != "closed" else None)
                self._instance = instance
                return self._read_context_locked(instance)
            except OSError:
                self._instance = None
                return self._unknown("read_error", pid)

    def close(self) -> None:
        self._cancel.set()
        with self._lock:
            self._release_locked()

    @staticmethod
    def _result(active: bool | None, process_id: int | None, status: str) -> dict:
        return {
            "active": active,
            "process_id": process_id,
            "status": status,
            "event": None,
            "event_status": status,
            "validity": None,
            "validity_status": status,
        }

    def _unknown(self, status: str, process_id: int | None = None) -> dict:
        return self._result(None, self._pid if process_id is None else process_id, status)

    def _release_locked(self, *, keep_process: bool = False) -> None:
        if self._handle is not None:
            self._backend.close_handle(self._handle)
        self._handle = None
        self._instance = None
        self._vtables = None
        if not keep_process:
            self._pid = None
            self._base = None

    def _validate_image_locked(self) -> bool:
        assert self._handle is not None and self._base is not None
        getter = self._backend.read(self._handle, self._base + GETTER_RVA, len(GETTER_SIGNATURE))
        if getter != GETTER_SIGNATURE:
            return False
        prop = self._backend.read(self._handle, self._base + PROPERTY_RVA, len(PROPERTY_NAME) + 1)
        if prop != PROPERTY_NAME + b"\0":
            return False
        self._vtables = _derive_vtables(self._backend, self._handle, self._base)
        return self._vtables is not None

    def _validate_instance_locked(self, address: int) -> bool:
        assert self._handle is not None and self._vtables is not None
        for offset, expected in zip(VTABLE_MEMBER_OFFSETS, self._vtables):
            if self._read_qword(address + offset) != expected:
                return False
        return True

    def _read_qword(self, address: int) -> int:
        assert self._handle is not None
        value = self._backend.read(self._handle, address, 8)
        if len(value) != 8:
            raise OSError("short process-memory read")
        return struct.unpack("<Q", value)[0]

    def _read_context_locked(self, instance: int) -> dict:
        value = self._read_qword(instance + CONTEXT_OFFSET)
        result = self._result(bool(value), self._pid, "active" if value else "inactive")
        if value:
            result["event"], result["event_status"] = self._read_event_detail_locked(instance, value)
        else:
            result["event_status"] = "inactive"
        result["validity"], result["validity_status"] = self._read_validity_detail_locked()
        if self._read_qword(instance + CONTEXT_OFFSET) != value:
            return self._result(None, self._pid, "read_error")
        if result["event"] is not None:
            repeated_event = self._backend.read(self._handle, value, 0x1C)
            if (len(repeated_event) != 0x1C
                    or struct.unpack_from("<Q", repeated_event)[0] != self._base + EVENT_VTABLE_RVA
                    or struct.unpack_from("<I", repeated_event, EVENT_ROUTE_OFFSET)[0] & 0x00FFFFFF
                    != result["event"]["route_id"]):
                return self._result(None, self._pid, "read_error")
        return result

    @staticmethod
    def _valid_pointer(value: int, *, aligned: bool = True) -> bool:
        return 0x10000 <= value < (1 << 47) and (not aligned or value % 8 == 0)

    def _read_event_detail_locked(self, instance: int, event: int) -> tuple[dict | None, str]:
        assert self._base is not None
        try:
            if not self._valid_pointer(event):
                return None, "read_error"
            block = self._backend.read(self._handle, event, EVENT_SIZE)
            if len(block) != EVENT_SIZE:
                raise OSError("short event read")
            if struct.unpack_from("<Q", block)[0] != self._base + EVENT_VTABLE_RVA:
                return None, "unsupported"
            route_id = struct.unpack_from("<I", block, EVENT_ROUTE_OFFSET)[0] & 0x00FFFFFF
            name_object = struct.unpack_from("<Q", block, EVENT_NAME_OBJECT_OFFSET)[0]
            if not route_id or not self._valid_pointer(name_object):
                return None, "read_error"
            name_header = self._backend.read(self._handle, name_object + 0x20, 12)
            if len(name_header) != 12:
                raise OSError("short event-name header read")
            text_pointer, encoded_length = struct.unpack("<QI", name_header)
            length = encoded_length & 0x7FFFFFFF
            if not self._valid_pointer(text_pointer, aligned=False) or not 0 < length <= MAX_EVENT_NAME_BYTES:
                return None, "read_error"
            raw = self._backend.read(self._handle, text_pointer, length)
            if len(raw) != length:
                raise OSError("short event-name read")
            name = raw.decode("utf-8", errors="strict")
            if not name or self._read_qword(instance + CONTEXT_OFFSET) != event:
                return None, "read_error"
            if self._read_qword(event) != self._base + EVENT_VTABLE_RVA:
                return None, "read_error"
            return {"route_id": route_id, "name": name, "instance_id": hex(event)}, "available"
        except (OSError, UnicodeDecodeError, struct.error):
            return None, "read_error"

    def _read_validity_detail_locked(self) -> tuple[dict | None, str]:
        assert self._base is not None
        try:
            instruction_address = self._base + RACE_MANAGER_INSTRUCTION_RVA
            instruction = self._backend.read(self._handle, instruction_address, 7)
            if instruction != RACE_MANAGER_INSTRUCTION:
                return None, "unsupported"
            getter = self._backend.read(
                self._handle, self._base + RECORD_CLEAN_GETTER_RVA,
                len(RECORD_CLEAN_GETTER_SIGNATURE),
            )
            if getter != RECORD_CLEAN_GETTER_SIGNATURE:
                return None, "unsupported"
            slot = instruction_address + 7 + struct.unpack("<i", instruction[3:])[0]
            manager = self._read_qword(slot)
            if not self._valid_pointer(manager):
                return None, "read_error"
            bounds = self._backend.read(self._handle, manager + 0x70, 16)
            if len(bounds) != 16:
                raise OSError("short race-vector read")
            begin, end = struct.unpack("<QQ", bounds)
            span = end - begin
            if end < begin or span % 8 or span > RACE_VECTOR_MAX_RECORDS * 8:
                return None, "read_error"
            if span == 0:
                return None, "unavailable"
            if not self._valid_pointer(begin):
                return None, "read_error"
            entries = self._backend.read(self._handle, begin, span)
            if len(entries) != span:
                raise OSError("short race-record vector read")
            candidates: list[tuple] = []
            for index, (record,) in enumerate(struct.iter_unpack("<Q", entries)):
                if not self._valid_pointer(record):
                    return None, "read_error"
                record_data = self._backend.read(self._handle, record, 0x140)
                if len(record_data) != 0x140:
                    raise OSError("short race-record read")
                vtable = struct.unpack_from("<Q", record_data)[0]
                car = struct.unpack_from("<Q", record_data, 0x18)[0]
                if not self._valid_pointer(vtable) or not self._valid_pointer(car):
                    return None, "read_error"
                function = self._read_qword(vtable + 0x38)
                if function != self._base + RECORD_CLEAN_GETTER_RVA:
                    continue
                race_clock = struct.unpack_from("<d", record_data, 0x138)[0]
                lap_start_clock = struct.unpack_from("<d", record_data, 0x70)[0]
                if (not math.isfinite(race_clock) or not math.isfinite(lap_start_clock)
                        or race_clock < lap_start_clock):
                    return None, "read_error"
                flags = self._backend.read(self._handle, car + 0x90A4, 7)
                if len(flags) != 7:
                    raise OSError("short current-lap flags read")
                repeated_lap_start = struct.unpack(
                    "<d", self._backend.read(self._handle, record + 0x70, 8)
                )[0]
                repeated_race = struct.unpack(
                    "<d", self._backend.read(self._handle, record + 0x138, 8)
                )[0]
                if (not math.isfinite(repeated_race)
                        or not math.isfinite(repeated_lap_start)
                        or repeated_lap_start != lap_start_clock
                        or repeated_race < race_clock
                        or repeated_race - race_clock > VALIDITY_CLOCK_MAX_ADVANCE
                        or repeated_race < repeated_lap_start):
                    return None, "read_error"
                candidates.append((begin + index * 8, record, vtable, car, repeated_race,
                                   repeated_lap_start,
                                   any(flags[flag_index] for flag_index in DIRTY_FLAG_INDICES),
                                   record_data, flags))
            if len(candidates) != 1:
                return None, "unavailable"
            (entry_address, record, vtable, car, race_clock, lap_start_clock, dirty,
             record_data, flags) = candidates[0]
            checkpoints, checkpoint_status = self._read_checkpoints_locked(
                record, vtable, record_data, flags, race_clock
            )
            if self._read_qword(slot) != manager:
                return None, "read_error"
            repeated_bounds = self._backend.read(self._handle, manager + 0x70, 16)
            if (repeated_bounds != bounds
                    or self._backend.read(self._handle, begin, span) != entries
                    or self._read_qword(entry_address) != record):
                return None, "read_error"
            if self._read_qword(record) != vtable or self._read_qword(record + 0x18) != car:
                return None, "read_error"
            if self._read_qword(vtable + 0x38) != self._base + RECORD_CLEAN_GETTER_RVA:
                return None, "read_error"
            return {
                "checkpoints": checkpoints,
                "checkpoint_status": checkpoint_status,
                "dirty": dirty,
                "race_clock": race_clock,
                "lap_start_clock": lap_start_clock,
                "record_id": hex(record),
                "car_id": hex(car),
            }, "available"
        except (OSError, struct.error):
            return None, "read_error"

    def _read_checkpoints_locked(
        self, record: int, vtable: int, first: bytes, flags: bytes, race_clock: float,
    ) -> tuple[dict | None, str]:
        """Optional bounded observation, bracketed by existing ownership checks.

        Repeated crossing fields must match even when the counter has not moved:
        the setter writes timestamps before incrementing the counter. Temporal
        transitions between samples are validated by the sector consumer.
        """
        assert self._base is not None
        try:
            for offset, rva in ((0x98, 0x28F41B0), (0x78, 0x28F5C20)):
                if self._read_qword(vtable + offset) != self._base + rva:
                    return None, "unsupported"
            for rva, signature in CHECKPOINT_SIGNATURES:
                if self._backend.read(self._handle, self._base + rva, len(signature)) != signature:
                    return None, "unsupported"
            repeated = self._backend.read(self._handle, record, 0x140)
            if len(repeated) != 0x140:
                return None, "read_error"
            for offset, size in ((0, 8), (0x18, 8), (0x70, 8), (0x8C, 4),
                                 (0xA0, 16), (0xE0, 4)):
                if first[offset:offset + size] != repeated[offset:offset + size]:
                    return None, "unavailable"
            crossing = struct.unpack_from("<i", first, 0xE0)[0]
            lap = struct.unpack_from("<i", first, 0x8C)[0]
            absolute, elapsed = struct.unpack_from("<dd", first, 0xA0)
            start = struct.unpack_from("<d", first, 0x70)[0]
            repeated_race = struct.unpack_from("<d", repeated, 0x138)[0]
            if (crossing < -1 or lap < 0
                    or any(not math.isfinite(value) or value < 0
                           for value in (absolute, elapsed, start, race_clock, repeated_race))
                    or absolute > race_clock or elapsed > absolute
                    or repeated_race < race_clock
                    or repeated_race - race_clock > VALIDITY_CLOCK_MAX_ADVANCE):
                return None, "read_error"
            return {
                "crossing_index": crossing,
                "lap_index": lap,
                "crossing_race_clock": absolute,
                "crossing_lap_clock": elapsed,
                "rewind_active": bool(flags[5]),
                "rewind_affected": bool(flags[4]),
            }, "available"
        except (OSError, struct.error):
            return None, "read_error"

    def _scan_locked(self) -> int | None:
        assert self._handle is not None and self._vtables is not None
        started = time.monotonic()
        scanned = 0
        matches: list[int] = []
        needle = struct.pack("<Q", self._vtables[0])

        for region_start, region_size, eligible in self._backend.writable_private_regions(self._handle):
            if self._cancel.is_set() or time.monotonic() - started >= self._scan_seconds:
                return None
            if not eligible:
                continue
            offset = 0
            while offset < region_size:
                if self._cancel.is_set() or scanned >= self._scan_byte_limit:
                    return None
                if time.monotonic() - started >= self._scan_seconds:
                    return None
                size = min(_SCAN_CHUNK, region_size - offset, self._scan_byte_limit - scanned)
                try:
                    data = self._backend.read(self._handle, region_start + offset, size)
                except OSError:
                    offset += size
                    scanned += size
                    continue
                scanned += len(data)
                cursor = data.find(needle)
                while cursor >= 0:
                    candidate = region_start + offset + cursor
                    if candidate % 8 == 0 and self._validate_instance_locked(candidate):
                        matches.append(candidate)
                        if len(matches) > 1:
                            return None
                    cursor = data.find(needle, cursor + 8)
                offset += size
        return matches[0] if len(matches) == 1 else None


class _WindowsBackend:
    TH32CS_SNAPPROCESS = 0x00000002
    TH32CS_SNAPMODULE = 0x00000008
    TH32CS_SNAPMODULE32 = 0x00000010
    MEM_COMMIT = 0x1000
    MEM_PRIVATE = 0x20000
    WRITABLE = {0x04, 0x08, 0x40, 0x80}

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    class MODULEENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("GlblcntUsage", wintypes.DWORD),
            ("ProccntUsage", wintypes.DWORD), ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
            ("modBaseSize", wintypes.DWORD), ("hModule", wintypes.HMODULE),
            ("szModule", wintypes.WCHAR * 256), ("szExePath", wintypes.WCHAR * 260),
        ]

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
            ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
            ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD),
        ]

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in (
            "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS",
            "dwProductVersionMS", "dwProductVersionLS", "dwFileFlagsMask", "dwFileFlags",
            "dwFileOS", "dwFileType", "dwFileSubtype", "dwFileDateMS", "dwFileDateLS",
        )]

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Rivals memory reading is available only on Windows")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.version = ctypes.WinDLL("version", use_last_error=True)
        _configure_kernel32(self.kernel32)
        _configure_version(self.version)

    def find_process(self) -> tuple[int, int, str] | None:
        snapshot = self.kernel32.CreateToolhelp32Snapshot(self.TH32CS_SNAPPROCESS, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = self.PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                if entry.szExeFile.lower() == PROCESS_NAME:
                    return self._module(int(entry.th32ProcessID))
                ok = self.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            return None
        finally:
            self.close_handle(snapshot)

    def _module(self, pid: int) -> tuple[int, int, str]:
        flags = self.TH32CS_SNAPMODULE | self.TH32CS_SNAPMODULE32
        snapshot = self.kernel32.CreateToolhelp32Snapshot(flags, pid)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = self.MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not self.kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
                raise ctypes.WinError(ctypes.get_last_error())
            base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
            return pid, int(base or 0), entry.szExePath
        finally:
            self.close_handle(snapshot)

    def file_version(self, path: str) -> str | None:
        package_version = _package_version_from_image_path(path)
        size = self.version.GetFileVersionInfoSizeW(str(Path(path)), None)
        if not size:
            return package_version
        buffer = ctypes.create_string_buffer(size)
        if not self.version.GetFileVersionInfoW(str(Path(path)), 0, size, buffer):
            return package_version
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not self.version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return package_version
        info = ctypes.cast(pointer, ctypes.POINTER(self.VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        if not ms and not ls:
            return package_version
        return f"{ms >> 16}.{ms & 0xffff}.{ls >> 16}.{ls & 0xffff}"

    def open_process(self, pid: int, access: int):
        return self.kernel32.OpenProcess(access, False, pid)

    def process_alive(self, handle) -> bool:
        exit_code = wintypes.DWORD()
        if not self.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise OSError(ctypes.get_last_error(), "GetExitCodeProcess failed")
        return exit_code.value == 259  # STILL_ACTIVE

    def close_handle(self, handle) -> None:
        if handle:
            self.kernel32.CloseHandle(handle)

    def read(self, handle, address: int, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        read_count = ctypes.c_size_t()
        if not self.kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(read_count)
        ) or read_count.value != size:
            raise OSError(ctypes.get_last_error(), "ReadProcessMemory failed")
        return buffer.raw

    def writable_private_regions(self, handle) -> Iterable[tuple[int, int, bool]]:
        address = 0
        maximum = (1 << 47) - 1
        mbi = self.MEMORY_BASIC_INFORMATION()
        while address < maximum:
            result = self.kernel32.VirtualQueryEx(
                handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
            )
            if not result:
                break
            base = int(mbi.BaseAddress or 0)
            size = int(mbi.RegionSize)
            protection = int(mbi.Protect) & 0xFF
            eligible = (
                mbi.State == self.MEM_COMMIT
                and mbi.Type == self.MEM_PRIVATE
                and protection in self.WRITABLE
                and not (mbi.Protect & 0x100)
                and not (mbi.Protect & 0x400)
            )
            yield base, size, eligible
            next_address = base + size
            if next_address <= address:
                break
            address = next_address
