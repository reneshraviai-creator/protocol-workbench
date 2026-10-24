import asyncio,copy,json,sys,time
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from access_control import AccessControl,KEY,COOKIE,permitted
from credential_vault import Vault
from write_access import Grant,CONTEXT,check_write

PASSWORD='Disposable-test-password-927!'


def test_hash_session_expiry_revocation_last_admin_and_restart(tmp_path):
 async def run():
  now=[1000.];s=AccessControl(tmp_path,clock=lambda:now[0])
  await s.put_user('admin','Administrator',PASSWORD)
  row=s.db.execute('SELECT * FROM users').fetchone();assert PASSWORD not in str(tuple(row));assert len(row['hash'])==64
  raw=await s.login('admin',PASSWORD);assert raw and raw not in str(s.sessions)
  assert s.session(raw)['role']=='Administrator'
  now[0]+=1801;assert s.session(raw) is None
  raw=await s.login('admin',PASSWORD);await s.put_user('admin','Administrator',active=True);assert s.session(raw) is None
  with pytest.raises(ValueError):await s.put_user('admin','Read-only',active=False)
  assert await s.login('admin','wrong-password-927') is None
  s.settings['enabled']=True;s.save_settings();s.close();s=AccessControl(tmp_path);assert s.enabled and s.users()[0]['name']=='admin';s.close()
 asyncio.run(run())

@pytest.mark.parametrize('role,path,method,body,expected',[
 ('Read-only','/api/project','GET',{},True),('Read-only','/api/project','PUT',{},False),
 ('Read-only','/api/action','POST',{'action':'stop_all'},True),('Read-only','/api/action','POST',{'action':'start_all'},False),
 ('Operator','/api/action','POST',{'action':'start'},True),('Operator','/api/project','PUT',{},False),
 ('Operator','/api/connection','POST',{'mode':'write'},False),('Engineer','/api/connection','POST',{'mode':'write'},True),
 ('Engineer','/api/security/users','POST',{},False),('Operator','/api/security/write-enable','POST',{},True),
 ('Read-only','/api/security/write-enable','POST',{},False),('Engineer','/api/future-api','POST',{},False),
 ('Engineer','/api/shutdown','POST',{},False),('Administrator','/api/security/users','POST',{},True),
 ('Read-only','/api/monitors/a/stop','POST',{},True),('Engineer','/api/event-logs/settings','PUT',{},False)])
def test_rbac_matrix(role,path,method,body,expected):assert permitted(role,path,method,body)==expected

@pytest.mark.parametrize('password',['short',' '*15,123,None,'a'*129])
def test_password_validation(tmp_path,password):
 async def run():
  s=AccessControl(tmp_path)
  try:
   with pytest.raises(ValueError):await s.put_user('admin','Administrator',password)
  finally:s.close()
 asyncio.run(run())


def test_vault_roundtrip_binding_no_plaintext_and_restart(tmp_path):
 c={'protocol':'opcua','url':'opc.tcp://127.0.0.1:4840','username':'user','security':'Basic256Sha256,SignAndEncrypt','password':PASSWORD};v=Vault(tmp_path)
 sealed=v.protect({'project':{'connections':[c]}})['project']['connections'][0];assert sealed['password'].startswith('secret:');assert v.seal(c)==sealed;assert v.resolve(sealed)['password']==PASSWORD
 for field,value in [('url','opc.tcp://elsewhere:4840'),('username','another'),('security','None')]:
  with pytest.raises(ValueError):v.resolve({**sealed,field:value})
 v.close();assert PASSWORD.encode() not in (tmp_path/'vault.sqlite3').read_bytes();v=Vault(tmp_path);assert v.resolve(sealed)['password']==PASSWORD;v.close()


def test_write_grant_exact_targets_count_expiry_and_revocation(tmp_path):
 from resources import empty,add_writes
 c={'protocol':'modbus','host':'127.0.0.1','port':1502,'device_id':1};tags=[{'id':'a','dtype':'Float','address':0,'area':'holding'}];p=empty();add_writes(p,c,tags)
 now=[100.];s=AccessControl(tmp_path,clock=lambda:now[0]);user={'last':100.,'created':100.,'revoked':False};g=Grant(s,user,p['writes']);scope=CONTEXT.set(g)
 try:
  check_write(c,tags)
  with pytest.raises(ValueError):check_write({**c,'port':1503},tags)
  with pytest.raises(ValueError):check_write(c,[{**tags[0],'address':10}])
  now[0]=401
  with pytest.raises(ValueError):check_write(c,tags)
  now[0]=100;user['revoked']=True
  with pytest.raises(ValueError):g.check()
 finally:CONTEXT.reset(scope);s.close()


def test_api_auth_permissions_write_confirmation_real_protocol_and_redaction(tmp_path,monkeypatch):
 async def run():
  from aiohttp.test_utils import TestClient,TestServer
  from aiohttp import CookieJar
  import server
  from model import default_project
  from test_scenarios import scenario,port
  monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(default_project())
  async with TestClient(TestServer(app),cookie_jar=CookieJar(unsafe=True)) as c:
   token=(await (await c.get('/api/bootstrap')).json())['token'];h={'X-Workbench-Token':token}
   r=await c.post('/api/security/setup',headers=h,json={'name':'admin','password':PASSWORD,'confirm':True});assert r.status==200,await r.text()
   assert (await c.get('/api/project',headers=h)).status==401
   boot=await (await c.get('/api/bootstrap')).json();assert boot['authentication_required'] and boot['data_directory'] is None
   r=await c.post('/api/security/login',headers=h,json={'name':'admin','password':PASSWORD});assert r.status==200;assert r.cookies[COOKIE]['httponly'] and r.cookies[COOKIE]['samesite']=='Strict'
   notice=await (await c.get('/api/licence',headers=h)).json();assert (await c.post('/api/licence/acknowledge',headers=h,json={'notice_sha256':notice['notice_sha256'],'confirm':True})).status==200
   assert (await c.get('/api/project',headers=h)).status==200
   assert (await c.post('/api/security/users',headers=h,json={'name':'reader','role':'Read-only','password':PASSWORD})).status==200
   assert (await c.post('/api/security/users',headers=h,json={'name':'operator','role':'Operator','password':PASSWORD})).status==200
   s=scenario(port());assert (await c.post('/api/scenarios',headers=h,json=s)).status==200
   payload={'id':'test'};r=await c.post('/api/scenarios/run',headers=h,json=payload);assert r.status==428,await r.text();review=(await r.json())['write_review'];assert not app['runner'].active()
   assert (await c.post('/api/security/write-enable',headers=h,json={'id':review['id'],'confirm':False})).status==400
   r=await c.post('/api/scenarios/run',headers=h,json=payload);review=(await r.json())['write_review']
   assert (await c.post('/api/security/write-enable',headers=h,json={'id':review['id'],'confirm':True})).status==200
   r=await c.post('/api/scenarios/run',headers=h,json=payload);assert r.status==200,await r.text();key=(await r.json())['id'];await app['runner'].task;assert app['catalogue'].get('runs',key)['state']=='PASSED'
   assert (await c.post('/api/scenarios/run',headers=h,json=payload)).status==428 # no replay
   await c.post('/api/security/logout',headers=h,json={});assert (await c.get('/api/status',headers=h)).status==401
   await c.post('/api/security/login',headers=h,json={'name':'reader','password':PASSWORD})
   notice=await (await c.get('/api/licence',headers=h)).json();assert (await c.post('/api/licence/acknowledge',headers=h,json={'notice_sha256':notice['notice_sha256'],'confirm':True})).status==200
   assert (await c.put('/api/project',headers=h,json=default_project())).status==403
   assert (await c.post('/api/security/users',headers=h,json={})).status==403
   assert (await c.post('/api/scenarios/run',headers=h,json=payload)).status==403
   assert (await c.post('/api/action',headers=h,json={'action':'stop_all'})).status==200
   assert (await c.get('/api/security/users',headers=h)).status==403
   text=json.dumps(app['event_log'].search(category='security'));assert PASSWORD not in text and 'login_succeeded' in text
 asyncio.run(run())


def certificate_pair(folder,name='server',host='127.0.0.1',uri='urn:workbench:security-test',expired=False):
 from datetime import datetime,timedelta,timezone
 import ipaddress
 from cryptography import x509
 from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
 from cryptography.hazmat.primitives import hashes,serialization
 from cryptography.hazmat.primitives.asymmetric import rsa
 now=datetime.now(timezone.utc);key=rsa.generate_private_key(public_exponent=65537,key_size=2048);subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)])
 cert=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=2)).not_valid_after(now+timedelta(days=-1 if expired else 10)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host)),x509.UniformResourceIdentifier(uri)]),False).add_extension(x509.KeyUsage(True,True,True,True,False,False,False,False,False),False).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH,ExtendedKeyUsageOID.CLIENT_AUTH]),False).sign(key,hashes.SHA256())
 c=folder/(name+'.der');k=folder/(name+'.key');c.write_bytes(cert.public_bytes(serialization.Encoding.DER));k.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));return c,k,cert


def test_certificate_pinning_validity_uri_hostname_and_key_matching(tmp_path):
 from certificate_access import validator,describe,validate_upload
 from asyncua import ua
 async def run():
  c,k,cert=certificate_pair(tmp_path);other,otherkey,othercert=certificate_pair(tmp_path,'other');expired,expiredkey,expiredcert=certificate_pair(tmp_path,'expired',expired=True)
  cfg={'url':'opc.tcp://127.0.0.1:4840','certificate':str(c),'private_key':str(k),'server_certificate':str(c)}
  app=ua.ApplicationDescription(ApplicationUri='urn:workbench:security-test',ApplicationType=ua.ApplicationType.Server)
  check=validator(cfg);await check(cert,app);assert describe(c.read_bytes())['valid_now'];validate_upload('a.der',c.read_bytes());validate_upload('a.key',k.read_bytes());validate_upload('private.pem',k.read_bytes())
  with pytest.raises(ValueError,match='differs'):await check(othercert,app)
  wrong=validator({**cfg,'url':'opc.tcp://127.0.0.2:4840'})
  with pytest.raises(ValueError,match='hostname'):await wrong(cert,app)
  with pytest.raises(Exception):await check(cert,ua.ApplicationDescription(ApplicationUri='urn:wrong',ApplicationType=ua.ApplicationType.Server))
  with pytest.raises(ValueError,match='match'):validator({**cfg,'private_key':str(otherkey)})
  with pytest.raises(ValueError,match='expired'):validator({**cfg,'certificate':str(expired),'private_key':str(expiredkey)})
  with pytest.raises(ValueError):validate_upload('bad.der',b'not a certificate')
  with pytest.raises(ValueError):validate_upload('bad.key',b'not a key')
 asyncio.run(run())


def test_login_throttle_and_unknown_user(tmp_path):
 async def run():
  s=AccessControl(tmp_path)
  try:
   for _ in range(5):assert await s.login('unknown',PASSWORD) is None
   with pytest.raises(ValueError,match='Too many'):await s.login('unknown',PASSWORD)
  finally:s.close()
 asyncio.run(run())


def test_authorization_is_bound_to_saved_configuration_and_logout_stops_worker(tmp_path,monkeypatch):
 async def run():
  from aiohttp.test_utils import TestClient,TestServer
  from aiohttp import CookieJar
  import server
  from test_scenarios import scenario,port
  from model import default_project
  monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');s=scenario(port());s['project']['jobs'][0]['dynamic']=True
  p=default_project();p['connections']=s['project']['connections'];p['jobs']=s['project']['jobs'];app=server.create_app(p)
  async with TestClient(TestServer(app),cookie_jar=CookieJar(unsafe=True)) as c:
   h={'X-Workbench-Token':(await (await c.get('/api/bootstrap')).json())['token']}
   await c.post('/api/security/setup',headers=h,json={'name':'admin','password':PASSWORD,'confirm':True});await c.post('/api/security/login',headers=h,json={'name':'admin','password':PASSWORD})
   notice=await (await c.get('/api/licence',headers=h)).json();assert (await c.post('/api/licence/acknowledge',headers=h,json={'notice_sha256':notice['notice_sha256'],'confirm':True})).status==200
   body={'action':'start','id':'server'}
   r=await c.post('/api/action',headers=h,json=body);review=(await r.json())['write_review']
   await c.post('/api/security/write-enable',headers=h,json={'id':review['id'],'confirm':True})
   p=copy.deepcopy(p);p['connections'][0]['port']=port();assert (await c.put('/api/project',headers=h,json=p)).status==200
   r=await c.post('/api/action',headers=h,json=body);assert r.status==428 # saved endpoint changed
   review=(await r.json())['write_review'];await c.post('/api/security/write-enable',headers=h,json={'id':review['id'],'confirm':True})
   r=await c.post('/api/action',headers=h,json=body);assert r.status==200,await r.text()
   await asyncio.sleep(.2);assert app['engine'].active()
   await c.post('/api/security/logout',headers=h,json={});await asyncio.sleep(.4);assert not app['engine'].active();assert not app['resources'].owners
 asyncio.run(run())


def test_secure_opcua_connection_and_acknowledged_write(tmp_path):
 async def run():
  from asyncua import Server,ua
  from test_scenarios import port
  from drivers import OPC
  from credential_vault import CONTEXT as VAULT
  from resources import empty,add_writes
  uri='urn:workbench:security-test';cert,key,_=certificate_pair(tmp_path);server=Server();await server.init();server.set_endpoint(f'opc.tcp://127.0.0.1:{port()}');await server.set_application_uri(uri);await server.load_certificate(cert);await server.load_private_key(key,format="PEM");server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt]);ns=await server.register_namespace(uri);node=await server.nodes.objects.add_variable(ua.NodeId('Measured',ns),'Measured',ua.Variant(0.,ua.VariantType.Float));await node.set_writable()
  cfg={'protocol':'opcua','url':server.endpoint.geturl(),'security':'Basic256Sha256,SignAndEncrypt','certificate':str(cert),'private_key':str(key),'server_certificate':str(cert)};tags=[{'id':'a','tag':'Measured','nodeid':node.nodeid.to_string(),'dtype':'Float'}];p=empty();add_writes(p,cfg,tags);store=AccessControl(tmp_path/'access');vault=Vault(tmp_path/'vault');user={'last':time.time(),'created':time.time(),'revoked':False};grant=Grant(store,user,p['writes']);vt=VAULT.set(vault);wt=CONTEXT.set(grant);client=OPC(cfg)
  try:
   async with server:
    await client.connect();await client.prepare(tags,write=True);assert await client.write(tags,{'a':47.25})==1;assert (await client.read(tags))['a']==47.25
    await client.close()
  finally:CONTEXT.reset(wt);VAULT.reset(vt);vault.close();store.close()
 asyncio.run(run())


def test_api_vault_protects_legacy_secrets_without_rewriting_project(tmp_path,monkeypatch):
 async def run():
  from aiohttp.test_utils import TestClient,TestServer
  from aiohttp import CookieJar
  from model import default_project
  import server
  p=default_project();p['connections']=[{'id':'ua','name':'Protected','protocol':'opcua','url':'opc.tcp://127.0.0.1:4840','security':'Basic256Sha256,SignAndEncrypt','username':'fixture','password':PASSWORD,'tags':[]}]
  original=json.dumps(p).encode();(tmp_path/'project.json').write_bytes(original)
  monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(p)
  async with TestClient(TestServer(app),cookie_jar=CookieJar(unsafe=True)) as c:
   h={'X-Workbench-Token':(await (await c.get('/api/bootstrap')).json())['token']}
   await c.post('/api/security/setup',headers=h,json={'name':'admin','password':PASSWORD,'confirm':True});await c.post('/api/security/login',headers=h,json={'name':'admin','password':PASSWORD})
   notice=await (await c.get('/api/licence',headers=h)).json();assert (await c.post('/api/licence/acknowledge',headers=h,json={'notice_sha256':notice['notice_sha256'],'confirm':True})).status==200
   r=await c.get('/api/project',headers=h);out=await r.json();assert PASSWORD not in json.dumps(out);assert out['connections'][0]['password'].startswith('secret:');assert (tmp_path/'project.json').read_bytes()==original
   assert PASSWORD not in await (await c.get('/api/export',headers=h)).text()
   assert (await c.put('/api/project',headers=h,json=out)).status==200;assert PASSWORD not in (tmp_path/'project.json').read_text()
   out['connections'][0]['url']='opc.tcp://127.0.0.2:4840';assert (await c.put('/api/project',headers=h,json=out)).status==400
   assert (await c.post('/api/certificate',headers=h,json={'filename':'bad.pem','data':'aW52YWxpZA=='})).status==400
 asyncio.run(run())


def test_launcher_identifies_authenticated_workspace_without_disclosing_path(tmp_path,monkeypatch):
 import hashlib,launcher
 monkeypatch.setattr(launcher,'DATA',tmp_path)
 assert launcher.workspace_matches({'data_directory':str(tmp_path)})
 assert launcher.workspace_matches({'data_directory':None,'workspace_id':hashlib.sha256(str(tmp_path.resolve()).encode()).hexdigest()})
 assert not launcher.workspace_matches({'data_directory':None,'workspace_id':'wrong'})
