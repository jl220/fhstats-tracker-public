"""Read-only car metadata bound to the participant in a native race record.

Addresses are discovered from executable instructions and property labels.
No native function is executed. The game's PI table is read, not assumed.
"""
from __future__ import annotations

import hashlib
import math
import re
import struct

PI_PROPERTY = re.compile(rb'\x48\x8d\x15....\x48\x8d\x4f\x48\xe8....\x48\x8d\x2d....\x48\x89\x6f\x48\x44\x89\x7f\x60', re.DOTALL)
CLASS_PROPERTY = re.compile(rb'\x48\x8d\x15....\x48\x8d\x8f\xb8\x02\x00\x00\xe8....\x48\x8d\x05....\x48\x89\x87\xb8\x02\x00\x00\x44\x89\xbf\xd0\x02\x00\x00', re.DOTALL)
CONVERTER = re.compile(rb'\x48\x8b\x0d....\x48\x85\xc9\x0f\x84....\xf3\x0f\x10\x4f\x20\x0f\x5a\xc9\xe8....\x41\xb8\x03\x00\x00\x00', re.DOTALL)
ALGORITHM_PREFIX = bytes.fromhex('0f28e10f57c0f20f5fe04533c04c8bc9418bd0488bc1')
# Verify arithmetic and control flow, allowing RIP-relative constants to move.
ALGORITHM_CODE = bytes.fromhex(
    '0f28e10f57c0f20f5fe04533c04c8bc9418bd0488bc1f20f5d25000000006690660f2f207610'
    '41ffc048ffc24883c0104883fa087cea4183f808b807000000410f45c04863d0488bca4803c985c0'
    '418b44c9087548f2410f5e24c9ffc0660f6ec8f30fe6c90f28c1f20f5c0d00000000f20f5c05'
    '00000000f20f59e1f20f2cd0f20f582500000000f20f2cc483f8647d06b864000000c33bc20f4fc2c3'
    'f2410f1004c94803d2ffc0f2410f5c64d1f0f2410f5c44d1f0660f6ed0418b44d1f8ffc0f30fe6d2'
    'f20f5ee0660f6ed80f28eaf20f5c2d00000000f30fe6dbf20f5cd3f20f2cc3f20f59e2f20f58e3'
    'f20f2cd43bd07c0bf20f2ccd8bc23bd10f4fc1c3')
ALGORITHM_CONSTANTS = ((0x16, 1.), (0x68, 100.), (0x70, 1.), (0x80, 100.), (0xd0, 1.))
NAME_GETTER = re.compile(rb'\x48\x8b\x81....\x48\x05....\x48\x83\x78\x18\x0f\x76\x03\x48\x8b\x00\xc3', re.DOTALL)
CLASS_NAMES = ('D', 'C', 'B', 'A', 'S1', 'S2', 'R', 'X')
CLASS_CAPS = (400, 500, 600, 700, 800, 900, 998, 999)


def _rip(blob, address, at, displacement=3, length=7):
    return address + at + length + struct.unpack_from('<i', blob, at + displacement)[0]


def decode_native_pi(value, table):
    """Reproduce native interpolation, truncation and class-boundary clamping."""
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('invalid native performance index')
    if len(table) != 128:
        raise ValueError('invalid native PI table size')
    rows = [struct.unpack_from('<di', table, n * 16) for n in range(8)]
    previous = 0.
    for index, (threshold, cap) in enumerate(rows):
        if not math.isfinite(threshold) or not previous < threshold <= 1 or cap != CLASS_CAPS[index]:
            raise ValueError('invalid native PI table')
        previous = threshold
    if rows[-1][0] != 1:
        raise ValueError('native PI table does not end at one')
    index = next((n for n, row in enumerate(rows) if value <= row[0]), 7)
    threshold, high = rows[index]
    if index == 0:
        low = 100
        interpolated = (value / threshold) * ((high + 1) - 100) + 100
    else:
        before, before_cap = rows[index - 1]
        low = before_cap + 1
        interpolated = ((value - before) / (threshold - before)) * ((high + 1) - low) + low
    return max(low, min(high, int(interpolated))), CLASS_NAMES[index], index


class NativeCarReader:
    """Shares the parent reader's handle, bounded image scan and heap scan."""
    def __init__(self, read, base, text, rdata, data):
        self.read, self.base = read, base
        self.text, self.rdata, self.data = text, rdata, data
        self.models, self.mappers, self.getters = {}, {}, {}
        self.roots = set()
        self.model = self.mapping = self.name_getter = None

    @staticmethod
    def _within(address, size, region):
        return region[0] <= address and address + size <= region[0] + region[1]

    def _qword(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def _algorithm_valid(self, address):
        if not self._within(address, len(ALGORITHM_CODE), self.text):
            return False
        raw = self.read(address, len(ALGORITHM_CODE))
        normalized = bytearray(raw)
        for at, value in ALGORITHM_CONSTANTS:
            target = _rip(raw, address, at, 4, 8)
            if not self._within(target, 8, self.rdata) or self.read(target, 8) != struct.pack('<d', value):
                return False
            normalized[at + 4:at + 8] = bytes(4)
        return normalized == ALGORITHM_CODE

    def discover_chunk(self, blob, address):
        for match in PI_PROPERTY.finditer(blob):
            at = match.start()
            cls = CLASS_PROPERTY.search(blob, match.end(), min(len(blob), match.end() + 0x400))
            if cls is None:
                continue
            k = cls.start()
            try:
                pi_label, class_label = _rip(blob, address, at), _rip(blob, address, k)
                if (not self._within(pi_label, 10, self.rdata) or not self._within(class_label, 9, self.rdata)
                        or self.read(pi_label, 10) != b'PerfIndex\0' or self.read(class_label, 9) != b'CarClass\0'):
                    continue
                # Constructor initializes the embedded player reference at +0x88.
                reference_at = at + 0x3a
                if blob[reference_at:reference_at + 8] != bytes.fromhex('488d8f88000000e8'):
                    continue
                ref_ctor = _rip(blob, address, reference_at + 7, 1, 5)
                if not self._within(ref_ctor, 0x30, self.text):
                    continue
                code = self.read(ref_ctor, 0x30)
                if code[0x1f:0x22] != b'\x48\x8d\x05' or code[0x26:0x29] != b'\x48\x89\x03':
                    continue
                model = dict(code_address=address + at, code=blob[at:cls.end()],
                             pi_vtable=_rip(blob, address, at + 16), class_vtable=_rip(blob, address, k + 19),
                             reference_vtable=_rip(code, ref_ctor, 0x1f), reference_code_address=ref_ctor,
                             reference_code=code)
                if all(self._within(model[key], 8, self.rdata) for key in ('pi_vtable', 'class_vtable', 'reference_vtable')):
                    self.models[address + at] = model
            except (OSError, ValueError, struct.error):
                continue
        for match in CONVERTER.finditer(blob):
            at = match.start()
            callee, slot = _rip(blob, address, at + 24, 1, 5), _rip(blob, address, at)
            if not self._within(callee, len(ALGORITHM_PREFIX), self.text) or not self._within(slot, 8, self.data):
                continue
            try:
                if not self._algorithm_valid(callee):
                    continue
                self.mappers[address + at] = dict(code_address=address + at, code=match.group(),
                    mapping_slot=slot, algorithm=callee)
            except OSError:
                continue
        for match in NAME_GETTER.finditer(blob):
            at = match.start()
            pointer_offset, name_offset = struct.unpack_from('<i', blob, at + 3)[0], struct.unpack_from('<i', blob, at + 9)[0]
            if 0 <= pointer_offset <= 0x10000 and 0 <= name_offset <= 0x1000:
                self.getters[address + at] = dict(code_address=address + at, code=match.group(),
                    pointer_offset=pointer_offset, name_offset=name_offset)

    def finish_discovery(self):
        if len(self.models) != 1 or len(self.mappers) != 1 or len(self.getters) != 1:
            raise ValueError('native car signatures unavailable or ambiguous')
        self.model, self.mapping, self.name_getter = (next(iter(items.values())) for items in (self.models, self.mappers, self.getters))

    def _parameters(self, raw):
        if len(raw) != 0x330 or self.model is None:
            return None
        if any(struct.unpack_from('<Q', raw, offset)[0] != self.model[key] for offset, key in (
                (0x48, 'pi_vtable'), (0x88, 'reference_vtable'), (0x2b8, 'class_vtable'))):
            return None
        return (struct.unpack_from('<Q', raw, 0x90)[0], struct.unpack_from('<f', raw, 0x60)[0],
                struct.unpack_from('<I', raw, 0x2d0)[0])

    def observe_heap(self, blob, address):
        if self.model is None:
            return
        needle = struct.pack('<Q', self.model['pi_vtable'])
        at = blob.find(needle)
        while at >= 0:
            start = at - 0x48
            if start >= 0 and (address + start) % 8 == 0:
                values = self._parameters(blob[start:start + 0x330])
                if values is not None and values[0] >= 0x10000:
                    if len(self.roots) >= 256 and address + start not in self.roots:
                        raise ValueError('native car parameter candidate limit exceeded')
                    self.roots.add(address + start)
            at = blob.find(needle, at + 8)

    def _sso(self, address):
        raw = self.read(address, 32)
        length, capacity = struct.unpack_from('<QQ', raw, 16)
        if not 0 < length <= 120 or not length <= capacity <= 4096:
            raise ValueError('invalid native car name')
        value = raw[:length] if capacity < 16 else self.read(struct.unpack_from('<Q', raw)[0], length)
        name = value.decode('ascii')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{2,119}', name) or self.read(address, 32) != raw:
            raise ValueError('unstable native car name')
        return name

    def _spec(self, car):
        """Use the matching interface's RTTI adjustment, never another vtable's this."""
        header = self.read(car, 0x80)
        candidates = []
        getter = self.name_getter
        for offset in range(0, 0x80, 8):
            vt = struct.unpack_from('<Q', header, offset)[0]
            if not self._within(vt - 8, 16, self.rdata):
                continue
            try:
                col = self._qword(vt - 8)
                if not self._within(col, 24, self.rdata):
                    continue
                signature, adjustment, _, _, _, self_rva = struct.unpack('<6I', self.read(col, 24))
                if signature != 1 or adjustment != offset or col != self.base + self_rva:
                    continue
                for slot in range(64):
                    if not self._within(vt + slot * 8, 8, self.rdata):
                        break
                    function = self._qword(vt + slot * 8)
                    if not self._within(function, 1, self.text):
                        break
                    if function == getter['code_address']:
                        candidates.append((offset, vt, slot))
            except (OSError, ValueError, struct.error):
                continue
        if len(candidates) != 1:
            raise ValueError('physical car metadata interface missing or ambiguous')
        offset, vt, slot = candidates[0]
        pointer_field = car + offset + getter['pointer_offset']
        spec = self._qword(pointer_field)
        ordinal_raw = self.read(spec, 4)
        ordinal = struct.unpack('<I', ordinal_raw)[0]
        name = self._sso(spec + getter['name_offset'])
        if not 0 < ordinal <= 1000000:
            raise ValueError('invalid native car ordinal')
        checks = [(car + offset, struct.pack('<Q', vt)), (vt + slot * 8, struct.pack('<Q', getter['code_address'])),
                  (pointer_field, struct.pack('<Q', spec)), (spec, ordinal_raw)]
        return ordinal, name, spec, checks

    def read_for(self, record, process_id):
        """Unknown, changing or multiply bound metadata is never competitive data."""
        try:
            if self.model is None or type(process_id) is not int or process_id <= 0:
                return None
            for item in (self.model, self.mapping, self.name_getter):
                if self.read(item['code_address'], len(item['code'])) != item['code']:
                    return None
            if (self.read(self.model['reference_code_address'], len(self.model['reference_code'])) != self.model['reference_code']
                    or not self._algorithm_valid(self.mapping['algorithm'])):
                return None
            record_id, car, participant = (int(record[key], 16) for key in ('record_id', 'car_id', 'participant_id'))
            record_head = self.read(record_id + 8, 24)
            if struct.unpack_from('<Q', record_head)[0] != participant or struct.unpack_from('<Q', record_head, 16)[0] != car:
                return None
            matched = []
            for root in tuple(self.roots):
                try:
                    raw = self.read(root, 0x330)
                    values = self._parameters(raw)
                except OSError:
                    self.roots.discard(root)
                    continue
                if values is None:
                    self.roots.discard(root)
                elif values[0] == participant + 8:
                    matched.append((root, raw, values))
            if len(matched) != 1:
                return None
            root, raw, (_, index_value, raw_class) = matched[0]
            mapping_slot = self.mapping['mapping_slot']
            table_pointer = self._qword(mapping_slot)
            table = self.read(table_pointer, 128)
            pi, car_class, index = decode_native_pi(index_value, table)
            if raw_class != index:
                return None
            ordinal, name, spec, checks = self._spec(car)
            # Recheck only consumed parameter fields; unrelated assists may change.
            if self._parameters(self.read(root, 0x330)) != self._parameters(raw):
                return None
            if (self._qword(mapping_slot) != table_pointer or self.read(table_pointer, 128) != table
                    or self.read(record_id + 8, 24) != record_head
                    or any(self.read(address, len(value)) != value for address, value in checks)):
                return None
            evidence = dict(source='process_memory', process_id=process_id, record_id=record['record_id'],
                car_id=record['car_id'], participant_id=record['participant_id'], parameter_id=hex(root),
                car_spec_id=hex(spec), normalized_index=index_value, raw_class=raw_class,
                mapping_sha256=hashlib.sha256(table).hexdigest())
            return dict(car_ordinal=ordinal, car_internal_name=name, car_class=car_class,
                        performance_index=pi, metadata_source='process_memory', car_metadata_evidence=evidence)
        except (OSError, ValueError, OverflowError, KeyError, TypeError, struct.error, UnicodeError):
            return None
