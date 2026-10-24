import asyncio,copy,csv,io,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from watchlists import make,resolve
from recordings import Recorder,catalogue
from live_monitor import LiveMonitor
from test_live_monitor import setup,until
from scenarios import Catalogue


def test_watchlist_no_credentials_persistent_and_identity(tmp_path):
    c,p=setup('opcua');c.update(username='secret-user',password='secret-password',certificate='/private/cert',private_key='/private/key')
    row=make('Temperature watch',c,['t'],{},['t']);assert 'secret' not in json.dumps(row) and '/private' not in json.dumps(row)
    store=Catalogue(tmp_path/'saved.db');store.put('watchlists',row)
    saved=Catalogue(store.path).get('watchlists',row['id']);assert resolve(saved,p)==c
    c['password']='new';assert resolve(saved,p)==c
    c['url']='opc.tcp://127.0.0.1:19999'
    with pytest.raises(ValueError,match='changed'):resolve(saved,p)

@pytest.mark.parametrize('change',[
    lambda c:c['tags'][0].update(nodeid='ns=2;s=Other'),
    lambda c:c['tags'][0].update(dtype='Float'),
    lambda c:c['tags'].clear(),
])
def test_changed_tag_rejected(change):
    c,p=setup('opcua');row=make('Watch',c,['t'],{})
    change(c)
    with pytest.raises(ValueError):resolve(row,p)

@pytest.mark.parametrize('name,tags,settings,trends',[
    ('', ['t'], {},None),('Watch',[],{},None),('Watch',['missing'],{},None),
    ('Watch',['t'],{'queue_size':1.5},None),('Watch',['t'],{},['missing']),
])
def test_invalid_watchlist(name,tags,settings,trends):
    c,_=setup('modbus')
    with pytest.raises(ValueError):make(name,c,tags,settings,trends)


def test_multi_series_quality_and_selection_bounds():
    c,_=setup('modbus');t=copy.deepcopy(c['tags'][0]);t.update(id='two',tag='Pressure',address=2);c['tags'].append(t)
    m=LiveMonitor(c,c['tags'],{});m.record(c['tags'][0],12);m.record(t,24);m.record(t,None,'BadNoData')
    series=m.snapshot(trends=['t','two'])['series'];assert [s['name'] for s in series]==['Temperature','Pressure']
    assert series[1]['samples'][-1]['value'] is None
    with pytest.raises(ValueError):m.snapshot(trends=['missing'])
    with pytest.raises(ValueError):m.snapshot(trends=['t']*7)


def test_recording_limit_csv_and_safe_labels(tmp_path):
    r=Recorder(tmp_path,'Evidence',limit=3)
    for n in range(6):r.record({'tag':'=formula','value':n,'quality':'Good','received':n})
    assert r.meta['rows']==3 and r.meta['state']=='SAVED' and r.file.closed
    rows=list(csv.DictReader((tmp_path/(r.id+'.csv')).open()));assert len(rows)==3 and rows[0]['tag']=="'=formula"
    assert catalogue(tmp_path)[0]['reason']=='Row limit reached'


def test_watchlist_api_restart_record_and_import_safety(tmp_path):
    async def run():
        import server
        from engine import Engine
        from aiohttp.test_utils import TestClient,TestServer
        c,p=setup('modbus');server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';app=server.create_app(p);engine=Engine(p)
        try:
            await engine.start('server');await until(lambda:engine.states['server']['state']=='RUNNING')
            async with TestClient(TestServer(app)) as client:
                b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
                res=await client.post('/api/monitors',json={'connection':c['id'],'tags':['t'],'settings':{'poll_seconds':.1}},headers=h);m=(await res.json())['id'];monitor=app['monitors'].monitors[m]
                await until(lambda:monitor.events>0)
                res=await client.post('/api/watchlists',json={'monitor':m,'name':'Saved selection','trends':['t']},headers=h);assert res.status==200;key=(await res.json())['id']
                assert (await client.get('/api/watchlists',headers=h)).status==200
                res=await client.post('/api/monitors/'+m+'/record',json={'limit':100},headers=h);assert res.status==200;record=(await res.json())['id']
                assert (await client.post('/api/monitors/'+m+'/record',json={},headers=h)).status==400
                await until(lambda:monitor.recorder.meta['rows']>=2)
                await client.post('/api/monitors/'+m+'/stop',json={},headers=h)
                assert monitor.recorder.meta['state']=='SAVED' and engine.active()
                response=await client.get('/api/recordings/'+record+'/csv',headers=h);assert response.status==200 and 'Temperature' in await response.text()
                assert (await client.get('/api/recordings/not-valid/csv',headers=h)).status==400
                response=await client.post('/api/watchlists/start',json={'id':key},headers=h);assert response.status==200
                app['engine'].project['connections'][0]['port']+=1
                assert (await client.post('/api/watchlists/start',json={'id':key},headers=h)).status==400
                rows=await (await client.get('/api/watchlists',headers=h)).json();assert not rows[0]['valid']
            assert len(Catalogue(tmp_path/'scenarios.sqlite3').all('watchlists'))==1
        finally:await engine.stop_all()
    asyncio.run(run())


def test_interrupted_recording_not_replayed(tmp_path):
    import server
    from model import default_project
    r=Recorder(tmp_path/'recordings','Interrupted');r.record({'tag':'t','value':12});r.file.close()
    server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';app=server.create_app(default_project())
    assert catalogue(tmp_path/'recordings')[0]['state']=='INTERRUPTED'
    assert not app['monitors'].monitors


def test_recording_disk_errors_do_not_break_monitoring(tmp_path):
    class FullDisk:
        def writerow(self,row):raise OSError('disk full')
    r=Recorder(tmp_path,'Disk error');r.writer=FullDisk();r.record({'value':1})
    assert r.meta['state']=='ERROR' and r.file.closed
    r2=Recorder(tmp_path,'Flush error')
    class FlushFailure:
        def flush(self):raise OSError('disk full')
        def close(self):pass
    r2.file.close();r2.file=FlushFailure();r2.stop()
    assert r2.meta['state']=='ERROR'
