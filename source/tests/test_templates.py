import asyncio,copy,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from templates import portable,preview,instantiate
from test_scenarios import scenario,port
from scenarios import Catalogue,Runner


def test_allowlist_and_roundtrip():
    s=scenario();c=s['project']['connections'][0];c.update(username='user-secret',password='password-secret',private_key='/secret/key',certificate='/secret/cert',server_certificate='/secret/server',unknown={'token':'nested-secret'})
    s['project']['settings']['autostart']=True
    c['tags'][0].update(initial_value=12,units='bar',simulation={'pattern':'csv','sequence':[1,2,3],'password':'nested-secret'})
    t=portable(s);raw=json.dumps(t)
    for value in ('user-secret','password-secret','/secret','nested-secret','127.0.0.1'):assert value not in raw
    assert t['scenario']['project']['settings']=={'autostart':False}
    assert preview(t)[2]==['local']
    with pytest.raises(ValueError,match='every endpoint'):instantiate(t,{})
    new=instantiate(t,{'local':'localhost'},'Copy')
    assert new['id']!=s['id'] and new['name']=='Copy'
    assert new['project']['connections'][0]['tags'][0]['simulation']['sequence']==[1,2,3]
    assert new['steps']==s['steps'] and new['project']['jobs']==s['project']['jobs']
    assert s['project']['settings']['autostart'] is True


def test_untrusted_metadata_and_credentials_are_discarded():
    t=portable(scenario());t['parameters']=[{'id':'evil','password':'secret'}]
    t['scenario']['project']['connections'][0].update(password='secret',private_key='secret')
    t['scenario']['steps'][0]['shell']='secret'
    clean,_,_=preview(t)
    assert 'secret' not in json.dumps(clean) and clean['parameters'][0]['id']=='local'
    t['scenario']['steps'][0]['action']='shell'
    with pytest.raises(ValueError):preview(t)

@pytest.mark.parametrize('mutation',[
    lambda t:t.update(version=2),lambda t:t.update(format='other'),
    lambda t:t['scenario']['project']['connections'][0].update(host='10.1.1.1'),
    lambda t:t['scenario']['steps'][1].update(tag='missing'),
])
def test_reject_invalid(mutation):
    t=portable(scenario());mutation(t)
    with pytest.raises(ValueError):preview(t)


def test_large_opc_template():
    p=json.loads((Path(__file__).resolve().parents[1]/'examples/Local_1000_OPCUA_Project.json').read_text())
    s={'name':'Large','project':p,'steps':[{'action':'start_job','job':p['jobs'][0]['id']}]}
    t=portable(s);new=instantiate(t,{'local_1000':'opc.tcp://127.0.0.1:15430'})
    assert new['project']['connections'][0]['tags']==p['connections'][0]['tags']
    assert len(new['project']['connections'][0]['tags'])==1000


def test_api_import_library_and_execute_preserves_workspace(tmp_path):
    async def run():
        import server
        from model import default_project
        from aiohttp.test_utils import TestClient,TestServer
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';original=default_project();app=server.create_app(original)
        async with TestClient(TestServer(app)) as client:
            b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
            assert (await client.post('/api/templates/import',json={},headers={})).status==403
            s=scenario(port());await client.post('/api/scenarios',json=s,headers=h)
            t=await (await client.get('/api/scenarios/test/template',headers=h)).json()
            assert (await client.post('/api/templates/preview',json={'template':t},headers=h)).status==200
            response=await client.post('/api/templates/import',json={'template':t},headers=h);assert response.status==400
            response=await client.post('/api/templates/save',json={'scenario':'test'},headers=h);assert response.status==200
            library=await (await client.get('/api/templates',headers=h)).json();assert len(library)==1
            waiting=copy.deepcopy(s);waiting['id']='waiting';waiting['steps']=[{'action':'wait','seconds':60,'timeout':65}];app['catalogue'].put('scenarios',waiting)
            active=await app['runner'].start('waiting');task=app['runner'].tasks[active]
            payload={'template':t,'bindings':{'local':'127.0.0.1'},'name':'Imported'}
            a=await (await client.post('/api/templates/import',json=payload,headers=h)).json()
            b=await (await client.post('/api/templates/import',json=payload,headers=h)).json();assert a['id']!=b['id']
            assert app['engine'].project==original and not app['engine'].active() and not task.done()
            await app['runner'].stop(active)
            assert not app['runner'].active()
            assert not server.PROJECT.exists()
            response=await client.post('/api/scenarios/run',json={'id':a['id']},headers=h);assert response.status==200
            await app['runner'].task
            key=(await response.json())['id'];assert app['catalogue'].get('runs',key)['state']=='PASSED'
        assert len(Catalogue(tmp_path/'scenarios.sqlite3').all('templates'))==1
    asyncio.run(run())


def test_binding_validation_and_gateway_roundtrip():
    p=json.loads((Path(__file__).resolve().parents[1]/'examples/Local_Gateway_Demo.json').read_text())
    s={'name':'Gateway','project':p,'steps':[{'action':'start_job','job':p['jobs'][0]['id']}]}
    t=portable(s)
    bindings={c['id']:('opc.tcp://127.0.0.1:15499' if c['protocol']=='opcua' else '127.0.0.1') for c in p['connections']}
    assert instantiate(t,bindings)['project']['jobs']==p['jobs']
    for value in ('bad host','http://example.com','opc.tcp://user:pass@host:4840','opc.tcp://host:99999'):
        opc=portable({'name':'UA','project':json.loads((Path(__file__).resolve().parents[1]/'examples/Local_1000_OPCUA_Project.json').read_text()),'steps':[{'action':'wait','seconds':0}]})
        with pytest.raises(ValueError):instantiate(opc,{'local_1000':value})
    with pytest.raises(ValueError):instantiate(t,bindings,'')
