"""Short-lived, exact-operation write consent for authenticated local sessions."""
import asyncio
from contextvars import ContextVar
import hashlib
import json
import secrets
import time
from aiohttp import web
from access_control import KEY,COOKIE

CONTEXT=ContextVar('write_access',default=None)


def check_write(config,tags):
    scope=CONTEXT.get()
    if scope is None:
        from credential_vault import CONTEXT as VAULT
        if VAULT.get():raise ValueError('Explicit write authorization required')
        return
    scope.check()
    from resources import empty,add_writes
    p=empty();add_writes(p,config,tags)
    if len(tags)>scope.maximum or not p['writes'].issubset(scope.targets):raise ValueError('Write exceeds the confirmed targets or operation limit')


class Grant:
    def __init__(self,store,session,targets,maximum=10000):
        self.store=store;self.session=session;self.targets=targets;self.maximum=maximum;self.expires=store.clock()+300
    def check(self):
        if self.session['revoked'] or self.store.clock()-self.session['last']>self.store.settings['timeout_minutes']*60 or self.store.clock()-self.session['created']>86400 or self.store.clock()>self.expires:
            raise ValueError('Write authorization expired; stop and confirm the target again')


def operation_plan(app,path,body):
    from resources import empty,add_writes,job_plan,scenario_plan
    p=empty();project=app['engine'].project
    if path=='/api/action' and body.get('action') in ('start','start_all','resume'):
        jobs=project['jobs'] if body['action']=='start_all' else [j for j in project['jobs'] if j['id']==body.get('id')]
        for job in jobs:
            part=job_plan(project,job);p['writes'].update(part['writes'])
    elif path=='/api/scenarios/run':p=scenario_plan(app['catalogue'].get('scenarios',body['id']))
    elif path=='/api/connection' and body.get('mode')=='write':
        c=body['connection'];chosen=set(body.get('tags',[]));add_writes(p,c,[t for t in c.get('tags',[]) if not chosen or t['id'] in chosen])
    elif path.startswith('/api/monitors/') and path.endswith('/write') and body.get('mode')!='preview':
        m=app['monitors'].monitors.get(path.split('/')[3])
        if m is None:raise ValueError('Monitor not found')
        c=m.connection
        add_writes(p,c,[next(t for t in m.tags if t['id']==body['tag'])])
    elif path=='/api/diagnostics':
        p['writes'].add(('local-self-test','fixed loopback synthetic endpoints only',0,''))
    elif path=='/api/rtu-demo/start':
        # Dynamic demo writes in an internal PTY server: explicit local consent.
        p['writes'].add(('local-demo','virtual RTU pair',0,''))
    return p


def digest(path,body):return hashlib.sha256(json.dumps([path,body],sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


async def enable(request):
    b=await request.json();store=request.app[KEY];session=request['security_user']
    if not session or session['role'] not in ('Administrator','Engineer','Operator'):raise web.HTTPForbidden()
    pending=request.app['write_pending'].pop(b.get('id',''),None)
    if not pending or pending['session'] is not session or store.clock()>pending['expires']:raise ValueError('Write review expired; retry the operation')
    if b.get('confirm') is not True:raise ValueError('Explicit target confirmation required')
    session['write_consent']={'digest':pending['digest'],'expires':store.clock()+30}
    store.event('write_enabled',account=session['name'],targets=pending['summary'],lease_seconds=300)
    return web.json_response({'ok':True})


@web.middleware
async def boundary(request,handler):
    store=request.app[KEY]
    if not store.enabled:return await handler(request)
    user=request.get('security_user');path=request.path
    if not user:return await handler(request)
    b=await request.json() if request.body_exists and request.content_type=='application/json' else {}
    plan=operation_plan(request.app,path,b) if request.method not in ('GET','HEAD') else {'writes':set()}
    targets=plan['writes'];grant=None
    if targets:
        if user['role'] not in ('Administrator','Engineer','Operator'):return web.json_response({'error':'Write permission required'},status=403)
        if len(targets)>20000:raise ValueError('Operation exceeds 20,000 physical write addresses; split the operation')
        identity=request.app['catalogue'].get('scenarios',b['id']) if path=='/api/scenarios/run' else request.app['engine'].project if path=='/api/action' else None
        signature=digest(path,{'request':b,'targets':sorted(targets,key=str),'configuration':identity});consent=user.pop('write_consent',None)
        if not consent or consent['digest']!=signature or store.clock()>consent['expires']:
            pending=request.app['write_pending'];now=store.clock()
            for key in list(pending):
                if pending[key]['expires']<now:pending.pop(key)
            if len(pending)>=100:raise ValueError('Too many pending write reviews; wait one minute')
            ident=secrets.token_urlsafe(24);summary=sorted({str(t[:4]) for t in targets})
            pending[ident]={'session':user,'expires':now+60,'digest':signature,'summary':summary}
            return web.json_response({'error':'Explicit write enablement required','write_review':{'id':ident,'targets':summary,'target_details':[str(t) for t in sorted(targets,key=str)[:100]],'addresses':len(targets),'lease_seconds':300,'point_limit':10000}},status=428)
        grant=Grant(store,user,targets);store.event('write_operation_started',account=user['name'],path=path,addresses=len(targets))
    context=CONTEXT.set(grant)
    before={task for task in asyncio.all_tasks()}
    try:return await handler(request)
    finally:
        # Track only application workers, never aiohttp request/transport tasks.
        if grant:
            tasks=[]
            tasks.extend(request.app['engine'].tasks.values());tasks.extend(request.app['runner'].tasks.values())
            if request.app['rtu_demo'].task:tasks.append(request.app['rtu_demo'].task)
            for task in tasks:
                if task not in before and not task.done():request.app['write_owners'][task]=grant
        CONTEXT.reset(context)


def install(app):
    app['write_pending']={};app['write_owners']={};app.add_routes([web.post('/api/security/write-enable',enable)])
    async def lifecycle(app):
        async def sweep():
            while True:
                await asyncio.sleep(.25)
                for task,grant in list(app['write_owners'].items()):
                    if task.done():app['write_owners'].pop(task,None);continue
                    try:grant.check()
                    except ValueError:
                        task.cancel();app['write_owners'].pop(task,None);app[KEY].event('write_worker_cancelled',account=grant.session['name'])
        task=asyncio.create_task(sweep())
        try:yield
        finally:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
    app.cleanup_ctx.append(lifecycle)
