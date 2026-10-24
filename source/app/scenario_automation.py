"""Declarative scenario policy and evidence checks; no script execution."""
import copy
import hashlib
import json
import math
from model import TYPES

STATES=('PASSED','FAILED','ERROR','SKIPPED')
RESPONSES=('SUCCEEDED','FAILED','UNKNOWN','NOT_ATTEMPTED','PARTIAL','RESPONSE_ERROR')

def fingerprint(s):
    body={k:v for k,v in s.items() if k not in ('id','updated','revision','fingerprint')}
    return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def validate_policy(step,index,preceding):
    if 'continue_on_failure' in step and type(step['continue_on_failure']) is not bool:raise ValueError('Continue on failure must be Boolean')
    when=step.get('when')
    if when is not None:
        if not isinstance(when,dict) or set(when)!={'step','state'} or type(when['step']) is not int or not 1<=when['step']<=index or when['state'] not in STATES:raise ValueError('Condition needs a preceding step number and result state')
    retry=step.get('retry',{})
    if not isinstance(retry,dict) or set(retry)-{'attempts','delay'}:raise ValueError('Retry needs attempts and delay')
    attempts=retry.get('attempts',1);delay=retry.get('delay',0)
    if type(attempts) is not int or not 1<=attempts<=5:raise ValueError('Retry attempts must be 1..5')
    if type(delay) not in (int,float) or not math.isfinite(delay) or not 0<=delay<=30:raise ValueError('Retry delay must be 0..30 seconds')
    if attempts>1 and step['action'] not in ('read','assert','check'):raise ValueError('Retries are supported only for reads and assertions; writes/probes/jobs never replay')
    if step['action']=='check':
        kind=step.get('check');ref=step.get('source_step')
        if kind not in ('datatype','response','status','duration'):raise ValueError('Select datatype, response, status or duration check')
        if type(ref) is not int or not 1<=ref<=index:raise ValueError('Check must refer to a preceding step')
        source=preceding[ref-1]
        if kind in ('datatype','status') and source['action']!='read':raise ValueError('Datatype/status checks require a preceding read')
        if kind=='response' and source['action'] not in ('read','write'):raise ValueError('Response checks require a preceding client read/write')
        if kind=='datatype' and step.get('expected') not in TYPES:raise ValueError('Select a supported expected datatype')
        if kind=='response' and step.get('expected') not in RESPONSES:raise ValueError('Select an expected protocol response outcome')
        if kind=='status' and (not isinstance(step.get('expected'),str) or not 1<=len(step['expected'])<=200):raise ValueError('Status requires an exact observed quality name')
        if kind=='duration':
            lo=step.get('minimum',0);hi=step.get('maximum')
            if type(lo) not in (int,float) or type(hi) not in (int,float) or not math.isfinite(lo) or not math.isfinite(hi) or not 0<=lo<=hi<=300:raise ValueError('Execution-time limits must satisfy 0 <= minimum <= maximum <= 300 seconds')

def allowed(step,results):
    when=step.get('when')
    return when is None or results[when['step']-1]['state']==when['state']

def check(step,results,result):
    source=results[step['source_step']-1];kind=step['check'];result['source_step']=step['source_step'];result['expectation']={k:v for k,v in step.items() if k in ('check','expected','minimum','maximum')}
    if kind=='duration':
        actual=source.get('duration');passed=actual is not None and step.get('minimum',0)<=actual<=step['maximum'];result['observed']=actual
    elif kind=='response':
        actual=[t['state'] for t in source.get('transactions',[])];result['observed']=actual;passed=bool(actual) and all(x==step['expected'] for x in actual)
    else:
        field='observed_dtype' if kind=='datatype' else 'quality';items=source.get('tags',[]);actual=[t.get(field) for t in items];result['observed']=actual
        passed=bool(items) and all(x is not None and x==step['expected'] for x in actual)
    if not passed:raise AssertionError(f"{kind} check on step {step['source_step']}: observed {actual}; expected {result['expectation']}")

def compare_runs(left,right):
    terminal=lambda r:r['state'] not in ('RUNNING','QUEUED','STOPPING')
    if not terminal(left) or not terminal(right):raise ValueError('Compare completed runs only')
    rows=[];a_steps={s['index']:s for s in left['steps']};b_steps={s['index']:s for s in right['steps']}
    for i in range(max(left.get('total_steps',len(left['steps'])),right.get('total_steps',len(right['steps'])))):
        a=a_steps.get(i+1,{});b=b_steps.get(i+1,{})
        fields=('action','label','state','observed','written','expectation','error','tags')
        changes={k:{'left':copy.deepcopy(a.get(k)),'right':copy.deepcopy(b.get(k))} for k in fields if a.get(k)!=b.get(k)}
        rows.append({'index':i+1,'left_state':a.get('state','NOT_STARTED'),'right_state':b.get('state','NOT_STARTED'),'duration_delta':b['duration']-a['duration'] if 'duration' in a and 'duration' in b else None,'changes':changes})
    return {'left':left['id'],'right':right['id'],'same_scenario':left.get('scenario')==right.get('scenario'),'same_definition':bool(left.get('fingerprint')) and left.get('fingerprint')==right.get('fingerprint'),'left_revision':left.get('revision'),'right_revision':right.get('revision'),'left_state':left['state'],'right_state':right['state'],'changed_steps':sum(bool(r['changes']) for r in rows),'steps':rows,'scope':'Recorded application observations; matching results do not establish physical causality. Timing includes local execution and communication.'}
