import asyncio,copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from resources import ResourceRegistry,empty,add_writes,job_plan,scenario_plan
from scenarios import Catalogue,Runner
from engine import Engine
from model import default_project
from test_scenarios import scenario,port


def test_listener_cross_protocol_and_aliases():
    s=scenario();p=s['project'];reg=ResourceRegistry();reg.acquire('a','First',job_plan(p,p['jobs'][0]),'run')
    c={'id':'opc','name':'Other','protocol':'opcua','url':'opc.tcp://localhost:15030','tags':[]};j={'id':'b','kind':'server','connection':'opc','dynamic':False};p2=default_project();p2['connections']=[c]
    with pytest.raises(ValueError,match='First'):reg.acquire('b','Second',job_plan(p2,j),'run')


def test_word_spans_and_device_ids():
    c=scenario()['project']['connections'][0];reg=ResourceRegistry();p=empty();add_writes(p,c,c['tags']);reg.acquire('a','First',p,'run')
    c2=copy.deepcopy(c);c2['host']='localhost';c2['tags'][0].update(dtype='UInt16',address=1);other=empty();add_writes(other,c2,c2['tags'])
    with pytest.raises(ValueError):reg.acquire('b','Overlap',other,'run')
    c2['device_id']=2;other=empty();add_writes(other,c2,c2['tags']);reg.acquire('b','Other unit',other,'run')


def test_unresolved_ua_is_conservative():
    c={'protocol':'opcua','url':'opc.tcp://localhost:4840/a'};reg=ResourceRegistry();a=empty();add_writes(a,c,[{'id':'t','tag':'Unknown'}]);reg.acquire('a','Unresolved',a,'run')
    b=empty();add_writes(b,{'protocol':'opcua','url':'opc.tcp://127.0.0.1:4840/b'},[{'nodeid':'ns=2;s=Known'}])
    with pytest.raises(ValueError):reg.acquire('b','Resolved',b,'run')


def test_dynamic_server_locks_actual_nodeids():
    p=default_project();c={'id':'c','name':'Server','protocol':'opcua','url':'opc.tcp://127.0.0.1:4840','tags':[{'id':'t','tag':'Point','dtype':'Float','nodeid':'ns=7;i=99'}]};p['connections']=[c]
    resources=job_plan(p,{'kind':'server','connection':'c','dynamic':True});assert ('opcua','loopback',4840,'','ns=2;s=Point') in resources['writes']


def test_independent_runs_and_individual_stop(tmp_path):
    async def run():
        store=Catalogue(tmp_path/'runs.db');s=scenario(port());s['steps']=[s['steps'][0],{'action':'wait','seconds':60,'timeout':65}];s['id']='one';store.put('scenarios',s)
        second=copy.deepcopy(s);second['id']='two';second['name']='Second';second['project']['connections'][0]['port']=port();store.put('scenarios',second)
        registry=ResourceRegistry();runner=Runner(store,registry);a=await runner.start('one');b=await runner.start('two')
        try:
            await asyncio.sleep(.2);assert not runner.tasks[a].done() and not runner.tasks[b].done()
            assert len(registry.owners)==2
            await runner.stop(a);assert store.get('runs',a)['state']=='CANCELLED' and not runner.tasks[b].done()
            assert len(registry.owners)==1
            with pytest.raises(ValueError,match='Resource conflict'):await runner.start('two')
        finally:await runner.stop()
        assert not registry.owners
    asyncio.run(run())


def test_disjoint_writers_same_device_can_reserve():
    c=scenario()['project']['connections'][0];reg=ResourceRegistry();a=empty();add_writes(a,c,c['tags']);reg.acquire('a','First',a,'run')
    second=copy.deepcopy(c['tags'][0]);second['address']=2;b=empty();add_writes(b,c,[second]);reg.acquire('b','Next point',b,'run')
    assert len(reg.owners)==2


def test_workspace_and_scenario_share_registry(tmp_path):
    async def run():
        s=scenario(port());store=Catalogue(tmp_path/'runs.db');store.put('scenarios',s);registry=ResourceRegistry();engine=Engine(s['project'],registry);runner=Runner(store,registry)
        try:
            await engine.start('server')
            with pytest.raises(ValueError):await runner.start('test')
            independent=copy.deepcopy(s);independent['id']='other';independent['project']['connections'][0]['port']=port();store.put('scenarios',independent)
            key=await runner.start('other');await runner.tasks[key]
            assert store.get('runs',key)['state']=='PASSED' and engine.active()
        finally:await runner.stop();await engine.stop_all()
        assert not registry.owners
    asyncio.run(run())


def test_unrelated_config_edit_during_workspace_job(tmp_path):
    from config_changes import require_active_unchanged
    old=scenario()['project'];old['connections'].append({'id':'free','name':'Free','protocol':'opcua','url':'opc.tcp://127.0.0.1:4840','tags':[]});new=copy.deepcopy(old);new['connections'][1]['name']='Edited'
    require_active_unchanged(old,new,{'server'})
    new['connections'][0]['name']='Changed active'
    with pytest.raises(ValueError):require_active_unchanged(old,new,{'server'})

def test_reservation_released_on_catalogue_failure(tmp_path,monkeypatch):
    async def run():
        store=Catalogue(tmp_path/'runs.db');s=scenario(port());store.put('scenarios',s);registry=ResourceRegistry();runner=Runner(store,registry)
        original=store.put
        def fail(table,row):
            if table=='runs':raise OSError('disk failure')
            return original(table,row)
        monkeypatch.setattr(store,'put',fail)
        with pytest.raises(OSError):await runner.start('test')
        assert not registry.owners
    asyncio.run(run())


def test_manual_write_conflict_and_start_all_rollback(tmp_path):
    async def run():
        import server
        from aiohttp.test_utils import TestClient,TestServer
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';p=scenario(port())['project'];p['jobs'][0]['dynamic']=True
        app=server.create_app(p)
        async with TestClient(TestServer(app)) as client:
            b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
            await client.post('/api/action',json={'action':'start','id':'server'},headers=h)
            async with asyncio.timeout(5):
                while app['engine'].states['server']['state']!='RUNNING':await asyncio.sleep(.01)
            response=await client.post('/api/connection',json={'connection':p['connections'][0],'mode':'write','values':{'temp':22}},headers=h)
            assert response.status==400 and 'Resource conflict' in (await response.json())['error']
            assert set(app['resources'].owners)=={'job:server'}
            await client.post('/api/action',json={'action':'stop_all'},headers=h)
            assert not app['resources'].owners
    asyncio.run(run())

def test_concurrent_disjoint_real_writes(tmp_path):
    async def run():
        p=scenario(port())['project'];c=p['connections'][0];c['tags'].append({**c['tags'][0],'id':'second','tag':'Pressure','address':2});server=Engine(p);registry=ResourceRegistry();store=Catalogue(tmp_path/'runs.db');runner=Runner(store,registry)
        try:
            await server.start('server')
            async with asyncio.timeout(5):
                while server.states['server']['state']!='RUNNING':await asyncio.sleep(.01)
            for key,tag,value in [('one','temp',11),('two','second',22)]:
                s={'id':key,'name':key,'project':copy.deepcopy(p),'steps':[{'action':'write','connection':'local','tag':tag,'value':value},{'action':'wait','seconds':.2,'timeout':2},{'action':'read','connection':'local','tag':tag,'variable':'v'},{'action':'assert','variable':'v','operator':'equals','expected':value}]}
                store.put('scenarios',s)
            a=await runner.start('one');b=await runner.start('two');await asyncio.gather(runner.tasks[a],runner.tasks[b])
            assert store.get('runs',a)['state']=='PASSED' and store.get('runs',b)['state']=='PASSED'
            assert not registry.owners and server.active()
        finally:await runner.stop();await server.stop_all()
    asyncio.run(run())

def test_start_all_preflight_does_not_partially_start(tmp_path):
    async def run():
        import server
        from aiohttp.test_utils import TestClient,TestServer
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json'
        occupied=scenario(port());occupied['steps']=[occupied['steps'][0],{'action':'wait','seconds':60,'timeout':65}]
        workspace=scenario(port())['project'];second=copy.deepcopy(occupied['project']['connections'][0]);second['id']='second'
        workspace['connections'].append(second);workspace['jobs'].append({**workspace['jobs'][0],'id':'second','connection':'second'})
        app=server.create_app(workspace)
        async with TestClient(TestServer(app)) as client:
            boot=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':boot['token']}
            app['catalogue'].put('scenarios',occupied);key=await app['runner'].start('test')
            response=await client.post('/api/action',json={'action':'start_all'},headers=h)
            assert response.status==400
            assert not app['engine'].active() and app['runner'].active()
            assert set(app['resources'].owners)=={'run:'+key}
            await app['runner'].stop()
    asyncio.run(run())
