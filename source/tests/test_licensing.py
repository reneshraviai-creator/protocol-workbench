import asyncio,copy,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from licensing import validate,DEFAULT,NOTICE

@pytest.mark.parametrize('change',[{'expiry_date':'bad'},{'issue_date':'2026-02-30'},{'issue_date':'20260101'},{'issue_date':'2026-02-01','expiry_date':'2026-01-01'},{'licence_reference':'x'*201},{'publisher':'a\nb'},{'software_version':'fake'},{'revision':True}])
def test_licence_validation(change):
 with pytest.raises(ValueError):validate({**DEFAULT,**change})


def test_login_receipt_gate_expiry_roles_audit_and_restart(tmp_path,monkeypatch):
 async def run():
  from aiohttp.test_utils import TestClient,TestServer
  from aiohttp import CookieJar
  import server
  from model import default_project
  from test_access_control import PASSWORD
  from project_api import KEY
  monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(default_project())
  async with TestClient(TestServer(app),cookie_jar=CookieJar(unsafe=True)) as c:
   h={'X-Workbench-Token':(await (await c.get('/api/bootstrap')).json())['token']}
   assert (await c.put('/api/licence',headers=h,json=DEFAULT)).status==403
   assert (await c.post('/api/security/setup',headers=h,json={'name':'admin','password':PASSWORD,'confirm':True})).status==200
   async def login(name):
    assert (await c.post('/api/security/login',headers=h,json={'name':name,'password':PASSWORD})).status==200
   async def ack():
    info=await (await c.get('/api/licence',headers=h)).json()
    assert (await c.post('/api/licence/acknowledge',headers=h,json={'confirm':True,'notice_sha256':info['notice_sha256']})).status==200
   await login('admin');assert (await (await c.get('/api/bootstrap')).json())['licence_ack_required']
   for path in ('project','status','project-library','scenarios','engineering-reports'):
    r=await c.get('/api/'+path,headers=h);assert r.status==428 and (await r.json())['licence_ack_required']
   assert (await c.post('/api/action',headers=h,json={'action':'start_all'})).status==428
   assert (await c.post('/api/action',headers=h,json={'action':'stop_all'})).status==200 # safe stop remains available
   assert (await c.post('/api/licence/acknowledge',headers=h,json={'confirm':True,'notice_sha256':'wrong'})).status==400
   await ack();assert (await c.get('/api/project',headers=h)).status==200
   count=app['event_log'].search(category='security',query='licence_notice_acknowledged')['total'];await ack();assert app['event_log'].search(category='security',query='licence_notice_acknowledged')['total']==count
   old=await (await c.get('/api/licence',headers=h)).json();value={k:old[k] for k in DEFAULT};value.update(licensed_organisation='Disposable lab',licence_reference='LAB-EXPIRED',licence_type='Evaluation',issue_date='2020-01-01',expiry_date='2020-02-01')
   r=await c.put('/api/licence',headers=h,json=value);assert r.status==200 and (await r.json())['expired']
   assert (await c.get('/api/project',headers=h)).status==200 # expiry cannot block functionality
   assert (await c.put('/api/licence',headers=h,json=value)).status==400
   await c.post('/api/security/users',headers=h,json={'name':'reader','role':'Read-only','password':PASSWORD})
   await c.post('/api/security/logout',headers=h,json={});await login('reader');assert (await c.get('/api/project',headers=h)).status==428;await ack()
   assert (await c.get('/api/project',headers=h)).status==200
   assert (await c.put('/api/licence',headers=h,json={**value,'revision':1})).status==403
   assert app[KEY].setting('licence_information',{})['licence_reference']=='LAB-EXPIRED'
  from project_library import ProjectLibrary
  assert ProjectLibrary(tmp_path/'project-library.sqlite3').setting('licence_information',{})['expiry_date']=='2020-02-01'
 asyncio.run(run())


def test_authenticated_installation_check_requires_local_review_then_passes(tmp_path,monkeypatch):
 async def run():
  from aiohttp.test_utils import TestClient,TestServer
  from aiohttp import CookieJar
  from model import default_project
  from test_access_control import PASSWORD
  import server
  monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(default_project())
  async with TestClient(TestServer(app),cookie_jar=CookieJar(unsafe=True)) as c:
   h={'X-Workbench-Token':(await (await c.get('/api/bootstrap')).json())['token']}
   await c.post('/api/security/setup',headers=h,json={'name':'admin','password':PASSWORD,'confirm':True});await c.post('/api/security/login',headers=h,json={'name':'admin','password':PASSWORD});notice=await (await c.get('/api/licence',headers=h)).json();await c.post('/api/licence/acknowledge',headers=h,json={'notice_sha256':notice['notice_sha256'],'confirm':True})
   r=await c.post('/api/diagnostics',headers=h,json={});assert r.status==428;review=(await r.json())['write_review'];assert 'local-self-test' in str(review['targets'])
   await c.post('/api/security/write-enable',headers=h,json={'id':review['id'],'confirm':True})
   r=await c.post('/api/diagnostics',headers=h,json={});result=await r.json();assert r.status==200 and result['ok'],result
   assert not app['engine'].project['connections'] and not app['resources'].owners
 asyncio.run(run())
