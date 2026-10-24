"""Read-only performance dashboard endpoints use the existing local API boundary."""
import json
from aiohttp import web
from performance import Performance
KEY=web.AppKey('performance',Performance)
async def listing(request):
    size=int(request.query.get('history','120'))
    if not 1<=size<=3600:raise ValueError('Performance history count must be 1..3600')
    return web.json_response(request.app[KEY].snapshot(history_limit=size))
async def export(request):
    p=request.app[KEY];fmt=request.query.get('format','json')
    if fmt not in ('json','csv'):raise ValueError('Performance export format must be json or csv')
    return web.Response(text=p.csv() if fmt=='csv' else json.dumps(p.snapshot(),allow_nan=False),content_type='text/csv' if fmt=='csv' else 'application/json',headers={'Content-Disposition':f'attachment; filename="performance.{fmt}"'})
def install(app):app.add_routes([web.get('/api/performance',listing),web.get('/api/performance/export',export)])
