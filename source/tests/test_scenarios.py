import asyncio
import copy
import socket
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from scenarios import Catalogue, Runner, validate_scenario
from drivers import adapter


def scenario(port=15030):
    c={'id':'local','name':'Loopback','protocol':'modbus','host':'127.0.0.1','port':port,'device_id':1,'tags':[{'id':'temp','tag':'Temperature','dtype':'Float','address':0,'area':'holding','order':'ABCD'}]}
    j={'id':'server','name':'Server','kind':'server','connection':'local','interval':.1,'dynamic':False,'simulation':{'pattern':'constant','value':0}}
    return {'id':'test','name':'Write and verify','project':{'version':1,'name':'Test','connections':[c],'jobs':[j],'settings':{'autostart':False}},'steps':[{'action':'start_job','job':'server','timeout':5},{'action':'write','connection':'local','tag':'temp','value':23.5},{'action':'read','connection':'local','tag':'temp','variable':'actual'},{'action':'assert','variable':'actual','operator':'within','expected':23.5,'tolerance':.001}]}

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def test_catalogue_roundtrip_and_restart(tmp_path):
    store=Catalogue(tmp_path/'catalogue.sqlite3');s=validate_scenario(scenario());store.put('scenarios',s)
    assert store.get('scenarios','test')==s
    store.put('runs',{'id':'r','state':'RUNNING','started':1})
    assert Catalogue(store.path).get('runs','r')['state']=='ERROR'

@pytest.mark.parametrize('change',[
    lambda s:s.update(name=''),
    lambda s:s.update(steps=[]),
    lambda s:s['steps'][1].update(tag='missing'),
    lambda s:s['steps'][3].update(variable='missing'),
    lambda s:s['steps'][3].update(tolerance=-1),
    lambda s:s['steps'][3].update(timeout=float('nan')),
    lambda s:s['steps'][3].update(action='shell'),
    lambda s:s['steps'][3].update(operator='between',minimum=10,maximum=0),
])
def test_validation(change):
    s=scenario();change(s)
    with pytest.raises((ValueError,KeyError)):validate_scenario(s)

def test_passing_run_and_listener_cleanup(tmp_path):
    async def run():
        p=port();store=Catalogue(tmp_path/'c.db');store.put('scenarios',scenario(p));runner=Runner(store)
        key=await runner.start('test');await runner.task
        r=store.get('runs',key)
        assert r['state']=='PASSED',r
        assert len(r['steps'])==4 and r['steps'][2]['observed']==23.5
        with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1',p))!=0
    asyncio.run(run())

def test_failed_assertion_skips_remaining(tmp_path):
    async def run():
        s=scenario(port());s['steps'][3].update(expected=99);s['steps'].append({'action':'wait','seconds':0})
        store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);runner=Runner(store);key=await runner.start('test');await runner.task
        r=store.get('runs',key);assert r['state']=='FAILED' and r['skipped']==1
        assert r['steps'][-1]['state']=='FAILED'
    asyncio.run(run())

def test_cancellation_of_independent_wait_runs(tmp_path):
    async def run():
        s=scenario();s['steps']=[{'action':'wait','seconds':20,'timeout':30}]
        store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);runner=Runner(store);key=await runner.start('test')
        await asyncio.sleep(.02)
        second=await runner.start('test')
        assert second!=key and not runner.tasks[key].done()
        await runner.stop();r=store.get('runs',key);assert r['state']=='CANCELLED' and r['steps'][0]['state']=='CANCELLED'
    asyncio.run(run())

def test_timeout_is_infrastructure_error(tmp_path):
    async def run():
        s=scenario(port());s['project']['jobs']=[];s['steps']=[{'action':'read','connection':'local','tag':'temp','variable':'v','timeout':.1}]
        store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);runner=Runner(store);key=await runner.start('test');await runner.task
        assert store.get('runs',key)['state']=='ERROR'
    asyncio.run(run())

def test_immediate_cancellation(tmp_path):
    async def run():
        s=scenario();s['steps']=[{'action':'wait','seconds':20,'timeout':30}]
        store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);runner=Runner(store);key=await runner.start('test');await runner.stop()
        assert store.get('runs',key)['state']=='CANCELLED'
    asyncio.run(run())

def test_api_reports_and_run_conflicts(tmp_path):
    async def run():
        from aiohttp.test_utils import TestClient,TestServer
        import server
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json'
        app=server.create_app({'version':1,'name':'Empty','connections':[],'jobs':[],'settings':{}})
        async with TestClient(TestServer(app)) as client:
            init=await (await client.get('/api/bootstrap')).json();headers={'X-Workbench-Token':init['token']}
            assert (await client.get('/api/scenarios')).status==403
            s=scenario(port());s['steps']=[{'action':'wait','seconds':10,'timeout':15}]
            assert (await client.post('/api/scenarios',json=s,headers=headers)).status==200
            resp=await client.post('/api/scenarios/run',json={'id':'test'},headers=headers);key=(await resp.json())['id']
            assert (await client.put('/api/project',json=s['project'],headers=headers)).status==200
            assert (await client.post('/api/action',json={'action':'start_all'},headers=headers)).status==200
            assert app['runner'].active() and app['engine'].active()
            await client.post('/api/action',json={'action':'stop_all'},headers=headers)
            r=await (await client.get('/api/runs/'+key,headers=headers)).json();assert r['state']=='CANCELLED' and 'snapshot' not in r
            report=await (await client.get('/api/runs/'+key+'/report',headers=headers)).text();assert 'CANCELLED' in report and 'password' not in report
    asyncio.run(run())

def test_opcua_scenario(tmp_path):
    async def run():
        from asyncua import Server
        p=port();srv=Server();await srv.init();srv.set_endpoint(f'opc.tcp://127.0.0.1:{p}')
        ns=await srv.register_namespace('urn:test:scenario');node=await srv.nodes.objects.add_variable(f'ns={ns};s=Temperature','Temperature',0.0);await node.set_writable()
        s=scenario();c=s['project']['connections'][0];c.update(protocol='opcua',url=f'opc.tcp://127.0.0.1:{p}',security='None');c['tags'][0].update(dtype='Double',nodeid=f'ns={ns};s=Temperature')
        s['project']['jobs']=[];s['steps']=s['steps'][1:]
        store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);runner=Runner(store)
        async with srv:
            key=await runner.start('test');await runner.task;r=store.get('runs',key);assert r['state']=='PASSED',r
            assert await node.read_value()==23.5
    asyncio.run(run())

def bulk_scenario(count, test_port):
    s=scenario(test_port);c=s['project']['connections'][0]
    c['tags']=[{'id':f't{i}','tag':f'Point{i+1:04d}','dtype':'Float','address':i*2,'area':'holding','order':'ABCD'} for i in range(count)]
    for step in s['steps']:
        if step['action'] in ('read','write'):step.update(scope='all',timeout=120);step.pop('tag',None)
    return s

@pytest.mark.parametrize('count',[20,5400])
def test_bulk_all_modbus(tmp_path,count):
    async def run():
        store=Catalogue(tmp_path/'bulk.db');store.put('scenarios',bulk_scenario(count,port()));runner=Runner(store);key=await runner.start('test');await runner.task
        r=store.get('runs',key);assert r['state']=='PASSED',r.get('error')
        for step in r['steps'][1:]:
            assert step['passed_tags']==count
            assert len(step['tags'])==count
        assert all(t['observed']==23.5 for t in r['steps'][2]['tags'])
    asyncio.run(run())

def test_bulk_selected_and_mismatch(tmp_path):
    async def run():
        s=bulk_scenario(4,port())
        s['steps'][1].update(scope='selected',tags=['t0','t2'])
        # Read all points: the unselected two remain zero, and must fail verification.
        store=Catalogue(tmp_path/'bulk.db');store.put('scenarios',s);runner=Runner(store);key=await runner.start('test');await runner.task
        r=store.get('runs',key);assert r['state']=='FAILED'
        assert r['steps'][1]['passed_tags']==2
        assert r['steps'][3]['passed_tags']==2 and r['steps'][3]['failed_tags']==2
        assert {t['id'] for t in r['steps'][3]['tags'] if t['state']=='FAILED'}=={'t1','t3'}
    asyncio.run(run())

@pytest.mark.parametrize('scope,tags',[('selected',[]),('selected',['missing']),('selected',['t0','t0']),('wrong',[])])
def test_invalid_bulk_selection(scope,tags):
    s=bulk_scenario(2,15030);s['steps'][1].update(scope=scope,tags=tags)
    with pytest.raises(ValueError):validate_scenario(s)

def test_bulk_validation_before_write():
    s=bulk_scenario(2,15030);s['project']['connections'][0]['tags'][1]['dtype']='Boolean'
    with pytest.raises(ValueError):validate_scenario(s)

def test_bulk_opcua(tmp_path):
    async def run():
        from asyncua import Server
        p=port();srv=Server();await srv.init();srv.set_endpoint(f'opc.tcp://127.0.0.1:{p}')
        ns=await srv.register_namespace('urn:bulk');s=bulk_scenario(20,p);c=s['project']['connections'][0]
        c.update(protocol='opcua',url=f'opc.tcp://127.0.0.1:{p}',security='None')
        for tag in c['tags']:
            tag.update(dtype='Double',nodeid=f"ns={ns};s={tag['tag']}")
            node=await srv.nodes.objects.add_variable(tag['nodeid'],tag['tag'],0.0);await node.set_writable()
        s['project']['jobs']=[];s['steps']=s['steps'][1:]
        store=Catalogue(tmp_path/'bulk.db');store.put('scenarios',s);runner=Runner(store)
        async with srv:
            key=await runner.start('test');await runner.task;r=store.get('runs',key)
            assert r['state']=='PASSED',r.get('error')
            assert r['steps'][-1]['passed_tags']==20
    asyncio.run(run())

def test_bulk_partial_failure_keeps_evidence(tmp_path,monkeypatch):
    import scenarios
    class Client:
        def __init__(self): self.calls=0
        async def connect(self): pass
        async def prepare(self,tags,write=False): pass
        async def close(self): pass
        async def write(self,tags,values):
            self.calls+=1
            if self.calls==2:raise ConnectionError('disconnected')
    monkeypatch.setattr(scenarios,'adapter',lambda c:Client())
    async def run():
        s=bulk_scenario(101,15030);s['project']['jobs']=[];s['steps']=s['steps'][1:]
        store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);r=Runner(store);key=await r.start('test');await r.task
        row=store.get('runs',key);step=row['steps'][0]
        assert row['state']=='ERROR' and row['skipped']==2
        assert (step['passed_tags'],step['error_tags'],step['skipped_tags'])==(50,50,1)
    asyncio.run(run())

def test_bulk_timeout_marks_unknown_not_passed(tmp_path,monkeypatch):
    import scenarios
    class Client:
        async def connect(self): pass
        async def prepare(self,tags,write=False): pass
        async def close(self): pass
        async def write(self,tags,values):await asyncio.sleep(10)
    monkeypatch.setattr(scenarios,'adapter',lambda c:Client())
    async def run():
        s=bulk_scenario(60,15030);s['project']['jobs']=[];s['steps']=s['steps'][1:];s['steps'][0]['timeout']=.1
        store=Catalogue(tmp_path/'c.db');store.put('scenarios',s);r=Runner(store);key=await r.start('test');await r.task
        row=store.get('runs',key);step=row['steps'][0]
        assert row['state']=='ERROR'
        assert step['unknown_tags']==50 and step['skipped_tags']==10 and step['passed_tags']==0
    asyncio.run(run())

def test_bulk_api_reports(tmp_path):
    async def run():
        import csv,io
        from aiohttp.test_utils import TestClient,TestServer
        import server
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json'
        app=server.create_app({'version':1,'name':'Empty','connections':[],'jobs':[],'settings':{}})
        async with TestClient(TestServer(app)) as client:
            init=await (await client.get('/api/bootstrap')).json();headers={'X-Workbench-Token':init['token']}
            await client.post('/api/scenarios',json=bulk_scenario(20,port()),headers=headers)
            resp=await client.post('/api/scenarios/run',json={'id':'test'},headers=headers);key=(await resp.json())['id'];await app['runner'].task
            summary=await (await client.get('/api/runs',headers=headers)).json();assert 'tags' not in summary[0]['steps'][1]
            report=await (await client.get('/api/runs/'+key+'/report',headers=headers)).text();assert 'Point0020' in report and 'tag results' in report
            content=await (await client.get('/api/runs/'+key+'/csv',headers=headers)).text();rows=list(csv.DictReader(io.StringIO(content)))
            assert len(rows)==60 and all(r['state']=='PASSED' for r in rows)
    asyncio.run(run())
