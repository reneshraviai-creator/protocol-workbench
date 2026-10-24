"""Opt-in local identities. Passwords and session tokens never appear in API results."""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from aiohttp import web

KEY=web.AppKey('access_control',object)
ROLES=('Administrator','Engineer','Operator','Read-only')
COOKIE='workbench_session'


def password_hash(password,salt):
    if not isinstance(password,str) or not 12<=len(password)<=128 or not password.strip():
        raise ValueError('Use a password of 12–128 characters')
    return hashlib.scrypt(password.encode(),salt=salt,n=131072,r=8,p=1,dklen=32,maxmem=268435456).hex()


class AccessControl:
    def __init__(self,folder,audit=None,clock=time.time):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True,mode=0o700);self.folder.chmod(0o700)
        path=self.folder/'users.sqlite3';self.db=sqlite3.connect(path);path.chmod(0o600)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('CREATE TABLE IF NOT EXISTS users (name TEXT PRIMARY KEY, role TEXT NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL, active INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL);')
        row=self.db.execute('SELECT value FROM settings WHERE id=1').fetchone()
        self.settings=json.loads(row[0]) if row else {'enabled':False,'timeout_minutes':30}
        self.sessions={};self.attempts={};self.clock=clock;self.audit=audit;self.hash_lock=asyncio.Semaphore(2)
    @property
    def enabled(self):return self.settings['enabled']
    def event(self,event,**details):
        if self.audit:self.audit.emit('security','INFO',event,**details)
    def save_settings(self):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)',(json.dumps(self.settings),));self.db.commit()
    def users(self):return [dict(r) for r in self.db.execute('SELECT name,role,active FROM users ORDER BY name')]
    async def put_user(self,name,role,password=None,active=True):
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}',name):raise ValueError('Username: 3–64 letters, digits, underscore, dot or dash')
        if role not in ROLES or not isinstance(active,bool):raise ValueError('Invalid role or active flag')
        old=self.db.execute('SELECT * FROM users WHERE name=?',(name,)).fetchone()
        if old and old['role']=='Administrator' and old['active'] and (role!='Administrator' or not active):
            if self.db.execute("SELECT count(*) FROM users WHERE role='Administrator' AND active=1").fetchone()[0]<=1:raise ValueError('Keep at least one active administrator')
        if not old and len(self.users())>=50:raise ValueError('Maximum 50 local accounts')
        if password is not None:
            salt=secrets.token_bytes(16)
            async with self.hash_lock:digest=await asyncio.to_thread(password_hash,password,salt)
            salt=salt.hex()
        elif old:salt=old['salt'];digest=old['hash']
        else:raise ValueError('A new account requires a password')
        # Re-check the last administrator after the asynchronous hash finishes.
        if old and old['role']=='Administrator' and (role!='Administrator' or not active):
            if self.db.execute("SELECT count(*) FROM users WHERE role='Administrator' AND active=1 AND name!=?",(name,)).fetchone()[0]==0:raise ValueError('Keep at least one active administrator')
        self.db.execute('INSERT OR REPLACE INTO users VALUES(?,?,?,?,?)',(name,role,salt,digest,int(active)));self.db.commit()
        self.revoke(name);self.event('account_changed',account=name,role=role,active=active)
    def revoke(self,name):
        for session in self.sessions.values():
            if session['name']==name:session['revoked']=True
    async def login(self,name,password):
        now=self.clock();self.attempts={k:v for k,v in self.attempts.items() if now-v['since']<60}
        if len(self.attempts)>=200 and name not in self.attempts:raise ValueError('Too many login attempts; wait one minute')
        item=self.attempts.setdefault(str(name)[:64],{'since':now,'count':0});item['count']+=1
        if item['count']>5:self.event('login_throttled');raise ValueError('Too many login attempts; wait one minute')
        row=self.db.execute('SELECT * FROM users WHERE name=?',(str(name),)).fetchone()
        try:
            async with self.hash_lock:digest=await asyncio.to_thread(password_hash,password,bytes.fromhex(row['salt']) if row else b'\0'*16)
            valid=row and row['active'] and hmac.compare_digest(digest,row['hash'])
        except (ValueError,TypeError):valid=False
        if not valid:self.event('login_failed');return None
        self.attempts.pop(str(name),None)
        self.sessions={k:v for k,v in self.sessions.items() if not v['revoked'] and now-v['last']<=self.settings['timeout_minutes']*60 and now-v['created']<=86400}
        if len(self.sessions)>=100:raise ValueError('Maximum 100 active sessions; sign out unused sessions')
        raw=secrets.token_urlsafe(32);key=hashlib.sha256(raw.encode()).hexdigest()
        self.sessions[key]={'name':row['name'],'role':row['role'],'created':now,'last':now,'revoked':False}
        self.event('login_succeeded',account=row['name']);return raw
    def session(self,raw,touch=True):
        if not raw:return None
        row=self.sessions.get(hashlib.sha256(raw.encode()).hexdigest())
        if not row:return None
        if row['revoked'] or self.clock()-row['last']>self.settings['timeout_minutes']*60 or self.clock()-row['created']>86400:
            row['revoked']=True;return None
        if touch:row['last']=self.clock()
        return row
    def logout(self,raw):
        row=self.session(raw,False)
        if row:row['revoked']=True;self.event('logout',account=row['name'])
    def close(self):self.db.close()


def permitted(role,path,method,body):
    if role=='Administrator':return True
    if path.startswith('/api/security/'):
        return method=='GET' and path in ('/api/security/status','/api/security/certificates') or path=='/api/security/logout' or (path=='/api/security/write-enable' and role in ('Engineer','Operator'))
    if path=='/api/licence/acknowledge' and method=='POST':return True
    if method in ('GET','HEAD'):return True
    if path=='/api/action' and body.get('action') in ('stop','stop_all','pause'):return True
    if path in ('/api/scenarios/stop','/api/rtu-demo/stop') or re.fullmatch(r'/api/monitors/[^/]+/stop',path):return True
    if path=='/api/connection' and body.get('mode','test') in ('test','browse','resolve','read'):return True
    if path=='/api/project-library/preferences' and method=='PUT':return True
    if role=='Engineer' and path in ('/api/project-library/command','/api/project-library/preview'):return True
    if role=='Read-only':return False
    if role=='Engineer':
        # Explicit set: adding a new API does not implicitly grant mutation privileges.
        return path in ENGINEER or bool(re.fullmatch(r'/api/(monitors/[^/]+/(write|record|record-stop)|health/[^/]+/resume|mixed-generation/[^/]+/(cancel|accept)|mixed-templates/[^/]+/delete|bridge-templates/[^/]+/delete)',path))
    if role=='Operator':return path in ('/api/action','/api/scenarios/run','/api/monitors','/api/watchlists/start') or bool(re.fullmatch(r'/api/monitors/[^/]+/(record|record-stop)',path))
    return False

ENGINEER=set('/api/project /api/action /api/connection /api/resolve /api/node-inspect /api/monitors /api/watchlists /api/watchlists/start /api/import-tags /api/generate-tags /api/validate-project /api/simulation-draft /api/preview-tags /api/scenarios /api/scenarios/run /api/signal-path/bulk /api/signal-path/draft /api/signal-path/demo /api/recovery/drafts /api/device-setup/inspect /api/device-setup/address /api/templates/preview /api/templates/save /api/templates/import /api/mixed-templates /api/mixed-generation /api/bridges/preview /api/bridge-templates /api/bridge-templates/resolve /api/engineering-reports/preview /api/engineering-reports/save /api/report-templates /api/diagnostics /api/rtu-demo/start /api/certificate'.split())


async def status(request):
    store=request.app[KEY];user=store.session(request.cookies.get(COOKIE),False)
    return web.json_response({'enabled':store.enabled,'timeout_minutes':store.settings['timeout_minutes'],'user':{'name':user['name'],'role':user['role']} if user else None,'roles':ROLES})

async def setup(request):
    store=request.app[KEY]
    if store.enabled:raise web.HTTPForbidden(text='Authentication already enabled')
    if request.app['engine'].active() or request.app['runner'].active() or any(m.task and not m.task.done() for m in request.app['monitors'].monitors.values()):raise ValueError('Stop jobs, runs and monitors before enabling authentication')
    body=await request.json()
    if body.get('confirm') is not True:raise ValueError('Explicitly confirm enabling local authentication')
    # Serialize bootstrap across password hashing and prevent two first administrators.
    async with request.app['security_setup_lock']:
        if store.enabled:raise ValueError('Authentication already enabled')
        await store.put_user(body.get('name'),'Administrator',body.get('password'))
        if request.app['engine'].active() or request.app['runner'].active() or any(m.task and not m.task.done() for m in request.app['monitors'].monitors.values()):raise ValueError('Work started during setup; stop it and enable authentication again')
        store.settings['enabled']=True;store.save_settings();store.event('authentication_enabled')
    return web.json_response({'ok':True,'message':'Authentication enabled. Sign in with the administrator you created.'})

async def login(request):
    store=request.app[KEY]
    if not store.enabled:raise ValueError('Enable authentication first')
    body=await request.json();raw=await store.login(body.get('name',''),body.get('password',''))
    if raw is None:return web.json_response({'error':'Invalid username or password'},status=401)
    response=web.json_response({'ok':True});response.set_cookie(COOKIE,raw,httponly=True,samesite='Strict',path='/',max_age=86400);response.headers['Cache-Control']='no-store';return response

async def logout(request):
    request.app[KEY].logout(request.cookies.get(COOKIE));response=web.json_response({'ok':True});response.del_cookie(COOKIE,path='/');return response

async def users(request):return web.json_response(request.app[KEY].users())
async def user_save(request):
    b=await request.json();await request.app[KEY].put_user(b.get('name'),b.get('role'),b.get('password'),b.get('active',True));return web.json_response({'ok':True})
async def settings(request):
    b=await request.json();value=b.get('timeout_minutes')
    if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=240:raise ValueError('Session timeout must be 1–240 minutes')
    store=request.app[KEY];store.settings['timeout_minutes']=value;store.save_settings();store.event('security_settings_changed',timeout_minutes=value);return web.json_response({'ok':True})


@web.middleware
async def access_boundary(request,handler):
    if not request.path.startswith('/api/'):return await handler(request)
    store=request.app[KEY];path=request.path
    public=('/api/bootstrap','/api/security/login','/api/security/setup')
    user=store.session(request.cookies.get(COOKIE),touch=request.method not in ('GET','HEAD')) if store.enabled else None
    request['security_user']=user
    if store.enabled and path not in public:
        if not user:return web.json_response({'error':'Sign in to continue','authentication_required':True},status=401)
        body=await request.json() if request.body_exists and request.content_type=='application/json' else {}
        if not permitted(user['role'],path,request.method,body):
            store.event('permission_denied',account=user['name'],method=request.method,path=path)
            return web.json_response({'error':'Your role does not allow this operation','permission_denied':True},status=403)
    elif not store.enabled and path.startswith('/api/security/') and path not in ('/api/security/status','/api/security/setup'):
        return web.json_response({'error':'Enable authentication first'},status=403)
    response=await handler(request)
    if path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
        if store.enabled and request.method not in ('GET','HEAD') and path not in public:
            store.event('user_activity',account=user['name'] if user else '',method=request.method,path=path,status=response.status)
    return response


def install(app,folder):
    app[KEY]=AccessControl(Path(folder)/'security',app['event_log']);app['security_setup_lock']=asyncio.Lock()
    app.add_routes([web.get('/api/security/status',status),web.post('/api/security/setup',setup),web.post('/api/security/login',login),web.post('/api/security/logout',logout),web.get('/api/security/users',users),web.post('/api/security/users',user_save),web.put('/api/security/settings',settings)])
    async def close(app):app[KEY].close()
    app.on_cleanup.append(close)
