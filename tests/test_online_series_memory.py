import json
from pathlib import Path
import struct

import pytest

from forza_tracker.online_series_memory import OnlineSeriesMemoryReader, discover_class_layout, discover_series_layout, read_class_restriction


def captured_constructor():
    source = Path(__file__).parent / 'fixtures' / 'online_series_constructor.json'
    data = json.loads(source.read_text())
    return data, bytes.fromhex(data['bytes'])


def test_constructor_discovery_uses_signatures_and_relocated_addresses():
    data, raw = captured_constructor()
    layout = discover_series_layout(raw, int(data['address'], 16))
    assert layout['count_vtable'] == int(data['count_vtable'], 16)
    assert layout['tracks_vtable'] == int(data['tracks_vtable'], 16)
    relocated = discover_series_layout(raw, int(data['address'], 16) + 0x100000)
    assert relocated['count_vtable'] == layout['count_vtable'] + 0x100000
    assert relocated['tracks_vtable'] == layout['tracks_vtable'] + 0x100000


def test_changed_layout_or_multiple_constructors_are_not_guessed():
    data, raw = captured_constructor()
    corrupt = raw.replace(b'\xc6\x87\xe8\x00\x00\x00\x00', b'\xc6\x87\xe9\x00\x00\x00\x00')
    assert discover_series_layout(corrupt, int(data['address'], 16)) is None
    assert discover_series_layout(raw + raw, int(data['address'], 16)) is None


def test_class_layout_matches_the_independently_captured_constructor():
    data = json.loads((Path(__file__).parent / 'fixtures' / 'online_class_constructor.json').read_text())
    layout = discover_class_layout(bytes.fromhex(data['bytes']), int(data['address'], 16))
    assert layout['pi_vtable'] == int(data['pi_vtable'], 16)
    assert layout['class_vtable'] == int(data['class_vtable'], 16)


def test_process_exit_releases_all_addresses_and_previous_class_candidates():
    class Backend:
        closed = []
        def process_alive(self, handle):
            return False
        def close_handle(self, handle):
            self.closed.append(handle)
    backend = Backend()
    reader = OnlineSeriesMemoryReader(backend=backend)
    reader.handle, reader.pid, reader.base = 7, 123, 0x100000
    reader.layout = {'old': True}
    reader.roots.add(0x800000)
    reader.buckets.add(0x900000)
    reader.previous_classes[0x900000] = 4
    reader.heap_cursor = 0x123456
    assert reader.sample() == {'status': 'process_exit'}
    assert backend.closed == [7]
    assert not reader.roots and not reader.buckets and not reader.previous_classes
    assert reader.layout is None and reader.handle is None and reader.pid is None
    assert reader.heap_cursor == 0


def test_bounded_heap_scans_resume_instead_of_starving_later_object_arenas(monkeypatch):
    import forza_tracker.online_series_memory as module
    monkeypatch.setattr(module, 'CHUNK', 1)
    class Backend:
        calls = []
        def writable_private_regions(self, handle):
            return [(0x10000 + i * 0x1000, 128, True) for i in range(12)]
        def read(self, handle, address, size):
            self.calls.append(address)
            return bytes(size)
    backend = Backend()
    reader = OnlineSeriesMemoryReader(backend=backend)
    list(reader._heap())
    assert not any(a >= 0x1b000 for a in backend.calls)
    list(reader._heap())
    assert any(a >= 0x1b000 for a in backend.calls)
    assert reader.heap_cursor == 0


def test_native_class_restriction_is_separate_from_verified_series_class():
    raw = bytearray(0x400)
    for off, vt in [(0x1a0, 0x710000), (0x290, 0x710000), (0x350, 0x720000)]:
        struct.pack_into('<Q', raw, off, vt)
    raw[0x368] = 4
    result = read_class_restriction(lambda a, n: bytes(raw[:n]), 0x800000, 0x710000, 0x720000)
    assert result['raw_class_id'] == 4
    assert result['candidate_name'] == 'S1'
    assert result['series_binding_verified'] is False
    raw[0x368] = 255
    assert read_class_restriction(lambda a, n: bytes(raw[:n]), 0x800000, 0x710000, 0x720000) is None
    raw[0x368] = 4
    raw[0x350] ^= 1
    assert read_class_restriction(lambda a, n: bytes(raw[:n]), 0x800000, 0x710000, 0x720000) is None


def test_discovery_does_not_fill_root_budget_with_unrelated_property_instances():
    reader = OnlineSeriesMemoryReader(backend=object())
    reader.layout = dict(count_vtable=0x710000, tracks_vtable=0x720000)
    raw = bytearray(0x10000)
    for i in range(100):
        struct.pack_into('<Q', raw, i * 0x80, 0x710000)
    root = 0xe000
    for off, table in ((0x20, 0x710000), (0xd0, 0x710000), (0x110, 0x720000)):
        struct.pack_into('<Q', raw, root + off, table)
    reader.scan = iter([(0x800000, bytes(raw))])
    reader._discover_step()
    assert reader.roots == {0x800000 + root}


def test_full_reader_rediscovers_image_and_heap_in_a_relocated_process():
    data, constructor = captured_constructor()
    original = int(data['address'], 16)
    class Backend:
        alive = True
        pid = 1
        delta = 0
        opened = []
        def find_process(self):
            return self.pid, original - 0x1000 + self.delta, 'game.exe'
        def open_process(self, pid, access):
            self.opened.append((pid, access))
            assert access & 0x0020 == 0  # No write access.
            return pid
        def process_alive(self, handle):
            return self.alive
        def close_handle(self, handle):
            pass
        def writable_private_regions(self, handle):
            return [(0x800000 + self.delta, 0x1000, True)]
        def read(self, handle, address, size):
            base = original - 0x1000 + self.delta
            head = bytearray(4096)
            head[:2] = b'MZ'
            struct.pack_into('<I', head, 0x3c, 0x80)
            head[0x80:0x84] = b'PE\0\0'
            struct.pack_into('<H', head, 0x86, 1)
            struct.pack_into('<H', head, 0x94, 0xf0)
            off = 0x80 + 24 + 0xf0
            head[off:off+5] = b'.text'
            struct.pack_into('<II', head, off + 8, len(constructor), 0x1000)
            heap = bytearray(0x1000)
            for o, value in [(0x20, int(data['count_vtable'], 16)),
                             (0xd0, int(data['count_vtable'], 16)),
                             (0x110, int(data['tracks_vtable'], 16))]:
                struct.pack_into('<Q', heap, 0x100 + o, value + self.delta)
            heap[0x138] = 3
            struct.pack_into('<10I', heap, 0x228, 71, 74, 75, *([0] * 7))
            blocks = {base: bytes(head), original + self.delta: constructor,
                      0x7ff7c28224c8 + self.delta: b'EventCount\0',
                      0x7ff7c28226c8 + self.delta: b'EventTrackIds\0',
                      0x800000 + self.delta: bytes(heap)}
            for start, raw in blocks.items():
                if start <= address and address + size <= start + len(raw):
                    return raw[address-start:address-start+size]
            raise OSError('unreadable fixture address')
    backend = Backend()
    reader = OnlineSeriesMemoryReader(backend=backend)
    first = reader.sample()
    assert first['status'] == 'observed'
    assert first['series']['track_names'] == ['Waterfall Trail', 'Airfield Trail', 'Cherry Field Trail']
    backend.alive = False
    assert reader.sample()['status'] == 'process_exit'
    backend.pid, backend.delta, backend.alive = 2, 0x200000, True
    second = reader.sample()
    assert second['status'] == 'observed'
    assert second['process_id'] == 2
    assert second['series']['address'] != first['series']['address']
