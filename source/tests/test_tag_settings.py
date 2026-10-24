import asyncio,copy,socket,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from model import generated,initial,validate_project,default_project
from engine import Engine
from drivers import adapter


def project(protocol='modbus'):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    tags=[{'id':'a','tag':'Temperature','dtype':'Float','index':1,'address':0,'area':'holding','order':'ABCD','nodeid':'ns=2;s=Temperature','initial_value':'10','units':'°C','simulation':{'pattern':'constant','value':'23.5'}},{'id':'b','tag':'Running','dtype':'Boolean','index':2,'address':2,'area':'holding','nodeid':'ns=2;s=Running','initial_value':'false','simulation':{'pattern':'constant','value':'true'}},{'id':'c','tag':'Count','dtype':'Int32','index':3,'address':3,'area':'holding','nodeid':'ns=2;s=Count','initial_value':'-5','simulation':{'pattern':'constant','value':'7'}}]
    c={'id':'c','name':'Mixed simulation','protocol':protocol,'host':'127.0.0.1','port':port,'device_id':1,'url':f'opc.tcp://127.0.0.1:{port}','tags':tags}
    p=default_project();p['connections']=[c];p['jobs']=[{'id':'j','name':'Server','kind':'server','connection':'c','interval':.7,'dynamic':True,'simulation':{'pattern':'ramp','minimum':0,'maximum':100}}]
    return p


def test_inheritance_and_initial():
    tag={'dtype':'Float','simulation':{'minimum':10,'maximum':20},'initial_value':5}
    assert generated(tag,{'pattern':'ramp','period':3},0)==10
    assert generated(tag,{'pattern':'ramp','period':3},2)==20
    assert initial(tag,{})==5
    assert tag['simulation']=={'minimum':10,'maximum':20}


def test_mixed_validates():assert validate_project(project())

@pytest.mark.parametrize('change',[
    lambda t:t.update(initial_value='NaN'),
    lambda t:t.update(units='x'*81),
    lambda t:t.update(simulation={'pattern':'unknown'}),
    lambda t:t.update(simulation={'period':2.5}),
    lambda t:t.update(simulation={'period':1}),
    lambda t:t.update(simulation={'minimum':20,'maximum':10}),
    lambda t:t.update(simulation={'pattern':'csv','sequence':[]}),
    lambda t:t.update(dtype='UInt16',initial_value='-1'),
    lambda t:t.update(simulation='bad'),
])
def test_invalid_tag_setting(change):
    p=project();change(p['connections'][0]['tags'][0])
    with pytest.raises((ValueError,TypeError)):validate_project(p)


def test_csv_override_ignores_job_csv_type():
    p=project();p['jobs'][0]['simulation']={'pattern':'csv','sequence':[99,88]}
    # Boolean tag has its own constant value, so irrelevant numeric CSV must not reject it.
    validate_project(p)
    assert generated(p['connections'][0]['tags'][1],p['jobs'][0]['simulation'],1) is True

@pytest.mark.parametrize('protocol',['modbus','opcua'])
def test_mixed_server_initial_then_per_tag_values(protocol):
    async def run():
        p=validate_project(project(protocol));e=Engine(p);c=p['connections'][0];client=None
        try:
            await e.start('j')
            async with asyncio.timeout(10):
                while e.states['j']['state']!='RUNNING':
                    if e.states['j']['state']=='ERROR':raise RuntimeError(e.states['j']['error'])
                    await asyncio.sleep(.01)
            client=adapter(c);await client.connect();await client.prepare(c['tags'])
            first=await client.read(c['tags']);assert first=={'a':10.0,'b':False,'c':-5}
            async with asyncio.timeout(5):
                while e.states['j']['cycles']<2:await asyncio.sleep(.02)
            second=await client.read(c['tags']);assert second=={'a':23.5,'b':True,'c':7}
            assert e.snapshot()['jobs']['j']['values'][0]['units']=='°C'
        finally:
            if client:await client.close()
            await e.stop_all()
    asyncio.run(run())

def test_static_modbus_initial():
    async def run():
        p=project();p['jobs'][0]['dynamic']=False;e=Engine(validate_project(p));c=p['connections'][0];client=None
        try:
            await e.start('j')
            async with asyncio.timeout(10):
                while e.states['j']['state']!='RUNNING':await asyncio.sleep(.01)
            client=adapter(c);await client.connect();await client.prepare(c['tags']);assert await client.read(c['tags'])=={'a':10.0,'b':False,'c':-5}
        finally:
            if client:await client.close()
            await e.stop_all()
    asyncio.run(run())


def test_preview_is_offline_and_honours_overrides(tmp_path):
    async def run():
        import server
        from aiohttp.test_utils import TestClient,TestServer
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';p=project()
        async with TestClient(TestServer(server.create_app(p))) as client:
            init=await (await client.get('/api/bootstrap')).json();headers={'X-Workbench-Token':init['token']}
            response=await client.post('/api/preview-tags',json={'connection':p['connections'][0],'tags':['a','b','c'],'simulation':p['jobs'][0]['simulation']},headers=headers)
            result=await response.json();assert response.status==200,result
            assert result['rows'][0]['values']==[10.0]+[23.5]*11
            assert result['rows'][1]['values']==[False]+[True]*11
            assert result['rows'][2]['values']==[-5]+[7]*11
            assert not client.server.app['engine'].active()
            response=await client.post('/api/validate-project',json=p,headers=headers);assert response.status==200
    asyncio.run(run())
