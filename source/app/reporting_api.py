"""Local report/log UI endpoints; snapshots are collected server-side only."""
import asyncio,json,time
from aiohttp import web
from event_log import EventLog,emit
from engineering_reports import ReportStore,collect,csv_export,KINDS
KEY=web.AppKey('reports',ReportStore)

def filters(request):return {k:request.query[k] for k in ('query','level','category','since','until') if request.query.get(k)}
async def logs(request):return web.json_response(request.app['event_log'].search(**filters(request),page=int(request.query.get('page',0)),limit=100))
async def log_settings(request):
    s=request.app['event_log'];v=s.configure(await request.json());s.emit('application','WARNING','logging_policy_changed',settings=v);return web.json_response(v)
async def export_logs(request):
    fmt=request.query.get('format','json');text=await asyncio.to_thread(request.app['event_log'].export,fmt,**filters(request));return web.Response(text=text,content_type='application/json' if fmt=='json' else 'text/csv',headers={'Content-Disposition':f'attachment; filename="structured-logs.{fmt}"'})
async def history(request):return web.json_response(await asyncio.to_thread(request.app[KEY].history))
async def templates(request):return web.json_response(request.app[KEY].templates())
async def save_template(request):return web.json_response(request.app[KEY].save_template(await request.json()))
async def preview(request):
    b=await request.json()
    if not isinstance(b,dict) or set(b)-{'type','selection','template_id'}:raise ValueError('Preview accepts type, selection and template_id only; recorded results cannot be supplied')
    store=request.app[KEY];chosen=next((x for x in store.templates() if x['id']==b.get('template_id','builtin-'+str(b.get('type')))),None)
    if not chosen:raise ValueError('Report template not found')
    records,source=collect(request.app,b['type'],b.get('selection',{}));spec={k:v for k,v in chosen.items() if k!='id'}
    return web.json_response(store.preview(b['type'],records,source,spec,request.app['event_log'].secrets))
async def save(request):
    b=await request.json()
    if set(b)!={'preview_id'}:raise ValueError('Save requires the reviewed preview ID only')
    row=request.app[KEY].save(b['preview_id']);emit('application','INFO','report_saved',report=row['id'],type=row['type'],status=row['summary']['status']);return web.json_response(row)
async def detail(request):return web.json_response(request.app[KEY].get(request.match_info['id']))
async def download(request):
    doc=request.app[KEY].get(request.match_info['id']);fmt=request.query.get('format','json')
    if fmt=='json':body=json.dumps(doc,ensure_ascii=False,allow_nan=False).encode();mime='application/json'
    elif fmt=='csv':body=(await asyncio.to_thread(csv_export,doc)).encode();mime='text/csv'
    elif fmt=='pdf':
        from report_pdf import render
        body=await asyncio.to_thread(render,doc);mime='application/pdf'
    else:raise ValueError('Report export must be pdf/csv/json')
    return web.Response(body=body,content_type=mime,headers={'Content-Disposition':f'attachment; filename="engineering-report-{doc["id"]}.{fmt}"'})
def install(app,folder):
    app['event_log']=EventLog(folder/'structured-logs');app['event_log'].learn(app['engine'].project);app[KEY]=ReportStore(folder/'engineering-reports.sqlite3')
    app.add_routes([web.get('/api/event-logs',logs),web.put('/api/event-logs/settings',log_settings),web.get('/api/event-logs/export',export_logs),web.get('/api/engineering-reports',history),web.get('/api/report-templates',templates),web.post('/api/report-templates',save_template),web.post('/api/engineering-reports/preview',preview),web.post('/api/engineering-reports/save',save),web.get('/api/engineering-reports/{id}',detail),web.get('/api/engineering-reports/{id}/download',download)])
