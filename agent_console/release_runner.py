"""Pinned-source release worker; external truth comes from a read-only probe."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from .config import Settings
from .database import utc_now
from .manager import SessionManager
from .workflow_release import ReleaseService, regular
from .workflow_runner import PATH_SETTINGS
from .workflow_store import bounded_text, canonical


def run(manifest_path):
    manifest_path=Path(manifest_path);manifest=json.loads(regular(manifest_path));settings=manifest['settings']
    for key in PATH_SETTINGS:
        if settings.get(key) is not None:settings[key]=Path(settings[key])
    settings['shared_skills']=tuple(settings['shared_skills'])
    service=ReleaseService(SessionManager(Settings(**settings)));identity=manifest['attempt_id'];folder=manifest_path.parent
    view=manifest['view'];adapter=manifest['adapter'];claimed=False
    def finish(state,summary,reference=''):
        with service.store.connect(write=True) as db:
            db.execute('UPDATE release_attempts SET state=?,summary=?,external_reference=?,updated_at=? WHERE id=?',(state,summary,reference,utc_now(),identity))
            service.store.event(db,'release.observed',service.attempt(identity)['grant_id'],{'attempt_id':identity,'state':state,'candidate_sha':view['candidate_sha']},'release-adapter')
    def stop(*_):
        finish('unknown','Release worker interrupted; check the external outcome before retrying')
        signal.signal(signal.SIGTERM,signal.SIG_DFL)
        os.killpg(os.getpgrp(),signal.SIGTERM)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGHUP,stop)
    try:
        # No adapter can run until the spawning process persists this identity.
        for _ in range(100):
            attempt=service.attempt(identity)
            if attempt['state']!='queued':return
            if attempt['pid']==os.getpid():break
            time.sleep(.05)
        else:return
        with service.store.connect(write=True) as db:
            if db.execute("UPDATE release_attempts SET state='running',updated_at=? WHERE id=? AND state='queued' AND pid=?",(utc_now(),identity,os.getpid())).rowcount!=1:return
        claimed=True
        current=service.target(view['target'],view['action'])
        if current['fingerprint']!=adapter['fingerprint']:raise ValueError('adapter changed')
        if attempt['mode']=='apply':
            preview=service.preview(view['candidate_result_id'],[e['id'] for e in view['evidence']],view['action'],view['target'])
            if preview['hash']!=view['hash']:
                finish('not-applied','Candidate or check evidence changed before execution; review a new release');return
        data,_=service.store.artifact(view['candidate_result_id'],view['artifact_index'])
        if hashlib.sha256(data).hexdigest()!=view['artifact_hash']:raise ValueError('snapshot changed')
        snapshot=folder/'candidate.tar.gz';snapshot.write_bytes(data);snapshot.chmod(0o600)
        if any(key not in os.environ for key in adapter['environment']):raise ValueError('adapter credential reference unavailable')
        def invoke(mode):
            response=folder/(mode+'-response.json')
            request=folder/(mode+'-request.json')
            request.write_text(canonical({'version':1,'mode':mode,'operation_id':attempt['grant_id'],
                'attempt_id':identity,'action':view['action'],'target':view['target'],
                'candidate_sha':view['candidate_sha'],'artifact_sha256':view['artifact_hash'],
                'snapshot_path':str(snapshot),'response_path':str(response)}));request.chmod(0o600)
            # The reviewed executable is checked immediately before each call.
            if service.target(view['target'],view['action'])['fingerprint']!=adapter['fingerprint']:raise ValueError('adapter changed')
            try:
                result=subprocess.run(adapter[mode]+[str(request)],cwd=folder,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=adapter['timeout_seconds'])
            except subprocess.TimeoutExpired:
                finish('unknown','Release adapter timed out; check the external outcome before retrying')
                signal.signal(signal.SIGTERM,signal.SIG_DFL);os.killpg(os.getpgrp(),signal.SIGTERM)
                raise
            return result.returncode,response
        if attempt['mode']=='apply':invoke('apply')
        code,response=invoke('probe')
        if code:raise ValueError('outcome probe failed')
        observed=json.loads(regular(response,16384))
        if not isinstance(observed,dict) or set(observed)!={'outcome','candidate_sha','summary','external_reference'}:raise ValueError('invalid outcome probe')
        if observed['outcome'] not in {'applied','not-applied','unknown'} or observed['candidate_sha']!=view['candidate_sha']:raise ValueError('probe did not identify the candidate')
        bounded_text(observed['summary'],'observation summary',4000,True);bounded_text(observed['external_reference'],'external reference',2000)
        finish(observed['outcome'],observed['summary'],observed['external_reference'])
    except Exception:
        if claimed:finish('unknown','External outcome could not be verified; check the target before retrying')
