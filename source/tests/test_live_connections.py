import asyncio,copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from config_changes import require_connection_additions_only
from model import default_project

def new_connection():return {'id':'added','name':'Added while running','protocol':'opcua','url':'opc.tcp://127.0.0.1:15499','security':'None','tags':[]}

def test_append_only_policy():
    old=default_project();new=copy.deepcopy(old);new['connections'].append(new_connection());require_connection_additions_only(old,new)
    require_connection_additions_only(new,new)

@pytest.mark.parametrize('change',[
 lambda p:p.update(name='Changed'),lambda p:p.update(settings={'autostart':True}),
 lambda p:p['connections'][0].update(name='Changed'),lambda p:p.update(connections=[]),
 lambda p:p.update(jobs=[{'id':'j'}]),
])
def test_protected_changes(change):
    old=default_project();old['connections']=[new_connection()];new=copy.deepcopy(old);change(new)
    with pytest.raises(ValueError):require_connection_additions_only(old,new)

def test_live_server_add_connection_does_not_interrupt(tmp_path):
    async def run():
        import server
        from test_scenarios import scenario,port
        from aiohttp.test_utils import TestClient,TestServer
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';p=scenario(port())['project'];app=server.create_app(p)
        async with TestClient(TestServer(app)) as client:
            init=await (await client.get('/api/bootstrap')).json();headers={'X-Workbench-Token':init['token']}
            await client.post('/api/action',json={'action':'start','id':'server'},headers=headers)
            engine=app['engine']
            async with asyncio.timeout(5):
                while engine.states['server']['state']!='RUNNING':await asyncio.sleep(.01)
            task=engine.tasks['server'];state=engine.states['server'];cycle=state['cycles']
            updated=copy.deepcopy(p);updated['connections'].append(new_connection())
            response=await client.put('/api/project',json=updated,headers=headers);assert response.status==200,await response.text()
            assert engine.tasks['server'] is task and engine.states['server'] is state
            async with asyncio.timeout(3):
                while state['cycles']<=cycle:await asyncio.sleep(.01)
            assert state['state']=='RUNNING'
            changed=copy.deepcopy(updated);changed['connections'][0]['host']='192.0.2.1'
            assert (await client.put('/api/project',json=changed,headers=headers)).status==400
            assert engine.project==updated and not task.done()
            await client.post('/api/action',json={'action':'stop_all'},headers=headers)
    asyncio.run(run())

def test_add_connection_during_scenario_keeps_snapshot(tmp_path):
    async def run():
        import server
        from test_scenarios import scenario
        from aiohttp.test_utils import TestClient,TestServer
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';old=default_project();app=server.create_app(old)
        async with TestClient(TestServer(app)) as client:
            b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
            s=scenario();s['steps']=[{'action':'wait','seconds':20,'timeout':30}]
            await client.post('/api/scenarios',json=s,headers=h);res=await client.post('/api/scenarios/run',json={'id':'test'},headers=h);key=(await res.json())['id']
            snapshot=app['catalogue'].get('runs',key)['snapshot']
            new=copy.deepcopy(old);new['connections'].append(new_connection());assert (await client.put('/api/project',json=new,headers=h)).status==200
            assert app['runner'].active() and app['catalogue'].get('runs',key)['snapshot']==snapshot
            await client.post('/api/action',json={'action':'stop_all'},headers=h)
    asyncio.run(run())
