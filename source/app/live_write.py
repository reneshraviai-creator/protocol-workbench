"""Explicit one-shot manual writes, separate from read-only acquisition."""
import asyncio,time
from drivers import adapter
from model import cast,view_value,encode
from resources import empty,add_writes


def preview(m,tag_id,value):
    if m.demo:raise ValueError('The temporary RTU demo is observation-only; its ports are not manual-write targets')
    if m.connection.get('transport')=='rtu':raise ValueError('Stop the RTU monitor, then use Tags & registers to write; a serial port supports one active app connection')
    tag=next((t for t in m.tags if t['id']==tag_id),None)
    if not tag:raise ValueError('Choose a monitored tag')
    if m.state!='RUNNING':raise ValueError('Monitor must be running before a manual write')
    if m.connection['protocol']=='modbus' and tag.get('area') in ('input','discrete'):raise ValueError('This Modbus area is read-only')
    typed=cast(value,tag['dtype'])
    return tag,typed,{'device':m.connection['name'],'endpoint':m.connection.get('url') if m.connection['protocol']=='opcua' else f"{m.connection['host']}:{m.connection.get('port',502)}",'tag':tag['tag'],'dtype':tag['dtype'],'value':view_value(typed,tag['dtype']),'target':tag.get('nodeid') if m.connection['protocol']=='opcua' else f"Unit {m.connection.get('device_id',1)} · {tag.get('area','holding')} offset {tag['address']} · {tag.get('order','ABCD')}",'raw_words':encode(typed,tag['dtype'],tag.get('order','ABCD')) if m.connection['protocol']=='modbus' else None}


async def write(m,tag_id,value,registry):
    tag,typed,detail=preview(m,tag_id,value);owner='manual-live:'+__import__('uuid').uuid4().hex
    plan=empty();add_writes(plan,m.connection,[tag]);registry.acquire(owner,'Live inspector manual write',plan,'manual write')
    client=adapter(m.connection);result={**detail,'write_outcome':'NOT_ATTEMPTED','readback':None,'time':time.time()}
    try:
        async with asyncio.timeout(30):
            await client.connect();await client.prepare([tag],write=True)
            result['write_outcome']='UNKNOWN'
            await client.write([tag],{tag['id']:typed});result['write_outcome']='ACKNOWLEDGED'
            try:
                values=await client.read([tag]);result['readback']=view_value(values[tag['id']],tag['dtype']);result['readback_words']=getattr(client,'last_raw',{}).get(tag['id'])
            except Exception as error:result['readback_error']=str(error)
    except Exception as error:result['error']=f'{type(error).__name__}: {error}'
    finally:
        m.log(f"Manual write {tag['tag']}: {result['write_outcome']}")
        try:await asyncio.wait_for(client.close(),3)
        except Exception:pass
        registry.release(owner)
    return result
