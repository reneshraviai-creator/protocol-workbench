import asyncio,copy,csv,io,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from signal_path import demo,definition,execute,build,equal
from scenarios import Catalogue,Runner,validate_scenario
from resources import ResourceRegistry,scenario_plan,job_plan
from test_scenarios import port
from test_live_monitor import until
from engine import Engine
from drivers import OPC


def fixture():
 s=demo();a,b=s['project']['connections'];a['url']=f'opc.tcp://127.0.0.1:{port()}';b['port']=port();return s


def test_local_scaled_path_and_cleanup(tmp_path):
 async def run():
  s=fixture();store=Catalogue(tmp_path/'runs.db');s['id']='path';store.put('scenarios',s);registry=ResourceRegistry();runner=Runner(store,registry);key=await runner.start(s['id']);await runner.tasks[key];r=store.get('runs',key)
  assert r['state']=='PASSED',r
  result=r['steps'][-1];assert result['written']==23.5 and result['observed']==57
  assert result['path']['order']=='CDAB' and result['raw_words']==result['path']['expected_words']
  assert 0<=result['observed_delay_ms']<=3000 and result['write_outcome']=='ACKNOWLEDGED'
  assert result['baseline_source']==0 and result['baseline_destination']==10 and not registry.owners
 asyncio.run(run())

@pytest.mark.parametrize('change',[
 lambda s:s['steps'][-1].update(max_delay=0),lambda s:s['steps'][-1].update(max_delay=float('nan')),
 lambda s:s['steps'][-1].update(poll_seconds=10,max_delay=1),lambda s:s['steps'][-1].update(value='NaN'),
 lambda s:s['steps'][-1].update(source_tag='missing'),lambda s:s['steps'][-1].update(source_connection='path-target'),
 lambda s:s['steps'][-1].update(tolerance=-1),lambda s:s['steps'][-1].update(max_delay=31,timeout=30),
 lambda s:s['project']['jobs'][0].update(dynamic=True),
])
def test_invalid_definitions(change):
 s=fixture();change(s)
 with pytest.raises(ValueError):validate_scenario(s)


def test_exact_large_integer_comparison():
 assert not equal(9007199254740992,9007199254740993,0)
 assert equal(True,1,0)

@pytest.mark.parametrize('already',['source','target'])
def test_already_present_probe_is_rejected_without_writing(already):
 async def run():
  s=fixture();p=s['project'];p['jobs']=p['jobs'][:2];p['jobs'][0]['simulation']['value']=23.5 if already=='source' else 0;p['jobs'][1]['simulation']['value']=57 if already=='target' else 0;p['connections'][1]['tags'][0]['initial_value']=57 if already=='target' else 0;e=Engine(p);writer=OPC(p['connections'][0]);result={}
  try:
   for j in p['jobs']:await e.start(j['id']);await until(lambda:e.states[j['id']]['state']=='RUNNING')
   with pytest.raises(ValueError,match='already present'):await execute(s['steps'][-1],p,result)
   assert result['write_outcome']=='NOT_ATTEMPTED'
   await writer.connect();await writer.prepare(p['connections'][0]['tags']);assert (await writer.read(p['connections'][0]['tags']))['probe']==(23.5 if already=='source' else 0)
  finally:await writer.close();await e.stop_all()
 asyncio.run(run())

@pytest.mark.parametrize('wrong_order',[False,True])
def test_missing_path_or_wrong_word_order_fails_and_retains_probe(wrong_order):
 async def run():
  s=fixture();p=s['project'];e=Engine(p);result={};observer=copy.deepcopy(p);step=s['steps'][-1];step.update(max_delay=.3,poll_seconds=.05)
  try:
   for j in p['jobs'][:(3 if wrong_order else 2)]:await e.start(j['id']);await until(lambda:e.states[j['id']]['state']=='RUNNING')
   if wrong_order:observer['connections'][1]['tags'][0]['order']='ABCD'
   with pytest.raises(AssertionError,match='No matching observation'):await execute(step,observer,result)
   assert result['write_outcome']=='ACKNOWLEDGED' and result['tags'][1]['state']=='FAILED' and result['samples']
   c=OPC(p['connections'][0])
   try:await c.connect();await c.prepare(p['connections'][0]['tags']);assert (await c.read(p['connections'][0]['tags']))['probe']==23.5
   finally:await c.close()
  finally:await e.stop_all()
 asyncio.run(run())


@pytest.mark.parametrize('outcome',['cancel','deadline'])
def test_cancel_during_write_reports_unknown_and_releases_ownership(tmp_path,monkeypatch,outcome):
 async def run():
  import signal_path
  entered=asyncio.Event()
  class Fake:
   def __init__(self,c):self.c=c
   async def connect(self):pass
   async def prepare(self,*args,**kwargs):pass
   async def read(self,tags):return {tags[0]['id']:0 if self.c['protocol']=='opcua' else 10}
   async def write(self,*args):entered.set();await asyncio.Event().wait()
   async def close(self):pass
  monkeypatch.setattr(signal_path,'adapter',Fake);s=fixture();s['steps']=[s['steps'][-1]];s['project']['jobs']=[];s['id']='cancel';store=Catalogue(tmp_path/'runs.db');store.put('scenarios',s);registry=ResourceRegistry();runner=Runner(store,registry)
  if outcome=='deadline':s['steps'][0].update(max_delay=.1,poll_seconds=.05);store.put('scenarios',s)
  key=await runner.start('cancel');await asyncio.wait_for(entered.wait(),2)
  if outcome=='cancel':await runner.stop(key)
  else:await runner.tasks[key]
  r=store.get('runs',key);assert r['state']==('CANCELLED' if outcome=='cancel' else 'FAILED') and r['steps'][0]['write_outcome']=='UNKNOWN' and not registry.owners
 asyncio.run(run())


def test_source_write_resources_and_template_roundtrip():
 from templates import portable,instantiate
 s=fixture();registry=ResourceRegistry();registry.acquire('path','Path test',scenario_plan(s),'run')
 with pytest.raises(ValueError,match='Path test'):registry.acquire('other','Other writer',job_plan(s['project'],{'kind':'simulation','connection':'path-source'}),'job')
 t=portable(s);recreated=instantiate(t,{'path-source':s['project']['connections'][0]['url'],'path-target':'127.0.0.1'})
 assert recreated['steps'][-1]==s['steps'][-1]


def test_api_draft_is_offline_and_reports_include_path_evidence(tmp_path):
 async def run():
  import server
  from aiohttp.test_utils import TestClient,TestServer
  s=fixture();server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';p=s['project'];app=server.create_app(p)
  async with TestClient(TestServer(app)) as client:
   b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
   assert (await client.post('/api/signal-path/demo',json={})).status==403
   response=await client.post('/api/signal-path/draft',json={**s['steps'][-1],'name':'Offline definition'},headers=h);assert response.status==200
   draft=await response.json();assert draft['preview']['expected']==57 and draft['scenario']['project']['jobs']==[]
   assert app['engine'].project==p and not app['runner'].active() and not app['engine'].active() and not server.PROJECT.exists()
   saved=await (await client.post('/api/scenarios',json=s,headers=h)).json();key=await app['runner'].start(saved['id']);await app['runner'].tasks[key]
   html=await (await client.get('/api/runs/'+key+'/report',headers=h)).text();assert 'Observed delay (ms)' in html and 'CDAB' in html and 'Signal-path verification' in html
   rows=list(csv.DictReader(io.StringIO(await (await client.get('/api/runs/'+key+'/csv',headers=h)).text())));assert len(rows)==2 and rows[0]['write_outcome']=='ACKNOWLEDGED' and float(rows[1]['observed_delay_ms'])>=0
 asyncio.run(run())


def test_bad_source_quality_blocks_write():
 async def run():
  from asyncua import Server,ua
  s=fixture();p=s['project'];source=p['connections'][0];srv=Server();await srv.init();srv.set_endpoint(source['url']);await srv.register_namespace('urn:path-quality');node=await srv.nodes.objects.add_variable(ua.NodeId.from_string(source['tags'][0]['nodeid']),'Probe',ua.Variant(0.,ua.VariantType.Float));await node.set_writable()
  p2=copy.deepcopy(p);p2['connections']=[p['connections'][1]];p2['jobs']=[p['jobs'][1]];e=Engine(p2);result={}
  async with srv:
   try:
    await node.write_attribute(ua.AttributeIds.Value,ua.DataValue(ua.Variant(0.,ua.VariantType.Float),ua.StatusCode(ua.StatusCodes.BadOutOfService)))
    await e.start('path-mb-server');await until(lambda:e.states['path-mb-server']['state']=='RUNNING')
    with pytest.raises(ValueError,match='quality'):await execute(s['steps'][-1],p,result)
    assert result['write_outcome']=='NOT_ATTEMPTED' and (await node.read_data_value(raise_on_bad_status=False)).StatusCode.is_bad()
   finally:await e.stop_all()
 asyncio.run(run())
