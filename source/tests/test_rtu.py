import asyncio,copy,os,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from model import default_project,validate_project
from engine import Engine
from drivers import Modbus
from live_monitor import MonitorHub
from serial_config import lock
from test_live_monitor import until

class SerialPair:
 def __enter__(self):
  import pty,tty
  self.fds=[];self.loop=asyncio.get_running_loop();self.errors=[];self.corrupt_next_response=False
  for _ in range(2):
   master,slave=pty.openpty();tty.setraw(slave);os.set_blocking(master,False);self.fds.extend([master,slave])
  self.ports=[os.ttyname(self.fds[1]),os.ttyname(self.fds[3])]
  def relay(source,target):
   try:
    data=os.read(source,65536)
    if data:
     if source==self.fds[0] and self.corrupt_next_response:
      data=data[:-1]+bytes([data[-1]^255]);self.corrupt_next_response=False
     os.write(target,data)
   except BlockingIOError:pass
   except OSError as e:self.errors.append(str(e))
  self.loop.add_reader(self.fds[0],relay,self.fds[0],self.fds[2]);self.loop.add_reader(self.fds[2],relay,self.fds[2],self.fds[0]);return self
 def __exit__(self,*args):
  for fd in self.fds[::2]:self.loop.remove_reader(fd)
  for fd in self.fds:os.close(fd)

def config(port):return {'id':'rtu','name':'Virtual RTU','protocol':'modbus','transport':'rtu','serial_port':port,'baudrate':19200,'bytesize':8,'parity':'N','stopbits':1,'device_id':1,'timeout':.3,'retry_seconds':.5,'tags':[{'id':'real','tag':'Real','dtype':'Float','address':0,'area':'holding','order':'CDAB','initial_value':12.5},{'id':'coil','tag':'Coil','dtype':'Boolean','address':0,'area':'coil'}]}

@pytest.mark.skipif(os.name=='nt',reason='POSIX PTY fixture; native Windows virtual COM/hardware required')
def test_rtu_server_client_monitor_and_exclusive_port():
 async def run():
  with SerialPair() as pair:
   server=config(pair.ports[0]);client=config(pair.ports[1]);p=default_project();p['connections']=[server];p['jobs']=[{'id':'server','name':'RTU simulator','kind':'server','connection':'rtu','interval':.1,'dynamic':False}];validate_project(p);e=Engine(p);adapter=Modbus(client);hub=MonitorHub()
   try:
    await e.start('server');await until(lambda:e.states['server']['state'] in ('RUNNING','ERROR'));assert e.states['server']['state']=='RUNNING',e.states
    await adapter.connect();await adapter.prepare(client['tags'],write=True);values=await adapter.read(client['tags']);assert values['real']==12.5
    await adapter.write(client['tags'],{'real':37.25,'coil':True});values=await adapter.read(client['tags']);assert values=={'coil':True,'real':37.25}
    pair.corrupt_next_response=True
    with pytest.raises(Exception):await adapter.read([client['tags'][0]])
    assert (await adapter.read([client['tags'][0]]))['real']==37.25
    with pytest.raises(ValueError,match='already in use'):lock(client)
    await adapter.close();m=hub.start(client,['real','coil'],{'poll_seconds':.1});await until(lambda:m.events>=4)
    assert m.snapshot()['rows'][0]['value']==37.25 and m.snapshot()['transport']=='rtu'
    assert any(t['kind']=='bytes' for t in m.traffic) and any(t['kind']=='pdu' for t in m.traffic)
    assert not m.snapshot()['rows'][0]['writable_area']
    await m.stop()
    # Reopen after stop, and verify wrong-unit timeout does not silently retry writes.
    bad=Modbus({**client,'device_id':2});await bad.connect()
    try:
     with pytest.raises(Exception):await bad.read(client['tags'])
    finally:await bad.close()
    await adapter.connect();assert (await adapter.read(client['tags']))['real']==37.25
   finally:await adapter.close();await hub.stop_all();await e.stop_all()
 asyncio.run(run())

@pytest.mark.parametrize('change',[{'serial_port':'socket://localhost:1234'},{'serial_port':''},{'baudrate':0},{'baudrate':9600.5},{'bytesize':7},{'parity':'X'},{'stopbits':3},{'device_id':0},{'device_id':1.5}])
def test_invalid_rtu_settings(change):
 p=default_project();p['connections']=[{**config('/dev/virtual'),**change}]
 with pytest.raises(ValueError):validate_project(p)

def test_rtu_template_bindings_and_resources():
 from templates import portable,instantiate
 from resources import scenario_plan,ResourceRegistry
 p=default_project();p['connections']=[config('/dev/virtual')];s={'name':'Serial read','project':p,'steps':[{'action':'read','connection':'rtu','scope':'all','variable':'values'}]}
 portable_doc=portable(s);assert portable_doc['parameters'][0]['field']=='serial_port';assert '/dev/virtual' not in str(portable_doc)
 restored=instantiate(portable_doc,{'rtu':'/dev/new-port'});assert restored['project']['connections'][0]['serial_port']=='/dev/new-port'

@pytest.mark.skipif(os.name=='nt',reason='POSIX virtual serial fixture')
def test_rtu_source_to_tcp_gateway():
 from test_scenarios import port
 async def run():
  with SerialPair() as pair:
   a=config(pair.ports[0]);source=config(pair.ports[1]);source['id']='reader';dest={**config('unused'),'id':'tcp','transport':'tcp','host':'127.0.0.1','port':port()}
   p=default_project();p['connections']=[a,source,dest];p['jobs']=[{'id':'serial-server','name':'RTU server','kind':'server','connection':a['id'],'interval':.1,'dynamic':False},{'id':'tcp-server','name':'TCP server','kind':'server','connection':'tcp','interval':.1,'dynamic':False},{'id':'route','name':'RTU → TCP','kind':'gateway','source':source['id'],'connection':'tcp','interval':.1,'mappings':[{'source':'real','destination':'real','scale':2,'offset':1}]}]
   validate_project(p);e=Engine(p);reader=Modbus(dest)
   try:
    for job in ('serial-server','tcp-server','route'):await e.start(job)
    await until(lambda:e.states['route'].get('cycles',0)>=2)
    await reader.connect();assert (await reader.read(dest['tags']))['real']==26
    assert e.states['route']['state']=='RUNNING'
   finally:await reader.close();await e.stop_all()
 asyncio.run(run())

@pytest.mark.skipif(os.name=='nt',reason='Uses POSIX paths')
def test_failed_open_releases_serial_lock():
 async def run():
  c=config('/dev/protocol-workbench-missing-port');client=Modbus(c)
  with pytest.raises(Exception):await client.connect()
  with lock(c):pass
 asyncio.run(run())

def test_serial_roles_conflict_even_for_different_units():
 from resources import ResourceRegistry,job_plan
 a=config('/dev/example');b={**config('/dev/example'),'id':'second','device_id':2};p=default_project();p['connections']=[a,b];registry=ResourceRegistry()
 registry.acquire('scan','First RTU scan',job_plan(p,{'kind':'scan','connection':a['id']}),'job')
 with pytest.raises(ValueError,match='Serial port'):registry.acquire('second','Other RTU scan',job_plan(p,{'kind':'scan','connection':b['id']}),'job')
 p['jobs']=[{'id':'route','name':'Bad bus gateway','kind':'gateway','connection':b['id'],'source':a['id'],'interval':1,'mappings':[{'source':'real','destination':'real'}]}]
 with pytest.raises(ValueError,match='separate serial'):validate_project(p)
