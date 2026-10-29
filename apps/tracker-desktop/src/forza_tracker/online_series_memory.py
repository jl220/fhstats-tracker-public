"""Bounded read-only discovery of native series state. No fixed process addresses."""
from __future__ import annotations

import re
import struct
import threading
import time

from .online_series import read_series_state
from .rivals_memory import PROCESS_ACCESS, _WindowsBackend

CHUNK = 1024 * 1024
COUNT = re.compile(rb'\x48\x8d\x15....\x48\x8d\x4f\x20\xe8....\x48\x8d\x2d....\x48\x89\x6f\x20\xc6\x47\x38\x00', re.DOTALL)
TRACKS = re.compile(rb'\x48\x8d\x9f\x10\x01\x00\x00\x48\x8d\x15....\x48\x8b\xcb\xe8....\x48\x8d\x35....\x48\x89\x33\x33\xed\x48\x89\x6b\x18\x48\x89\x6b\x20\x48\x89\x6b\x28\x48\x89\x6b\x30\x48\x89\x6b\x38', re.DOTALL)
COMPLETED = b'\x48\x89\xaf\xd0\x00\x00\x00\xc6\x87\xe8\x00\x00\x00\x00'
PI = re.compile(rb'\x48\x8d\x15....\x48\x8d\x8f\xa0\x01\x00\x00\xe8....\x48\x8d\x35....\x48\x89\xb7\xa0\x01\x00\x00', re.DOTALL)
CLASS = re.compile(rb'\x48\x8d\x15....\x48\x8d\x8f\x50\x03\x00\x00\xe8....\x48\x8d\x05....\x48\x89\x87\x50\x03\x00\x00\xc6\x87\x68\x03\x00\x00\xff', re.DOTALL)


def rip(blob, address, start, displacement, length):
    return address + start + length + struct.unpack_from('<i', blob, start + displacement)[0]


def discover_series_layout(blob, address):
    found = []
    for count in COUNT.finditer(blob):
        tracks = TRACKS.search(blob, count.end(), min(len(blob), count.end() + 0x180))
        if tracks is None or COMPLETED not in blob[count.end():tracks.start()]:
            continue
        c, t = count.start(), tracks.start()
        found.append(dict(count_vtable=rip(blob, address, c + 16, 3, 7),
                          tracks_vtable=rip(blob, address, t + 22, 3, 7),
                          count_label=rip(blob, address, c, 3, 7),
                          tracks_label=rip(blob, address, t + 7, 3, 7),
                          code_address=address + c, code=blob[c:tracks.end()]))
    return found[0] if len(found) == 1 else None


def discover_class_layout(blob, address):
    found = []
    for pi in PI.finditer(blob):
        cls = CLASS.search(blob, pi.end(), min(len(blob), pi.end() + 0x300))
        if cls is None:
            continue
        p, c = pi.start(), cls.start()
        found.append(dict(pi_vtable=rip(blob, address, p + 19, 3, 7),
                          class_vtable=rip(blob, address, c + 19, 3, 7),
                          pi_label=rip(blob, address, p, 3, 7),
                          class_label=rip(blob, address, c, 3, 7)))
    return found[0] if len(found) == 1 else None


def read_class_restriction(read, address, pi_vtable, class_vtable):
    """An observed restriction does not establish its membership in the online series."""
    try:
        raw = read(address, 0x400)
        if tuple(struct.unpack_from('<Q', raw, o)[0] for o in (0x1a0, 0x290, 0x350)) != (
                pi_vtable, pi_vtable, class_vtable) or read(address, 0x400) != raw:
            return None
        value = raw[0x368]
        if value not in range(7):
            return None
        return dict(address=hex(address), raw_class_id=value,
                    candidate_name='S1' if value == 4 else None,
                    pi_min=struct.unpack_from('<H', raw, 0x1b8)[0],
                    pi_max=struct.unpack_from('<H', raw, 0x2a8)[0],
                    series_binding_verified=False)
    except (OSError, ValueError, OverflowError, struct.error):
        return None


class OnlineSeriesMemoryReader:
    def __init__(self, backend=None):
        self.backend = backend or _WindowsBackend()
        self.handle = None
        self.pid = self.base = None
        self.layout = self.class_layout = None
        self.roots, self.buckets = set(), set()
        self.cancel = threading.Event()
        self.scan = None
        self.retry_at = self.scan_at = 0.
        self.previous_classes = {}
        self.heap_cursor = 0

    def close(self):
        self.cancel.set()

    def release(self):
        if self.handle is not None:
            self.backend.close_handle(self.handle)
        self.handle = self.layout = self.class_layout = self.scan = None
        self.pid = self.base = None
        self.roots.clear()
        self.buckets.clear()
        self.previous_classes.clear()
        self.heap_cursor = 0

    def _read(self, address, size):
        return self.backend.read(self.handle, address, size)

    def _discover_image(self):
        head = self._read(self.base, 4096)
        pe = struct.unpack_from('<I', head, 0x3c)[0]
        if head[:2] != b'MZ' or pe > 2048 or head[pe:pe+4] != b'PE\0\0':
            raise ValueError('invalid executable header')
        count = struct.unpack_from('<H', head, pe + 6)[0]
        optional = struct.unpack_from('<H', head, pe + 20)[0]
        sections = {}
        for i in range(min(count, 32)):
            off = pe + 24 + optional + i * 40
            size, rva = struct.unpack_from('<II', head, off + 8)
            if 0 < size <= 160 * CHUNK and rva + size <= 512 * CHUNK:
                sections[head[off:off+8].split(b'\0')[0]] = (self.base + rva, size)
        start, size = sections[b'.text']
        layouts, class_layouts = {}, {}
        previous = b''
        deadline = time.monotonic() + 5
        for off in range(0, size, CHUNK):
            if self.cancel.is_set() or time.monotonic() > deadline:
                raise ValueError('executable discovery budget exceeded')
            raw = self._read(start + off, min(CHUNK, size - off))
            blob, address = previous + raw, start + off - len(previous)
            layout = discover_series_layout(blob, address)
            if layout:
                if (self._read(layout['count_label'], 11) == b'EventCount\0'
                        and self._read(layout['tracks_label'], 14) == b'EventTrackIds\0'):
                    layouts[layout['code_address']] = layout
            cls = discover_class_layout(blob, address)
            if cls and (self._read(cls['pi_label'], 6) == b'PIMin\0'
                        and self._read(cls['class_label'], 11) == b'CarClassId\0'):
                class_layouts[(cls['pi_vtable'], cls['class_vtable'])] = cls
            previous = blob[-2048:]
        if len(layouts) != 1:
            raise ValueError('series layout unavailable or ambiguous')
        self.layout = next(iter(layouts.values()))
        self.class_layout = next(iter(class_layouts.values())) if len(class_layouts) == 1 else None

    def _heap(self):
        total = 0
        deadline = time.monotonic() + 60
        resume = self.heap_cursor
        for address, size, eligible in self.backend.writable_private_regions(self.handle):
            # These fields have been observed in small object arenas. Avoid
            # paging giant graphics/geometry allocations during gameplay.
            if not eligible or size > 128 * CHUNK:
                continue
            if address + size <= resume:
                continue
            previous = b''
            for off in range(max(0, resume - address), size, CHUNK):
                if self.cancel.is_set():
                    return
                if total >= 1024 * CHUNK or time.monotonic() > deadline:
                    self.heap_cursor = max(address, address + off - min(1024, CHUNK))
                    return
                length = min(CHUNK, size - off)
                try:
                    raw = self._read(address + off, length)
                except OSError:
                    previous = b''
                    self.heap_cursor = address + off + length
                    continue
                total += length
                self.heap_cursor = address + off + length
                yield address + off - len(previous), previous + raw
                previous = raw[-1024:]
        self.heap_cursor = 0

    def _discover_step(self):
        deadline = time.monotonic() + .02
        while time.monotonic() < deadline:
            try:
                address, blob = next(self.scan)
            except StopIteration:
                self.scan = None
                self.scan_at = time.monotonic() + 30
                return
            needles = [(struct.pack('<Q', self.layout['count_vtable']), 0x20, self.roots, 64)]
            if self.class_layout:
                needles.append((struct.pack('<Q', self.class_layout['pi_vtable']), 0x1a0, self.buckets, 2048))
            for needle, offset, roots, limit in needles:
                at = blob.find(needle)
                while at >= 0 and len(roots) < limit:
                    root = address + at - offset
                    if root >= 0x10000 and root % 8 == 0:
                        local = at - offset
                        checks = ((0x20, self.layout['count_vtable']),
                                  (0xd0, self.layout['count_vtable']),
                                  (0x110, self.layout['tracks_vtable'])) if offset == 0x20 else (
                                  (0x1a0, self.class_layout['pi_vtable']),
                                  (0x290, self.class_layout['pi_vtable']),
                                  (0x350, self.class_layout['class_vtable']))
                        if all(0 <= local + off <= len(blob) - 8 and
                               struct.unpack_from('<Q', blob, local + off)[0] == vt
                               for off, vt in checks):
                            roots.add(root)
                    at = blob.find(needle, at + 8)

    def sample(self):
        if self.cancel.is_set():
            return {'status': 'closed'}
        try:
            if self.handle is not None and not self.backend.process_alive(self.handle):
                self.release()
                return {'status': 'process_exit'}
            if self.handle is None:
                if time.monotonic() < self.retry_at:
                    return {'status': 'waiting'}
                process = self.backend.find_process()
                if process is None:
                    return {'status': 'process_exit'}
                self.pid, self.base, _ = process
                self.handle = self.backend.open_process(self.pid, PROCESS_ACCESS)
                self._discover_image()
                self.scan = self._heap()
            layout = self.layout
            if self._read(layout['code_address'], len(layout['code'])) != layout['code']:
                raise ValueError('series constructor changed')
            if self.scan is None and time.monotonic() >= self.scan_at:
                self.scan = self._heap()
            if self.scan is not None:
                self._discover_step()
            candidates = []
            for address in tuple(self.roots):
                item = read_series_state(self._read, address, layout['count_vtable'], layout['tracks_vtable'])
                if item['status'] in ('identity_changed', 'unreadable'):
                    self.roots.discard(address)
                if item['status'] == 'candidate' and item['event_count'] == 3:
                    candidates.append(item)
            changes = []
            if self.class_layout:
                cls = self.class_layout
                for address in self.buckets:
                    item = read_class_restriction(self._read, address, cls['pi_vtable'], cls['class_vtable'])
                    value = item['raw_class_id'] if item else None
                    if address in self.previous_classes and self.previous_classes[address] != value and item:
                        changes.append(item)
                    self.previous_classes[address] = value
            if len(candidates) != 1:
                return dict(status='ambiguous' if candidates else 'scanning' if self.scan else 'waiting_for_series',
                            process_id=self.pid, class_candidates=changes)
            return dict(status='observed', process_id=self.pid, series=candidates[0], class_candidates=changes)
        except (OSError, KeyError, ValueError, OverflowError, struct.error):
            self.release()
            self.retry_at = time.monotonic() + 15
            return {'status': 'unavailable'}
