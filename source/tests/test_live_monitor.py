import asyncio,copy,socket,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from live_monitor import MonitorHub,LiveMonitor
from model import default_project
from engine import Engine

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def setup(protocol):
    n=port();c={'id':'c','name':'Monitor fixture','protocol':protocol,'host':'127.0.0.1','port':n,'device_id':1,'url':f'opc.tcp://127.0.0.1:{n}','security':'None','timeout':1,'retry_seconds':.5,'tags':[{'id':'t','tag':'Temperature','dtype':'Double' if protocol=='opcua' else 'Float','nodeid':'ns=2;s=Temperature','address':0,'area':'holding','order':'ABCD','units':'°C'}]}
    p=default_project();p['connections']=[c];p['jobs']=[{'id':'server','name':'Server','kind':'server','connection':'c','interval':.1,'dynamic':True,'simulation':{'pattern':'ramp','minimum':0,'maximum':100,'period':20}}]
    return c,p

async def until(condition,seconds=10):
    async with asyncio.timeout(seconds):
        while not condition():await asyncio.sleep(.02)

@pytest.mark.parametrize('protocol',['opcua','modbus'])
def test_live_data_trend_and_stop_leaves_source(protocol):
    async def run():
        c,p=setup(protocol);e=Engine(p);hub=MonitorHub()
        try:
            await e.start('server');await until(lambda:e.states['server']['state']=='RUNNING')
            m=hub.start(c,['t'],{'publishing_ms':100,'sampling_ms':100,'poll_seconds':.1})
            await until(lambda:m.events>=3)
            snap=m.snapshot();assert snap['state']=='RUNNING',snap
            assert snap['rows'][0]['quality']=='Good' and snap['rows'][0]['units']=='°C'
            assert len(snap['trend'])>=3
            if protocol=='opcua':
                assert snap['rows'][0]['source_timestamp']
                assert m.revised['sampling_ms_requested']==100
            else:
                assert snap['rows'][0]['raw'] is not None
                assert any(t.get('function')==3 and t['direction']=='TX' for t in snap['traffic'])
                assert any(t['kind']=='bytes' and t['direction']=='RX' for t in snap['traffic'])
            await m.stop();assert m.state=='STOPPED' and e.active()
        finally:await hub.stop_all();await e.stop_all()
    asyncio.run(run())

def test_bad_quality_retains_last_good():
    async def run():
        from asyncua import Server,ua
        c,_=setup('opcua');srv=Server();await srv.init();srv.set_endpoint(c['url']);ns=await srv.register_namespace('urn:monitor');node=await srv.nodes.objects.add_variable(c['tags'][0]['nodeid'],'Temperature',12.5)
        hub=MonitorHub()
        async with srv:
            try:
                m=hub.start(c,['t'],{'publishing_ms':100,'sampling_ms':100});await until(lambda:m.events>=1)
                await node.write_attribute(ua.AttributeIds.Value,ua.DataValue(ua.Variant(0.0,ua.VariantType.Double),ua.StatusCode(ua.StatusCodes.BadOutOfService)))
                await until(lambda:m.values['t']['quality']=='BadOutOfService')
                assert m.values['t']['last_good']==12.5
                assert m.history['t'][-1]['value'] is None
            finally:await hub.stop_all()
    asyncio.run(run())

def test_reconnect_modbus():
    async def run():
        c,p=setup('modbus');hub=MonitorHub();e=Engine(p)
        try:
            m=hub.start(c,['t'],{'poll_seconds':.1});await until(lambda:m.state=='RECONNECTING')
            await e.start('server');await until(lambda:m.events>0)
            assert m.state=='RUNNING' and m.reconnects>0
            await e.stop_all();await until(lambda:m.state=='RECONNECTING')
            assert not m.snapshot()['rows'][0]['connected']
            assert m.snapshot()['rows'][0]['value'] is not None
        finally:await hub.stop_all();await e.stop_all()
    asyncio.run(run())

@pytest.mark.parametrize('settings',[{'publishing_ms':0},{'sampling_ms':float('nan')},{'queue_size':0},{'poll_seconds':0}])
def test_settings_rejected(settings):
    c,_=setup('modbus')
    with pytest.raises(ValueError):MonitorHub().start(c,['t'],settings)


def test_history_and_traffic_bounded():
    c,_=setup('modbus');m=LiveMonitor(c,c['tags'],{})
    for n in range(700):m.record(c['tags'][0],n)
    for n in range(300):m.trace('bytes',True,b'1234')
    assert len(m.history['t'])==300 and len(m.traffic)==200
    assert len(m.snapshot()['trend'])==300


def test_monitor_api_and_attributes(tmp_path):
    async def run():
        import server
        from aiohttp.test_utils import TestClient,TestServer
        server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';c,p=setup('opcua');app=server.create_app(p)
        async with TestClient(TestServer(app)) as client:
            b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
            await client.post('/api/action',json={'action':'start','id':'server'},headers=h);await until(lambda:app['engine'].states['server']['state']=='RUNNING')
            body={'connection':'c','tags':['t'],'settings':{'publishing_ms':100,'sampling_ms':100}}
            assert (await client.post('/api/monitors',json=body)).status==403
            response=await client.post('/api/monitors',json=body,headers=h);data=await response.json();assert response.status==200,data
            m=app['monitors'].monitors[data['id']];await until(lambda:m.events>0)
            attrs=await (await client.post('/api/node-inspect',json={'connection':'c','tag':'t'},headers=h)).json()
            assert any(x['attribute']=='DataType' for x in attrs['attributes']) and attrs['references_total']>0
            snap=await (await client.get('/api/monitors/'+m.id,headers=h)).json();assert snap['rows'][0]['quality']=='Good'
            export=await (await client.get('/api/monitors/'+m.id+'/csv',headers=h)).text();assert 'Temperature' in export
            await client.post('/api/monitors/'+m.id+'/stop',json={},headers=h);assert app['engine'].active()
            await client.post('/api/action',json={'action':'stop_all'},headers=h);assert not app['engine'].active()
    asyncio.run(run())

def test_reconnect_opcua():
    async def run():
        c,p=setup('opcua');hub=MonitorHub();e=Engine(p)
        try:
            await e.start('server');await until(lambda:e.states['server']['state']=='RUNNING')
            m=hub.start(c,['t'],{'publishing_ms':100,'sampling_ms':100});await until(lambda:m.events>1)
            await e.stop_all();await until(lambda:m.state=='RECONNECTING',15)
            assert not m.snapshot()['rows'][0]['connected']
            count=m.events;await e.start('server');await until(lambda:m.state=='RUNNING' and m.events>count,15)
            assert m.reconnects>0 and any(x['value'] is None for x in m.history['t'])
        finally:await hub.stop_all();await e.stop_all()
    asyncio.run(run())

def test_constant_subscription_is_healthy_without_changes():
    async def run():
        c,p=setup('opcua');p['jobs'][0]['simulation']={'pattern':'constant','value':7};e=Engine(p);hub=MonitorHub()
        try:
            await e.start('server');await until(lambda:e.states['server']['state']=='RUNNING')
            m=hub.start(c,['t'],{'publishing_ms':100,'sampling_ms':100});await until(lambda:m.events>0);await asyncio.sleep(1.3)
            assert m.state=='RUNNING' and m.values['t']['value']==7
            assert m.last_check and m.snapshot()['rows'][0]['connected']
        finally:await hub.stop_all();await e.stop_all()
    asyncio.run(run())

def test_quality_good_with_information_bits():
    c,_=setup('modbus');m=LiveMonitor(c,c['tags'],{})
    m.record(c['tags'][0],7,status='GoodClamped',good=True)
    assert m.values['t']['last_good']==7 and m.history['t'][-1]['value']==7

def test_pagination_and_exception_meaning():
    from pymodbus.pdu import ExceptionResponse
    c,_=setup('modbus');tags=[{**c['tags'][0],'id':str(i),'tag':f'Point_{i:03d}'} for i in range(120)];m=LiveMonitor(c,tags,{})
    for t in tags:m.record(t,1)
    assert len(m.snapshot(page=2)['rows'])==20 and m.snapshot(page=2)['page']==2
    assert m.snapshot(query='Point_11')['total']==10
    response=ExceptionResponse(3,2);m.trace('pdu',False,response)
    assert m.traffic[-1]['exception_meaning']=='Illegal data address'

def test_subscription_1000_points():
    async def run():
        c,p=setup('opcua');c['tags']=[{**c['tags'][0],'id':f't{i}','tag':f'Point_{i:04d}','nodeid':f'ns=2;s=Point_{i:04d}','index':i+1} for i in range(1000)];p['jobs'][0]['interval']=1;e=Engine(p);hub=MonitorHub()
        try:
            await e.start('server');await until(lambda:e.states['server']['state']=='RUNNING',20)
            m=hub.start(c,[t['id'] for t in c['tags']],{'publishing_ms':250,'sampling_ms':250});await until(lambda:len(m.values)==1000,20)
            assert m.state=='RUNNING' and all(t['quality']=='Good' for t in m.values.values())
            assert len(m.snapshot(page=19)['rows'])==50 and m.depth==50
        finally:await hub.stop_all();await e.stop_all()
    asyncio.run(run())
