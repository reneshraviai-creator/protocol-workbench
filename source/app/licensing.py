"""Offline paper-licence metadata and receipt notice. Never enforces expiry."""
from datetime import date
import hashlib,json
from aiohttp import web
from project_api import KEY as PROJECTS
from version import VERSION,SOFTWARE_NAME

DEFAULT={'licensed_organisation':'','licence_reference':'','licence_type':'Not configured','issue_date':'','expiry_date':'','licensed_user':'','publisher':'Renesh Ravindran','support_contact':'renesh.ravi.ai@gmail.com','revision':0}
NOTICE='This software is provided under a commercial software licence agreement.\n\nUse of Protocol Workbench is subject to the terms and conditions of the licence agreement issued to the licensed organisation.\n\nUnauthorised copying, redistribution or commercial use may constitute a breach of the applicable licence agreement.'


def validate(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT):raise ValueError('Unsupported licence information field')
    v={**DEFAULT,**value}
    for k in DEFAULT:
        if k=='revision':continue
        if not isinstance(v[k],str) or len(v[k])>200 or any(ord(c)<32 for c in v[k]):raise ValueError(f'{k} must be plain text of at most 200 characters')
        v[k]=v[k].strip()
    for k in ('issue_date','expiry_date'):
        if v[k]:
            try:
                parsed=date.fromisoformat(v[k])
                if parsed.isoformat()!=v[k]:raise ValueError()
            except ValueError:raise ValueError(f'{k} must be YYYY-MM-DD or blank') from None
    if v['issue_date'] and v['expiry_date'] and v['expiry_date']<v['issue_date']:raise ValueError('Expiry must not precede issue date')
    if type(v['revision']) is not int or v['revision']<0:raise ValueError('Invalid licence information revision')
    return v


def information(app):
    v=validate(app[PROJECTS].setting('licence_information',DEFAULT));expired=bool(v['expiry_date'] and v['expiry_date']<date.today().isoformat())
    v.update(software_name=SOFTWARE_NAME,software_version=VERSION,expired=expired,status='Expired — operation remains available' if expired else 'Current' if v['issue_date'] else 'Not configured',notice=NOTICE)
    v['notice_sha256']=hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest();return v

async def info(request):return web.json_response(information(request.app),headers={'Cache-Control':'no-store'})
async def configure(request):
    user=request.get('security_user')
    if not user or user['role']!='Administrator':return web.json_response({'error':'Sign in as Administrator to edit licence information','permission_denied':True},status=403)
    b=await request.json();old=information(request.app)
    if b.get('revision')!=old['revision'] or type(b.get('revision')) is not int:raise ValueError('Licence information changed; reload before saving')
    value=validate(b);value['revision']+=1;request.app[PROJECTS].save_setting('licence_information',value)
    request.app['event_log'].emit('security','INFO','licence_information_changed',account=user['name'],revision=value['revision'])
    return web.json_response(information(request.app))
async def acknowledge(request):
    user=request.get('security_user')
    if user is None:raise ValueError('Sign in before acknowledging a login notice')
    if user.get('licence_acknowledged'):return web.json_response({'ok':True,'already_acknowledged':True})
    b=await request.json();value=information(request.app)
    if b.get('confirm') is not True or b.get('notice_sha256')!=value['notice_sha256']:raise ValueError('Review the current licence notice before acknowledging receipt')
    user['licence_acknowledged']={'sha256':value['notice_sha256'],'revision':value['revision']}
    request.app['event_log'].emit('security','INFO','licence_notice_acknowledged',account=user['name'],notice_sha256=value['notice_sha256'],revision=value['revision'],meaning='Informational receipt only; no agreement acceptance or activation')
    return web.json_response({'ok':True})


def safe_stop(path,body):
    return path=='/api/action' and body.get('action') in ('stop','stop_all','pause') or path in ('/api/scenarios/stop','/api/rtu-demo/stop') or path.startswith('/api/monitors/') and path.endswith('/stop')

@web.middleware
async def boundary(request,handler):
    user=request.get('security_user');path=request.path
    if user and not user.get('licence_acknowledged') and path.startswith('/api/') and path not in ('/api/bootstrap','/api/security/login','/api/security/logout','/api/licence','/api/licence/acknowledge'):
        b=await request.json() if request.body_exists and request.content_type=='application/json' else {}
        if not safe_stop(path,b):return web.json_response({'error':'Acknowledge the licence information notice to enter the application','licence_ack_required':True},status=428,headers={'Cache-Control':'no-store'})
    return await handler(request)


def install(app):app.add_routes([web.get('/api/licence',info),web.put('/api/licence',configure),web.post('/api/licence/acknowledge',acknowledge)])
