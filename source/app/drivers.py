"""OPC UA and Modbus TCP adapters. Every writer checks types/access before use."""
import asyncio
from asyncua import Client, ua
from pymodbus.client import AsyncModbusTcpClient,AsyncModbusSerialClient
from serial_config import is_rtu,options,lock
from model import cast,encode,decode,width
from transactions import STORE_CONTEXT,CURRENT_TRANSACTION,tracked
from communication_health import observed
from performance import batch_settings

MODBUS_EXCEPTIONS={1:'Illegal function',2:'Illegal data address',3:'Illegal data value',4:'Server device failure',5:'Acknowledge',6:'Server busy',10:'Gateway path unavailable',11:'Gateway target failed to respond'}

def opc_status(status):return f'{status.name} (0x{status.value:08X})'

class OPC:
    def __init__(self,config):
        from credential_vault import config as secure_config
        config=secure_config(config)
        self.config=config;self.evidence=STORE_CONTEXT.get();self.client=Client(config['url'],timeout=float(config.get('timeout',10)));self.nodes={};self.types={}
        self.read_batch=batch_settings(config)['opcua_batch_size'];self.write_batch=self.read_batch;self.oldest_source_timestamp=None
    @observed('connect')
    async def connect(self):
        c=self.config
        if c.get('username'): self.client.set_user(c['username']);self.client.set_password(c.get('password',''))
        if c.get('security','None')!='None':
            if not c.get('certificate') or not c.get('private_key') or not c.get('server_certificate'):
                raise ValueError('Secure OPC UA requires client certificate, private key, and trusted server certificate file paths')
            from credential_vault import CONTEXT as VAULT_CONTEXT
            if VAULT_CONTEXT.get():
                from certificate_access import validator,load,client_identity
                from pathlib import Path
                from cryptography.hazmat.primitives import serialization
                from asyncua.crypto import security_policies
                self.client.certificate_validator=validator(c)
                self.client.application_uri=client_identity(c)
                raw=Path(c['private_key']).read_bytes()
                key=serialization.load_pem_private_key(raw,password=None) if b'-----BEGIN' in raw else serialization.load_der_private_key(raw,password=None)
                policy,mode=c['security'].split(',')
                await self.client.set_security(getattr(security_policies,'SecurityPolicy'+policy),load(Path(c['certificate']).read_bytes()).public_bytes(serialization.Encoding.DER),key.private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()),server_certificate=load(Path(c['server_certificate']).read_bytes()).public_bytes(serialization.Encoding.DER),mode=getattr(ua.MessageSecurityMode,mode))
            else:
                await self.client.set_security_string(f"{c['security']},{c['certificate']},{c['private_key']},{c['server_certificate']}")
        await self.client.connect()
        for attr in ('read','write'):
            try:
                ident=getattr(ua.ObjectIds,f'Server_ServerCapabilities_OperationLimits_MaxNodesPer{attr.title()}')
                limit=await self.client.get_node(ident).read_value()
                if isinstance(limit,int) and limit>0: setattr(self,attr+'_batch',min(batch_settings(self.config)['opcua_batch_size'],limit))
            except ua.UaStatusCodeError: pass
    @observed('close')
    async def close(self):
        try: await self.client.disconnect()
        except Exception: pass
    @observed('health')
    async def healthcheck(self):return await self.client.nodes.server_state.read_value()
    async def browse(self):
        queue=[(self.client.nodes.objects,0)];seen=set();result=[]
        while queue:
            node,depth=queue.pop(0)
            key=node.nodeid.to_string()
            if key in seen: continue
            seen.add(key)
            if len(seen)>5000: raise ValueError('Browse exceeded 5000 containers; configure explicit NodeIds in CSV')
            refs=await node.get_children_descriptions()
            for ref in refs:
                name=ref.BrowseName.Name or '';nid=ref.NodeId.to_string()
                if ref.NodeClass==ua.NodeClass.Variable:
                    result.append({'tag':name,'nodeid':nid})
                    if len(result)>25000: raise ValueError('Browse exceeded 25000 variables; use explicit NodeIds')
                if depth<12 and ref.NodeId.NamespaceIndex!=0 and (ref.NodeClass==ua.NodeClass.Object or name.startswith('OPC_UA_Server_DB') or name=='SIM_DB'):
                    queue.append((self.client.get_node(ref.NodeId),depth+1))
        return result
    async def prepare(self,tags,write=False):
        unresolved=[t for t in tags if not t.get('nodeid')]
        if unresolved:
            entries=await self.browse();lookup={}
            for e in entries:
                for name in {e['tag'],e['tag'].rsplit('.',1)[-1].strip('"')}:
                    lookup.setdefault(name,set()).add(e['nodeid'])
            for t in unresolved:
                matches=lookup.get(t['tag'],set())
                if len(matches)!=1: raise ValueError(f"{t['tag']}: expected one matching NodeId; found {len(matches)}. Browse and map explicitly.")
                self.nodes[t['id']]=self.client.get_node(next(iter(matches)))
        for t in tags:
            if t.get('nodeid'):self.nodes[t['id']]=self.client.get_node(t['nodeid'])
        for start in range(0,len(tags),self.read_batch):
            part=tags[start:start+self.read_batch];nodes=[self.nodes[t['id']] for t in part]
            names=await self.client.read_attributes(nodes,ua.AttributeIds.BrowseName)
            types=await self.client.read_attributes(nodes,ua.AttributeIds.DataType)
            ranks=await self.client.read_attributes(nodes,ua.AttributeIds.ValueRank)
            rights=await self.client.read_attributes(nodes,ua.AttributeIds.UserAccessLevel)
            for t,name,dt,rank,access in zip(part,names,types,ranks,rights):
                for item in (name,dt,rank,access):item.StatusCode.check()
                actual_name=name.Value.Value.Name
                expected=t.get('browse_name') or t['tag']
                if expected not in (actual_name,actual_name.rsplit('.',1)[-1].strip('"')):raise ValueError(f"{t['tag']}: NodeId now refers to {actual_name}. Resolve the mapping before writing.")
                expected=ua.NodeId(getattr(ua.ObjectIds,t['dtype']))
                if dt.Value.Value!=expected: raise ValueError(f"{t['tag']}: configured {t['dtype']}, server DataType {dt.Value.Value}. Browse/resolve tags to update the datatype.")
                if rank.Value.Value!=-1: raise ValueError(f"{t['tag']}: only scalar variables are supported")
                if not access.Value.Value & (2 if write else 1): raise ValueError(f"{t['tag']}: current user cannot {'write' if write else 'read'} this value")
                self.types[t['id']]=getattr(ua.VariantType,t['dtype'])
    @observed('read')
    @tracked('read')
    async def read(self,tags,*,allow_bad_quality=False):
        values={}
        self.last_observations={};self.last_read_transaction=CURRENT_TRANSACTION.get().row['id']
        timestamps=[]
        for start in range(0,len(tags),self.read_batch):
            part=tags[start:start+self.read_batch]
            tx=CURRENT_TRANSACTION.get();batch=tx.batch(part)
            try:data=await self.client.read_attributes([self.nodes[t['id']] for t in part],ua.AttributeIds.Value)
            except ua.UaStatusCodeError as error:
                opc_service_error(tx,part,error);raise
            tx.response(batch,len(data));errors=[]
            batch['response_statuses']=[{'status':opc_status(dv.StatusCode),'good':dv.StatusCode.is_good()} for dv in data]
            for t,dv in zip(part,data):
                self.last_observations[t['id']]={'value':dv.Value.Value,'good':dv.StatusCode.is_good(),'quality':dv.StatusCode.name,'status_code':dv.StatusCode.value,'source_timestamp':dv.SourceTimestamp.timestamp() if dv.SourceTimestamp else None,'datatype':dv.Value.VariantType.name}
                good=dv.StatusCode.is_good();tx.point(t,'SUCCEEDED' if good else 'FAILED',opc_status(dv.StatusCode))
                if not good:errors.append(f"Source quality is bad: {t['tag']} {dv.StatusCode}. No destination write for this cycle.")
                if good or allow_bad_quality:
                    values[t['id']]=dv.Value.Value
                    timestamps.append(dv.SourceTimestamp.timestamp() if dv.SourceTimestamp else None)
            if len(data)!=len(part):
                batch['error']='Response count mismatch';raise ValueError('Unexpected OPC UA read response length')
            if errors and not allow_bad_quality:raise ValueError('; '.join(errors[:3]))
        self.oldest_source_timestamp=min(timestamps) if timestamps and all(t is not None for t in timestamps) else None
        return values
    @observed('write')
    @tracked('write')
    async def write(self,tags,values,*,metadata=None):
        from write_access import check_write
        check_write(self.config,tags)
        count=0
        for start in range(0,len(tags),self.write_batch):
            check_write(self.config,tags)
            part=tags[start:start+self.write_batch]
            dvs=[ua.DataValue(ua.Variant(cast(values[t['id']],t['dtype']),self.types[t['id']]),StatusCode=None) for t in part]
            if metadata:
                from datetime import datetime,timezone
                for tag,dv in zip(part,dvs):
                    meta=metadata.get(tag['id'],{})
                    if 'source_timestamp' in meta:dv.SourceTimestamp=datetime.fromtimestamp(meta['source_timestamp'],timezone.utc)
                    if 'status_code' in meta:dv.StatusCode=ua.StatusCode(meta['status_code'])
            tx=CURRENT_TRANSACTION.get();batch=tx.batch(part)
            try:statuses=await self.client.write_values([self.nodes[t['id']] for t in part],dvs,raise_on_partial_error=False)
            except ua.UaStatusCodeError as error:
                opc_service_error(tx,part,error);raise
            tx.response(batch,len(statuses));errors=[]
            batch['response_statuses']=[{'status':opc_status(status),'good':status.is_good()} for status in statuses]
            for t,status in zip(part,statuses):
                good=status.is_good();tx.point(t,'SUCCEEDED' if good else 'FAILED',opc_status(status))
                if good:count+=1
                else:errors.append(f"{t['tag']} {opc_status(status)}")
            if len(statuses)!=len(part):
                batch['error']='Response count mismatch';raise ValueError('Unexpected OPC UA write response length')
            if errors:raise ValueError(f"Partial write: {'; '.join(errors[:3])}; {count} writes acknowledged; inspect Communication Verification for every point")
        return count

def opc_service_error(tx,tags,error):
    # Service rejection is evidence; timeout/transport faults do not prove a write failed.
    uncertain=type(error).__name__ in ('BadTimeout','BadRequestTimeout','BadSessionClosed','BadSessionIdInvalid','BadSecureChannelClosed','BadSecureChannelIdInvalid','BadConnectionClosed','BadCommunicationError','BadServerNotConnected','BadNotConnected','BadShutdown')
    if not uncertain:
        batch=tx.row['batches'][-1];tx.response(batch,0);batch['error']=type(error).__name__
        for tag in tags:tx.point(tag,'FAILED',type(error).__name__)


def modbus_response(tx,batch,part,response,operation):
    # A local no-response object is not a received Modbus exception response.
    code=getattr(response,'exception_code',None)
    if response.isError():
        if isinstance(code,int) and code>0:
            tx.response(batch,1);batch['error']=f'Modbus exception {code}: {MODBUS_EXCEPTIONS.get(code,"Device exception")}'
            # Exception 05 acknowledges deferred processing; 0B reports no downstream response.
            uncertain=code in (5,11)
            for tag in part:tx.point(tag,'UNKNOWN' if uncertain else 'FAILED',batch['error'])
        else:
            batch['error']='No received response: '+type(response).__name__
            batch['timed_out']=any(word in str(response).casefold() for word in ('no response received','timeout','timed out'))
        modbus_error(response,operation)
    tx.response(batch,1)


def modbus_error(response,operation):
    if response.isError():
        message=f'Modbus {operation} failed: {response}'+('; earlier write batches may have succeeded' if operation=='write' else '')
        if getattr(response,'exception_code',None) in (1,2,3):raise ValueError(message+'; check function, address, and value configuration')
        raise ConnectionError(message)

class Modbus:
    def __init__(self,config,trace=None):
        self.config=config;self.evidence=STORE_CONTEXT.get();self.serial_lock=None
        trace_options={'trace_packet':lambda sending,data:trace('bytes',sending,data),'trace_pdu':lambda sending,data:trace('pdu',sending,data)} if trace else {}
        common={'timeout':float(config.get('timeout',10)),'retries':0,**trace_options}
        self.client=AsyncModbusSerialClient(config['serial_port'],**options(config),**common) if is_rtu(config) else AsyncModbusTcpClient(config['host'],port=int(config.get('port',502)),**common)
        self.device=int(config.get('device_id',1));self.last_raw={};self.batch_units=batch_settings(config)['modbus_batch_units']
    @observed('connect')
    async def connect(self):
        if is_rtu(self.config):self.serial_lock=lock(self.config)
        try:
            if not await self.client.connect():raise ConnectionError('Modbus '+('RTU serial' if is_rtu(self.config) else 'TCP')+' connection failed')
        except BaseException:
            await self.close();raise
    @observed('close')
    async def close(self):
        self.client.close()
        if self.serial_lock:self.serial_lock.close();self.serial_lock=None
    async def prepare(self,tags,write=False):
        if write and any(t.get('area','holding') in ('input','discrete') for t in tags):raise ValueError('Cannot write input registers/discrete inputs')
    def groups(self,tags):
        groups=[]
        for tag in sorted(tags,key=lambda t:(t.get('area','holding'),int(t['address']))):
            area=tag.get('area','holding');size=1 if area in ('coil','discrete') else width(tag['dtype']);start=int(tag['address'])
            if groups and groups[-1][0]==area and groups[-1][1]+groups[-1][2]==start and groups[-1][2]+size<=self.batch_units:
                groups[-1][2]+=size;groups[-1][3].append(tag)
            else:groups.append([area,start,size,[tag]])
        return groups
    @observed('read')
    @tracked('read')
    async def read(self,tags):
        result={}
        self.last_observations={};self.last_read_transaction=CURRENT_TRANSACTION.get().row['id']
        funcs={'holding':self.client.read_holding_registers,'input':self.client.read_input_registers,'coil':self.client.read_coils,'discrete':self.client.read_discrete_inputs}
        for area,start,size,part in self.groups(tags):
            tx=CURRENT_TRANSACTION.get();batch=tx.batch(part)
            response=await funcs[area](start,count=size,device_id=self.device)
            modbus_response(tx,batch,part,response,'read')
            data=response.bits if area in ('coil','discrete') else response.registers
            if (area in ('coil','discrete') and len(data)<size) or (area not in ('coil','discrete') and len(data)!=size):
                batch['error']='Response count mismatch';raise ValueError('Unexpected Modbus read response length')
            for t in part:
                offset=int(t['address'])-start
                self.last_raw[t['id']]=data[offset:offset+(1 if area in ('coil','discrete') else width(t['dtype']))]
                result[t['id']]=bool(data[offset]) if area in ('coil','discrete') else decode(data[offset:offset+width(t['dtype'])],t['dtype'],t.get('order','ABCD'))
                self.last_observations[t['id']]={'value':result[t['id']],'good':True,'quality':'TransportGood; process quality unavailable','source_timestamp':None,'datatype':t['dtype']}
                tx.point(t,'SUCCEEDED','Good')
        return result
    @observed('write')
    @tracked('write')
    async def write(self,tags,values):
        from write_access import check_write
        check_write(self.config,tags)
        count=0
        for area,start,size,part in self.groups(tags):
            check_write(self.config,tags)
            if area not in ('holding','coil'):raise ValueError('Read-only Modbus area')
            raw=[]
            for t in part:raw.extend([bool(cast(values[t['id']],'Boolean'))] if area=='coil' else encode(values[t['id']],t['dtype'],t.get('order','ABCD')))
            tx=CURRENT_TRANSACTION.get();batch=tx.batch(part)
            response=await (self.client.write_coils(start,raw,device_id=self.device) if area=='coil' else self.client.write_registers(start,raw,device_id=self.device))
            modbus_response(tx,batch,part,response,'write')
            if getattr(response,'address',None)!=start or getattr(response,'count',None)!=size:
                batch['error']='Write acknowledgement address/count mismatch';raise ValueError('Unexpected Modbus write acknowledgement')
            for t in part:tx.point(t,'SUCCEEDED','Acknowledged group write; no per-point status in Modbus')
            count+=len(part)
        return count

def adapter(config):return OPC(config) if config['protocol']=='opcua' else Modbus(config)
