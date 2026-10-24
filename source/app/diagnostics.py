"""Offline localhost acceptance check. Never reads a saved project's endpoints."""
import asyncio
import json
from pathlib import Path
import socket
import time
from asyncua import Server,ua
from pymodbus.server import ModbusTcpServer
from pymodbus.datastore import ModbusDeviceContext,ModbusSequentialDataBlock,ModbusServerContext
from drivers import OPC,Modbus
from model import encode,decode,transform,validate_project,default_project

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]

async def run_diagnostics():
    checks=[];server=mbserver=uc=mc=None
    try:
        for order in ('ABCD','BADC','CDAB','DCBA'):
            assert decode(encode(123.25,'Float',order),'Float',order)==123.25
        assert transform(2**64-1,{'scale':1,'offset':0},'UInt64')==2**64-1
        checks.append('Numeric conversion, all four word orders, exact UInt64')
        server=Server();await server.init();up=free_port();server.set_endpoint(f'opc.tcp://127.0.0.1:{up}');server.set_security_policy([ua.SecurityPolicyType.NoSecurity]);idx=await server.register_namespace('urn:workbench:self-test');tags=[]
        for name,kind,value in [('float','Float',12.25),('double','Double',99.5),('boolean','Boolean',True)]:
            node=await server.nodes.objects.add_variable(ua.NodeId(name,idx),name,ua.Variant(value,getattr(ua.VariantType,kind)));await node.set_writable();tags.append({'id':name,'tag':name,'dtype':kind,'nodeid':node.nodeid.to_string(),'index':len(tags)+1})
        await server.start();uc=OPC({'url':f'opc.tcp://127.0.0.1:{up}','security':'None','timeout':5});await uc.connect();await uc.prepare(tags,write=True);await uc.write(tags,{'float':42.125,'double':123.5,'boolean':False});values=await uc.read(tags);assert values=={'float':42.125,'double':123.5,'boolean':False};checks.append('OPC UA scalar read/write: Float, Double, Boolean')
        mp=free_port();device=ModbusDeviceContext(**{k:ModbusSequentialDataBlock(1,[0]*100) for k in ('co','di','hr','ir')});mbserver=ModbusTcpServer(ModbusServerContext(devices={1:device},single=False),address=('127.0.0.1',mp));await mbserver.serve_forever(background=True)
        mt=[{'id':'float','tag':'Float','dtype':'Float','address':0,'area':'holding','order':'CDAB'},{'id':'coil','tag':'Coil','dtype':'Boolean','address':0,'area':'coil'}];mc=Modbus({'host':'127.0.0.1','port':mp,'device_id':1,'timeout':5});await mc.connect();await mc.prepare(mt,write=True);await mc.write(mt,{'float':42.125,'coil':True});assert await mc.read(mt)=={'float':42.125,'coil':True};checks.append('Modbus TCP holding registers, coils, address zero, word swap')
        # One source-to-destination transfer with conversion, then verify the result.
        source=await uc.read([tags[0]]);v=transform(source['float'],{'scale':2,'offset':1},'Float');await mc.write([mt[0]],{'float':v});assert (await mc.read([mt[0]]))['float']==85.25;checks.append('OPC UA to Modbus conversion and destination readback')
        validate_project(default_project());checks.append('Project schema validation')
        return {'ok':True,'checks':checks,'message':'All local checks passed. No saved endpoints were contacted.'}
    except Exception as error:
        return {'ok':False,'checks':checks,'error':f'{type(error).__name__}: {error}'}
    finally:
        for client in (uc,mc):
            if client:
                try:await client.close()
                except Exception:pass
        if server:
            try:await server.stop()
            except Exception:pass
        if mbserver:
            try:await mbserver.shutdown()
            except Exception:pass

if __name__=='__main__':
    result=asyncio.run(run_diagnostics());print(json.dumps(result,indent=2));raise SystemExit(0 if result['ok'] else 1)
