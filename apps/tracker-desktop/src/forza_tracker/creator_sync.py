"""Account-bound delivery of native clean laps, independent of memory sampling."""
from uuid import UUID

from .car_catalog.fh6 import lookup_fh6_car


class CommunitySync:
    def __init__(self, store, client, tracker_version):
        self.store,self.client,self.version=store,client,tracker_version

    def sync(self, profile, bindings, *, cancelled=lambda:False):
        result=dict(status='synced',sent=0,blocked=0)
        try:
            owner=self.client.eligibility()
            if owner.get('eligible') is not True:return {**result,'status':'claim_required'}
            if str(owner.get('gamertag','')).strip().casefold()!=profile.strip().casefold():
                return {**result,'status':'profile_mismatch'}
            account=str(UUID(owner['account_id']))
            ownership=str(UUID(owner['ownership_id']))
            if not self.store.bind_account(profile,account):return {**result,'status':'account_mismatch'}
            tracks=self.client.catalog()['tracks']
            bindings=dict(bindings)
            remote={}
            for track in tracks:
                if track.get('blueprint_guid') is None:continue
                guid=str(UUID(track['blueprint_guid']))
                candidate=dict(track_id=str(UUID(str(track['id']))),blueprint_code=track['blueprint_code'],version=track['version'])
                if guid in remote and remote[guid]!=candidate:raise ValueError('ambiguous native track registration')
                remote[guid]=candidate
            for guid,candidate in remote.items():
                if guid in bindings and bindings[guid]!=candidate:
                    bindings[guid]=None
                else:bindings[guid]=candidate
            for attempt in self.store.pending(profile):
                if result['sent']>=20:break
                if cancelled():return {**result,'status':'paused'}
                if self.store.delivery_block(attempt['attempt_id']):
                    result['blocked']+=1;continue
                binding=bindings.get(attempt['blueprint_guid'])
                if not isinstance(binding,dict) or not self.store.native_evidence(attempt['attempt_id']):
                    result['blocked']+=1;continue
                matches=[t for t in tracks if str(t.get('id'))==binding.get('track_id')
                         and t.get('blueprint_code')==binding.get('blueprint_code')
                         and type(t.get('version')) is int and t['version']==binding.get('version')]
                if len(matches)!=1:
                    result['blocked']+=1;continue
                car=lookup_fh6_car(attempt['car_ordinal'])
                name=car.display_name if car else None
                payload=self.store.prepare(attempt['attempt_id'],bindings,self.version,car_name=name)
                if payload is None or payload['track_id']!=binding['track_id']:
                    result['blocked']+=1;continue
                if cancelled():return {**result,'status':'paused'}
                try:
                    receipt=self.client.submit(payload,account_id=account,ownership_id=ownership)
                except Exception as error:
                    response=getattr(error,'response',None)
                    if getattr(response,'status_code',None) in (400,409,422):
                        self.store.delivery_block(attempt['attempt_id'],f'http_{response.status_code}')
                        result['blocked']+=1;continue
                    raise
                self.store.acknowledge(attempt['attempt_id'],receipt)
                result['sent']+=1
            if result['blocked']:result['status']='local_pending'
            return result
        except Exception as error:
            # No credentials or HTTP bodies are copied to logs or UI state.
            response=getattr(error,'response',None)
            status=getattr(response,'status_code',None)
            if status==401:return {**result,'status':'login_required'}
            if status==403:return {**result,'status':'claim_required'}
            if status==429:
                try:seconds=max(15,min(int(response.headers.get('Retry-After','15')),3600))
                except (ValueError,TypeError):seconds=60
                return {**result,'status':'retry_pending','retry_after':seconds}
            return {**result,'status':'retry_pending'}
