import struct
from uuid import UUID

from forza_tracker.creator_memory import decode_blueprint_metadata, discover_code, read_race_record, active_blueprint_link
from forza_tracker.creator_memory import CreatorMemoryReader
from forza_tracker.rivals_memory import RECORD_CLEAN_GETTER_SIGNATURE


def test_code_discovery_relocates_signatures_without_a_build_number():
    primary, secondary = 0x10001000, 0x10002000
    at = 0x100
    ctor = (b'\x48\x8d\x05'+struct.pack('<i',primary-(at+7))+b'\x48\x89\x01'
            +b'\x48\x8d\x05'+struct.pack('<i',secondary-(at+17))
            +bytes.fromhex('488941084883b95003000000'))
    blob = b'\0'*at + ctor + b'\0'*40 + RECORD_CLEAN_GETTER_SIGNATURE
    found = discover_code(blob, 0)
    assert found['blueprint_vtables'] == [(primary, secondary)]
    assert len(found['clean_getters']) == 1


def test_blueprint_metadata_requires_same_guid_and_valid_inline_code():
    guid = UUID('bf5ca8f8-ed33-4d41-b887-0a8b72df2dbf')
    raw = bytearray(0x50)
    raw[:16] = guid.bytes_le
    struct.pack_into('<II',raw,0x10,4,1)
    raw[0x28:0x31] = b'477632104'
    struct.pack_into('<QQ',raw,0x38,9,15)
    assert decode_blueprint_metadata(bytes(raw),guid.bytes_le) == {'blueprint_code':'477632104','configured_laps':4,'max_players':1}
    assert decode_blueprint_metadata(bytes(raw),bytes(16)) is None
    raw[0x28] = 0
    assert decode_blueprint_metadata(bytes(raw),guid.bytes_le) is None


class Memory:
    def __init__(self):
        self.blocks = {}
    def read(self, handle, addr, size):
        for begin,raw in self.blocks.items():
            if begin <= addr and addr+size <= begin+len(raw):return raw[addr-begin:addr-begin+size]
        raise OSError('unreadable')


def test_record_uses_memory_flags_and_last_lap_with_coherent_pointer_checks():
    b=Memory();slot,manager,vector,record,vt,car,getter=0x1000,0x2000,0x3000,0x4000,0x5000,0x6000,0x7000
    b.blocks[slot]=struct.pack('<Q',manager)
    raw=bytearray(0x80);struct.pack_into('<QQ',raw,0x70,vector,vector+8);b.blocks[manager]=bytes(raw)
    b.blocks[vector]=struct.pack('<Q',record)
    raw=bytearray(0x140);struct.pack_into('<Q',raw,0,vt);struct.pack_into('<Q',raw,8,0x8000);struct.pack_into('<Q',raw,0x18,car)
    struct.pack_into('<ddd',raw,0x70,10.,9.,8.);struct.pack_into('<I',raw,0x8c,1);struct.pack_into('<d',raw,0x138,12.)
    b.blocks[record]=bytes(raw);raw=bytearray(0x40);struct.pack_into('<Q',raw,0x38,getter);b.blocks[vt]=bytes(raw)
    b.blocks[car+0x90a4]=bytes([1,0,0,0,0,0,0]);b.blocks[getter]=RECORD_CLEAN_GETTER_SIGNATURE
    name=bytearray(32);name[:8]=b'Jolu4327';struct.pack_into('<QQ',name,16,8,15);b.blocks[0x8000+0x1d8]=bytes(name)
    r=read_race_record(b,None,slot,getter)
    assert r['race_clock']==12. and r['completed_laps']==1 and r['last_lap_seconds']==9.
    assert r['flags']=='01000000000000'
    assert r['participant_id']==hex(0x8000)
    b.blocks[vector]=struct.pack('<Q',record)*2
    raw=bytearray(b.blocks[manager]);struct.pack_into('<QQ',raw,0x70,vector,vector+16);b.blocks[manager]=bytes(raw)
    assert read_race_record(b,None,slot,getter) is None


def changing_record(*, changed_offset, changed_bytes):
    """A live record changes between reads, as during normal game updates."""
    class ChangingMemory(Memory):
        def __init__(self):
            super().__init__()
            self.record_reads = 0

        def read(self, handle, addr, size):
            result = super().read(handle, addr, size)
            if addr == 0x4000 and size == 0x140:
                self.record_reads += 1
                if self.record_reads >= 2:
                    raw = bytearray(result)
                    raw[changed_offset:changed_offset+len(changed_bytes)] = changed_bytes
                    return bytes(raw)
            return result

    b = ChangingMemory()
    b.blocks[0x1000] = struct.pack('<Q', 0x2000)
    manager = bytearray(0x80)
    struct.pack_into('<QQ', manager, 0x70, 0x3000, 0x3008)
    b.blocks[0x2000] = manager
    b.blocks[0x3000] = struct.pack('<Q', 0x4000)
    record = bytearray(0x140)
    struct.pack_into('<QQQQ', record, 0, 0x5000, 0x8000, 0x8000, 0x6000)
    struct.pack_into('<dd', record, 0x70, 0., 0.)
    struct.pack_into('<d', record, 0x138, 12.)
    b.blocks[0x4000] = record
    table = bytearray(0x40)
    struct.pack_into('<Q', table, 0x38, 0x7000)
    b.blocks[0x5000] = table
    b.blocks[0x7000] = RECORD_CLEAN_GETTER_SIGNATURE
    b.blocks[0x6000+0x90a4] = bytes(7)
    name = bytearray(32)
    name[:8] = b'Jolu4327'
    struct.pack_into('<QQ', name, 16, 8, 15)
    b.blocks[0x8000+0x1d8] = name
    return b


def test_live_auxiliary_field_change_does_not_discard_stable_competitive_fields():
    b = changing_record(changed_offset=0x68, changed_bytes=struct.pack('<d', 1.))
    result = read_race_record(b, None, 0x1000, 0x7000)
    assert result is not None
    assert result['race_clock'] == 12.
    assert result['completed_laps'] == 0
    assert result['flags'] == '00000000000000'


def test_timing_identity_and_phase_changes_are_still_rejected():
    for offset, value in ((0, struct.pack('<Q', 0x5100)),
                          (8, struct.pack('<Q', 0x8100)),
                          (0x18, struct.pack('<Q', 0x6100)),
                          (0x70, struct.pack('<d', 1.)),
                          (0x78, struct.pack('<d', 1.)),
                          (0x8c, struct.pack('<I', 1)),
                          (0x100, b'\x01')):
        b = changing_record(changed_offset=offset, changed_bytes=value)
        assert read_race_record(b, None, 0x1000, 0x7000) is None, hex(offset)


def test_dirty_flags_changing_during_the_record_read_are_rejected():
    b = changing_record(changed_offset=0x68, changed_bytes=bytes(8))
    original_read = b.read
    flag_reads = []

    def read(handle, addr, size):
        if addr == 0x6000+0x90a4:
            flag_reads.append(addr)
            return bytes(7) if len(flag_reads) == 1 else bytes([1, 0, 0, 0, 0, 0, 0])
        return original_read(handle, addr, size)

    b.read = read
    assert read_race_record(b, None, 0x1000, 0x7000) is None


def test_retained_selected_blueprint_is_not_an_active_race_link():
    b=Memory();career,card=0x1000,0x2000;tables=(0x3000,0x4000,0x5000)
    header=bytearray(0x210)
    struct.pack_into('<QQ',header,0,*tables[:2]);struct.pack_into('<Q',header,0x38,tables[2])
    b.blocks[career]=bytes(header);guid=UUID('bf5ca8f8-ed33-4d41-b887-0a8b72df2dbf').bytes_le
    raw=bytearray(0x90);raw[0x80:]=guid;b.blocks[card]=bytes(raw)
    assert active_blueprint_link(b,None,career,tables,card,guid) is None
    struct.pack_into('<Q',header,0x208,card);b.blocks[career]=bytes(header)
    assert active_blueprint_link(b,None,career,tables,card,guid)['card_id']==hex(card)
    raw[0x80:]=bytes(16);b.blocks[card]=bytes(raw)
    assert active_blueprint_link(b,None,career,tables,card,guid) is None


def test_idle_known_modules_do_not_repeat_heap_or_image_discovery():
    class Backend:
        def process_alive(self,h):return True
        def close_handle(self,h):raise AssertionError('idle context must stay attached')
    r=CreatorMemoryReader(backend=Backend())
    r.h=object();r.pid=123;r.module=None;r._module_roots={0x1000};r._scan=None
    r._blueprint=lambda address:None
    for _ in range(3):assert r.sample()['status']=='waiting_for_event'


def test_active_blueprint_without_a_matching_cached_module_gets_bounded_rediscovery(monkeypatch):
    class Backend:
        def process_alive(self,h):return True
        def read(self,h,address,size):
            assert (address,size)==(0x1000+0x208,8)
            return struct.pack('<Q',0x2000)
    r=CreatorMemoryReader(backend=Backend())
    r.h=object();r.pid=17;r.career=(0x1000,(1,2,3));r._module_roots={0x3000}
    r._blueprint=lambda address:None
    scans=[];r._start_scan=lambda stage:scans.append(stage)
    monkeypatch.setattr('forza_tracker.creator_memory.time.monotonic',lambda:100.)
    assert r.sample()['status']=='scanning'
    assert scans==['module']
    assert r.sample()['status']=='waiting_for_event'
    assert scans==['module']


def test_free_drive_without_active_blueprint_does_not_trigger_extra_discovery(monkeypatch):
    class Backend:
        def process_alive(self,h):return True
        def read(self,h,address,size):return bytes(size)
    r=CreatorMemoryReader(backend=Backend())
    r.h=object();r.pid=17;r.career=(0x1000,(1,2,3));r._module_roots={0x3000}
    r._blueprint=lambda address:None
    def forbidden(stage):raise AssertionError('no active blueprint to rediscover')
    r._start_scan=forbidden
    for _ in range(3):assert r.sample()['status']=='waiting_for_event'


def test_retained_valid_module_refreshes_when_career_points_to_a_different_card():
    class Backend:
        def process_alive(self,h):return True
        def read(self,h,address,size):
            if (address,size)==(0x1000,0x210):
                raw=bytearray(size)
                struct.pack_into('<QQ',raw,0,11,22);struct.pack_into('<Q',raw,0x38,33)
                struct.pack_into('<Q',raw,0x208,0x4000)
                return bytes(raw)
            if (address,size)==(0x1208,8):return struct.pack('<Q',0x4000)
            raise AssertionError((address,size))
    r=CreatorMemoryReader(backend=Backend())
    r.h=object();r.pid=17;r.career=(0x1000,(11,22,33));r.metadata={}
    module=dict(module_address=0x3000,card_address=0x2000,blueprint_guid='bf5ca8f8-ed33-4d41-b887-0a8b72df2dbf',name='Old selected track')
    r.module=module;r._blueprint=lambda address:module
    scans=[];r._start_scan=lambda stage:scans.append(stage)
    assert r.sample()['status']=='scanning'
    assert r.module is None and scans==['module']
    assert r.sample()['status']=='waiting_for_event'
    assert scans==['module']


def test_reader_attaches_native_car_metadata_to_the_same_solo_record():
    from test_creator_car_memory import reader_fixture
    m,car_reader,identity,root=reader_fixture()
    class Backend:
        def read(self,h,a,n):return m.read(a,n)
        def process_alive(self,h):return True
        def close_handle(self,h):pass
    b=Backend();r=CreatorMemoryReader(backend=b)
    r.h=object();r.pid=77;r.build='test';r.car_reader=car_reader
    slot,manager,vector,vt,getter=0x800000,0x810000,0x820000,0x830000,0x840000
    record=int(identity['record_id'],16);car=int(identity['car_id'],16);participant=int(identity['participant_id'],16)
    m.blocks[slot]=struct.pack('<Q',manager);raw=bytearray(0x80)
    struct.pack_into('<QQ',raw,0x70,vector,vector+8);m.blocks[manager]=raw;m.blocks[vector]=struct.pack('<Q',record)
    raw=bytearray(0x140);struct.pack_into('<QQQQ',raw,0,vt,participant,participant,car)
    struct.pack_into('<d',raw,0x70,1.);struct.pack_into('<d',raw,0x138,1.5);m.blocks[record]=raw
    raw=bytearray(0x40);struct.pack_into('<Q',raw,0x38,getter);m.blocks[vt]=raw
    m.blocks[getter]=RECORD_CLEAN_GETTER_SIGNATURE;m.blocks[car+0x90a4]=bytes(7)
    raw=bytearray(32);raw[:8]=b'Jolu4327';struct.pack_into('<QQ',raw,16,8,15);m.blocks[participant+0x1d8]=raw
    guid='bf5ca8f8-ed33-4d41-b887-0a8b72df2dbf';career,card=0x850000,0x860000;tables=(1,2,3)
    raw=bytearray(0x210);struct.pack_into('<QQ',raw,0,1,2);struct.pack_into('<Q',raw,0x38,3)
    struct.pack_into('<Q',raw,0x208,card);m.blocks[career]=raw
    raw=bytearray(0x90);raw[0x80:]=UUID(guid).bytes_le;m.blocks[card]=raw
    r.career=(career,tables);r.module=dict(module_address=0x870000,card_address=card,blueprint_guid=guid,name='Sportsland')
    r._blueprint=lambda _:r.module;r.metadata=dict(blueprint_code='477632104',configured_laps=4,max_players=1)
    r.slot=slot;r.getter=getter
    reading=r.sample()
    assert reading['status']=='available'
    assert (reading['car_ordinal'],reading['car_class'],reading['performance_index'])==(368,'B',600)
    assert reading['car_metadata_evidence']['record_id']==identity['record_id']
    assert reading['metadata_source']=='process_memory'
    # A second player's valid parameters cannot complete this player's lap.
    struct.pack_into('<Q',m.blocks[root],0x90,0x999008)
    r._car_retry_at=float('inf')
    assert r.sample()['status']=='car_metadata_unavailable'
    # A later allocation is discovered without replacing the active event.
    car_reader.roots.clear()
    struct.pack_into('<Q',m.blocks[root],0x90,participant+8)
    module=r.module;career_context=r.career
    r._heap=lambda:iter([(root,bytes(m.blocks[root]))])
    r._start_scan('car');r._discover_step()
    assert r.module is module and r.career==career_context
    assert r.sample()['performance_index']==600
    r.release()
    assert r.car_reader is None


def test_module_scan_selects_only_the_active_card_among_retained_modules():
    b=Memory();r=CreatorMemoryReader(backend=b);r.h=object()
    career=0x1000;tables=(11,22,33);card=0x4000
    header=bytearray(0x210)
    struct.pack_into('<QQ',header,0,*tables[:2]);struct.pack_into('<Q',header,0x38,tables[2])
    struct.pack_into('<Q',header,0x208,card);b.blocks[career]=bytes(header)
    guid=UUID('ad7f991a-897c-419b-a366-8a815fd99be7')
    raw=bytearray(0x90);raw[0x80:]=guid.bytes_le;b.blocks[card]=bytes(raw)
    old=dict(module_address=0x3000,card_address=0x2000,blueprint_guid=str(guid),name='Old')
    active=dict(module_address=0x5000,card_address=card,blueprint_guid=str(guid),name='CC')
    r._candidates={0x3000:old,0x5000:active};r._career_candidates={career:tables}
    r._stage='module';r._scan=iter(())
    r._discover_step()
    assert r.module==active and r.career==(career,tables)
    # Two module identities claiming the same active card remain ambiguous.
    r._candidates[0x3000]={**old,'card_address':card}
    r._scan=iter(())
    import pytest
    with pytest.raises(ValueError,match='ambiguous'):r._discover_step()
