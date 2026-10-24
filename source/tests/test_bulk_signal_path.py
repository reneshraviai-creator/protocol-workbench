import asyncio,copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from signal_path import demo,build_bulk
from scenarios import Catalogue,Runner

def body():return {'source_connection':'path-source','destination_connection':'path-target','csv':'source_tag,destination_tag,value,scale,offset\nProbe,Observed,23.5,2,10\n'}

def test_csv_typed_preview_and_no_mutation():
 p=demo()['project'];before=copy.deepcopy(p);s,rows=build_bulk(p,body())
 assert p==before and rows[0]['expected']==57 and s['project']['jobs']==[]
 assert s['steps'][0]['continue_on_failure'] is True

@pytest.mark.parametrize('csv',['source_tag,value\nProbe,1','source_tag,destination_tag,value\nMissing,Observed,1','source_tag,destination_tag,value\nProbe,Observed,NaN','source_tag,destination_tag,value\nProbe,Observed,1\nProbe,Observed,2'])
def test_bad_csv(csv):
 with pytest.raises(ValueError):build_bulk(demo()['project'],{**body(),'csv':csv})

def test_1000_mapping_validation():
 p=demo()['project'];a,b=p['connections'];a['tags']=[{**a['tags'][0],'id':f'a{i}','tag':f'A{i}','nodeid':f'ns=2;s=A{i}'} for i in range(1000)];b['tags']=[{**b['tags'][0],'id':f'b{i}','tag':f'B{i}','address':i*2} for i in range(1000)]
 s,rows=build_bulk(p,{'source_connection':a['id'],'destination_connection':b['id'],'mappings':[{'source_tag':f'A{i}','destination_tag':f'B{i}','value':i+1} for i in range(1000)]})
 assert len(s['steps'])==1000 and len(rows)==1000

def test_continue_failure_and_error_and_cancellation(tmp_path,monkeypatch):
 async def run():
  p=demo()['project'];s,_=build_bulk(p,body());step=s['steps'][0];s['steps']=[copy.deepcopy(step) for _ in range(3)];s['id']='bulk'
  store=Catalogue(tmp_path/'test.db');store.put('scenarios',s);runner=Runner(store)
  calls=[]
  async def fake(step,project,engine,variables,result):
   calls.append(1)
   if len(calls)==1:raise AssertionError('mismatch')
   if len(calls)==2:raise ValueError('bad quality')
  monkeypatch.setattr(runner,'step',fake);key=await runner.start('bulk');await runner.tasks[key];r=store.get('runs',key)
  assert r['state']=='ERROR' and [x['state'] for x in r['steps']]==['FAILED','ERROR','PASSED'] and not runner.registry.owners
  async def cancel(*args):raise asyncio.CancelledError
  monkeypatch.setattr(runner,'step',cancel);key=await runner.start('bulk');await runner.tasks[key];r=store.get('runs',key)
  assert r['state']=='CANCELLED' and r['skipped']==2 and not runner.registry.owners
 asyncio.run(run())

def test_real_bulk_pass_mismatch_then_pass(tmp_path):
 from test_scenarios import port
 async def run():
  lab=demo();p=lab['project'];a,b=p['connections'];a['url']=f'opc.tcp://127.0.0.1:{port()}';b['port']=port()
  at=a['tags'][0];bt=b['tags'][0]
  a['tags']=[{**at,'id':f'a{i}','tag':f'A{i}','nodeid':f'ns=2;s=A{i}'} for i in range(3)]
  b['tags']=[{**bt,'id':f'b{i}','tag':f'B{i}','address':i*2} for i in range(3)]
  p['jobs'][2]['mappings']=[{'source':f'a{i}','destination':f'b{i}','scale':2,'offset':10} for i in [0,2]]
  s,_=build_bulk(p,{'source_connection':a['id'],'destination_connection':b['id'],'mappings':[{'source_tag':f'A{i}','destination_tag':f'B{i}','value':23.5+i,'scale':2,'offset':10,'max_delay':.5} for i in range(3)]})
  s['project']=p;s['steps']=lab['steps'][:3]+s['steps'];s['id']='real-bulk'
  store=Catalogue(tmp_path/'real.db');store.put('scenarios',s);runner=Runner(store);key=await runner.start(s['id']);await runner.tasks[key];r=store.get('runs',key)
  assert r['state']=='FAILED',r
  assert [x['state'] for x in r['steps'][-3:]]==['PASSED','FAILED','PASSED']
  assert r['steps'][-1]['observed']==61 and not runner.registry.owners
 asyncio.run(run())
