"""Existing local API boundary protects health queries and explicit hold release."""
import json
from aiohttp import web
from communication_health import HealthHub
KEY=web.AppKey('communication_health',HealthHub)


async def listing(request):return web.json_response(request.app[KEY].snapshot())


async def release(request):
    body=await request.json()
    if body.get('confirm') is not True:raise ValueError('Explicit recovery target confirmation required')
    controller=request.app[KEY].controllers.get(request.match_info['id'])
    if controller is None:raise ValueError('Recovery worker not found or expired')
    if controller.unknown_writes and body.get('acknowledge_unknown') is not True:raise ValueError('Acknowledge unresolved write outcomes before resuming')
    controller.release(body.get('target'));return web.json_response({'ok':True,'message':'Recovery released; next cycle uses latest values, not uncertain replay'})


async def export(request):return web.Response(text=json.dumps(request.app[KEY].snapshot(),allow_nan=False),content_type='application/json',headers={'Content-Disposition':'attachment; filename="communication-health.json"'})


def install(app):app.add_routes([web.get('/api/health',listing),web.get('/api/health/export',export),web.post('/api/health/{id}/resume',release)])
