from datetime import datetime, timezone, timedelta

from forza_tracker.creator_runtime import enrich_memory
from test_creator_capture import sample


NOW=datetime.now(timezone.utc)


def packet(**changes):
    p=dict(connected=True,last_packet_at=NOW.isoformat(),car_ordinal=1155,
           car_class='S1',performance_index=800,current_race_time_seconds=1.)
    p.update(changes)
    return p


def native_reading(**changes):
    r=sample(1.,1.,gamertag='Jolu4327',metadata_source='process_memory',
             car_metadata_evidence=dict(source='process_memory',process_id=17,
                                        record_id='0x123',car_id='0x456'))
    r.update(changes)
    return r


def test_metadata_and_competitive_fields_are_kept_from_the_same_native_reading():
    r=native_reading()
    result=enrich_memory(r,'Jolu4327')
    assert result['last_lap_seconds']==0. and result['completed_laps']==0
    assert result['metadata_source']=='process_memory' and result['race_clock']==1.


def test_mixed_missing_or_wrong_car_native_metadata_is_rejected():
    for changes in (dict(metadata_source='telemetry'),dict(car_metadata_evidence=None),
                    dict(car_ordinal=0),dict(car_class='A'),dict(performance_index=True),
                    dict(car_metadata_evidence=dict(source='process_memory',process_id=17,
                                                   record_id='0x123',car_id='0x999'))):
        assert enrich_memory(native_reading(**changes),'Jolu4327')['status']=='car_metadata_unavailable'
    assert enrich_memory(native_reading(),'AnotherPlayer')['status']=='profile_mismatch'


def test_shared_udp_listener_does_not_replace_an_existing_collectors_status():
    import errno
    from forza_tracker.telemetry.runtime import _run_listener
    writes=[]
    def occupied(**kwargs):raise OSError(errno.EADDRINUSE,'already listening')
    _run_listener(listener=occupied,status_writer=lambda *a,**k:writes.append((a,k)),host='127.0.0.1',port=9999,reuse_existing=True)
    assert writes==[]


def test_colliding_receiver_never_writes_listening_before_bind(monkeypatch):
    import errno
    import forza_tracker.telemetry.listener as listener
    writes=[]
    class BusySocket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,*args):raise OSError(errno.EADDRINUSE,'occupied')
    monkeypatch.setattr(listener.socket,'socket',lambda *a,**k:BusySocket())
    monkeypatch.setattr(listener,'write_telemetry_status',lambda *a,**k:writes.append((a,k)))
    import pytest
    with pytest.raises(OSError):listener.listen_forza_horizon_6_udp(duration_seconds=1,emit_summary=False)
    assert writes==[]


def test_background_runtime_persists_best_and_stops_cleanly(tmp_path):
    import threading,time
    from forza_tracker.creator_runtime import CreatorEventsRuntime
    from forza_tracker.creator_store import CreatorStore
    class Reader:
        def __init__(self):self.lock=threading.Lock();self.value=native_reading();self.closed=False
        def sample(self):
            with self.lock:return dict(self.value)
        def set(self,clock,start,laps,last=0.):
            with self.lock:self.value={**native_reading(),'race_clock':clock,'lap_start_clock':start,
                                       'completed_laps':laps,'last_lap_seconds':last}
        def close(self):self.closed=True
    reader=Reader()
    runtime=CreatorEventsRuntime(tmp_path,'Jolu4327',reader=reader)
    def until(predicate):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.01)
        raise AssertionError(runtime.snapshot())
    try:
        runtime.start();until(lambda:runtime.snapshot()['current'].get('status')=='observing')
        reader.set(1.5,1.,0);until(lambda:runtime.snapshot()['current'].get('seconds')==.5)
        reader.set(2.,2.,1,1.);until(lambda:len(runtime.snapshot()['bests'])==1)
        assert runtime.snapshot()['bests'][0]['time_ms']==1000
        runtime.set_profile('AnotherPlayer');until(lambda:runtime.snapshot()['status']=='profile_mismatch')
        assert runtime.snapshot()['bests']==[]
    finally:runtime.stop()
    assert reader.closed and not runtime.thread.is_alive()
    store=CreatorStore(tmp_path/'data/creator-events/personal.sqlite3')
    assert [a['time_ms'] for a in store.bests('Jolu4327')]==[1000]
    store.close()


def test_upload_worker_does_not_block_memory_sampling(tmp_path):
    import json,threading,time
    from forza_tracker.creator_runtime import CreatorEventsRuntime
    from forza_tracker.creator_store import CreatorStore
    from test_creator_sync import Client,native_attempt
    from test_creator_capture import GUID
    root=tmp_path/'data/creator-events';root.mkdir(parents=True)
    store=CreatorStore(root/'personal.sqlite3');store.save_best(native_attempt());store.close()
    entered=threading.Event();release=threading.Event()
    class SlowClient(Client):
        def catalog(self):return {'tracks':[dict(id=self.track,blueprint_guid=GUID,blueprint_code='477632104',version=1)]}
        def submit_game(self,payload):
            entered.set();release.wait(3)
            return {'id':str(__import__('uuid').uuid4()),'status':'PENDING'}
    client=SlowClient()
    (root/'catalog.json').write_text(json.dumps({'memory_bindings':{GUID:dict(
        track_id=client.track,blueprint_code='477632104',version=1)}}))
    class Reader:
        count=0
        def sample(self):self.count+=1;return {'status':'waiting'}
        def close(self):pass
    reader=Reader();runtime=CreatorEventsRuntime(tmp_path,'Jolu4327',reader=reader,community_client=client,upload_enabled=True)
    try:
        runtime.start();assert entered.wait(3)
        count=reader.count;time.sleep(.2)
        assert reader.count>count
        release.set()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and runtime.snapshot().get('sync_status')!='synced':time.sleep(.02)
        assert runtime.snapshot()['sync_status']=='synced'
    finally:release.set();runtime.stop()
    store=CreatorStore(root/'personal.sqlite3')
    assert store.pending('Jolu4327')==[];store.close()
