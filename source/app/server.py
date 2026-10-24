"""Local-only Protocol Workbench UI/API, backed by independent protocol workers."""
import argparse
import asyncio
import csv
import io
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import time
from aiohttp import web
from asyncua import ua
from drivers import adapter
from engine import Engine
from model import default_project,validate_project,import_tags,uid,TYPES,view_value
from runtime_lock import acquire
from scenarios import Catalogue, Runner, validate_scenario, ident
from live_monitor import MonitorHub,safe
from resources import ResourceRegistry
from recovery import remember_safe
from transactions import TransactionStore,STORE_CONTEXT
from transaction_api import install as install_transactions,KEY as TRANSACTIONS_KEY
from verification import VerificationStore,STORE_CONTEXT as VERIFICATION_CONTEXT
from verification_api import install as install_verification,KEY as VERIFICATION_KEY
from communication_health import HealthHub,HUB as HEALTH_CONTEXT
from health_api import install as install_health,KEY as HEALTH_KEY
from performance import Performance,settings as performance_settings
from performance_api import install as install_performance,KEY as PERFORMANCE_KEY
from event_log import CONTEXT as EVENT_CONTEXT
from bridge import STORE as BRIDGE_CONTEXT
from bridge_api import install as install_bridges,KEY as BRIDGE_KEY
from access_control import access_boundary,install as install_access,KEY as ACCESS_KEY
from write_access import boundary as write_boundary,install as install_write_access
from credential_vault import boundary as vault_boundary,Vault
from licensing import boundary as licence_boundary,install as install_licensing
from version import VERSION

BASE=Path(__file__).resolve().parent
ROOT=BASE.parent
DATA=Path(os.environ.get('WORKBENCH_DATA',str(ROOT/'data')))
DATA.mkdir(parents=True,exist_ok=True)
PROJECT=DATA/'project.json'
TOKEN=secrets.token_urlsafe(32)

@web.middleware
async def boundary(request,handler):
    events=request.app.get('event_log')
    def denied(reason):
        if events:events.emit('security','WARNING','local_request_denied',reason=reason,method=request.method,path=request.path[:200])
    host=request.host.split(':')[0]
    if host not in ('127.0.0.1','localhost','[::1]'):
        denied('host');return web.json_response({'error':'Localhost access only'},status=403)
    origin=request.headers.get('Origin')
    if origin and origin not in (f'http://127.0.0.1:{request.url.port}',f'http://localhost:{request.url.port}'):
        denied('origin')
        return web.json_response({'error':'Cross-origin requests are not allowed'},status=403)
    if request.path.startswith('/api/') and request.path!='/api/bootstrap' and request.headers.get('X-Workbench-Token')!=TOKEN:
        denied('session token')
        return web.json_response({'error':'Reload the dashboard to establish a local session'},status=403)
    if events:events.learn(request.app['engine'].project)
    event_context=EVENT_CONTEXT.set(events)
    context=STORE_CONTEXT.set(request.app[TRANSACTIONS_KEY])
    verification_context=VERIFICATION_CONTEXT.set(request.app[VERIFICATION_KEY])
    health_context=HEALTH_CONTEXT.set(request.app[HEALTH_KEY])
    bridge_context=BRIDGE_CONTEXT.set(request.app[BRIDGE_KEY])
    try:return await handler(request)
    except (ValueError,KeyError,TypeError,OverflowError,json.JSONDecodeError) as error:
        from event_log import scrub
        return web.json_response({'error':scrub(str(error),events.secrets) if events and request.app[ACCESS_KEY].enabled else str(error)},status=400,headers={'Cache-Control':'no-store'})
    except web.HTTPException:raise
    except Exception as error:
        request.app['engine'].log('application','ERROR',f'{type(error).__name__}: {error}')
        from event_log import scrub
        message=f'{type(error).__name__}: {error}'
        return web.json_response({'error':scrub(message,events.secrets) if events and request.app[ACCESS_KEY].enabled else message},status=502,headers={'Cache-Control':'no-store'})
    finally:EVENT_CONTEXT.reset(event_context);STORE_CONTEXT.reset(context);VERIFICATION_CONTEXT.reset(verification_context);HEALTH_CONTEXT.reset(health_context);BRIDGE_CONTEXT.reset(bridge_context)

async def index(request):return web.FileResponse(BASE/'static/index.html')
async def bootstrap(request):
    store=request.app[ACCESS_KEY];user=request.get('security_user')
    return web.json_response({'token':TOKEN,'version':VERSION,'workspace_id':hashlib.sha256(str(DATA.resolve()).encode()).hexdigest(),'authentication_required':store.enabled and user is None,'licence_ack_required':bool(user and not user.get('licence_acknowledged')),'user':{'name':user['name'],'role':user['role']} if user else None,'data_directory':str(DATA) if not store.enabled or user else None,'login_startup_available':os.name=='nt' and (ROOT/'windows/Startup.ps1').is_file()})
async def project_get(request):
    from recovery import revision
    return web.json_response(request.app['engine'].project,headers={'ETag':revision(request.app['engine'].project)})
async def project_put(request):
    return await apply_project(request,validate_project(await request.json()))
async def apply_project(request,p,*,expected=None,require_idle=False):
    e=request.app['engine'];p=validate_project(p)
    async with e.lock:
        from recovery import revision
        if require_idle:
            from project_api import require_idle as idle_check
            idle_check(request.app)
        expected=expected if expected is not None else request.headers.get('If-Match')
        if expected and expected!=revision(e.project):return web.json_response({'error':'The saved workspace changed in another window. Your draft remains available in Recover work. Reload to load the latest saved configuration before reviewing and reapplying your draft.'},status=409)
        perf=performance_settings(p.get('settings',{}).get('performance'))
        perf_changed=perf!=request.app[PERFORMANCE_KEY].config
        if perf_changed and (e.active() or request.app['runner'].active() or any(m.task and not m.task.done() for m in request.app['monitors'].monitors.values()) or request.app[PERFORMANCE_KEY].active or request.app[PERFORMANCE_KEY].waiting):raise ValueError('Stop jobs, scenarios, monitors and client operations before changing performance controls')
        running=e.active() or request.app['runner'].active()
        if running:
            from config_changes import require_active_unchanged
            require_active_unchanged(e.project,p,{key for key,task in e.tasks.items() if not task.done()})
        recovery_warning=remember_safe(request.app['recovery'],'workspace','Before workspace change',e.project)
        temp=PROJECT.with_suffix('.tmp');temp.write_text(json.dumps(p,indent=2),encoding='utf-8');temp.replace(PROJECT)
        e.project=p
        if perf_changed:request.app[PERFORMANCE_KEY].configure(perf)
        if not running:e.states={}
        recovery_warning=remember_safe(request.app['recovery'],'workspace','Saved workspace',p) or recovery_warning
    return web.json_response({'ok':True,**({'recovery_warning':recovery_warning} if recovery_warning else {})},headers={'ETag':revision(p)})
async def status(request):
    data=request.app['engine'].snapshot();data['monitors']=[m.summary() for m in request.app['monitors'].monitors.values()];data['resources']=request.app['resources'].snapshot();data['rtu_demo']=request.app['rtu_demo'].summary();return web.json_response(data)
async def action(request):
    e=request.app['engine'];body=await request.json();act=body['action'];ident=body.get('id')
    if act=='stop_all':
        await request.app['rtu_demo'].stop();await request.app['runner'].stop();await request.app['monitors'].stop_all();await e.stop_all()
    elif act=='start_all':
        validate_project(e.project)
        added=[]
        try:
            from resources import job_plan
            for j in e.project['jobs']:
                if j['id'] in e.tasks and not e.tasks[j['id']].done():continue
                owner='job:'+j['id'];e.registry.acquire(owner,j.get('name',j['id']),job_plan(e.project,j),'workspace job');added.append(j['id'])
            for key in added:await e.start(key)
        except BaseException:
            for key in added:await e.stop(key)
            raise
    elif act=='pause':await e.pause(ident)
    elif act=='resume':await e.resume(ident)
    elif act=='start':validate_project(e.project);await e.start(ident)
    elif act=='stop':await e.stop(ident)
    else:raise ValueError('Unknown job action')
    return web.json_response({'ok':True})
async def shutdown(request):request.app['shutdown'].set();return web.json_response({'ok':True})
async def rtu_demo_start(request):return web.json_response(await request.app['rtu_demo'].start())
async def rtu_demo_stop(request):return web.json_response(await request.app['rtu_demo'].stop())
async def serial_ports(request):
    from serial.tools import list_ports
    return web.json_response({'ports':[{'device':p.device,'description':p.description} for p in list_ports.comports()]})
async def monitor_start(request):
    body=await request.json();c=next((c for c in request.app['engine'].project['connections'] if c['id']==body['connection']),None)
    if not c:raise ValueError('Connection not found; save your project first')
    m=request.app['monitors'].start(c,body['tags'],body.get('settings',{}))
    return web.json_response(m.summary())
async def monitor_get(request):
    m=request.app['monitors'].monitors.get(request.match_info['id'])
    if not m:raise ValueError('Monitor not found')
    return web.json_response(m.snapshot(int(request.query.get('page',0)),request.query.get('q',''),request.query.get('tag'),request.query.get('trends','').split(',') if request.query.get('trends') else None,request.query.get('quality','all')))
async def monitor_write(request):
    from live_write import preview,write
    m=request.app['monitors'].monitors.get(request.match_info['id'])
    if not m:raise ValueError('Monitor not found')
    b=await request.json()
    if b.get('mode')=='preview':return web.json_response(preview(m,b['tag'],b['value'])[2])
    if b.get('confirmed') is not True:raise ValueError('Confirm the target and typed value before writing')
    return web.json_response(await write(m,b['tag'],b['value'],request.app['resources']))
async def watchlist_list(request):
    from watchlists import resolve
    rows=request.app['catalogue'].all('watchlists')
    for row in rows:
        try:resolve(row,request.app['engine'].project);row['valid']=True;row['error']=''
        except ValueError as error:row['valid']=False;row['error']=str(error)
    return web.json_response(rows)
async def watchlist_save(request):
    from watchlists import make,resolve
    b=await request.json();m=request.app['monitors'].monitors.get(b.get('monitor'))
    if not m:raise ValueError('Open a monitor before saving a watchlist')
    if m.demo:raise ValueError('Temporary RTU demo ports expire; create a saved device before making a watchlist')
    row=make(b['name'],m.connection,[t['id'] for t in m.tags],m.settings,b.get('trends'))
    resolve(row,request.app['engine'].project)
    request.app['catalogue'].put('watchlists',row);return web.json_response(row)
async def watchlist_start(request):
    from watchlists import resolve
    b=await request.json();row=request.app['catalogue'].get('watchlists',b['id']);c=resolve(row,request.app['engine'].project)
    m=request.app['monitors'].start(c,row['tags'],row['settings'])
    return web.json_response({**m.summary(),'trends':row['trends']})
async def recording_start(request):
    from recordings import Recorder
    m=request.app['monitors'].monitors.get(request.match_info['id'])
    if not m or not m.task or m.task.done():raise ValueError('Start a monitor before recording')
    if m.recorder and m.recorder.meta['state']=='RECORDING':raise ValueError('Recording already active')
    b=await request.json();limit=b.get('limit',100000)
    if type(limit) is not int or not 100<=limit<=100000:raise ValueError('Recording limit must be 100..100000 rows')
    m.recorder=Recorder(DATA/'recordings',m.connection['name'],limit)
    return web.json_response(m.recorder.meta)
async def recording_stop(request):
    m=request.app['monitors'].monitors.get(request.match_info['id'])
    if not m or not m.recorder:raise ValueError('Recording not found')
    m.recorder.stop();return web.json_response(m.recorder.meta)
async def recording_list(request):
    from recordings import catalogue
    return web.json_response(catalogue(DATA/'recordings'))
async def recording_download(request):
    key=request.match_info['id']
    import re
    if not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('Invalid recording ID')
    for m in request.app['monitors'].monitors.values():
        if m.recorder and m.recorder.id==key and not m.recorder.file.closed:m.recorder.file.flush()
    path=DATA/'recordings'/(key+'.csv')
    if not path.exists():raise ValueError('Recording not found')
    return web.FileResponse(path,headers={'Content-Disposition':'attachment; filename="protocol-recording.csv"'})

async def monitor_stop(request):
    m=request.app['monitors'].monitors.get(request.match_info['id'])
    if not m:raise ValueError('Monitor not found')
    await m.stop();return web.json_response({'ok':True})
async def monitor_export(request):
    m=request.app['monitors'].monitors.get(request.match_info['id'])
    if not m:raise ValueError('Monitor not found')
    stream=io.StringIO();writer=csv.DictWriter(stream,['tag','dtype','units','value','quality','received','source_timestamp','server_timestamp','last_good','raw'],extrasaction='ignore');writer.writeheader()
    for row in m.values.values():writer.writerow(row)
    return web.Response(text=stream.getvalue(),content_type='text/csv',headers={'Content-Disposition':'attachment; filename="live-values.csv"'})
async def monitor_traffic(request):
    m=request.app['monitors'].monitors.get(request.match_info['id'])
    if not m:raise ValueError('Monitor not found')
    return web.Response(text=json.dumps(list(m.traffic),indent=2),content_type='application/json',headers={'Content-Disposition':'attachment; filename="modbus-traffic.json"'})
async def node_inspect(request):
    body=await request.json();monitor=request.app['monitors'].monitors.get(body.get('monitor'))
    c=monitor.connection if monitor else next((c for c in request.app['engine'].project['connections'] if c['id']==body['connection']),None)
    if not c or c['protocol']!='opcua':raise ValueError('Select a configured OPC UA connection')
    tag=next((t for t in c['tags'] if t['id']==body['tag']),None)
    if not tag:raise ValueError('Tag not found')
    from drivers import OPC
    client=OPC(c)
    try:
        await client.connect();await client.prepare([tag]);node=client.nodes[tag['id']]
        names=['NodeId','NodeClass','BrowseName','DisplayName','Description','DataType','ValueRank','ArrayDimensions','AccessLevel','UserAccessLevel','MinimumSamplingInterval','Historizing','Value']
        data=await node.read_attributes([getattr(ua.AttributeIds,n) for n in names]);rows=[]
        for name,value in zip(names,data):
            raw=value.Value.Value if value.Value else None
            human=view_value(raw,tag['dtype']) if name=='Value' and raw is not None else safe(raw)
            if name=='NodeClass' and isinstance(raw,int):human=f'{ua.NodeClass(raw).name} ({raw})'
            if name=='DataType' and isinstance(raw,ua.NodeId):human=f'{getattr(ua,"ObjectIdNames",{}).get(raw.Identifier,"Custom type")} ({raw.to_string()})' if raw.NamespaceIndex==0 else raw.to_string()
            if name in ('DisplayName','Description') and raw is not None:human=raw.Text
            if name in ('AccessLevel','UserAccessLevel') and isinstance(raw,int):human=', '.join(item.name for item in ua.AccessLevel if raw & (1<<item.value))+f' ({raw})'
            if name=='ValueRank' and raw==-1:human='Scalar (-1)'
            rows.append({'attribute':name,'value':human,'raw':safe(raw),'status':value.StatusCode.name})
        refs=await node.get_references()
        return web.json_response({'tag':tag['tag'],'attributes':rows,'references':[{'name':r.BrowseName.Name,'nodeid':r.NodeId.to_string(),'type':r.ReferenceTypeId.to_string(),'forward':r.IsForward} for r in refs[:200]],'references_total':len(refs)})
    finally:await client.close()

async def simulation_draft(request):
    from simulation_wizard import build_simulation
    return web.json_response({'scenario':build_simulation(await request.json())})

async def validate_only(request):
    validate_project(await request.json());return web.json_response({'ok':True})
async def preview_tags(request):
    from model import generated,initial,validate_simulation,default_project,view_value
    body=await request.json();c=body['connection'];p=default_project();p['connections']=[c];validate_project(p)
    chosen=body.get('tags',[])
    if not isinstance(chosen,list) or not chosen or len(chosen)>10:raise ValueError('Choose 1..10 tags for preview')
    byid={t['id']:t for t in c['tags']}
    if any(k not in byid for k in chosen):raise ValueError('Unknown preview tag')
    settings=body.get('simulation',{});rows=[]
    for key in chosen:
        t=byid[key];validate_simulation(t,settings)
        values=[view_value(initial(t,settings) if n==0 and 'initial_value' in t else generated(t,settings,n),t['dtype']) for n in range(12)]
        rows.append({'id':key,'tag':t['tag'],'dtype':t['dtype'],'units':t.get('units',''),'values':values})
    return web.json_response({'rows':rows,'cycles':12,'message':'Offline preview uses relative cycles 0..11. Live waveforms use the shared wall clock.'})

async def setup_inspect(request):
    from device_setup import inspect
    b=await request.json();return web.json_response(await inspect(b['connection'],b['mode']))
async def setup_address(request):
    from device_setup import modbus_offset
    from model import encode
    b=await request.json();result={'offset':modbus_offset(b['value'],b['notation'],b['area'])}
    if b.get('dtype') not in ('Boolean','Int16','UInt16','Int32','UInt32','Int64','UInt64','Float','Double'):raise ValueError('Unsupported datatype')
    result['words']=encode(b.get('example',1),b['dtype'],b.get('order','ABCD'))
    return web.json_response(result)

async def generated_tags(request):
    from tag_builder import generate_tags
    body=await request.json()
    tags=generate_tags(body['connection'],body['spec'])
    return web.json_response({'tags':tags,'count':len(tags)})

async def imported(request):
    body=await request.json();return web.json_response({'tags':import_tags(body['text'],body['filename'])})
async def connection_operation(request):
    body=await request.json();config=body['connection'];request.app['event_log'].learn({'connections':[config]});mode=body.get('mode','test')
    manual_owner=None
    p=default_project();p['connections']=[config];validate_project(p)
    client=adapter(config)
    try:
        await client.connect()
        if mode=='test':return web.json_response({'ok':True,'message':'Connected successfully'})
        if mode=='browse':
            if config['protocol']!='opcua':raise ValueError('Modbus has no tag browsing; add register addresses')
            return web.json_response({'tags':await client.browse()})
        if mode=='resolve':
            if config['protocol']!='opcua':raise ValueError('Resolve is for OPC UA only')
            tags=config['tags'];await client.prepare(tags,write=False)
            # Allow datatype discovery here by reading actual types, unlike strict job validation.
            return web.json_response({'tags':tags})
        tags=config.get('tags',[])
        selected=set(body.get('tags',[]));tags=[t for t in tags if not selected or t['id'] in selected]
        if not tags:raise ValueError('No tags selected')
        if mode=='write':
            from resources import empty,add_writes
            plan=empty();add_writes(plan,config,tags);manual_owner='manual:'+ident();request.app['resources'].acquire(manual_owner,'Manual write',plan,'manual write')
        await client.prepare(tags,write=mode=='write')
        if mode=='read':
            values=await client.read(tags)
            for t in tags:
                values[t['id']]=view_value(values[t['id']],t['dtype'])
            return web.json_response({'values':values,'raw':getattr(client,'last_raw',{})})
        if mode=='write':
            values=body['values'];count=await client.write(tags,values)
            return web.json_response({'ok':True,'written':count})
        raise ValueError('Unknown connection operation')
    finally:
        try:await client.close()
        finally:
            if manual_owner:request.app['resources'].release(manual_owner)
async def resolve(request):
    # Explicit tag resolution updates NodeIds AND live scalar datatypes, without writing.
    config=(await request.json())['connection']
    if config['protocol']!='opcua':raise ValueError('Only OPC UA tags need resolution')
    from drivers import OPC
    client=OPC(config)
    try:
        await client.connect();entries=await client.browse();lookup={}
        for entry in entries:
            for name in {entry['tag'],entry['tag'].rsplit('.',1)[-1].strip('"')}:
                lookup.setdefault(name,set()).add(entry['nodeid'])
        tags=json.loads(json.dumps(config.get('tags',[])))
        for t in tags:
            expected=t.get('browse_name') or t['tag']
            found=lookup.get(expected,set())
            if len(found)==1:t['nodeid']=next(iter(found))
            elif len(found)>1 and t.get('nodeid') in found:pass
            elif found or not t.get('nodeid'):raise ValueError(f"{t['tag']}: found {len(found)} matches; select a verified NodeId")
        for start in range(0,len(tags),50):
            part=tags[start:start+50];nodes=[client.client.get_node(t['nodeid']) for t in part]
            names=await client.client.read_attributes(nodes,ua.AttributeIds.BrowseName)
            types=await client.client.read_attributes(nodes,ua.AttributeIds.DataType)
            ranks=await client.client.read_attributes(nodes,ua.AttributeIds.ValueRank)
            for t,name,dt,rank in zip(part,names,types,ranks):
                name.StatusCode.check();dt.StatusCode.check();rank.StatusCode.check()
                actual=name.Value.Value.Name;expected=t.get('browse_name') or t['tag']
                if expected not in (actual,actual.rsplit('.',1)[-1].strip('"')):raise ValueError(f"{t['tag']}: NodeId refers to {actual}; confirm the expected browse name or choose the correct node")
                t['browse_name']=actual
                if rank.Value.Value!=-1:raise ValueError(f"{t['tag']} is not a scalar")
                kind=next((k for k in TYPES if dt.Value.Value==ua.NodeId(getattr(ua.ObjectIds,k))),None)
                if not kind:raise ValueError(f"{t['tag']} has an unsupported datatype")
                t['dtype']=kind
        return web.json_response({'tags':tags})
    finally:await client.close()
async def certificate(request):
    body=await request.json();name=Path(body['filename']).name
    if Path(name).suffix.lower() not in ('.pem','.der','.crt','.key'):raise ValueError('Certificate/key must be PEM, DER, CRT, or KEY')
    import base64
    raw=base64.b64decode(body['data'],validate=True)
    if len(raw)>100000:raise ValueError('Certificate file is too large')
    if request.app[ACCESS_KEY].enabled:
        from certificate_access import validate_upload
        validate_upload(name,raw)
    folder=DATA/'certificates';folder.mkdir(exist_ok=True);path=folder/(uid()+'-'+name);path.write_bytes(raw)
    if os.name!='nt':path.chmod(0o600)
    return web.json_response({'path':str(path)})
async def export(request):
    import copy
    p=copy.deepcopy(request.app['engine'].project)
    for c in p['connections']:c.pop('password',None)
    return web.Response(text=json.dumps(p,indent=2),content_type='application/json',headers={'Content-Disposition':'attachment; filename="protocol-project.json"'})
async def export_tags(request):
    c=next(c for c in request.app['engine'].project['connections'] if c['id']==request.match_info['id'])
    text=io.StringIO();fields=['tag','dtype','nodeid','browse_name','address','area','order']
    writer=csv.DictWriter(text,fields,extrasaction='ignore');writer.writeheader();writer.writerows(c.get('tags',[]))
    return web.Response(text=text.getvalue(),content_type='text/csv',headers={'Content-Disposition':'attachment; filename="tags.csv"'})
async def diagnostics(request):
    if request.app['engine'].active():raise ValueError('Stop all jobs before running the local installation check')
    from diagnostics import run_diagnostics
    from credential_vault import CONTEXT as VAULT
    from write_access import CONTEXT as WRITE
    if request.app[ACCESS_KEY].enabled:
        scope=WRITE.get()
        if scope is None or ('local-self-test','fixed loopback synthetic endpoints only',0,'') not in scope.targets:raise ValueError('Confirm the isolated self-test before starting')
        scope.check()
    # Fixed built-in self-test only: no supplied/saved endpoint is consumed. Its
    # ephemeral ports cannot be known in the preflight physical-target plan.
    vault_token=VAULT.set(None);write_token=WRITE.set(None)
    try:return web.json_response(await run_diagnostics())
    finally:VAULT.reset(vault_token);WRITE.reset(write_token)

async def startup(request):
    if os.name!='nt':raise ValueError('Windows login startup is available only on Windows')
    import subprocess
    if not (ROOT/'windows/Startup.ps1').is_file():raise ValueError('Login startup is not included in this community preview package')
    enabled=bool((await request.json())['enabled'])
    script=ROOT/'windows/Startup.ps1'
    proc=await asyncio.create_subprocess_exec('powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Enabled',str(int(enabled)),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    output,error=await proc.communicate()
    if proc.returncode:raise ValueError(error.decode(errors='replace'))
    return web.json_response({'ok':True,'message':output.decode(errors='replace')})


async def template_list(request):
    return web.json_response(request.app['catalogue'].all('templates'))
async def template_export(request):
    from templates import portable
    return web.json_response(portable(request.app['catalogue'].get('scenarios',request.match_info['id'])))
async def template_preview(request):
    from templates import preview
    b=await request.json();clean,resolved,missing=preview(b['template'],b.get('bindings'),b.get('name'))
    return web.json_response({'template':clean,'missing':missing,'points':sum(len(c['tags']) for c in resolved['project']['connections']),'jobs':len(resolved['project']['jobs']),'steps':len(resolved['steps'])})
async def template_save(request):
    from templates import preview,portable
    b=await request.json()
    document=portable(request.app['catalogue'].get('scenarios',b['scenario'])) if 'scenario' in b else preview(b['template'])[0]
    row={'id':ident(),'name':document['scenario']['name'],'updated':time.time(),'template':document}
    request.app['catalogue'].put('templates',row)
    return web.json_response(row)
async def template_import(request):
    from templates import instantiate,preview
    b=await request.json();scenario=instantiate(b['template'],b.get('bindings',{}),b.get('name'));scenario['updated']=time.time()
    request.app['catalogue'].put('scenarios',scenario)
    return web.json_response(scenario)

async def signal_path_draft(request):
    from signal_path import build
    s,detail=build(request.app['engine'].project,await request.json());return web.json_response({'scenario':s,'preview':detail})
async def signal_path_bulk(request):
    from signal_path import build_bulk
    s,details=build_bulk(request.app['engine'].project,await request.json());return web.json_response({'scenario':s,'preview':details})
async def signal_path_demo(request):
    from signal_path import demo
    return web.json_response({'scenario':demo()})

async def recovery_drafts(request):return web.json_response(request.app['recovery'].drafts())
async def recovery_save(request):return web.json_response(request.app['recovery'].draft(await request.json()))
async def recovery_get(request):return web.json_response(request.app['recovery'].get_draft(request.match_info['id']))
async def recovery_mark(request):
    request.app['recovery'].mark(request.match_info['id'],(await request.json())['state']);return web.json_response({'ok':True})
async def recovery_history(request):return web.json_response(request.app['recovery'].history())
async def recovery_snapshot(request):return web.json_response(request.app['recovery'].snapshot(request.match_info['id']))

async def scenario_list(request):
    store=request.app['catalogue'];return web.json_response([{**s,**store.revision(s)} for s in store.all('scenarios')])
async def scenario_save(request):
    body=validate_scenario(await request.json())
    body['id']=body.get('id') or ident();body['updated']=time.time()
    try:remember_safe(request.app['recovery'],'scenario:'+body['id'],'Before scenario change',request.app['catalogue'].get('scenarios',body['id']))
    except ValueError:pass
    request.app['catalogue'].put('scenarios',body)
    remember_safe(request.app['recovery'],'scenario:'+body['id'],body['name'],body)
    return web.json_response({**body,**request.app['catalogue'].revision(body)})
async def scenario_run(request):
    key=await request.app['runner'].start((await request.json())['id'])
    return web.json_response({'id':key})
async def scenario_stop(request):
    body=await request.json()
    await request.app['runner'].stop(body.get('id'));return web.json_response({'ok':True})
async def run_list(request):
    rows=[]
    for r in request.app['catalogue'].all('runs')[:100]:
        r.pop('snapshot',None)
        r.pop('logs',None)
        for step in r['steps']:
            for field in ('tags','attempts','transactions'):step.pop(field,None)
        rows.append(r)
    return web.json_response(rows)
async def run_detail(request):
    r=request.app['catalogue'].get('runs',request.match_info['id']);r.pop('snapshot',None)
    return web.json_response(r)
async def run_csv(request):
    r=request.app['catalogue'].get('runs',request.match_info['id'])
    stream=io.StringIO();fields=['run_id','step','action','tag','dtype','state','observed','written','expected','error','observed_delay_ms','write_outcome','raw_words','baseline_source','baseline_destination']
    writer=csv.DictWriter(stream,fields,extrasaction='ignore');writer.writeheader()
    for step in r['steps']:
        for t in step.get('tags',[]):
            writer.writerow({**t,'run_id':r['id'],'step':step['index'],'action':step['action'],'expected':json.dumps(step.get('expectation',{})),'observed_delay_ms':step.get('observed_delay_ms'),'write_outcome':step.get('write_outcome'),'raw_words':json.dumps(t.get('raw_words',[])),'baseline_source':step.get('baseline_source'),'baseline_destination':step.get('baseline_destination')})
    return web.Response(text=stream.getvalue(),content_type='text/csv',headers={'Content-Disposition':'attachment; filename="tag-results.csv"'})

async def run_report(request):
    import html
    r=request.app['catalogue'].get('runs',request.match_info['id']);r.pop('snapshot',None)
    rows=''.join('<tr>'+''.join('<td>'+html.escape(str(x.get(k,'')))+'</td>' for k in ('index','label','state','observed','written','expectation','duration','error'))+'</tr>' for x in r['steps'])
    tag_tables=''.join('<h2>Step '+str(step['index'])+' · tag results</h2><table><tr><th>Tag</th><th>Type</th><th>State</th><th>Observed</th><th>Written</th><th>Error</th></tr>'+''.join('<tr>'+''.join('<td>'+html.escape(str(t.get(k,'')))+'</td>' for k in ('tag','dtype','state','observed','written','error'))+'</tr>' for t in step.get('tags',[]))+'</table>' for step in r['steps'] if step.get('tags'))
    path_tables=''
    for step in r['steps']:
        if not step.get('path'):continue
        p=step['path'];fields={'Source tag':p['source_tag'],'Observed tag':p['destination_tag'],'Probe / write outcome':str(step.get('written','—'))+' / '+step.get('write_outcome','—'),'Expected observation':p['expected'],'Observed value':step.get('observed','—'),'Starting source / destination':str(step.get('baseline_source','—'))+' / '+str(step.get('baseline_destination','—')),'Modbus unit / area / offset':str(p['unit'])+' / '+p['area']+' / '+str(p['address']),'Datatype / word order':p['destination_type']+' / '+p['order'],'Expected raw words':p['expected_words'],'Observed raw words':step.get('raw_words','—'),'Observed delay (ms)':step.get('observed_delay_ms','Not confirmed'),'Allowed delay (ms)':p['max_delay']*1000,'Write acknowledgement (ms)':step.get('write_ack_ms','—')}
        path_tables+='<h2>Signal-path verification</h2><table>'+''.join('<tr><th>'+html.escape(k)+'</th><td>'+html.escape(str(v))+'</td></tr>' for k,v in fields.items())+'</table><p>'+html.escape(step.get('measurement_note',''))+'</p>'
    content='<!doctype html><html lang="en"><meta charset="utf-8"><title>Test report</title><style>body{font:15px system-ui;margin:40px;color:#172b40}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:10px;text-align:left}pre{white-space:pre-wrap}</style><h1>'+html.escape(r['name'])+'</h1><p>Run '+html.escape(r['id'])+' · '+html.escape(r['state'])+'</p><p>Skipped steps: '+str(r.get('skipped',0))+'</p><table><tr><th>Step</th><th>Label</th><th>State</th><th>Observed</th><th>Written</th><th>Expected</th><th>Seconds</th><th>Error</th></tr>'+rows+'</table>'+path_tables+tag_tables+'<h2>Run evidence</h2><pre>'+html.escape(json.dumps(r,indent=2))+'</pre></html>'
    return web.Response(text=content,content_type='text/html',headers={'Content-Disposition':'attachment; filename="test-report.html"'})

def create_app(project=None):
    if project is None:
        project=validate_project(json.loads(PROJECT.read_text(encoding='utf-8'))) if PROJECT.exists() else default_project()
    app=web.Application(middlewares=[boundary,access_boundary,licence_boundary,vault_boundary,write_boundary],client_max_size=25*1024*1024)
    from recordings import catalogue
    for row in catalogue(DATA/'recordings'):
        if row['state']=='RECORDING':
            row.update(state='INTERRUPTED',finished=time.time(),reason='Application exited before recording closed; last buffered rows may be missing')
            (DATA/'recordings'/(row['id']+'.json')).write_text(json.dumps(row),encoding='utf-8')
    from recovery import RecoveryStore
    app['recovery']=RecoveryStore(DATA/'recovery.sqlite3');remember_safe(app['recovery'],'workspace','Saved workspace at startup',project)
    app['resources']=ResourceRegistry();app['engine']=Engine(project,registry=app['resources']);app['shutdown']=asyncio.Event()
    from scenario_api import install as install_scenario_automation
    install_scenario_automation(app)
    app['catalogue']=Catalogue(DATA/'scenarios.sqlite3');app['runner']=Runner(app['catalogue'],registry=app['resources']);app['monitors']=MonitorHub()
    from rtu_demo import RTUDemo
    app['rtu_demo']=RTUDemo(app['monitors'])
    app.add_routes([web.post('/api/rtu-demo/start',rtu_demo_start),web.post('/api/rtu-demo/stop',rtu_demo_stop)])
    app.add_routes([web.get('/api/watchlists',watchlist_list),web.post('/api/watchlists',watchlist_save),web.post('/api/watchlists/start',watchlist_start),web.get('/api/recordings',recording_list),web.get('/api/recordings/{id}/csv',recording_download),web.post('/api/monitors/{id}/record',recording_start),web.post('/api/monitors/{id}/record-stop',recording_stop)])
    app.add_routes([web.post('/api/monitors/{id}/write',monitor_write),web.post('/api/monitors',monitor_start),web.get('/api/monitors/{id}',monitor_get),web.post('/api/monitors/{id}/stop',monitor_stop),web.get('/api/monitors/{id}/csv',monitor_export),web.get('/api/monitors/{id}/traffic',monitor_traffic),web.post('/api/node-inspect',node_inspect)])
    app.add_routes([web.post('/api/signal-path/bulk',signal_path_bulk),web.post('/api/signal-path/draft',signal_path_draft),web.post('/api/signal-path/demo',signal_path_demo)])
    app.add_routes([web.get('/api/recovery/drafts',recovery_drafts),web.post('/api/recovery/drafts',recovery_save),web.get('/api/recovery/drafts/{id}',recovery_get),web.post('/api/recovery/drafts/{id}/mark',recovery_mark),web.get('/api/recovery/history',recovery_history),web.get('/api/recovery/history/{id}',recovery_snapshot)])
    app.add_routes([web.post('/api/device-setup/inspect',setup_inspect),web.post('/api/device-setup/address',setup_address)])
    app.add_routes([web.get('/api/templates',template_list),web.post('/api/templates/preview',template_preview),web.post('/api/templates/save',template_save),web.post('/api/templates/import',template_import),web.get('/api/scenarios/{id}/template',template_export)])
    app.add_routes([web.get('/api/scenarios',scenario_list),web.post('/api/scenarios',scenario_save),web.post('/api/scenarios/run',scenario_run),web.post('/api/scenarios/stop',scenario_stop),web.get('/api/runs',run_list),web.get('/api/runs/{id}',run_detail),web.get('/api/runs/{id}/report',run_report),web.get('/api/runs/{id}/csv',run_csv)])
    app.add_routes([web.get('/',index),web.get('/api/bootstrap',bootstrap),web.get('/api/project',project_get),web.put('/api/project',project_put),web.get('/api/serial-ports',serial_ports),web.get('/api/status',status),web.post('/api/action',action),web.post('/api/shutdown',shutdown),web.post('/api/import-tags',imported),web.post('/api/generate-tags',generated_tags),web.post('/api/validate-project',validate_only),web.post('/api/simulation-draft',simulation_draft),web.post('/api/preview-tags',preview_tags),web.post('/api/connection',connection_operation),web.post('/api/resolve',resolve),web.post('/api/certificate',certificate),web.get('/api/export',export),web.get('/api/tags/{id}',export_tags),web.post('/api/startup',startup),web.post('/api/diagnostics',diagnostics)])
    app[PERFORMANCE_KEY]=Performance(project.get('settings',{}).get('performance'));install_performance(app)
    app[TRANSACTIONS_KEY]=TransactionStore(performance=app[PERFORMANCE_KEY]);install_transactions(app)
    app[VERIFICATION_KEY]=VerificationStore();install_verification(app)
    app[HEALTH_KEY]=HealthHub();install_health(app)
    from mixed_api import install as install_mixed
    install_mixed(app,DATA/'mixed-templates')
    install_bridges(app,DATA/'bridge-templates')
    from reporting_api import install as install_reporting
    install_reporting(app,DATA)
    from project_api import install as install_project_library
    install_project_library(app,DATA,apply_project);install_licensing(app)
    install_access(app,DATA);install_write_access(app)
    app['credential_vault']=Vault(DATA/'security')
    from certificate_access import install as install_certificate_access
    install_certificate_access(app,DATA)
    app['event_log'].secrets.add(TOKEN)
    app['event_log'].emit('application','INFO','application_initialized',version=VERSION)
    app.router.add_static('/static',BASE/'static');app.router.add_static('/guides',ROOT/'guides');app.router.add_static('/examples',ROOT/'examples')
    async def cleanup(app):
        await app['rtu_demo'].stop();await app['runner'].stop();await app['monitors'].stop_all();await app['engine'].stop_all()
        app['event_log'].emit('application','INFO','application_cleanup_complete')
        app['credential_vault'].close()
        app['event_log'].close()
    async def performance_lifecycle(app):
        await app[PERFORMANCE_KEY].start()
        try:yield
        finally:await app[PERFORMANCE_KEY].stop()
    app.cleanup_ctx.append(performance_lifecycle)
    app.on_cleanup.append(cleanup)
    return app
async def main(port):
    handler=RotatingFileHandler(DATA/'application.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
    logging.basicConfig(level=logging.WARNING,handlers=[handler],format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('workbench').setLevel(logging.INFO)
    app=create_app();EVENT_CONTEXT.set(app['event_log']);STORE_CONTEXT.set(app[TRANSACTIONS_KEY]);VERIFICATION_CONTEXT.set(app[VERIFICATION_KEY]);HEALTH_CONTEXT.set(app[HEALTH_KEY]);BRIDGE_CONTEXT.set(app[BRIDGE_KEY]);runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',port)
    try:
        await site.start();(DATA/'port.txt').write_text(str(port));(DATA/'token.txt').write_text(TOKEN)
        if os.name!='nt':(DATA/'token.txt').chmod(0o600)
        if not app[ACCESS_KEY].enabled and app['engine'].project.get('settings',{}).get('autostart',False):
            for j in app['engine'].project['jobs']:
                try:await app['engine'].start(j['id'])
                except ValueError as error:
                    app['engine'].states[j['id']]={'state':'ERROR','error':str(error),'cycles':0,'values':[]}
                    app['engine'].log(j['id'],'ERROR',str(error))
        print(f'Protocol Workbench ready: http://127.0.0.1:{port}',flush=True)
        stop=DATA/'stop.request'
        while not app['shutdown'].is_set():
            if stop.exists():break
            await asyncio.sleep(0.25)
    finally:await runner.cleanup();(DATA/'stop.request').unlink(missing_ok=True)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8877);args=parser.parse_args()
    with acquire(DATA/'app.lock'):
        (DATA/'stop.request').unlink(missing_ok=True)
        asyncio.run(main(args.port))
