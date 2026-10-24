import asyncio,copy,json,sys,time
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from communication_health import HealthHub,HUB,OWNER,RecoveryController,policy,classify
from pymodbus.exceptions import ConnectionException
from transactions import endpoint


def config(**recovery):return {'id':'c','name':'Test connection','protocol':'modbus','host':'127.0.0.1','port':1502,'retry_seconds':.5,'recovery':recovery}


@pytest.mark.parametrize('r',[{'policy':'bad'},{'backoff':'random'},{'retry_limit':-1},{'retry_limit':1.2},{'retry_limit':True},{'max_delay_seconds':.1},{'max_delay_seconds':float('inf')},{'max_delay_seconds':True},{'repeat_safe':'yes'},{'policy':'resume_latest'},{'policy':'retry_failed'},{'extra':1}])
def test_invalid_policy(r):
    with pytest.raises(ValueError):policy(config(**r))


def test_invalid_recovery_shape_and_defaults():
    for r in ([],False,'none'):
        c=config();c['recovery']=r
        with pytest.raises(ValueError):policy(c)
    assert policy({'retry_seconds':.5})['policy']=='hold_uncertain'
    assert policy(config(policy='resume_latest',repeat_safe=True))['repeat_safe']


@pytest.mark.parametrize('error,category',[(ConnectionException('lost'),'TRANSPORT_SESSION'),(ConnectionError(),'TRANSPORT_SESSION'),(TimeoutError(),'TIMEOUT'),(ValueError(),'CONFIGURATION_DATA'),(PermissionError(),'CONFIGURATION_DATA'),(RuntimeError(),'INTERNAL')])
def test_classification(error,category):assert classify(error)==category


def test_backoff_cap_limit_and_reset(monkeypatch):
    async def run():
        delays=[]
        async def sleep(n):delays.append(n)
        monkeypatch.setattr('communication_health.asyncio.sleep',sleep)
        token=HUB.set(HealthHub());c=RecoveryController(config(backoff='exponential',max_delay_seconds=1,retry_limit=3),'job','Retry test');state={}
        try:
            for _ in range(3):assert await c.wait(ConnectionError(),state)
            assert delays==[.5,1,1] and c.total_retries==3 and c.failures==3
            assert not await c.wait(ConnectionError(),state) and state['state']=='ERROR' and c.state=='RETRY_EXHAUSTED'
            c.success();assert c.failures==0 and c.recoveries==1 and c.last_recovery_seconds>=0
            assert await c.wait(ConnectionError(),state);assert delays[-1]==.5
        finally:c.close();HUB.reset(token)
    asyncio.run(run())


def test_fatal_errors_do_not_retry():
    async def run():
        c=RecoveryController(config(),'job','Bad config');state={}
        assert not await c.wait(ValueError('Invalid datatype'),state)
        assert state['error']=='Invalid datatype' and c.total_retries==0
        c.close();assert c.state=='ERROR' and c.closed
    asyncio.run(run())


def test_cancellation_does_not_count_a_scheduled_retry_as_attempted():
    async def run():
        c=RecoveryController(config(),'job','Cancel');state={};task=asyncio.create_task(c.wait(ConnectionError(),state))
        await asyncio.sleep(.01);assert c.scheduled_retries==1 and c.total_retries==0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        c.close();assert c.total_retries==0 and c.closed
    asyncio.run(run())


def test_manual_read_hold_and_target_release():
    async def run():
        c=RecoveryController(config(policy='manual'),'monitor','Manual',True);state={};task=asyncio.create_task(c.wait(ConnectionError(),state))
        await asyncio.sleep(.01);assert state['state']=='WAITING_RECOVERY'
        with pytest.raises(ValueError):c.release('wrong endpoint')
        assert not c.gate.is_set();c.release(c.endpoint);assert await task;assert c.total_retries==1
        c.close()
        with pytest.raises(ValueError):c.release(c.endpoint)
    asyncio.run(run())


def test_health_latency_uptime_redaction_and_modbus_session_semantics():
    hub=HealthHub();c=config();s=hub.session(c);s.connected();s.success(.012,'read');snapshot=s.snapshot()
    assert snapshot['session_state']=='NOT_APPLICABLE' and snapshot['latency_ms']==12 and snapshot['last_operation']=='read' and snapshot['last_success']
    s.close();assert s.snapshot()['connected_uptime_seconds']==0 and s.snapshot()['uptime_seconds']>=0
    c={'protocol':'opcua','url':'opc.tcp://user:password@127.0.0.1:4840/private?secret=yes'};s=hub.session(c);s.connected();s.success(.1,'connect');data=json.dumps(hub.snapshot());assert 'password' not in data and 'secret' not in data and '/private' not in data
    assert s.snapshot()['session_state']=='ESTABLISHED'
    s.close()


def project(recovery):
    from test_live_monitor import setup
    c,p=setup('modbus');c['recovery']=recovery;c['tags']=[{'id':k,'tag':k,'dtype':'UInt16','area':'holding','address':i,'initial_value':99} for i,k in enumerate(('a','b'))]
    p['jobs']=[{'id':'writer','name':'Recovery writer','kind':'simulation','connection':c['id'],'interval':.1,'simulation':{'pattern':'constant','value':10},'verify':False}]
    return c,p


@pytest.mark.parametrize('mode',['hold_uncertain','manual','resume_latest'])
def test_unknown_write_never_automatically_replays_initial_value(monkeypatch,mode):
    async def run():
        from engine import Engine
        from test_live_monitor import until
        c,p=project({'policy':mode,'repeat_safe':mode=='resume_latest'});calls=[];clients=[];hub=HealthHub();token=HUB.set(hub)
        class Client:
            closed=False
            async def connect(self):pass
            async def prepare(self,*a,**kw):pass
            async def close(self):self.closed=True
            async def write(self,tags,values):
                calls.append(dict(values))
                if len(calls)==1:raise ConnectionError('lost write response')
                return len(tags)
        def factory(c):cl=Client();clients.append(cl);return cl
        monkeypatch.setattr('engine.adapter',factory);e=Engine(p)
        try:
            await e.start('writer')
            if mode!='resume_latest':
                await until(lambda:e.states['writer']['state']=='WAITING_RECOVERY')
                assert clients[0].closed and len(calls)==1 and e.registry.owners
                await asyncio.sleep(.1);assert len(calls)==1
                recovery=e.recovery_controllers['writer'];assert recovery.unknown_writes==1
                recovery.release(recovery.endpoint)
            await until(lambda:len(calls)>=2,4)
            assert calls[0]=={'a':99,'b':99} and calls[1]=={'a':10,'b':10}
        finally:await e.stop_all();HUB.reset(token)
        assert not e.registry.owners and all(x.closed for x in clients)
    asyncio.run(run())


def test_retry_confirmed_transient_failed_points_only(monkeypatch):
    async def run():
        from engine import Engine
        from test_live_monitor import until
        c,p=project({'policy':'retry_failed','repeat_safe':True});calls=[]
        class Client:
            async def connect(self):pass
            async def close(self):pass
            async def prepare(self,*a,**kw):pass
            async def write(self,tags,values):
                calls.append(([t['id'] for t in tags],dict(values)))
                if len(calls)==1:
                    self.last_write_result={'counts':{'UNKNOWN':0},'points':[{'id':'a','outcome':'SUCCEEDED','status':'Good'},{'id':'b','outcome':'FAILED','status':'BadServerTooBusy'}],'batches':[]}
                    raise ValueError('Partial write')
                return len(tags)
        monkeypatch.setattr('engine.adapter',lambda c:Client());e=Engine(p)
        try:
            await e.start('writer');await until(lambda:len(calls)>=3,4)
            assert calls[:3]==[(['a','b'],{'a':99,'b':99}),(['b'],{'b':99}),(['a','b'],{'a':10,'b':10})]
        finally:await e.stop_all()
    asyncio.run(run())


def test_stop_releases_unknown_hold_and_owned_resources(monkeypatch):
    async def run():
        from engine import Engine
        from test_live_monitor import until
        c,p=project({})
        class Client:
            async def connect(self):pass
            async def close(self):pass
            async def prepare(self,*a,**kw):pass
            async def write(self,*a):raise TimeoutError()
        monkeypatch.setattr('engine.adapter',lambda c:Client());e=Engine(p)
        await e.start('writer');await until(lambda:e.states['writer']['state']=='WAITING_RECOVERY');controller=e.recovery_controllers['writer']
        await e.stop_all();assert controller.closed and not e.registry.owners and not e.active()
    asyncio.run(run())


@pytest.mark.parametrize('protocol',['opcua','modbus'])
def test_real_scan_disconnect_and_session_reinitialisation(protocol):
    async def run():
        from engine import Engine
        from test_live_monitor import setup,until
        c,p=setup(protocol);p['jobs'][0]['dynamic']=False
        server=Engine(p);reader_project=copy.deepcopy(p);reader_project['jobs']=[{'id':'scan','name':'Read recovery','kind':'scan','connection':c['id'],'interval':.1}]
        hub=HealthHub();token=HUB.set(hub);reader=Engine(reader_project)
        try:
            await server.start('server');await until(lambda:server.states['server']['state']=='RUNNING');await reader.start('scan');await until(lambda:reader.states['scan'].get('cycles',0)>0)
            await server.stop_all();await until(lambda:reader.states['scan']['state']=='RECONNECTING',15)
            old=reader.states['scan']['cycles'];await server.start('server');await until(lambda:reader.states['scan'].get('cycles',0)>old,15)
            rec=reader.recovery_controllers['scan'];assert rec.recoveries>=1 and rec.last_recovery_seconds>=0
            assert any(s.snapshot()['last_success'] for s in hub.sessions.values())
            assert len(hub.sessions)>=2
        finally:await reader.stop_all();await server.stop_all();HUB.reset(token)
    asyncio.run(run())


def test_health_api_requires_auth_and_explicit_target_acknowledgement(tmp_path,monkeypatch):
    async def run():
        import server
        from aiohttp.test_utils import TestClient,TestServer
        from model import default_project
        monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(default_project());hub=app[server.HEALTH_KEY];token=HUB.set(hub)
        rec=RecoveryController(config(),'job','Held');rec.state='WAITING_RECOVERY';rec.unknown_writes=1;HUB.reset(token)
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/api/health')).status==403
            init=await(await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':init['token']};url='/api/health/'+rec.id+'/resume'
            assert (await client.post(url,json={},headers=h)).status==400
            assert (await client.post(url,json={'confirm':True,'target':rec.endpoint},headers=h)).status==400
            assert (await client.post(url,json={'confirm':True,'target':'wrong','acknowledge_unknown':True},headers=h)).status==400
            assert not rec.gate.is_set()
            r=await client.post(url,json={'confirm':True,'target':rec.endpoint,'acknowledge_unknown':True},headers=h);assert r.status==200 and rec.gate.is_set()
            data=await(await client.get('/api/health/export',headers=h)).json();assert data['recovery'][0]['unknown_writes']==1
            assert (await client.post('/api/health/missing/resume',json={'confirm':True},headers=h)).status==400
            assert not app['engine'].active()
    asyncio.run(run())
