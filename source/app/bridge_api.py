"""Existing local API boundary protects bridge evidence and reusable maps."""
import csv,io,json,re,uuid
from pathlib import Path
from aiohttp import web
from bridge import BridgeStore,converted,filtered,options,validate_mapping
from model import cast,view_value
KEY=web.AppKey('bridge_evidence',BridgeStore)
FOLDER=web.AppKey('bridge_templates',Path)


def brief(r):return {k:v for k,v in r.items() if k!='points'}
async def listing(request):
    job=request.query.get('job','');store=request.app[KEY];rows=[r for r in reversed(store.rows) if not job or r['job']==job];page=int(request.query.get('page',0))
    if not 0<=page<=100000:raise ValueError('Invalid bridge page')
    return web.json_response({'summary':store.snapshot(),'total':len(rows),'rows':[brief(r) for r in rows[page*25:(page+1)*25]],'page':page})
async def detail(request):
    r=next((r for r in request.app[KEY].rows if r['id']==request.match_info['id']),None)
    if not r:raise ValueError('Bridge cycle not found or evicted')
    offset=int(request.query.get('offset',0))
    if not 0<=offset<=20000:raise ValueError('Invalid bridge point offset')
    return web.json_response({**brief(r),'points':r['points'][offset:offset+100],'offset':offset,'total_points':len(r['points'])})
async def export(request):
    rows=[r for r in request.app[KEY].rows if not request.query.get('job') or r['job']==request.query['job']];fmt=request.query.get('format','json')
    if fmt=='json':text=json.dumps({'summary':request.app[KEY].snapshot(),'rows':rows},allow_nan=False);mime='application/json'
    elif fmt=='csv':
        f=io.StringIO();w=csv.writer(f);w.writerow(['cycle','job','time','source_transaction','write_transaction','source_tag','destination_tag','source_value','output_value','source_quality','source_timestamp','delivery_outcome','reason','cycle_ms'])
        def safe(v):
            text=str(v) if v is not None else ''
            return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text
        for r in rows:
            for p in r['points']:w.writerow([safe(v) for v in [r['id'],r['job'],r['time'],r['source_transaction'],r['write_transaction'],p.get('source_tag'),p.get('destination_tag'),p.get('source_value'),p.get('value'),p.get('source_quality'),p.get('source_timestamp'),p['outcome'],p.get('reason'),r['duration_ms']]])
        text=f.getvalue();mime='text/csv'
    else:raise ValueError('Bridge export format must be json/csv')
    return web.Response(text=text,content_type=mime,headers={'Content-Disposition':f'attachment; filename="bridge-delivery.{fmt}"'})
async def preview(request):
    b=await request.json();a=b['source_tag'];d=b['destination_tag'];m=b['mapping'];v=cast(b['value'],a['dtype']);output=converted(v,m,a,d);reason=filtered(output,m)
    return web.json_response({'source':view_value(v,a['dtype']),'value':view_value(output,d['dtype']),'destination_dtype':d['dtype'],'filtered':bool(reason),'reason':reason,'scope':'Offline transformation sample only; no device I/O or delivery claim. On-change needs live acknowledged history.'})


def validate_template(t):
    if not isinstance(t,dict) or t.get('version')!=1 or not isinstance(t.get('name'),str) or not 1<=len(t['name'].strip())<=100:raise ValueError('Mapping template needs version 1 and a name of 1..100 characters')
    maps=t.get('mappings')
    if not isinstance(maps,list) or not 1<=len(maps)<=20000:raise ValueError('Mapping template needs 1..20000 rows')
    destinations=set()
    for m in maps:
        for k in ('source_tag','destination_tag'):
            if not isinstance(m.get(k),str) or not 1<=len(m[k])<=250:raise ValueError('Template tag names must be text')
        if m['destination_tag'] in destinations:raise ValueError('Duplicate template destination')
        destinations.add(m['destination_tag'])
        for k in ('source_address','destination_address'):
            if k in m and (not isinstance(m[k],str) or len(m[k])>1000):raise ValueError('Template address snapshots must be text up to 1000 characters')
        from model import TYPES
        if m.get('source_dtype') not in TYPES or m.get('destination_dtype') not in TYPES:raise ValueError('Template datatypes must be canonical')
        validate_mapping(m,{'dtype':m['source_dtype']},{'dtype':m['destination_dtype']})
    options({'bridge':t.get('bridge')},{'protocol':'opcua'},{'protocol':'opcua'})
    return t
async def templates(request):
    folder=request.app[FOLDER];folder.mkdir(exist_ok=True);rows=[]
    for p in folder.glob('*.json'):
        if len(rows)>=200:break
        try:rows.append(json.loads(p.read_text()))
        except (ValueError,OSError):continue
    return web.json_response({'templates':rows})
async def save_template(request):
    b=validate_template(await request.json());ident=b.get('id') or uuid.uuid4().hex
    if not re.fullmatch('[0-9a-f]{32}',str(ident)):raise ValueError('Invalid mapping template ID')
    folder=request.app[FOLDER];folder.mkdir(exist_ok=True);path=folder/(ident+'.json')
    if not path.exists() and len(list(folder.glob('*.json')))>=200:raise ValueError('Maximum 200 mapping templates')
    # Whitelist reusable fields: no connection URLs, credentials, user paths or job IDs.
    keys=('source_tag','destination_tag','source_dtype','destination_dtype','source_address','destination_address','scale','offset','minimum','maximum','unit','expression','boolean_mode','threshold','true_value','filter_minimum','filter_maximum','filter_expression','on_change','enabled')
    t={'version':1,'id':ident,'name':b['name'].strip(),'bridge':b.get('bridge',{}),'mappings':[{k:m[k] for k in keys if k in m} for m in b['mappings']]};temp=path.with_suffix('.tmp');temp.write_text(json.dumps(t,indent=2,allow_nan=False));temp.replace(path);return web.json_response(t)
async def resolve_template(request):
    b=await request.json();t=validate_template(b['template']);a=b['source'];d=b['destination'];options({'bridge':t.get('bridge')},a,d);src={x['tag']:x for x in a['tags']};dst={x['tag']:x for x in d['tags']};mappings=[];changes=[]
    for m in t['mappings']:
        s=src.get(m['source_tag']);v=dst.get(m['destination_tag'])
        if not s or not v:raise ValueError('Template has an unknown exact source/destination tag name')
        if s['dtype']!=m['source_dtype'] or v['dtype']!=m['destination_dtype']:raise ValueError('Template datatype mismatch; review the device mapping')
        mapping={k:value for k,value in m.items() if k not in ('source_tag','destination_tag','source_dtype','destination_dtype','source_address','destination_address')};mapping.update(source=s['id'],destination=v['id']);validate_mapping(mapping,s,v);mappings.append(mapping)
        for key,tag,connection in [('source_address',s,a),('destination_address',v,d)]:
            current=tag.get('nodeid') if connection['protocol']=='opcua' else f"{tag.get('area','holding')}:{tag.get('address')}:{tag.get('order','ABCD')}"
            if key in m and m[key]!=current:changes.append({'tag':tag['tag'],'field':key,'template':m[key],'current':current})
    return web.json_response({'mappings':mappings,'bridge':t.get('bridge',{}),'address_changes':changes,'scope':'Resolved against current tag definitions; addresses are not silently overwritten. Review before saving.'})
async def delete_template(request):
    b=await request.json();ident=request.match_info['id']
    if b.get('confirm') is not True or not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('Confirm a valid template deletion')
    path=request.app[FOLDER]/(ident+'.json')
    if not path.exists():raise ValueError('Mapping template not found')
    trash=path.parent/'trash';trash.mkdir(exist_ok=True);path.replace(trash/(ident+'-'+uuid.uuid4().hex+'.json'));return web.json_response({'ok':True})

def install(app,folder):
    app[KEY]=BridgeStore();app[FOLDER]=folder
    app.add_routes([web.get('/api/bridges',listing),web.get('/api/bridges/export',export),web.post('/api/bridges/preview',preview),web.get('/api/bridges/{id}',detail),web.get('/api/bridge-templates',templates),web.post('/api/bridge-templates',save_template),web.post('/api/bridge-templates/resolve',resolve_template),web.post('/api/bridge-templates/{id}/delete',delete_template)])
