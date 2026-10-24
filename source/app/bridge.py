"""Deterministic scalar bridge transforms and bounded delivery evidence."""
import ast
import math
import time
import uuid
from collections import Counter,deque
from contextvars import ContextVar
from decimal import Decimal,InvalidOperation,localcontext
from functools import lru_cache
from model import cast,view_value,transform

UNITS=('none','C_to_F','F_to_C','bar_to_kPa','kPa_to_bar','Pa_to_bar','ms_to_s','s_to_ms','rpm_to_Hz','Hz_to_rpm','percent_to_fraction','fraction_to_percent')
BOOL_MODES=('strict','nonzero','threshold','equals','invert')


def number(v):
    if isinstance(v,bool):return Decimal(int(v))
    try:n=Decimal(str(v))
    except (InvalidOperation,ValueError):raise ValueError('Expected a finite numeric value')
    if not n.is_finite():raise ValueError('Expected a finite numeric value')
    return n


@lru_cache(maxsize=256)
def expression(text):
    if not isinstance(text,str) or not 1<=len(text)<=400:raise ValueError('Expression must be 1..400 characters')
    try:tree=ast.parse(text,mode='eval')
    except (SyntaxError,RecursionError):raise ValueError('Invalid transformation expression')
    nodes=list(ast.walk(tree))
    allowed=(ast.Expression,ast.Constant,ast.Name,ast.Load,ast.BinOp,ast.UnaryOp,ast.BoolOp,ast.Compare,ast.IfExp,ast.Call,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Mod,ast.USub,ast.UAdd,ast.Not,ast.And,ast.Or,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE)
    if len(nodes)>80 or any(not isinstance(n,allowed) for n in nodes):raise ValueError('Unsupported or overly complex expression')
    for n in nodes:
        if isinstance(n,ast.Name) and n.id not in ('x','abs','min','max'):raise ValueError('Expression names: x, abs, min, max')
        if isinstance(n,ast.Constant) and (type(n.value) not in (str,int,float,bool) or isinstance(n.value,str) and len(n.value)>256):raise ValueError('Invalid expression literal')
        if isinstance(n,ast.Call) and (not isinstance(n.func,ast.Name) or n.func.id not in ('abs','min','max') or n.keywords or not 1<=len(n.args)<=8):raise ValueError('Only abs/min/max calls with 1..8 arguments are supported')
    def depth(n):return 1+max((depth(c) for c in ast.iter_child_nodes(n)),default=0)
    if depth(tree)>20:raise ValueError('Expression nesting is too deep')
    return tree.body


def evaluate(text,x):
    def walk(n):
        if isinstance(n,ast.Constant):return number(n.value) if type(n.value) in (int,float) else n.value
        if isinstance(n,ast.Name):
            if n.id!='x':raise ValueError('Function names cannot be used as values')
            return x
        if isinstance(n,ast.IfExp):return walk(n.body if walk(n.test) else n.orelse)
        if isinstance(n,ast.UnaryOp):
            v=walk(n.operand)
            return not v if isinstance(n.op,ast.Not) else -number(v) if isinstance(n.op,ast.USub) else number(v)
        if isinstance(n,ast.BoolOp):
            return all(walk(v) for v in n.values) if isinstance(n.op,ast.And) else any(walk(v) for v in n.values)
        if isinstance(n,ast.Compare):
            a=walk(n.left)
            for op,node in zip(n.ops,n.comparators):
                b=walk(node);fn={ast.Eq:lambda:a==b,ast.NotEq:lambda:a!=b,ast.Lt:lambda:a<b,ast.LtE:lambda:a<=b,ast.Gt:lambda:a>b,ast.GtE:lambda:a>=b}[type(op)]
                if not fn():return False
                a=b
            return True
        if isinstance(n,ast.Call):
            args=[walk(a) for a in n.args]
            if n.func.id=='abs':
                if len(args)!=1:raise ValueError('abs requires one argument')
                return abs(number(args[0]))
            return min(args) if n.func.id=='min' else max(args)
        if isinstance(n,ast.BinOp):
            a,b=walk(n.left),walk(n.right)
            if isinstance(n.op,ast.Add) and isinstance(a,str) and isinstance(b,str):
                if len(a)+len(b)>4096:raise ValueError('Expression text exceeds String limit')
                return a+b
            a,b=number(a),number(b)
            if isinstance(n.op,ast.Add):return a+b
            if isinstance(n.op,ast.Sub):return a-b
            if isinstance(n.op,ast.Mult):return a*b
            if isinstance(n.op,ast.Div):return a/b
            if isinstance(n.op,ast.Mod):return a%b
        raise ValueError('Unsupported expression operation')
    try:
        with localcontext() as ctx:ctx.prec=50;return walk(expression(text))
    except (ArithmeticError,TypeError,InvalidOperation) as e:raise ValueError('Expression evaluation failed: '+type(e).__name__) from e


def options(job,source,destination):
    p={'bad_quality':'stop','preserve_timestamp':False,'preserve_quality':False}
    supplied=job.get('bridge');supplied={} if supplied is None else supplied
    if not isinstance(supplied,dict) or set(supplied)-set(p):raise ValueError('Invalid bridge settings')
    p.update(supplied)
    if p['bad_quality'] not in ('stop','filter','preserve'):raise ValueError('Bad-quality policy must be stop, filter or preserve')
    if type(p['preserve_timestamp']) is not bool or type(p['preserve_quality']) is not bool:raise ValueError('Metadata preservation must be Boolean')
    if (p['preserve_timestamp'] or p['preserve_quality']) and (source['protocol']!='opcua' or destination['protocol']!='opcua'):raise ValueError('Source timestamp/status preservation requires OPC UA source and destination; Modbus has no equivalent metadata')
    if p['bad_quality']=='preserve' and not p['preserve_quality']:raise ValueError('Preserving bad-quality values requires explicit OPC UA quality preservation')
    return p


def validate_mapping(m,source,destination):
    if type(m.get('enabled',True)) is not bool or type(m.get('on_change',False)) is not bool:raise ValueError('Mapping enabled/on_change must be Boolean')
    if m.get('unit','none') not in UNITS or m.get('boolean_mode','strict') not in BOOL_MODES:raise ValueError('Invalid unit or Boolean conversion')
    for k in ('scale','offset','minimum','maximum','threshold','filter_minimum','filter_maximum'):
        if m.get(k) is not None:number(m[k])
    for a,b in [('minimum','maximum'),('filter_minimum','filter_maximum')]:
        if m.get(a) is not None and m.get(b) is not None and number(m[a])>number(m[b]):raise ValueError(a+' exceeds '+b)
    if m.get('expression'):expression(m['expression'])
    if m.get('filter_expression'):expression(m['filter_expression'])
    if m.get('boolean_mode')=='equals' and (m.get('unit','none')!='none' or m.get('expression') or number(m.get('scale',1))!=1 or number(m.get('offset',0))!=0 or m.get('minimum') is not None or m.get('maximum') is not None):raise ValueError('Equals mode compares the raw typed source and cannot combine numeric transforms')
    if m.get('boolean_mode')=='equals':cast(m.get('true_value',True),source['dtype'])
    if destination['dtype']!='Boolean' and m.get('boolean_mode','strict')!='strict':raise ValueError('Boolean conversion modes require Boolean destination')
    if destination['dtype']=='DateTime' or source['dtype']=='DateTime':
        if (source['dtype'],destination['dtype']) not in (('DateTime','DateTime'),('DateTime','String'),('String','DateTime')):raise ValueError('DateTime supports identity and ISO String conversions only')
        if m.get('unit','none')!='none' or m.get('expression') or number(m.get('scale',1))!=1 or number(m.get('offset',0))!=0 or m.get('minimum') is not None or m.get('maximum') is not None:raise ValueError('DateTime conversions cannot use numeric transforms')
    if destination['dtype'] in ('String','DateTime') and (m.get('filter_minimum') is not None or m.get('filter_maximum') is not None):raise ValueError('Numeric filters require numeric destinations')
    if destination['dtype']=='String' and (m.get('unit','none')!='none' or number(m.get('scale',1))!=1 or number(m.get('offset',0))!=0 or m.get('minimum') is not None or m.get('maximum') is not None):raise ValueError('String destinations use identity/expression without numeric scale/clamp')


def converted(value,m,source,destination):
    validate_mapping(m,source,destination);kind=destination['dtype']
    value=cast(value,source['dtype'])
    if source['dtype']=='DateTime':value=value.isoformat() if kind=='String' else value
    if kind=='DateTime':return cast(value,kind)
    if kind=='String':
        x=value if isinstance(value,str) else 'true' if value is True else 'false' if value is False else str(value)
        return cast(evaluate(m['expression'],x) if m.get('expression') else x,kind)
    if kind=='Boolean' and m.get('boolean_mode')=='equals':return value==cast(m.get('true_value',True),source['dtype'])
    with localcontext() as ctx:
        ctx.prec=50;x=number(value) if not isinstance(value,str) or source['dtype']!='String' or m.get('unit','none')!='none' else value
        unit=m.get('unit','none')
        if unit!='none':
            x=number(x)
            x={'C_to_F':lambda:x*9/5+32,'F_to_C':lambda:(x-32)*5/9,'bar_to_kPa':lambda:x*100,'kPa_to_bar':lambda:x/100,'Pa_to_bar':lambda:x/100000,'ms_to_s':lambda:x/1000,'s_to_ms':lambda:x*1000,'rpm_to_Hz':lambda:x/60,'Hz_to_rpm':lambda:x*60,'percent_to_fraction':lambda:x/100,'fraction_to_percent':lambda:x*100}[unit]()
        if m.get('expression'):x=evaluate(m['expression'],x)
        if kind=='Boolean':
            mode=m.get('boolean_mode','strict')
            if isinstance(x,str) and x.lower() in ('true','false'):
                if number(m.get('scale',1))!=1 or number(m.get('offset',0))!=0 or m.get('minimum') is not None or m.get('maximum') is not None:raise ValueError('Boolean text cannot use numeric scale/clamp')
                return not cast(x,'Boolean') if mode=='invert' else cast(x,'Boolean') if mode=='strict' else number(x)!=0
            if mode!='strict':
                x=number(x)*number(m.get('scale',1))+number(m.get('offset',0))
                if m.get('minimum') is not None:x=max(x,number(m['minimum']))
                if m.get('maximum') is not None:x=min(x,number(m['maximum']))
            if mode=='invert':return not cast(x,'Boolean')
            if mode=='nonzero':return number(x)!=0
            if mode=='threshold':return number(x)>=number(m.get('threshold',0))
        return transform(x,m,kind)


def filtered(value,m,previous=None,has_previous=False):
    if not m.get('enabled',True):return 'Mapping disabled'
    for k,op in [('filter_minimum',lambda x,y:x<y),('filter_maximum',lambda x,y:x>y)]:
        if m.get(k) is not None and op(number(value),number(m[k])):return k
    if m.get('filter_expression'):
        x=number(value) if type(value) in (int,float,bool) else value
        passed=evaluate(m['filter_expression'],x)
        if type(passed) is not bool:raise ValueError('Filter expression must return Boolean')
        if not passed:return 'filter_expression'
    if m.get('on_change') and has_previous and previous==value:return 'Unchanged since last acknowledged delivery'
    return ''


class BridgeStore:
    def __init__(self):self.rows=deque();self.points=0;self.evicted=0;self.totals=Counter()
    def retain(self,row):
        from event_log import emit
        emit('bridge','WARNING' if any(row['counts'].get(k) for k in ('FAILED','UNKNOWN','TRANSFORM_ERROR')) else 'DEBUG','bridge_cycle',cycle=row['id'],job=row['job'],counts=row['counts'],duration_ms=row['duration_ms'])
        self.totals.update(row['counts']);self.rows.append(row);self.points+=len(row['points'])
        while len(self.rows)>200 or self.points>20000:self.points-=len(self.rows.popleft()['points']);self.evicted+=1
    def snapshot(self):return {'totals':dict(self.totals),'retained_cycles':len(self.rows),'retained_points':self.points,'evicted_cycles':self.evicted,'limits':{'cycles':200,'points':20000},'scope':'App-level source observations, transforms and destination write acknowledgements; not proof of physical process delivery. History is process-local.'}
DEFAULT_STORE=BridgeStore()
STORE=ContextVar('bridge_store',default=DEFAULT_STORE)


class Session:
    def __init__(self,job,source,destination,state):self.job=job;self.source=source;self.destination=destination;self.settings=options(job,source,destination);self.store=STORE.get();self.current=None;self.previous={};self.previous_metadata={};self.last=None;self.state=state
    def reconnected(self):
        self.previous.clear();self.previous_metadata.clear()
    def begin(self,retry_of=None):
        self.start=time.monotonic();self.current={'id':uuid.uuid4().hex,'job':self.job['id'],'name':self.job.get('name',self.job['id']),'time':time.time(),'source':self.source['id'],'destination':self.destination['id'],'points':[],'source_transaction':None,'write_transaction':None,'retry_of':retry_of,'settings':self.settings}
    def plan(self,raw,observations,transaction=None):
        self.current['source_transaction']=transaction;src={t['id']:t for t in self.source['tags']};dst={t['id']:t for t in self.destination['tags']};values={};metadata={};errors=[]
        for m in self.job['mappings']:
            a,b=src[m['source']],dst[m['destination']];ob=observations.get(a['id'],{});v=raw.get(a['id']);p={'source':a['id'],'source_tag':a['tag'],'destination':b['id'],'destination_tag':b['tag'],'source_dtype':a['dtype'],'destination_dtype':b['dtype'],'source_value':view_value(v,a['dtype']) if v is not None else None,'value':None,'source_quality':ob.get('quality','Unavailable'),'source_timestamp':ob.get('source_timestamp'),'outcome':'NOT_ATTEMPTED','reason':''};p.update(source_target=a.get('nodeid') if self.source['protocol']=='opcua' else f"{a.get('area','holding')}:{a.get('address')}",destination_target=b.get('nodeid') if self.destination['protocol']=='opcua' else f"{b.get('area','holding')}:{b.get('address')}",source_units=a.get('units',''),destination_units=b.get('units',''),transform={k:v for k,v in m.items() if k not in ('source','destination')});self.current['points'].append(p)
            try:
                if not m.get('enabled',True):p.update(outcome='FILTERED',reason='Mapping disabled');continue
                if ob.get('good') is False:
                    if self.settings['bad_quality']=='filter':p.update(outcome='FILTERED',reason='Bad source quality');continue
                    if self.settings['bad_quality']!='preserve':raise ValueError('Bad source quality; no destination write')
                if a['id'] not in raw:raise ValueError('Missing source value')
                output=converted(v,m,a,b);p['value']=view_value(output,b['dtype']);reason=filtered(output,m,self.previous.get(b['id']),b['id'] in self.previous)
                meta={}
                if self.settings['preserve_timestamp']:
                    stamp=ob.get('source_timestamp')
                    if stamp is None:raise ValueError('Source timestamp unavailable; refusing to invent it')
                    meta['source_timestamp']=stamp
                if self.settings['preserve_quality']:
                    if type(ob.get('status_code')) is not int:raise ValueError('Source OPC UA status unavailable')
                    meta['status_code']=ob['status_code']
                if reason=='Unchanged since last acknowledged delivery' and self.previous_metadata.get(b['id'],{})!=meta:reason=''
                if reason:p.update(outcome='FILTERED',reason=reason);continue
                p['requested_metadata']=meta
                if meta:metadata[b['id']]=meta
                values[b['id']]=output;p['outcome']='READY'
            except Exception as error:p.update(outcome='TRANSFORM_ERROR',reason=str(error)[:250]);errors.append(f"Mapping {a['tag']} → {b['tag']}: {error}")
        if errors:raise ValueError(errors[0])
        return [t for t in self.destination['tags'] if t['id'] in values],values,metadata
    def retry(self,tags,values,metadata):
        previous=self.last;self.begin(previous['id'] if previous else None)
        self.current['source_transaction']=previous.get('source_transaction') if previous else None
        byid={p['destination']:p for p in previous['points']} if previous else {}
        for t in tags:
            p=dict(byid.get(t['id'],{'destination':t['id'],'destination_tag':t['tag'],'destination_dtype':t['dtype']}));p.update(value=view_value(values[t['id']],t['dtype']),outcome='READY',reason='Confirmed-failed retry');self.current['points'].append(p)
    def finish(self,write_row=None,error=None):
        if self.current is None:return
        row=self.current
        if not row['points'] and error:
            src={t['id']:t for t in self.source['tags']};dst={t['id']:t for t in self.destination['tags']}
            row['points']=[{'source':m['source'],'source_tag':src[m['source']]['tag'],'destination':m['destination'],'destination_tag':dst[m['destination']]['tag'],'outcome':'NOT_ATTEMPTED','reason':'Source operation did not complete before transformation'} for m in self.job['mappings']]
        if write_row:
            row['write_transaction']=write_row['id'];outcomes={p['id']:p for p in write_row['points']}
            for p in row['points']:
                result=outcomes.get(p['destination'])
                if p['outcome']=='READY' and result:
                    p.update(outcome=result['outcome'],reason=result['status'])
                    if result['outcome']=='SUCCEEDED':
                        self.previous[p['destination']]=cast(p['value'],p['destination_dtype']);self.previous_metadata[p['destination']]=p.get('requested_metadata',{})
        for p in row['points']:
            if p['outcome']=='READY':p['outcome']='NOT_ATTEMPTED'
        counts=Counter(p['outcome'] for p in row['points']);row.update(counts=dict(counts),duration_ms=(time.monotonic()-self.start)*1000,error=type(error).__name__ if error else '')
        self.store.retain(row);self.state['bridge']={k:v for k,v in row.items() if k not in ('points','settings')};self.last=row;self.current=None
