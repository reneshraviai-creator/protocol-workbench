import asyncio,copy,hashlib,json,sys,time,sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from event_log import EventLog,CONTEXT,settings,scrub
from engineering_reports import ReportStore,collect,verdict,outcome,csv_export,KINDS

def spec(kind='test'):return {'name':'Recorded test','title':'Engineering evidence','type':kind,'description':'Local isolated acceptance evidence'}

@pytest.mark.parametrize('value',[{'level':'TRACE'},{'max_rows':99},{'max_rows':True},{'retention_days':0},{'max_bytes':2},{'backup_count':11},{'unknown':1}])
def test_invalid_logging_settings(value):
 with pytest.raises(ValueError):settings(value)

def test_structured_redaction_filter_rotation_retention_and_restart(tmp_path):
 s=EventLog(tmp_path/'logs');s.learn({'connections':[{'password':'my-private-password','private_key':'/private/key.pem','username':'secret-user'}]})
 s.emit('application','DEBUG','omitted');assert s.search()['total']==0
 s.configure({'level':'DEBUG','max_rows':100,'retention_days':1,'max_bytes':64000,'backup_count':2})
 s.emit('security','WARNING','denied',password='plain',message='my-private-password token=abc opc.tcp://user:pass@localhost /private/key.pem',deep={'authorization':'Bearer X'})
 raw=s.export('json');assert all(x not in raw for x in ('my-private-password','user:pass','/private/key.pem','Bearer X','token=abc')) and '[REDACTED]' in raw
 assert s.search(query='denied',level='WARNING',category='security')['total']==1
 assert s.search(since=time.time()-10,until=time.time()+10)['total']==1
 assert 'denied' in s.export('csv',category='security')
 for n in range(105):s.emit('communication','INFO','batch',message='x'*1500,transaction=str(n))
 assert s.search()['total']==100 and len(list((tmp_path/'logs').glob('events.jsonl.*')))<=2
 with s.db() as db:db.execute('UPDATE events SET time=0')
 s.prune();assert s.search()['total']==0;s.emit('application','INFO','persisted');s.close();again=EventLog(tmp_path/'logs');assert again.search(query='persisted')['total']==1;again.close()

@pytest.mark.parametrize('query',[{'level':'BAD'},{'category':'wire'},{'page':-1},{'limit':0},{'since':float('nan')},{'since':2,'until':1},{'query':'x'*201}])
def test_log_query_bounds(tmp_path,query):
 s=EventLog(tmp_path/'logs')
 try:
  with pytest.raises(ValueError):s.search(**query)
 finally:s.close()

def test_log_faults_do_not_escape_and_security_not_suppressed(tmp_path,monkeypatch):
 s=EventLog(tmp_path/'logs');s.configure({'level':'CRITICAL'});s.emit('application','INFO','hidden');s.emit('security','WARNING','denial');assert s.search()['total']==1
 def fail(*args):raise OSError('disk unavailable')
 monkeypatch.setattr(s.handler,'emit',fail);s.emit('application','CRITICAL','fault');assert s.failures==1 and 'OSError' in s.last_error;s.close()

@pytest.mark.parametrize('states,expected',[([], 'NOT TESTED'),(['PASS'],'PASS'),(['FAIL','PASS'],'FAIL'),(['INCONCLUSIVE','PASS'],'INCONCLUSIVE'),(['NOT TESTED'],'NOT TESTED'),(['PASS','NOT TESTED'],'INCONCLUSIVE')])
def test_verdict_is_evidence_based(states,expected):assert verdict(states)==expected

def test_report_preview_is_immutable_private_persistent_and_csv_safe(tmp_path):
 s=ReportStore(tmp_path/'r.db');rows=[{'source_id':'actual-run','result':'PASS','evidence':{'value':-2,'formula':'=evil','password':'hidden','message':'token=abc'}}];doc=s.preview('test',rows,{},spec());before=copy.deepcopy(doc);rows[0]['result']='FAIL';doc['summary']['status']='FAIL'
 saved=s.save(doc['id']);assert saved['summary']['status']=='PASS' and 'hidden' not in json.dumps(saved) and 'token=abc' not in json.dumps(saved)
 raw={k:v for k,v in saved.items() if k!='evidence_sha256'};assert hashlib.sha256(json.dumps(raw,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()==saved['evidence_sha256']
 assert len(s.history())==1 and 'records' not in s.history()[0];assert ReportStore(s.path).get(saved['id'])==saved
 text=csv_export(saved);assert "'=evil" in text and '-2' in text
 assert s.save(saved['id'])==saved
 s.pending[saved['id']]['created']=0
 with pytest.raises(ValueError):s.save(saved['id'])
 with pytest.raises(ValueError):s.get('../../elsewhere')

@pytest.mark.parametrize('change',[{'type':'unknown'},{'name':''},{'title':'x'*161},{'script':'eval'},{'description':'x'*1001},{'id':'../../file'}])
def test_template_validation(tmp_path,change):
 s=ReportStore(tmp_path/'r.db')
 with pytest.raises(ValueError):s.save_template({**spec(),**change})

def test_template_update_and_preview_limits(tmp_path):
 s=ReportStore(tmp_path/'r.db');t=s.save_template(spec());t['title']='New heading';s.save_template(t);assert len(s.templates())==9
 for _ in range(12):s.preview('test',[],{},spec())
 assert len(s.pending)==10
 with pytest.raises(ValueError):s.preview('bridge',[],{},spec())
 with pytest.raises(ValueError):s.preview('test',[{'source_id':'r','result':'PASS','evidence':'x'*5000001}],{},spec())


def app_fixture(tmp_path):
 from scenarios import Catalogue
 from model import default_project
 from engine import Engine
 from transactions import TransactionStore
 from verification import VerificationStore
 from communication_health import HealthHub
 from performance import Performance
 from transaction_api import KEY as TX
 from verification_api import KEY as VER
 from health_api import KEY as HEALTH
 from performance_api import KEY as PERF
 from bridge_api import KEY as BRIDGE
 from bridge import BridgeStore
 return {'catalogue':Catalogue(tmp_path/'c.db'),'engine':Engine(default_project()),'event_log':EventLog(tmp_path/'events'),TX:TransactionStore(),VER:VerificationStore(),HEALTH:HealthHub(),PERF:Performance(),BRIDGE:BridgeStore()}

@pytest.mark.parametrize('kind',KINDS)
def test_no_evidence_never_becomes_artificial_pass(tmp_path,kind):
 app=app_fixture(tmp_path)
 try:
  rows,source=collect(app,kind,{'limit':10});s=ReportStore(tmp_path/'r.db');doc=s.preview(kind,rows,source,spec(kind));assert doc['summary']['status']=='NOT TESTED'
 finally:app['event_log'].close()

def test_all_sources_capture_actual_store_rows_with_explicit_semantics(tmp_path):
 from transaction_api import KEY as TX
 from verification_api import KEY as VER
 from health_api import KEY as HEALTH
 from performance_api import KEY as PERF
 from bridge_api import KEY as BRIDGE
 app=app_fixture(tmp_path)
 try:
  app['catalogue'].put('runs',{'id':'r','name':'Recorded fixture','state':'CANCELLED','started':1,'steps':[],'snapshot':{'password':'never-export'}})
  app['engine'].run_history.append({'job':'j','state':'STOPPED','cycles':3,'finished':1})
  app[TX].rows.append({'id':'tx','state':'UNKNOWN','counts':{'UNKNOWN':1}})
  app[VER].rows.append({'id':'v','job':'j','outcome':'FAIL'})
  app[HEALTH].events.append({'id':'e','event':'RECOVERED','time':1})
  app[PERF].history.append({'time':1,'measured':'fixture'})
  app[BRIDGE].rows.append({'id':'b','job':'j','counts':{'SUCCEEDED':1,'FILTERED':1}})
  app['event_log'].emit('protocol','ERROR','actual-recorded-error')
  for kind,expected in [('test','INCONCLUSIVE'),('simulation','INCONCLUSIVE'),('communication','INCONCLUSIVE'),('data_verification','FAIL'),('recovery','INCONCLUSIVE'),('performance','INCONCLUSIVE'),('bridge','INCONCLUSIVE'),('error','FAIL')]:
   rows,source=collect(app,kind,{});assert rows[0]['result']==expected,(kind,rows);assert 'never-export' not in json.dumps(rows)
  assert collect(app,'bridge',{'job':'other'})[0]==[]
  with pytest.raises(ValueError):collect(app,'test',{'limit':0})
  with pytest.raises(ValueError):collect(app,'test',{'records':['fake']})
 finally:app['event_log'].close()

def test_real_protocol_logging_report_exports_and_api_boundaries(tmp_path):
 async def run():
  from aiohttp.test_utils import TestClient,TestServer
  from model import default_project
  from test_scenarios import scenario,port
  import server
  server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';app=server.create_app(default_project())
  async with TestClient(TestServer(app)) as c:
   token=(await (await c.get('/api/bootstrap')).json())['token'];h={'X-Workbench-Token':token}
   for path in ('event-logs','engineering-reports','report-templates'):
    assert (await c.get('/api/'+path)).status==403
   events=app['event_log'].search(category='security');assert events['total']==3 and token not in json.dumps(events)
   await c.put('/api/event-logs/settings',json={'level':'DEBUG'},headers=h)
   s=scenario(port());await c.post('/api/scenarios',json=s,headers=h);key=(await (await c.post('/api/scenarios/run',json={'id':'test'},headers=h)).json())['id'];await app['runner'].task
   assert app['catalogue'].get('runs',key)['state']=='PASSED'
   assert app['event_log'].search(category='communication')['total']>=2 and app['event_log'].search(category='simulation')['total']>=1
   payload={'type':'test','selection':{'id':key,'limit':1}};response=await c.post('/api/engineering-reports/preview',json=payload,headers=h);assert response.status==200,await response.text();doc=await response.json();assert doc['summary']['status']=='PASS'
   assert (await c.post('/api/engineering-reports/preview',json={**payload,'records':['fake']},headers=h)).status==400
   doc['summary']['status']='FAIL';assert (await c.post('/api/engineering-reports/save',json={'preview_id':doc['id'],'records':[]},headers=h)).status==400
   saved=await (await c.post('/api/engineering-reports/save',json={'preview_id':doc['id']},headers=h)).json();assert saved['summary']['status']=='PASS'
   for fmt,mime in [('json','application/json'),('csv','text/csv'),('pdf','application/pdf')]:
    r=await c.get('/api/engineering-reports/'+saved['id']+'/download?format='+fmt,headers=h);body=await r.read();assert r.status==200 and mime in r.headers['Content-Type'];assert body.startswith(b'%PDF') if fmt=='pdf' else len(body)>100
   assert len(await (await c.get('/api/engineering-reports',headers=h)).json())==1
   t=await c.post('/api/report-templates',json=spec(),headers=h);assert t.status==200
   assert (await c.get('/api/engineering-reports/'+saved['id']+'/download?format=exe',headers=h)).status==400
 asyncio.run(run())

def test_pdf_contains_real_verdict_ids_and_paginated_evidence(tmp_path):
 from report_pdf import render
 s=ReportStore(tmp_path/'r.db');doc=s.preview('communication',[{'source_id':f'tx-{n}','result':'INCONCLUSIVE','evidence':{'measured_value':n,'state':'UNKNOWN','message':'long value '*20}} for n in range(30)],{},spec('communication'));pdf=render(doc);assert pdf.startswith(b'%PDF') and pdf.count(b'/Type /Page')>2
 # ReportLab emits uncompressed metadata and valid page structures; visual/text validation also runs against a local real report artifact.
 assert b'Protocol Workbench' in pdf

def test_pdf_large_field_limit_is_explicit(tmp_path):
 from report_pdf import render
 s=ReportStore(tmp_path/'r.db');doc=s.preview('test',[{'source_id':'many','result':'PASS','evidence':list(range(10001))}],{},spec())
 with pytest.raises(ValueError,match='10,000'):render(doc)

def test_report_history_capacity_and_source_filter_validation(tmp_path):
 s=ReportStore(tmp_path/'r.db')
 with s.db() as db:
  for n in range(200):db.execute('INSERT INTO reports VALUES (?,?)',(str(n),json.dumps({'id':str(n)})))
 doc=s.preview('test',[],{},spec())
 with pytest.raises(ValueError,match='history full'):s.save(doc['id'])
 app=app_fixture(tmp_path)
 try:
  with pytest.raises(ValueError,match='Job filter'):collect(app,'test',{'job':'j'})
  for n in range(3):app['catalogue'].put('runs',{'id':str(n),'started':n,'name':'Recorded fixture','state':'PASSED','steps':[],'snapshot':{'password':'private'}})
  rows,source=collect(app,'test',{'limit':1});assert len(rows)==1 and rows[0]['source_id']=='2' and source['truncated'] and source['matching_records']==3
  rows,source=collect(app,'test',{'id':'0','limit':1});assert rows[0]['source_id']=='0' and source['matching_records']==1 and 'private' not in json.dumps(rows)
 finally:app['event_log'].close()

def test_redaction_preserves_canonical_results_and_log_envelope(tmp_path):
 s=EventLog(tmp_path/'events');s.learn({'connections':[{'password':'application'},{'password':'PASS'},{'password':'test'}]})
 try:
  s.emit('application','INFO','recorded',message='application PASS test',password='PASS');row=s.search()['rows'][0];assert row['category']=='application' and row['details']['message']=='[REDACTED] [REDACTED] [REDACTED]' and row['details']['password']=='[REDACTED]'
  r=ReportStore(tmp_path/'r.db');doc=r.preview('test',[{'source_id':'r','result':'PASS','evidence':{'state':'PASSED','password':'PASS'}}],{},spec(),s.secrets)
  assert doc['type']=='test' and doc['summary']['status']=='PASS' and doc['records'][0]['result']=='PASS' and doc['records'][0]['evidence']['password']=='[REDACTED]'
 finally:s.close()
