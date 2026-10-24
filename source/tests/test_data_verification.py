import asyncio,copy,csv,io,json,math,sys,time
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from verification import Session,VerificationStore,STORE_CONTEXT,settings,selected
from model import cast,validate_project


def tags(n=4,kind='Float'):
    return [{'id':f't{i}','tag':f'Tag{i}','dtype':kind,'nodeid':f'ns=2;s=Tag{i}','area':'holding','address':i*2,'order':'ABCD'} for i in range(n)]


def session(profile=None,kind='Float',n=4):
    ts=tags(n,kind);job={'id':'j','name':'Verification','kind':'simulation','verify':True}
    if profile is not None:job['verification']=profile
    s=Session(job,ts,'opcua');s.store=VerificationStore();return s,ts


def observed(values,kind='Float',stamp=100):
    return {k:{'value':v,'good':True,'quality':'Good','source_timestamp':stamp,'datatype':kind} for k,v in values.items()}


@pytest.mark.parametrize('mode,count',[('off',0),('basic',2),('sampled',3),('full',9),('selected',2),('continuous',9)])
def test_modes(mode,count):
    ts=tags(9);c=settings({'kind':'simulation','verification':{'mode':mode,'sample_count':3,'selected':['t1','t8']}},ts)
    chosen=selected(ts,c);assert len(chosen)==count and len({t['id'] for t in chosen})==count
    if mode=='sampled':assert [t['id'] for t in chosen]==['t0','t4','t8']


def test_legacy_selection_verify_false_and_interval():
    s,ts=session();assert s.config['legacy'] and s.config['mode']=='basic'
    assert settings({'kind':'scan'},ts)['mode']=='off'
    assert settings({'verify':False},ts)['mode']=='off'
    s,ts=session({'mode':'full','interval_seconds':60});assert s.due();v={t['id']:1.0 for t in ts};s.evaluate(ts,v,v,observed(v));assert not s.due()
    s.config['mode']='continuous';assert s.due()


@pytest.mark.parametrize('profile',[
 {'mode':'invalid'},{'comparison':'near'},{'on_failure':'ignore'},{'sample_count':0},{'sample_count':1.5},
 {'max_failed_points':-1},{'absolute_tolerance':-1},{'relative_tolerance':2},{'interval_seconds':float('nan')},
 {'minimum':5,'maximum':2},{'selected':['absent']},{'selected':['t0','t0']},{'mode':'selected','selected':[]},
 {'require_timestamp':'yes'},{'future_tolerance_seconds':-1},{'new_unknown_setting':True},{'legacy':False},
 {'sequence_step':True},{'max_age_seconds':0},{'unchanged_seconds':0}])
def test_invalid_settings(profile):
    with pytest.raises(ValueError):settings({'kind':'simulation','verification':profile},tags())


@pytest.mark.parametrize('key',['require_timestamp','check_timestamp_order','check_future_timestamp','max_age_seconds'])
def test_modbus_timestamp_settings_rejected(key):
    with pytest.raises(ValueError,match='OPC UA'):settings({'kind':'scan','verification':{key:1 if key=='max_age_seconds' else True}},tags(),'modbus')


def test_server_verification_rejected_and_selected_must_be_job_subset():
    with pytest.raises(ValueError):settings({'kind':'server','verification':{'mode':'full'}},tags())
    with pytest.raises(ValueError):settings({'kind':'scan','verification':{'mode':'selected','selected':['t3']}},tags(2))


def test_exact_tolerance_and_no_integer_precision_loss():
    s,ts=session({'mode':'full','comparison':'tolerance','absolute_tolerance':.001});a={t['id']:1.00001 for t in ts};e={t['id']:1.0 for t in ts}
    assert s.evaluate(ts,a,e,observed(a))['outcome']=='PASS'
    s.config['comparison']='exact';r=s.evaluate(ts,a,e,observed(a));assert r['counts']['FAIL']==4 and all('VALUE_MISMATCH' in p['issues'] for p in r['points'])
    s,ts=session({'mode':'full','comparison':'tolerance','absolute_tolerance':100000},'UInt64',1);a={'t0':2**64-1};e={'t0':2**64-2}
    r=s.evaluate(ts,a,e,observed(a,'UInt64'));assert r['outcome']=='FAIL';assert r['points'][0]['actual']==str(2**64-1)


@pytest.mark.parametrize('kind,value,issue',[('Boolean',1,'INVALID_DATATYPE_OR_VALUE'),('Int16',1.5,'INVALID_DATATYPE_OR_VALUE'),('UInt16',-1,'INVALID_DATATYPE_OR_VALUE'),('Float',float('nan'),'INVALID_DATATYPE_OR_VALUE'),('Float',float('inf'),'INVALID_DATATYPE_OR_VALUE'),('Float',[1],'INVALID_DATATYPE_OR_VALUE'),('Float',None,'NULL_VALUE'),('Float','1','INVALID_DATATYPE_OR_VALUE')])
def test_invalid_null_and_datatype(kind,value,issue):
    s,ts=session({'mode':'full'},kind,1);v={'t0':value};r=s.evaluate(ts,v,None,observed(v,kind));assert issue in r['points'][0]['issues'];assert r['outcome']=='FAIL';json.dumps(r,allow_nan=False)


def test_missing_and_unknown_are_not_confirmed_failures():
    s,ts=session({'mode':'full'},n=2);r=s.evaluate(ts,{},read_error=TimeoutError());assert r['outcome']=='INCONCLUSIVE' and r['counts']['FAIL']==0 and r['counts']['UNVERIFIED']==2
    assert all('READ_ERROR' in p['issues'] for p in r['points'])
    s,ts=session({'mode':'full'},n=1);r=s.evaluate(ts,{'t0':1.0},observations={});assert r['outcome']=='INCONCLUSIVE' and 'QUALITY_UNAVAILABLE' in r['points'][0]['issues']


def test_metadata_quality_and_type_and_timestamp_errors():
    s,ts=session({'mode':'full','require_timestamp':True,'max_age_seconds':5,'check_future_timestamp':True,'check_timestamp_order':True},n=2)
    a={'t0':1.0,'t1':2.0};o=observed(a,stamp=99);assert s.evaluate(ts,a,observations=o,now=100)['outcome']=='PASS'
    o=observed(a,stamp=90);o['t0']['good']=False;o['t0']['quality']='Bad';o['t1']['datatype']='Double'
    r=s.evaluate(ts,a,observations=o,now=100);assert 'BAD_QUALITY' in r['points'][0]['issues'];assert 'DATATYPE_MISMATCH' in r['points'][1]['issues'];assert 'STALE_TIMESTAMP' in r['points'][1]['issues'];assert 'OUT_OF_ORDER_TIMESTAMP' in r['points'][1]['issues']
    o=observed(a,stamp=200);r=s.evaluate(ts,a,observations=o,now=100);assert 'FUTURE_TIMESTAMP' in r['points'][0]['issues']
    o=observed(a,stamp=None);r=s.evaluate(ts,a,observations=o,now=100);assert r['outcome']=='INCONCLUSIVE' and 'MISSING_TIMESTAMP' in r['points'][0]['issues']
    o=observed(a,stamp=float('nan'));r=s.evaluate(ts,a,observations=o,now=100);json.dumps(r,allow_nan=False);assert 'INVALID_TIMESTAMP' in r['points'][0]['issues']


def test_duplicate_unchanged_sequence_and_range_opt_in():
    s,ts=session({'mode':'full','check_duplicates':True,'unchanged_seconds':2,'sequence_step':1,'minimum':0,'maximum':5},n=2)
    a={'t0':1.0,'t1':1.0};r=s.evaluate(ts,a,observations=observed(a),now=100);assert all('DUPLICATE_VALUE' in p['issues'] for p in r['points']);assert r['counts']['FAIL']==2
    r=s.evaluate(ts,a,observations=observed(a),now=103);assert all('UNCHANGED_VALUE' in p['issues'] and 'OUT_OF_SEQUENCE_VALUE' in p['issues'] for p in r['points'])
    a={'t0':-1.0,'t1':6.0};r=s.evaluate(ts,a,observations=observed(a),now=104);assert 'BELOW_RANGE' in r['points'][0]['issues'] and 'ABOVE_RANGE' in r['points'][1]['issues']
    s,ts=session({'mode':'full'},n=2);assert s.evaluate(ts,{'t0':1.0,'t1':1.0},observations=observed({'t0':1.0,'t1':1.0}))['outcome']=='PASS'


def test_integer_sequence_and_range_preserve_large_values():
    s,ts=session({'mode':'full','sequence_step':'1','minimum':str(2**63),'maximum':str(2**64-1)},'UInt64',1)
    v={'t0':2**64-2};assert s.evaluate(ts,v,observations=observed(v,'UInt64'))['outcome']=='PASS'
    v={'t0':2**64-1};r=s.evaluate(ts,v,observations=observed(v,'UInt64'));assert r['outcome']=='PASS' and r['settings']['maximum']==str(2**64-1)


def test_unselected_and_failure_allowance_do_not_fake_passes():
    s,ts=session({'mode':'selected','selected':['t1'],'max_failed_points':1});v={t['id']:2.0 for t in ts};e={t['id']:1.0 for t in ts};r=s.evaluate(selected(ts,s.config),v,e,observed(v));assert r['outcome']=='FAIL' and r['counts']=={'PASS':0,'FAIL':1,'UNVERIFIED':3,'total':4,'verified':1};assert not r['threshold_exceeded']


def test_history_bounded_and_counters_survive_eviction():
    s,ts=session({'mode':'full'},n=2);s.store=VerificationStore(max_records=2,max_points=4)
    for i in range(4):s.evaluate(ts,{'t0':1.0,'t1':2.0},observations=observed({'t0':1.0,'t1':2.0}))
    assert s.store.summary()['PASS']==8 and s.store.evicted==2 and len(s.store.rows)==2
    assert len(s.store.filtered(query='Tag1'))==2
    with pytest.raises(ValueError):s.store.filtered(outcome='unknown')


@pytest.mark.parametrize('protocol',['opcua','modbus'])
def test_real_full_verification_write_and_scan(protocol):
    async def run():
        from engine import Engine
        from test_live_monitor import setup,until
        c,p=setup(protocol);p['jobs'][0]['dynamic']=False
        # Independent local simulator and client writer; no saved endpoint is read.
        p['jobs'].append({'id':'writer','name':'Verified writer','kind':'simulation','connection':c['id'],'interval':.1,'simulation':{'pattern':'constant','value':42.125},'verification':{'mode':'full','check_quality':True}})
        token=STORE_CONTEXT.set(VerificationStore());store=STORE_CONTEXT.get();e=Engine(p)
        try:
            await e.start('server');await until(lambda:e.states['server']['state']=='RUNNING');await e.start('writer');await until(lambda:len(store.rows)>0)
            assert store.rows[-1]['outcome']=='PASS' and store.rows[-1]['points'][0]['actual']==42.125
            assert store.rows[-1]['transaction']
            await e.stop('writer')
            scan={'id':'scan','name':'Verified scan','kind':'scan','connection':c['id'],'interval':.1,'verification':{'mode':'full','maximum':10,'on_failure':'continue'}};p['jobs'].append(scan);await e.start('scan');await until(lambda:any(r['job']=='scan' for r in store.rows));r=next(r for r in store.rows if r['job']=='scan');assert r['outcome']=='FAIL' and 'ABOVE_RANGE' in r['points'][0]['issues'];assert not e.tasks['scan'].done()
        finally:await e.stop_all();STORE_CONTEXT.reset(token)
    asyncio.run(run())


def test_api_auth_result_detail_export_and_no_io(tmp_path,monkeypatch):
    async def run():
        import server
        from aiohttp.test_utils import TestClient,TestServer
        from model import default_project
        monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(default_project());s,ts=session({'mode':'full'},n=55);s.store=app[server.VERIFICATION_KEY];v={t['id']:1.0 for t in ts};row=s.evaluate(ts,v,v,observed(v));row['points'][0]['tag']='=danger'
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/api/verifications')).status==403
            init=await(await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':init['token']}
            data=await(await client.get('/api/verifications?outcome=PASS',headers=h)).json();assert data['total']==1 and 'points' not in data['rows'][0]
            r=await(await client.get('/api/verifications/'+row['id']+'?page=1',headers=h)).json();assert len(r['points'])==5
            assert (await client.get('/api/verifications?outcome=bad',headers=h)).status==400
            assert (await client.get('/api/verifications?page=-1',headers=h)).status==400
            assert (await client.get('/api/verifications/missing',headers=h)).status==400
            r=await(await client.get('/api/verifications/export?format=json',headers=h)).json();assert r['results'][0]['counts']['PASS']==55
            text=await(await client.get('/api/verifications/export?format=csv',headers=h)).text();rows=list(csv.reader(io.StringIO(text)));assert len(rows)==56 and rows[1][8]=="'=danger"
            assert not app['engine'].active()
        assert STORE_CONTEXT.get() is not s.store
    asyncio.run(run())


@pytest.mark.parametrize('kind,value',[('Boolean',False),('Int16',-32768),('UInt16',65535),('Int32',-2147483648),('UInt32',4294967295),('Int64',-(2**63)),('UInt64',2**64-1),('Float',cast(.1,'Float')),('Double',.1)])
def test_all_existing_datatypes(kind,value):
    s,ts=session({'mode':'full'},kind,1);v={'t0':value};r=s.evaluate(ts,v,v,observed(v,kind));assert r['outcome']=='PASS';json.dumps(r,allow_nan=False)


@pytest.mark.parametrize('action,allowance,stop',[('stop',0,True),('continue',0,False),('stop',1,False)])
def test_engine_failure_action_and_threshold(action,allowance,stop):
    async def run():
        from engine import Engine
        s,ts=session({'mode':'full','on_failure':action,'max_failed_points':allowance},n=1)
        class Client:
            last_observations=observed({'t0':2.0})
            async def read(self,chosen):return {'t0':2.0}
        state={};e=Engine({'jobs':[]})
        if stop:
            with pytest.raises(ValueError,match='Data verification FAIL'):await e.verify_cycle(s,Client(),{'t0':1.0},state)
        else:await e.verify_cycle(s,Client(),{'t0':1.0},state)
        assert s.store.rows[-1]['outcome']=='FAIL' and state['verification']['counts']['FAIL']==1
    asyncio.run(run())


@pytest.mark.parametrize('error',[TimeoutError(),asyncio.CancelledError()])
def test_engine_read_error_inconclusive_and_propagated(error):
    async def run():
        from engine import Engine
        s,ts=session({'mode':'full','on_failure':'continue'},n=1)
        class Client:
            last_observations={}
            async def read(self,chosen):raise error
        with pytest.raises(type(error)):await Engine({'jobs':[]}).verify_cycle(s,Client(),{'t0':1.0},{})
        assert s.store.rows[-1]['outcome']=='INCONCLUSIVE' and s.store.rows[-1]['counts']['FAIL']==0
    asyncio.run(run())


def test_large_selected_set_and_persisted_configuration_validation():
    ts=tags(6500);c=settings({'kind':'scan','verification':{'mode':'selected','selected':[t['id'] for t in ts]}},ts)
    assert len(selected(ts,c))==6500
    from test_live_monitor import setup
    connection,project=setup('modbus');project['jobs'][0]['verification']={'mode':'full'}
    with pytest.raises(ValueError,match='Server jobs'):validate_project(copy.deepcopy(project))
    project['jobs'][0]['verification']={'mode':'off'};assert validate_project(copy.deepcopy(project))==project


def test_gui_quick_save_and_persisted_recovery_cleanup():
    import shutil,subprocess
    node=shutil.which('node')
    if not node:pytest.skip('Node required for GUI recovery lifecycle test')
    source=str(Path(__file__).resolve().parents[1]/'app/static/recovery.js')
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const records=new Map(),marks=[];
const ctx={crypto:require('crypto').webcrypto,sessionStorage:{getItem:()=>null,setItem:()=>{}},window:{addEventListener:()=>{}},setTimeout:()=>1,clearTimeout:()=>{},$:()=>null,
api:async(path,method,body)=>{if(path==='recovery/drafts'&&method==='POST'){records.set(body.id,{id:body.id,state:'ACTIVE'});return{ok:true}}
if(path==='recovery/drafts')return [...records.values()];
const id=path.split('/')[2];assert(records.has(id),'Cannot mark a draft that was never persisted');marks.push(id);records.get(id).state='SAVED';return{ok:true}}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
(async()=>{await vm.runInContext(`(async()=>{recoveryReady=true;queueRecovery('quick','job','Job',{});await completeRecovery('quick')})()`,ctx);assert.equal(marks.length,0);
await vm.runInContext(`(async()=>{queueRecovery('stored','job','Job',{});await flushRecovery('stored');await completeRecovery('stored')})()`,ctx);assert.deepEqual(marks,['stored']);assert.equal(records.get('stored').state,'SAVED');
console.log('Quick and persisted GUI save cleanup passed')})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run([node,'-e',script,source],check=True,capture_output=True,text=True)


def test_opt_in_scan_can_continue_bad_quality_without_double_counting(monkeypatch):
    async def run():
        from engine import Engine
        from test_live_monitor import setup,until
        c,p=setup('opcua');p['jobs']=[{'id':'scan','name':'Bad quality scan','kind':'scan','connection':c['id'],'interval':.1,'verification':{'mode':'full','on_failure':'continue'}}]
        class Client:
            last_observations={}
            reads=0
            async def connect(self):pass
            async def close(self):pass
            async def prepare(self,*a,**kw):pass
            async def read(self,ts):
                self.reads+=1;self.last_observations={t['id']:{'value':None,'good':False,'quality':'Bad','datatype':'Float'} for t in ts};raise ValueError('Bad source quality')
        client=Client();monkeypatch.setattr('engine.adapter',lambda c:client);store=VerificationStore();token=STORE_CONTEXT.set(store);e=Engine(p)
        try:
            await e.start('scan');await until(lambda:e.states['scan'].get('cycles',0)>=2)
            assert not e.tasks['scan'].done() and store.summary()['checks']==client.reads
            assert store.rows[-1]['outcome']=='FAIL' and 'BAD_QUALITY' in store.rows[-1]['points'][0]['issues']
            assert e.states['scan']['values'][0]['value'] is None
        finally:await e.stop_all();STORE_CONTEXT.reset(token)
    asyncio.run(run())
