"""In-process ownership of listeners and physical write targets."""
from urllib.parse import urlparse
from model import width


def host(value):
    value=str(value).lower().rstrip('.')
    return 'loopback' if value in ('localhost','127.0.0.1','::1') else value


def endpoint(c):
    if c['protocol']=='opcua':
        u=urlparse(c['url']);return ('opcua',host(u.hostname),u.port or 4840,'')
    if c.get('transport')=='rtu':
        from serial_config import port_key
        return ('rtu','serial:'+port_key(c['serial_port']),0,'')
    return ('modbus',host(c['host']),int(c.get('port',502)),'')


def empty():return {'listeners':set(),'writes':set()}


def add_writes(plan,c,tags):
    e=endpoint(c)
    for t in tags:
        if c['protocol']=='opcua':
            from asyncua import ua
            node=ua.NodeId.from_string(t['nodeid']).to_string() if t.get('nodeid') else '*'
            plan['writes'].add((*e,node))
        else:
            area=t.get('area','holding');size=1 if area in ('coil','discrete') else width(t['dtype'])
            for address in range(int(t['address']),int(t['address'])+size):plan['writes'].add((*e,int(c.get('device_id',1)),area,address))


def job_plan(project,job):
    p=empty();c=next(c for c in project['connections'] if c['id']==job['connection']);chosen=set(job.get('tags',[]));tags=[t for t in c['tags'] if not chosen or t['id'] in chosen]
    if c.get('transport')=='rtu':p['listeners'].add((endpoint(c)[1],0))
    if job['kind']=='gateway':
        source=next(c for c in project['connections'] if c['id']==job['source'])
        if source.get('transport')=='rtu':p['listeners'].add((endpoint(source)[1],0))
    if job['kind']=='server':
        e=endpoint(c);p['listeners'].add((e[1],e[2]))
    if job['kind']=='gateway':
        keys={m['destination'] for m in job['mappings']};tags=[t for t in c['tags'] if t['id'] in keys]
    if job['kind']=='server' and c['protocol']=='opcua':tags=[{**t,'nodeid':'ns=2;s='+t['tag']} for t in tags]
    if job['kind'] in ('simulation','gateway') or (job['kind']=='server' and job.get('dynamic',True)):add_writes(p,c,tags)
    return p


def scenario_plan(s):
    from scenarios import selected_tags
    p=empty();project=s['project'];connections={c['id']:c for c in project['connections']};jobs={j['id']:j for j in project['jobs']}
    for step in s['steps']:
        keys=[step.get('connection'),step.get('source_connection'),step.get('destination_connection')]
        for key in keys:
            if key in connections and connections[key].get('transport')=='rtu':p['listeners'].add((endpoint(connections[key])[1],0))
        if step['action']=='start_job':
            part=job_plan(project,jobs[step['job']]);p['listeners'].update(part['listeners']);p['writes'].update(part['writes'])
        elif step['action']=='verify_path':
            c=connections[step['source_connection']];add_writes(p,c,[next(t for t in c['tags'] if t['id']==step['source_tag'])])
        elif step['action']=='write':
            c=connections[step['connection']];add_writes(p,c,selected_tags(step,c))
    return p


def conflict(a,b):
    for ah,ap in a['listeners']:
        for bh,bp in b['listeners']:
            if ap==bp and (ah==bh or ah in ('0.0.0.0','::') or bh in ('0.0.0.0','::')):return ('Serial port '+ah[7:]) if ah.startswith('serial:') else f'TCP listener {ah}:{ap}'
    shared=a['writes']&b['writes']
    if shared:return 'write target '+str(next(iter(shared)))
    for first,second in ((a,b),(b,a)):
        bound={ (key[0],*key[2:]) for key in first['writes'] if key[1] in ('0.0.0.0','::')}
        if any((key[0],*key[2:]) in bound for key in second['writes']):return 'write target served on a wildcard bind address'
        wildcard={key[:4] for key in first['writes'] if len(key)==5 and key[-1]=='*'}
        if any(key[:4] in wildcard for key in second['writes']):return 'OPC UA endpoint with unresolved NodeIds'
    return None


class ResourceRegistry:
    def __init__(self):self.owners={}
    def acquire(self,owner,label,plan,kind):
        # No awaits: validation and ownership assignment are atomic on the app event loop.
        for key,other in self.owners.items():
            if key==owner:continue
            overlap=conflict(plan,other['plan'])
            if overlap:raise ValueError(f"Resource conflict: {overlap} is reserved by {other['label']} ({key}). Stop that owner or choose different resources.")
        self.owners[owner]={'label':label,'plan':plan,'kind':kind}
    def release(self,owner):self.owners.pop(owner,None)
    def snapshot(self):return [{'owner':key,'label':v['label'],'kind':v['kind'],'listeners':[f'{h}:{p}' for h,p in sorted(v['plan']['listeners'])],'write_targets':len(v['plan']['writes'])} for key,v in self.owners.items()]
