import asyncio,copy,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from scenarios import Catalogue,Runner,validate_scenario
from scenario_automation import fingerprint,compare_runs
from test_scenarios import scenario,port

@pytest.mark.parametrize('change',[
 lambda s:s['steps'][0].update(when={'step':1,'state':'PASSED'}),
 lambda s:s['steps'][1].update(when={'step':True,'state':'PASSED'}),
 lambda s:s['steps'][1].update(when={'step':1,'state':'RUNNING'}),
 lambda s:s['steps'][1].update(when={'step':1,'state':'PASSED','script':'x'}),
 lambda s:s['steps'][1].update(retry={'attempts':2}),
 lambda s:s['steps'][0].update(retry={'attempts':2}),
 lambda s:s['steps'][2].update(retry={'attempts':0}),
 lambda s:s['steps'][2].update(retry={'attempts':6}),
 lambda s:s['steps'][2].update(retry={'attempts':True}),
 lambda s:s['steps'][2].update(retry={'delay':float('nan')}),
 lambda s:s['steps'][2].update(retry={'delay':31}),
 lambda s:s['steps'][2].update(retry={'replay':True}),
 lambda s:s['steps'][2].update(continue_on_failure='yes'),
 lambda s:s['steps'].append({'action':'check','check':'shell','source_step':3}),
 lambda s:s['steps'].append({'action':'check','check':'datatype','source_step':2,'expected':'Float'}),
 lambda s:s['steps'].append({'action':'check','check':'datatype','source_step':3,'expected':'REAL'}),
 lambda s:s['steps'].append({'action':'check','check':'status','source_step':3,'expected':''}),
 lambda s:s['steps'].append({'action':'check','check':'response','source_step':1,'expected':'SUCCEEDED'}),
 lambda s:s['steps'].append({'action':'check','check':'response','source_step':3,'expected':'Good'}),
 lambda s:s['steps'].append({'action':'check','check':'duration','source_step':5,'maximum':5}),
 lambda s:s['steps'].append({'action':'check','check':'duration','source_step':3,'minimum':-1,'maximum':5}),
 lambda s:s['steps'].append({'action':'check','check':'duration','source_step':3,'maximum':float('inf')}),
])
def test_invalid_automation_is_rejected(change):
 s=scenario();change(s)
 with pytest.raises(ValueError):validate_scenario(s)

def test_versions_durable_noop_and_fingerprints(tmp_path):
 store=Catalogue(tmp_path/'c.db');s=scenario();store.put('scenarios',s);first=store.revision(s)
 s['updated']=123;store.put('scenarios',s);assert len(store.versions('test'))==1
 old=copy.deepcopy(s);s['name']='Changed';store.put('scenarios',s)
 assert len(store.versions('test'))==2 and store.historical('test',1)['name']==old['name']
 assert store.revision(s)['revision']==2 and store.revision(s)['fingerprint']!=first['fingerprint']
 assert len(Catalogue(store.path).versions('test'))==2
 supplied=copy.deepcopy(s);supplied.update(revision=999,fingerprint='untrusted');store.put('scenarios',supplied);assert 'revision' not in store.get('scenarios','test') and len(store.versions('test'))==2
 s.update(id='new',revision=999,fingerprint='untrusted');assert fingerprint(s)==store.revision(store.get('scenarios','test'))['fingerprint']
 with pytest.raises(ValueError):store.historical('test',99)

def run_scenario(tmp_path,s):
 async def run():
  store=Catalogue(tmp_path/'c.db');store.put('scenarios',validate_scenario(s));r=Runner(store);key=await r.start(s['id']);await r.task
  assert not r.registry.snapshot();return store.get('runs',key)
 return asyncio.run(run())

@pytest.mark.parametrize('protocol',['modbus','opcua'])
def test_real_evidence_checks_and_template_roundtrip(tmp_path,protocol):
 from templates import portable,instantiate
 s=scenario(port());c=s['project']['connections'][0]
 if protocol=='opcua':c.update(protocol='opcua',url=f"opc.tcp://127.0.0.1:{c['port']}",security='None');c['tags'][0].update(dtype='Double',nodeid='ns=2;s=Temperature')
 dtype=c['tags'][0]['dtype'];quality='Good' if protocol=='opcua' else 'TransportGood; process quality unavailable'
 s['steps'] += [{'action':'check','check':kind,'source_step':ref,'expected':value,'when':{'step':3,'state':'PASSED'},'retry':{'attempts':2,'delay':0}} for kind,ref,value in [('datatype',3,dtype),('status',3,quality),('response',2,'SUCCEEDED')]]
 s['steps'].append({'action':'check','check':'duration','source_step':3,'minimum':0,'maximum':5})
 document=portable(s);imported=instantiate(document,{'local':c['url'] if protocol=='opcua' else c['host']});assert imported['steps']==s['steps']
 r=run_scenario(tmp_path,s);assert r['state']=='PASSED',r
 assert r['fingerprint']==fingerprint(s) and r['revision']==1 and r['environment']['asyncua']
 assert len(r['logs'])==8 and r['steps'][1]['transactions'][0]['state']=='SUCCEEDED'
 read=r['steps'][2];assert read['tags'][0]['observed_dtype']==dtype and read['tags'][0]['quality']==quality
 assert r['steps'][4]['observed']==[dtype] and r['steps'][-1]['observed']>=0

@pytest.mark.parametrize('kind,expected',[('datatype','Double'),('status','Good'),('response','FAILED'),('duration',None)])
def test_negative_check_fails_with_actual_evidence(tmp_path,kind,expected):
 s=scenario(port());s['steps'].append({'action':'check','check':kind,'source_step':3,'expected':expected,'minimum':0,'maximum':0})
 r=run_scenario(tmp_path,s);assert r['state']=='FAILED' and r['steps'][-1]['state']=='FAILED' and 'observed' in r['steps'][-1]

def test_conditions_failures_retries_and_final_evaluation(tmp_path):
 s=scenario(port());s['steps'][3].update(expected=99,continue_on_failure=True,retry={'attempts':3,'delay':0})
 s['steps'] += [{'action':'wait','seconds':0,'when':{'step':4,'state':'PASSED'}},{'action':'wait','seconds':0,'when':{'step':4,'state':'FAILED'}},{'action':'wait','seconds':0,'when':{'step':5,'state':'SKIPPED'}}]
 r=run_scenario(tmp_path,s);assert r['state']=='FAILED' and r['skipped']==1
 assert [x['state'] for x in r['steps'][-3:]]==['SKIPPED','PASSED','PASSED']
 assert len(r['steps'][3]['attempts'])==3 and all(a['state']=='FAILED' for a in r['steps'][3]['attempts'])
 assert r['steps'][4]['reason'] and r['logs'][3]['attempts']==3

def test_read_retry_recovers_with_attempt_evidence(tmp_path,monkeypatch):
 async def run():
  s=scenario();s['steps']=[{'action':'read','connection':'local','tag':'temp','variable':'v','retry':{'attempts':3,'delay':0}}];store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);runner=Runner(store);calls=[]
  async def step(step,p,e,v,result):
   calls.append(1);result['observed']=len(calls)
   if len(calls)<3:raise ConnectionError('offline')
  monkeypatch.setattr(runner,'step',step);key=await runner.start('test');await runner.task;r=store.get('runs',key)
  assert r['state']=='PASSED' and [x['state'] for x in r['steps'][0]['attempts']]==['ERROR','ERROR','PASSED']
  assert 'error' not in r['steps'][0] and len(calls)==3
 asyncio.run(run())

def test_cancel_retry_delay_does_not_dispatch_another_attempt(tmp_path,monkeypatch):
 async def run():
  s=scenario();s['steps']=[{'action':'read','connection':'local','tag':'temp','variable':'v','retry':{'attempts':5,'delay':1}}];store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);runner=Runner(store);gate=asyncio.Event();calls=[]
  async def step(*args):calls.append(1);gate.set();raise ConnectionError('offline')
  monkeypatch.setattr(runner,'step',step);key=await runner.start('test');await gate.wait();await asyncio.sleep(0);await runner.stop(key);r=store.get('runs',key)
  assert r['state']=='CANCELLED' and r['steps'][0]['state']=='CANCELLED' and len(calls)==1 and not runner.registry.snapshot()
 asyncio.run(run())

def test_timeout_bounds_all_retry_attempts(tmp_path,monkeypatch):
 async def run():
  s=scenario();s['steps']=[{'action':'read','connection':'local','tag':'temp','variable':'v','timeout':.1,'retry':{'attempts':5,'delay':1}}];store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);runner=Runner(store);calls=[]
  async def step(*args):calls.append(1);raise ConnectionError('offline')
  monkeypatch.setattr(runner,'step',step);key=await runner.start('test');await runner.task;r=store.get('runs',key)
  assert r['state']=='ERROR' and 'TimeoutError' in r['error'] and len(calls)==1 and r['steps'][0]['duration']<1
 asyncio.run(run())

def test_run_comparison_and_immutable_revision(tmp_path):
 async def run():
  store=Catalogue(tmp_path/'c.db');s=scenario();s['steps']=[{'action':'wait','seconds':0}];store.put('scenarios',s);runner=Runner(store)
  a=await runner.start('test');await runner.task;b=await runner.start('test');await runner.task
  comparison=compare_runs(store.get('runs',a),store.get('runs',b));assert comparison['same_definition'] and comparison['changed_steps']==0 and comparison['steps'][0]['duration_delta'] is not None
  s['steps'][0]['label']='Changed';store.put('scenarios',s);c=await runner.start('test');await runner.task
  comparison=compare_runs(store.get('runs',a),store.get('runs',c));assert not comparison['same_definition'] and comparison['right_revision']==2 and comparison['changed_steps']==1
  assert store.get('runs',a)['snapshot']['steps'][0].get('label') is None
  r=store.get('runs',a);r['state']='RUNNING'
  with pytest.raises(ValueError):compare_runs(r,store.get('runs',b))
 asyncio.run(run())

def test_automation_api_auth_versions_comparison_and_safe_exports(tmp_path):
 async def run():
  from aiohttp.test_utils import TestClient,TestServer
  from model import default_project
  import server
  server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';app=server.create_app(default_project())
  async with TestClient(TestServer(app)) as client:
   h={'X-Workbench-Token':(await (await client.get('/api/bootstrap')).json())['token']};s=scenario();s['project']['connections'][0]['password']='DO-NOT-EXPORT';s['steps']=[{'action':'wait','seconds':0}]
   await client.post('/api/scenarios',json=s,headers=h)
   assert (await client.get('/api/scenarios/test/versions')).status==403
   rows=await (await client.get('/api/scenarios/test/versions',headers=h)).json();assert rows[0]['revision']==1
   assert (await client.get('/api/scenarios/test/versions/1',headers=h)).status==200
   ids=[]
   for _ in range(2):
    response=await client.post('/api/scenarios/run',json={'id':'test'},headers=h);ids.append((await response.json())['id']);await app['runner'].task
   summary=await (await client.get('/api/runs',headers=h)).json();assert 'attempts' not in summary[0]['steps'][0] and 'logs' not in summary[0]
   r=await client.get('/api/run-comparison?left='+ids[0]+'&right='+ids[1],headers=h);assert r.status==200 and (await r.json())['same_definition']
   for suffix in ('evidence','report'):
    text=await (await client.get('/api/runs/'+ids[0]+'/'+suffix,headers=h)).text();assert 'DO-NOT-EXPORT' not in text and 'fingerprint' in text
 asyncio.run(run())

def test_bad_ua_quality_evidence_can_be_checked_without_faking_good(tmp_path):
 async def run():
  from asyncua import Server,ua
  p=port();server=Server();await server.init();server.set_endpoint(f'opc.tcp://127.0.0.1:{p}');ns=await server.register_namespace('urn:phase7');node=await server.nodes.objects.add_variable(f'ns={ns};s=Temperature','Temperature',0.)
  await node.write_attribute(ua.AttributeIds.Value,ua.DataValue(ua.Variant(5.,ua.VariantType.Double),StatusCode=ua.StatusCode(ua.StatusCodes.BadSensorFailure)))
  s=scenario();c=s['project']['connections'][0];c.update(protocol='opcua',url=f'opc.tcp://127.0.0.1:{p}');c['tags'][0].update(dtype='Double',nodeid=f'ns={ns};s=Temperature');s['project']['jobs']=[]
  s['steps']=[{'action':'read','connection':'local','tag':'temp','variable':'v','continue_on_failure':True},{'action':'check','check':'status','source_step':1,'expected':'BadSensorFailure'},{'action':'check','check':'response','source_step':1,'expected':'FAILED'}]
  store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);runner=Runner(store)
  async with server:
   key=await runner.start('test');await runner.task
  r=store.get('runs',key);assert r['state']=='ERROR' and r['steps'][1]['state']=='PASSED' and r['steps'][2]['state']=='PASSED',r
  assert r['steps'][0]['tags'][0]['quality']=='BadSensorFailure'
 asyncio.run(run())

def test_parallel_runs_allow_independent_and_reject_conflicting_writers(tmp_path):
 async def run():
  store=Catalogue(tmp_path/'c.db');s=scenario(port());s['steps'].insert(1,{'action':'wait','seconds':10,'timeout':15});store.put('scenarios',s);runner=Runner(store);first=await runner.start('test')
  with pytest.raises(ValueError,match='Resource conflict'):await runner.start('test')
  other=copy.deepcopy(s);other['id']='other';other['project']['connections'][0]['port']=port();store.put('scenarios',other);second=await runner.start('other');assert first!=second
  await runner.stop();assert not runner.active() and not runner.registry.snapshot()
 asyncio.run(run())

def test_reserved_internal_name_does_not_break_evidence_checks(tmp_path):
 s=scenario(port());s['steps'][2]['variable']='__steps__';s['steps'][3]['variable']='__steps__';s['steps'].append({'action':'check','check':'response','source_step':3,'expected':'SUCCEEDED'})
 assert run_scenario(tmp_path,s)['state']=='PASSED'

def test_active_edit_new_revision_leaves_original_run_immutable(tmp_path):
 async def run():
  store=Catalogue(tmp_path/'c.db');s=scenario();s['steps']=[{'action':'wait','seconds':10,'timeout':15}];store.put('scenarios',s);runner=Runner(store);key=await runner.start('test');before=store.get('runs',key)
  s['steps'][0]['seconds']=0;store.put('scenarios',s);assert store.revision(s)['revision']==2;await runner.stop(key);after=store.get('runs',key)
  assert before['snapshot']==after['snapshot'] and after['revision']==1 and after['fingerprint']!=fingerprint(s)
 asyncio.run(run())

@pytest.mark.parametrize('expected,passed',[('2026-10-20T00:00:00Z',True),('2026-10-20T08:00:00+08:00',True),('2026-10-20T00:00:01Z',False)])
def test_datetime_expected_values_compare_instants(expected,passed):
 from datetime import datetime,timezone
 from scenarios import comparison
 assert comparison(datetime(2026,10,20,tzinfo=timezone.utc),{'operator':'equals','expected':expected}) is passed

def test_real_ua_datetime_expected_value_assertion(tmp_path):
 async def run():
  from asyncua import Server
  from datetime import datetime,timezone
  p=port();server=Server();await server.init();server.set_endpoint(f'opc.tcp://127.0.0.1:{p}');ns=await server.register_namespace('urn:phase7:date');await server.nodes.objects.add_variable(f'ns={ns};s=Temperature','Temperature',datetime(2026,10,20,tzinfo=timezone.utc))
  s=scenario();c=s['project']['connections'][0];c.update(protocol='opcua',url=f'opc.tcp://127.0.0.1:{p}');c['tags'][0].update(dtype='DateTime',nodeid=f'ns={ns};s=Temperature');s['project']['jobs']=[];s['steps']=[{'action':'read','connection':'local','tag':'temp','variable':'v'},{'action':'assert','variable':'v','operator':'equals','expected':'2026-10-20T08:00:00+08:00'},{'action':'check','check':'datatype','source_step':1,'expected':'DateTime'}]
  store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);runner=Runner(store)
  async with server:
   key=await runner.start('test');await runner.task
  r=store.get('runs',key);assert r['state']=='PASSED',r
 asyncio.run(run())
