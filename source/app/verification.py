"""Datatype-aware, evidence-based verification; no protocol I/O or write retries."""
import math
import time
import uuid
from decimal import Decimal
from collections import Counter,deque
from contextvars import ContextVar
from model import cast,view_value

MODES=('off','basic','sampled','full','selected','continuous')


def settings(job,tags,protocol='opcua'):
    explicit=job.get('verification')
    legacy=explicit is None
    c={'mode':'basic' if job.get('verify',True) and job.get('kind')!='scan' else 'off',
       'comparison':'exact','absolute_tolerance':0.,'relative_tolerance':0.,'sample_count':10,
       'selected':[],'interval_seconds':0.,'minimum':None,'maximum':None,
       'max_age_seconds':None,'unchanged_seconds':None,'sequence_step':None,
       'check_quality':not legacy,'require_timestamp':False,'check_timestamp_order':False,
       'check_future_timestamp':False,'future_tolerance_seconds':5.,'check_duplicates':False,
       'max_failed_points':0,'on_failure':'stop','legacy':legacy}
    if explicit is not None:
        if not isinstance(explicit,dict):raise ValueError('Verification settings must be an object')
        if set(explicit)-set(c) or 'legacy' in explicit:raise ValueError('Unknown verification setting')
        c.update(explicit)
    if c['mode'] not in MODES:raise ValueError('Invalid verification mode')
    if job.get('kind')=='server' and explicit and c['mode']!='off':raise ValueError('Server jobs expose data; configure verification on a client job')
    if c['comparison'] not in ('exact','tolerance') or c['on_failure'] not in ('stop','continue'):raise ValueError('Invalid verification comparison or failure action')
    for key in ('check_quality','require_timestamp','check_timestamp_order','check_future_timestamp','check_duplicates'):
        if not isinstance(c[key],bool):raise ValueError('Verification flags must be Boolean')
    for key,lo,hi in [('absolute_tolerance',0,1e308),('relative_tolerance',0,1),('interval_seconds',0,86400),('future_tolerance_seconds',0,86400),('max_age_seconds',.001,86400),('unchanged_seconds',.001,86400),('sequence_step',-1e308,1e308),('minimum',-1e308,1e308),('maximum',-1e308,1e308)]:
        if c[key] is None and key in ('max_age_seconds','unchanged_seconds','sequence_step','minimum','maximum'):continue
        if isinstance(c[key],bool):raise ValueError('Verification numerical settings cannot be Boolean')
        try:v=float(c[key])
        except (TypeError,ValueError,OverflowError):raise ValueError('Invalid verification numerical setting: '+key)
        if not math.isfinite(v) or not lo<=v<=hi:raise ValueError('Verification setting outside limits: '+key)
        raw=Decimal(str(c[key]))
        c[key]=int(raw) if key in ('minimum','maximum','sequence_step') and raw==raw.to_integral_value() else v
    for key,lo,hi in [('sample_count',1,20000),('max_failed_points',0,20000)]:
        if type(c[key]) is not int or not lo<=c[key]<=hi:raise ValueError('Invalid verification count: '+key)
    if c['minimum'] is not None and c['maximum'] is not None and c['minimum']>c['maximum']:raise ValueError('Verification minimum exceeds maximum')
    if c['sequence_step'] is not None and any(t['dtype'] not in ('Float','Double','Boolean') for t in tags) and not float(c['sequence_step']).is_integer():raise ValueError('Integer sequence steps must be whole numbers')
    if any(t['dtype'] in ('String','DateTime') for t in tags) and any(c[k] is not None for k in ('minimum','maximum','sequence_step')):raise ValueError('Numeric verification range/sequence rules do not apply to String/DateTime')
    ids={t['id'] for t in tags}
    if not isinstance(c['selected'],list) or any(not isinstance(i,str) for i in c['selected']) or len(set(c['selected']))!=len(c['selected']) or not set(c['selected'])<=ids:raise ValueError('Verification selection must contain unique job tag IDs')
    if c['mode']=='selected' and not c['selected']:raise ValueError('Select verification points')
    if protocol!='opcua' and (c['require_timestamp'] or c['check_timestamp_order'] or c['check_future_timestamp'] or c['max_age_seconds'] is not None):raise ValueError('Source timestamp verification requires OPC UA; Modbus has no source timestamps')
    return c


def selected(tags,c):
    if c['mode']=='off':return []
    if c['mode']=='basic':return tags[:1]+tags[-1:] if len(tags)>1 else tags
    if c['mode']=='selected':
        wanted=set(c['selected']);return [t for t in tags if t['id'] in wanted]
    if c['mode']=='sampled':
        n=min(len(tags),c['sample_count'])
        return [tags[(i*(len(tags)-1))//(n-1)] for i in range(n)] if n>1 else tags[:1]
    return tags


def safe(value,kind):
    if value is None:return None
    if kind=='String' and isinstance(value,str):return value
    if kind=='DateTime':
        try:return view_value(value,kind)
        except ValueError:return str(value)[:250]
    if isinstance(value,(bool,int,float)):
        if isinstance(value,float) and not math.isfinite(value):return str(value)
        return view_value(value,kind) if isinstance(value,(int,float)) and not isinstance(value,bool) else value
    return str(value)[:250]


def valid_type(value,kind):
    if kind=='String':return isinstance(value,str) and len(value)<=4096
    if kind=='DateTime':
        from datetime import datetime
        try:return isinstance(value,datetime) and cast(value,kind)==value
        except ValueError:return False
    if kind=='Boolean':return type(value) is bool
    if kind in ('Float','Double'):
        if type(value) not in (int,float) or type(value) is float and not math.isfinite(value):return False
    elif type(value) is not int:return False
    try:return math.isfinite(cast(value,kind)) if kind in ('Float','Double') else cast(value,kind)==value
    except (ValueError,TypeError,OverflowError):return False


def equal(actual,expected,kind,c):
    if kind not in ('Float','Double') or c['comparison']=='exact':return actual==expected
    return math.isclose(actual,expected,abs_tol=c['absolute_tolerance'],rel_tol=c['relative_tolerance'])


class VerificationStore:
    def __init__(self,max_records=200,max_points=50000):
        self.rows=deque();self.max_records=max_records;self.max_points=max_points;self.points=0;self.evicted=0;self.totals=Counter();self.categories=Counter()
    def add(self,row):
        from event_log import emit
        emit('verification','DEBUG' if row['outcome']=='PASS' else 'WARNING','data_verification',verification=row['id'],job=row['job'],outcome=row['outcome'],counts=row['counts'])
        self.rows.append(row);self.points+=len(row['points']);self.totals['checks']+=1
        self.totals.update(row['counts'])
        for p in row['points']:self.categories.update(p['issues'])
        while len(self.rows)>self.max_records or self.points>self.max_points:
            self.points-=len(self.rows.popleft()['points']);self.evicted+=1
    def summary(self):
        return {**{k:self.totals[k] for k in ('checks','total','verified','PASS','FAIL','UNVERIFIED')},'categories':dict(self.categories),'retained_checks':len(self.rows),'evicted_checks':self.evicted,'max_records':self.max_records,'max_points':self.max_points}
    def filtered(self,job='',outcome='',query=''):
        if outcome and outcome not in ('PASS','FAIL','INCONCLUSIVE'):raise ValueError('Invalid verification result filter')
        q=query.casefold()[:200]
        return [r for r in reversed(self.rows) if (not job or r['job']==job) and (not outcome or r['outcome']==outcome) and (not q or q in r['name'].casefold() or any(q in p['tag'].casefold() or q in ' '.join(p['issues']).casefold() for p in r['points']))]
    def find(self,ident):
        row=next((r for r in self.rows if r['id']==ident),None)
        if row is None:raise ValueError('Verification result not found or evicted')
        return row


DEFAULT_STORE=VerificationStore()
STORE_CONTEXT=ContextVar('verification_store',default=DEFAULT_STORE)


class Session:
    def __init__(self,job,tags,protocol):
        self.job=job;self.tags=tags;self.config=settings(job,tags,protocol);self.previous={};self.last_check=None;self.store=STORE_CONTEXT.get()
    def due(self):
        return self.config['mode']!='off' and (self.last_check is None or self.config['mode']=='continuous' or time.monotonic()-self.last_check>=self.config['interval_seconds'])
    def evaluate(self,chosen,actual,expected=None,observations=None,read_error=None,transaction=None,now=None):
        c=self.config;now=time.time() if now is None else now;observations=observations or {};chosen_ids={t['id'] for t in chosen};rows=[];seen={}
        for t in self.tags:
            key=t['id'];kind=t['dtype'];p={'id':key,'tag':t['tag'],'dtype':kind,'expected':safe(expected.get(key),kind) if expected and key in expected else None,'actual':None,'source_timestamp':None,'quality':'Unavailable','issues':[],'outcome':'UNVERIFIED'}
            rows.append(p)
            if key not in chosen_ids:p['issues']=['NOT_SELECTED'];continue
            ob=observations.get(key,{})
            present=key in actual or 'value' in ob;value=actual.get(key,ob.get('value'));p['actual']=safe(value,kind);p['quality']=ob.get('quality','Unavailable');p['source_timestamp']=ob.get('source_timestamp')
            issues=p['issues'];unknown=False
            if ob.get('good') is False:issues.append('BAD_QUALITY')
            if c['check_quality']:
                if ob.get('good') is False:issues.append('BAD_QUALITY')
                elif ob.get('good') is not True:issues.append('QUALITY_UNAVAILABLE');unknown=True
            if not present:
                issues.append('READ_ERROR' if read_error else 'MISSING_VALUE');unknown=True
            elif value is None:issues.append('NULL_VALUE')
            elif not valid_type(value,kind):issues.append('INVALID_DATATYPE_OR_VALUE')
            if ob.get('datatype') is not None and ob['datatype']!=kind:issues.append('DATATYPE_MISMATCH')
            value_ok=present and value is not None and valid_type(value,kind) and ob.get('good') is not False and 'DATATYPE_MISMATCH' not in issues
            stamp=ob.get('source_timestamp');previous=self.previous.get(key)
            if stamp is not None and (type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp<=0):issues.append('INVALID_TIMESTAMP');p['source_timestamp']=str(stamp);stamp=None
            if c['require_timestamp'] and stamp is None:issues.append('MISSING_TIMESTAMP');unknown=True
            if c['max_age_seconds'] is not None:
                if stamp is None:issues.append('MISSING_TIMESTAMP');unknown=True
                elif now-stamp>c['max_age_seconds']:issues.append('STALE_TIMESTAMP')
            if c['check_future_timestamp'] and stamp is not None and stamp-now>c['future_tolerance_seconds']:issues.append('FUTURE_TIMESTAMP')
            if c['check_timestamp_order'] and previous and stamp is not None and previous['timestamp'] is not None and stamp<previous['timestamp']:issues.append('OUT_OF_ORDER_TIMESTAMP')
            if value_ok:
                if expected is not None:
                    if key not in expected:issues.append('MISSING_EXPECTED');unknown=True
                    elif not valid_type(expected[key],kind):issues.append('INVALID_EXPECTED')
                    elif not equal(value,expected[key],kind,c):issues.append('VALUE_MISMATCH')
                if kind not in ('Boolean','String','DateTime'):
                    if c['minimum'] is not None and value<c['minimum']:issues.append('BELOW_RANGE')
                    if c['maximum'] is not None and value>c['maximum']:issues.append('ABOVE_RANGE')
                    if c['sequence_step'] is not None and previous and not equal(value,previous['value']+c['sequence_step'],kind,c):issues.append('OUT_OF_SEQUENCE_VALUE')
                changed_at=now if previous is None or previous['value']!=value else previous['changed_at']
                if c['unchanged_seconds'] is not None and now-changed_at>=c['unchanged_seconds']:issues.append('UNCHANGED_VALUE')
                if c['check_duplicates']:
                    same=(kind,value)
                    if same in seen:
                        issues.append('DUPLICATE_VALUE');other=seen[same]
                        if 'DUPLICATE_VALUE' not in other['issues']:other['issues'].append('DUPLICATE_VALUE');other['outcome']='FAIL'
                    else:seen[same]=p
                self.previous[key]={'value':value,'timestamp':stamp,'changed_at':changed_at}
            issues[:]=list(dict.fromkeys(issues))
            definite=[i for i in issues if i not in ('QUALITY_UNAVAILABLE','MISSING_TIMESTAMP','MISSING_EXPECTED','MISSING_VALUE','READ_ERROR')]
            p['outcome']='FAIL' if definite else 'UNVERIFIED' if unknown else 'PASS'
        counts=Counter(p['outcome'] for p in rows);counts={k:counts[k] for k in ('PASS','FAIL','UNVERIFIED')};counts.update(total=len(rows),verified=counts['PASS']+counts['FAIL'])
        chosen_rows=[p for p in rows if p['id'] in chosen_ids]
        inconclusive=any(p['outcome']=='UNVERIFIED' for p in chosen_rows)
        outcome='FAIL' if counts['FAIL'] else 'INCONCLUSIVE' if inconclusive else 'PASS'
        row={'id':uuid.uuid4().hex,'job':self.job['id'],'name':self.job.get('name',self.job['id']),'time':now,'mode':c['mode'],'comparison':c['comparison'],'settings':{k:str(v) if k in ('minimum','maximum','sequence_step') and type(v) is int and abs(v)>2**53 else v for k,v in c.items()},'transaction':transaction,'outcome':outcome,'counts':counts,'points':rows,'error':type(read_error).__name__ if read_error else '', 'threshold_exceeded':counts['FAIL']>c['max_failed_points'],'inconclusive':inconclusive}
        self.store.add(row);self.last_check=time.monotonic();return row
