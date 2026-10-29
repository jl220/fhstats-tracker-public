"""Background EventLab capture and local community outbox coordination."""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
from pathlib import Path

from .creator_capture import CLASS_BOUNDS, CleanLapCapture
from .creator_memory import CreatorMemoryReader
from .creator_store import CreatorStore
from .creator_game_sync import GameCommunitySync
from .api.creator_events import GameCommunityClient
from .version import APP_VERSION

log=logging.getLogger(__name__)


def enrich_memory(reading, profile_id):
    """Require native car metadata tied to the observed race record."""
    if reading.get('status')!='available':return reading
    if str(reading.get('gamertag','')).strip().casefold()!=profile_id.strip().casefold():
        return {**reading,'status':'profile_mismatch'}
    try:
        low,high=CLASS_BOUNDS[reading['car_class']]
        evidence=reading['car_metadata_evidence']
        valid=(reading.get('metadata_source')=='process_memory' and isinstance(evidence,dict)
               and evidence.get('source')=='process_memory'
               and all(reading.get(k) is not None and evidence.get(k)==reading[k]
                       for k in ('process_id','record_id','car_id'))
               and type(reading['car_ordinal']) is int and reading['car_ordinal']>0
               and type(reading['performance_index']) is int
               and low<=reading['performance_index']<=high)
        if not valid:raise ValueError('native car metadata does not match the race record')
        return {**reading,'profile_id':profile_id}
    except (KeyError,TypeError,ValueError):
        return {**reading,'status':'car_metadata_unavailable'}


class CreatorEventsRuntime:
    def __init__(self,data_root,profile_id,*,reader=None,community_client=None,upload_enabled=False):
        self.root=Path(data_root)/'data'/'creator-events'
        self.profile_id=profile_id
        self.reader=reader
        self.stop_event=threading.Event()
        self.store_ready=threading.Event()
        self.lock=threading.Lock()
        self.thread=None
        self.sync_thread=None
        self._community_client=community_client
        self.upload_enabled=bool(upload_enabled)
        self.state=dict(status='waiting',event=None,current={},bests=[],pending=0,prepared=0,error='',sync_status='waiting')

    def start(self):
        if self.thread is None:
            self.thread=threading.Thread(target=self._run,name='creator-events-memory',daemon=True)
            self.thread.start()
            self.sync_thread=threading.Thread(target=self._sync_run,name='creator-events-sync',daemon=True)
            self.sync_thread.start()

    def stop(self):
        self.stop_event.set()
        if self.reader is not None:self.reader.close()
        if self.thread is not None and self.thread is not threading.current_thread():self.thread.join(timeout=2)
        if self.sync_thread is not None and self.sync_thread is not threading.current_thread():self.sync_thread.join(timeout=2)

    def set_upload_enabled(self,enabled):
        with self.lock:
            self.upload_enabled=bool(enabled)
            if not enabled:self.state['sync_status']='uploads_disabled'

    def set_profile(self,profile_id):
        with self.lock:self.profile_id=profile_id

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)

    def _publish(self,**values):
        with self.lock:self.state.update(values)

    def _catalog(self):
        try:
            path=self.root/'catalog.json'
            if path.stat().st_size>1024*1024:return {}
            value=json.loads(path.read_text(encoding='utf-8'))
            bindings=value.get('memory_bindings',{}) if isinstance(value,dict) else {}
            return bindings if isinstance(bindings,dict) else {}
        except (OSError,ValueError):return {}

    def _sync_run(self):
        store=None;client=self._community_client;next_sync=0.;delay=15
        try:
            while not self.store_ready.wait(.1):
                if self.stop_event.is_set():return
            store=CreatorStore(self.root/'personal.sqlite3')
            while not self.stop_event.is_set():
                with self.lock:
                    profile=self.profile_id;enabled=self.upload_enabled
                if not enabled:
                    self._publish(sync_status='uploads_disabled')
                elif time.monotonic()>=next_sync:
                    client=client or GameCommunityClient()
                    def cancelled():
                        with self.lock:return self.stop_event.is_set() or self.profile_id!=profile or not self.upload_enabled
                    result=GameCommunitySync(store,client,APP_VERSION).sync(profile,self._catalog(),cancelled=cancelled)
                    if not cancelled():self._publish(sync_status=result['status'],sync_sent=result['sent'])
                    if result['status']=='retry_pending':
                        next_sync=time.monotonic()+max(delay,result.get('retry_after',0));delay=min(delay*2,300)
                    else:next_sync=time.monotonic()+5;delay=15
                self.stop_event.wait(.2)
        except Exception:
            self._publish(sync_status='sync_failed')
        finally:
            if client is not None and hasattr(client,'close'):client.close()
            if store is not None:store.close()

    def _run(self):
        store=None
        try:
            store=CreatorStore(self.root/'personal.sqlite3')
            self.store_ready.set()
            self.reader=self.reader or CreatorMemoryReader(blueprint_cache=self.root/'blueprints')
            profile=self.profile_id;capture=CleanLapCapture(profile)
            bests=store.bests(profile);pending=store.pending(profile)
            self._publish(bests=bests,pending=len(pending))
            next_prepare=0
            while not self.stop_event.is_set():
                with self.lock:new_profile=self.profile_id
                if new_profile!=profile:
                    profile=new_profile;capture=CleanLapCapture(profile)
                    bests=store.bests(profile);next_prepare=0
                reading=self.reader.sample()
                at=time.monotonic()
                enriched=enrich_memory(reading,profile)
                changed=False
                for attempt in capture.update(enriched,observed_at=at):
                    name=enriched.get('car_name')
                    if not name:
                        from .car_catalog.fh6 import lookup_fh6_car
                        car=lookup_fh6_car(attempt['car_ordinal']);name=car.display_name if car else None
                    if name:attempt['car_name']=name
                    changed=store.save_best(attempt) or changed
                if changed:bests=store.bests(profile);next_prepare=0
                if at>=next_prepare:
                    catalog=self._catalog();pending=store.pending(profile);prepared=0
                    for attempt in pending:
                        name=attempt.get('car_name')
                        if not name:
                            from .car_catalog.fh6 import lookup_fh6_car
                            car=lookup_fh6_car(attempt['car_ordinal']);name=car.display_name if car else None
                        try:
                            if store.prepare(attempt['attempt_id'],catalog,APP_VERSION,car_name=name):prepared+=1
                        except (ValueError,TypeError,KeyError):
                            log.warning('Creator Events catalog mapping is invalid; attempt remains local')
                    next_prepare=at+5
                event=reading.get('event')
                if reading.get('blueprint_guid'):
                    event={k:reading.get(k) for k in ('name','blueprint_guid','blueprint_code','configured_laps')}
                self._publish(status=enriched.get('status','waiting'),event=event,current=capture.current,
                              bests=bests,pending=len(pending),prepared=prepared,error='')
                self.stop_event.wait(.05)
        except Exception:
            log.exception('Creator Events capture stopped')
            self._publish(status='error',error='capture_failed')
        finally:
            if self.reader is not None:
                self.reader.close()
                if hasattr(self.reader,'release'):self.reader.release()
            if store is not None:store.close()
