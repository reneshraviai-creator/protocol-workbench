import asyncio,copy,json,sqlite3,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from project_library import ProjectLibrary,content,package,migrate,fingerprint,defaults,preferences
from model import default_project
from test_scenarios import scenario,port


def test_project_revisions_archive_restart_and_integrity(tmp_path):
 store=ProjectLibrary(tmp_path/'projects.db');p=default_project();p['settings']['autostart']=True
 row=store.create('First',{'project':p});ident=row['id'];assert not row['content']['project']['settings']['autostart'];assert p['settings']['autostart']
 value=row['content'];value['project']['name']='Edited';new=store.update(ident,value,1);assert new['revision']==2;assert store.get(ident,1)['name']=='Edited' # metadata name is current; content is historical
 assert store.get(ident,1)['content']['project']['name']=='First'
 with pytest.raises(ValueError,match='revision changed'):store.update(ident,value,1)
 with pytest.raises(ValueError):store.archive(ident,2,'wrong',True)
 store.archive(ident,2,'Edited',True);assert not store.listing();assert store.listing(True)[0]['id']==ident
 with pytest.raises(ValueError):store.update(ident,value,2)
 store.archive(ident,2,'Edited',False);store.touch(ident);assert ProjectLibrary(store.path).listing()[0]['last_opened']>0
 with store.db() as db:db.execute('UPDATE revisions SET body=? WHERE project=? AND revision=2',(json.dumps({'project':default_project(),'scenarios':[]}),ident))
 with pytest.raises(ValueError,match='integrity'):store.get(ident)


def test_package_redaction_migration_and_tamper_rejection():
 p=default_project();p['connections']=[{'id':'ua','name':'PLC','protocol':'opcua','url':'opc.tcp://127.0.0.1:4840','password':'private-password','username':'local-user','certificate':'private-cert-path','private_key':'private-key-path','tags':[]}]
 value=content({'project':p});assert 'private-password' not in json.dumps(value)
 backup=package(value);raw=json.dumps(backup);assert 'private-key-path' not in raw and 'private-cert-path' not in raw
 restored,note=migrate(backup);assert restored['project']['name']==p['name'] and 'Verified' in note
 bad=copy.deepcopy(backup);bad['content']['project']['name']='tampered'
 with pytest.raises(ValueError,match='integrity'):migrate(bad)
 assert 'Migrated' in migrate(p)[1];assert p['connections'][0]['password']=='private-password'
 for bad in ({'version':2},{'format':'unknown'},[],{**backup,'version':99}):
  with pytest.raises(ValueError):migrate(bad)

@pytest.mark.parametrize('value',[{'tag_count':True},{'tag_count':0},{'tag_prefix':'x;exec'},{'timeout':float('nan')},{'retry_seconds':0},{'simulation_interval':61},{'unknown':1}])
def test_default_validation(value):
 with pytest.raises(ValueError):defaults(value)

@pytest.mark.parametrize('value',[{'density':'tiny'},{'landing_page':'javascript:'},{'unknown':1}])
def test_preference_validation(value):
 with pytest.raises(ValueError):preferences(value)


def test_profiles_schema_capacity_and_invalid_snapshots(tmp_path):
 store=ProjectLibrary(tmp_path/'p.db');c=scenario()['project']['connections'][0];c['password']='never-save';ident=store.save_profile('Reusable',c)
 assert 'never-save' not in json.dumps(store.profiles());assert store.profiles()[0]['id']==ident
 store.save_setting('defaults',defaults({'tag_count':200}));assert store.setting('defaults',{})['tag_count']==200
 with store.db() as db:db.execute('PRAGMA user_version=99')
 with pytest.raises(ValueError,match='newer'):ProjectLibrary(store.path)
 with pytest.raises(ValueError):content({'project':default_project(),'scenarios':[{'id':'bad'}]})


def test_api_project_lifecycle_running_isolation_backup_and_preferences(tmp_path,monkeypatch):
 async def run():
  from aiohttp.test_utils import TestClient,TestServer
  from recovery import revision
  import server
  from project_api import KEY
  s=scenario(port());p=s['project'];monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');(tmp_path/'project.json').write_text(json.dumps(p));app=server.create_app(p);app['catalogue'].put('scenarios',s)
  async with TestClient(TestServer(app)) as c:
   h={'X-Workbench-Token':(await (await c.get('/api/bootstrap')).json())['token']}
   async def cmd(b):return await c.post('/api/project-library/command',headers=h,json=b)
   r=await cmd({'action':'capture','name':'Saved lab','scenarios':['test']});assert r.status==200,await r.text();row=await r.json();ident=row['id'];assert len(row['content']['scenarios'])==1
   before=(tmp_path/'project.json').read_bytes();await app['engine'].start('server');await asyncio.sleep(.15)
   assert (await cmd({'action':'duplicate','id':ident,'name':'Copy'})).status==200
   r=await cmd({'action':'open','id':ident,'expected_revision':1,'workspace_revision':revision(app['engine'].project),'confirm':True});assert r.status==400 and 'Stop jobs' in await r.text();assert app['engine'].active() and (tmp_path/'project.json').read_bytes()==before
   await app['engine'].stop_all()
   r=await cmd({'action':'open','id':ident,'expected_revision':1,'workspace_revision':'0'*64,'confirm':True});assert r.status==409
   r=await cmd({'action':'open','id':ident,'expected_revision':1,'workspace_revision':revision(app['engine'].project),'confirm':False});assert r.status==400
   r=await cmd({'action':'open','id':ident,'expected_revision':1,'workspace_revision':revision(app['engine'].project),'confirm':True});assert r.status==200,await r.text();assert app['engine'].project['name']=='Saved lab';assert not app['engine'].active();assert len(app['catalogue'].all('scenarios'))==1
   r=await cmd({'action':'copy-test','id':ident,'scenario':'test','name':'Copied test'});assert r.status==200;assert len(app['catalogue'].all('scenarios'))==2 and not app['runner'].active()
   backup=await (await c.get('/api/project-library/'+ident+'/backup',headers=h)).json();assert (await c.post('/api/project-library/preview',headers=h,json={'document':backup})).status==200
   r=await cmd({'action':'import','document':backup,'name':'Imported'});assert r.status==200
   assert (await cmd({'action':'rename','id':ident,'expected_revision':1,'name':'Renamed'})).status==200
   assert (await cmd({'action':'restore-revision','id':ident,'expected_revision':2,'source_revision':1})).status==200
   assert (await cmd({'action':'archive','id':ident,'name':'Saved lab','expected_revision':3,'confirm':True})).status==200
   assert (await cmd({'action':'open','id':ident,'expected_revision':3,'workspace_revision':revision(app['engine'].project),'confirm':True})).status==400
   assert (await cmd({'action':'unarchive','id':ident,'name':'Saved lab','expected_revision':3,'confirm':True})).status==200
   assert (await c.put('/api/project-library/preferences',headers=h,json={'landing_page':'projects','density':'compact'})).status==200
   assert (await (await c.get('/api/project-library/preferences',headers=h)).json())['landing_page']=='projects'
   assert (await cmd({'action':'save-profile','connection':'local','name':'Lab profile'})).status==200
   assert len(await (await c.get('/api/project-library/profiles',headers=h)).json())==1
   assert len(app[KEY].listing())==3
 asyncio.run(run())


def test_authenticated_project_permissions_and_preference_isolation(tmp_path,monkeypatch):
 async def run():
  from aiohttp.test_utils import TestClient,TestServer
  from aiohttp import CookieJar
  import server
  from test_access_control import PASSWORD
  monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(default_project())
  async with TestClient(TestServer(app),cookie_jar=CookieJar(unsafe=True)) as c:
   h={'X-Workbench-Token':(await (await c.get('/api/bootstrap')).json())['token']}
   await c.post('/api/security/setup',headers=h,json={'name':'admin','password':PASSWORD,'confirm':True});await c.post('/api/security/login',headers=h,json={'name':'admin','password':PASSWORD})
   notice=await (await c.get('/api/licence',headers=h)).json();assert (await c.post('/api/licence/acknowledge',headers=h,json={'notice_sha256':notice['notice_sha256'],'confirm':True})).status==200
   for name,role in [('engineer','Engineer'),('reader','Read-only')]:await c.post('/api/security/users',headers=h,json={'name':name,'role':role,'password':PASSWORD})
   await c.put('/api/project-library/preferences',headers=h,json={'landing_page':'projects'})
   await c.post('/api/security/logout',headers=h,json={});await c.post('/api/security/login',headers=h,json={'name':'engineer','password':PASSWORD})
   notice=await (await c.get('/api/licence',headers=h)).json();assert (await c.post('/api/licence/acknowledge',headers=h,json={'notice_sha256':notice['notice_sha256'],'confirm':True})).status==200
   assert (await c.post('/api/project-library/command',headers=h,json={'action':'create','name':'Engineer project'})).status==200
   assert (await c.put('/api/project-library/defaults',headers=h,json={'tag_count':200})).status==403
   assert (await (await c.get('/api/project-library/preferences',headers=h)).json())['landing_page']=='dashboard'
   await c.post('/api/security/logout',headers=h,json={});await c.post('/api/security/login',headers=h,json={'name':'reader','password':PASSWORD})
   notice=await (await c.get('/api/licence',headers=h)).json();assert (await c.post('/api/licence/acknowledge',headers=h,json={'notice_sha256':notice['notice_sha256'],'confirm':True})).status==200
   assert (await c.get('/api/project-library',headers=h)).status==200
   assert (await c.post('/api/project-library/command',headers=h,json={'action':'create','name':'Denied'})).status==403
   assert (await c.put('/api/project-library/preferences',headers=h,json={'density':'compact'})).status==200
 asyncio.run(run())
