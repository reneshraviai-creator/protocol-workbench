import asyncio,copy,socket,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from simulation_wizard import build_simulation
from model import generated

def spec(protocol='modbus'):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    return {'name':'Wizard simulation','mode':'local','connection':{'id':'c','name':'Wizard device','protocol':protocol,'host':'127.0.0.1','port':port,'device_id':1,'url':f'opc.tcp://127.0.0.1:{port}','security':'None','tags':[]},'tag_source':'generate','tag_spec':{'count':20,'prefix':'Point_','dtype':'Float','stride':2},'interval':.2,'duration':1,'simulation':{'pattern':'constant','value':23.5,'minimum':0,'maximum':100,'period':20}}

@pytest.mark.parametrize('protocol',['modbus','opcua'])
def test_compile_is_pure(protocol):
    source=spec(protocol);original=copy.deepcopy(source);result=build_simulation(source)
    assert source==original
    assert len(result['project']['connections'][0]['tags'])==20
    assert result['project']['jobs'][0]['kind']=='server'
    assert result['steps'][-1]['scope']=='all'
    assert result['project']['settings']['autostart'] is False

@pytest.mark.parametrize('protocol',['modbus','opcua'])
def test_local_wizard_scenario_runs(tmp_path,protocol):
    from scenarios import Catalogue,Runner
    async def run():
        store=Catalogue(tmp_path/'wizard.db');s=build_simulation(spec(protocol));s['id']='wizard';store.put('scenarios',s);runner=Runner(store);key=await runner.start('wizard');await runner.task
        r=store.get('runs',key);assert r['state']=='PASSED',r.get('error');assert r['steps'][-1]['passed_tags']==20
    asyncio.run(run())

def test_long_duration_is_bounded_steps():
    s=spec();s['duration']=3600;r=build_simulation(s)
    assert sum(x.get('seconds',0) for x in r['steps'])==3600
    assert max(x.get('seconds',0) for x in r['steps'])==240

@pytest.mark.parametrize('change',[
 lambda s:s.update(duration=0),lambda s:s.update(duration=3601),lambda s:s.update(duration=1.5),
 lambda s:s.update(interval=0),lambda s:s.update(interval=61),lambda s:s.update(name=''),
 lambda s:s['connection'].update(host='10.1.1.1'),lambda s:s.update(tag_source='configured'),
 lambda s:s.update(mode='wrong'),lambda s:s['tag_spec'].update(count=0),
])
def test_invalid(change):
    s=spec();change(s)
    with pytest.raises((ValueError,KeyError)):build_simulation(s)

def test_existing_reuses_and_preserves_settings():
    s=spec('opcua');s.update(mode='existing',tag_source='configured')
    s['connection'].update(url='opc.tcp://192.0.2.1:4840',security='Basic256Sha256,Sign',username='test',password='test-secret',certificate='client.der',private_key='client.key',server_certificate='server.der',tags=[{'id':'original','tag':'Valve','nodeid':'ns=4;i=24','dtype':'Boolean','simulation':{'pattern':'constant','value':True}}])
    result=build_simulation(s);c=result['project']['connections'][0]
    assert c['security']=='Basic256Sha256,Sign' and c['tags'][0]['nodeid']=='ns=4;i=24'
    assert c['tags'][0]['id']!='original' and c['tags'][0]['simulation']['value'] is True
    assert result['project']['jobs'][0]['kind']=='simulation'

def test_existing_modbus_read_only_rejected():
    s=spec();s['mode']='existing';s['tag_spec']['area']='input'
    with pytest.raises(ValueError):build_simulation(s)

def test_initial_and_units():
    s=spec();s.update(initial_value='5',units='bar');r=build_simulation(s)
    assert r['project']['connections'][0]['tags'][0]['initial_value']=='5'
    assert r['project']['connections'][0]['tags'][0]['units']=='bar'

def test_wizard_api_has_no_side_effects(tmp_path):
    async def run():
        from aiohttp.test_utils import TestClient,TestServer
        from model import default_project
        import server
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json'
        async with TestClient(TestServer(server.create_app(default_project()))) as client:
            b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
            assert (await client.post('/api/simulation-draft',json=spec())).status==403
            response=await client.post('/api/simulation-draft',json=spec(),headers=h);assert response.status==200
            assert (await (await client.get('/api/scenarios',headers=h)).json())==[]
            assert not client.server.app['engine'].active()
            assert (await (await client.get('/api/project',headers=h)).json())['connections']==[]
    asyncio.run(run())

def test_local_template_rewrites_nodeids_preserves_overrides():
    s=spec('opcua');s['tag_source']='configured';s['connection']['tags']=[{'id':'old','tag':'Valve','nodeid':'ns=7;i=44','dtype':'Boolean','simulation':{'pattern':'constant','value':True},'units':'state'}]
    r=build_simulation(s);t=r['project']['connections'][0]['tags'][0]
    assert t['nodeid']=='ns=2;s=Valve' and t['simulation']['value'] is True and t['units']=='state'

@pytest.mark.parametrize('protocol',['modbus','opcua'])
def test_existing_mode_writes_to_local_device(tmp_path,protocol):
    from engine import Engine
    from scenarios import Catalogue,Runner
    async def run():
        source=spec(protocol);local=build_simulation(source);local['project']['jobs'][0]['dynamic']=False
        server=Engine(local['project']);job=local['project']['jobs'][0]['id']
        try:
            await server.start(job)
            async with asyncio.timeout(10):
                while server.states[job]['state']!='RUNNING':await asyncio.sleep(.01)
            source.update(mode='existing',tag_source='configured',connection=copy.deepcopy(local['project']['connections'][0]))
            scenario=build_simulation(source);scenario['id']='existing';store=Catalogue(tmp_path/'runs.db');store.put('scenarios',scenario);runner=Runner(store)
            key=await runner.start('existing');await runner.task;r=store.get('runs',key)
            assert r['state']=='PASSED',r.get('error')
            assert all(t['observed']==23.5 for t in r['steps'][-1]['tags'])
            assert server.active()  # Runner cleanup must not stop the external device.
        finally:await server.stop_all()
    asyncio.run(run())
