"""Managed configurations alongside, never automatically replacing, the workspace."""
import copy,json,uuid
from aiohttp import web
from project_library import ProjectLibrary,content,package,migrate,defaults,preferences,DEFAULTS,PREFERENCES,title
from model import default_project
from recovery import restore_credentials
from scenarios import validate_scenario
from performance_api import KEY as PERFORMANCE

KEY=web.AppKey('project_library',ProjectLibrary)
APPLY=web.AppKey('apply_workspace',object)


def require_idle(app):
    perf=app[PERFORMANCE]
    if app['engine'].active() or app['runner'].active() or any(m.task and not m.task.done() for m in app['monitors'].monitors.values()) or app['resources'].owners or perf.active or perf.waiting or app['rtu_demo'].state not in ('STOPPED','ERROR'):
        raise ValueError('Stop jobs, scenarios, monitors and client operations before opening a project')

def account(request):return (request.get('security_user') or {}).get('name','local')
async def listing(request):return web.json_response(request.app[KEY].listing(request.query.get('archived')=='1'))
async def detail(request):
    store=request.app[KEY];ident=request.match_info['id'];rev=request.query.get('revision')
    value=store.get(ident,int(rev) if rev else None);value['history']=store.history(ident);return web.json_response(value)
async def backup(request):
    value=request.app[KEY].get(request.match_info['id']);return web.json_response(package(value['content']),headers={'Content-Disposition':f'attachment; filename="project-{value["id"]}.json"'})
async def preview(request):
    value,note=migrate((await request.json())['document']);return web.json_response({'content':value,'migration':note,'sha256':package(value)['sha256']})
async def profiles(request):return web.json_response(request.app[KEY].profiles())
async def prefs(request):
    store=request.app[KEY];name='preferences:'+account(request)
    if request.method=='PUT':store.save_setting(name,preferences(await request.json()))
    return web.json_response(preferences(store.setting(name,PREFERENCES)))
async def default_settings(request):
    store=request.app[KEY]
    if request.method=='PUT':store.save_setting('defaults',defaults(await request.json()))
    return web.json_response(defaults(store.setting('defaults',DEFAULTS)))

async def command(request):
    app=request.app;store=app[KEY];b=await request.json();action=b.get('action');ident=b.get('id');result=None
    if action in ('create','capture'):
        if action=='create':
            p=default_project();p['settings']['creation_defaults']=defaults(store.setting('defaults',DEFAULTS));value={'project':p,'scenarios':[]}
        else:
            chosen=b.get('scenarios',[])
            if not isinstance(chosen,list) or len(chosen)>100:raise ValueError('Choose at most 100 saved scenario IDs')
            value={'project':copy.deepcopy(app['engine'].project),'scenarios':[app['catalogue'].get('scenarios',i) for i in chosen]}
        result=store.create(b.get('name'),value,b.get('description',''))
    elif action=='import':
        value,note=migrate(b['document']);result=store.create(b.get('name') or value['project']['name'],value,b.get('description',''));result['migration']=note
    elif action=='duplicate':
        old=store.get(ident);result=store.create(b.get('name'),old['content'],old['description'])
    elif action in ('update','rename','restore-revision'):
        old=store.get(ident)
        if action=='restore-revision':value=store.get(ident,b['source_revision'])['content']
        elif action=='update':
            value=old['content'];value['project']=copy.deepcopy(app['engine'].project)
        else:value=old['content'];value['project']['name']=title(b.get('name'))
        result=store.update(ident,value,b.get('expected_revision'),action,b.get('description'))
    elif action in ('archive','unarchive'):
        if b.get('confirm') is not True:raise ValueError('Explicit confirmation required')
        store.archive(ident,b.get('expected_revision'),b.get('name'),action=='archive');result={'ok':True}
    elif action=='open':
        old=store.get(ident)
        if old['deleted']:raise ValueError('Restore the archived project before opening')
        if b.get('confirm') is not True or b.get('expected_revision')!=old['revision']:raise ValueError('Confirm the selected project and current revision')
        expected=b.get('workspace_revision')
        if not isinstance(expected,str) or len(expected)!=64:raise ValueError('Current workspace revision is required')
        p=restore_credentials(old['content']['project'],app['engine'].project)
        response=await app[APPLY](request,p,expected=expected,require_idle=True)
        if response.status!=200:return response
        store.touch(ident);app['event_log'].emit('application','INFO','managed_project_opened',project_id=ident,revision=old['revision'],account=account(request))
        return response
    elif action=='save-profile':
        c=next((c for c in app['engine'].project['connections'] if c['id']==b.get('connection')),None)
        if not c:raise ValueError('Save the workspace connection first')
        result={'id':store.save_profile(b.get('name'),c)}
    elif action=='delete-profile':
        if b.get('confirm') is not True:raise ValueError('Confirm profile removal')
        from project_library import key
        with store.db() as db:db.execute('DELETE FROM profiles WHERE id=?',(key(ident),))
        result={'ok':True}
    elif action=='copy-test':
        old=store.get(ident);source=next((s for s in old['content']['scenarios'] if s['id']==b.get('scenario')),None)
        if source is None:raise ValueError('Saved test definition not found')
        s=copy.deepcopy(source);s['id']=uuid.uuid4().hex;s['name']=title(b.get('name'));s=validate_scenario(s);app['catalogue'].put('scenarios',s);result={'id':s['id'],'name':s['name']}
    else:raise ValueError('Unknown project command')
    app['event_log'].emit('application','INFO','managed_configuration_changed',action=action,project_id=ident or result.get('id',''),account=account(request))
    return web.json_response(result)


def install(app,folder,apply):
    from pathlib import Path
    app[KEY]=ProjectLibrary(Path(folder)/'project-library.sqlite3');app[APPLY]=apply
    app.add_routes([web.get('/api/project-library',listing),web.post('/api/project-library/command',command),web.post('/api/project-library/preview',preview),web.get('/api/project-library/profiles',profiles),web.get('/api/project-library/preferences',prefs),web.put('/api/project-library/preferences',prefs),web.get('/api/project-library/defaults',default_settings),web.put('/api/project-library/defaults',default_settings),web.get('/api/project-library/{id}/backup',backup),web.get('/api/project-library/{id}',detail)])
