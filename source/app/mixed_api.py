"""Cancellable mixed generation and local reusable templates under existing auth."""
import asyncio,copy,json,time,uuid
from pathlib import Path
from aiohttp import web
from mixed_tags import generate,fingerprint,checked_spec
KEY=web.AppKey('mixed_generation',dict)
TEMPLATES=web.AppKey('mixed_templates',Path)


def rows(app):
    folder=app[TEMPLATES];folder.mkdir(exist_ok=True)
    result=[]
    for p in folder.glob('*.json'):
        if len(result)>=200:break
        try:result.append(json.loads(p.read_text()))
        except (ValueError,OSError):continue
    return result


async def templates(request):return web.json_response({'templates':rows(request.app)})
async def save(request):
    body=await request.json();spec=body.get('spec');checked_spec(spec)
    name=body.get('name','')
    if not isinstance(name,str) or not 1<=len(name.strip())<=100:raise ValueError('Template name must be 1..100 characters')
    ident=body.get('id') or uuid.uuid4().hex
    if not isinstance(ident,str) or len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):raise ValueError('Invalid template ID')
    folder=request.app[TEMPLATES];folder.mkdir(exist_ok=True)
    if not (folder/(ident+'.json')).exists() and len(rows(request.app))>=200:raise ValueError('Maximum 200 mixed templates')
    # Validate all row settings through generation against a harmless local configuration.
    protocol=body.get('protocol','opcua');connection={'id':'template','name':'Template','protocol':protocol,'url':'opc.tcp://127.0.0.1:4840','host':'127.0.0.1','tags':[]}
    await generate(connection,spec)
    row={'version':1,'id':ident,'name':name.strip(),'protocol':protocol,'spec':spec}
    temp=folder/(ident+'.tmp');temp.write_text(json.dumps(row,indent=2));temp.replace(folder/(ident+'.json'));return web.json_response(row)
async def delete(request):
    body=await request.json();ident=request.match_info['id']
    if body.get('confirm') is not True:raise ValueError('Confirm template deletion')
    if len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):raise ValueError('Invalid template ID')
    path=request.app[TEMPLATES]/(ident+'.json')
    if not path.exists():raise ValueError('Template not found')
    # Normal app recovery is a reusable trash archive, not destructive deletion.
    trash=request.app[TEMPLATES]/'trash';trash.mkdir(exist_ok=True);path.replace(trash/(ident+'-'+uuid.uuid4().hex+'.json'));return web.json_response({'ok':True})
async def start(request):
    body=await request.json();state=request.app[KEY]
    if len([r for r in state.values() if r['state']=='GENERATING'])>=2:raise ValueError('Two generation previews are already active')
    for k in list(state):
        if state[k]['state']!='GENERATING' and time.monotonic()-state[k]['created']>600:state.pop(k)
    if len(state)>=8:
        key=next((k for k,r in state.items() if r['state']!='GENERATING'),None)
        if key:state.pop(key)
    c=body['connection'];spec=body['spec'];checked_spec(spec);ident=uuid.uuid4().hex
    row={'id':ident,'state':'GENERATING','done':0,'total':sum(__import__('mixed_tags').quantities(spec)),'created':time.monotonic(),'fingerprint':fingerprint(c,spec),'result':None,'error':''};state[ident]=row
    async def run():
        try:
            row['result']=await generate(c,spec,lambda n,total:row.update(done=n,total=total));row['state']='READY'
        except asyncio.CancelledError:row.update(state='CANCELLED',result=None);raise
        except Exception as e:row.update(state='ERROR',error=str(e),result=None)
    row['task']=asyncio.create_task(run());return web.json_response({'id':ident})
async def status(request):
    r=request.app[KEY].get(request.match_info['id'])
    if not r:raise ValueError('Preview expired; generate again')
    result=r['result'];return web.json_response({k:r[k] for k in ('id','state','done','total','error')}|({'summary':{k:v for k,v in result.items() if k!='tags'},'preview':result['tags'][:5]+(result['tags'][-5:] if len(result['tags'])>5 else [])} if result else {}))
async def cancel(request):
    r=request.app[KEY].get(request.match_info['id'])
    if not r:raise ValueError('Preview not found')
    if not r['task'].done():r['task'].cancel();await asyncio.gather(r['task'],return_exceptions=True)
    r.update(state='CANCELLED',result=None);return web.json_response({'ok':True})
async def accept(request):
    body=await request.json();r=request.app[KEY].get(request.match_info['id'])
    if not r or time.monotonic()-r['created']>600 or r['state']!='READY':raise ValueError('No ready preview; generate and review again')
    if body.get('confirm') is not True or fingerprint(body['connection'],body['spec'])!=r['fingerprint']:raise ValueError('Preview changed; review the updated configuration before creating tags')
    result=r['result'];r.update(state='ACCEPTED',result=None);return web.json_response(result)


def install(app,folder):
    app[KEY]={};app[TEMPLATES]=folder
    app.add_routes([web.get('/api/mixed-templates',templates),web.post('/api/mixed-templates',save),web.post('/api/mixed-templates/{id}/delete',delete),web.post('/api/mixed-generation',start),web.get('/api/mixed-generation/{id}',status),web.post('/api/mixed-generation/{id}/cancel',cancel),web.post('/api/mixed-generation/{id}/accept',accept)])
    async def cleanup(app):
        tasks=[r['task'] for r in app[KEY].values() if not r['task'].done()]
        for t in tasks:t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    app.on_cleanup.append(cleanup)
