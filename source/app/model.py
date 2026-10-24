"""Configuration, exact DB imports, and protocol-neutral value conversion."""
import csv
import io
import json
import math
import re
import struct
import uuid

TYPES = {'Boolean','SByte','Byte','String','DateTime','Int16','UInt16','Int32','UInt32','Int64','UInt64','Float','Double'}
FORMATS = {'SByte':'b','Byte':'B','Boolean':'?', 'Int16':'h','UInt16':'H','Int32':'i','UInt32':'I','Int64':'q','UInt64':'Q','Float':'f','Double':'d'}
ALIASES = {'SINT':'SByte','INT8':'SByte','USINT':'Byte','UINT8':'Byte','BYTE':'Byte','STRING':'String','DATETIME':'DateTime','FLOAT32':'Float','FLOAT64':'Double','REAL':'Float','LREAL':'Double','BOOL':'Boolean','INT':'Int16','UINT':'UInt16','WORD':'UInt16','DINT':'Int32','DWORD':'UInt32','UDINT':'UInt32','LINT':'Int64','ULINT':'UInt64'}
AREAS = {'holding','input','coil','discrete'}

def uid():
    return uuid.uuid4().hex[:12]

def dtype(value):
    return ALIASES.get(str(value).upper(), str(value))

def width(kind):
    if kind not in FORMATS:raise ValueError(f'{kind} has no supported Modbus register encoding')
    return max(1, struct.calcsize(FORMATS[kind]) // 2)

def cast(value, kind):
    if kind not in TYPES:
        raise ValueError(f'Unsupported datatype {kind}')
    if kind=='String':
        if not isinstance(value,str) or len(value)>4096:raise ValueError('String must be text of at most 4096 characters')
        return value
    if kind=='DateTime':
        from datetime import datetime,timezone
        try:v=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
        except (ValueError,TypeError):raise ValueError('DateTime needs an ISO 8601 timestamp with timezone')
        if not isinstance(v,datetime) or v.tzinfo is None:raise ValueError('DateTime needs a timezone-aware timestamp')
        v=v.astimezone(timezone.utc)
        if v.year<1601:raise ValueError('OPC UA DateTime must be 1601 or later')
        return v
    if kind == 'Boolean':
        if value in (True,False,0,1,'0','1','true','false','True','False'):
            return str(value).lower() in ('1','true')
        raise ValueError(f'{value!r} cannot be converted to Boolean; use 0 or 1')
    from decimal import Decimal, InvalidOperation
    try:
        number = float(value) if kind in ('Float','Double') else int(Decimal(str(int(value) if isinstance(value,bool) else value)))
    except (InvalidOperation,OverflowError,ValueError) as error:
        raise ValueError(f'{value!r} is not a finite numeric value') from error
    if not math.isfinite(number):
        raise ValueError('NaN and infinity are not supported')
    try:
        return struct.unpack('>' + FORMATS[kind],struct.pack('>' + FORMATS[kind],number))[0]
    except (OverflowError,struct.error) as error:
        raise ValueError(f'{value} is outside {kind} range') from error

def reorder(data, order):
    if order not in ('ABCD','BADC','CDAB','DCBA'):
        raise ValueError('Order must be ABCD, BADC, CDAB, or DCBA')
    words=[data[i:i+2] for i in range(0,len(data),2)]
    if order in ('CDAB','DCBA'): words.reverse()
    if order in ('BADC','DCBA'): words=[w[::-1] for w in words]
    return b''.join(words)

def encode(value, kind, order='ABCD'):
    if kind=='Boolean': return [int(cast(value,kind))]
    if kind in ('SByte','Byte'):return encode(cast(value,kind),'Int16' if kind=='SByte' else 'UInt16',order)
    raw=reorder(struct.pack('>'+FORMATS[kind],cast(value,kind)),order)
    return list(struct.unpack('>'+'H'*(len(raw)//2),raw))

def decode(registers, kind, order='ABCD'):
    if kind=='Boolean': return bool(registers[0])
    if kind in ('SByte','Byte'):return cast(decode(registers,'Int16' if kind=='SByte' else 'UInt16',order),kind)
    raw=struct.pack('>'+'H'*len(registers),*registers)
    return struct.unpack('>'+FORMATS[kind],reorder(raw,order))[0]

def transform(value, mapping, kind):
    from decimal import Decimal, InvalidOperation
    try:
        value=Decimal(str(int(value) if isinstance(value,bool) else value))*Decimal(str(mapping.get('scale',1)))+Decimal(str(mapping.get('offset',0)))
        if mapping.get('minimum') is not None: value=max(value,Decimal(str(mapping['minimum'])))
        if mapping.get('maximum') is not None: value=min(value,Decimal(str(mapping['maximum'])))
        return cast(value,kind)
    except InvalidOperation as error:
        raise ValueError('Invalid numeric source or conversion setting') from error

def generated(tag, settings, slot):
    settings={**settings, **tag.get('simulation',{})}
    if tag['dtype'] in ('String','DateTime') or settings.get('pattern') in ('toggle','increment','decrement','step'):
        from typed_patterns import value as typed_value
        typed=typed_value(tag,settings,slot)
        if typed is not NotImplemented:return typed
    low=float(settings.get('minimum',0)); high=float(settings.get('maximum',1000))
    index=int(tag.get('index',1))
    period=int(settings.get('period',20))
    phase=2*math.pi*(slot%period)/period+index*0.013
    pattern=settings.get('pattern','sine')
    if pattern=='constant': value=settings.get('value',0)
    elif pattern=='sine': value=low+(high-low)*(1+math.sin(phase))/2
    elif pattern=='ramp': value=low+(high-low)*(slot%period)/(period-1)
    elif pattern=='random':
        # Deterministic per-slot so independently connected clients can match.
        import random
        value=random.Random(f"{settings.get('seed',1)}:{slot}:{index}").uniform(low,high)
    elif pattern=='csv':
        sequence=settings.get('sequence',[])
        if not sequence: raise ValueError('CSV playback needs a nonempty value sequence')
        value=sequence[slot%len(sequence)]
    else: raise ValueError('Unknown simulation pattern')
    if tag['dtype']=='Boolean' and pattern in ('sine','ramp','random'): value=value >= (low+high)/2
    return cast(value,tag['dtype'])

def initial(tag, settings):
    return cast(tag['initial_value'], tag['dtype']) if 'initial_value' in tag else generated(tag, settings, 0)


def validate_simulation(tag, settings):
    effective={**settings, **tag.get('simulation',{})}
    from typed_patterns import validate as typed_validate
    if typed_validate(tag,effective):
        if 'initial_value' in tag:cast(tag['initial_value'],tag['dtype'])
        return
    if effective.get('pattern','sine') not in ('sine','ramp','constant','random','csv'): raise ValueError('Invalid simulation pattern')
    period=effective.get('period',20)
    if not math.isfinite(float(period)) or float(period)!=int(float(period)) or not 2<=int(float(period))<=1000000: raise ValueError('Period must be a whole number from 2 to 1000000 cycles')
    for key,default in (('minimum',0),('maximum',1000)):
        if not math.isfinite(float(effective.get(key,default))): raise ValueError('Simulation values must be finite')
    low=effective.get('minimum',0);high=effective.get('maximum',1000)
    if float(low)>float(high): raise ValueError('Minimum exceeds maximum')
    pattern=effective.get('pattern','sine')
    if pattern=='csv':
        sequence=effective.get('sequence')
        if not isinstance(sequence,list) or not 1<=len(sequence)<=100000: raise ValueError('CSV playback supports 1..100000 samples')
        for value in sequence:cast(value,tag['dtype'])
    elif pattern=='constant':cast(effective.get('value',0),tag['dtype'])
    elif tag['dtype']!='Boolean':
        cast(low,tag['dtype']);cast(high,tag['dtype'])
    if 'initial_value' in tag:cast(tag['initial_value'],tag['dtype'])
    generated(tag,settings,0)


def view_value(value, kind):
    if kind=='DateTime' and value is not None:return cast(value,kind).isoformat()
    if kind in ('Int64','UInt64'): return str(value)
    if isinstance(value,float) and not math.isfinite(value): return 'NaN' if math.isnan(value) else ('Infinity' if value>0 else '-Infinity')
    return value

def import_tags(text, filename):
    result=[]
    if filename.lower().endswith('.db'):
        match=re.search(r'DATA_BLOCK\s+"([^"]+)"',text)
        if not match: raise ValueError('No Siemens DATA_BLOCK declaration found')
        # Flat scalar DBs only; reject structured/array declarations instead of silently dropping them.
        if re.search(r'\b(STRUCT|ARRAY)\b',text,re.I):
            raise ValueError('Only flat scalar Siemens DB declarations are supported; use CSV for arrays/structures')
        var=re.search(r'\bVAR\b(.*?)\bEND_VAR\b',text,re.S|re.I)
        if not var: raise ValueError('DB contains no VAR section')
        lines=re.findall(r'^\s*(?:"([^"]+)"|(\w+))\s*:\s*(\w+)\s*(?::=[^;]+)?;',var.group(1),re.M)
        result=[{'tag':quoted or name,'dtype':dtype(kind),'db':match.group(1)} for quoted,name,kind in lines]
    else:
        rows=list(csv.DictReader(io.StringIO(text.lstrip('\ufeff'))))
        for row in rows:
            name=row.get('tag') or row.get('name')
            if not name: raise ValueError('CSV requires a tag column')
            item={'tag':name,'dtype':dtype(row.get('dtype') or row.get('datatype') or 'Float'), 'nodeid':row.get('nodeid',''), 'browse_name':row.get('browse_name',''), 'area':row.get('area') or 'holding','order':row.get('order') or 'ABCD'}
            if row.get('address','')!='': item['address']=int(row['address'])
            result.append(item)
    if not result: raise ValueError('No tags found')
    names=set()
    for i,tag in enumerate(result,1):
        if tag['tag'] in names: raise ValueError(f"Duplicate tag {tag['tag']}")
        if tag['dtype'] not in TYPES: raise ValueError(f"Unsupported datatype {tag['dtype']} on {tag['tag']}")
        names.add(tag['tag']);tag.update(id=uid(),index=i)
    return result

def default_project():
    return {'version':1,'name':'My protocol project','connections':[],'jobs':[],'settings':{'autostart':False}}

def validate_project(p):
    if not isinstance(p,dict) or p.get('version')!=1: raise ValueError('Unsupported project schema; version must be 1')
    if not isinstance(p.get('name'),str) or not p['name'].strip(): raise ValueError('Project needs a name')
    from performance import settings as performance_settings
    project_settings=p.get('settings',{})
    if not isinstance(project_settings,dict):raise ValueError('Project settings must be an object')
    performance_settings(project_settings.get('performance'))
    connections=p.get('connections',[]);jobs=p.get('jobs',[])
    if not isinstance(connections,list) or not isinstance(jobs,list): raise ValueError('Connections and jobs must be lists')
    if len(connections)>100 or len(jobs)>100: raise ValueError('Maximum 100 connections and jobs')
    for item in connections+jobs:
        if not isinstance(item.get('id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',item['id']): raise ValueError('IDs must contain 1..64 letters, digits, underscores or hyphens')
    ids=[c['id'] for c in connections]
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate connection ID')
    lookup={c['id']:c for c in connections}
    for c in connections:
        if c.get('protocol') not in ('opcua','modbus'): raise ValueError('Unsupported protocol')
        if c['protocol']=='opcua':
            from urllib.parse import urlparse
            u=urlparse(c.get('url',''))
            if u.scheme!='opc.tcp' or not u.hostname: raise ValueError('OPC UA URL must start opc.tcp:// and include a host')
            if c.get('security','None') not in ('None','Basic256Sha256,Sign','Basic256Sha256,SignAndEncrypt'):
                raise ValueError('Unsupported OPC UA security configuration')
        else:
            if c.get('transport','tcp') not in ('tcp','rtu'):raise ValueError('Modbus transport must be tcp or rtu')
            if c.get('transport','tcp')=='rtu':
                from serial_config import validate
                validate(c)
            else:
                if not c.get('host'): raise ValueError('Modbus host is required')
                if not 1<=int(c.get('port',502))<=65535: raise ValueError('Invalid TCP port')
            if not 1<=int(c.get('device_id',1))<=247: raise ValueError('Device ID must be 1..247')
        if not 0.2<=float(c.get('timeout',10))<=120: raise ValueError('Timeout must be 0.2..120 seconds')
        if not 0.5<=float(c.get('retry_seconds',5))<=300: raise ValueError('Retry delay must be 0.5..300 seconds')
        from communication_health import policy as recovery_policy
        recovery_policy(c)
        from performance import batch_settings
        batch_settings(c)
        tags=c.get('tags',[])
        if len(tags)>20000: raise ValueError('Maximum 20000 tags per connection')
        if len({t['id'] for t in tags})!=len(tags) or len({t['tag'] for t in tags})!=len(tags): raise ValueError('Tag IDs and names must be unique per connection')
        register_spans=set()
        for t in tags:
            if not isinstance(t.get('id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',t['id']): raise ValueError('Invalid tag ID')
            if not isinstance(t.get('tag'),str) or not t['tag']: raise ValueError('Tag name is required')
            if t.get('dtype') not in TYPES: raise ValueError('Unsupported datatype')
            if type(t.get('enabled',True)) is not bool:raise ValueError('Tag enabled must be Boolean')
            if not isinstance(t.get('group',''),str) or len(t.get('group',''))>80:raise ValueError('Tag group must be text up to 80 characters')
            if not isinstance(t.get('units',''),str) or len(t.get('units',''))>80: raise ValueError('Units must be text up to 80 characters')
            if not isinstance(t.get('simulation',{}),dict): raise ValueError('Tag simulation settings must be an object')
            if t.get('simulation'):validate_simulation(t,{})
            if 'initial_value' in t:cast(t['initial_value'],t['dtype'])
            if c['protocol']=='opcua' and t.get('nodeid'):
                from asyncua import ua
                try: ua.NodeId.from_string(t['nodeid'])
                except Exception as error: raise ValueError(f"Invalid NodeId on {t['tag']}") from error
            if c['protocol']=='modbus':
                if t.get('area','holding') not in AREAS: raise ValueError('Invalid Modbus area')
                a=int(t.get('address',-1)); size=1 if t.get('area') in ('coil','discrete') else width(t['dtype'])
                span={(t.get('area','holding'),i) for i in range(a,a+size)}
                if register_spans & span: raise ValueError(f"Overlapping Modbus register/coil span on {t['tag']}")
                register_spans.update(span)
                if not 0<=a<=65536-size: raise ValueError(f"Invalid zero-based address on {t['tag']}")
                if t.get('area') in ('coil','discrete') and t['dtype']!='Boolean': raise ValueError('Coils/discrete inputs require Boolean')
                if t.get('order','ABCD') not in ('ABCD','BADC','CDAB','DCBA'): raise ValueError('Invalid byte/word order')
    if len({j['id'] for j in jobs})!=len(jobs): raise ValueError('Duplicate job ID')
    write_targets=set()
    listeners=set()
    for j in jobs:
        if j.get('kind') not in ('simulation','scan','gateway','server'): raise ValueError('Invalid job kind')
        if j.get('connection') not in lookup: raise ValueError('Job destination connection not found')
        if not 0.1<=float(j.get('interval',2))<=3600: raise ValueError('Interval must be 0.1..3600 seconds')
        from simulation_control import validate_schedule
        validate_schedule(j)
        c=lookup[j['connection']]; available={t['id']:t for t in c.get('tags',[])}
        selected=j.get('tags',[]) or list(available)
        if len(selected)!=len(set(selected)) or any(t not in available for t in selected): raise ValueError('Invalid/duplicate job tags')
        if j['kind'] in ('simulation','server'):
            settings=j.get('simulation',{})
            for t in (available[i] for i in selected):validate_simulation(t,settings)
        if j['kind']=='gateway':
            if j.get('source') not in lookup: raise ValueError('Gateway source not found')
            from serial_config import is_rtu,port_key
            source_config=lookup[j['source']]
            if is_rtu(c) and is_rtu(source_config) and port_key(c['serial_port'])==port_key(source_config['serial_port']):raise ValueError('RTU gateway source and destination need separate serial ports')
            source_tags={t['id'] for t in lookup[j['source']].get('tags',[])}
            mappings=j.get('mappings',[])
            if not mappings: raise ValueError('Gateway needs at least one mapping')
            if len({m['destination'] for m in mappings})!=len(mappings): raise ValueError('Duplicate gateway destination')
            for m in mappings:
                if m['source'] not in source_tags or m['destination'] not in available: raise ValueError('Mapping references an unknown tag')
                if m.get('minimum') is not None and m.get('maximum') is not None and float(m['minimum'])>float(m['maximum']): raise ValueError('Mapping minimum exceeds maximum')
                for k in ('scale','offset','minimum','maximum'):
                    if m.get(k) is not None and not math.isfinite(float(m[k])): raise ValueError('Conversion must be finite')
            from bridge import options as bridge_options,validate_mapping
            bridge_options(j,source_config,c)
            source_lookup={t['id']:t for t in source_config['tags']}
            for m in mappings:validate_mapping(m,source_lookup[m['source']],available[m['destination']])
            selected=[m['destination'] for m in mappings]
        if j.get('max_age_seconds') is not None and not 0<float(j['max_age_seconds'])<=86400: raise ValueError('Maximum source age must be > 0 and <= 86400 seconds')
        if j['kind']=='server':
            endpoint=c.get('url') if c['protocol']=='opcua' else ("serial:"+c["serial_port"] if c.get("transport")=="rtu" else f"{c['host']}:{c['port']}")
            if endpoint in listeners: raise ValueError('Two server jobs cannot bind the same endpoint')
            listeners.add(endpoint)
            if c['protocol']=='opcua' and c.get('security','None')!='None': raise ValueError('OPC UA simulator server supports Security=None only')
        if j['kind'] in ('simulation','gateway','server') and not (j['kind']=='server' and not j.get('dynamic',True)):
            for ident in selected:
                t=available[ident]
                if c['protocol']=='modbus' and j['kind']!='server' and t.get('area') in ('input','discrete'): raise ValueError('Input registers and discrete inputs are read-only')
                if c['protocol']=='opcua':
                    keys={(c['url'],t.get('nodeid') or t['tag'])}
                else:
                    size=1 if t.get('area') in ('coil','discrete') else width(t['dtype'])
                    keys={(c.get('serial_port') if c.get('transport')=='rtu' else c['host'],0 if c.get('transport')=='rtu' else int(c.get('port',502)),int(c.get('device_id',1)),t.get('area','holding'),i) for i in range(int(t['address']),int(t['address'])+size)}
                if keys & write_targets: raise ValueError('Two jobs target the same tag; use nonoverlapping destinations')
                write_targets.update(keys)
        if j.get('verification') is not None:
            from verification import settings as verification_settings
            verification_settings(j,[available[i] for i in selected],c['protocol'])
    return p
