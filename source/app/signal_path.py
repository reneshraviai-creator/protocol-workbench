"""Controlled OPC UA write -> Modbus observation, using monotonic timing."""
import asyncio
import copy
import math
import time
from decimal import Decimal
from drivers import adapter
from model import cast,transform,view_value,encode,default_project,uid


def definition(step,project):
    connections={c['id']:c for c in project['connections']}
    source=connections.get(step.get('source_connection'));destination=connections.get(step.get('destination_connection'))
    if not source or source['protocol']!='opcua':raise ValueError('Signal-path source must be an OPC UA connection')
    if not destination or destination['protocol']!='modbus':raise ValueError('Signal-path observation must be a Modbus TCP connection')
    a=next((t for t in source['tags'] if t['id']==step.get('source_tag')),None);b=next((t for t in destination['tags'] if t['id']==step.get('destination_tag')),None)
    if not a or not b:raise ValueError('Select a source tag and destination register/bit')
    value=cast(step['value'],a['dtype'])
    for k,default,lo,hi in [('scale',1,-1e12,1e12),('offset',0,-1e18,1e18),('tolerance',.001,0,1e12),('max_delay',5,.1,60),('poll_seconds',.1,.05,10)]:
        n=float(step.get(k,default))
        if not math.isfinite(n) or not lo<=n<=hi:raise ValueError(f'{k} must be finite and between {lo} and {hi}')
    if float(step.get('poll_seconds',.1))>float(step.get('max_delay',5)):raise ValueError('Poll interval must not exceed the allowed delay')
    if float(step.get('timeout',15))<=float(step.get('max_delay',5)):raise ValueError('Step timeout must allow connection setup in addition to the transfer deadline')
    expected=transform(value,step,b['dtype'])
    if b['dtype']=='Boolean' and float(step.get('tolerance',.001))!=0:raise ValueError('Boolean observations require zero tolerance')
    return source,destination,a,b,value,expected


def equal(a,b,tolerance):return abs(Decimal(str(int(a) if isinstance(a,bool) else a))-Decimal(str(int(b) if isinstance(b,bool) else b)))<=Decimal(str(tolerance))


def preview(step,project):
    source,dest,a,b,value,expected=definition(step,project)
    return {'source_name':source['name'],'source_tag':a['tag'],'source_type':a['dtype'],'probe':view_value(value,a['dtype']),'destination_name':dest['name'],'destination_tag':b['tag'],'destination_type':b['dtype'],'expected':view_value(expected,b['dtype']),'expected_words':encode(expected,b['dtype'],b.get('order','ABCD')),'area':b.get('area','holding'),'address':b['address'],'order':b.get('order','ABCD'),'unit':dest.get('device_id',1),'max_delay':float(step.get('max_delay',5)),'poll_seconds':float(step.get('poll_seconds',.1)),'tolerance':float(step.get('tolerance',.001)),'scale':float(step.get('scale',1)),'offset':float(step.get('offset',0))}


def build(project,body):
    step={k:body[k] for k in ('source_connection','destination_connection','source_tag','destination_tag','value','scale','offset','tolerance','max_delay','poll_seconds') if k in body}
    step.update(action='verify_path',label='Verify OPC UA → Modbus signal path',timeout=min(300,float(body.get('max_delay',5))+90))
    detail=preview(step,project);source,dest,a,b,_,_=definition(step,project)
    p=default_project();p['name']=body.get('name','Signal-path verification');p['connections']=[{**copy.deepcopy(source),'tags':[copy.deepcopy(a)]},{**copy.deepcopy(dest),'tags':[copy.deepcopy(b)]}]
    s={'name':body.get('name','Signal-path verification'),'description':'Controlled OPC UA probe and Modbus observation. Leaves the last written probe value; does not start an external PLC/gateway.','project':p,'steps':[step]}
    from scenarios import validate_scenario
    return validate_scenario(s),detail


def demo():
    a={'id':'path-source','name':'Local OPC UA probe','protocol':'opcua','url':'opc.tcp://127.0.0.1:15496','security':'None','timeout':3,'retry_seconds':.5,'tags':[{'id':'probe','tag':'Probe','dtype':'Float','nodeid':'ns=2;s=Probe','index':1}]}
    b={'id':'path-target','name':'Local Modbus observation','protocol':'modbus','host':'127.0.0.1','port':15096,'device_id':1,'timeout':3,'retry_seconds':.5,'tags':[{'id':'observed','tag':'Observed','dtype':'Float','area':'holding','address':0,'order':'CDAB','index':1}]}
    p=default_project();p.update(name='Isolated local signal-path lab',connections=[a,b],jobs=[{'id':'path-ua-server','name':'Local OPC UA server','kind':'server','connection':a['id'],'interval':.1,'dynamic':False,'simulation':{'pattern':'constant','value':0}},{'id':'path-mb-server','name':'Local Modbus server','kind':'server','connection':b['id'],'interval':.1,'dynamic':False,'simulation':{'pattern':'constant','value':0}},{'id':'path-route','name':'Local scaling route','kind':'gateway','source':a['id'],'connection':b['id'],'interval':.1,'verify':True,'mappings':[{'source':'probe','destination':'observed','scale':2,'offset':10}]}])
    step={'action':'verify_path','source_connection':a['id'],'destination_connection':b['id'],'source_tag':'probe','destination_tag':'observed','value':23.5,'scale':2,'offset':10,'tolerance':.001,'max_delay':3,'poll_seconds':.05,'timeout':30,'label':'Probe 23.5 → expect 57, CDAB word order'}
    from scenarios import validate_scenario
    return validate_scenario({'name':'Local signal-path verification','description':'Loopback-only OPC UA → scaling gateway → Modbus. Test-owned servers and gateway stop after the run. No PLC endpoint is used.','project':p,'steps':[{'action':'start_job','job':j['id'],'timeout':15} for j in p['jobs']]+[step]})


async def execute(step,project,result):
    source,dest,a,b,value,expected=definition(step,project);detail=preview(step,project);tol=step.get('tolerance',.001)
    result.update(path=detail,expectation={'expected':detail['expected'],'tolerance':tol,'max_delay_seconds':detail['max_delay']},write_outcome='NOT_ATTEMPTED',samples=[],tags=[{'id':a['id'],'tag':a['tag'],'dtype':a['dtype'],'state':'SKIPPED'},{'id':b['id'],'tag':b['tag'],'dtype':b['dtype'],'state':'SKIPPED'}])
    writer=adapter(source);reader=adapter(dest)
    try:
        await writer.connect();await writer.prepare([a],write=True);await reader.connect();await reader.prepare([b])
        original=(await writer.read([a]))[a['id']];baseline=(await reader.read([b]))[b['id']]
        result.update(baseline_source=view_value(original,a['dtype']),baseline_destination=view_value(baseline,b['dtype']))
        if equal(original,value,0) or equal(baseline,expected,tol):raise ValueError('Probe or expected value is already present. Choose a different probe so a fresh transition can be observed. No write was attempted.')
        start=time.monotonic();deadline=start+detail['max_delay'];result['write_outcome']='UNKNOWN';result['tags'][0]['state']='UNKNOWN'
        try:
            async with asyncio.timeout(max(.001,deadline-time.monotonic())):await writer.write([a],{a['id']:value})
        except TimeoutError:raise AssertionError('Transfer deadline expired during the source write; device write outcome is unknown')
        result['write_outcome']='ACKNOWLEDGED';result['written']=view_value(value,a['dtype']);result['write_ack_ms']=round((time.monotonic()-start)*1000,3);result['tags'][0].update(state='PASSED',written=result['written'])
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            try:
                async with asyncio.timeout(remaining):observed=(await reader.read([b]))[b['id']]
            except TimeoutError:result['deadline_detail']='Observation read did not finish before the deadline';break
            elapsed=time.monotonic()-start;raw=getattr(reader,'last_raw',{}).get(b['id']);result['observed']=view_value(observed,b['dtype']);result['raw_words']=raw
            sample={'elapsed_ms':round(elapsed*1000,3),'value':result['observed'],'raw_words':raw,'quality':'Modbus read succeeded'}
            result['samples'].append(sample)
            if len(result['samples'])>500:result['samples'].pop(0)
            result['tags'][1].update(observed=result['observed'],raw_words=raw)
            if elapsed<=detail['max_delay'] and equal(observed,expected,tol):
                result['observed_delay_ms']=round(elapsed*1000,3);result['tags'][1]['state']='PASSED';return
            await asyncio.sleep(min(detail['poll_seconds'],max(0,deadline-time.monotonic())))
        result['tags'][1]['state']='FAILED'
        raise AssertionError(f"No matching observation within {detail['max_delay']} seconds. Last value: {result.get('observed','not received')}; expected {detail['expected']} ± {tol}. Check the mapping, scaling, register address and word order.")
    except asyncio.CancelledError:
        if result['tags'][1]['state']=='SKIPPED':result['tags'][1]['state']='CANCELLED'
        raise
    except AssertionError:raise
    except Exception as error:
        result['tags'][1].update(state='ERROR',error=str(error));raise
    finally:
        result['measurement_note']='Observed upper-bound delay from write request to matching read completion, including communication and polling; not PLC-internal execution time. Probe values are not automatically reset.'
        for client in (reader,writer):
            try:await asyncio.wait_for(client.close(),3)
            except Exception:pass


def build_bulk(project,body):
    """Build sequential, independently reported probes; never perform device I/O."""
    import csv,io
    connections={c['id']:c for c in project['connections']}
    source=connections.get(body.get('source_connection'));dest=connections.get(body.get('destination_connection'))
    if not source or not dest:raise ValueError('Select both devices')
    rows=body.get('mappings')
    if 'csv' in body:
        reader=csv.DictReader(io.StringIO(body['csv'].lstrip('\ufeff')))
        if not {'source_tag','destination_tag','value'}<=set(reader.fieldnames or []):raise ValueError('CSV requires source_tag,destination_tag,value columns')
        rows=list(reader)
    if not isinstance(rows,list) or not 1<=len(rows)<=6500:raise ValueError('Provide 1..6500 mapping rows')
    def names(c):
        out={}
        for tag in c['tags']:out.setdefault(tag['tag'],[]).append(tag)
        return out
    sources=names(source);targets=names(dest);steps=[];details=[];used=set();chosen_a={};chosen_b={}
    for index,row in enumerate(rows,1):
        try:
            aa=sources.get(row.get('source_tag'),[]);bb=targets.get(row.get('destination_tag'),[])
            if len(aa)!=1 or len(bb)!=1:raise ValueError('Use unique exact configured tag names on both devices')
            a,b=aa[0],bb[0]
            if a['id'] in used:raise ValueError('Each source tag may appear only once')
            used.add(a['id']);chosen_a[a['id']]=a;chosen_b[b['id']]=b
            step={'action':'verify_path','source_connection':source['id'],'destination_connection':dest['id'],'source_tag':a['id'],'destination_tag':b['id'],'value':row['value'],'continue_on_failure':True,'label':f"{index}. {a['tag']} → {b['tag']}"}
            for key,default in [('scale',1),('offset',0),('tolerance',.001),('max_delay',5),('poll_seconds',.1)]:step[key]=row.get(key) if row.get(key) not in ('',None) else body.get(key,default)
            step['timeout']=min(300,float(step['max_delay'])+90)
            details.append(preview(step,project));steps.append(step)
        except (ValueError,KeyError,TypeError,OverflowError) as error:raise ValueError(f'Mapping row {index}: {error}') from error
    p=default_project();p['connections']=[{**copy.deepcopy(source),'tags':copy.deepcopy(list(chosen_a.values()))},{**copy.deepcopy(dest),'tags':copy.deepcopy(list(chosen_b.values()))}]
    s={'name':body.get('name','Bulk signal-path verification'),'description':'Sequential OPC UA → Modbus probes. Continues after individual failures/errors. Probe values remain written. No transfer route is created.','project':p,'steps':steps}
    from scenarios import validate_scenario
    return validate_scenario(s),details
