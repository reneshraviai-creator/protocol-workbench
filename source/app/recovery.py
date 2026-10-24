"""Local recovery snapshots. Drafts never mutate the engine or execute protocol I/O."""
import copy
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid

KINDS={'workspace','device','simulation','connection','job','scenario','tag','tagtable','generator','signalpath'}
MAX_DOCUMENT=20*1024*1024

def protected(value):
    if isinstance(value,dict):
        return {k:protected(v) for k,v in value.items() if not any(word in str(k).lower().replace('-','').replace('_','') for word in ('password','passphrase','accesstoken','refreshtoken')) and str(k).lower() not in ('token','secret')}
    if isinstance(value,list):return [protected(v) for v in value]
    return value

def revision(project):return hashlib.sha256(json.dumps(project,sort_keys=True,allow_nan=False).encode()).hexdigest()

def restore_credentials(project,current):
    result=copy.deepcopy(project);by_id={c['id']:c for c in current.get('connections',[])}
    for c in result.get('connections',[]):
        old=by_id.get(c['id'],{})
        # Keep credentials only when the restored endpoint/security identity matches.
        keys=('protocol','url','host','port','device_id','security','username','transport','serial_port','baudrate','bytesize','parity','stopbits')
        if all(c.get(k)==old.get(k) for k in keys) and 'password' in old:c['password']=old['password']
    result.setdefault('settings',{})['autostart']=False
    return result

class RecoveryStore:
    def __init__(self,path):
        self.path=path
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY,kind TEXT,title TEXT,updated REAL,state TEXT,body TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS history (id TEXT PRIMARY KEY,entity TEXT,title TEXT,created REAL,digest TEXT,body TEXT)')
        if os.name!='nt':path.chmod(0o600)
    def db(self):return sqlite3.connect(self.path)
    def encoded(self,value):
        raw=json.dumps(protected(value),allow_nan=False,sort_keys=True)
        if len(raw.encode())>MAX_DOCUMENT:raise ValueError('Recovery document exceeds the 20 MB limit')
        return raw
    def draft(self,body):
        key=body.get('id') or uuid.uuid4().hex;kind=body['kind'];title=body.get('title','Untitled draft')
        if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',key):raise ValueError('Invalid draft ID')
        if kind not in KINDS:raise ValueError('Unsupported draft kind')
        if not isinstance(title,str) or not 1<=len(title)<=160:raise ValueError('Draft title must contain 1..160 characters')
        if not isinstance(body.get('data'),dict):raise ValueError('Draft data must be an object')
        raw=self.encoded(body['data']);now=time.time()
        with self.db() as db:
            existing=db.execute('SELECT kind FROM drafts WHERE id=?',(key,)).fetchone()
            if existing and existing[0]!=kind:raise ValueError('Draft kind cannot change')
            db.execute('INSERT OR REPLACE INTO drafts VALUES (?,?,?,?,?,?)',(key,kind,title,now,'ACTIVE',raw))
            self.prune(db,'drafts','updated',30,60*1024*1024)
        return {'id':key,'updated':now,'state':'ACTIVE'}
    def prune(self,db,table,clock,count,budget):
        rows=db.execute(f'SELECT id,length(CAST(body AS BLOB)) FROM {table} ORDER BY {clock} DESC').fetchall();used=0
        for n,(key,size) in enumerate(rows):
            used+=size
            if n>=count or (used>budget and n>0):db.execute(f'DELETE FROM {table} WHERE id=?',(key,))
    def drafts(self):
        with self.db() as db:
            return [dict(zip(('id','kind','title','updated','state'),r)) for r in db.execute('SELECT id,kind,title,updated,state FROM drafts ORDER BY updated DESC')]
    def get_draft(self,key):
        with self.db() as db:row=db.execute('SELECT kind,title,body FROM drafts WHERE id=?',(key,)).fetchone()
        if not row:raise ValueError('Draft not found (it may have expired under retention limits)')
        return {'id':key,'kind':row[0],'title':row[1],'data':json.loads(row[2])}
    def mark(self,key,state):
        if state not in ('DISMISSED','SAVED'):raise ValueError('Invalid draft state')
        with self.db() as db:
            if not db.execute('UPDATE drafts SET state=? WHERE id=?',(state,key)).rowcount:raise ValueError('Draft not found')
    def remember(self,entity,title,data):
        raw=self.encoded(data);digest=hashlib.sha256(raw.encode()).hexdigest()
        with self.db() as db:
            last=db.execute('SELECT digest FROM history WHERE entity=? ORDER BY created DESC LIMIT 1',(entity,)).fetchone()
            if last and last[0]==digest:return
            db.execute('INSERT INTO history VALUES (?,?,?,?,?,?)',(uuid.uuid4().hex,entity,title[:160],time.time(),digest,raw))
            old=db.execute('SELECT id FROM history WHERE entity=? ORDER BY created DESC LIMIT -1 OFFSET 10',(entity,)).fetchall()
            db.executemany('DELETE FROM history WHERE id=?',old);self.prune(db,'history','created',100,100*1024*1024)
    def history(self):
        with self.db() as db:return [dict(zip(('id','entity','title','created','digest'),r)) for r in db.execute('SELECT id,entity,title,created,digest FROM history ORDER BY created DESC')]
    def snapshot(self,key):
        with self.db() as db:row=db.execute('SELECT entity,body FROM history WHERE id=?',(key,)).fetchone()
        if not row:raise ValueError('Snapshot not found')
        return {'entity':row[0],'data':json.loads(row[1])}


def remember_safe(store,entity,title,data):
    try:store.remember(entity,title,data);return ''
    except (ValueError,OSError,sqlite3.Error) as error:return 'Saved configuration history unavailable: '+str(error)
