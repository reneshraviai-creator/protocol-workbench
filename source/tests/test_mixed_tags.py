import asyncio,copy,json,sys,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from model import cast,generated,initial,validate_simulation,validate_project,default_project,dtype,encode,decode,view_value
from mixed_tags import generate,quantities


def connection(protocol='opcua'):return {'id':'c','name':'Mixed fixture','protocol':protocol,'url':'opc.tcp://127.0.0.1:4840','host':'127.0.0.1','port':1502,'device_id':1,'tags':[]}
def row(kind='Float',**kw):
    default=False if kind=='Boolean' else 'TEST' if kind=='String' else '2026-01-01T00:00:00Z' if kind=='DateTime' else 0
    r={'dtype':kind,'quantity':3,'prefix':kind,'default':default,'group':kind,'simulation':{'pattern':'fixed_timestamp' if kind=='DateTime' else 'constant','value':default}};r.update(kw);return r


@pytest.mark.parametrize('alias,canonical',[('SINT','SByte'),('Int8','SByte'),('UInt8','Byte'),('USINT','Byte'),('BYTE','Byte'),('WORD','UInt16'),('DWORD','UInt32'),('REAL','Float'),('Float32','Float'),('LREAL','Double'),('STRING','String')])
def test_canonical_aliases(alias,canonical):assert dtype(alias)==canonical


@pytest.mark.parametrize('kind,lo,hi',[('SByte',-128,127),('Byte',0,255)])
@pytest.mark.parametrize('order',['ABCD','BADC','CDAB','DCBA'])
def test_eight_bit_registers_and_limits(kind,lo,hi,order):
    for value in (lo,0,hi):assert decode(encode(value,kind,order),kind,order)==value
    for value in (lo-1,hi+1):
        with pytest.raises(ValueError):cast(value,kind)


@pytest.mark.parametrize('kind,pattern',[('Boolean','toggle'),('Boolean','random'),('Int16','increment'),('Int16','decrement'),('Int16','step'),('Float','sine'),('Double','ramp'),('String','constant'),('String','text_sequence'),('String','random_text'),('DateTime','current_timestamp'),('DateTime','fixed_timestamp'),('DateTime','increment_timestamp')])
def test_typed_patterns(kind,pattern):
    t={'id':'a','index':1,'dtype':kind,'simulation':{'pattern':pattern,'minimum':0,'maximum':10,'period':10,'step':1,'value':'2026-01-01T00:00:00Z' if kind=='DateTime' else 'TEXT' if kind=='String' else False if kind=='Boolean' else 0}}
    validate_simulation(t,{})
    for n in (0,1,9,1000000000):assert cast(generated(t,{},n),kind)==generated(t,{},n) if pattern!='current_timestamp' else isinstance(generated(t,{},n),datetime)
    if pattern=='increment':assert generated(t,{},1)==1
    if pattern=='decrement':assert generated(t,{},1)==9


def test_exact_uint64_increment_datetime_json_and_sequences():
    t={'dtype':'UInt64','simulation':{'pattern':'increment','minimum':'18446744073709551600','maximum':'18446744073709551615','step':1,'period':20}}
    validate_simulation(t,{});assert generated(t,{},14)==18446744073709551614
    for kind,seq in [('String',['a','b']),('DateTime',['2026-01-01T00:00:00Z','2026-01-02T00:00:00Z'])]:
        t={'dtype':kind,'simulation':{'pattern':'csv','sequence':seq}};validate_simulation(t,{})
        assert generated(t,{},1)==cast(seq[1],kind);json.dumps(view_value(generated(t,{},1),kind))
    with pytest.raises(ValueError):cast('2026-01-01T00:00:00','DateTime')
    with pytest.raises(ValueError):cast('1500-01-01T00:00:00Z','DateTime')
    with pytest.raises(ValueError):cast('a'*4097,'String')
    with pytest.raises(ValueError):validate_simulation({'dtype':'String','simulation':{'pattern':'sine'}},{})


def test_mixed_atomic_metadata_names_and_protocol_addresses():
    async def run():
        c=connection();before=copy.deepcopy(c);spec={'rows':[row(k) for k in ('Boolean','SByte','Byte','Int16','UInt16','Int32','UInt32','Int64','UInt64','Float','Double','String','DateTime')]}
        r=await generate(c,spec);assert c==before and r['created']==39
        assert len({t['id'] for t in r['tags']})==39 and r['tags'][0]['tag']=='Boolean_0001'
        for t in r['tags']:assert t['group']==t['dtype'] and t['simulation'] and 'initial_value' in t
        c=connection('modbus');r=await generate(c,{'rows':[row('Float'),row('Boolean',area='coil'),row('Double')]})
        assert [t['address'] for t in r['tags']]==[0,2,4,0,1,2,6,10,14]
        for kind in ('String','DateTime'):
            with pytest.raises(ValueError):await generate(c,{'rows':[row(kind)]})
        with pytest.raises(ValueError):await generate(c,{'rows':[row('Float'),row('Double',address=0)]})
        assert not c['tags']
    asyncio.run(run())


def test_percentage_rounding_exact_total_stable_ties():
    s={'mode':'percentage','total':7,'rows':[dict(row(),percentage=40),dict(row('Boolean'),percentage=30),dict(row('Int16'),percentage=30)]}
    assert quantities(s)==[3,2,2]
    s['total']=1;s['rows']=[dict(row('Float'),percentage=50),dict(row('Boolean'),percentage=50)];assert quantities(s)==[1,0]
    s['rows'][0]['percentage']=49
    with pytest.raises(ValueError):quantities(s)


def test_conflict_policies_never_overwrite_and_ids_unique():
    async def run():
        c=connection();one=await generate(c,{'rows':[row(quantity=1)]});c['tags']=one['tags'];before=copy.deepcopy(c)
        with pytest.raises(ValueError):await generate(c,{'rows':[row()]})
        r=await generate(c,{'rows':[row()],'conflicts':'skip'});assert r['skipped']==1 and r['created']==2
        r=await generate(c,{'rows':[row()],'conflicts':'rename'});assert r['renamed']==1 and r['tags'][0]['tag']=='Float_0001_1'
        assert c==before
        r=await generate(connection(),{'rows':[row(quantity=1,naming='{prefix}-{index}-{suffix}',prefix='P',suffix='PV',digits=2,start=5)]});assert r['tags'][0]['tag']=='P-05-PV'
        with pytest.raises(ValueError):await generate(c,{'rows':[row(naming='{unsupported}')]})
    asyncio.run(run())


def test_large_generation_yields_progress_and_cancels_atomically():
    async def run():
        c=connection();progress=[];task=asyncio.create_task(generate(c,{'rows':[row(quantity=20000)]},lambda n,t:progress.append(n)))
        await asyncio.sleep(0);await asyncio.sleep(0);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert progress and not c['tags']
        r=await generate(c,{'rows':[row(quantity=10000),row('Boolean',quantity=10000)]});assert r['created']==20000
        assert len({t['id'] for t in r['tags']})==20000
    asyncio.run(run())


@pytest.mark.parametrize('bad',[{'rows':[]},{'rows':[row(quantity=-1)]},{'rows':[row(quantity=20001)]},{'rows':[row(dtype='bad')]},{'rows':[row(prefix='\n')]},{'rows':[row(simulation={'pattern':'toggle'})]},{'mode':'bad','rows':[row()]},{'rows':[row(enabled='yes')]},{'rows':[row(group='x'*81)]}])
def test_invalid_mixed_no_partial(bad):
    async def run():
        c=connection()
        with pytest.raises((ValueError,KeyError,TypeError)):await generate(c,bad)
        assert not c['tags']
    asyncio.run(run())


@pytest.mark.parametrize('protocol',['opcua','modbus'])
def test_real_typed_simulation_pause_resume_schedule_duration_history(protocol):
    from test_live_monitor import setup,until
    from engine import Engine
    from drivers import adapter
    async def run():
        base,p=setup(protocol);base.pop('url',None) if protocol=='modbus' else None
        kinds=['Boolean','SByte','Byte','Int16','Float']+(['String','DateTime'] if protocol=='opcua' else [])
        result=await generate(base,{'rows':[row(k,quantity=1,default='42' if k not in ('Boolean','String','DateTime') else row(k)['default'],simulation={'pattern':'fixed_timestamp' if k=='DateTime' else 'constant','value':'42' if k not in ('Boolean','String','DateTime') else row(k)['default']}) for k in kinds]})
        base['tags']=result['tags'];p['jobs'][0].update(interval=.05,dynamic=True);p['jobs'][0]['schedule_at']=(datetime.now(timezone.utc)+timedelta(seconds=.3)).isoformat();p['jobs'][0]['duration_seconds']=1.5
        e=Engine(p);client=None
        try:
            await e.start('server');await until(lambda:e.states['server']['state']=='SCHEDULED');assert not e.states['server'].get('started')
            await until(lambda:e.states['server']['state']=='RUNNING');client=adapter(base);await client.connect();await client.prepare(base['tags'],write=True);values=await client.read(base['tags']);assert values[result['tags'][1]['id']]==42
            await e.pause('server');await until(lambda:e.states['server']['state']=='PAUSED');cycles=e.states['server']['cycles'];await asyncio.sleep(.1);assert e.states['server']['cycles']==cycles and e.registry.snapshot()
            await e.resume('server');await until(lambda:e.states['server']['cycles']>cycles)
            # Verify actual client writes and readback for every supported generated datatype.
            wanted={t['id']:cast(t['initial_value'],t['dtype']) for t in base['tags']};await client.write(base['tags'],wanted);got=await client.read(base['tags']);assert got==wanted
            await client.close();client=None;await until(lambda:e.tasks['server'].done());assert e.states['server']['state']=='COMPLETED';assert e.run_history[-1]['state']=='COMPLETED' and not e.registry.snapshot()
        finally:
            if client:await client.close()
            await e.stop_all()
    asyncio.run(run())


def test_string_datetime_verification_and_json():
    from verification import Session
    ts=[{'id':'s','tag':'S','dtype':'String'},{'id':'d','tag':'D','dtype':'DateTime'}];v={'s':'T'*300,'d':cast('2026-01-01T00:00:00Z','DateTime')};s=Session({'id':'j','kind':'simulation','verification':{'mode':'full','check_quality':False}},ts,'opcua');r=s.evaluate(ts,v,v,{k:{'value':v[k],'datatype':t['dtype']} for k,t in zip(v,ts)});assert r['outcome']=='PASS';assert r['points'][0]['actual']=='T'*300;json.dumps(r)
    with pytest.raises(ValueError):Session({'id':'j','kind':'simulation','verification':{'mode':'full','minimum':0}},ts,'opcua')


def test_generation_api_preview_confirmation_cancel_templates(tmp_path,monkeypatch):
    from aiohttp.test_utils import TestClient,TestServer
    import server
    async def run():
        monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(default_project())
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/api/mixed-templates')).status==403
            b=await(await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']};c=connection();spec={'rows':[row(),row('Boolean')]}
            async def call(path,body):return await client.post('/api/'+path,json=body,headers=h)
            start=await(await call('mixed-generation',{'connection':c,'spec':spec})).json();key=start['id']
            for _ in range(100):
                r=await(await client.get('/api/mixed-generation/'+key,headers=h)).json()
                if r['state']!='GENERATING':break
                await asyncio.sleep(.01)
            assert r['state']=='READY' and r['summary']['created']==6
            assert (await call('mixed-generation/'+key+'/accept',{'connection':c,'spec':spec})).status==400
            changed=copy.deepcopy(spec);changed['rows'][0]['quantity']=4
            assert (await call('mixed-generation/'+key+'/accept',{'confirm':True,'connection':c,'spec':changed})).status==400
            r=await(await call('mixed-generation/'+key+'/accept',{'confirm':True,'connection':c,'spec':spec})).json();assert len(r['tags'])==6
            assert not app['engine'].project['connections']
            t=await(await call('mixed-templates',{'name':'Example','protocol':'opcua','spec':spec})).json();assert t['id']
            t['name']='Edited';assert (await call('mixed-templates',t)).status==200
            duplicate=await(await call('mixed-templates',{'name':'Copy','protocol':'opcua','spec':spec})).json();assert duplicate['id']!=t['id']
            assert (await call('mixed-templates/'+t['id']+'/delete',{})).status==400
            assert (await call('mixed-templates/'+t['id']+'/delete',{'confirm':True})).status==200
            items=await(await client.get('/api/mixed-templates',headers=h)).json();assert len(items['templates'])==1 and list((tmp_path/'mixed-templates'/'trash').glob('*.json'))
            start=await(await call('mixed-generation',{'connection':c,'spec':{'rows':[row(quantity=20000)]}})).json();assert (await call('mixed-generation/'+start['id']+'/cancel',{})).status==200
            r=await(await client.get('/api/mixed-generation/'+start['id'],headers=h)).json();assert r['state']=='CANCELLED' and 'summary' not in r
    asyncio.run(run())


def test_client_simulation_pause_selective_activation_and_stop_scheduled():
    from test_live_monitor import setup,until
    from engine import Engine
    from drivers import adapter
    async def run():
        c,p=setup('modbus');c.pop('url',None);c['tags']=[dict(c['tags'][0],initial_value=0,simulation={'pattern':'constant','value':0}),dict(c['tags'][0],id='disabled',tag='Disabled',address=2,initial_value=7,simulation={'pattern':'constant','value':7})];p['jobs'][0]['dynamic']=False
        e=Engine(p);dest=copy.deepcopy(c);dest['id']='writer';dest['tags'][0]['simulation']={'pattern':'increment','minimum':10,'maximum':100,'period':20,'step':1};dest['tags'][0]['initial_value']=10;dest['tags'][1]['enabled']=False
        project=default_project();project['connections']=[dest];project['jobs']=[{'id':'w','name':'Writer','kind':'simulation','connection':'writer','interval':.1,'verify':True}];w=Engine(project);client=None
        try:
            await e.start('server');await until(lambda:e.states['server']['state']=='RUNNING');await w.start('w');await until(lambda:w.states['w']['cycles']>=1)
            assert w.states['w']['points']==1
            await w.pause('w');await until(lambda:w.states['w']['state']=='PAUSED');n=w.states['w']['cycles'];client=adapter(c);await client.connect();await client.prepare(c['tags']);assert (await client.read(c['tags']))['disabled']==7
            await asyncio.sleep(.12);assert w.states['w']['cycles']==n;await w.resume('w');await until(lambda:w.states['w']['cycles']>n);await w.stop('w');assert not w.registry.snapshot()
            project['jobs'][0]['schedule_at']=(datetime.now(timezone.utc)+timedelta(seconds=10)).isoformat();await w.start('w');await until(lambda:w.states['w']['state']=='SCHEDULED');await w.stop('w');assert w.states['w']['state']=='STOPPED' and not w.registry.snapshot()
        finally:
            if client:await client.close()
            await w.stop_all();await e.stop_all()
    asyncio.run(run())
