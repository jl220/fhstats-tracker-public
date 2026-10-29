"""Automatic delivery of observed game names, without account credentials."""
from uuid import UUID

from .car_catalog.fh6 import lookup_fh6_car


class GameCommunitySync:
    def __init__(self,store,client,tracker_version):
        self.store,self.client,self.version=store,client,tracker_version

    def sync(self,profile,bindings,*,cancelled=lambda:False):
        result=dict(status='synced',sent=0,blocked=0)
        try:
            pending=self.store.pending(profile)
            if not pending:return result
            tracks=self.client.catalog()['tracks']
            remote={}
            for track in tracks:
                if not track.get('blueprint_guid'):continue
                guid=str(UUID(track['blueprint_guid']))
                item=dict(track_id=str(UUID(str(track['id']))),blueprint_code=track['blueprint_code'],version=track['version'])
                if guid in remote and remote[guid]!=item:raise ValueError('ambiguous native registration')
                remote[guid]=item
            for attempt in pending:
                if result['sent']>=20:break
                if cancelled():return {**result,'status':'paused'}
                aid=attempt['attempt_id'];mapping=remote.get(attempt['blueprint_guid'])
                if (self.store.delivery_block(aid) or not mapping or not self.store.native_evidence(aid)
                        or (attempt['blueprint_guid'] in bindings and bindings[attempt['blueprint_guid']]!=mapping)):
                    result['blocked']+=1;continue
                name=self.store.observed_gamertag(aid)
                if not name or name.strip().casefold()!=profile.strip().casefold():
                    result['blocked']+=1;continue
                car=lookup_fh6_car(attempt['car_ordinal'])
                payload=self.store.prepare(aid,remote,self.version,car_name=car.display_name if car else None)
                if payload is None or payload['track_id']!=mapping['track_id']:
                    result['blocked']+=1;continue
                if cancelled():return {**result,'status':'paused'}
                try:
                    receipt=self.client.submit_game({**payload,'gamertag':name})
                except Exception as error:
                    response=getattr(error,'response',None)
                    if getattr(response,'status_code',None) in (400,409,422):
                        self.store.delivery_block(aid,f'http_{response.status_code}')
                        result['blocked']+=1;continue
                    raise
                self.store.acknowledge(aid,receipt);result['sent']+=1
            if result['blocked']:result['status']='local_pending'
            return result
        except Exception as error:
            response=getattr(error,'response',None)
            status=getattr(response,'status_code',None)
            if status==403:return {**result,'status':'claim_required'}
            if status==429:
                try:seconds=max(15,min(int(response.headers.get('Retry-After','15')),3600))
                except (ValueError,TypeError):seconds=60
                return {**result,'status':'retry_pending','retry_after':seconds}
            return {**result,'status':'retry_pending'}
