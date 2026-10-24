"""Read-only data verification API with bounded, private result evidence."""
import csv,io,json
from aiohttp import web
from verification import VerificationStore
from transaction_api import offset

KEY=web.AppKey('data_verification',VerificationStore)


def filters(request):return {key:request.query.get(key,'') for key in ('job','outcome','query')}
def brief(row):return {k:v for k,v in row.items() if k!='points'}


async def listing(request):
    store=request.app[KEY];rows=store.filtered(**filters(request));page=offset(request)
    return web.json_response({'summary':store.summary(),'total':len(rows),'page':page,'rows':[brief(r) for r in rows[page*50:(page+1)*50]]})


async def detail(request):
    row=request.app[KEY].find(request.match_info['id']);page=offset(request)
    return web.json_response({**brief(row),'point_page':page,'points':row['points'][page*50:(page+1)*50]})


async def export(request):
    store=request.app[KEY];rows=store.filtered(**filters(request));fmt=request.query.get('format','json')
    if fmt=='json':return web.Response(text=json.dumps({'summary':store.summary(),'results':rows},allow_nan=False),content_type='application/json',headers={'Content-Disposition':'attachment; filename="data-verification.json"'})
    if fmt!='csv':raise ValueError('Choose JSON or CSV')
    stream=io.StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(['verification','time','job','name','mode','result','transaction','point','tag','dtype','expected','actual','quality','source_timestamp','outcome','issues'])
    for r in rows:
        for p in r['points']:
            values=[r['id'],r['time'],r['job'],r['name'],r['mode'],r['outcome'],r['transaction'],p['id'],p['tag'],p['dtype'],p['expected'],p['actual'],p['quality'],p['source_timestamp'],p['outcome'],'; '.join(p['issues'])]
            writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in values])
    return web.Response(text=stream.getvalue(),content_type='text/csv',headers={'Content-Disposition':'attachment; filename="data-verification.csv"'})


def install(app):app.add_routes([web.get('/api/verifications',listing),web.get('/api/verifications/export',export),web.get('/api/verifications/{id}',detail)])
