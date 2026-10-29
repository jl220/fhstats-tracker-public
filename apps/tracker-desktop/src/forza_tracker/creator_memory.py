"""Read-only EventLab observation with bounded signature discovery.

Executable version is diagnostic metadata. No getter is executed and no game
memory is written. A cached title is never used as active event identity.
"""
from __future__ import annotations

import math
import json
import re
import struct
import threading
import time
from uuid import UUID
from pathlib import Path

from .rivals_memory import PROCESS_ACCESS, RECORD_CLEAN_GETTER_SIGNATURE, _WindowsBackend
from .creator_car_memory import NativeCarReader

CHUNK = 1024*1024
CTOR = re.compile(rb'\x48\x8d\x05....\x48\x89\x01\x48\x8d\x05....\x48\x89\x41\x08\x48\x83\xb9\x50\x03\x00\x00\x00', re.DOTALL)
CAREER_CTOR = re.compile(rb'\x48\x8d\x05....\x48\x89\x01\x48\x8d\x05....\x48\x89\x41\x08\x48\x8d\x05....\x48\x89\x41\x38\x48\x81\xc1\x38\x03\x00\x00', re.DOTALL)


def discover_code(blob, address):
    pairs = []
    for match in CTOR.finditer(blob):
        p = match.start()
        pairs.append((address+p+7+struct.unpack_from('<i',blob,p+3)[0],
                      address+p+17+struct.unpack_from('<i',blob,p+13)[0]))
    hits = []
    at = 0
    while (at := blob.find(RECORD_CLEAN_GETTER_SIGNATURE, at)) >= 0:
        hits.append(address+at)
        at += 1
    career=[]
    for match in CAREER_CTOR.finditer(blob):
        p=match.start()
        career.append(tuple(address+p+off+7+struct.unpack_from('<i',blob,p+off+3)[0] for off in (0,10,21)))
    return dict(blueprint_vtables=pairs, clean_getters=hits, career_vtables=career)


def decode_blueprint_metadata(raw, guid_bytes):
    if len(raw)<0x48 or raw[:16] != guid_bytes:
        return None
    laps, players = struct.unpack_from('<II', raw, 0x10)
    length, capacity = struct.unpack_from('<QQ', raw, 0x38)
    code = raw[0x28:0x31]
    if length != 9 or capacity != 15 or raw[0x31] != 0 or not code.isdigit():
        return None
    if not 1 <= laps <= 10000 or not 1 <= players <= 100:
        return None
    return dict(blueprint_code=code.decode('ascii'), configured_laps=laps, max_players=players)


def qword(b, handle, address):
    return struct.unpack('<Q', b.read(handle,address,8))[0]


def read_sso(b, h, address, *, limit=120):
    raw=b.read(h,address,32)
    length,capacity=struct.unpack_from('<QQ',raw,16)
    if not 0<length<=limit or not length<=capacity<=4096:
        raise ValueError('invalid memory string')
    text=(raw[:length] if capacity<16 else b.read(h,struct.unpack_from('<Q',raw)[0],length)).decode('utf-8')
    if not text.isprintable() or b.read(h,address,32)!=raw:
        raise ValueError('unstable memory string')
    return text


def active_blueprint_link(b,h,career,tables,card,guid):
    """CareerModule drops this shared pointer on leaving the actual event."""
    raw=b.read(h,career,0x210)
    if tuple(struct.unpack_from('<Q',raw,o)[0] for o in (0,8,0x38)) != tables:
        return None
    active=struct.unpack_from('<Q',raw,0x208)[0]
    if not active or active!=card or b.read(h,active+0x80,16)!=guid:
        return None
    if b.read(h,career+0x208,8)!=struct.pack('<Q',active):
        return None
    return dict(career_id=hex(career),card_id=hex(active),blueprint_guid=str(UUID(bytes_le=guid)))


def read_race_record(b, h, slot, getter):
    if b.read(h,getter,len(RECORD_CLEAN_GETTER_SIGNATURE)) != RECORD_CLEAN_GETTER_SIGNATURE:
        return None
    manager = qword(b,h,slot)
    bounds = b.read(h,manager+0x70,16)
    begin,end = struct.unpack('<QQ',bounds)
    # Community circuit capture currently supports one player, no bots.
    if not begin or end-begin != 8:
        return None
    record = qword(b,h,begin)
    raw = b.read(h,record,0x140)
    vtable,car = struct.unpack_from('<Q',raw)[0],struct.unpack_from('<Q',raw,0x18)[0]
    if qword(b,h,vtable+0x38) != getter:
        return None
    clock,start,last = (struct.unpack_from('<d',raw,o)[0] for o in (0x138,0x70,0x78))
    flags = b.read(h,car+0x90a4,7)
    repeated = b.read(h,record,0x140)
    clock2 = struct.unpack_from('<d',repeated,0x138)[0]
    # The game updates auxiliary values in this record while racing. Bracket
    # the fields we consume, plus the observed phase, rather than requiring
    # every unrelated byte in the object to stay frozen between reads.
    stable_ranges = ((0,0x20),(0x70,0x80),(0x8c,0x90),(0x100,0x104))
    if (not all(math.isfinite(n) and n>=0 for n in (clock,start,last,clock2))
            or clock<start or abs(clock2-clock)>.2
            or any(repeated[begin:end]!=raw[begin:end] for begin,end in stable_ranges)
            or b.read(h,car+0x90a4,7)!=flags
            or b.read(h,slot,8) != struct.pack('<Q',manager)
            or b.read(h,manager+0x70,16) != bounds
            or qword(b,h,begin) != record or qword(b,h,vtable+0x38) != getter):
        return None
    participant=struct.unpack_from('<Q',raw,8)[0]
    gamertag=read_sso(b,h,participant+0x1d8,limit=40)
    return dict(record_id=hex(record),car_id=hex(car),participant_id=hex(participant),participant_count=1,gamertag=gamertag,
                race_clock=clock,lap_start_clock=start,last_lap_seconds=last,
                completed_laps=struct.unpack_from('<I',raw,0x8c)[0],flags=flags.hex())


class CreatorMemoryReader:
    def __init__(self, *, backend=None, blueprint_cache=None):
        self.b = backend or _WindowsBackend()
        self.h = None
        self.pid = self.base = None
        self.cancel = threading.Event()
        self._stage = self._scan = None
        self._retry_at = 0.
        self.module = self.metadata = None
        self.career = None
        self._career_candidates = {}
        self._module_roots=set()
        self._module_retry_at=0.
        self._candidates = {}
        self.getter = self.slot = None
        self._slot_retry_at=0.
        self.car_reader=None
        self._car_retry_at=0.
        self.blueprint_cache=Path(blueprint_cache) if blueprint_cache is not None else None

    def _metadata_for(self,guid):
        # Share-code headers can be freed after loading. The active GUID is
        # authoritative; remembered code headers are optional display metadata.
        result={'blueprint_code':None,'configured_laps':None,'max_players':None}
        if self.blueprint_cache is None:return result
        directory=self.blueprint_cache/guid
        try:
            candidates={}
            for path in directory.glob('*.json'):
                if path.stat().st_size>4096:continue
                try:
                    header=bytes.fromhex(json.loads(path.read_text(encoding='utf8'))['memory_header_hex'])
                    decoded=decode_blueprint_metadata(header,UUID(guid).bytes_le)
                    if decoded:candidates[decoded['blueprint_code']]=decoded
                except (ValueError,KeyError,TypeError):continue
            return next(iter(candidates.values())) if len(candidates)==1 else result
        except OSError:return result

    def close(self):
        self.cancel.set()

    def release(self):
        if self.h is not None:
            self.b.close_handle(self.h)
        self.h = None
        self.module = self.metadata = self._scan = None
        self.career = None
        self._module_roots=set()
        self._module_retry_at=0.
        self._slot_retry_at=0.
        self.getter = self.slot = None
        self.car_reader=None
        self._car_retry_at=0.

    def _image(self):
        head = self.b.read(self.h,self.base,4096)
        pe = struct.unpack_from('<I',head,0x3c)[0]
        if pe>2048 or head[:2] != b'MZ' or head[pe:pe+4] != b'PE\0\0':
            raise ValueError('invalid executable headers')
        count = struct.unpack_from('<H',head,pe+6)[0]
        optional = struct.unpack_from('<H',head,pe+20)[0]
        sections = {}
        for i in range(min(count,32)):
            at=pe+24+optional+i*40
            name=head[at:at+8].split(b'\0')[0]
            size,rva=struct.unpack_from('<II',head,at+8)
            if 0<size<=160*1024*1024 and rva+size<=512*1024*1024:
                sections[name]=(self.base+rva,size)
        text=sections[b'.text'];self.data=sections[b'.data']
        self.car_reader=NativeCarReader(lambda a,n:self.b.read(self.h,a,n),self.base,
                                        text,sections[b'.rdata'],self.data)
        getters=set();pairs=set();careers=set();slots=set();previous=b'';deadline=time.monotonic()+5
        for off in range(0,text[1],CHUNK):
            if self.cancel.is_set() or time.monotonic()>deadline:
                raise ValueError('image scan budget exceeded')
            raw=self.b.read(self.h,text[0]+off,min(CHUNK,text[1]-off))
            blob=previous+raw;address=text[0]+off-len(previous)
            found=discover_code(blob,address);getters.update(found['clean_getters']);pairs.update(found['blueprint_vtables'])
            self.car_reader.discover_chunk(blob,address)
            careers.update(found['career_vtables'])
            for match in re.finditer(re.escape(b'\x48\x8b\x0d'),blob):
                at=match.start()
                if at+7>len(blob):continue
                target=address+at+7+struct.unpack_from('<i',blob,at+3)[0]
                if self.data[0]<=target<self.data[0]+self.data[1]-8:slots.add(target)
            previous=blob[-2048:]
        if len(getters)!=1 or not pairs or not careers:
            raise ValueError('memory signatures unavailable or ambiguous')
        self.getter=next(iter(getters));self.pairs=pairs;self.slots=slots;self.career_tables=careers
        self.car_reader.finish_discovery()

    def _heap(self):
        total=0;started=time.monotonic()
        for address,size,eligible in self.b.writable_private_regions(self.h):
            # Module instances live in object arenas. Avoid paging through large
            # graphics/geometry allocations while the player is racing.
            if not eligible or size>128*1024*1024:continue
            previous=b''
            for off in range(0,size,CHUNK):
                if self.cancel.is_set():return
                if total>=8*1024**3 or time.monotonic()-started>60:
                    raise ValueError('heap scan budget exceeded')
                n=min(CHUNK,size-off)
                try:raw=self.b.read(self.h,address+off,n)
                except OSError:previous=b'';continue
                total+=n
                yield address+off-len(previous), previous+raw
                previous=raw[-1024:]

    def _start_scan(self,stage):
        self._stage=stage;self._scan=self._heap()
        if stage=='module':self._candidates={};self._career_candidates={};self._module_roots=set()

    def _blueprint(self,address):
        head=self.b.read(self.h,address,0x54)
        pair=struct.unpack_from('<QQ',head)
        if pair not in self.pairs or struct.unpack_from('<I',head,0x50)[0] != 5:
            return None
        card=struct.unpack_from('<Q',head,0x38)[0]
        raw=self.b.read(self.h,card,0x90)
        guid=raw[0x80:0x90]
        if guid==bytes(16):return None
        sso=raw[0x38:0x58];length,capacity=struct.unpack_from('<QQ',sso,16)
        if not 0<length<=120 or not length<=capacity<=4096:return None
        title=(sso[:length] if capacity<16 else self.b.read(self.h,struct.unpack_from('<Q',sso)[0],length)).decode('utf-8')
        if not title.isprintable():return None
        if self.b.read(self.h,address,0x54)!=head or self.b.read(self.h,card+0x80,16)!=guid:return None
        return dict(module_address=address,card_address=card,blueprint_guid=str(UUID(bytes_le=guid)),name=title)

    def _discover_step(self):
        deadline=time.monotonic()+.04
        while time.monotonic()<deadline:
            try:address,blob=next(self._scan)
            except StopIteration:
                self._scan=None
                if self._stage=='car':return
                if len(self._career_candidates)!=1:
                    raise ValueError('active career context missing or ambiguous')
                self.career=next(iter(self._career_candidates.items()))
                candidates=self._active_candidates(list(self._candidates.values()))
                self.module=next(iter(candidates),None)
                self.metadata=self._metadata_for(self.module['blueprint_guid']) if self.module else None
                return
            if self.car_reader is not None:self.car_reader.observe_heap(blob,address)
            if self._stage=='module':
                for tables in self.career_tables:
                    needle=struct.pack('<QQ',*tables[:2]);at=0
                    while (at:=blob.find(needle,at))>=0:
                        try:
                            if qword(self.b,self.h,address+at+0x38)==tables[2]:
                                self._career_candidates[address+at]=tables
                        except OSError:pass
                        at+=8
                for pair in self.pairs:
                    needle=struct.pack('<QQ',*pair);at=0
                    while (at:=blob.find(needle,at))>=0:
                        try:
                            self.b.read(self.h,address+at,0x54)
                            self._module_roots.add(address+at)
                            candidate=self._blueprint(address+at)
                            if candidate:self._candidates[address+at]=candidate
                        except (OSError,UnicodeError):pass
                        at+=8

    def _active_candidates(self,candidates):
        if len(candidates)<=1:return candidates
        career,tables=self.career
        active=[]
        for candidate in candidates:
            try:
                if active_blueprint_link(self.b,self.h,career,tables,
                        candidate['card_address'],UUID(candidate['blueprint_guid']).bytes_le):
                    active.append(candidate)
            except (OSError,ValueError,struct.error):pass
        if len(active)>1:raise ValueError('event identity missing or ambiguous')
        return active

    def _rediscover_active_card(self,expected_card=None):
        if self.career is None:return False
        now=time.monotonic()
        if now<self._module_retry_at:return False
        active=qword(self.b,self.h,self.career[0]+0x208)
        if not active or active==expected_card:return False
        self._module_retry_at=now+60
        self.module=self.metadata=self.slot=None
        self._start_scan('module')
        return True

    def sample(self):
        if self.cancel.is_set():return {'status':'closed'}
        try:
            if self.h is not None and not self.b.process_alive(self.h):
                self.release();return {'status':'process_exit'}
            if self.h is None:
                if time.monotonic()<self._retry_at:return {'status':'waiting'}
                process=self.b.find_process()
                if process is None:return {'status':'process_exit'}
                self.pid,self.base,path=process;self.h=self.b.open_process(self.pid,PROCESS_ACCESS)
                self.build=self.b.file_version(path);self._image();self._start_scan('module')
            if self._scan is not None:
                self._discover_step()
                return {'status':'scanning','process_id':self.pid}
            if self.module is None:
                selected=[]
                for address in self._module_roots:
                    try:
                        candidate=self._blueprint(address)
                        if candidate:selected.append(candidate)
                    except (OSError,UnicodeError):pass
                if self.career is not None:selected=self._active_candidates(selected)
                if len(selected)!=1:
                    # EventLab can allocate a new module after an earlier idle
                    # scan. Only an active career blueprint triggers a bounded
                    # refresh; free driving must not keep rescanning the heap.
                    if self._rediscover_active_card():
                        return {'status':'scanning','process_id':self.pid}
                    return {'status':'waiting_for_event','process_id':self.pid}
                self.module=selected[0];self.metadata=self._metadata_for(self.module['blueprint_guid'])
            current=self._blueprint(self.module['module_address'])
            if current != self.module:
                self.module=current;self.metadata=self._metadata_for(current['blueprint_guid']) if current else None
                self.slot=None
                return {'status':'event_changed','process_id':self.pid}
            career,tables=self.career
            guid=UUID(current['blueprint_guid']).bytes_le
            link=active_blueprint_link(self.b,self.h,career,tables,current['card_address'],guid)
            if link is None:
                if self._rediscover_active_card(current['card_address']):
                    return {'status':'scanning','process_id':self.pid}
                return dict(status='no_active_blueprint',process_id=self.pid,event={**current,**self.metadata})
            if self.slot is None:
                if time.monotonic()<self._slot_retry_at:
                    return dict(status='no_solo_race',process_id=self.pid,event={**current,**self.metadata})
                candidates=[]
                for slot in self.slots:
                    if self.cancel.is_set():return {'status':'closed'}
                    try:
                        if read_race_record(self.b,self.h,slot,self.getter):candidates.append(slot)
                    except (OSError,OverflowError,ValueError,struct.error,UnicodeError):pass
                if len(candidates)!=1:
                    self._slot_retry_at=time.monotonic()+2
                    return dict(status='no_solo_race',process_id=self.pid,event={**current,**self.metadata})
                self.slot=candidates[0]
            record=read_race_record(self.b,self.h,self.slot,self.getter)
            if record is None:
                self.slot=None
                return {'status':'unavailable','process_id':self.pid}
            car_metadata=self.car_reader.read_for(record,self.pid) if self.car_reader is not None else None
            if car_metadata is None:
                # A new participant model may be allocated after the initial scan.
                # Rediscover only while a verified solo blueprint is active.
                if self.car_reader is not None and time.monotonic()>=self._car_retry_at:
                    self._car_retry_at=time.monotonic()+30
                    self._start_scan('car')
                return dict(status='car_metadata_unavailable',process_id=self.pid,event={**current,**self.metadata})
            repeated=read_race_record(self.b,self.h,self.slot,self.getter)
            if repeated is None or any(repeated[key]!=record[key] for key in (
                    'record_id','car_id','participant_id','gamertag','completed_laps','lap_start_clock','flags')):
                return {'status':'unavailable','process_id':self.pid}
            # Bracket race observation by the selected blueprint's identity.
            if self._blueprint(self.module['module_address'])!=current:return {'status':'event_changed'}
            if active_blueprint_link(self.b,self.h,career,tables,current['card_address'],guid)!=link:
                return {'status':'event_changed'}
            return dict(status='available',process_id=self.pid,build=self.build,**record,**car_metadata,
                        **{k:v for k,v in current.items() if k not in ('module_address','card_address')},
                        **self.metadata,identity_verified=True,identity_source='memory_active_career_blueprint',identity_evidence=link)
        except (OSError,ValueError,KeyError,OverflowError,struct.error,UnicodeError) as error:
            self.release();self._retry_at=time.monotonic()+15
            return {'status':'unavailable','reason':str(error),'process_id':self.pid}
