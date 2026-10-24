"""Immutable engineering snapshots of recorded results, never invented measurements."""
import copy,csv,hashlib,io,json,sqlite3,time,uuid
from pathlib import Path
from collections import Counter
from event_log import scrub,cell
KINDS=('test','simulation','communication','performance','data_verification','recovery','error','bridge')
RESULTS=('PASS','FAIL','INCONCLUSIVE','NOT TESTED')

def outcome(state):
    if state in ('PASSED','PASS','SUCCEEDED'):return 'PASS'
    if state in ('FAILED','FAIL','ERROR','TRANSFORM_ERROR','RETRY_EXHAUSTED'):return 'FAIL'
    if state in ('SKIPPED','NOT_ATTEMPTED','NOT_TESTED'):return 'NOT TESTED'
    return 'INCONCLUSIVE'

def verdict(states):
    if not states:return 'NOT TESTED'
    if 'FAIL' in states:return 'FAIL'
    if 'INCONCLUSIVE' in states:return 'INCONCLUSIVE'
    if all(x=='NOT TESTED' for x in states):return 'NOT TESTED'
    if 'NOT TESTED' in states:return 'INCONCLUSIVE'
    return 'PASS'

def template(value):
    if not isinstance(value,dict) or set(value)-{'id','name','type','title','description'}:raise ValueError('Invalid report template fields')
    if value.get('type') not in KINDS:raise ValueError('Unknown report type')
    for key,maximum in [('name',100),('title',160),('description',1000)]:
        if not isinstance(value.get(key,''),str) or len(value.get(key,''))>maximum or (key in ('name','title') and not value.get(key,'').strip()):raise ValueError('Invalid report template '+key)
    return {k:value.get(k,'') for k in ('name','type','title','description')}

def flatten(value,path=''):
    if isinstance(value,dict) and value:
        for key,v in value.items():yield from flatten(v,path+'/'+str(key).replace('~','~0').replace('/','~1'))
    elif isinstance(value,list) and value:
        for n,v in enumerate(value):yield from flatten(v,path+'/'+str(n))
    else:yield path,value

def csv_export(doc):
    f=io.StringIO();w=csv.writer(f);w.writerow(['report_id','type','overall_result','path','value'])
    for path,value in flatten(doc):w.writerow([doc['id'],doc['type'],doc['summary']['status'],cell(path),cell(value)])
    return f.getvalue()

class ReportStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.pending={}
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY,body TEXT)');db.execute('CREATE TABLE IF NOT EXISTS report_templates (id TEXT PRIMARY KEY,body TEXT)')
        if __import__('os').name!='nt':self.path.chmod(0o600)
    def db(self):return sqlite3.connect(self.path)
    def templates(self):
        builtins=[{'id':'builtin-'+k,'name':k.replace('_',' ').title(),'type':k,'title':k.replace('_',' ').title()+' report','description':'Recorded application evidence; review scope and limitations.'} for k in KINDS]
        with self.db() as db:return builtins+[json.loads(r[0]) for r in db.execute('SELECT body FROM report_templates')]
    def save_template(self,value):
        row=template(value);key=value.get('id') or uuid.uuid4().hex
        if not __import__('re').fullmatch('[0-9a-f]{32}',key):raise ValueError('Use a custom report template ID')
        row['id']=key
        with self.db() as db:
            if db.execute('SELECT count(*) FROM report_templates').fetchone()[0]>=100 and not db.execute('SELECT 1 FROM report_templates WHERE id=?',(key,)).fetchone():raise ValueError('100 custom report templates supported')
            db.execute('INSERT OR REPLACE INTO report_templates VALUES (?,?)',(key,json.dumps(row)))
        return row
    def preview(self,kind,records,source,chosen,secrets=()):
        chosen=template(chosen)
        if chosen['type']!=kind:raise ValueError('Template type does not match report')
        now=time.time();self.pending={k:v for k,v in self.pending.items() if now-v['created']<600}
        while len(self.pending)>=10:self.pending.pop(next(iter(self.pending)))
        doc=scrub({'format':'protocol-workbench-engineering-report','version':1,'id':uuid.uuid4().hex,'created':now,'type':kind,'title':chosen['title'],'description':chosen['description'],'template':chosen,'summary':{'status':verdict([r['result'] for r in records]),'records':len(records),'counts':dict(Counter(r['result'] for r in records))},'source':source,'scope':'Application-observed results only. Service acknowledgement is not independent readback or physical delivery. No production qualification is implied.','records':copy.deepcopy(records)},secrets)
        raw=json.dumps(doc,sort_keys=True,allow_nan=False,ensure_ascii=False).encode()
        if len(raw)>5000000:raise ValueError('Report exceeds 5 MB; reduce the source record limit or filter')
        doc['evidence_sha256']=hashlib.sha256(raw).hexdigest();self.pending[doc['id']]=copy.deepcopy(doc);return doc
    def save(self,key):
        row=self.pending.get(key)
        if row is None or time.time()-row['created']>=600:raise ValueError('Preview expired or unavailable; preview actual evidence again')
        raw=json.dumps(row,ensure_ascii=False,allow_nan=False)
        with self.db() as db:
            if db.execute('SELECT 1 FROM reports WHERE id=?',(key,)).fetchone():return self.get(key)
            count,size=db.execute('SELECT count(*),coalesce(sum(length(CAST(body AS BLOB))),0) FROM reports').fetchone()
            if count>=200 or size+len(raw.encode())>100000000:raise ValueError('Report history full (200 reports / 100 MB); export/archive the local data before adding more')
            db.execute('INSERT INTO reports VALUES (?,?)',(key,raw))
        return copy.deepcopy(row)
    def get(self,key):
        with self.db() as db:r=db.execute('SELECT body FROM reports WHERE id=?',(key,)).fetchone()
        if not r:raise ValueError('Saved report not found')
        return json.loads(r[0])
    def history(self):
        with self.db() as db:rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM reports')]
        return [{k:v for k,v in r.items() if k!='records'} for r in sorted(rows,key=lambda r:r['created'],reverse=True)]

def collect(app,kind,selection):
    from transaction_api import KEY as TX
    from verification_api import KEY as VER
    from health_api import KEY as HEALTH
    from performance_api import KEY as PERF
    from bridge_api import KEY as BRIDGE
    if kind not in KINDS:raise ValueError('Unknown report type')
    if not isinstance(selection,dict) or set(selection)-{'id','job','limit'}:raise ValueError('Invalid report source selection')
    limit=selection.get('limit',100)
    if type(limit) is not int or not 1<=limit<=1000:raise ValueError('Source limit must be 1..1000 records')
    if selection.get('job') and kind not in ('simulation','data_verification','bridge'):raise ValueError('Job filter applies only to simulation, data verification and bridge reports')
    rows=[];source={'selection':copy.deepcopy(selection),'captured':time.time(),'process_local':kind not in ('test','error'),'record_limit':limit,'retention_note':'Only retained evidence is available. Reports never fetch devices or invent missing history.'}
    if kind=='test':
        where=' WHERE id=?' if selection.get('id') else '';args=(selection['id'],) if selection.get('id') else ();size=0
        with app['catalogue'].db() as db:
            total=db.execute('SELECT count(*) FROM runs'+where,args).fetchone()[0];source['available_records']=total
            raw=[]
            for body, in db.execute("SELECT json_remove(body,'$.snapshot') FROM runs"+where+" ORDER BY json_extract(body,'$.started') DESC LIMIT ?",(*args,limit+1)):
                size+=len(body.encode())
                if size>5000000:raise ValueError('Selected test evidence exceeds 5 MB; select one smaller run or use existing run evidence export')
                raw.append(json.loads(body))
        for r in raw:rows.append({'source_id':r['id'],'result':outcome(r['state']),'evidence':r})
    elif kind=='simulation':
        raw=list(reversed(app['engine'].run_history));source['available_records']=len(raw)
        for n,r in enumerate(raw):rows.append({'source_id':r['job']+':'+str(r.get('finished',n)),'job':r['job'],'result':'FAIL' if r['state']=='ERROR' else 'INCONCLUSIVE' if r.get('cycles',0)>0 else 'NOT TESTED','evidence':r,'note':'Lifecycle/cycle evidence does not establish output correctness.'})
    elif kind=='communication':
        store=app[TX];source['retention']=store.summary()
        for r in reversed(store.rows):rows.append({'source_id':r['id'],'result':outcome('FAILED' if r['counts'].get('FAILED') else r['state']),'evidence':r})
    elif kind=='data_verification':
        store=app[VER];source['retention']=store.summary()
        for r in reversed(store.rows):rows.append({'source_id':r['id'],'job':r['job'],'result':outcome(r['outcome']),'evidence':r})
    elif kind=='bridge':
        store=app[BRIDGE];source['retention']=store.snapshot()
        for r in reversed(store.rows):
            counts=r['counts'];state='FAIL' if any(counts.get(k) for k in ('FAILED','TRANSFORM_ERROR')) else 'INCONCLUSIVE' if any(counts.get(k) for k in ('UNKNOWN','NOT_ATTEMPTED','READY')) else 'INCONCLUSIVE' if counts.get('SUCCEEDED') and counts.get('FILTERED') else 'PASS' if counts.get('SUCCEEDED') else 'NOT TESTED'
            rows.append({'source_id':r['id'],'job':r['job'],'result':state,'evidence':r,'note':'FILTERED is not a new delivery; acknowledgements do not prove physical delivery.'})
    elif kind=='performance':
        snap=app[PERF].snapshot();source['selection_scope']='Recorded current snapshot/history; no new threshold verdict is invented.'
        rows=[{'source_id':'performance-snapshot','result':'INCONCLUSIVE' if snap.get('history') else 'NOT TESTED','evidence':snap,'note':'Recorded measurements only; not a performance qualification.'}]
    elif kind=='recovery':
        snap=app[HEALTH].snapshot();source['retention']=snap['limits']
        for n,r in enumerate(snap['events']):rows.append({'source_id':r['id'],'result':'INCONCLUSIVE','evidence':r,'note':'A recovery event alone does not prove restored process correctness.'})
        for r in snap['recovery']:rows.append({'source_id':r['id'],'result':'FAIL' if r.get('state')=='RETRY_EXHAUSTED' else 'INCONCLUSIVE','evidence':r})
    else:
        store=app['event_log'];raw=store.search(level='ERROR',limit=1000)['rows']+store.search(level='CRITICAL',limit=1000)['rows'];raw.sort(key=lambda r:r['time'],reverse=True)
        rows=[{'source_id':r['id'],'result':'FAIL','evidence':r,'note':'An application error was recorded; this is not a universal device verdict.'} for r in raw];source['retention']=store.config
    if selection.get('id'):rows=[r for r in rows if r['source_id']==selection['id']]
    if selection.get('job'):rows=[r for r in rows if r.get('job')==selection['job']]
    source['matching_records']=source.get('available_records',len(rows)) if kind=='test' else len(rows);source['included_records']=min(len(rows),limit);source['truncated']=source['matching_records']>limit
    return copy.deepcopy(rows[:limit]),source
