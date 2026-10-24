import asyncio,copy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))

def test_create_edit_duplicate_while_running_preserves_snapshot(tmp_path):
    async def run():
        import server
        from test_scenarios import scenario
        from aiohttp.test_utils import TestClient,TestServer
        from model import default_project
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';app=server.create_app(default_project())
        async with TestClient(TestServer(app)) as client:
            b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
            original=scenario();original['steps']=[{'action':'wait','seconds':60,'timeout':65}]
            await client.post('/api/scenarios',json=original,headers=h)
            response=await client.post('/api/scenarios/run',json={'id':'test'},headers=h);key=(await response.json())['id']
            task=app['runner'].task;before=app['catalogue'].get('runs',key)['snapshot']
            edited=copy.deepcopy(original);edited['name']='Future version';edited['steps'][0]['seconds']=1
            assert (await client.post('/api/scenarios',json=edited,headers=h)).status==200
            added=copy.deepcopy(edited);added.pop('id');added['name']='New scenario'
            response=await client.post('/api/scenarios',json=added,headers=h);new=(await response.json())['id'];assert response.status==200
            assert app['runner'].task is task and app['runner'].active()
            assert app['catalogue'].get('runs',key)['snapshot']==before
            assert app['catalogue'].get('scenarios','test')['steps'][0]['seconds']==1
            assert (await client.post('/api/scenarios/run',json={'id':new},headers=h)).status==200
            assert len([task for task in app['runner'].tasks.values() if not task.done()])==2
            await client.post('/api/scenarios/stop',json={},headers=h)
    asyncio.run(run())
