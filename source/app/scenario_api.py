"""Version/history and result comparison through the existing local API boundary."""
from aiohttp import web
from scenario_automation import compare_runs

async def versions(request):return web.json_response(request.app['catalogue'].versions(request.match_info['id']))
async def revision(request):return web.json_response(request.app['catalogue'].historical(request.match_info['id'],request.match_info['revision']))
async def comparison(request):
    store=request.app['catalogue'];return web.json_response(compare_runs(store.get('runs',request.query['left']),store.get('runs',request.query['right'])))
async def evidence(request):
    r=request.app['catalogue'].get('runs',request.match_info['id']);r.pop('snapshot',None)
    return web.json_response(r,headers={'Content-Disposition':'attachment; filename="test-evidence.json"'})
def install(app):
    app.add_routes([web.get('/api/scenarios/{id}/versions',versions),web.get('/api/scenarios/{id}/versions/{revision}',revision),web.get('/api/run-comparison',comparison),web.get('/api/runs/{id}/evidence',evidence)])
