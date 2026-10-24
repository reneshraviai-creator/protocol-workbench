"""Versioned portable scenario templates. No execution during preview/import."""
import copy
import uuid
from scenarios import validate_scenario

FORMAT = 'protocol-workbench-template'
SIM = set('pattern minimum maximum period seed value sequence'.split())
def fields(obj, names):
    return {k: copy.deepcopy(v) for k,v in obj.items() if k in names}
def portable(scenario):
    s=validate_scenario(scenario)
    result=fields(s, {'name','description','steps'})
    result['steps']=[fields(x,set('action label timeout connection scope tag tags variable value operator expected tolerance minimum maximum seconds job source_connection destination_connection source_tag destination_tag scale offset max_delay poll_seconds continue_on_failure when retry check source_step'.split())) for x in s['steps']]
    p=s['project']; result['project']={'version':1,'name':p['name'],'settings':{'autostart':False},'connections':[],'jobs':[]}
    parameters=[]
    for c in p['connections']:
        clean=fields(c,set('id name protocol timeout retry_seconds security device_id port transport baudrate bytesize parity stopbits'.split()))
        clean['tags']=[]
        for t in c.get('tags',[]):
            tag=fields(t,set('id tag dtype index nodeid browse_name address area order units initial_value'.split()))
            if 'simulation' in t:tag['simulation']=fields(t['simulation'],SIM)
            clean['tags'].append(tag)
        key='url' if c['protocol']=='opcua' else ('serial_port' if c.get('transport')=='rtu' else 'host')
        clean[key]='${'+c['id']+'}'
        parameters.append({'id':c['id'],'name':c.get('name',c['id']),'protocol':c['protocol'],'field':key})
        result['project']['connections'].append(clean)
    for j in p['jobs']:
        clean=fields(j,set('id name kind connection source interval tags verify dynamic max_age_seconds bad_quality_policy stale_policy'.split()))
        if 'simulation' in j:clean['simulation']=fields(j['simulation'],SIM)
        if 'mappings' in j:clean['mappings']=[fields(m,set('source destination scale offset minimum maximum'.split())) for m in j['mappings']]
        result['project']['jobs'].append(clean)
    return {'format':FORMAT,'version':1,'scenario':result,'parameters':parameters}

def preview(document, bindings=None, name=None):
    if not isinstance(document,dict) or document.get('format')!=FORMAT or type(document.get('version')) is not int or document['version']!=1:
        raise ValueError('Unsupported template format or version')
    s=copy.deepcopy(document['scenario']); bindings=bindings or {}
    if not isinstance(s,dict) or not isinstance(s.get('project'),dict) or not isinstance(s['project'].get('connections'),list):raise ValueError('Template needs a scenario project with connections')
    if not isinstance(bindings,dict):raise ValueError('Endpoint bindings must be an object')
    for c in s['project']['connections']:
        value=bindings.get(c['id'])
        if value:
            if not isinstance(value,str) or value!=value.strip() or any(ch.isspace() for ch in value):raise ValueError('Endpoint must be text without whitespace')
            if c['protocol']=='opcua':
                from urllib.parse import urlparse
                u=urlparse(value)
                if u.scheme!='opc.tcp' or not u.hostname or u.username or u.password or u.query or u.fragment:raise ValueError('Use an opc.tcp endpoint without embedded credentials, query or fragment')
                if u.port is not None and not 1<=u.port<=65535:raise ValueError('Invalid OPC UA TCP port')
            elif c.get('transport')=='rtu':
                from serial_config import validate
                validate({**c,'serial_port':value})
            elif any(ch in value for ch in '/@?#') :raise ValueError('Enter a Modbus hostname or IP address only')
    # Never trust supplied parameter metadata or imported credential fields.
    for index,c in enumerate(s['project']['connections']):
        key='url' if c['protocol']=='opcua' else ('serial_port' if c.get('transport')=='rtu' else 'host')
        if c.get(key)!='${'+c['id']+'}':raise ValueError('Template endpoint must be a placeholder')
        c[key]=bindings.get(c['id']) or (f'opc.tcp://127.0.0.1:{18000+index}' if key=='url' else ('COM1' if key=='serial_port' else '127.0.0.1'))
        for field in ('username','password','certificate','private_key','server_certificate'):c.pop(field,None)
    clean=portable(s)
    if name is not None:clean['scenario']['name']=name
    # Re-export through the allowlist, dropping unknown fields at every level.
    resolved=copy.deepcopy(clean['scenario'])
    missing=[]
    for index,c in enumerate(resolved['project']['connections']):
        key='url' if c['protocol']=='opcua' else ('serial_port' if c.get('transport')=='rtu' else 'host')
        if not bindings.get(c['id']):missing.append(c['id'])
        c[key]=bindings.get(c['id']) or (f'opc.tcp://127.0.0.1:{18000+index}' if key=='url' else ('COM1' if key=='serial_port' else '127.0.0.1'))
    validate_scenario(resolved)
    return clean,resolved,missing

def instantiate(document, bindings, name=None):
    clean,s,missing=preview(document,bindings,name)
    if missing:raise ValueError('Configure every endpoint before importing')
    s['id']=uuid.uuid4().hex
    return s
