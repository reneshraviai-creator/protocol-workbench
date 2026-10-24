import asyncio,os,sys,copy
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from rtu_demo import RTUDemo
from live_monitor import MonitorHub
from test_live_monitor import until

@pytest.mark.parametrize('serial',[False,True] if os.name!='nt' else [False])
def test_demo_dynamic_data_idempotent_restart_and_cleanup(serial):
 async def run():
  hub=MonitorHub();demo=RTUDemo(hub,serial=serial)
  try:
   a=await demo.start();m=demo.monitor;assert a['state']=='RUNNING' and m.demo
   before=m.values['rtu-demo-0']['value'];await until(lambda:m.values['rtu-demo-0']['value']!=before)
   assert len(m.values)==20 and any(t['kind']=='bytes' for t in m.traffic)
   assert not m.snapshot()['rows'][0]['writable_area']
   from live_write import preview
   with pytest.raises(ValueError,match='observation-only'):preview(m,'rtu-demo-0',42)
   assert (await demo.start())['monitor']==a['monitor'] and len(hub.monitors)==1
   pair=demo.pair;fds=list(pair.fds) if pair else []
   await demo.stop();assert not hub.monitors and demo.server is None and demo.task is None
   for fd in fds:
    with pytest.raises(OSError):os.fstat(fd)
   await demo.start();assert demo.monitor.id!=a['monitor']
  finally:await demo.stop()
 asyncio.run(run())

def test_stopping_monitor_and_expiry_cleanup():
 async def run():
  hub=MonitorHub();demo=RTUDemo(hub,serial=False,lifetime=1)
  try:
   await demo.start();await until(lambda:demo.state=='STOPPED',5);assert not hub.monitors
   demo.lifetime=10;await demo.start();await demo.monitor.stop();await until(lambda:demo.state=='STOPPED',5)
  finally:await demo.stop()
 asyncio.run(run())

def test_api_demo_preserves_workspace_and_stop_all(tmp_path,monkeypatch):
 async def run():
  import server
  from model import default_project
  from aiohttp.test_utils import TestClient,TestServer
  monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json');p=default_project();p['name']='Keep my workspace';before=copy.deepcopy(p);app=server.create_app(p)
  async with TestClient(TestServer(app)) as client:
   token=(await (await client.get('/api/bootstrap')).json())['token'];headers={'X-Workbench-Token':token}
   assert (await client.post('/api/rtu-demo/start')).status==403
   row=await (await client.post('/api/rtu-demo/start',headers=headers)).json();assert row['state']=='RUNNING',row
   assert app['engine'].project==before and not server.PROJECT.exists()
   assert (await client.post('/api/watchlists',json={'monitor':row['monitor'],'name':'Invalid temporary watchlist'},headers=headers)).status==400
   await client.post('/api/action',json={'action':'stop_all'},headers=headers)
   assert app['rtu_demo'].state=='STOPPED' and not app['monitors'].monitors
 asyncio.run(run())

def test_failed_start_and_concurrent_start_cleanup(monkeypatch):
 async def run():
  import rtu_demo
  hub=MonitorHub();demo=RTUDemo(hub,serial=False)
  original=rtu_demo.ModbusTcpServer.serve_forever
  async def fail(*args,**kw):raise RuntimeError('deliberate startup failure')
  monkeypatch.setattr(rtu_demo.ModbusTcpServer,'serve_forever',fail)
  with pytest.raises(RuntimeError,match='deliberate'):await demo.start()
  assert demo.state=='ERROR' and demo.server is None and not hub.monitors
  monkeypatch.setattr(rtu_demo.ModbusTcpServer,'serve_forever',original)
  try:
   a,b=await asyncio.gather(demo.start(),demo.start());assert a['monitor']==b['monitor'] and len(hub.monitors)==1
  finally:await demo.stop()
 asyncio.run(run())
