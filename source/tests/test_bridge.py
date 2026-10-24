import asyncio,copy,json,sys,time
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from bridge import converted,evaluate,expression,filtered,options,validate_mapping,Session,BridgeStore,STORE
from model import cast,default_project,validate_project


def tag(kind='Double',id='t'):return {'id':id,'tag':id,'dtype':kind,'nodeid':'ns=2;s='+id,'address':0,'area':'holding','order':'ABCD'}
def cfg(protocol='opcua',id='c',kind='Double'):return {'id':id,'name':id,'protocol':protocol,'url':'opc.tcp://127.0.0.1:4840','host':'127.0.0.1','port':1502,'device_id':1,'tags':[tag(kind)]}


@pytest.mark.parametrize('unit,value,wanted',[('C_to_F',25,77),('F_to_C',212,100),('bar_to_kPa',2,200),('kPa_to_bar',250,2.5),('Pa_to_bar',100000,1),('ms_to_s',2500,2.5),('s_to_ms',1.5,1500),('rpm_to_Hz',120,2),('Hz_to_rpm',3,180),('percent_to_fraction',50,.5),('fraction_to_percent',.2,20)])
def test_units(unit,value,wanted):assert converted(value,{'unit':unit},tag(),tag())==wanted


@pytest.mark.parametrize('text,x,wanted',[('x * 2 + 1',Decimal(3),Decimal(7)),('max(0, min(x, 100))',Decimal(120),Decimal(100)),('abs(x)',Decimal(-3),Decimal(3)),('1 if x > 2 else 0',Decimal(4),Decimal(1)),('x != 0 and 10/x > 1',Decimal(0),False),('x == 0 or 10/x > 1',Decimal(0),True),('x + "_OK"','RUN','RUN_OK')])
def test_interpreted_expressions(text,x,wanted):assert evaluate(text,x)==wanted


@pytest.mark.parametrize('text',['__import__("os")','x.__class__','x[0]','[x for x in ()]','x ** 1000000','lambda: x','open("x")','min(x, y=1)','abs()','x; x','a+1','"x"'*200])
def test_unsafe_expressions_rejected(text):
    with pytest.raises(ValueError):expression(text)


def test_range_type_string_datetime_and_boolean_conversion():
    a=tag();b=tag('Int16')
    assert converted(10,{'scale':2,'offset':3,'minimum':0,'maximum':20},a,b)==20
    assert converted(5,{'expression':'x+1','scale':2},a,b)==12
    assert converted('18446744073709551614',{},tag('String'),tag('UInt64'))==18446744073709551614
    assert converted('RUN',{'expression':'x+"_OK"'},tag('String'),tag('String'))=='RUN_OK'
    d=cast('2026-01-01T00:00:00Z','DateTime');assert converted(d,{},tag('DateTime'),tag('String'))==d.isoformat();assert converted(d.isoformat(),{},tag('String'),tag('DateTime'))==d
    assert converted('true',{},tag('String'),tag('Boolean')) is True
    assert converted('RUN',{'boolean_mode':'equals','true_value':'RUN'},tag('String'),tag('Boolean')) is True
    assert converted(5,{'boolean_mode':'threshold','threshold':10,'scale':2},a,tag('Boolean')) is True
    assert converted(7,{'boolean_mode':'nonzero'},a,tag('Boolean')) is True
    assert converted(False,{'boolean_mode':'invert'},tag('Boolean'),tag('Boolean')) is True
    for m,source,dest in [({'expression':'x/0'},a,b),({},tag('String'),b),({'unit':'none','expression':'x+1'},tag('DateTime'),tag('DateTime')),({'boolean_mode':'threshold'},a,b)]:
        with pytest.raises(ValueError):converted('bad' if source['dtype']=='String' else d if source['dtype']=='DateTime' else 2,m,source,dest)


def test_filters_on_change_and_invalid_boolean_result():
    assert filtered(2,{'filter_minimum':3})=='filter_minimum'
    assert filtered(4,{'filter_maximum':3})=='filter_maximum'
    assert filtered(2,{'filter_expression':'x > 3'})=='filter_expression'
    assert filtered(2,{'on_change':True},2,True).startswith('Unchanged')
    assert not filtered(2,{'on_change':True},2,False)
    with pytest.raises(ValueError):filtered(2,{'filter_expression':'x+1'})


@pytest.mark.parametrize('profile',[{'preserve_timestamp':True},{'preserve_quality':True},{'bad_quality':'preserve','preserve_quality':True}])
def test_modbus_cannot_claim_ua_semantics(profile):
    with pytest.raises(ValueError):options({'bridge':profile},cfg(),cfg('modbus'))
    with pytest.raises(ValueError):options({'bridge':profile},cfg('modbus'),cfg())


def test_cycle_atomic_errors_unchanged_cache_metadata_and_unknown():
    a=cfg(id='a');b=cfg(id='b');a['tags']=[tag(id='a'),tag(id='b')];b['tags']=[tag(id='x'),tag(id='y')]
    j={'id':'j','mappings':[{'source':'a','destination':'x'},{'source':'b','destination':'y','expression':'x/0'}]};s=Session(j,a,b,{})
    s.begin()
    with pytest.raises(ValueError):s.plan({'a':1,'b':2},{})
    s.finish(error=ValueError());assert s.last['counts']=={'NOT_ATTEMPTED':1,'TRANSFORM_ERROR':1}
    j['mappings']=j['mappings'][:1];j['mappings'][0]['on_change']=True;j['bridge']={'preserve_timestamp':True,'preserve_quality':True};s=Session(j,a,b,{})
    obs={'a':{'good':True,'quality':'Good','status_code':0,'source_timestamp':1000}}
    s.begin();ts,values,meta=s.plan({'a':3},obs,'read');s.finish({'id':'write','points':[{'id':'x','outcome':'UNKNOWN','status':'No response'}]});assert not s.previous
    s.begin();ts,values,meta=s.plan({'a':3},obs);assert values;s.finish({'id':'write2','points':[{'id':'x','outcome':'SUCCEEDED','status':'Good'}]})
    s.begin();assert not s.plan({'a':3},obs)[1];s.finish();assert s.last['counts']=={'FILTERED':1}
    obs['a']['source_timestamp']=1001;s.begin();assert s.plan({'a':3},obs)[1];s.finish()
    s.reconnected();s.begin();assert s.plan({'a':3},obs)[1];s.finish()


def bridge_project(source_protocol='opcua',destination_protocol='modbus',profile=None,mapping=None):
    from test_live_monitor import setup
    a,_=setup(source_protocol);b,_=setup(destination_protocol)
    if source_protocol=='modbus':a.pop('url',None)
    if destination_protocol=='modbus':b.pop('url',None)
    a['id']='src-server';b['id']='dst-server'
    for c in (a,b):c['tags'][0]['initial_value']=25;c['tags'][0]['simulation']={'pattern':'constant','value':25}
    source=copy.deepcopy(a);source['id']='src';dest=copy.deepcopy(b);dest['id']='dst';p=default_project();p['connections']=[a,b,source,dest];p['jobs']=[{'id':'a','name':'Source','kind':'server','connection':a['id'],'interval':.1,'dynamic':False},{'id':'b','name':'Destination','kind':'server','connection':b['id'],'interval':.1,'dynamic':False},{'id':'route','name':'Bridge','kind':'gateway','source':source['id'],'connection':dest['id'],'interval':.1,'verify':True,'mappings':[{'source':'t','destination':'t',**(mapping or {})}]}]
    if profile:p['jobs'][-1]['bridge']=profile
    return p,source,dest


@pytest.mark.parametrize('direction',[('opcua','modbus'),('modbus','opcua'),('opcua','opcua'),('modbus','modbus')])
def test_real_gateway_units_filters_verification_and_delivery(direction):
    from engine import Engine
    from test_live_monitor import until
    from drivers import adapter
    async def run():
        p,source,dest=bridge_project(*direction,mapping={'unit':'C_to_F','on_change':True});validate_project(p);store=BridgeStore();context=STORE.set(store);e=Engine(p);client=None
        try:
            for ident in ('a','b'):await e.start(ident);await until(lambda:e.states[ident]['state']=='RUNNING')
            await e.start('route');await until(lambda:e.states['route']['cycles']>=2)
            client=adapter(dest);await client.connect();await client.prepare(dest['tags']);assert (await client.read(dest['tags']))['t']==77
            assert any(r['counts'].get('SUCCEEDED')==1 for r in store.rows) and any(r['counts'].get('FILTERED')==1 for r in store.rows)
            first=next(r for r in store.rows if r['counts'].get('SUCCEEDED'));assert first['source_transaction'] and first['write_transaction'] and first['duration_ms']>0
            point=first['points'][0]
            for side,protocol in zip(('source','destination'),direction):
                assert point[side+'_target'].startswith('ns=' if protocol=='opcua' else 'holding:')
            assert e.states['route']['state'] in ('RUNNING','OVERRUN')
            await e.stop('route');assert not any(x['owner']=='job:route' for x in e.registry.snapshot())
        finally:
            if client:await client.close()
            await e.stop_all();STORE.reset(context)
    asyncio.run(run())


def test_real_ua_timestamp_and_quality_forwarding():
    from engine import Engine
    from test_live_monitor import until
    from drivers import adapter
    from asyncua import ua
    async def run():
        p,source,dest=bridge_project('opcua','opcua',{'bad_quality':'preserve','preserve_timestamp':True,'preserve_quality':True});p['jobs'][-1]['verify']=False;e=Engine(p);writer=reader=None;store=BridgeStore();context=STORE.set(store)
        try:
            for ident in ('a','b'):await e.start(ident);await until(lambda:e.states[ident]['state']=='RUNNING')
            writer=adapter(source);await writer.connect();await writer.prepare(source['tags'],True)
            stamp=1700000000.;status=ua.StatusCodes.Uncertain;await writer.write(source['tags'],{'t':42},metadata={'t':{'source_timestamp':stamp,'status_code':status}})
            await e.start('route');await until(lambda:e.states['route']['cycles']>=1)
            reader=adapter(dest);await reader.connect();await reader.prepare(dest['tags']);assert (await reader.read(dest['tags'],allow_bad_quality=True))['t']==42
            ob=reader.last_observations['t'];assert ob['status_code']==status and ob['source_timestamp']==stamp
            assert store.rows[-1]['counts']['SUCCEEDED']==1
        finally:
            if writer:await writer.close()
            if reader:await reader.close()
            await e.stop_all();STORE.reset(context)
    asyncio.run(run())


def test_real_transform_failure_never_writes_destination():
    from engine import Engine
    from test_live_monitor import until
    from drivers import adapter
    async def run():
        p,source,dest=bridge_project(mapping={'expression':'x/0'});store=BridgeStore();context=STORE.set(store);e=Engine(p);client=None
        try:
            for ident in ('a','b'):await e.start(ident);await until(lambda:e.states[ident]['state']=='RUNNING')
            await e.start('route');await until(lambda:e.tasks['route'].done());assert e.states['route']['state']=='ERROR';assert store.rows[-1]['counts']['TRANSFORM_ERROR']==1 and not store.rows[-1]['write_transaction']
            client=adapter(dest);await client.connect();await client.prepare(dest['tags']);assert (await client.read(dest['tags']))['t']==25
        finally:
            if client:await client.close()
            await e.stop_all();STORE.reset(context)
    asyncio.run(run())


def test_string_datetime_bridge_model_and_real_ua_identity():
    from engine import Engine
    from test_live_monitor import until
    from drivers import adapter
    async def run():
        p,source,dest=bridge_project('opcua','opcua');stamp='2026-01-01T00:00:00Z'
        for c in p['connections']:
            c['tags']=[dict(tag('String','s'),initial_value='RUN',simulation={'pattern':'constant','value':'RUN'}),dict(tag('DateTime','d'),address=2,initial_value=stamp,simulation={'pattern':'fixed_timestamp','value':stamp})]
        p['jobs'][-1]['mappings']=[{'source':'s','destination':'s','expression':'x+"_OK"'},{'source':'d','destination':'d'}];validate_project(p);e=Engine(p);client=None
        try:
            for ident in ('a','b'):await e.start(ident);await until(lambda:e.states[ident]['state']=='RUNNING')
            await e.start('route');await until(lambda:e.states['route']['cycles']>=1);client=adapter(dest);await client.connect();await client.prepare(dest['tags']);r=await client.read(dest['tags']);assert r['s']=='RUN_OK' and r['d']==cast(stamp,'DateTime')
        finally:
            if client:await client.close()
            await e.stop_all()
    asyncio.run(run())


def test_api_auth_offline_preview_evidence_and_template_resolution(tmp_path,monkeypatch):
    from aiohttp.test_utils import TestClient,TestServer
    import server
    async def run():
        monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(default_project())
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/api/bridges')).status==403
            init=await(await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':init['token']}
            r=await client.post('/api/bridges/preview',json={'source_tag':tag(),'destination_tag':tag(),'mapping':{'unit':'C_to_F'},'value':25},headers=h);assert (await r.json())['value']==77
            template={'version':1,'name':'Example','url':'must-not-save','password':'must-not-save','mappings':[{'source_tag':'t','destination_tag':'t','source_dtype':'Double','destination_dtype':'Double','source_address':'old','unit':'C_to_F'}]}
            r=await client.post('/api/bridge-templates',json=template,headers=h);assert r.status==200;t=await r.json();assert 'password' not in t and 'url' not in t
            r=await client.post('/api/bridge-templates/resolve',json={'template':t,'source':cfg(id='s'),'destination':cfg(id='d')},headers=h);data=await r.json();assert data['mappings'][0]['source']=='t' and data['address_changes']
            bad=cfg(id='bad');bad['tags'][0]['dtype']='Float';assert (await client.post('/api/bridge-templates/resolve',json={'template':t,'source':bad,'destination':cfg(id='d')},headers=h)).status==400
            t['name']='Edited';assert (await client.post('/api/bridge-templates',json=t,headers=h)).status==200
            assert (await client.post('/api/bridge-templates/'+t['id']+'/delete',json={},headers=h)).status==400
            assert (await client.post('/api/bridge-templates/'+t['id']+'/delete',json={'confirm':True},headers=h)).status==200
            store=app[server.BRIDGE_KEY];s=Session({'id':'j','mappings':[{'source':'t','destination':'t'}]},cfg(id='s'),cfg(id='d'),{});s.store=store;s.begin();s.plan({'t':3},{});s.finish({'id':'w','points':[{'id':'t','outcome':'UNKNOWN','status':'No response'}]})
            r=await(await client.get('/api/bridges',headers=h)).json();assert r['rows'][0]['counts']['UNKNOWN']==1
            key=r['rows'][0]['id'];assert (await client.get('/api/bridges/'+key,headers=h)).status==200
            assert (await client.get('/api/bridges/export?format=csv',headers=h)).status==200
            assert (await client.get('/api/bridges/export?format=bad',headers=h)).status==400
            assert (await client.get('/api/bridges?page=-1',headers=h)).status==400
            assert not app['engine'].tasks
    asyncio.run(run())


def test_real_bad_quality_filter_skips_without_verifying_unwritten_points():
    from engine import Engine
    from test_live_monitor import until
    from drivers import adapter
    from asyncua import ua
    async def run():
        p,source,dest=bridge_project('opcua','modbus',{'bad_quality':'filter'});store=BridgeStore();context=STORE.set(store);e=Engine(p);writer=reader=None
        try:
            for ident in ('a','b'):await e.start(ident);await until(lambda:e.states[ident]['state']=='RUNNING')
            writer=adapter(source);await writer.connect();await writer.prepare(source['tags'],True);await writer.write(source['tags'],{'t':42},metadata={'t':{'status_code':ua.StatusCodes.BadSensorFailure}})
            await e.start('route');await until(lambda:e.states['route']['cycles']>=2 or e.tasks['route'].done())
            assert e.states['route']['cycles']>=2,e.snapshot()
            assert store.rows[-1]['counts']=={'FILTERED':1} and store.rows[-1]['write_transaction'] is None
            reader=adapter(dest);await reader.connect();await reader.prepare(dest['tags']);assert (await reader.read(dest['tags']))['t']==25
        finally:
            if writer:await writer.close()
            if reader:await reader.close()
            await e.stop_all();STORE.reset(context)
    asyncio.run(run())


def test_retry_link_metadata_and_bounded_retention():
    source=cfg(id='a');dest=cfg(id='b');s=Session({'id':'r','bridge':{'preserve_timestamp':True},'mappings':[{'source':'t','destination':'t'}]},source,dest,{})
    s.store=BridgeStore();s.begin();ts,values,meta=s.plan({'t':2},{'t':{'source_timestamp':1000}},'read')
    s.finish({'id':'w1','points':[{'id':'t','outcome':'FAILED','status':'Busy'}]});first=s.last['id'];s.retry(ts,values,meta);s.finish({'id':'w2','points':[{'id':'t','outcome':'SUCCEEDED','status':'Good'}]})
    assert s.last['retry_of']==first and s.last['source_transaction']=='read' and s.last['points'][0]['requested_metadata']==meta['t'] and s.previous['t']==2
    for _ in range(205):
        s.begin();s.plan({'t':2},{'t':{'source_timestamp':1000}});s.finish()
    assert len(s.store.rows)==200 and s.store.evicted==7 and s.store.points==200


def test_disabled_mapping_does_not_prepare_unavailable_nodes():
    from engine import Engine
    from test_live_monitor import until
    async def run():
        p,source,dest=bridge_project('opcua','opcua');source['tags'].append(tag('Double','unavailable'));dest['tags'].append(tag('Double','unavailable'));p['jobs'][-1]['mappings'].append({'source':'unavailable','destination':'unavailable','enabled':False});validate_project(p)
        store=BridgeStore();context=STORE.set(store);e=Engine(p)
        try:
            for ident in ('a','b'):await e.start(ident);await until(lambda:e.states[ident]['state']=='RUNNING')
            await e.start('route');await until(lambda:e.states['route']['cycles']>=1 or e.tasks['route'].done());assert e.states['route']['cycles']>=1,e.states['route'];assert store.rows[-1]['counts']=={'SUCCEEDED':1,'FILTERED':1}
        finally:await e.stop_all();STORE.reset(context)
    asyncio.run(run())


@pytest.mark.parametrize('operation',['read','write'])
def test_library_converted_cancellation_stops_without_recovery_or_replay(operation):
    from transactions import TransactionStore,CURRENT_TRANSACTION,tracked
    from pymodbus.exceptions import ModbusIOException
    async def run():
        gate=asyncio.Event();store=TransactionStore()
        class Client:
            config={};evidence=store
            @tracked(operation)
            async def call(self,tags):
                CURRENT_TRANSACTION.get().batch(tags);gate.set()
                try:await asyncio.sleep(60)
                except asyncio.CancelledError as error:raise ModbusIOException('Request cancelled outside pymodbus') from error
        c=Client()
        async def work():
            try:await c.call([tag()])
            except asyncio.CancelledError:
                # An attempted follow-on call in the same cancelling task must not dispatch.
                await c.call([tag()])
        task=asyncio.create_task(work());await gate.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,1)
        assert store.rows[0]['cancelled'] and store.rows[0]['counts']['UNKNOWN']==1
        assert store.rows[-1]['cancelled'] and store.rows[-1]['counts']['NOT_ATTEMPTED']==1 and not store.rows[-1]['batches']
        if operation=='write':assert c.last_write_result['cancelled']
    asyncio.run(run())
