import asyncio,copy,csv,io,json,math,sys,time
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from performance import Performance,BackpressureError,settings,batch_settings,latency,resident_memory
from transactions import TransactionStore,STORE_CONTEXT,tracked


@pytest.mark.parametrize('value',[[],False,{'unknown':1},{'enabled':1},{'concurrency':0},{'concurrency':1.5},{'concurrency':True},{'concurrency':65},{'queue_limit':-1},{'queue_limit':4097},{'operations_per_second':-1},{'operations_per_second':float('inf')},{'operations_per_second':True},{'cpu_percent':float('nan')},{'memory_mb':'bad'},{'loop_lag_ms':-1},{'p95_ms':-1},{'queue_percent':101}])
def test_invalid_settings(value):
    with pytest.raises(ValueError):settings(value)


@pytest.mark.parametrize('value',[[],False,{'bad':2},{'opcua_batch_size':0},{'opcua_batch_size':1001},{'opcua_batch_size':True},{'modbus_batch_units':3},{'modbus_batch_units':121},{'modbus_batch_units':4.5}])
def test_invalid_batches(value):
    with pytest.raises(ValueError):batch_settings({'performance':value})


def test_batch_and_project_defaults_do_not_mutate():
    from model import default_project,validate_project
    p=default_project();before=copy.deepcopy(p);validate_project(p);assert p==before
    assert settings()['enabled'] is False and settings()['operations_per_second']==0
    assert batch_settings({})=={'opcua_batch_size':50,'modbus_batch_units':120}
    p['settings']['performance']={'queue_limit':-1}
    with pytest.raises(ValueError):validate_project(p)


def test_percentiles_empty_single_and_nearest_rank():
    assert latency([])['p99'] is None
    assert latency([4])=={'average':4,'median':4,'minimum':4,'maximum':4,'p95':4,'p99':4,'samples':1}
    r=latency(range(1,101));assert r['average']==50.5 and r['median']==50.5 and r['p95']==95 and r['p99']==99


def test_actual_batch_counts_partial_unknown_and_rate_idle_expiry():
    clock=[0.];p=Performance(clock=lambda:clock[0]);store=TransactionStore(performance=p)
    tags=[{'id':str(i),'tag':str(i)} for i in range(3)];tx=store.begin({},'write',tags)
    a=tx.batch(tags[:2]);assert p.totals['requests']==1 and p.totals['responses']==0
    tx.response(a,2);tx.point(tags[0],'SUCCEEDED','Good');tx.point(tags[1],'FAILED','Bad')
    b=tx.batch(tags[2:]);tx.finish(TimeoutError())
    assert p.totals['responses']==1 and p.totals['requests']==2
    assert p.totals['success']==1 and p.totals['failed']==1 and p.totals['unknown']==1
    assert p.totals['operation_failed']==1 and p.totals['operation_unknown']==1 and p.totals['operation_success']==0
    assert p.latencies()['samples']==2 and p.latencies()['minimum']>=0
    clock[0]=5;assert p.rates()['requests']==.4
    clock[0]=11;assert p.rates()['requests']==0
    clock[0]=61;assert p.latencies()['samples']==0


def test_metrics_are_bounded_and_alerts_clear(monkeypatch):
    monkeypatch.setattr('performance.resident_memory',lambda:(10*1024**2,'test measured'))
    p=Performance({'memory_mb':1,'loop_lag_ms':10,'cpu_percent':0});p.sample(20);p.sample(20)
    assert len(p.alerts)==2 and p.alerting=={'memory_mb','loop_lag_ms'}
    monkeypatch.setattr('performance.resident_memory',lambda:(None,'unavailable'))
    p.sample(0);assert p.history[-1]['memory_mb'] is None and not p.alerting
    assert len(p.alerts)==4
    assert any(a['metric']=='memory_mb' and a['state']=='UNAVAILABLE' for a in p.alerts)
    assert any(a['metric']=='loop_lag_ms' and a['state']=='CLEARED' for a in p.alerts)
    for i in range(4000):p.history.append({'time':i})
    for i in range(12000):p.durations.append((p.clock(),i))
    for i in range(600):p.alerts.append({'time':i})
    assert len(p.history)==3600 and len(p.durations)==10000 and len(p.alerts)==500


def test_current_process_memory_and_cpu_sampling():
    n,source=resident_memory();assert n is None or n>0
    p=Performance();r=p.sample();assert r['cpu_percent'] is None or r['cpu_percent']>=0
    assert r['active_tasks'] is None and source
    assert len(list(csv.reader(io.StringIO(p.csv()))))==2
    assert len(list(csv.reader(io.StringIO(p.csv())))[0])==len(list(csv.reader(io.StringIO(p.csv())))[1])
    json.dumps(p.snapshot(),allow_nan=False)


def test_concurrency_queue_overflow_cancellation_and_cleanup():
    async def run():
        p=Performance({'enabled':True,'concurrency':1,'queue_limit':1});gate=asyncio.Event();started=[]
        async def work(n):
            async with p.slot():started.append(n);await gate.wait()
        first=asyncio.create_task(work(1));await asyncio.sleep(.01)
        second=asyncio.create_task(work(2));await asyncio.sleep(.01);assert p.active==1 and p.waiting==1
        with pytest.raises(BackpressureError):
            async with p.slot():raise AssertionError('must not dispatch')
        assert p.rejected==1
        with pytest.raises(ValueError):p.configure({})
        second.cancel()
        with pytest.raises(asyncio.CancelledError):await second
        assert p.waiting==0 and started==[1]
        gate.set();await first;assert p.active==0 and p.wait_seconds>0
        p.configure({});assert not p.config['enabled']
    asyncio.run(run())


def test_disabled_controls_preserve_concurrency():
    async def run():
        p=Performance({'enabled':False,'concurrency':1,'queue_limit':0,'operations_per_second':1});gate=asyncio.Event()
        async def work():
            async with p.slot():await gate.wait()
        ts=[asyncio.create_task(work()) for _ in range(5)];await asyncio.sleep(.01)
        assert p.active==5 and p.waiting==0;gate.set();await asyncio.gather(*ts);assert p.active==0
    asyncio.run(run())


def test_rate_spacing_no_burst_and_wait_cancellation():
    async def run():
        p=Performance({'enabled':True,'concurrency':3,'operations_per_second':20});stamps=[]
        async def work():
            async with p.slot():stamps.append(time.monotonic())
        await asyncio.gather(*(work() for _ in range(4)))
        assert len(stamps)==4 and min(b-a for a,b in zip(stamps,stamps[1:]))>=.045
        task=asyncio.create_task(work());await asyncio.sleep(.005);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert p.active==p.waiting==0
    asyncio.run(run())


def test_rejected_write_has_no_attempted_points_and_no_dispatch():
    async def run():
        p=Performance({'enabled':True,'concurrency':1,'queue_limit':0});store=TransactionStore(performance=p)
        class Writer:
            evidence=store;config={}
            @tracked('write')
            async def write(self,tags,values):raise AssertionError('no dispatch')
        async with p.slot():
            w=Writer()
            with pytest.raises(BackpressureError):await w.write([{'id':'x'}],{'x':42})
            assert w.last_write_result['counts']['NOT_ATTEMPTED']==1
            assert w.last_write_result['counts']['UNKNOWN']==0 and not w.last_write_result['batches']
        assert p.totals['requests']==0
    asyncio.run(run())


def test_sampler_lifecycle_tasks_lag_and_export():
    async def run():
        p=Performance();await p.start()
        try:
            assert p.sample_task and p.history[-1]['active_tasks']>=1
            # Actual event-loop stall: sampler records the missed scheduled wakeup.
            await asyncio.sleep(.8);time.sleep(.3);await asyncio.sleep(.03)
            assert p.history[-1]['loop_lag_ms']>50 and len(p.history)>=2
            assert 'loop_lag_ms' in p.csv()
        finally:await p.stop()
        assert p.sample_task is None
    asyncio.run(run())


@pytest.mark.parametrize('protocol',['opcua','modbus'])
def test_real_batches_metrics_limits_and_verified_data(protocol):
    from test_live_monitor import setup,until
    from engine import Engine
    from drivers import adapter
    async def run():
        c,proj=setup(protocol);c.pop('url',None) if protocol=='modbus' else None
        c['performance']={'opcua_batch_size':2,'modbus_batch_units':4}
        c['tags']=[dict(c['tags'][0],id=f't{i}',tag=f'T{i}',nodeid=f'ns=2;s=T{i}',address=i*2) for i in range(7)]
        engine=Engine(proj);p=Performance({'enabled':True,'concurrency':1,'operations_per_second':100});store=TransactionStore(performance=p);context=STORE_CONTEXT.set(store);client=None
        try:
            await engine.start('server');await until(lambda:engine.states['server']['state']=='RUNNING')
            client=adapter(c);await client.connect();await client.prepare(c['tags'],write=True)
            values={t['id']:float(i+10) for i,t in enumerate(c['tags'])};await client.write(c['tags'],values);actual=await client.read(c['tags']);assert actual==values
            assert len(store.rows)==2 and all(len(r['batches'])==4 for r in store.rows)
            assert p.totals['requests']==p.totals['responses']==8 and p.totals['success']==14
            assert p.totals['operation_success']==2 and p.latencies()['samples']==8
            assert p.active==p.waiting==0
        finally:
            if client:await client.close()
            await engine.stop_all();STORE_CONTEXT.reset(context)
    asyncio.run(run())


def test_modbus_batches_preserve_whole_scalars_and_sparse_addresses():
    from drivers import Modbus
    c={'protocol':'modbus','host':'127.0.0.1','performance':{'modbus_batch_units':4}}
    async def run():
        m=Modbus(c);tags=[{'id':'a','address':0,'dtype':'Double'},{'id':'b','address':4,'dtype':'UInt16'},{'id':'c','address':6,'dtype':'UInt16'}]
        assert [g[2] for g in m.groups(tags)]==[4,1,1]
    asyncio.run(run())


def test_api_auth_export_settings_persistence_and_active_guard(tmp_path,monkeypatch):
    from aiohttp.test_utils import TestClient,TestServer
    import server
    from model import default_project
    async def run():
        monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(default_project())
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/api/performance')).status==403
            init=await(await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':init['token']}
            r=await(await client.get('/api/performance',headers=h)).json();assert r['latest'] and not r['settings']['enabled']
            assert (await client.get('/api/performance/export?format=bad',headers=h)).status==400
            r=await client.get('/api/performance/export?format=csv',headers=h);assert r.status==200 and 'requests_s' in await r.text()
            project=default_project();project['settings']['performance']={'enabled':True,'concurrency':2,'queue_limit':3,'operations_per_second':5}
            assert (await client.put('/api/project',json=project,headers=h)).status==200
            assert json.loads((tmp_path/'project.json').read_text())['settings']['performance']['concurrency']==2
            perf=app[server.PERFORMANCE_KEY]
            async with perf.slot():
                project['settings']['performance']['concurrency']=3
                assert (await client.put('/api/project',json=project,headers=h)).status==400
                assert perf.config['concurrency']==2
            assert (await client.put('/api/project',json=project,headers=h)).status==200
            r=await(await client.get('/api/performance/export',headers=h)).json();assert r['settings']['concurrency']==3
        assert app[server.PERFORMANCE_KEY].sample_task is None
    asyncio.run(run())
