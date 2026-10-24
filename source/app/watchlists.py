"""Persistent selections referencing saved workspace devices, never credentials."""
import hashlib
import json
import time
import uuid
from live_monitor import monitor_settings


def signature(connection, selected):
    tags={t['id']:t for t in connection['tags']}
    if not isinstance(selected,list) or not selected or len(set(selected))!=len(selected) or any(k not in tags for k in selected):raise ValueError('Select known unique tags')
    fields=('id','tag','dtype','nodeid','browse_name','address','area','order')
    target={k:connection.get(k) for k in ('id','protocol','url','host','port','device_id','security')}
    if connection.get('transport')=='rtu':target.update({k:connection.get(k) for k in ('transport','serial_port','baudrate','bytesize','parity','stopbits')})
    target['tags']=[{k:tags[key].get(k) for k in fields} for key in sorted(selected)]
    return hashlib.sha256(json.dumps(target,sort_keys=True).encode()).hexdigest()


def resolve(row, project):
    c=next((c for c in project['connections'] if c['id']==row['connection']),None)
    if not c:raise ValueError('Device is missing from the saved workspace')
    if signature(c,row['tags'])!=row['signature']:raise ValueError('Device endpoint or selected tag definitions changed. Create a new watchlist after reviewing the device.')
    return c


def make(name,connection,tags,settings,trends=None):
    if not isinstance(name,str) or not name.strip() or len(name)>120:raise ValueError('Watchlist name must contain 1..120 characters')
    sig=signature(connection,tags);trends=trends or tags[:1]
    if not isinstance(trends,list) or len(trends)>6 or len(set(trends))!=len(trends) or any(t not in tags for t in trends):raise ValueError('Choose up to six monitored trend tags')
    return {'id':uuid.uuid4().hex,'name':name.strip(),'connection':connection['id'],'tags':tags,'settings':monitor_settings(settings),'trends':trends,'signature':sig,'updated':time.time()}
