"""Durable improvements and outbox; no online ingestion credentials."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from uuid import UUID

from .creator_capture import evidence_bytes


class CreatorStore:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        if self.db.execute('PRAGMA journal_mode').fetchone()[0]!='wal':
            self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS creator_improvements_v2 (
                attempt_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,
                blueprint_guid TEXT NOT NULL, car_ordinal INTEGER NOT NULL,
                car_class TEXT NOT NULL, time_ms INTEGER NOT NULL CHECK(time_ms>0),
                attempt_json TEXT NOT NULL, evidence BLOB NOT NULL,
                payload_json TEXT, receipt_json TEXT
            );
            CREATE INDEX IF NOT EXISTS creator_best_lookup_v2 ON creator_improvements_v2
                (profile_id,blueprint_guid,car_ordinal,car_class,time_ms);
            CREATE TABLE IF NOT EXISTS creator_account_bindings (
                profile_id TEXT PRIMARY KEY COLLATE NOCASE, account_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS creator_delivery_blocks (
                attempt_id TEXT PRIMARY KEY, reason TEXT NOT NULL
            );
        ''')

        # Preserve the original table and its evidence permanently. V2 allows a
        # native observation at the same time as an older mixed-source result.
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='creator_improvements'").fetchone():
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO creator_improvements_v2 SELECT * FROM creator_improvements ORDER BY rowid')

    def close(self):
        self.db.close()

    def save_best(self, attempt):
        blob = evidence_bytes(attempt['evidence'])
        if hashlib.sha256(blob).hexdigest() != attempt['evidence_sha256']:
            raise ValueError('evidence hash mismatch')
        identity=attempt['evidence'].get('identity',[])
        expected=[attempt[k] for k in ('profile_id','blueprint_guid','blueprint_code','car_ordinal','car_class','performance_index')]
        measured=attempt['evidence'].get('measured_seconds',0)
        if (identity[:6]!=expected or not attempt['evidence'].get('clean_observed')
                or type(measured) not in (int,float) or not math.isfinite(measured)
                or int(math.floor(measured*1000+.5))!=attempt['time_ms']):
            raise ValueError('attempt disagrees with preserved memory evidence')
        key = tuple(attempt[k] for k in ('profile_id','blueprint_guid','car_ordinal','car_class'))
        data = {k:v for k,v in attempt.items() if k != 'evidence'}
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            native=self._native_evidence(data,attempt['evidence'],blob)
            candidates=self.db.execute('SELECT attempt_id,time_ms FROM creator_improvements_v2 WHERE profile_id=? AND blueprint_guid=? AND car_ordinal=? AND car_class=?',key).fetchall()
            if any(r['time_ms']<=attempt['time_ms'] and self.native_evidence(r['attempt_id'])==native for r in candidates):
                return False
            self.db.execute('INSERT INTO creator_improvements_v2(attempt_id,profile_id,blueprint_guid,car_ordinal,car_class,time_ms,attempt_json,evidence) VALUES(?,?,?,?,?,?,?,?)',
                            (attempt['attempt_id'], *key, attempt['time_ms'], json.dumps(data), blob))
        return True

    def bests(self, profile_id):
        rows=self.db.execute('SELECT attempt_json FROM creator_improvements_v2 WHERE profile_id=? ORDER BY rowid',(profile_id,))
        groups={}
        for row in rows:
            a=json.loads(row[0]);key=tuple(a[k] for k in ('blueprint_guid','car_ordinal','car_class'))
            native=self.native_evidence(a['attempt_id'])
            previous=groups.get(key)
            if previous is None or (native,-a['time_ms'])>(previous[0],-previous[1]['time_ms']):groups[key]=(native,a)
        return [groups[key][1] for key in sorted(groups)]

    def history(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT attempt_json FROM creator_improvements_v2 ORDER BY rowid')]

    def pending(self, profile_id):
        rows=self.db.execute('SELECT attempt_id,attempt_json,payload_json,receipt_json FROM creator_improvements_v2 WHERE profile_id=?',(profile_id,)).fetchall()
        sent={r['attempt_id'] for r in rows if r['receipt_json'] is not None}
        attempts={a['attempt_id']:a for a in self.bests(profile_id) if a['attempt_id'] not in sent}
        # A frozen request may already exist on the server after a timeout.
        # Retain it until its original identifier has received a receipt.
        for row in rows:
            if row['payload_json'] is not None and row['receipt_json'] is None:
                attempts[row['attempt_id']]=json.loads(row['attempt_json'])
        return list(attempts.values())

    def bind_account(self, profile_id, account_id):
        account_id=str(UUID(account_id))
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO creator_account_bindings(profile_id,account_id) VALUES(?,?)',
                            (profile_id,account_id))
        row=self.db.execute('SELECT account_id FROM creator_account_bindings WHERE profile_id=?', (profile_id,)).fetchone()
        return row[0]==account_id

    def delivery_block(self, attempt_id, reason=None):
        if reason is not None:
            with self.db:self.db.execute('INSERT OR IGNORE INTO creator_delivery_blocks VALUES(?,?)',(attempt_id,reason))
        return self.db.execute('SELECT reason FROM creator_delivery_blocks WHERE attempt_id=?',(attempt_id,)).fetchone() is not None

    def native_evidence(self, attempt_id):
        row=self.db.execute('SELECT attempt_json,evidence FROM creator_improvements_v2 WHERE attempt_id=?', (attempt_id,)).fetchone()
        if row is None:return False
        a=json.loads(row[0]);e=json.loads(row[1])
        return self._native_evidence(a,e,row[1])

    def observed_gamertag(self,attempt_id):
        row=self.db.execute('SELECT evidence FROM creator_improvements_v2 WHERE attempt_id=?',(attempt_id,)).fetchone()
        if row is None:return None
        evidence=json.loads(row[0]);name=evidence.get('gamertag');identity=evidence.get('identity',[])
        if (isinstance(name,str) and name.strip() and identity
                and name.strip().casefold()==str(identity[0]).strip().casefold()):return name.strip()
        return None

    @staticmethod
    def _native_evidence(a,e,blob):
        identity=e.get('identity',[])
        car=e.get('car_metadata_evidence');context=e.get('active_blueprint_context')
        return bool(hashlib.sha256(blob).hexdigest()==a['evidence_sha256']
                    and len(identity)==9 and e.get('source')=='process_memory'
                    and e.get('metadata_source')=='process_memory' and e.get('clean_observed') is True
                    and a.get('identity_source')=='memory_active_career_blueprint'
                    and isinstance(context,dict) and context.get('blueprint_guid')==a['blueprint_guid']
                    and isinstance(car,dict) and car.get('source')=='process_memory'
                    and all(car.get(key)==identity[n] for key,n in (('process_id',6),('record_id',7),('car_id',8))))

    def prepare(self, attempt_id, catalog, tracker_version, *, car_name=None):
        row = self.db.execute('SELECT * FROM creator_improvements_v2 WHERE attempt_id=?', (attempt_id,)).fetchone()
        if row is None:
            return None
        if row['payload_json']:
            return json.loads(row['payload_json'])
        a = json.loads(row['attempt_json'])
        mapping = catalog.get(a['blueprint_guid'])
        if (not isinstance(mapping,dict) or not car_name
                or (a['blueprint_code'] is not None and mapping.get('blueprint_code') != a['blueprint_code'])):
            return None
        code=mapping.get('blueprint_code')
        if not isinstance(code,str) or len(code)!=9 or not code.isascii() or not code.isdigit():return None
        track_id = str(UUID(mapping['track_id']))
        if hashlib.sha256(row['evidence']).hexdigest() != a['evidence_sha256']:
            raise ValueError('stored evidence hash mismatch')
        payload = {k:a[k] for k in ('attempt_id','car_class','performance_index','time_ms','recorded_at','evidence_sha256')}
        payload.update(track_id=track_id, car=car_name, tracker_version=tracker_version)
        with self.db:
            self.db.execute('UPDATE creator_improvements_v2 SET payload_json=? WHERE attempt_id=? AND payload_json IS NULL',
                            (json.dumps(payload), attempt_id))
        return json.loads(self.db.execute('SELECT payload_json FROM creator_improvements_v2 WHERE attempt_id=?', (attempt_id,)).fetchone()[0])

    def acknowledge(self, attempt_id, receipt):
        if not receipt.get('id') or receipt.get('status') not in ('PENDING','APPROVED','REJECTED'):
            raise ValueError('invalid community API receipt')
        UUID(str(receipt['id']))
        with self.db:
            self.db.execute('UPDATE creator_improvements_v2 SET receipt_json=? WHERE attempt_id=? AND payload_json IS NOT NULL AND receipt_json IS NULL',
                            (json.dumps(receipt), attempt_id))
