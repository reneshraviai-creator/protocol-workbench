"""Template/test removal is recoverable and cannot interrupt immutable runs."""
import asyncio
import copy
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from access_control import permitted

@pytest.mark.parametrize('role,expected',[('Administrator',True),('Engineer',True),('Operator',False),('Read-only',False)])
@pytest.mark.parametrize('kind',['templates','scenarios'])
@pytest.mark.parametrize('action',['archive','restore'])
def test_library_mutations_require_configuration_role(role,expected,kind,action):
    assert permitted(role,f'/api/library-items/{kind}/abc/{action}','POST',{})==expected


def test_library_trash_restore_and_run_independence(tmp_path,monkeypatch):
    async def check():
        from aiohttp.test_utils import TestClient,TestServer
        import server
        from model import default_project
        from test_scenarios import scenario,port
        monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json')
        original=default_project();app=server.create_app(original)
        async with TestClient(TestServer(app)) as client:
            h={'X-Workbench-Token':(await (await client.get('/api/bootstrap')).json())['token']}
            s=scenario(port());s['steps']=[{'action':'wait','seconds':60,'timeout':65}]
            assert (await client.post('/api/scenarios',json=s,headers=h)).status==200
            template=await (await client.post('/api/templates/save',json={'scenario':s['id']},headers=h)).json()
            assert template['source_scenario']==s['id']
            payload={'template':template['template'],'bindings':{'local':'127.0.0.1'},'source_template':template['id']}
            imported=await (await client.post('/api/templates/import',json=payload,headers=h)).json()
            assert imported['source_template']==template['id']
            run=await (await client.post('/api/scenarios/run',json={'id':s['id']},headers=h)).json()
            task=app['runner'].tasks[run['id']]
            path=f"/api/library-items/templates/{template['id']}/archive"
            assert (await client.post(path,json={})).status==403
            assert (await client.post(path,json={},headers=h)).status==200
            assert await (await client.get('/api/templates',headers=h)).json()==[]
            assert not task.done() and app['engine'].project==original
            assert (await client.post('/api/templates/import',json=payload,headers=h)).status==400
            assert (await client.post(f"/api/library-items/scenarios/{s['id']}/archive",json={},headers=h)).status==400
            assert len(await (await client.get('/api/scenarios',headers=h)).json())==2
            trash=await (await client.get('/api/library-trash',headers=h)).json();assert trash[0]['kind']=='templates'
            assert (await client.post(path.replace('/archive','/restore'),json={},headers=h)).status==200
            restored=await (await client.get('/api/templates',headers=h)).json()
            assert restored[0]['template']==template['template']
            # A long active run stays visible even after many newer completed runs.
            for i in range(105):
                app['catalogue'].put('runs',{'id':f'completed-{i}','name':'Later test','scenario':s['id'],'state':'PASSED','started':9999999999+i,'steps':[]})
            history=await (await client.get('/api/runs',headers=h)).json()
            assert len(history)==101 and history[0]['id']==run['id']
            await app['runner'].stop(run['id'])
            before=copy.deepcopy(app['catalogue'].get('runs',run['id']))
            sp=f"/api/library-items/scenarios/{s['id']}/archive"
            assert (await client.post(sp,json={},headers=h)).status==200
            assert len(await (await client.get('/api/scenarios',headers=h)).json())==1
            assert (await client.post('/api/scenarios/run',json={'id':s['id']},headers=h)).status==400
            assert app['catalogue'].get('runs',run['id'])==before
            assert (await client.post(sp.replace('/archive','/restore'),json={},headers=h)).status==200
            assert len(await (await client.get('/api/scenarios',headers=h)).json())==2
            assert (await client.post('/api/library-items/runs/abc/archive',json={},headers=h)).status==400
            assert (await client.post('/api/library-items/templates/missing/archive',json={},headers=h)).status==400
            assert app['engine'].project==original
    asyncio.run(check())
