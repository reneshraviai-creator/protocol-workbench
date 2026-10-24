import asyncio,copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from device_setup import modbus_offset,inspect,diagnose
from test_live_monitor import setup,until
from engine import Engine

@pytest.mark.parametrize('value,notation,area,expected',[
 ('40001','reference','holding',0),('40003','reference','holding',2),('30001','reference','input',0),('00001','reference','coil',0),('10001','reference','discrete',0),('49999','reference','holding',9998),(0,'offset','holding',0),(65535,'offset','holding',65535),
])
def test_address_conventions(value,notation,area,expected):assert modbus_offset(value,notation,area)==expected

@pytest.mark.parametrize('value,notation,area',[
 ('30001','reference','holding'),('400001','reference','holding'),('-1','offset','holding'),('1.5','offset','holding'),('65536','offset','holding'),('0','reference','holding'),('40001','guess','holding'),('1','offset','invalid'),
])
def test_address_rejects_ambiguous_or_invalid(value,notation,area):
 with pytest.raises(ValueError):modbus_offset(value,notation,area)

@pytest.mark.parametrize('protocol',['opcua','modbus'])
def test_read_only_setup_test_browse_and_preview(protocol):
 async def run():
  c,p=setup(protocol);p['jobs'][0]['dynamic']=False;e=Engine(p)
  try:
   await e.start('server');await until(lambda:e.states['server']['state']=='RUNNING')
   assert (await inspect(c,'test'))['ok']
   if protocol=='opcua':
    result=await inspect({**c,'tags':[]},'browse');assert result['ok']
    tag=next(t for t in result['tags'] if t['nodeid']=='ns=2;s=Temperature');assert tag['dtype']=='Double'
    c['tags']=[tag]
   one=await inspect(c,'preview');two=await inspect(c,'preview')
   assert one['ok'] and two['ok'] and one['rows'][0]['value']==two['rows'][0]['value']
   assert one['rows'][0]['quality'].startswith('Good' if protocol=='opcua' else 'Read succeeded')
   if protocol=='modbus':assert one['rows'][0]['raw'] is not None
   else:assert one['rows'][0]['source_timestamp']
  finally:await e.stop_all()
 asyncio.run(run())


def test_preview_bad_quality():
 async def run():
  from asyncua import Server,ua
  c,_=setup('opcua');srv=Server();await srv.init();srv.set_endpoint(c['url']);await srv.register_namespace('urn:test');node=await srv.nodes.objects.add_variable(c['tags'][0]['nodeid'],'Temperature',12.5)
  async with srv:
   await node.write_attribute(ua.AttributeIds.Value,ua.DataValue(ua.Variant(0.,ua.VariantType.Double),ua.StatusCode(ua.StatusCodes.BadOutOfService)))
   result=await inspect(c,'preview');assert result['ok'] and result['rows'][0]['quality']=='BadOutOfService' and result['rows'][0]['value'] is None
 asyncio.run(run())


def test_unreachable_device_has_actionable_result():
 async def run():
  c,_=setup('modbus');result=await inspect(c,'test');assert not result['ok'] and result['action'] and result['detail']
 asyncio.run(run())


def test_setup_api_validation_and_no_project_mutation(tmp_path):
 async def run():
  import server
  from aiohttp.test_utils import TestClient,TestServer
  from model import default_project
  server.DATA=tmp_path;server.PROJECT=tmp_path/'project.json';p=default_project();app=server.create_app(p)
  async with TestClient(TestServer(app)) as client:
   b=await (await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':b['token']}
   assert (await client.post('/api/device-setup/address',json={},headers={})).status==403
   body={'value':'40001','notation':'reference','area':'holding','dtype':'Float','order':'ABCD','example':1}
   r=await (await client.post('/api/device-setup/address',json=body,headers=h)).json();assert r=={'offset':0,'words':[16256,0]}
   body['order']='CDAB';r=await (await client.post('/api/device-setup/address',json=body,headers=h)).json();assert r['words']==[0,16256]
   body['value']='30001';assert (await client.post('/api/device-setup/address',json=body,headers=h)).status==400
   c,_=setup('modbus');r=await client.post('/api/device-setup/inspect',json={'connection':c,'mode':'write'},headers=h);assert r.status==400
   assert app['engine'].project==p and not app['engine'].active() and not server.PROJECT.exists()
 asyncio.run(run())
