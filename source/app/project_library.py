"""Versioned configuration library. No method performs protocol I/O."""
import copy,hashlib,json,re,sqlite3,time,uuid
from pathlib import Path
from model import default_project,validate_project
from recovery import protected
from scenarios import validate_scenario

FORMAT='protocol-workbench-project'
MAX_BYTES=20*1024*1024


def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def fingerprint(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def title(value):
    if not isinstance(value,str) or not value.strip() or len(value)>160:raise ValueError('Name must contain 1–160 characters')
    return value.strip()
def key(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{32}',value):raise ValueError('Invalid library ID')
    return value


def content(value):
    if not isinstance(value,dict) or set(value)-{'project','scenarios'}:raise ValueError('Project content requires project and optional scenarios')
    value=protected(copy.deepcopy(value));p=value.get('project');validate_project(p)
    p.setdefault('settings',{})['autostart']=False
    rows=value.setdefault('scenarios',[])
    if not isinstance(rows,list) or len(rows)>100:raise ValueError('Maximum 100 saved test definitions per project')
    ids=set()
    for s in rows:
        validate_scenario(s)
        if s['id'] in ids:raise ValueError('Duplicate saved test ID')
        ids.add(s['id']);s['project'].setdefault('settings',{})['autostart']=False
    if len(encoded(value).encode())>MAX_BYTES:raise ValueError('Project configuration exceeds 20 MiB')
    return value


def portable(value):
    if isinstance(value,dict):return {k:portable(v) for k,v in protected(value).items() if k not in ('certificate','private_key','server_certificate')}
    if isinstance(value,list):return [portable(v) for v in value]
    return value


def package(value):
    data={'format':FORMAT,'version':1,'content':portable(content(value)),'credentials_omitted':True}
    data['sha256']=fingerprint(data);return data


def migrate(value):
    if not isinstance(value,dict):raise ValueError('Import must be a JSON object')
    if value.get('format')==FORMAT:
        if value.get('version')!=1 or set(value)!={'format','version','content','credentials_omitted','sha256'}:raise ValueError('Unsupported project package version or fields')
        data={k:v for k,v in value.items() if k!='sha256'}
        if value['sha256']!=fingerprint(data):raise ValueError('Backup integrity check failed; no configuration imported')
        return content(value['content']),'Verified version-1 project package; credentials omitted'
    if 'format' in value:raise ValueError('Unknown backup format')
    if value.get('version')==1 and 'connections' in value and 'jobs' in value:
        return content({'project':value,'scenarios':[]}),'Migrated legacy workspace schema 1 to library package 1; autostart disabled'
    raise ValueError('Unsupported configuration schema; automatic guessing/migration is not allowed')


DEFAULTS={'simulation_interval':2,'tag_prefix':'Point_','tag_count':100,'timeout':10,'retry_seconds':5}
PREFERENCES={'landing_page':'dashboard','density':'comfortable'}

def defaults(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULTS):raise ValueError('Unknown default setting')
    v={**DEFAULTS,**value}
    for k,lo,hi in [('simulation_interval',.1,60),('timeout',.2,60),('retry_seconds',.5,60)]:
        if isinstance(v[k],bool) or not isinstance(v[k],(int,float)) or not lo<=v[k]<=hi:raise ValueError(f'{k} outside {lo}–{hi}')
    if type(v['tag_count']) is not int or not 1<=v['tag_count']<=20000:raise ValueError('Tag count must be 1–20,000')
    if not isinstance(v['tag_prefix'],str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,39}',v['tag_prefix']):raise ValueError('Tag prefix must be a simple 1–40 character identifier')
    return v

def preferences(value):
    if not isinstance(value,dict) or set(value)-set(PREFERENCES):raise ValueError('Unknown preference')
    v={**PREFERENCES,**value}
    if v['landing_page'] not in ('dashboard','projects','connections','tags','jobs','scenarios','inspector','reports'):raise ValueError('Unsupported landing page')
    if v['density'] not in ('comfortable','compact'):raise ValueError('Unsupported density')
    return v


class ProjectLibrary:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            version=db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,1):raise ValueError('Project library schema is newer than this app; use the matching app or a backup')
            db.executescript('CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT,description TEXT,revision INTEGER,created REAL,updated REAL,last_opened REAL,deleted INTEGER); CREATE TABLE IF NOT EXISTS revisions(project TEXT,revision INTEGER,body TEXT,digest TEXT,created REAL,reason TEXT,PRIMARY KEY(project,revision)); CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY,name TEXT,body TEXT,digest TEXT); CREATE TABLE IF NOT EXISTS settings(id TEXT PRIMARY KEY,body TEXT); PRAGMA user_version=1;')
        self.path.chmod(0o600)
    def db(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;return db
    def budget(self,db):
        size=db.execute('SELECT coalesce(sum(length(CAST(body AS BLOB))),0) FROM revisions').fetchone()[0]+db.execute('SELECT coalesce(sum(length(CAST(body AS BLOB))),0) FROM profiles').fetchone()[0]
        if size>200*1024*1024:raise ValueError('Project library exceeds 200 MiB logical configuration budget')
    def listing(self,deleted=False):
        with self.db() as db:return [dict(r) for r in db.execute('SELECT * FROM projects WHERE deleted=? ORDER BY last_opened DESC,updated DESC',(int(deleted),))]
    def metadata(self,db,ident):
        row=db.execute('SELECT * FROM projects WHERE id=?',(key(ident),)).fetchone()
        if not row:raise ValueError('Project not found')
        return dict(row)
    def checked(self,row):
        value=json.loads(row['body'])
        if fingerprint(value)!=row['digest']:raise ValueError('Stored configuration integrity check failed; restore a verified backup')
        return content(value)
    def get(self,ident,revision=None):
        with self.db() as db:
            meta=self.metadata(db,ident);rev=meta['revision'] if revision is None else revision
            if type(rev) is not int or rev<1:raise ValueError('Invalid revision')
            row=db.execute('SELECT * FROM revisions WHERE project=? AND revision=?',(ident,rev)).fetchone()
            if not row:raise ValueError('Revision not found')
            return {**meta,'selected_revision':rev,'content':self.checked(row),'sha256':row['digest']}
    def create(self,name,value,description=''):
        name=title(name)
        if not isinstance(description,str) or len(description)>2000:raise ValueError('Description must contain at most 2,000 characters')
        value=content(value);value['project']['name']=name;raw=encoded(value);ident=uuid.uuid4().hex;now=time.time()
        with self.db() as db:
            if db.execute('SELECT count(*) FROM projects').fetchone()[0]>=100:raise ValueError('Maximum 100 projects including archive')
            db.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)',(ident,name,description,1,now,now,0,0));db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?)',(ident,1,raw,fingerprint(value),now,'Created'));self.budget(db)
        return self.get(ident)
    def update(self,ident,value,expected,reason='Updated configuration',description=None):
        value=content(value);name=title(value['project']['name'])
        if not isinstance(reason,str) or not 1<=len(reason)<=200:raise ValueError('Revision reason must contain 1–200 characters')
        if description is not None and (not isinstance(description,str) or len(description)>2000):raise ValueError('Description must contain at most 2,000 characters')
        with self.db() as db:
            meta=self.metadata(db,ident)
            if meta['deleted']:raise ValueError('Restore the archived project before editing')
            if type(expected) is not int or expected!=meta['revision']:raise ValueError('Project revision changed; refresh and review before saving')
            if meta['revision']>=50:raise ValueError('Maximum 50 revisions; duplicate the project to continue with a new history')
            self.get(ident) # Verify the head before extending its history.
            rev=meta['revision']+1;now=time.time();db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?)',(ident,rev,encoded(value),fingerprint(value),now,reason));db.execute('UPDATE projects SET name=?,description=?,revision=?,updated=? WHERE id=?',(name,meta['description'] if description is None else description,rev,now,ident));self.budget(db)
        return self.get(ident)
    def history(self,ident):
        with self.db() as db:
            self.metadata(db,ident)
            return [dict(r) for r in db.execute('SELECT revision,digest,created,reason FROM revisions WHERE project=? ORDER BY revision DESC',(ident,))]
    def archive(self,ident,expected,name,deleted):
        with self.db() as db:
            meta=self.metadata(db,ident)
            if type(expected) is not int or expected!=meta['revision'] or name!=meta['name']:raise ValueError('Confirm the exact project name and current revision')
            db.execute('UPDATE projects SET deleted=?,updated=? WHERE id=?',(int(deleted),time.time(),ident))
    def touch(self,ident):
        with self.db() as db:self.metadata(db,ident);db.execute('UPDATE projects SET last_opened=? WHERE id=?',(time.time(),ident))
    def profiles(self):
        with self.db() as db:
            rows=[]
            for r in db.execute('SELECT * FROM profiles ORDER BY name'):
                value=json.loads(r['body'])
                if fingerprint(value)!=r['digest']:raise ValueError('Connection profile integrity check failed')
                rows.append({'id':r['id'],'name':r['name'],'connection':value})
            return rows
    def save_profile(self,name,connection):
        name=title(name);c=protected(copy.deepcopy(connection));p=default_project();p['connections']=[c];validate_project(p)
        if len(encoded(c).encode())>MAX_BYTES:raise ValueError('Profile exceeds 20 MiB')
        ident=uuid.uuid4().hex
        with self.db() as db:
            if db.execute('SELECT count(*) FROM profiles').fetchone()[0]>=100:raise ValueError('Maximum 100 connection profiles')
            db.execute('INSERT INTO profiles VALUES(?,?,?,?)',(ident,name,encoded(c),fingerprint(c)));self.budget(db)
        return ident
    def setting(self,name,fallback):
        with self.db() as db:row=db.execute('SELECT body FROM settings WHERE id=?',(name,)).fetchone()
        return json.loads(row[0]) if row else fallback.copy()
    def save_setting(self,name,value):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(name,encoded(value)))
