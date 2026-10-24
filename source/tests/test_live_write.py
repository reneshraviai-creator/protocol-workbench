import asyncio,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from live_write import preview,write
from live_monitor import LiveMonitor,MonitorHub
from resources import ResourceRegistry,empty,add_writes
from engine import Engine
from test_live_monitor import setup,until

@pytest.mark.parametrize('protocol',['opcua','modbus'])
def test_manual_write_real_protocol(protocol):
 async def run():
  c,p=setup(protocol);p['jobs'][0]['dynamic']=False;e=Engine(p);hub=MonitorHub();registry=ResourceRegistry()
  try:
   await e.start('server');await until(lambda:e.states['server']['state']=='RUNNING');m=hub.start(c,['t'],{'poll_seconds':.1,'publishing_ms':100});await until(lambda:m.state=='RUNNING')
   tag,value,r=preview(m,'t','42.5');assert value==42.5
   result=await write(m,'t','42.5',registry)
   assert result['write_outcome']=='ACKNOWLEDGED' and result['readback']==42.5 and not registry.owners
   await until(lambda:m.values.get('t',{}).get('value')==42.5)
   plan=empty();add_writes(plan,c,c['tags']);registry.acquire('job','test',plan,'test')
   with pytest.raises(ValueError):await write(m,'t',99,registry)
   assert 'job' in registry.owners
  finally:await hub.stop_all();await e.stop_all()
 asyncio.run(run())

def test_quality_filters_and_address():
 c,_=setup('modbus');m=LiveMonitor(c,c['tags'],{});m.state='RUNNING';m.record(c['tags'][0],12)
 assert m.snapshot(quality='good')['total']==1
 row=m.snapshot()['rows'][0];assert row['address']==0 and row['order']=='ABCD' and row['writable_area']
 m.state='RECONNECTING';assert m.snapshot(quality='good')['total']==0 and m.snapshot(quality='attention')['total']==1
 with pytest.raises(ValueError):m.snapshot(quality='invalid')

def test_write_preview_rejects_invalid_and_stopped():
 c,_=setup('modbus');m=LiveMonitor(c,c['tags'],{})
 with pytest.raises(ValueError):preview(m,'t',1)
 m.state='RUNNING'
 with pytest.raises(ValueError):preview(m,'t','NaN')
 m.tags[0]['area']='input'
 with pytest.raises(ValueError):preview(m,'t',1)

def test_unknown_write_outcome_and_release(monkeypatch):
 async def run():
  c,_=setup('modbus');m=LiveMonitor(c,c['tags'],{});m.state='RUNNING';r=ResourceRegistry()
  class Fake:
   async def connect(self):pass
   async def prepare(self,*a,**kw):pass
   async def write(self,*a):raise ConnectionError('lost acknowledgement')
   async def close(self):pass
  monkeypatch.setattr('live_write.adapter',lambda c:Fake());result=await write(m,'t',23,r)
  assert result['write_outcome']=='UNKNOWN' and not r.owners
 asyncio.run(run())

def test_6500_point_modbus_monitor():
 async def run():
  c,p=setup('modbus');base=c['tags'][0];c['tags']=[{**base,'id':f't{i}','tag':f'Value{i:04}','address':i*2} for i in range(6500)];p['jobs'][0]['dynamic']=False
  e=Engine(p);hub=MonitorHub()
  try:
   await e.start('server');await until(lambda:e.states['server']['state']=='RUNNING');m=hub.start(c,[t['id'] for t in c['tags']],{'poll_seconds':.1});await until(lambda:m.events>=6500,20)
   s=m.snapshot(page=129);assert s['points']==6500 and len(s['rows'])==50 and s['rows'][-1]['tag']=='Value6499'
   assert m.snapshot(query='Value6499')['total']==1 and m.snapshot(quality='good')['total']==6500
   assert m.depth==7
  finally:await hub.stop_all();await e.stop_all()
 asyncio.run(run())

def test_api_write_requires_confirmation_and_preview_is_offline(tmp_path,monkeypatch):
 async def run():
  import server
  from aiohttp.test_utils import TestClient,TestServer
  c,p=setup('modbus');monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');app=server.create_app(p)
  m=LiveMonitor(c,c['tags'],{});m.state='RUNNING';app['monitors'].monitors[m.id]=m
  async with TestClient(TestServer(app)) as client:
   token=(await (await client.get('/api/bootstrap')).json())['token'];h={'X-Workbench-Token':token};url='/api/monitors/'+m.id+'/write'
   assert (await client.post(url,json={'tag':'t','value':1})).status==403
   assert (await client.post(url,json={'tag':'t','value':1},headers=h)).status==400
   res=await client.post(url,json={'mode':'preview','tag':'t','value':12.5},headers=h)
   assert res.status==200 and (await res.json())['value']==12.5
   assert m.events==0 and not app['resources'].owners and not app['engine'].active()
 asyncio.run(run())
