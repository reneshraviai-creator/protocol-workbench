"""Shared RTU validation and per-port process lock."""
import hashlib,os,tempfile
from pathlib import Path
from runtime_lock import acquire

def is_rtu(c):return c.get('protocol')=='modbus' and c.get('transport','tcp')=='rtu'
def port_key(port):
 return port.upper().removeprefix('\\\\.\\') if os.name=='nt' else os.path.realpath(port)
def options(c):return {k:c.get(k,v) for k,v in [('baudrate',19200),('bytesize',8),('parity','N'),('stopbits',1)]}
def validate(c):
 port=c.get('serial_port')
 if not isinstance(port,str) or not port.strip() or port!=port.strip() or '://' in port or any(ch in port for ch in ('\0','\n','\r')):raise ValueError('Select a local serial port, for example COM3 or /dev/cu.usbserial; network serial URLs are not supported')
 for key,allowed,default in [('baudrate',[1200,2400,4800,9600,19200,38400,57600,115200,230400],19200),('bytesize',[8],8),('stopbits',[1,2],1)]:
  value=c.get(key,default)
  if type(value) is not int or value not in allowed:raise ValueError(f'Invalid RTU {key}; supported values: {allowed}')
 if c.get('parity','N') not in ('N','E','O'):raise ValueError('RTU parity must be N, E or O')
 if type(c.get('device_id',1)) is not int or not 1<=c.get('device_id',1)<=247:raise ValueError('RTU unit ID must be an integer 1..247; broadcast writes are not supported')
def lock(c):
 path=Path(tempfile.gettempdir())/('protocol-workbench-serial-'+hashlib.sha256(port_key(c['serial_port']).encode()).hexdigest()+'.lock')
 try:return acquire(path)
 except RuntimeError as error:raise ValueError('Serial port is already in use by another app connection; stop that monitor/job before opening this port') from error
