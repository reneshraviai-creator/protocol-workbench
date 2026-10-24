"""Temporary local RTU lab. Never mutates the saved workspace or opens hardware."""
import asyncio,os,socket,time
from pymodbus import FramerType
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.server import ModbusSerialServer,ModbusTcpServer
from pymodbus.datastore import ModbusDeviceContext,ModbusSequentialDataBlock,ModbusServerContext
from drivers import Modbus
from live_monitor import LiveMonitor,monitor_settings
from model import encode
from serial_config import lock

class VirtualPair:
 def __init__(self):self.fds=[];self.loop=None;self.ports=[]
 def open(self):
  import pty,tty
  self.loop=asyncio.get_running_loop()
  try:
   for _ in range(2):
    master,slave=pty.openpty();self.fds.extend([master,slave]);tty.setraw(slave);os.set_blocking(master,False);self.ports.append(os.ttyname(slave))
   self.loop.add_reader(self.fds[0],self.relay,self.fds[0],self.fds[2]);self.loop.add_reader(self.fds[2],self.relay,self.fds[2],self.fds[0])
  except BaseException:self.close();raise
  return self
 def relay(self,source,target):
  try:
   data=os.read(source,4096)
   if data:os.write(target,data)
  except BlockingIOError:pass
  except OSError:pass
 def close(self):
  if self.loop:
   for fd in self.fds[::2]:self.loop.remove_reader(fd)
  for fd in self.fds:
   try:os.close(fd)
   except OSError:pass
  self.fds=[];self.ports=[]

class RTUDemo:
 def __init__(self,hub,serial=None,lifetime=1800):
  self.hub=hub;self.serial=os.name!='nt' if serial is None else serial;self.lifetime=lifetime;self.guard=asyncio.Lock();self.monitor=None;self.server=None;self.pair=None;self.task=None;self.port_lock=None;self.started=None;self.state='STOPPED';self.error='';self.cycles=0;self._stopping=False
 def summary(self):return {'state':self.state,'error':self.error,'monitor':self.monitor.id if self.monitor else None,'mode':'Virtual serial RTU pair' if self.serial else 'RTU frames over loopback TCP (not COM-port validation)','cycles':self.cycles,'expires_in':max(0,round(self.lifetime-(time.monotonic()-self.started))) if self.started and self.state=='RUNNING' else None,'temporary':True}
 async def start(self):
  async with self.guard:
   if self.state=='RUNNING' and self.monitor and self.monitor.state=='RUNNING':return self.summary()
   await self._stop()
   if sum(m.task is not None and not m.task.done() for m in self.hub.monitors.values())>=8:raise ValueError('Stop a monitor before starting the RTU demo (8 monitors supported)')
   self.state='STARTING';self.error='';self.cycles=0
   try:
    async with asyncio.timeout(15):
     tags=[{'id':f'rtu-demo-{i}','tag':f'Demo_REAL_{i+1:03}','dtype':'Float','area':'holding','address':i*2,'order':'CDAB'} for i in range(20)]
     config={'id':'temporary-rtu-demo','name':'Local RTU demo · temporary','protocol':'modbus','device_id':1,'timeout':1,'retry_seconds':.5,'tags':tags}
     device=ModbusDeviceContext(**{k:ModbusSequentialDataBlock(1,[0]*256) for k in ('co','di','hr','ir')});context=ModbusServerContext(devices={1:device},single=False)
     if self.serial:
      self.pair=VirtualPair().open();config.update(transport='rtu',serial_port=self.pair.ports[1],baudrate=19200,bytesize=8,parity='N',stopbits=1)
      self.port_lock=lock({**config,'serial_port':self.pair.ports[0]});self.server=ModbusSerialServer(context,port=self.pair.ports[0],baudrate=19200,bytesize=8,parity='N',stopbits=1)
     else:
      # Select an ephemeral loopback port; never choose/accept a hardware endpoint.
      self.server=ModbusTcpServer(context,address=('127.0.0.1',0),framer=FramerType.RTU);config.update(transport='tcp',host='127.0.0.1',port=0)
     await self.server.serve_forever(background=True)
     if not self.serial:config['port']=self.server.transport.sockets[0].getsockname()[1]
     self.monitor=LiveMonitor(config,tags,monitor_settings({'poll_seconds':.25}));self.monitor.demo=True
     if not self.serial:
      def factory(c):
       adapter=Modbus(c);adapter.client=AsyncModbusTcpClient('127.0.0.1',port=config['port'],framer=FramerType.RTU,timeout=1,retries=0,trace_packet=lambda sending,data:self.monitor.trace('bytes',sending,data),trace_pdu=lambda sending,data:self.monitor.trace('pdu',sending,data));return adapter
      self.monitor.driver_factory=factory
     self.hub.monitors[self.monitor.id]=self.monitor;self.monitor.start();self.started=time.monotonic();self._stopping=False;self.task=asyncio.create_task(self._generate(device,tags))
     while self.monitor.state!='RUNNING' or self.monitor.events<20:
      if self.monitor.state=='ERROR':raise ValueError(self.monitor.error)
      await asyncio.sleep(.02)
     self.state='RUNNING';return self.summary()
   except BaseException as error:
    await self._stop();self.state='ERROR';self.error=f'{type(error).__name__}: {error}';raise
 async def _generate(self,device,tags):
  try:
   while True:
    if time.monotonic()-self.started>=self.lifetime or self.monitor.state in ('STOPPED','ERROR'):break
    for i,t in enumerate(tags):device.setValues(3,t['address'],encode(float(i+1+(self.cycles%100)/10),t['dtype'],t['order']))
    self.cycles+=1;await asyncio.sleep(.5)
  except asyncio.CancelledError:raise
  finally:
   if not self._stopping:
    # Avoid cancelling/awaiting this task from itself.
    asyncio.create_task(self._auto_stop(self.monitor))
 async def _auto_stop(self,monitor):
  async with self.guard:
   if self.monitor is monitor:await self._stop()
 async def _stop(self):
  self._stopping=True
  if self.task and self.task is not asyncio.current_task():
   self.task.cancel()
   try:await self.task
   except asyncio.CancelledError:pass
  self.task=None
  try:
   if self.monitor:
    try:await self.monitor.stop()
    finally:self.hub.monitors.pop(self.monitor.id,None);self.monitor=None
  finally:
   try:
    if self.server:await asyncio.wait_for(self.server.shutdown(),5)
   finally:
    self.server=None
    if self.port_lock:self.port_lock.close();self.port_lock=None
    if self.pair:self.pair.close();self.pair=None
    self.state='STOPPED';self.started=None
 async def stop(self):
  async with self.guard:await self._stop()
  return self.summary()
