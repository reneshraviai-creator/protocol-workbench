import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from tag_builder import generate_tags

def connection(protocol='modbus'):
    return {'id':'c','name':'Test','protocol':protocol,'host':'127.0.0.1','port':15040,'device_id':1,'url':'opc.tcp://127.0.0.1:15430','tags':[]}

def test_1000_float_addresses():
    c=connection();tags=generate_tags(c,{'count':1000})
    assert len(tags)==1000 and len({t['id'] for t in tags})==1000
    assert tags[0]['tag']=='Point_0001' and tags[-1]['tag']=='Point_1000'
    assert tags[0]['address']==0 and tags[-1]['address']==1998
    assert c['tags']==[]

def test_opcua_nodeids():
    tags=generate_tags(connection('opcua'),{'count':2,'prefix':'Pump_','suffix':'_PV','start_number':8,'digits':3,'namespace':4,'node_prefix':'DB1.'})
    assert tags[-1]['nodeid']=='ns=4;s=DB1.Pump_009_PV'

def test_append_and_pairing_index():
    c=connection();c['tags']=generate_tags(c,{'count':10})
    tags=generate_tags(c,{'count':2,'start_number':11,'address':20})
    assert tags[0]['index']==11 and tags[0]['address']==20
    assert len(c['tags'])==10

@pytest.mark.parametrize('spec',[
    {'count':0},{'count':20001},{'count':1.5},{'digits':-1},{'count':True},
    {'dtype':'String'},{'count':1000,'address':65000},{'stride':1},
    {'area':'coil','dtype':'Float'},{'order':'wrong'},{'prefix':'bad\n'},
])
def test_invalid(spec):
    with pytest.raises(ValueError):generate_tags(connection(),spec)

def test_collision_does_not_modify():
    c=connection();c['tags']=generate_tags(c,{'count':1})
    with pytest.raises(ValueError):generate_tags(c,{'count':1,'address':2})
    with pytest.raises(ValueError):generate_tags(c,{'count':1,'start_number':2})
    assert len(c['tags'])==1

def test_opc_duplicate_nodeid():
    c=connection('opcua');c['tags']=generate_tags(c,{'count':1});c['tags'][0]['tag']='Label'
    with pytest.raises(ValueError):generate_tags(c,{'count':1})

def test_boolean_and_double_strides():
    assert generate_tags(connection(),{'count':2,'dtype':'Boolean','area':'coil','stride':1})[-1]['address']==1
    assert generate_tags(connection(),{'count':2,'dtype':'Double','stride':4})[-1]['address']==4

def test_generated_1000_opcua_tags_simulate(tmp_path):
    import asyncio,socket
    from scenarios import Catalogue,Runner
    async def run():
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        c=connection('opcua');c['url']=f'opc.tcp://127.0.0.1:{port}';c['tags']=generate_tags(c,{'count':1000})
        p={'version':1,'name':'Generated simulation','connections':[c],'jobs':[{'id':'sim','name':'Generated server','kind':'server','connection':'c','interval':.2,'dynamic':True,'simulation':{'pattern':'sine','minimum':0,'maximum':100,'period':20}}],'settings':{}}
        s={'id':'generated','name':'1000 generated tags','project':p,'steps':[{'action':'start_job','job':'sim','timeout':30},{'action':'read','scope':'all','connection':'c','variable':'values','timeout':30},{'action':'assert','variable':'values','operator':'between','minimum':0,'maximum':100}]}
        store=Catalogue(tmp_path/'runs.db');store.put('scenarios',s);runner=Runner(store);key=await runner.start('generated');await runner.task
        result=store.get('runs',key);assert result['state']=='PASSED',result.get('error')
        assert result['steps'][-1]['passed_tags']==1000
    asyncio.run(run())

def test_generation_api_is_preview_only(tmp_path):
    import asyncio
    async def run():
        from aiohttp.test_utils import TestClient,TestServer
        import server
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json'
        p={'version':1,'name':'Empty','connections':[connection()],'jobs':[],'settings':{}}
        async with TestClient(TestServer(server.create_app(p))) as client:
            init=await (await client.get('/api/bootstrap')).json();headers={'X-Workbench-Token':init['token']};body={'connection':connection(),'spec':{'count':1000}}
            assert (await client.post('/api/generate-tags',json=body)).status==403
            response=await client.post('/api/generate-tags',json=body,headers=headers);data=await response.json()
            assert response.status==200 and len(data['tags'])==1000
            current=await (await client.get('/api/project',headers=headers)).json();assert current['connections'][0]['tags']==[]
            body['spec']['count']=-1
            assert (await client.post('/api/generate-tags',json=body,headers=headers)).status==400
    asyncio.run(run())
