"""Private per-app structured events. Logging faults never abort protocol workers."""
import csv,io,json,logging,math,re,sqlite3,time,uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
LEVELS={'DEBUG':10,'INFO':20,'WARNING':30,'ERROR':40,'CRITICAL':50}
CATEGORIES=('application','communication','protocol','simulation','verification','security','recovery','bridge')
DEFAULTS={'level':'INFO','max_rows':10000,'retention_days':30,'max_bytes':1000000,'backup_count':3}
CONTEXT=ContextVar('structured_events',default=None)
SEMANTIC_KEYS={'type','category','level','format','state','status','outcome','result','operation'}
SEMANTIC_VALUES=set('DEBUG INFO WARNING ERROR CRITICAL PASS FAIL INCONCLUSIVE NOT_TESTED PASSED FAILED CANCELLED SKIPPED SUCCEEDED UNKNOWN NOT_ATTEMPTED PARTIAL RESPONSE_ERROR RUNNING STOPPED CONNECTING RECONNECTING PAUSED WAITING_RECOVERY RETRY_EXHAUSTED Good read write test simulation communication performance data_verification recovery error bridge application protocol verification security Boolean SByte Byte Int16 UInt16 Int32 UInt32 Int64 UInt64 Float Double String DateTime'.split())|{'NOT TESTED','protocol-workbench-engineering-report'}
SECRET_KEYS={'password','token','authorization','private_key','certificate','server_certificate','username','secret','snapshot','project'}

def settings(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULTS):raise ValueError('Invalid logging settings')
    v={**DEFAULTS,**value}
    if v['level'] not in LEVELS:raise ValueError('Unknown logging level')
    for k,lo,hi in [('max_rows',100,50000),('retention_days',1,365),('max_bytes',64000,10000000),('backup_count',1,10)]:
        if type(v[k]) is not int or not lo<=v[k]<=hi:raise ValueError(f'{k} must be {lo}..{hi}')
    return v

def scrub(value,secrets=(),depth=0):
    if depth>30:return '[depth limit]'
    if isinstance(value,dict):return {str(k):'[REDACTED]' if str(k).lower() in SECRET_KEYS else v if str(k) in SEMANTIC_KEYS and isinstance(v,str) and v in SEMANTIC_VALUES else scrub(v,secrets,depth+1) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [scrub(x,secrets,depth+1) for x in value]
    if isinstance(value,str):
        for secret in sorted(secrets,key=len,reverse=True):
            if len(secret)>=4:value=value.replace(secret,'[REDACTED]')
        value=re.sub(r'(?i)(password|token|authorization|private_key|secret)(\s*[=:]\s*)([^\s,;]+)',r'\1\2[REDACTED]',value)
        return re.sub(r'(?i)([a-z][a-z0-9+.-]{0,30}://)[^/@\s]+@',r'\1[REDACTED]@',value) if '://' in value else value
    if isinstance(value,float) and not math.isfinite(value):return str(value)
    if value is None or isinstance(value,(bool,int,float)):return value
    return str(value)

def cell(v):
    if type(v) in (int,float):return v
    text=json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else str(v) if v is not None else ''
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text

def emit(category_name,level,event,**details):
    store=CONTEXT.get()
    if store:store.emit(category_name,level,event,**details)

class FileHandler(RotatingFileHandler):
    def _open(self):
        stream=super()._open()
        if __import__('os').name!='nt':__import__('os').chmod(self.baseFilename,0o600)
        return stream
    def handleError(self,record):raise OSError('Structured log file write failed')

class EventLog:
    def __init__(self,folder):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True);self.path=self.folder/'events.sqlite3';self.secrets=set();self.failures=0;self.last_error='';self.handler=None
        with self.db() as db:db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,time REAL,level TEXT,category TEXT,body TEXT)');db.execute('CREATE INDEX IF NOT EXISTS event_time ON events(time)')
        if __import__('os').name!='nt':self.folder.chmod(0o700);self.path.chmod(0o600)
        p=self.folder/'settings.json';self.config=settings(json.loads(p.read_text()) if p.exists() else {})
        self.open_handler();self.prune()
    def db(self):return sqlite3.connect(self.path)
    def learn(self,project):
        for c in project.get('connections',[]):
            for k in SECRET_KEYS-{'snapshot','project','username'}:
                if isinstance(c.get(k),str) and c[k]:self.secrets.add(c[k])
    def open_handler(self):
        if self.handler:self.handler.close()
        self.handler=FileHandler(self.folder/'events.jsonl',maxBytes=self.config['max_bytes'],backupCount=self.config['backup_count'],encoding='utf-8');self.handler.setFormatter(logging.Formatter('%(message)s'))
        if __import__('os').name!='nt':(self.folder/'events.jsonl').chmod(0o600)
    def configure(self,value):
        v=settings(value);p=self.folder/'settings.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(v));tmp.replace(p);self.config=v;self.open_handler();self.prune();return v
    def prune(self):
        with self.db() as db:
            db.execute('DELETE FROM events WHERE time < ?',(time.time()-86400*self.config['retention_days'],))
            db.execute('DELETE FROM events WHERE seq NOT IN (SELECT seq FROM events ORDER BY seq DESC LIMIT ?)',(self.config['max_rows'],))
        for p in self.folder.glob('events.jsonl.*'):
            if p.suffix[1:].isdigit() and (int(p.suffix[1:])>self.config['backup_count'] or p.stat().st_mtime<time.time()-86400*self.config['retention_days']):p.unlink()
    def emit(self,category_name,level,event,**details):
        try:
            level=level if level in LEVELS else 'INFO'
            if LEVELS[level]<LEVELS[self.config['level']] and category_name!='security' and event!='logging_policy_changed':return
            row=scrub({'id':uuid.uuid4().hex,'time':time.time(),'level':level,'category':category_name if category_name in CATEGORIES else 'application','event':str(event)[:200],'details':details},self.secrets)
            raw=json.dumps(row,ensure_ascii=False,allow_nan=False)
            if len(raw.encode())>32000:row['details']={'truncated':True,'reason':'Event exceeded 32 KB; inspect original evidence store'};raw=json.dumps(row)
            with self.db() as db:db.execute('INSERT INTO events(time,level,category,body) VALUES(?,?,?,?)',(row['time'],level,row['category'],raw))
            self.handler.emit(logging.LogRecord('structured',LEVELS[level],'',0,raw,(),None));self.prune()
        except Exception as error:self.failures+=1;self.last_error=scrub(f'{type(error).__name__}: {error}',self.secrets)[:500]
    def search(self,query='',level='',category='',since=None,until=None,page=0,limit=100):
        if level and level not in LEVELS:raise ValueError('Unknown log level')
        if category and category not in CATEGORIES:raise ValueError('Unknown log category')
        if type(page) is not int or not 0<=page<=100000 or type(limit) is not int or not 1<=limit<=50000:raise ValueError('Invalid log page/limit')
        if len(query)>200:raise ValueError('Search is limited to 200 characters')
        where=[];args=[]
        for key,value in [('level',level),('category',category)]:
            if value:where.append(key+'=?');args.append(value)
        for key,value in [('>=',since),('<=',until)]:
            if value is not None:
                value=float(value)
                if not math.isfinite(value) or value<0:raise ValueError('Invalid log time filter')
                where.append('time '+key+' ?');args.append(value)
        if since is not None and until is not None and float(since)>float(until):raise ValueError('Start exceeds end time')
        if query:where.append('instr(lower(body),lower(?))>0');args.append(query)
        clause=' WHERE '+' AND '.join(where) if where else ''
        self.prune()
        with self.db() as db:
            db.execute('BEGIN')
            total=db.execute('SELECT count(*) FROM events'+clause,args).fetchone()[0];rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM events'+clause+' ORDER BY seq DESC LIMIT ? OFFSET ?',(*args,limit,page*limit))]
        return {'rows':rows,'total':total,'page':page,'limit':limit,'settings':self.config,'health':{'write_failures':self.failures,'last_error':self.last_error},'scope':'Retained application-observed events; level and retention policies can omit older/detail events. Not a wire packet capture.'}
    def export(self,fmt,**filters):
        rows=self.search(limit=50000,**filters);all_rows=rows['rows']
        rows['total']=len(all_rows)
        if fmt=='json':return json.dumps({**rows,'rows':all_rows},ensure_ascii=False,allow_nan=False)
        if fmt!='csv':raise ValueError('Log format must be json/csv')
        f=io.StringIO();w=csv.writer(f);w.writerow(['id','time','level','category','event','details'])
        for r in all_rows:w.writerow([cell(r[k]) for k in ('id','time','level','category','event','details')])
        return f.getvalue()
    def close(self):
        if self.handler:self.handler.close()
