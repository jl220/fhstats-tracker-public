import copy
from uuid import uuid4

import pytest

from forza_tracker.creator_capture import CleanLapCapture
from forza_tracker.creator_store import CreatorStore
from forza_tracker.creator_sync import CommunitySync
from test_creator_capture import GUID, sample


def native_attempt(duration=1., ordinal=1155):
    capture=CleanLapCapture('Jolu4327')
    common=dict(car_ordinal=ordinal,metadata_source='process_memory',gamertag='Jolu4327',
        identity_source='memory_active_career_blueprint',
        identity_evidence={'blueprint_guid':GUID},
        car_metadata_evidence=dict(source='process_memory',process_id=17,record_id='0x123',car_id='0x456'))
    capture.update(sample(1.,1.,**common),observed_at=1.)
    capture.update(sample(1.5,1.,**common),observed_at=1.5)
    return capture.update(sample(1.+duration,1.+duration,1,last_lap_seconds=duration,**common),observed_at=1.+duration)[0]


class Client:
    def __init__(self):
        self.account=str(uuid4());self.track=str(uuid4());self.calls=[];self.fail=False
        self.ownership=str(uuid4())
        self.eligible=True;self.gamertag='Jolu4327'
    def eligibility(self):return dict(eligible=self.eligible,account_id=self.account,gamertag=self.gamertag,ownership_id=self.ownership)
    def catalog(self):return {'tracks':[dict(id=self.track,blueprint_code='477632104',version=1)]}
    def submit(self,payload,*,account_id,ownership_id):
        assert account_id==self.account
        assert ownership_id==self.ownership
        self.calls.append(copy.deepcopy(payload))
        if self.fail:raise OSError('uncertain connection outcome')
        return {'id':str(uuid4()),'status':'PENDING'}


def setup(tmp_path):
    store=CreatorStore(tmp_path/'outbox.sqlite3');client=Client()
    catalog={GUID:dict(track_id=client.track,blueprint_code='477632104',version=1)}
    return store,client,catalog


def test_only_native_best_per_car_is_sent_and_receipted(tmp_path):
    store,client,catalog=setup(tmp_path)
    try:
        for a in (native_attempt(),native_attempt(.9),native_attempt(1.,368)):store.save_best(a)
        sync=CommunitySync(store,client,'test')
        result=sync.sync('Jolu4327',catalog)
        assert result['sent']==2 and result['status']=='synced'
        assert sorted(p['time_ms'] for p in client.calls)==[900,1000]
        assert store.pending('Jolu4327')==[] and len(store.history())==3
        sync.sync('Jolu4327',catalog)
        assert len(client.calls)==2
    finally:store.close()


def test_uncertain_send_retries_identical_payload_after_restart_and_never_changes_account(tmp_path):
    store,client,catalog=setup(tmp_path);attempt=native_attempt();store.save_best(attempt)
    client.fail=True
    assert CommunitySync(store,client,'v1').sync('Jolu4327',catalog)['status']=='retry_pending'
    original=client.calls[0];store.close()
    store=CreatorStore(tmp_path/'outbox.sqlite3')
    try:
        client.fail=False;old_account=client.account;client.account=str(uuid4())
        assert CommunitySync(store,client,'v2').sync('Jolu4327',catalog)['status']=='account_mismatch'
        assert len(client.calls)==1
        client.account=old_account
        assert CommunitySync(store,client,'v2').sync('Jolu4327',catalog)['sent']==1
        assert client.calls[-1]==original
    finally:store.close()


@pytest.mark.parametrize('reason',['unclaimed','gamertag','catalog','legacy'])
def test_missing_eligibility_mapping_or_native_proof_never_sends(tmp_path,reason):
    store,client,catalog=setup(tmp_path);a=native_attempt()
    if reason=='unclaimed':client.eligible=False
    elif reason=='gamertag':client.gamertag='OtherPlayer'
    elif reason=='catalog':client.track=str(uuid4())
    else:
        from test_creator_capture import complete
        a=complete(CleanLapCapture('Jolu4327'))[0]
    store.save_best(a)
    try:
        CommunitySync(store,client,'v1').sync('Jolu4327',catalog)
        assert client.calls==[] and len(store.pending('Jolu4327'))==1
    finally:store.close()


def test_faster_legacy_record_cannot_hide_a_slower_native_best(tmp_path):
    from test_creator_capture import complete
    store,client,catalog=setup(tmp_path)
    try:
        legacy=complete(CleanLapCapture('Jolu4327'),duration=.9)[0]
        assert store.save_best(legacy)
        assert store.save_best(native_attempt(1.))
        assert CommunitySync(store,client,'v1').sync('Jolu4327',catalog)['sent']==1
        assert client.calls[0]['time_ms']==1000 and len(store.history())==2
    finally:store.close()


def test_frozen_uncertain_attempt_remains_pending_after_faster_lap(tmp_path):
    store,client,catalog=setup(tmp_path)
    try:
        old=native_attempt();store.save_best(old);client.fail=True
        CommunitySync(store,client,'v1').sync('Jolu4327',catalog)
        first=client.calls[0];store.save_best(native_attempt(.9));client.fail=False
        assert CommunitySync(store,client,'v2').sync('Jolu4327',catalog)['sent']==2
        assert first in client.calls[1:] and store.pending('Jolu4327')==[]
    finally:store.close()


def test_twenty_legacy_candidates_do_not_starve_valid_native_lap(tmp_path):
    from test_creator_capture import complete
    store,client,catalog=setup(tmp_path)
    try:
        for ordinal in range(1,21):store.save_best(complete(CleanLapCapture('Jolu4327'),ordinal=ordinal)[0])
        store.save_best(native_attempt())
        assert CommunitySync(store,client,'v1').sync('Jolu4327',catalog)['sent']==1
    finally:store.close()


def test_migration_preserves_original_and_allows_equal_native_time(tmp_path):
    import sqlite3,json
    from forza_tracker.creator_capture import evidence_bytes
    from test_creator_capture import complete
    path=tmp_path/'legacy.sqlite3';db=sqlite3.connect(path)
    db.execute('''CREATE TABLE creator_improvements (
        attempt_id TEXT PRIMARY KEY,profile_id TEXT NOT NULL,blueprint_guid TEXT NOT NULL,
        car_ordinal INTEGER NOT NULL,car_class TEXT NOT NULL,time_ms INTEGER NOT NULL,
        attempt_json TEXT NOT NULL,evidence BLOB NOT NULL,payload_json TEXT,receipt_json TEXT,
        UNIQUE(profile_id,blueprint_guid,car_ordinal,car_class,time_ms))''')
    a=complete(CleanLapCapture('Jolu4327'))[0];data={k:v for k,v in a.items() if k!='evidence'}
    db.execute('INSERT INTO creator_improvements VALUES(?,?,?,?,?,?,?,?,NULL,NULL)',
        tuple(a[k] for k in ('attempt_id','profile_id','blueprint_guid','car_ordinal','car_class','time_ms'))
        +(json.dumps(data),evidence_bytes(a['evidence'])))
    db.commit();db.close()
    store=CreatorStore(path)
    try:
        assert store.save_best(native_attempt())
        assert len(store.history())==2
        assert store.db.execute('SELECT evidence FROM creator_improvements').fetchone()[0]==evidence_bytes(a['evidence'])
        assert store.native_evidence(store.bests('Jolu4327')[0]['attempt_id'])
    finally:store.close()


@pytest.mark.parametrize('http_status',[422,401,429])
def test_http_failures_are_isolated_or_pause_session_without_losing_attempts(tmp_path,http_status):
    import requests
    store,client,catalog=setup(tmp_path)
    store.save_best(native_attempt(ordinal=368));store.save_best(native_attempt())
    original=client.submit
    def submit(payload,**kwargs):
        if 'Acura' in payload['car']:
            response=requests.Response();response.status_code=http_status
            response.headers['Retry-After']='600'
            raise requests.HTTPError(response=response)
        return original(payload,**kwargs)
    client.submit=submit
    try:
        result=CommunitySync(store,client,'v1').sync('Jolu4327',catalog)
        if http_status==422:
            assert result['sent']==1 and result['status']=='local_pending'
            assert len(store.pending('Jolu4327'))==1
            assert CommunitySync(store,client,'v1').sync('Jolu4327',catalog)['status']=='local_pending'
        elif http_status==401:assert result['sent']==0 and result['status']=='login_required'
        else:assert result['status']=='retry_pending' and result['retry_after']==600
        assert len(store.history())==2
    finally:store.close()




def test_verified_server_guid_binding_prepares_upload_without_manual_local_catalog(tmp_path):
    store,client,_=setup(tmp_path)
    original=client.catalog
    def catalog():
        result=original();result['tracks'][0]['blueprint_guid']=GUID;return result
    client.catalog=catalog
    try:
        store.save_best(native_attempt())
        assert CommunitySync(store,client,'v1').sync('Jolu4327',{})['sent']==1
    finally:store.close()


@pytest.mark.parametrize('conflict',['local','remote'])
def test_conflicting_native_catalog_bindings_never_send(tmp_path,conflict):
    store,client,local=setup(tmp_path);original=client.catalog
    def catalog():
        result=original();result['tracks'][0]['blueprint_guid']=GUID
        if conflict=='local':result['tracks'][0]['id']=str(uuid4())
        else:result['tracks'].append({**result['tracks'][0],'id':str(uuid4())})
        return result
    client.catalog=catalog
    try:
        store.save_best(native_attempt())
        CommunitySync(store,client,'v1').sync('Jolu4327',local)
        assert client.calls==[]
    finally:store.close()
