import copy
from uuid import uuid4

from test_creator_sync import native_attempt
from test_creator_capture import GUID
from forza_tracker.creator_store import CreatorStore


def test_memory_gamertag_sync_never_logs_in_and_retries_same_attempt(tmp_path):
    from forza_tracker.creator_game_sync import GameCommunitySync
    class Client:
        def __init__(self):self.calls=[];self.fail=True;self.track=str(uuid4())
        def catalog(self):return {'tracks':[dict(id=self.track,blueprint_guid=GUID,blueprint_code='477632104',version=1)]}
        def submit_game(self,payload):
            self.calls.append(copy.deepcopy(payload))
            if self.fail:raise OSError('lost response')
            return {'id':str(uuid4()),'status':'PENDING'}
    store=CreatorStore(tmp_path/'local.sqlite3');client=Client()
    try:
        a=native_attempt();store.save_best(a)
        sync=GameCommunitySync(store,client,'test')
        assert sync.sync('Jolu4327',{})['status']=='retry_pending'
        assert client.calls[0]['gamertag']=='Jolu4327'
        client.fail=False
        assert sync.sync('Jolu4327',{})['sent']==1
        assert client.calls[0]==client.calls[1]
        assert store.pending('Jolu4327')==[]
        assert store.db.execute('SELECT COUNT(*) FROM creator_account_bindings').fetchone()[0]==0
    finally:store.close()
