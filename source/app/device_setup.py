"""Read-only onboarding helpers and explicit Modbus address conventions."""
import asyncio
from model import default_project,validate_project,uid,TYPES,view_value,width,encode
from drivers import adapter
from asyncua import ua


def modbus_offset(value,notation,area):
    if area not in ('holding','input','coil','discrete'):raise ValueError('Choose a Modbus data area')
    text=str(value)
    if not text.isascii() or not text.isdigit():raise ValueError('Address must be a nonnegative whole number')
    n=int(text)
    if notation=='offset':
        if not 0<=n<=65535:raise ValueError('Zero-based offset must be 0..65535')
        return n
    if notation!='reference':raise ValueError('Choose reference or zero-based address notation')
    base={'holding':40001,'input':30001,'coil':1,'discrete':10001}[area]
    if not base<=n<=base+9998:raise ValueError('Reference does not match the selected area (holding 40001, input 30001, coil 00001, discrete 10001). Use zero-based notation for extended addresses.')
    return n-base


def diagnose(error):
    text=str(error);low=(type(error).__name__+' '+text).lower()
    if isinstance(error,(TimeoutError,asyncio.TimeoutError)) or 'timeout' in low:return {'category':'Timeout','action':'Check the device address, route/firewall and request timeout. Confirm the server is running.'}
    if 'certificate' in low or 'security' in low:return {'category':'Security configuration','action':'Check the selected security policy, local certificate/key paths and server certificate trust.'}
    if 'identity' in low or 'useraccess' in low or 'accessdenied' in low:return {'category':'Authentication or access','action':'Check the username/password and the user’s read permissions.'}
    if 'nodeid' in low or 'browsename' in low or 'datatype' in low or 'scalar' in low:return {'category':'Tag definition','action':'Browse the server again and verify the NodeId, scalar datatype and expected browse name.'}
    if 'exception' in low or 'illegal' in low:return {'category':'Modbus response','action':'Check unit ID, data area, zero-based offset and supported register range.'}
    return {'category':'Connection or configuration','action':'Check the address, TCP port and unit ID. Confirm the device service is reachable and review the technical detail.'}

async def inspect(config,mode):
    p=default_project();p['connections']=[config];validate_project(p)
    if mode not in ('test','browse','preview'):raise ValueError('Unsupported setup operation')
    if mode=='preview' and not 1<=len(config.get('tags',[]))<=10:raise ValueError('Preview supports 1..10 tags')
    client=adapter(config)
    try:
        async with asyncio.timeout(60):
            await client.connect()
            if mode=='test':return {'ok':True,'message':('OPC UA session established. No values were written.' if config['protocol']=='opcua' else ('RTU serial port opened' if config.get('transport')=='rtu' else 'TCP connection established')+'. A register read is still needed to verify unit ID and addresses. No values were written.')}
            if mode=='browse':
                if config['protocol']!='opcua':raise ValueError('Modbus uses a register map rather than browsing')
                entries=await client.browse();tags=[];skipped=0;names={};seen=set()
                for start in range(0,min(len(entries),5000),50):
                    part=entries[start:start+50];nodes=[client.client.get_node(t['nodeid']) for t in part]
                    types=await client.client.read_attributes(nodes,ua.AttributeIds.DataType)
                    ranks=await client.client.read_attributes(nodes,ua.AttributeIds.ValueRank)
                    for entry,dt,rank in zip(part,types,ranks):
                        kind=next((k for k in TYPES if dt.StatusCode.is_good() and dt.Value.Value==ua.NodeId(getattr(ua.ObjectIds,k))),None)
                        if not kind or not rank.StatusCode.is_good() or rank.Value.Value!=-1:skipped+=1;continue
                        if entry['nodeid'] in seen:continue
                        seen.add(entry['nodeid'])
                        name=entry['tag'];names[name]=names.get(name,0)+1
                        label=name if names[name]==1 else name+' ['+entry['nodeid']+']'
                        tags.append({'id':uid(),'tag':label,'browse_name':name,'nodeid':entry['nodeid'],'dtype':kind,'index':len(tags)+1})
                return {'ok':True,'tags':tags,'skipped':skipped,'truncated':len(entries)>5000}
            tags=config['tags'];await client.prepare(tags)
            rows=[]
            if config['protocol']=='opcua':
                values=await client.client.read_attributes([client.nodes[t['id']] for t in tags],ua.AttributeIds.Value)
                for t,v in zip(tags,values):rows.append({'tag':t['tag'],'dtype':t['dtype'],'units':t.get('units',''),'value':view_value(v.Value.Value,t['dtype']) if v.StatusCode.is_good() and v.Value else None,'quality':v.StatusCode.name,'source_timestamp':v.SourceTimestamp.isoformat() if v.SourceTimestamp else None,'raw':None})
            else:
                values=await client.read(tags)
                for t in tags:rows.append({'tag':t['tag'],'dtype':t['dtype'],'units':t.get('units',''),'value':view_value(values[t['id']],t['dtype']),'quality':'Read succeeded (Modbus has no OPC UA quality)','source_timestamp':None,'raw':client.last_raw.get(t['id'])})
            return {'ok':True,'rows':rows}
    except Exception as error:return {'ok':False,**diagnose(error),'detail':str(error) or type(error).__name__}
    finally:await client.close()
