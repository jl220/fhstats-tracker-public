import json
import math
from pathlib import Path
import struct

import pytest

from forza_tracker.creator_car_memory import NativeCarReader, decode_native_pi

FIXTURE=json.loads((Path(__file__).parent/'fixtures/creator_car_native_20261022.json').read_text())


class Memory:
    def __init__(self):self.blocks={};self.hook=None
    def read(self,address,size):
        if self.hook:self.hook(address,size)
        for begin,raw in self.blocks.items():
            if begin<=address and address+size<=begin+len(raw):return bytes(raw[address-begin:address-begin+size])
        raise OSError('unmapped memory')


def reader_fixture(shift=0):
    m=Memory();base=FIXTURE['base']+shift
    for block in FIXTURE['blocks']:m.blocks[block['address']+shift]=bytes.fromhex(block['hex'])
    sections={k:(v[0]+shift,v[1]) for k,v in FIXTURE['sections'].items()}
    r=NativeCarReader(m.read,base,sections[b'.text'.decode()],sections['.rdata'],sections['.data'])
    for block in FIXTURE['blocks']:r.discover_chunk(bytes.fromhex(block['hex']),block['address']+shift)
    r.finish_discovery()
    # Small disjoint fake heap containing only fields actually consumed.
    root,record,car,participant,spec,table=0x200000,0x300000,0x400000,0x500000,0x600000,0x700000
    model=r.model
    raw=bytearray(0x330)
    for offset,vt in [(0x48,model['pi_vtable']),(0x88,model['reference_vtable']),(0x2b8,model['class_vtable'])]:
        struct.pack_into('<Q',raw,offset,vt)
    struct.pack_into('<Q',raw,0x90,participant+8)
    struct.pack_into('<f',raw,0x60,0.5068062543869019);struct.pack_into('<I',raw,0x2d0,2)
    m.blocks[root]=raw;r.observe_heap(bytes(raw),root)
    m.blocks[record]=bytearray(0x20);struct.pack_into('<QQ',m.blocks[record],8,participant,participant)
    struct.pack_into('<Q',m.blocks[record],0x18,car)
    head=bytearray(0x400);vt=sections['.rdata'][0]+0x1000;col=vt+0x600
    struct.pack_into('<Q',head,0x60,vt);struct.pack_into('<Q',head,0x2e0,spec);m.blocks[car]=head
    m.blocks[vt-8]=struct.pack('<Q',col)
    vtable=bytearray(32);struct.pack_into('<Q',vtable,0,r.name_getter['code_address']);m.blocks[vt]=vtable
    m.blocks[col]=struct.pack('<6I',1,0x60,0,0,0,col-base)
    data=bytearray(0x350);struct.pack_into('<I',data,0,368)
    name=b'ACU_IntegraR_01';data[0x310:0x310+len(name)]=name
    struct.pack_into('<QQ',data,0x320,len(name),15);m.blocks[spec]=data
    m.blocks[table]=bytes.fromhex(FIXTURE['pi_table_hex'])
    m.blocks[r.mapping['mapping_slot']]=struct.pack('<Q',table)
    sample=dict(record_id=hex(record),car_id=hex(car),participant_id=hex(participant))
    return m,r,sample,root


def test_native_discovery_and_metadata_relocate_with_process_base():
    for shift in (0,0x13000000):
        m,r,sample,root=reader_fixture(shift)
        result=r.read_for(sample,77)
        assert result['car_ordinal']==368 and result['car_internal_name']=='ACU_IntegraR_01'
        assert (result['car_class'],result['performance_index'])==('B',600)
        assert result['metadata_source']=='process_memory'
        proof=result['car_metadata_evidence']
        assert proof['process_id']==77 and proof['car_id']==sample['car_id']
        assert proof['record_id']==sample['record_id'] and proof['participant_id']==sample['participant_id']
        assert proof['parameter_id']==hex(root)


def test_same_player_updates_use_native_piecewise_pi_conversion():
    m,r,sample,root=reader_fixture()
    struct.pack_into('<f',m.blocks[root],0x60,0.775177001953125)
    struct.pack_into('<I',m.blocks[root],0x2d0,5)
    result=r.read_for(sample,77)
    assert (result['car_class'],result['performance_index'])==('S2',881)


def test_changed_algorithm_arithmetic_after_prefix_is_rejected():
    m,r,sample,_=reader_fixture()
    address=r.mapping['algorithm']
    code=bytearray(m.blocks[address]);code[0xec]=0xf3;m.blocks[address]=code
    assert r.read_for(sample,77) is None


def test_changed_algorithm_constant_is_rejected():
    m,r,sample,_=reader_fixture()
    address=r.mapping['algorithm'];raw=m.read(address,256)
    target=address+0x16+8+struct.unpack_from('<i',raw,0x1a)[0]
    m.blocks[target]=struct.pack('<d',2.)
    assert r.read_for(sample,77) is None


@pytest.mark.parametrize('mutation',['wrong_adjustment','ambiguous_interface','replaced_slot'])
def test_invalid_or_ambiguous_rtti_interfaces_are_rejected(mutation):
    m,r,sample,_=reader_fixture();car=int(sample['car_id'],16)
    vt=struct.unpack_from('<Q',m.blocks[car],0x60)[0]
    col=struct.unpack('<Q',m.blocks[vt-8])[0]
    if mutation=='wrong_adjustment':
        raw=bytearray(m.blocks[col]);struct.pack_into('<I',raw,4,0x58);m.blocks[col]=raw
    elif mutation=='replaced_slot':m.blocks[vt]=bytes(32)
    else:
        other_vt=vt+0x100;other_col=col+0x100
        struct.pack_into('<Q',m.blocks[car],0x58,other_vt)
        m.blocks[other_vt-8]=struct.pack('<Q',other_col)
        m.blocks[other_vt]=m.blocks[vt]
        m.blocks[other_col]=struct.pack('<6I',1,0x58,0,0,0,other_col-r.base)
    assert r.read_for(sample,77) is None


@pytest.mark.parametrize('mutation',['other_player','wrong_class','ambiguous','car_changed','spec_changed','mapping_changed','missing_root','getter_changed'])
def test_unbound_inconsistent_or_changing_observations_never_supply_metadata(mutation):
    m,r,sample,root=reader_fixture();car=int(sample['car_id'],16)
    if mutation=='other_player':struct.pack_into('<Q',m.blocks[root],0x90,0x999008)
    elif mutation=='wrong_class':struct.pack_into('<I',m.blocks[root],0x2d0,3)
    elif mutation=='ambiguous':
        other=root+0x1000;m.blocks[other]=bytearray(m.blocks[root]);r.observe_heap(bytes(m.blocks[other]),other)
    elif mutation=='car_changed':struct.pack_into('<Q',m.blocks[int(sample['record_id'],16)],0x18,0x999000)
    elif mutation=='spec_changed':
        count=[0]
        def change(address,size):
            if address==car+0x2e0:
                count[0]+=1
                if count[0]>1:struct.pack_into('<Q',m.blocks[car],0x2e0,0x999000)
        m.hook=change
    elif mutation=='mapping_changed':
        count=[0];slot=r.mapping['mapping_slot']
        def change(address,size):
            if address==slot:
                count[0]+=1
                if count[0]>1:m.blocks[slot]=struct.pack('<Q',0x999000)
        m.hook=change
    elif mutation=='missing_root':m.blocks.pop(root)
    elif mutation=='getter_changed':m.blocks[r.name_getter['code_address']]=bytes(26)
    assert r.read_for(sample,77) is None


def test_zero_default_other_player_and_misaligned_false_hits_do_not_bind():
    m,r,sample,root=reader_fixture()
    r.roots.clear();r.observe_heap(b'\0'+bytes(m.blocks[root]),root)
    assert not r.roots
    r.observe_heap(bytes(m.blocks[root]),root)
    assert r.read_for(sample,77) is not None


def test_pi_conversion_validates_tables_limits_and_nonfinite_input():
    table=bytes.fromhex(FIXTURE['pi_table_hex'])
    assert decode_native_pi(0.,table)==(100,'D',0)
    for index,name in enumerate(('D','C','B','A','S1','S2','R','X')):
        threshold,cap=struct.unpack_from('<di',table,index*16)
        assert decode_native_pi(threshold,table)==(cap,name,index)
        if index<7:assert decode_native_pi(math.nextafter(threshold,math.inf),table)[0]==cap+1
    for value in (True,None,float('nan'),float('inf'),-.1,1.1):
        with pytest.raises(ValueError):decode_native_pi(value,table)
    for raw in (bytes(0x80),table[:64]):
        with pytest.raises(ValueError):decode_native_pi(.5,raw)
