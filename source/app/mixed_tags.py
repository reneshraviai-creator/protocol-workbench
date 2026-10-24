"""Atomic mixed-row plans and reusable configurations; no device I/O."""
import asyncio
import copy
import hashlib
import json
import re
from decimal import Decimal
from model import dtype,uid,width,cast,view_value,validate_simulation,validate_project,default_project,TYPES
from tag_builder import integer
from typed_patterns import patterns


def fingerprint(c,spec):return hashlib.sha256(json.dumps([c,spec],sort_keys=True,separators=(',',':')).encode()).hexdigest()


def quantities(spec):
    rows=spec.get('rows',[])
    if not isinstance(rows,list) or not 1<=len(rows)<=100 or any(not isinstance(r,dict) for r in rows):raise ValueError('Mixed generation needs 1..100 datatype rows')
    mode=spec.get('mode','quantity')
    if mode=='quantity':return [integer(r,'quantity',1,0,20000) for r in rows]
    if mode!='percentage':raise ValueError('Mode must be quantity or percentage')
    total=integer(spec,'total',1000,1,20000);parts=[]
    for row in rows:
        try:v=Decimal(str(row.get('percentage',0)))
        except Exception:raise ValueError('Percentages must be finite numbers')
        if not v.is_finite() or v<0 or v>100:raise ValueError('Percentages must be 0..100')
        parts.append(v)
    if sum(parts)!=100:raise ValueError('Percentages must total exactly 100')
    exact=[total*v/100 for v in parts];counts=[int(v) for v in exact]
    for i in sorted(range(len(rows)),key=lambda i:(-(exact[i]-counts[i]),i))[:total-sum(counts)]:counts[i]+=1
    return counts


def checked_spec(spec):
    if not isinstance(spec,dict):raise ValueError('Mixed configuration must be an object')
    counts=quantities(spec)
    if not 1<=sum(counts)<=20000:raise ValueError('Mixed total must be 1..20000')
    if spec.get('conflicts','cancel') not in ('cancel','skip','rename'):raise ValueError('Invalid conflict policy')
    return counts


async def generate(connection,spec,progress=None):
    counts=checked_spec(spec);c=copy.deepcopy(connection);existing=c.get('tags',[])
    if spec.get('conflicts','cancel')!='skip' and len(existing)+sum(counts)>20000:raise ValueError('Requested tags plus existing tags exceed 20000')
    protocol=c.get('protocol')
    if protocol not in ('opcua','modbus'):raise ValueError('Unsupported protocol')
    names={t['tag'] for t in existing};nodes={t.get('nodeid') for t in existing if t.get('nodeid')};ids={t['id'] for t in existing};tags=[];rename_counts={};skipped=0;renamed=0;conflicts=[];summaries=[]
    next_index=max((int(t.get('index',i+1)) for i,t in enumerate(existing)),default=0)+1
    addresses={area:max((int(t['address'])+(1 if area in ('coil','discrete') else width(t['dtype'])) for t in existing if t.get('area','holding')==area),default=0) for area in ('holding','input','coil','discrete')} if protocol=='modbus' else {}
    seen=0
    for row_no,(row,count) in enumerate(zip(spec['rows'],counts)):
        kind=dtype(row.get('dtype','Float'))
        if kind not in TYPES:raise ValueError(f'Row {row_no+1}: unsupported datatype')
        if protocol=='modbus' and kind in ('String','DateTime'):raise ValueError(f'{kind} is supported only by OPC UA in this app')
        start=integer(row,'start',1,0,999999999);digits=integer(row,'digits',4,0,12)
        prefix=row.get('prefix',kind);separator=row.get('separator','_');suffix=row.get('suffix','');naming=row.get('naming','{prefix}{separator}{index}{suffix}')
        if any(not isinstance(v,str) or len(v)>240 or any(ord(ch)<32 for ch in v) for v in (prefix,separator,suffix,naming)):raise ValueError('Naming fields must be text without control characters, up to 240 characters')
        if re.sub(r'\{(prefix|separator|index|suffix)\}','',naming).find('{')>=0 or '}' in re.sub(r'\{(prefix|separator|index|suffix)\}','',naming):raise ValueError('Naming placeholders: {prefix}, {separator}, {index}, {suffix}')
        simulation=copy.deepcopy(row.get('simulation',{'pattern':'constant','value':row.get('default',False if kind=='Boolean' else '' if kind=='String' else '2026-01-01T00:00:00Z' if kind=='DateTime' else 0)}))
        if not isinstance(simulation,dict) or simulation.get('pattern','sine') not in patterns(kind):raise ValueError(f'Incompatible {kind} pattern')
        default=row.get('default',False if kind=='Boolean' else '' if kind=='String' else '2026-01-01T00:00:00Z' if kind=='DateTime' else 0)
        initial=view_value(cast(default,kind),kind);group=row.get('group',prefix)
        if not isinstance(group,str) or len(group)>80:raise ValueError('Group must be text up to 80 characters')
        prototype={'dtype':kind,'simulation':simulation,'initial_value':initial};validate_simulation(prototype,{})
        area=row.get('area','holding');order=row.get('order','ABCD')
        if protocol=='modbus':
            if area not in addresses:raise ValueError('Invalid Modbus area')
            if area in ('coil','discrete') and kind!='Boolean':raise ValueError('Coils/discrete inputs require Boolean')
            address=integer(row,'address',addresses[area],0,65535);size=1 if area in ('coil','discrete') else width(kind)
        else:
            ns=integer(row,'namespace',2,0,65535);node_prefix=row.get('node_prefix','')
            if not isinstance(node_prefix,str) or len(node_prefix)>240:raise ValueError('NodeId prefix must be text up to 240 characters')
        made=0
        for n in range(count):
            seen+=1;name=naming.replace('{prefix}',prefix).replace('{separator}',separator).replace('{index}',str(start+n).zfill(digits)).replace('{suffix}',suffix)
            if not name or len(name)>250:raise ValueError('Generated name must contain 1..250 characters')
            original=name;serial=rename_counts.get(original,1)
            def nodeid(name):
                from asyncua import ua
                return ua.NodeId(node_prefix+name,ns).to_string()
            while name in names or protocol=='opcua' and nodeid(name) in nodes:
                if len(conflicts)<100:conflicts.append(original)
                choice=spec.get('conflicts','cancel')
                if choice=='cancel':raise ValueError('Naming/NodeId conflict: '+original+'; select skip or rename, or change the naming')
                if choice=='skip':skipped+=1;break
                name=original+'_'+str(serial);serial+=1
            else:
                rename_counts[original]=serial
                if len(name)>250:raise ValueError('Renamed tag exceeds 250 characters')
                if len(existing)+len(tags)>=20000:raise ValueError('Generated unique tags exceed 20000 connection limit')
                if name!=original:renamed+=1
                ident=uid()
                while ident in ids:ident=uid()
                tag={'id':ident,'tag':name,'dtype':kind,'index':next_index+len(tags),'initial_value':initial,'simulation':copy.deepcopy(simulation),'group':group,'enabled':row.get('enabled',True)}
                if protocol=='opcua':tag['nodeid']=nodeid(name);nodes.add(tag['nodeid'])
                else:
                    if address+size>65536:raise ValueError('Generated Modbus addresses exceed 65535')
                    tag.update(address=address,area=area,order=order);address+=size
                names.add(name);ids.add(ident);tags.append(tag);made+=1
            if seen%200==0:
                if progress:progress(seen,sum(counts))
                await asyncio.sleep(0)
        if protocol=='modbus':addresses[area]=max(addresses[area],address)
        summaries.append({'row':row_no+1,'dtype':kind,'requested':count,'created':made,'group':group,'default':initial,'simulation':simulation,'naming':naming})
    c['tags']=existing+tags;p=default_project();p['connections']=[c];await asyncio.to_thread(validate_project,p)
    if progress:progress(seen,sum(counts))
    return {'tags':tags,'requested':sum(counts),'created':len(tags),'skipped':skipped,'renamed':renamed,'conflicts':conflicts,'rows':summaries}
