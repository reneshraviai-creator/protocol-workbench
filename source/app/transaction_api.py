"""Read-only verification endpoints; protected by existing API middleware."""
import csv
import io
import json
from aiohttp import web
from transactions import TransactionStore

KEY = web.AppKey('transactions', TransactionStore)


def filters(request):
    return {key: request.query.get(key, '') for key in ('protocol', 'operation', 'outcome', 'query')}


def offset(request):
    page = int(request.query.get('page', 0))
    if not 0 <= page <= 1000000:
        raise ValueError('Invalid page')
    return page


def brief(row):
    return {k: v for k, v in row.items() if k not in ('points', 'batches')}


async def listing(request):
    store = request.app[KEY]
    rows = store.filtered(**filters(request))
    page = offset(request)
    return web.json_response({'summary': store.summary(), 'total': len(rows), 'page': page,
                              'rows': [brief(r) for r in rows[page*50:(page+1)*50]]})


async def detail(request):
    row = request.app[KEY].find(request.match_info['id'])
    page = offset(request)
    return web.json_response({**brief(row), 'batches': row['batches'], 'point_page': page,
                              'points': row['points'][page*50:(page+1)*50]})


async def export(request):
    rows = request.app[KEY].filtered(**filters(request))
    fmt = request.query.get('format', 'json')
    if fmt == 'json':
        text = json.dumps({'scope': 'Retained completed client data read/write transactions; not full wire capture',
                           'summary': request.app[KEY].summary(), 'transactions': rows}, allow_nan=False)
        return web.Response(text=text, content_type='application/json', headers={'Content-Disposition': 'attachment; filename="communication-transactions.json"'})
    if fmt != 'csv':
        raise ValueError('Choose JSON or CSV')
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['transaction', 'protocol', 'connection', 'source', 'destination', 'operation', 'request_time', 'response_time', 'duration_ms', 'batch', 'point_id', 'tag', 'target', 'outcome', 'status'])
    for row in rows:
        for point in row['points']:
            values = [row['id'], row['protocol'], row['connection'], row['source'], row['destination'], row['operation'], row['request_time'], row['response_time'], row['duration_ms'], point['batch_id'], point['id'], point['tag'], point['target'], point['outcome'], point['status']]
            # Avoid spreadsheet formula execution for user-provided identifiers.
            writer.writerow(["'"+v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for v in values])
    return web.Response(text=stream.getvalue(), content_type='text/csv', headers={'Content-Disposition': 'attachment; filename="communication-transactions.csv"'})


def install(app):
    app.add_routes([web.get('/api/transactions', listing), web.get('/api/transactions/export', export), web.get('/api/transactions/{id}', detail)])
