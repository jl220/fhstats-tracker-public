from __future__ import annotations

import struct
import threading
import math
from ctypes import wintypes

import forza_tracker.rivals_memory as rivals_memory

from forza_tracker.rivals_memory import (
    CONSTRUCTOR_LEA_RVAS,
    EXPECTED_BUILD,
    EVENT_VTABLE_RVA,
    GETTER_RVA,
    GETTER_SIGNATURE,
    EXPECTED_VTABLE_RVAS,
    PROPERTY_NAME,
    PROPERTY_RVA,
    RivalsMemoryReader,
    _WindowsBackend,
    _configure_kernel32,
    _configure_version,
    _derive_vtables,
    _package_version_from_image_path,
)


BASE = 0x10000000
PID = 42
INSTANCE = 0x20000000
VTABLES = tuple(BASE + rva for rva in EXPECTED_VTABLE_RVAS)
RACE_MANAGER_INSTRUCTION_RVA = 0x40845F5
RACE_MANAGER_INSTRUCTION = bytes.fromhex("488b0d44488206")
RECORD_CLEAN_GETTER_RVA = 0x28FFAD0
RECORD_CLEAN_GETTER_SIGNATURE = bytes.fromhex(
    "488b411880b8a490000000752780b8a790000000751e80b8a8900000007515"
    "80b8a990000000750c80b8aa900000007503b001c332c0c3"
)
EVENT = 0x21000000
NAME_OBJECT = 0x22000000
NAME_TEXT = 0x23000000
MANAGER = 0x24000000
VECTOR = 0x25000000
RECORD = 0x26000000
RECORD_VTABLE = 0x27000000
CAR = 0x28000000


def test_event_vtable_rva_matches_validated_reference_address() -> None:
    reference_base = 0x7FF608450000
    validated_event_vtable = 0x7FF60E838E90

    assert EVENT_VTABLE_RVA == validated_event_vtable - reference_base


def test_win32_process_handles_are_configured_as_pointer_sized() -> None:
    class Function:
        restype = None
        argtypes = None

    api = type("Api", (), {})()
    for name in (
        "CreateToolhelp32Snapshot",
        "OpenProcess",
        "Process32FirstW",
        "Process32NextW",
        "Module32FirstW",
        "VirtualQueryEx",
        "ReadProcessMemory",
        "CloseHandle",
        "GetExitCodeProcess",
    ):
        setattr(api, name, Function())

    _configure_kernel32(api)

    assert api.CreateToolhelp32Snapshot.restype is wintypes.HANDLE
    assert api.OpenProcess.restype is wintypes.HANDLE


def test_version_api_pointer_outputs_have_explicit_ctypes_signatures() -> None:
    class Function:
        restype = None
        argtypes = None

    api = type("Api", (), {})()
    for name in ("GetFileVersionInfoSizeW", "GetFileVersionInfoW", "VerQueryValueW"):
        setattr(api, name, Function())
    _configure_version(api)
    assert api.VerQueryValueW.argtypes is not None
    assert api.VerQueryValueW.restype is wintypes.BOOL


def test_default_scan_limits_cover_measured_heap_but_remain_bounded() -> None:
    assert rivals_memory._SCAN_LIMIT == 16 * 1024**3
    assert rivals_memory._SCAN_SECONDS == 30.0
    assert rivals_memory._RETRY_SECONDS >= 30.0


def test_store_executable_version_falls_back_to_strict_package_identity() -> None:
    supported = (
        r"C:\Program Files\WindowsApps\Microsoft.ForteBaseGame_3.440.853.0_x64__8wekyb3d8bbwe"
        r"\forzahorizon6.exe"
    )
    assert _package_version_from_image_path(supported) == "3.440.853.0"
    assert _package_version_from_image_path(supported.replace("forzahorizon6.exe", "helper.exe")) is None
    assert _package_version_from_image_path(supported.replace("Microsoft.ForteBaseGame", "Other.Game")) is None
    assert _package_version_from_image_path(supported.replace("x64", "arm64")) is None
    assert _package_version_from_image_path(supported.replace("8wekyb3d8bbwe", "publisher")) is None
    assert _package_version_from_image_path(r"C:\Games\Microsoft.ForteBaseGame_3.440.853.0_x64__8wekyb3d8bbwe\forzahorizon6.exe") is None


def test_windows_backend_uses_package_version_when_resource_size_is_zero() -> None:
    backend = _WindowsBackend.__new__(_WindowsBackend)
    backend.version = type(
        "VersionApi", (), {"GetFileVersionInfoSizeW": staticmethod(lambda _path, _unused: 0)}
    )()
    path = (
        r"C:\Program Files\WindowsApps\Microsoft.ForteBaseGame_3.440.853.0_x64__8wekyb3d8bbwe"
        r"\forzahorizon6.exe"
    )
    assert backend.file_version(path) == EXPECTED_BUILD


class FakeBackend:
    def __init__(self) -> None:
        self.build = EXPECTED_BUILD
        self.memory: dict[int, bytes] = {}
        self.regions = [(INSTANCE, 0x1000, True)]
        self.open_access: list[int] = []
        self.closed: list[object] = []
        self.handle = object()
        self.find_result = (PID, BASE, r"C:\Games\forzahorizon6.exe")
        self.find_calls = 0
        self.alive = True

        self.memory[BASE + GETTER_RVA] = GETTER_SIGNATURE
        self.memory[BASE + PROPERTY_RVA] = PROPERTY_NAME + b"\0"
        for instruction_rva, target in zip(CONSTRUCTOR_LEA_RVAS, VTABLES):
            displacement = target - (BASE + instruction_rva + 7)
            self.memory[BASE + instruction_rva] = b"\x48\x8d\x05" + struct.pack("<i", displacement)
        block = bytearray(0x1000)
        struct.pack_into("<Q", block, 0, VTABLES[0])
        struct.pack_into("<Q", block, 8, VTABLES[1])
        struct.pack_into("<Q", block, 0x38, VTABLES[2])
        struct.pack_into("<Q", block, 0x440, 0xDEADBEEF)
        self.memory[INSTANCE] = bytes(block)

        self.memory[BASE + RACE_MANAGER_INSTRUCTION_RVA] = RACE_MANAGER_INSTRUCTION
        slot = BASE + RACE_MANAGER_INSTRUCTION_RVA + 7 + struct.unpack(
            "<i", RACE_MANAGER_INSTRUCTION[3:]
        )[0]
        self.memory[slot] = struct.pack("<Q", MANAGER)
        manager = bytearray(0x80)
        struct.pack_into("<QQ", manager, 0x70, VECTOR, VECTOR + 8)
        self.memory[MANAGER] = bytes(manager)
        self.memory[VECTOR] = struct.pack("<Q", RECORD)
        record = bytearray(0x140)
        struct.pack_into("<Q", record, 0, RECORD_VTABLE)
        struct.pack_into("<Q", record, 0x18, CAR)
        struct.pack_into("<d", record, 0x70, 100.0)
        struct.pack_into("<d", record, 0x138, 125.5)
        self.memory[RECORD] = bytes(record)
        vtable = bytearray(0x40)
        struct.pack_into("<Q", vtable, 0x38, BASE + RECORD_CLEAN_GETTER_RVA)
        self.memory[RECORD_VTABLE] = bytes(vtable)
        self.memory[BASE + RECORD_CLEAN_GETTER_RVA] = RECORD_CLEAN_GETTER_SIGNATURE
        self.memory[CAR + 0x90A4] = bytes(7)

    def find_process(self):
        self.find_calls += 1
        return self.find_result

    def process_alive(self, _handle):
        return self.alive

    def file_version(self, _path: str):
        return self.build

    def open_process(self, pid: int, access: int):
        self.open_access.append(access)
        return self.handle

    def close_handle(self, handle: object):
        self.closed.append(handle)

    def read(self, _handle: object, address: int, size: int):
        for start, value in self.memory.items():
            if start <= address and address + size <= start + len(value):
                offset = address - start
                return value[offset : offset + size]
        raise OSError("unreadable")

    def writable_private_regions(self, _handle: object):
        return list(self.regions)


def test_derives_constructor_vtables_only_from_exact_lea_instructions() -> None:
    backend = FakeBackend()
    assert _derive_vtables(backend, backend.handle, BASE) == VTABLES
    backend.memory[BASE + CONSTRUCTOR_LEA_RVAS[1]] = b"\x90" * 7
    assert _derive_vtables(backend, backend.handle, BASE) is None


def test_rejects_relocated_lea_that_targets_the_wrong_vtable() -> None:
    backend = FakeBackend()
    instruction_rva = CONSTRUCTOR_LEA_RVAS[0]
    wrong_target = VTABLES[0] + 8
    displacement = wrong_target - (BASE + instruction_rva + 7)
    backend.memory[BASE + instruction_rva] = b"\x48\x8d\x05" + struct.pack("<i", displacement)
    assert _derive_vtables(backend, backend.handle, BASE) is None


def test_compatible_unique_instance_reads_active_and_uses_readonly_access() -> None:
    backend = FakeBackend()
    reader = RivalsMemoryReader(backend=backend)

    result = reader.sample()
    assert result["active"] is True
    assert result["event"] is None
    assert result["validity"]["dirty"] is False
    assert backend.open_access == [0x410]


def test_cached_sample_avoids_process_and_module_enumeration() -> None:
    backend = FakeBackend()
    reader = RivalsMemoryReader(backend=backend)
    assert reader.sample()["active"] is True
    assert reader.sample()["active"] is True
    assert backend.find_calls == 1


def test_incompatible_build_or_signature_fails_closed() -> None:
    for mutate in ("build", "getter", "property"):
        backend = FakeBackend()
        if mutate == "build":
            backend.build = "3.440.854.0"
        elif mutate == "getter":
            backend.memory[BASE + GETTER_RVA] = b"\x90" * len(GETTER_SIGNATURE)
        else:
            backend.memory[BASE + PROPERTY_RVA] = b"X" * len(PROPERTY_NAME) + b"\0"
        result = RivalsMemoryReader(backend=backend).sample()
        assert result["active"] is None
        assert result["status"] == "unsupported"
        if mutate != "build":
            assert backend.closed == [backend.handle]


def test_unsupported_result_is_cached_during_backoff() -> None:
    backend = FakeBackend()
    backend.build = "3.440.854.0"
    reader = RivalsMemoryReader(backend=backend)
    assert reader.sample()["status"] == "unsupported"
    assert reader.sample()["status"] == "unsupported"
    assert backend.find_calls == 1


def test_scan_requires_exactly_one_triple_vtable_instance() -> None:
    backend = FakeBackend()
    duplicate = INSTANCE + 0x2000
    backend.memory[duplicate] = backend.memory[INSTANCE]
    backend.regions.append((duplicate, 0x1000, True))

    result = RivalsMemoryReader(backend=backend).sample()
    assert result["active"] is None
    assert result["process_id"] == PID
    assert result["status"] == "scan_failed"
    assert result["event"] is None and result["validity"] is None


def test_cached_instance_is_revalidated_and_rescanned() -> None:
    backend = FakeBackend()
    reader = RivalsMemoryReader(backend=backend)
    assert reader.sample()["active"] is True

    block = bytearray(backend.memory[INSTANCE])
    struct.pack_into("<Q", block, 0, 0)
    backend.memory[INSTANCE] = bytes(block)
    assert reader.sample()["status"] == "scan_failed"


def test_zero_context_is_the_only_successful_inactive_result() -> None:
    backend = FakeBackend()
    block = bytearray(backend.memory[INSTANCE])
    struct.pack_into("<Q", block, 0x440, 0)
    backend.memory[INSTANCE] = bytes(block)
    result = RivalsMemoryReader(backend=backend).sample()
    assert (result["active"], result["process_id"], result["status"]) == (False, PID, "inactive")
    assert result["event"] is None


def test_process_exit_and_read_error_return_unknown() -> None:
    backend = FakeBackend()
    backend.find_result = None
    result = RivalsMemoryReader(backend=backend).sample()
    assert (result["active"], result["process_id"], result["status"]) == (None, None, "process_exit")
    assert result["event"] is None and result["validity"] is None

    backend = FakeBackend()
    backend.memory.pop(BASE + GETTER_RVA)
    result = RivalsMemoryReader(backend=backend).sample()
    assert result["active"] is None
    assert result["status"] == "read_error"


def test_transient_image_read_error_reopens_same_process_on_next_sample() -> None:
    backend = FakeBackend()
    getter = backend.memory.pop(BASE + GETTER_RVA)
    reader = RivalsMemoryReader(backend=backend)
    assert reader.sample()["status"] == "read_error"
    backend.memory[BASE + GETTER_RVA] = getter

    assert reader.sample()["active"] is True
    assert backend.open_access == [0x410, 0x410]


def test_close_is_cross_thread_safe_cancels_scan_and_releases_handle() -> None:
    entered = threading.Event()

    class BlockingBackend(FakeBackend):
        def writable_private_regions(self, handle):
            entered.set()
            for _ in range(10000):
                yield (INSTANCE + 0x10000, 0x1000, True)

    backend = BlockingBackend()
    reader = RivalsMemoryReader(backend=backend)
    worker = threading.Thread(target=reader.sample)
    worker.start()
    assert entered.wait(1)
    reader.close()
    worker.join(1)

    assert not worker.is_alive()
    assert backend.closed == [backend.handle]
    assert reader.sample()["status"] == "closed"


def install_event(backend: FakeBackend, *, pointer: int = EVENT, route_id: int = 69,
                  name: bytes = "Circuito de tierra de Sekibe".encode()) -> None:
    module = bytearray(backend.memory[INSTANCE])
    struct.pack_into("<Q", module, 0x440, pointer)
    backend.memory[INSTANCE] = bytes(module)
    event = bytearray(0xD0)
    struct.pack_into("<Q", event, 0, BASE + EVENT_VTABLE_RVA)
    struct.pack_into("<I", event, 0x18, route_id | 0xAB000000)
    struct.pack_into("<Q", event, 0xB8, NAME_OBJECT)
    backend.memory[pointer] = bytes(event)
    obj = bytearray(0x30)
    struct.pack_into("<Q", obj, 0x20, NAME_TEXT)
    struct.pack_into("<I", obj, 0x28, len(name) | 0x80000000)
    backend.memory[NAME_OBJECT] = bytes(obj)
    backend.memory[NAME_TEXT] = name


def test_reads_bounded_utf8_event_identity_and_tracks_changed_pointer() -> None:
    backend = FakeBackend()
    install_event(backend)
    reader = RivalsMemoryReader(backend=backend)
    first = reader.sample()
    assert first["event"] == {
        "route_id": 69, "name": "Circuito de tierra de Sekibe", "instance_id": hex(EVENT)
    }
    assert first["event_status"] == "available"

    changed = EVENT + 0x1000
    install_event(backend, pointer=changed, route_id=62, name="Circuito de tierra de Hirosaki".encode())
    second = reader.sample()
    assert second["event"]["route_id"] == 62
    assert second["event"]["instance_id"] == hex(changed)


def test_event_null_malformed_pointer_utf8_and_length_fail_closed() -> None:
    backend = FakeBackend()
    install_event(backend)
    reader = RivalsMemoryReader(backend=backend)
    assert reader.sample()["event"] is not None

    module = bytearray(backend.memory[INSTANCE])
    struct.pack_into("<Q", module, 0x440, 0)
    backend.memory[INSTANCE] = bytes(module)
    result = reader.sample()
    assert result["event"] is None and result["event_status"] == "inactive"

    struct.pack_into("<Q", module, 0x440, 3)
    backend.memory[INSTANCE] = bytes(module)
    result = reader.sample()
    assert result["event"] is None and result["event_status"] == "read_error"

    install_event(backend)
    obj = bytearray(backend.memory[NAME_OBJECT])
    struct.pack_into("<I", obj, 0x28, 513)
    backend.memory[NAME_OBJECT] = bytes(obj)
    result = reader.sample()
    assert result["event"] is None and result["event_status"] == "read_error"

    install_event(backend, name=b"\xff")
    result = reader.sample()
    assert result["event"] is None and result["event_status"] == "read_error"


def test_event_repeat_pointer_guard_rejects_mid_read_change() -> None:
    class ChangingBackend(FakeBackend):
        event_pointer_reads = 0
        def read(self, handle, address, size):
            if address == INSTANCE + 0x440 and size == 8:
                self.event_pointer_reads += 1
                if self.event_pointer_reads >= 2:
                    return struct.pack("<Q", EVENT + 0x1000)
            return super().read(handle, address, size)

    backend = ChangingBackend()
    install_event(backend)
    result = RivalsMemoryReader(backend=backend).sample()
    assert result["event"] is None and result["event_status"] == "read_error"


def test_unique_owned_race_record_reports_clocks_ids_and_all_five_dirty_flags() -> None:
    for index in (0, 3, 4, 5, 6):
        backend = FakeBackend()
        flags = bytearray(7)
        flags[index] = 1
        backend.memory[CAR + 0x90A4] = bytes(flags)
        result = RivalsMemoryReader(backend=backend).sample()
        assert result["validity"] == {
            "checkpoints": None,
            "checkpoint_status": "read_error",
            "dirty": True,
            "race_clock": 125.5,
            "lap_start_clock": 100.0,
            "record_id": hex(RECORD),
            "car_id": hex(CAR),
        }
        assert result["validity_status"] == "available"


def test_validity_rejects_same_record_when_lap_changes_during_flag_read() -> None:
    class LapBoundaryBackend(FakeBackend):
        def read(self, handle, address, size):
            if address == CAR + 0x90A4 and size == 7:
                record = bytearray(self.memory[RECORD])
                struct.pack_into("<d", record, 0x70, 126.0)
                struct.pack_into("<d", record, 0x138, 126.0)
                self.memory[RECORD] = bytes(record)
            return super().read(handle, address, size)

    result = RivalsMemoryReader(backend=LapBoundaryBackend()).sample()

    assert result["validity"] is None
    assert result["validity_status"] == "read_error"


def test_validity_rejects_backward_clock_during_flag_read() -> None:
    class BackwardClockBackend(FakeBackend):
        def read(self, handle, address, size):
            if address == CAR + 0x90A4 and size == 7:
                record = bytearray(self.memory[RECORD])
                struct.pack_into("<d", record, 0x138, 124.0)
                self.memory[RECORD] = bytes(record)
            return super().read(handle, address, size)

    result = RivalsMemoryReader(backend=BackwardClockBackend()).sample()

    assert result["validity"] is None
    assert result["validity_status"] == "read_error"


def test_validity_returns_post_flag_clock_from_coherent_observation() -> None:
    class AdvancingClockBackend(FakeBackend):
        def read(self, handle, address, size):
            if address == CAR + 0x90A4 and size == 7:
                record = bytearray(self.memory[RECORD])
                struct.pack_into("<d", record, 0x138, 125.6)
                self.memory[RECORD] = bytes(record)
            return super().read(handle, address, size)

    result = RivalsMemoryReader(backend=AdvancingClockBackend()).sample()

    assert result["validity_status"] == "available"
    assert result["validity"]["race_clock"] == 125.6


def test_validity_rejects_excessive_clock_advance_during_flag_read() -> None:
    class DelayedClockBackend(FakeBackend):
        def read(self, handle, address, size):
            if address == CAR + 0x90A4 and size == 7:
                record = bytearray(self.memory[RECORD])
                struct.pack_into("<d", record, 0x138, 126.0)
                self.memory[RECORD] = bytes(record)
            return super().read(handle, address, size)

    result = RivalsMemoryReader(backend=DelayedClockBackend()).sample()

    assert result["validity"] is None
    assert result["validity_status"] == "read_error"


def test_validity_rejects_multiple_records_invalid_vector_and_unowned_vtable() -> None:
    backend = FakeBackend()
    backend.memory[VECTOR] = struct.pack("<QQ", RECORD, RECORD)
    manager = bytearray(backend.memory[MANAGER])
    struct.pack_into("<QQ", manager, 0x70, VECTOR, VECTOR + 16)
    backend.memory[MANAGER] = bytes(manager)
    result = RivalsMemoryReader(backend=backend).sample()
    assert result["validity"] is None and result["validity_status"] == "unavailable"

    backend = FakeBackend()
    manager = bytearray(backend.memory[MANAGER])
    struct.pack_into("<QQ", manager, 0x70, VECTOR + 8, VECTOR)
    backend.memory[MANAGER] = bytes(manager)
    assert RivalsMemoryReader(backend=backend).sample()["validity_status"] == "read_error"

    backend = FakeBackend()
    backend.memory[RECORD_VTABLE] = bytes(0x40)
    assert RivalsMemoryReader(backend=backend).sample()["validity_status"] == "unavailable"


def test_validity_rejects_nan_clocks_short_reads_and_stale_payload() -> None:
    backend = FakeBackend()
    reader = RivalsMemoryReader(backend=backend)
    assert reader.sample()["validity"] is not None
    record = bytearray(backend.memory[RECORD])
    struct.pack_into("<d", record, 0x138, math.nan)
    backend.memory[RECORD] = bytes(record)
    result = reader.sample()
    assert result["validity"] is None and result["validity_status"] == "read_error"

    backend = FakeBackend()
    backend.memory[CAR + 0x90A4] = bytes(6)
    result = RivalsMemoryReader(backend=backend).sample()
    assert result["validity"] is None and result["validity_status"] == "read_error"


def test_detail_signature_failures_are_independent_from_context_and_each_other() -> None:
    backend = FakeBackend()
    install_event(backend)
    backend.memory[BASE + RECORD_CLEAN_GETTER_RVA] = b"\x90" * len(RECORD_CLEAN_GETTER_SIGNATURE)
    result = RivalsMemoryReader(backend=backend).sample()
    assert result["active"] is True and result["event"] is not None
    assert result["validity"] is None and result["validity_status"] == "unsupported"

    backend = FakeBackend()
    install_event(backend)
    event = bytearray(backend.memory[EVENT])
    struct.pack_into("<Q", event, 0, 0)
    backend.memory[EVENT] = bytes(event)
    result = RivalsMemoryReader(backend=backend).sample()
    assert result["active"] is True and result["validity"] is not None
    assert result["event"] is None and result["event_status"] == "unsupported"

    backend = FakeBackend()
    install_event(backend)
    backend.memory[BASE + RACE_MANAGER_INSTRUCTION_RVA] = b"\x90" * 7
    result = RivalsMemoryReader(backend=backend).sample()
    assert result["active"] is True and result["event"] is not None
    assert result["validity"] is None and result["validity_status"] == "unsupported"


CHECKPOINT_EVIDENCE = (
    (0x28f41b0, bytes.fromhex("488b010f28c1f20f5c4170f20f1189a0000000f20f5ac9f20f1181a800000048ff6078")),
    (0x28f5c20, bytes.fromhex("ff81e00000004c8bc98b91d80000000f57c0448b81d0000000440381d4000000418bc02bc2443bc2f30f5ac1440f4dc0498b81c80000004963c8f20f1104c8418b91d8000000418b81d40000003bc27d0affc0418981d4000000c3418b89d000000033c0ffc13bca0f4cc1418981d0000000c3")),
    (0x2a242a0, bytes.fromhex("488b81f08f0000c3")),
    (0x28f5f47, bytes.fromhex("488b070f28cf488bcfff9098000000")),
    (0x28f5e68, bytes.fromhex("e833e4120033d2488bf0488b88a0000000e8a21a9e02488b8ea000000033d2f30f10b698000000e830139e02f20f10bf38010000488b4f18f2410f5cf80f5ac6f20f59f8f2410f58f8e84a681300")),
    (0x28f6e29, bytes.fromhex("488b070f28cf488bcfff9098000000")),
    (0x28f2639, bytes.fromhex("4c89aed0000000c786e0000000ffffffff")),
)

def install_checkpoints(backend):
    for rva, signature in CHECKPOINT_EVIDENCE:
        backend.memory[BASE + rva] = signature
    table = bytearray(0xA0)
    table[:0x40] = backend.memory[RECORD_VTABLE][:0x40]
    struct.pack_into('<Q', table, 0x98, BASE + 0x28F41B0)
    struct.pack_into('<Q', table, 0x78, BASE + 0x28F5C20)
    backend.memory[RECORD_VTABLE] = bytes(table)
    update_checkpoint_record(backend, crossing=22, lap=2, absolute=120.0, elapsed=20.0)


def update_checkpoint_record(backend, *, crossing=22, lap=2, absolute=120.0, elapsed=20.0):
    block = bytearray(backend.memory[RECORD])
    for fmt, offset, value in [('<i', 0xE0, crossing), ('<i', 0x8C, lap), ('<d', 0xA0, absolute), ('<d', 0xA8, elapsed)]:
        struct.pack_into(fmt, block, offset, value)
    backend.memory[RECORD] = bytes(block)


def test_checkpoint_coherent_payload_and_rewind_flags():
    for flag in (None, 4, 5):
        backend = FakeBackend()
        install_checkpoints(backend)
        flags = bytearray(7)
        if flag is not None:
            flags[flag] = 1
        backend.memory[CAR + 0x90A4] = bytes(flags)
        result = RivalsMemoryReader(backend=backend).sample()['validity']
        assert result['checkpoints'] == dict(crossing_index=22, lap_index=2,
            crossing_race_clock=120.0, crossing_lap_clock=20.0,
            rewind_active=flag == 5, rewind_affected=flag == 4)
        assert result['checkpoint_status'] == 'available'


def test_checkpoint_signatures_and_virtuals_fail_independently_without_stale_values():
    for address in [BASE + rva for rva, _ in CHECKPOINT_EVIDENCE] + [RECORD_VTABLE + 0x78, RECORD_VTABLE + 0x98]:
        backend = FakeBackend()
        install_event(backend)
        install_checkpoints(backend)
        reader = RivalsMemoryReader(backend=backend)
        assert reader.sample()['validity']['checkpoints'] is not None
        if address >= RECORD_VTABLE:
            table = bytearray(backend.memory[RECORD_VTABLE])
            struct.pack_into('<Q', table, address - RECORD_VTABLE, 0)
            backend.memory[RECORD_VTABLE] = bytes(table)
        else:
            backend.memory[address] = b'\x90' * len(backend.memory[address])
        result = reader.sample()
        assert result['event'] is not None and result['validity']['dirty'] is False
        assert result['validity']['checkpoints'] is None
        assert result['validity']['checkpoint_status'] == 'unsupported'


def test_checkpoint_rejects_torn_fields_even_with_unchanged_counter():
    for offset, fmt, replacement in [(0xA0, '<d', 121.0), (0xA8, '<d', 21.0), (0xE0, '<i', 23), (0x8C, '<i', 3)]:
        class TornBackend(FakeBackend):
            def read(self, handle, address, size):
                if address == CAR + 0x90A4:
                    block = bytearray(self.memory[RECORD])
                    struct.pack_into(fmt, block, offset, replacement)
                    self.memory[RECORD] = bytes(block)
                return super().read(handle, address, size)
        backend = TornBackend()
        install_checkpoints(backend)
        result = RivalsMemoryReader(backend=backend).sample()
        assert result['validity']['dirty'] is False
        assert result['validity']['checkpoints'] is None


def test_checkpoint_rejects_invalid_values_accepts_unstarted():
    for changes in [dict(crossing=-2), dict(lap=-1), dict(absolute=math.nan), dict(elapsed=math.inf), dict(absolute=-1), dict(elapsed=-1), dict(absolute=126)]:
        backend = FakeBackend()
        install_checkpoints(backend)
        update_checkpoint_record(backend, **changes)
        result = RivalsMemoryReader(backend=backend).sample()['validity']
        assert result['checkpoints'] is None
    backend = FakeBackend()
    install_checkpoints(backend)
    update_checkpoint_record(backend, crossing=-1, lap=0, absolute=0, elapsed=0)
    assert RivalsMemoryReader(backend=backend).sample()['validity']['checkpoints']['crossing_index'] == -1


def test_context_revalidates_event_after_full_validity_observation():
    class EventChangeBackend(FakeBackend):
        def read(self, handle, address, size):
            if address == CAR + 0x90A4:
                block = bytearray(self.memory[INSTANCE])
                struct.pack_into('<Q', block, 0x440, EVENT + 0x1000)
                self.memory[INSTANCE] = bytes(block)
            return super().read(handle, address, size)
    backend = EventChangeBackend()
    install_event(backend)
    install_checkpoints(backend)
    result = RivalsMemoryReader(backend=backend).sample()
    assert result['event'] is None
    assert result['validity'] is None


def test_checkpoint_short_repeat_read_keeps_validity_available():
    class ShortBackend(FakeBackend):
        blocks = 0
        def read(self, handle, address, size):
            if address == RECORD and size == 0x140:
                self.blocks += 1
                if self.blocks == 2:
                    return bytes(0x100)
            return super().read(handle, address, size)
    backend = ShortBackend()
    install_checkpoints(backend)
    result = RivalsMemoryReader(backend=backend).sample()
    assert result['validity']['dirty'] is False
    assert result['validity']['checkpoints'] is None
    assert result['validity']['checkpoint_status'] == 'read_error'


def test_checkpoint_ownership_rechecked_after_optional_read():
    class OwnerChangeBackend(FakeBackend):
        blocks = 0
        def read(self, handle, address, size):
            if address == RECORD and size == 0x140:
                self.blocks += 1
                if self.blocks == 2:
                    self.memory[VECTOR] = struct.pack('<Q', RECORD + 0x1000)
            return super().read(handle, address, size)
    backend = OwnerChangeBackend()
    install_checkpoints(backend)
    result = RivalsMemoryReader(backend=backend).sample()
    assert result['validity'] is None


def test_checkpoint_finish_keeps_outgoing_time_after_lap_start_changes():
    backend = FakeBackend()
    install_checkpoints(backend)
    block = bytearray(backend.memory[RECORD])
    struct.pack_into('<d', block, 0x70, 120.0)
    backend.memory[RECORD] = bytes(block)
    result = RivalsMemoryReader(backend=backend).sample()['validity']
    assert result['lap_start_clock'] == 120.0
    assert result['checkpoints']['crossing_lap_clock'] == 20.0

def test_checkpoint_rejects_other_vector_entry_becoming_owned_during_read():
    other = RECORD + 0x1000
    class VectorChangeBackend(FakeBackend):
        blocks = 0
        def read(self, handle, address, size):
            if address == RECORD and size == 0x140:
                self.blocks += 1
                if self.blocks == 2:
                    self.memory[VECTOR] = struct.pack('<QQ', RECORD, RECORD)
            return super().read(handle, address, size)
    backend = VectorChangeBackend()
    install_checkpoints(backend)
    backend.memory[VECTOR] = struct.pack('<QQ', RECORD, other)
    manager = bytearray(backend.memory[MANAGER])
    struct.pack_into('<QQ', manager, 0x70, VECTOR, VECTOR + 16)
    backend.memory[MANAGER] = bytes(manager)
    block = bytearray(backend.memory[RECORD])
    table = RECORD_VTABLE + 0x1000
    struct.pack_into('<Q', block, 0, table)
    backend.memory[other] = bytes(block)
    backend.memory[table] = bytes(0x40)
    assert RivalsMemoryReader(backend=backend).sample()['validity'] is None
