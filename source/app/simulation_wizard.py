"""Compile a guided simulation configuration into an isolated timed scenario."""
import copy
import math
from model import default_project,validate_project,uid,cast,view_value
from tag_builder import generate_tags,integer
from scenarios import validate_scenario


def build_simulation(spec):
    mode=spec.get('mode','local')
    if mode not in ('local','existing'):raise ValueError('Choose local simulation or an existing device')
    c=copy.deepcopy(spec['connection']);c['id']=uid()
    if c.get('protocol') not in ('opcua','modbus'):raise ValueError('Select OPC UA or Modbus')
    if mode=='local':
        if c['protocol']=='opcua':
            from urllib.parse import urlparse
            if urlparse(c.get('url','')).hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('Local simulator must listen on a loopback address')
            c['security']='None';c.pop('username',None);c.pop('password',None)
        elif c.get('host') not in ('127.0.0.1','localhost','::1'):raise ValueError('Local simulator must listen on a loopback address')
    source=spec.get('tag_source','generate')
    if source=='generate':
        c['tags']=[];settings=copy.deepcopy(spec.get('tag_spec',{}))
        if mode=='local' and c['protocol']=='opcua':settings.update(namespace=2,node_prefix='')
        c['tags']=generate_tags(c,settings)
        for t in c['tags']:
            if spec.get('initial_value','')!='':t['initial_value']=spec['initial_value']
            if spec.get('units','')!='':t['units']=spec['units']
    elif source=='configured':
        if not c.get('tags'):raise ValueError('Selected connection has no tags; generate or import points first')
        # IDs are scenario-local, preserving any per-tag overrides.
        for t in c['tags']:t['id']=uid()
        if mode=='local' and c['protocol']=='opcua':
            from asyncua import ua
            for t in c['tags']:t['nodeid']=ua.NodeId(t['tag'],2).to_string()
    else:raise ValueError('Choose generated tags or configured tags')
    if mode=='existing' and c['protocol']=='modbus' and any(t.get('area','holding') in ('input','discrete') for t in c['tags']):raise ValueError('External writes require holding registers or coils')
    interval=float(spec.get('interval',2))
    if not math.isfinite(interval) or not .1<=interval<=60:raise ValueError('Update interval must be 0.1..60 seconds')
    duration=integer(spec,'duration',60,1,3600)
    job={'id':uid(),'name':'Simulate '+str(len(c['tags']))+' points','kind':'server' if mode=='local' else 'simulation','connection':c['id'],'interval':interval,'tags':[],'simulation':copy.deepcopy(spec.get('simulation',{})),'dynamic':True,'verify':True}
    p=default_project();p.update(name=spec.get('name','My simulation'),connections=[c],jobs=[job])
    validate_project(p)
    steps=[{'action':'start_job','job':job['id'],'timeout':min(300,max(30,interval+30)),'label':'Start local simulator' if mode=='local' else 'Start writing to existing device'}]
    remaining=duration
    while remaining:
        seconds=min(240,remaining);steps.append({'action':'wait','seconds':seconds,'timeout':seconds+10,'label':f'Simulate for {seconds} seconds'});remaining-=seconds
    steps.append({'action':'read','connection':c['id'],'scope':'all','variable':'final_values','timeout':120,'label':f'Read all {len(c["tags"])} points before cleanup'})
    s={'name':p['name'],'description':f'{mode.title()} {c["protocol"].upper()} simulation · {len(c["tags"])} tags · {duration}s after readiness · {interval}s update interval. Test-owned jobs stop automatically.','project':p,'steps':steps}
    validate_scenario(s)
    return s
