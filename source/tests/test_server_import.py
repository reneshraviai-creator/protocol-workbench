import asyncio,copy,json,sys,time
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from asyncua import Server,ua
from model import default_project
from server_import import source_connection,inventory,draft,matches,KEY
from test_scenarios import port
from test_live_monitor import until
from drivers import OPC
from engine import Engine


def row(c):return {'state':'READY','config':c,'tags':[],'_names':set(),'discovered':0,'browsed_nodes':0,'skipped':0,'partial':False,'issues_count':0,'issues':[]}


async def source():
    srv=Server();await srv.init();c={'id':'source','name':'Source','protocol':'opcua','url':f'opc.tcp://127.0.0.1:{port()}','security':'None','tags':[]}
    srv.set_endpoint(c['url']);await srv.register_namespace('urn:source')
    a=await srv.nodes.objects.add_folder(2,'Line A');b=await srv.nodes.objects.add_folder(2,'Line B')
    n=await a.add_variable('ns=2;s=Temperature.A','Temperature',ua.Variant(12.5,ua.VariantType.Float))
    await b.add_variable('ns=2;s=Temperature.B','Temperature',ua.Variant(21,ua.VariantType.Int16))
    await a.add_variable('ns=2;s=Text','Label','pump')
    await a.add_variable('ns=2;s=Count','Count',ua.Variant(2**63+9,ua.VariantType.UInt64))
    array=await a.add_variable('ns=2;s=Array','Array',ua.Variant([1,2],ua.VariantType.Int32));await array.write_value_rank(1)
    structure=await a.add_variable('ns=2;s=Structure','UnrelatedVendorStructure',ua.Variant([1],ua.VariantType.Int32));await structure.write_value_rank(1)
    await structure.add_variable('ns=2;s=Nested','NestedLeaf',ua.Variant(True,ua.VariantType.Boolean))
    return srv,c,n


def test_inventory_and_snapshot_create_an_independent_real_simulator():
 async def run():
    srv,c,node=await source();r=row(source_connection(c));engine=None
    async with srv:
     await inventory(c,r)
     assert len(r['tags'])==5 and r['skipped']==2 and r['partial']
     assert any(t['tag']=='NestedLeaf' and len(t['path'])==3 for t in r['tags'])
     assert len({t['tag'] for t in r['tags']})==5
     assert len(matches(r,folder=json.dumps(['Line A'])))==4
     assert len(matches(r,'temperature'))==2
     body={'tags':[t['id'] for t in r['tags']],'url':f'opc.tcp://127.0.0.1:{port()}','name':'Independent copy','copy_values':True,'dynamic':False,'accept_partial':True,'duration':1}
     before=copy.deepcopy(c);built=await draft(r,body)
     assert built['summary']['copied_values']==5 and built['summary']['snapshot_failures']==0
     assert c==before and await node.read_value()==12.5
     destination=built['scenario']['project']['connections'][0]
     assert destination['url']!=c['url'] and destination['security']=='None'
     assert not any(k in destination for k in ('password','username','certificate','private_key','server_certificate'))
     assert all(t['nodeid'].startswith('ns=2;s=') for t in destination['tags'])
     assert next(t for t in destination['tags'] if t['tag']=='Count')['initial_value']==str(2**63+9)
     p=built['scenario']['project'];engine=Engine(p);job=p['jobs'][0]['id']
     try:
      await engine.start(job);await until(lambda:engine.states[job]['state']=='RUNNING')
      client=OPC(destination)
      try:
       await client.connect();await client.prepare(destination['tags']);values=await client.read(destination['tags'])
       for t in destination['tags']:
        expected=t['initial_value'];actual=values[t['id']]
        assert str(actual)==str(expected) or actual==expected
      finally:await client.close()
     finally:await engine.stop_all()
 asyncio.run(run())


def test_bad_quality_snapshot_defaults_and_dynamic_scalar_patterns():
 async def run():
    srv,c,n=await source();r=row(c)
    async with srv:
     await inventory(c,r)
     await n.write_attribute(ua.AttributeIds.Value,ua.DataValue(ua.Variant(9.,ua.VariantType.Float),ua.StatusCode(ua.StatusCodes.BadOutOfService)))
     result=await draft(r,{'tags':[t['id'] for t in r['tags']],'url':f'opc.tcp://127.0.0.1:{port()}','copy_values':True,'dynamic':True,'accept_partial':True})
     assert result['summary']['snapshot_failures']==1 and result['summary']['defaulted_values']==1
     tags=result['scenario']['project']['connections'][0]['tags']
     t=next(t for t in tags if t['tag']=='Temperature');assert t['initial_value']==0 and t['simulation']['pattern']=='sine'
     assert next(t for t in tags if t['dtype']=='Boolean')['simulation']['pattern']=='toggle'
 asyncio.run(run())


@pytest.mark.parametrize('change',[{'tags':[]},{'tags':['missing']},{'tags':['a','a']},{'accept_partial':False},{'url':'opc.tcp://10.0.0.1:4840'},{'url':'opc.tcp://127.0.0.1:80'},{'url':'opc.tcp://user:pass@localhost:4841'},{'url':'opc.tcp://127.0.0.1:4840'},{'copy_values':'yes'},{'dynamic':1},{'name':''},{'interval':0},{'duration':0}])
def test_invalid_draft_never_creates_a_scenario(change):
 async def run():
    c={'id':'source','name':'Source','protocol':'opcua','url':'opc.tcp://127.0.0.1:4840','tags':[]};r=row(c);r.update(partial=True,tags=[{'id':'a','tag':'A','dtype':'Float','nodeid':'ns=3;s=A','path':['A']}])
    with pytest.raises((ValueError,KeyError)):await draft(r,{'tags':['a'],'url':'opc.tcp://127.0.0.1:15440','accept_partial':True,**change})
 asyncio.run(run())


def test_limit_is_visible_and_cancellation_closes_source(monkeypatch):
 async def run():
    import server_import
    srv,c,n=await source();r=row(c)
    async with srv:
     monkeypatch.setattr(server_import,'MAX_TAGS',2);await inventory(c,r)
     assert len(r['tags'])==2 and r['partial'] and any(x['kind']=='limit' for x in r['issues'])
     def stop():raise asyncio.CancelledError()
     with pytest.raises(asyncio.CancelledError):await inventory(c,row(c),stop)
     assert await n.read_value()==12.5
 asyncio.run(run())


def test_source_validation_and_no_credential_copy():
 with pytest.raises(ValueError):source_connection({'protocol':'modbus'})
 with pytest.raises(ValueError):source_connection({'protocol':'opcua','url':'opc.tcp://user:secret@localhost:4840'})
 async def run():
    c={'id':'s','name':'Private','protocol':'opcua','url':'opc.tcp://private-source:4840','password':'private-secret','username':'private-user','certificate':'/private/cert','private_key':'/private/key','tags':[]};r=row(c);r['tags']=[{'id':'x','tag':'X','dtype':'String','nodeid':'ns=9;s=X','path':['X']}]
    result=await draft(r,{'tags':['x']});raw=json.dumps(result)
    assert result['mapping'][0]['source_nodeid']=='ns=9;s=X' # explicit mapping retains identity only
    dest=result['scenario']['project']['connections'][0];assert set(dest)=={'id','name','protocol','url','security','timeout','retry_seconds','tags'}
    assert all(s not in json.dumps(result['scenario']) for s in ('private-source','private-secret','private-user','/private/','ns=9'))
 asyncio.run(run())


def test_api_selection_pagination_cancel_save_and_workspace_isolation(tmp_path,monkeypatch):
 async def run():
    import server
    from aiohttp.test_utils import TestClient,TestServer
    srv,c,n=await source();many=await srv.nodes.objects.add_folder(2,'Many')
    for i in range(120):await many.add_variable(f'ns=2;s=Point{i}',f'Point{i}',ua.Variant(i,ua.VariantType.Int32))
    monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');original=default_project();app=server.create_app(original)
    async with srv,TestClient(TestServer(app)) as client:
     h={'X-Workbench-Token':(await (await client.get('/api/bootstrap')).json())['token']}
     assert (await client.post('/api/opcua-import',json={'connection':c})).status==403
     res=await client.post('/api/opcua-import',json={'connection':c},headers=h);assert res.status==200
     key=(await res.json())['id'];url='/api/opcua-import/'+key
     result={}
     async def poll():
      nonlocal result
      for _ in range(100):
       result=await (await client.get(url,headers=h)).json()
       if result['state']!='BROWSING':return
       await asyncio.sleep(.02)
      raise TimeoutError()
     await poll();assert result['state']=='READY' and result['total']==125 and len(result['rows'])==100
     second=await (await client.get(url+'?page=1',headers=h)).json();assert len(second['rows'])==25
     all_ids=await (await client.post(url+'/selection',json={},headers=h)).json();assert len(all_ids['ids'])==125
     assert 'config' not in result and not any(t.get('password') for t in result['rows'])
     filtered=await (await client.post(url+'/selection',json={'query':'Temperature'},headers=h)).json();assert len(filtered['ids'])==2
     built=await (await client.post(url+'/draft',json={'tags':filtered['ids'],'accept_partial':True,'url':f'opc.tcp://127.0.0.1:{port()}'},headers=h)).json()
     assert (await client.post('/api/scenarios',json=built['scenario'],headers=h)).status==200
     assert len(app['catalogue'].all('scenarios'))==1 and not app['runner'].active()
     assert app['engine'].project==original and not app['engine'].active() and not server.PROJECT.exists()
     assert (await client.post(url+'/cancel',json={},headers=h)).status==200
     assert (await (await client.get(url,headers=h)).json())['state']=='CANCELLED'
 asyncio.run(run())


def test_rbac_and_inventory_login_session_ownership(tmp_path,monkeypatch):
 from access_control import permitted
 for suffix in ('selection','draft','cancel'):
  path='/api/opcua-import/abc/'+suffix
  assert permitted('Engineer',path,'POST',{})
  assert not permitted('Operator',path,'POST',{}) and not permitted('Read-only',path,'POST',{})
 assert permitted('Engineer','/api/opcua-import','POST',{})
 async def run():
    import server
    from aiohttp.test_utils import TestClient,TestServer
    from access_control import KEY as ACCESS,COOKIE
    monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app()
    async with TestClient(TestServer(app)) as client:
     store=app[ACCESS];store.settings['enabled']=True
     now=store.clock();raw='fixture-session-one';owner=__import__('hashlib').sha256(raw.encode()).hexdigest()
     store.sessions[owner]={'name':'engineer','role':'Engineer','created':now,'last':now,'revoked':False,'licence_acknowledged':None}
     # Direct row access tests owner binding without weakening API login/licence gates.
     from server_import import get
     class Request(dict):
      app={ACCESS:store,KEY:{'known':{'created':time.monotonic(),'owner':owner}}};cookies={COOKIE:'another-session'};match_info={'id':'known'}
     from aiohttp import web
     with pytest.raises(web.HTTPForbidden):get(Request())
     Request.cookies={COOKIE:raw};assert get(Request())['owner']==owner
 asyncio.run(run())


def test_continuation_points_and_malformed_browse_response():
 async def run():
    from types import SimpleNamespace as N
    from server_import import references
    class Session:
     calls=0
     async def browse(self,p):return [N(StatusCode=ua.StatusCode(),ContinuationPoint=b'more',References=['first'])]
     async def browse_next(self,p):
      self.calls+=1;assert p.ContinuationPoints==[b'more'] and not p.ReleaseContinuationPoints
      return [N(StatusCode=ua.StatusCode(),ContinuationPoint=b'',References=['second'])]
    session=Session();client=N(client=N(nodes=N(objects=N(session=session))))
    results=[r.References async for _,r in references(client,[(ua.NodeId(85),[])],lambda:None)]
    assert results==[['first'],['second']] and session.calls==1
    with pytest.raises(ValueError,match='Incomplete'): [r async for r in references(client,[(ua.NodeId(85),[]),(ua.NodeId(86),[])],lambda:None)]
 asyncio.run(run())


def test_background_cancel_closes_session_and_keeps_workspace(tmp_path,monkeypatch):
 async def run():
    import server,server_import
    from aiohttp.test_utils import TestClient,TestServer
    entered=asyncio.Event();closed=asyncio.Event()
    class Slow:
     def __init__(self,c):pass
     async def connect(self):entered.set();await asyncio.Event().wait()
     async def close(self):closed.set()
    monkeypatch.setattr(server_import,'OPC',Slow);monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(default_project())
    async with TestClient(TestServer(app)) as client:
     h={'X-Workbench-Token':(await (await client.get('/api/bootstrap')).json())['token']}
     r=await (await client.post('/api/opcua-import',json={'connection':{'protocol':'opcua','url':'opc.tcp://127.0.0.1:4840'}},headers=h)).json();await asyncio.wait_for(entered.wait(),2)
     assert (await client.post('/api/opcua-import/'+r['id']+'/cancel',json={},headers=h)).status==200
     assert closed.is_set() and app[KEY][r['id']]['config'] is None and app[KEY][r['id']]['tags']==[]
     assert not app['engine'].active() and app['catalogue'].all('scenarios')==[]
 asyncio.run(run())


@pytest.mark.parametrize('dynamic',[False,True])
def test_all_supported_scalar_defaults_compile_without_source_access(dynamic):
 async def run():
    from model import TYPES
    c={'id':'s','name':'Source','protocol':'opcua','url':'opc.tcp://unused-source:4840','tags':[]};r=row(c)
    r['tags']=[{'id':str(i),'tag':kind,'dtype':kind,'nodeid':f'ns=8;s={kind}','path':[kind]} for i,kind in enumerate(sorted(TYPES))]
    built=await draft(r,{'tags':[t['id'] for t in r['tags']],'dynamic':dynamic})
    assert len(built['scenario']['project']['connections'][0]['tags'])==len(TYPES)
    assert built['summary']['copied_values']==0 and built['summary']['defaulted_values']==len(TYPES)
    date=next(t for t in built['scenario']['project']['connections'][0]['tags'] if t['dtype']=='DateTime')
    assert date['simulation']['pattern']==('current_timestamp' if dynamic else 'fixed_timestamp')
    p=built['scenario']['project'];p['connections'][0]['url']=f'opc.tcp://127.0.0.1:{port()}';e=Engine(p);j=p['jobs'][0]['id'];client=None
    try:
     await e.start(j);await until(lambda:e.states[j]['state'] in ('RUNNING','ERROR'));assert e.states[j]['state']=='RUNNING'
     c=p['connections'][0];client=OPC(c);await client.connect();await client.prepare(c['tags']);values=await client.read(c['tags']);assert len(values)==len(TYPES)
     if not dynamic:
      from model import view_value,cast
      for t in c['tags']:assert view_value(values[t['id']],t['dtype'])==view_value(cast(t['initial_value'],t['dtype']),t['dtype'])
    finally:
     if client:await client.close()
     await e.stop_all()
 asyncio.run(run())
