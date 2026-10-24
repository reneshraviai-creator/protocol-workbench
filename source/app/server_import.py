"""Read-only OPC UA inventory and independent local simulation drafts.

No source write operation is invoked. Inventories are ephemeral and session-owned.
"""
import asyncio
import copy
import hashlib
import json
import time
from collections import deque
from urllib.parse import urlparse
from aiohttp import web
from asyncua import ua
from drivers import OPC
from model import TYPES,cast,default_project,uid,validate_project,view_value
from simulation_wizard import build_simulation
from access_control import COOKIE,KEY as ACCESS

KEY=web.AppKey('opcua_import',dict)
MAX_TAGS=20000
MAX_NODES=25000
MAX_DEPTH=32
TTL=1200


def source_connection(value):
    if not isinstance(value,dict) or value.get('protocol')!='opcua':raise ValueError('Choose an OPC UA source connection')
    keys=('name','url','security','username','password','certificate','private_key','server_certificate','timeout','retry_seconds','performance')
    c={k:copy.deepcopy(value[k]) for k in keys if k in value};c.update(id=uid(),protocol='opcua',tags=[])
    c.setdefault('name','Import source');u=urlparse(c.get('url',''))
    if u.username or u.password:raise ValueError('Enter source credentials in the authentication fields, not in the URL')
    p=default_project();p['connections']=[c];validate_project(p);return c


def issue(row,kind,path,reason):
    row['issues_count']+=1;row['partial']=True
    if len(row['issues'])<100:row['issues'].append({'kind':kind,'path':' / '.join(path),'reason':reason})


class ScanLimit(Exception):pass


async def references(client,parents,check):
    """Batch browse; consume server continuation points before returning each parent."""
    params=ua.BrowseParameters();params.RequestedMaxReferencesPerNode=200
    for node,path in parents:
        d=ua.BrowseDescription();d.NodeId=node;d.BrowseDirection=ua.BrowseDirection.Forward;d.ReferenceTypeId=ua.NodeId(ua.ObjectIds.HierarchicalReferences);d.IncludeSubtypes=True;d.NodeClassMask=int(ua.NodeClass.Object|ua.NodeClass.Variable);d.ResultMask=ua.BrowseResultMask.All;params.NodesToBrowse.append(d)
    session=client.client.nodes.objects.session
    results=await session.browse(params)
    if len(results)!=len(parents):raise ValueError('Incomplete OPC UA browse response')
    for parent,result in zip(parents,results):
        while True:
            check();yield parent,result
            if not result.StatusCode.is_good() or not result.ContinuationPoint:break
            nxt=ua.BrowseNextParameters();nxt.ContinuationPoints=[result.ContinuationPoint];nxt.ReleaseContinuationPoints=False
            extra=await session.browse_next(nxt)
            if len(extra)!=1:raise ValueError('Incomplete OPC UA BrowseNext response')
            result=extra[0]


async def metadata(client,entries,row,check):
    size=min(50,client.read_batch)
    for start in range(0,len(entries),size):
        check();part=entries[start:start+size];nodes=[client.client.get_node(e[0]) for e in part]
        types=await client.client.read_attributes(nodes,ua.AttributeIds.DataType)
        ranks=await client.client.read_attributes(nodes,ua.AttributeIds.ValueRank)
        if len(types)!=len(part) or len(ranks)!=len(part):raise ValueError('Incomplete OPC UA metadata response')
        for (nid,path),dt,rank,node in zip(part,types,ranks,nodes):
            if len(row['tags'])>=MAX_TAGS:raise ScanLimit('Supported tag limit reached (20,000); inventory is incomplete')
            kind=next((k for k in TYPES if dt.StatusCode.is_good() and dt.Value and dt.Value.Value==ua.NodeId(getattr(ua.ObjectIds,k))),None)
            if not kind and dt.StatusCode.is_good() and dt.Value and dt.Value.Value.NamespaceIndex!=0:
                try:
                    variant=await node.read_data_type_as_variant_type();kind=variant.name if variant.name in TYPES else None
                except ua.UaStatusCodeError:pass
            if not rank.StatusCode.is_good() or not rank.Value or rank.Value.Value!=-1 or not kind:
                row['skipped']+=1;issue(row,'unsupported or inaccessible',path,'Requires an accessible supported scalar datatype; arrays and structures are not copied as values');continue
            name=path[-1];label=name
            # Repeated browse names in different folders remain distinct and deterministic.
            if label in row['_names']:label=f'{name} [{nid}]'
            while label in row['_names']:label+=' (copy)'
            row['_names'].add(label)
            row['tags'].append({'id':uid(),'tag':label,'browse_name':name,'nodeid':nid,'dtype':kind,'index':len(row['tags'])+1,'path':path,'folder':json.dumps(path[:-1],ensure_ascii=False)})
        await asyncio.sleep(0)


async def inventory(config,row,check=lambda:None):
    client=OPC(config)
    try:
        async with asyncio.timeout(300):
            await client.connect();queue=deque([(client.client.nodes.objects.nodeid,[])]);seen=set();variables=set();references_seen=0
            try:
                while queue:
                    check();parents=[]
                    while queue and len(parents)<min(50,client.read_batch):
                        nid,path=queue.popleft();key=nid.to_string()
                        if key in seen:continue
                        if len(seen)>=MAX_NODES:raise ScanLimit('Browse node limit reached (25,000); inventory is incomplete')
                        seen.add(key);parents.append((nid,path))
                    if not parents:continue
                    entries=[]
                    async for (parent,path),result in references(client,parents,check):
                        if not result.StatusCode.is_good():issue(row,'browse denied',path,result.StatusCode.name);continue
                        for ref in result.References:
                            check();references_seen+=1
                            if references_seen>100000:raise ScanLimit('Browse reference limit reached (100,000); inventory is incomplete')
                            if ref.NodeId==ua.NodeId(ua.ObjectIds.Server):continue
                            if ref.NodeClass not in (ua.NodeClass.Object,ua.NodeClass.Variable):continue
                            nid=ref.NodeId.to_string();child=path+[ref.BrowseName.Name or nid]
                            if ref.NodeClass==ua.NodeClass.Variable and ref.NodeId.NamespaceIndex==0:continue
                            if ref.NodeClass==ua.NodeClass.Variable and nid not in variables:
                                variables.add(nid);row['discovered']+=1;entries.append((nid,child))
                            if len(child)<MAX_DEPTH:
                                if len(queue)>=MAX_NODES:raise ScanLimit('Pending browse node limit reached; inventory is incomplete')
                                if nid not in seen:queue.append((ref.NodeId,child))
                            else:issue(row,'depth limit',child,'Children beyond depth 32 are not browsed')
                    await metadata(client,entries,row,check);row['browsed_nodes']=len(seen)
            except ScanLimit as e:issue(row,'limit',[],str(e))
    finally:await client.close()


def matches(row,query='',folder=''):
    if not isinstance(query,str) or len(query)>200:raise ValueError('Search supports up to 200 characters')
    prefix=None
    if folder:
        try:prefix=json.loads(folder)
        except (TypeError,ValueError):raise ValueError('Invalid folder filter')
        if not isinstance(prefix,list) or any(not isinstance(x,str) for x in prefix):raise ValueError('Invalid folder filter')
    query=query.casefold()
    return [t for t in row['tags'] if (prefix is None or t['path'][:-1][:len(prefix)]==prefix) and (not query or query in (' / '.join(t['path'])+' '+t['nodeid']+' '+t['dtype']).casefold())]


def defaults(kind):return '' if kind=='String' else '2000-01-01T00:00:00+00:00' if kind=='DateTime' else False if kind=='Boolean' else 0


async def draft(row,body,check=lambda:None):
    if row['state']!='READY':raise ValueError('Wait for a completed inventory before creating a simulation')
    chosen=body.get('tags')
    if not isinstance(chosen,list) or not chosen or any(not isinstance(k,str) for k in chosen) or len(set(chosen))!=len(chosen):raise ValueError('Choose unique tag IDs from this inventory')
    byid={t['id']:t for t in row['tags']}
    if any(k not in byid for k in chosen):raise ValueError('Selection is not part of this inventory; browse again')
    if row['partial'] and body.get('accept_partial') is not True:raise ValueError('Review and acknowledge the incomplete inventory / skipped items')
    copy_values=body.get('copy_values',False);dynamic=body.get('dynamic',False)
    if type(copy_values) is not bool or type(dynamic) is not bool:raise ValueError('Snapshot and dynamic options must be true or false')
    endpoint=body.get('url','opc.tcp://127.0.0.1:15440');u=urlparse(endpoint)
    if u.hostname not in ('127.0.0.1','localhost','::1') or u.username or u.password or u.scheme!='opc.tcp' or not u.port or not 1024<=u.port<=65535:raise ValueError('Use a loopback OPC UA URL with an explicit port from 1024 to 65535')
    source=urlparse(row['config']['url'])
    if (source.port or 4840)==u.port and source.hostname in ('127.0.0.1','localhost','::1'):raise ValueError('Choose a different local port from the source server')
    name=body.get('name','Imported OPC UA simulation')
    if not isinstance(name,str) or not 1<=len(name.strip())<=120:raise ValueError('Simulation name must be 1..120 characters')
    selected=[byid[k] for k in chosen];values={};failures=[];client=None
    if copy_values:
        client=OPC(row['config'])
        try:
            async with asyncio.timeout(120):
                check();await client.connect();size=min(50,client.read_batch)
                for start in range(0,len(selected),size):
                    check();part=selected[start:start+size]
                    response=await client.client.read_attributes([client.client.get_node(t['nodeid']) for t in part],ua.AttributeIds.Value)
                    if len(response)!=len(part):raise ValueError('Incomplete OPC UA value response; retry the snapshot')
                    for t,v in zip(part,response):
                        try:
                            if not v.StatusCode.is_good() or not v.Value:raise ValueError(v.StatusCode.name)
                            values[t['id']]=view_value(cast(v.Value.Value,t['dtype']),t['dtype'])
                        except (ValueError,TypeError,OverflowError):failures.append({'tag':t['tag'],'reason':'Non-good quality or unsupported value; datatype default used'})
                    await asyncio.sleep(0)
        finally:await client.close()
    tags=[];mapping=[]
    for i,t in enumerate(selected,1):
        check();value=values.get(t['id'],defaults(t['dtype']));kind=t['dtype']
        settings={'pattern':'fixed_timestamp' if kind=='DateTime' else 'constant','value':value}
        if dynamic:
            settings=({'pattern':'toggle','value':bool(value)} if kind=='Boolean' else {'pattern':'current_timestamp'} if kind=='DateTime' else {'pattern':'constant','value':value} if kind=='String' else {'pattern':'sine','minimum':0,'maximum':100,'period':20})
        tags.append({'id':uid(),'tag':t['tag'],'dtype':kind,'index':i,'initial_value':value,'simulation':settings})
        mapping.append({'tag':t['tag'],'dtype':kind,'source_nodeid':t['nodeid'],'source_path':t['path'],'local_nodeid':ua.NodeId(t['tag'],2).to_string()})
    # Construct the destination from a whitelist. No source endpoint/credential fields survive.
    c={'id':uid(),'name':name.strip(),'protocol':'opcua','url':endpoint,'security':'None','timeout':15,'retry_seconds':5,'tags':tags}
    scenario=build_simulation({'name':name.strip(),'mode':'local','connection':c,'tag_source':'configured','interval':body.get('interval',2),'duration':body.get('duration',60),'simulation':{'pattern':'constant','value':0}})
    scenario['project']['jobs'][0]['dynamic']=dynamic
    scenario['description']+=' Imported application scalar definitions; local NodeIds remapped. No source connection is retained.'
    return {'scenario':scenario,'summary':{'selected':len(tags),'copied_values':len(values),'defaulted_values':len(tags)-len(values),'snapshot_requested':copy_values,'snapshot_failures':len(failures),'snapshot_issues':failures[:100],'partial_inventory':row['partial'],'source_issues':row['issues_count'],'dynamic':dynamic},'mapping':mapping}


def owner(request):return hashlib.sha256(request.cookies.get(COOKIE,'local-session').encode()).hexdigest() if request.app[ACCESS].enabled else 'local-session'


def get(request):
    row=request.app[KEY].get(request.match_info['id'])
    if not row or time.monotonic()-row['created']>TTL:raise ValueError('Import session expired; browse the server again')
    if row['owner']!=owner(request):raise web.HTTPForbidden(reason='Import belongs to another login session')
    return row


async def start(request):
    body=await request.json();config=source_connection(body.get('connection'));rows=request.app[KEY]
    for key in list(rows):
        r=rows[key]
        if time.monotonic()-r['created']>TTL:
            tasks=[t for t in (r['task'],r.get('draft_task')) if t and not t.done()]
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True);rows.pop(key,None)
    if sum(r['state']=='BROWSING' for r in rows.values())>=2:raise ValueError('Two source inventories are already browsing; cancel one first')
    if len(rows)>=4:
        key=next((k for k,r in rows.items() if r['state']!='BROWSING' and not (r.get('draft_task') and not r['draft_task'].done())),None)
        if key:rows.pop(key)
        else:raise ValueError('Import sessions are busy; close an import before browsing another source')
    row={'id':uid(),'state':'BROWSING','owner':owner(request),'created':time.monotonic(),'config':config,'tags':[],'_names':set(),'discovered':0,'browsed_nodes':0,'skipped':0,'partial':False,'issues_count':0,'issues':[],'error':''};rows[row['id']]=row
    store=request.app[ACCESS];cookie=request.cookies.get(COOKIE)
    def check():
        if row['state']=='CANCELLED' or time.monotonic()-row['created']>TTL or (store.enabled and not store.session(cookie,False)):raise asyncio.CancelledError()
    row['check']=check
    async def run():
        try:await inventory(config,row,check);row['state']='READY'
        except asyncio.CancelledError:row.update(state='CANCELLED',tags=[],config=None);raise
        except Exception:
            # Connection errors can include endpoint identities; keep raw credentials out of UI/logs.
            row.update(state='ERROR',tags=[],config=None,error='Source browse failed or timed out. Check endpoint, read permissions, security and certificates, then browse again.')
        finally:row.pop('_names',None)
    row['task']=asyncio.create_task(run());return web.json_response({'id':row['id']})


async def status(request):
    row=get(request);chosen=matches(row,request.query.get('query',''),request.query.get('folder',''));page=int(request.query.get('page',0))
    if page<0:raise ValueError('Invalid page')
    folders={}
    for t in row['tags']:
        for n in range(1,len(t['path'])):
            parts=t['path'][:n];key=json.dumps(parts,ensure_ascii=False);folders.setdefault(key,{'id':key,'label':' / '.join(parts),'count':0})['count']+=1
    return web.json_response({k:row[k] for k in ('id','state','discovered','browsed_nodes','skipped','partial','issues_count','issues','error')}|{'total':len(row['tags']),'matching':len(chosen),'rows':chosen[page*100:(page+1)*100],'folders':sorted(folders.values(),key=lambda f:f['label'].casefold()) if row['state']=='READY' else [],'page':page,'expires_seconds':max(0,int(TTL-(time.monotonic()-row['created'])))})


async def selection(request):
    row=get(request)
    if row['state']!='READY':raise ValueError('Wait for browsing to finish')
    b=await request.json();return web.json_response({'ids':[t['id'] for t in matches(row,b.get('query',''),b.get('folder',''))]})


async def compile_draft(request):
    row=get(request)
    if row.get('draft_task') and not row['draft_task'].done():raise ValueError('A snapshot review is already being built; wait or cancel it')
    row['draft_task']=asyncio.current_task()
    try:return web.json_response(await draft(row,await request.json(),row['check']))
    finally:row.pop('draft_task',None)


async def cancel(request):
    row=get(request)
    tasks=[t for t in (row['task'],row.get('draft_task')) if t and not t.done()]
    row['state']='CANCELLED'
    for task in tasks:task.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
    row.update(state='CANCELLED',tags=[],config=None);return web.json_response({'ok':True})


def install(app):
    app[KEY]={}
    app.add_routes([web.post('/api/opcua-import',start),web.get('/api/opcua-import/{id}',status),web.post('/api/opcua-import/{id}/selection',selection),web.post('/api/opcua-import/{id}/draft',compile_draft),web.post('/api/opcua-import/{id}/cancel',cancel)])
    janitor=None
    async def prune():
        while True:
            await asyncio.sleep(30)
            for key,r in list(app[KEY].items()):
                if time.monotonic()-r['created']>TTL:
                    tasks=[t for t in (r['task'],r.get('draft_task')) if t and not t.done()]
                    for task in tasks:task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True);r.update(config=None,tags=[]);app[KEY].pop(key,None)
    async def startup(app):
        nonlocal janitor
        janitor=asyncio.create_task(prune())
    app.on_startup.append(startup)
    async def close(app):
        if janitor:
            janitor.cancel();await asyncio.gather(janitor,return_exceptions=True)
        tasks=[t for r in app[KEY].values() for t in (r['task'],r.get('draft_task')) if t and not t.done()]
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True);app[KEY].clear()
    app.on_cleanup.append(close)
