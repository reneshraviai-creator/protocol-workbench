import asyncio,copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from architecture import device_draft,scanner_draft,visual
from test_simulation_wizard import spec,rtu_spec
from model import validate_project

@pytest.mark.parametrize('protocol',['modbus','opcua'])
@pytest.mark.parametrize('mode',['continuous','timed'])
def test_architecture_device_draft_is_pure_and_stopped(protocol,mode):
 s=spec(protocol);s.update(run_mode=mode,visual={'type':'Bioreactor','zone':'Suite A'});original=copy.deepcopy(s)
 p=device_draft(s);assert s==original and p['settings']['autostart'] is False
 assert p['connections'][0]['visual']==s['visual']
 assert (p['jobs'][0].get('duration_seconds')==s['duration']) if mode=='timed' else 'duration_seconds' not in p['jobs'][0]
 validate_project(p)

@pytest.mark.parametrize('protocol',['modbus','opcua'])
def test_scanner_copies_exact_definitions_without_mutating_source(protocol):
 c=device_draft(spec(protocol))['connections'][0];before=copy.deepcopy(c);new=scanner_draft(c)
 assert c==before and new['id']!=c['id']
 assert new['visual']['source_id']==c['id']
 assert [t['id'] for t in new['tags']]!=[t['id'] for t in c['tags']]
 assert [{k:v for k,v in t.items() if k!='id'} for t in new['tags']]==[{k:v for k,v in t.items() if k!='id'} for t in c['tags']]


def test_rtu_scanner_requires_a_separate_port():
 c=device_draft(rtu_spec())['connections'][0]
 for port in (None,'/dev/virtual'):
  with pytest.raises(ValueError,match='different'):scanner_draft(c,port)
 assert scanner_draft(c,'/dev/peer')['serial_port']=='/dev/peer'

@pytest.mark.parametrize('value',[{'type':'unsupported'},{'type':'PLC','zone':'x'*81},'PLC'])
def test_invalid_appearance_rejected(value):
 with pytest.raises(ValueError):visual(value)

@pytest.mark.parametrize('protocol',['modbus','opcua'])
def test_real_simulator_to_copied_scanner_readback_and_cleanup(protocol):
 from engine import Engine
 from drivers import adapter
 from test_live_monitor import until
 async def run():
  p=device_draft(spec(protocol));engine=Engine(p);key=p['jobs'][0]['id'];scanner=scanner_draft(p['connections'][0]);client=adapter(scanner)
  try:
   await engine.start(key);await until(lambda:engine.states[key]['state']=='RUNNING')
   await client.connect();await client.prepare(scanner['tags'],write=False)
   values=await client.read(scanner['tags']);assert len(values)==20 and all(v==23.5 for v in values.values())
  finally:await client.close();await engine.stop_all()
  assert not engine.active()
 asyncio.run(run())


def test_draft_api_does_not_save_or_run(tmp_path,monkeypatch):
 from aiohttp.test_utils import TestClient,TestServer
 import server
 from model import default_project
 async def run():
  monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json')
  async with TestClient(TestServer(server.create_app(default_project()))) as client:
   token=(await (await client.get('/api/bootstrap')).json())['token'];h={'X-Workbench-Token':token};s=spec();s['workspace_device']=True
   assert (await client.post('/api/simulation-draft',json=s)).status==403
   r=await client.post('/api/simulation-draft',json=s,headers=h);assert r.status==200
   c=(await r.json())['project']['connections'][0]
   r=await client.post('/api/simulation-draft',json={'scanner_draft':True,'connection':c},headers=h);assert r.status==200
   assert (await (await client.get('/api/project',headers=h)).json())['connections']==[]
   assert not client.server.app['engine'].active()
   assert (await (await client.get('/api/status',headers=h)).json())['scenario_devices']==[]
 asyncio.run(run())


def test_running_scenario_inventory_uses_snapshot_without_secrets():
 from architecture import scenario_devices
 class Task:
  def done(self):return False
 class Runner:tasks={'run1':Task()}
 class Store:
  def get(self,*args):return {'name':'Active test','state':'RUNNING','snapshot':{'project':{'connections':[{'id':'c','name':'PLC','protocol':'opcua','url':'opc.tcp://secret:password@127.0.0.1:4840','password':'private','tags':[{'id':'t'}]}]}}}
 rows=scenario_devices({'runner':Runner(),'catalogue':Store()})
 assert rows[0]['endpoint']=='opc.tcp://127.0.0.1:4840'
 assert 'secret' not in str(rows) and 'password' not in str(rows) and rows[0]['points']==1


def test_saved_presentation_views_are_configuration_only():
 from architecture import validate_presentation_views
 from model import default_project
 view={'id':'view1','name':'Customer A','description':'Readback','devices':['source','scanner']}
 assert validate_presentation_views([view])==[view]
 p=default_project();p['settings']['presentation_views']=[view];validate_project(p)
 # Stale IDs remain visible as missing, never silently expanded to all devices.
 assert p['connections']==[] and p['settings']['autostart'] is False

@pytest.mark.parametrize('views',[{},[{'id':'bad space','name':'A','devices':['a']}],[{'id':'v','name':'','devices':['a']}],[{'id':'v','name':'A','devices':[]}],[{'id':'v','name':'A','devices':['a','a']}],[{'id':'v','name':'A','devices':['<script>']}],[{'id':'v','name':'A','devices':['a'],'description':'x'*401}]])
def test_invalid_saved_presentation_views_are_rejected(views):
 from architecture import validate_presentation_views
 with pytest.raises(ValueError):validate_presentation_views(views)
