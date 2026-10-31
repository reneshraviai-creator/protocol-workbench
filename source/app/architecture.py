"""Pure architecture drafts: reuse existing validation and engine; never open devices."""
import copy,re
from model import default_project,validate_project,uid
from simulation_wizard import build_simulation
DEVICE_TYPES={'Common':['PLC','HMI / SCADA','Protocol gateway','Energy meter','Storage tank','Pump','Generic device','Local scanner'],'Data centre':['UPS','PDU','Server rack','Chiller','CRAC / CRAH','Generator','ATS','Battery bank','Leak detector','BMS controller','Temperature sensor'],'Pharma':['Bioreactor','Mixing vessel','CIP skid','Purified water skid','Autoclave','Cleanroom','AHU','Cold storage','Dosing pump','Filling machine','Filter skid'],'Manufacturing':['Conveyor','Robot cell','CNC machine','Motor / VFD','Compressor','Packaging line','Inspection camera','Weighing station','Industrial oven']}
ALL_TYPES={v for group in DEVICE_TYPES.values() for v in group}
def visual(value):
 if not isinstance(value,dict):raise ValueError('Device appearance must be an object')
 kind=value.get('type','Generic device');zone=value.get('zone','')
 if kind not in ALL_TYPES:raise ValueError('Choose a supported device type')
 if not isinstance(zone,str) or len(zone)>80:raise ValueError('Area name must be at most 80 characters')
 result={'type':kind,'zone':zone}
 if 'source_id' in value:
  if not isinstance(value['source_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value['source_id']):raise ValueError('Invalid architecture reference')
  result['source_id']=value['source_id']
 return result

def device_draft(spec):
 p=build_simulation(spec)['project'];p['connections'][0]['visual']=visual(spec.get('visual',{}))
 mode=spec.get('run_mode','continuous')
 if mode not in ('continuous','timed'):raise ValueError('Choose continuous or timed operation')
 if mode=='timed':p['jobs'][0]['duration_seconds']=float(spec.get('duration',60))
 validate_project(p);return p

def scanner_draft(source,serial_port=None):
 p=default_project();p['connections']=[copy.deepcopy(source)];validate_project(p)
 c=copy.deepcopy(source);c.update(id=uid(),name=source['name']+' scanner');c['visual']={'type':'Local scanner','zone':source.get('visual',{}).get('zone',''),'source_id':source['id']}
 for t in c['tags']:t['id']=uid()
 if c['protocol']=='modbus' and c.get('transport')=='rtu':
  from serial_config import port_key
  if not serial_port or port_key(serial_port)==port_key(source['serial_port']):raise ValueError('RTU scanner needs a different paired serial port or adapter')
  c['serial_port']=serial_port
 elif c['protocol']=='modbus' and c.get('host') in ('0.0.0.0','::'):c['host']='127.0.0.1' if c['host']=='0.0.0.0' else '::1'
 p['connections']=[c];validate_project(p);return c

def scenario_devices(app):
 rows=[]
 for key,task in app['runner'].tasks.items():
  if task.done():continue
  r=app['catalogue'].get('runs',key)
  for c in r.get('snapshot',{}).get('project',{}).get('connections',[]):
   endpoint=c.get('url','') if c['protocol']=='opcua' else (str(c.get('serial_port','')) if c.get('transport')=='rtu' else str(c.get('host',''))+':'+str(c.get('port',502)))
   if c['protocol']=='opcua':
    from urllib.parse import urlsplit,urlunsplit
    u=urlsplit(endpoint);host=u.hostname or '';host='['+host+']' if ':' in host else host
    endpoint=urlunsplit((u.scheme,host+(':'+str(u.port) if u.port else ''),u.path,'',''))
   rows.append({'run':key,'scenario':r['name'],'state':r['state'],'name':c['name'],'protocol':c['protocol'],'transport':c.get('transport','tcp'),'endpoint':endpoint,'points':len(c['tags'])})
 return rows


def validate_presentation_views(views):
 """Named display scopes are configuration only; they never start/stop jobs."""
 if not isinstance(views,list) or len(views)>50:raise ValueError('Maximum 50 presentation views')
 keys=set()
 for view in views:
  if not isinstance(view,dict):raise ValueError('Presentation view must be an object')
  key=view.get('id');name=view.get('name');description=view.get('description','');devices=view.get('devices')
  if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key) or key in keys:raise ValueError('Presentation views need unique valid IDs')
  keys.add(key)
  if not isinstance(name,str) or not name.strip() or len(name)>100:raise ValueError('Presentation title needs 1..100 characters')
  if not isinstance(description,str) or len(description)>400:raise ValueError('Test description must be at most 400 characters')
  if not isinstance(devices,list) or not 1<=len(devices)<=100 or len(set(str(x) for x in devices))!=len(devices):raise ValueError('Select 1..100 unique devices per view')
  if any(not isinstance(k,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',k) for k in devices):raise ValueError('Invalid presentation device ID')
 return views
