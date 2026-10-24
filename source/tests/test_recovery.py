import asyncio,copy,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from recovery import RecoveryStore,protected,revision,restore_credentials,remember_safe
from model import default_project
from test_scenarios import scenario,port


def test_drafts_persist_invalid_partial_forms_without_secrets(tmp_path):
    store=RecoveryStore(tmp_path/'recovery.db')
    data={'project':{'name':'','connections':[{'password':'never-store','username':'engineer','private_key':'/local/key.pem'}]},'fields':{'c-password':'never-store','c-name':'unfinished'},'nested':{'access_token':'never-store','passphrase':'never-store'}}
    row=store.draft({'kind':'connection','title':'Partial form','data':data})
    reopened=RecoveryStore(store.path).get_draft(row['id']);raw=json.dumps(reopened)
    assert 'never-store' not in raw and reopened['data']['fields']['c-name']=='unfinished'
    assert data['fields']['c-password']=='never-store'
    store.mark(row['id'],'DISMISSED');assert store.drafts()[0]['state']=='DISMISSED'
    assert store.get_draft(row['id'])
    store.mark(row['id'],'SAVED');assert store.drafts()[0]['state']=='SAVED'

@pytest.mark.parametrize('change',[
 lambda b:b.update(id='../escape'),lambda b:b.update(kind='execute'),lambda b:b.update(data='text'),lambda b:b.update(title=''),lambda b:b.update(title='x'*161),
])
def test_invalid_draft_metadata_rejected(tmp_path,change):
    b={'id':'a','kind':'workspace','title':'Draft','data':{}};change(b)
    with pytest.raises(ValueError):RecoveryStore(tmp_path/'r.db').draft(b)


def test_limits_history_dedup_and_deleted_device_recovery(tmp_path):
    store=RecoveryStore(tmp_path/'r.db');p=scenario()['project']
    store.remember('workspace','Original',p)
    altered=copy.deepcopy(p);altered['connections']=[];altered['jobs']=[]
    store.remember('workspace','Deleted device',altered)
    original=store.snapshot(store.history()[-1]['id']);assert original['data']['connections'][0]['tags']
    for n in range(15):altered['name']=str(n);store.remember('workspace','Update',altered)
    assert len(store.history())==10
    store.remember('workspace','No change',dict(reversed(list(altered.items()))));assert len(store.history())==10
    for n in range(35):store.draft({'kind':'workspace','id':str(n),'title':'Draft','data':{'name':n}})
    assert len(store.drafts())==30 and store.drafts()[0]['id']=='34'


def test_restore_credentials_only_same_identity_and_disable_autostart():
    p=scenario()['project'];c=p['connections'][0];c['password']='current';p['settings']['autostart']=True
    snapshot=protected(p);restored=restore_credentials(snapshot,p)
    assert restored['connections'][0]['password']=='current' and not restored['settings']['autostart']
    snapshot['connections'][0]['host']='other-host';assert 'password' not in restore_credentials(snapshot,p)['connections'][0]
    assert p['settings']['autostart'] is True


def test_recovery_size_error_is_explicit_and_history_failure_nonfatal(tmp_path,monkeypatch):
    import recovery
    store=RecoveryStore(tmp_path/'r.db');monkeypatch.setattr(recovery,'MAX_DOCUMENT',20)
    with pytest.raises(ValueError,match='limit'):store.draft({'kind':'workspace','title':'Large','data':{'x':'a'*100}})
    assert 'unavailable' in remember_safe(store,'workspace','Large',{'x':'a'*100})


def test_api_drafts_and_history_never_apply_or_interrupt_runs(tmp_path):
 async def run():
    import server
    from aiohttp.test_utils import TestClient,TestServer
    server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';p=default_project();app=server.create_app(p)
    async with TestClient(TestServer(app)) as client:
      b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
      assert (await client.get('/api/recovery/drafts')).status==403
      s=scenario(port());s['steps']=[{'action':'wait','seconds':60,'timeout':65}];await client.post('/api/scenarios',json=s,headers=h)
      key=await app['runner'].start('test');task=app['runner'].tasks[key];before=app['catalogue'].get('runs',key)['snapshot']
      data={'project':{'name':'invalid draft','connections':[]},'password':'never-store'}
      response=await client.post('/api/recovery/drafts',json={'kind':'workspace','title':'Unfinished','data':data},headers=h);assert response.status==200;draft=(await response.json())['id']
      response=await client.get('/api/recovery/drafts/'+draft,headers=h);assert 'never-store' not in await response.text()
      history=await (await client.get('/api/recovery/history',headers=h)).json();assert history
      for row in history:assert (await client.get('/api/recovery/history/'+row['id'],headers=h)).status==200
      assert app['engine'].project==p and not server.PROJECT.exists() and not task.done()
      assert app['catalogue'].get('runs',key)['snapshot']==before
      await app['runner'].stop(key)
 async def wrapper():await run()
 asyncio.run(wrapper())


def test_project_revision_rejects_stale_writer_without_mutation(tmp_path):
 async def run():
    import server
    from aiohttp.test_utils import TestClient,TestServer
    server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';p=default_project();app=server.create_app(p)
    async with TestClient(TestServer(app)) as client:
      b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
      response=await client.get('/api/project',headers=h);etag=response.headers['ETag'];new=copy.deepcopy(p);new['name']='New saved configuration'
      response=await client.put('/api/project',json=new,headers={**h,'If-Match':etag});assert response.status==200 and response.headers['ETag']!=etag
      stale=copy.deepcopy(p);stale['name']='Stale window'
      response=await client.put('/api/project',json=stale,headers={**h,'If-Match':etag});assert response.status==409
      assert app['engine'].project['name']==new['name'] and json.loads(server.PROJECT.read_text())['name']==new['name']
      rows=app['recovery'].history();assert len(rows)==2
      assert app['recovery'].snapshot(rows[-1]['id'])['data']['name']==p['name']
 asyncio.run(run())


def test_recovery_cannot_change_active_workspace_job(tmp_path):
 async def run():
    import server
    from aiohttp.test_utils import TestClient,TestServer
    from test_live_monitor import until
    server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';p=scenario(port())['project'];app=server.create_app(p)
    async with TestClient(TestServer(app)) as client:
      b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
      await app['engine'].start('server');await until(lambda:app['engine'].states['server']['state']=='RUNNING')
      task=app['engine'].tasks['server'];etag=(await client.get('/api/project',headers=h)).headers['ETag']
      changed=copy.deepcopy(p);changed['jobs'][0]['interval']=2
      response=await client.put('/api/project',json=changed,headers={**h,'If-Match':etag});assert response.status==400
      assert not task.done() and app['engine'].project==p
      assert (await client.post('/api/recovery/drafts',json={'kind':'workspace','title':'Restored config draft','data':{'project':changed}},headers=h)).status==200
      assert not task.done() and app['engine'].project==p
 asyncio.run(run())


def test_project_save_survives_history_storage_failure(tmp_path,monkeypatch):
 async def run():
    import server
    from aiohttp.test_utils import TestClient,TestServer
    server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';p=default_project();app=server.create_app(p)
    def unavailable(*args):raise OSError('Recovery disk unavailable')
    monkeypatch.setattr(app['recovery'],'remember',unavailable)
    async with TestClient(TestServer(app)) as client:
      b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']};new=copy.deepcopy(p);new['name']='Saved despite history error'
      response=await client.put('/api/project',json=new,headers=h);assert response.status==200
      assert 'recovery_warning' in await response.json()
      assert app['engine'].project==new and json.loads(server.PROJECT.read_text())==new
 asyncio.run(run())
