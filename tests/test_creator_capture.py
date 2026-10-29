from datetime import datetime, timezone
from uuid import uuid4
import json
from pathlib import Path

from forza_tracker.creator_capture import CleanLapCapture
from forza_tracker.creator_store import CreatorStore


GUID = 'bf5ca8f8-ed33-4d41-b887-0a8b72df2dbf'


def sample(clock=0., start=0., laps=0, *, flags='00000000000000', **changes):
    row = dict(process_id=17, record_id='0x123', car_id='0x456', participant_count=1,
               race_clock=clock, lap_start_clock=start, completed_laps=laps,
               last_lap_seconds=0., flags=flags, status='available',
               blueprint_guid=GUID, blueprint_code='477632104', identity_verified=True,
               car_ordinal=1155, car_class='S1', performance_index=800,
               metadata_source='memory', profile_id='Jolu4327')
    row.update(changes)
    return row


def complete(capture, start=1., duration=1., *, dirty=False, ordinal=1155):
    capture.update(sample(start, start, car_ordinal=ordinal), observed_at=start)
    capture.update(sample(start+.5, start, flags='01000000000000' if dirty else '00000000000000', car_ordinal=ordinal), observed_at=start+.5)
    return capture.update(sample(start+duration, start+duration, 1, last_lap_seconds=duration, car_ordinal=ordinal), observed_at=start+duration)


def test_clean_complete_lap_and_dirty_latch():
    c = CleanLapCapture('Jolu4327')
    assert complete(c)[0]['time_ms'] == 1000
    c = CleanLapCapture('Jolu4327')
    assert complete(c, dirty=True) == []


def test_first_partial_lap_is_never_saved():
    c = CleanLapCapture('Jolu4327')
    c.update(sample(20., 1.), observed_at=20.)
    assert c.update(sample(21., 21., 1, last_lap_seconds=20.), observed_at=21.) == []


def test_active_guid_can_identify_a_lap_when_share_code_text_is_not_resident():
    c=CleanLapCapture('Jolu4327')
    c.update(sample(1.,1.,blueprint_code=None),observed_at=1.)
    c.update(sample(1.5,1.,blueprint_code=None),observed_at=1.5)
    result=c.update(sample(2.,2.,1,last_lap_seconds=1.,blueprint_code=None),observed_at=2.)
    assert result[0]['blueprint_guid']==GUID and result[0]['blueprint_code'] is None


def test_rewind_invalidates_only_current_lap():
    c = CleanLapCapture('Jolu4327')
    c.update(sample(1., 1.), observed_at=1.)
    c.update(sample(1.5, 1.), observed_at=1.5)
    c.update(sample(1.2, 1.), observed_at=1.6)
    assert c.update(sample(2., 2., 1, last_lap_seconds=1.), observed_at=2.) == []
    c.update(sample(2.5, 2., 1), observed_at=2.5)
    result = c.update(sample(3., 3., 2, last_lap_seconds=1.), observed_at=3.)
    assert len(result) == 1


def test_gap_unavailable_or_identity_change_breaks_observation():
    for mutation in ({'status': 'unavailable'}, {'car_ordinal': 1156},
                     {'blueprint_guid': str(uuid4())}, {'participant_count': 2},
                     {'identity_verified': False}, {'car_class': 'S2', 'performance_index': 900}):
        c = CleanLapCapture('Jolu4327')
        c.update(sample(1., 1.), observed_at=1.)
        c.update(sample(1.5, 1., **mutation), observed_at=1.5)
        assert c.update(sample(2., 2., 1, last_lap_seconds=1.), observed_at=2.) == []
    c = CleanLapCapture('Jolu4327')
    c.update(sample(1., 1.), observed_at=1.)
    assert c.update(sample(3., 3., 1, last_lap_seconds=2.), observed_at=3.) == []


def test_finish_time_and_boundary_must_agree_and_duplicates_are_ignored():
    c = CleanLapCapture('Jolu4327')
    c.update(sample(1., 1.), observed_at=1.)
    c.update(sample(1.5, 1.), observed_at=1.5)
    assert c.update(sample(2., 2., 1, last_lap_seconds=7.), observed_at=2.) == []
    c = CleanLapCapture('Jolu4327')
    assert len(complete(c)) == 1
    assert c.update(sample(2., 2., 1, last_lap_seconds=1.), observed_at=2.1) == []


def test_store_keeps_best_by_car_class_and_preserves_improvements(tmp_path):
    store = CreatorStore(tmp_path/'creator.sqlite3')
    a = complete(CleanLapCapture('Jolu4327'))[0]
    assert store.save_best(a)
    assert not store.save_best(a)
    slower = complete(CleanLapCapture('Jolu4327'),duration=1.2)[0]
    assert not store.save_best(slower)
    faster = complete(CleanLapCapture('Jolu4327'),duration=.9)[0]
    assert store.save_best(faster)
    other = complete(CleanLapCapture('Jolu4327'),ordinal=1156)[0]
    assert store.save_best(other)
    assert sorted(x['time_ms'] for x in store.bests('Jolu4327')) == [900, 1000]
    assert len(store.history()) == 3
    assert len(store.pending('Jolu4327')) == 2
    store.close()


def test_store_rejects_attempt_fields_that_disagree_with_evidence(tmp_path):
    import pytest
    store=CreatorStore(tmp_path/'evidence.sqlite3')
    a=complete(CleanLapCapture('Jolu4327'))[0]
    with pytest.raises(ValueError):store.save_best({**a,'time_ms':999})
    with pytest.raises(ValueError):store.save_best({**a,'car_ordinal':1156})
    assert store.history()==[]
    store.close()


def test_queue_payload_requires_catalog_mapping_and_matches_existing_api(tmp_path):
    store = CreatorStore(tmp_path/'creator.sqlite3')
    a = complete(CleanLapCapture('Jolu4327'))[0]
    store.save_best(a)
    assert store.prepare(a['attempt_id'], {}, '0.4.12') is None
    track_id = str(uuid4())
    payload = store.prepare(a['attempt_id'], {GUID: {'track_id': track_id, 'blueprint_code': '477632104'}}, '0.4.12', car_name='Test car')
    assert set(payload) == {'attempt_id','track_id','car','car_class','performance_index','time_ms','recorded_at','evidence_sha256','tracker_version'}
    assert payload['track_id'] == track_id and payload['time_ms'] == 1000
    assert store.prepare(a['attempt_id'], {GUID: {'track_id': str(uuid4()), 'blueprint_code': '477632104'}}, '0.4.13', car_name='Changed') == payload
    store.close()


def test_real_controlled_memory_replay_saves_only_second_and_fourth(tmp_path):
    fixture=json.loads((Path(__file__).parent/'fixtures/creator_sugawara_memory.json').read_text())
    capture=CleanLapCapture('Jolu4327');store=CreatorStore(tmp_path/'replay.sqlite3');accepted=[]
    for at,clock,start,lap,last,flags in fixture['samples']:
        for attempt in capture.update(sample(clock,start,lap,last_lap_seconds=last,flags=flags),observed_at=at):
            accepted.append(attempt['time_ms']);store.save_best(attempt)
    assert accepted == [70520,70034]
    assert [a['time_ms'] for a in store.bests('Jolu4327')] == [70034]
    assert len(store.history())==2
    store.close()




def test_real_sprint_finish_matches_owner_but_missing_reads_prevent_admission():
    fixture=json.loads((Path(__file__).parent/'fixtures/creator_matsuri_netsu_memory.json').read_text())
    capture=CleanLapCapture('Jolu4327');attempts=[];missing=0;finish=None
    for sample_row in fixture['samples']:
        reading={**fixture['static_reading'],**sample_row['reading'], 'profile_id':'Jolu4327'}
        if reading['status']=='unavailable':missing+=1
        attempts.extend(capture.update(reading,observed_at=sample_row['at']))
        if reading['status']=='available' and reading['completed_laps']==1:
            finish=reading
    assert missing==3
    assert attempts==[]
    assert finish is not None
    assert finish['race_clock']==finish['last_lap_seconds']==finish['lap_start_clock']
    assert round(finish['last_lap_seconds']*1000)==fixture['owner_confirmed_time_ms']==120641


def test_real_restart_discards_aborted_attempt_and_saves_full_clean_sprint():
    fixture=json.loads((Path(__file__).parent/'fixtures/creator_matsuri_restart_memory.json').read_text())
    capture=CleanLapCapture('Jolu4327');attempts=[]
    for sample_row in fixture['samples']:
        reading={**fixture['static_reading'],**sample_row['reading'],'profile_id':'Jolu4327'}
        attempts.extend(capture.update(reading,observed_at=sample_row['at']))
    assert [a['time_ms'] for a in attempts]==[117062]
    assert attempts[0]['car_ordinal']==3655
    assert attempts[0]['evidence']['clock_reset']['observed_seconds']>=1.




def test_clock_reset_needs_sustained_exact_zero_and_clear_native_flags():
    for changes in ({'race_clock':.01}, {'flags':'10000000000000'}):
        capture=CleanLapCapture('Jolu4327')
        capture.update(sample(1.,1.),observed_at=1.)
        capture.update(sample(1.5,1.),observed_at=1.5)
        for at in (2.,2.5,3.,3.5):
            assert capture.update(sample(0.,0.,**changes),observed_at=at)==[]
        capture.update(sample(.5,0.),observed_at=4.)
        assert capture.update(sample(1.,1.,1,last_lap_seconds=1.),observed_at=4.5)==[]


def test_short_zero_glitch_or_late_rewind_flag_never_clears_invalidation():
    for long_baseline in (False,True):
        capture=CleanLapCapture('Jolu4327')
        capture.update(sample(1.,1.),observed_at=1.)
        capture.update(sample(1.5,1.),observed_at=1.5)
        times=(2.,2.5,3.,3.5) if long_baseline else (2.,)
        for at in times:capture.update(sample(0.,0.),observed_at=at)
        at=times[-1]+.5
        capture.update(sample(.5,0.,flags='10000000000000' if long_baseline else '00000000000000'),observed_at=at)
        assert capture.update(sample(1.,1.,1,last_lap_seconds=1.),observed_at=at+.5)==[]
