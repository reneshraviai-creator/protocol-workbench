"""Bounded client data-operation evidence. Never records credentials or values.

A transaction is one adapter read/write invocation; a batch is one service request.
Point success denotes a Good service result, not process-value verification.
"""
import asyncio
import time
import uuid
from collections import Counter, deque
from contextlib import nullcontext
from contextvars import ContextVar
from functools import wraps
from urllib.parse import urlsplit

OUTCOMES = ('SUCCEEDED', 'FAILED', 'UNKNOWN', 'NOT_ATTEMPTED')


def endpoint(config):
    if config.get('protocol') == 'opcua' or 'url' in config:
        try:
            u = urlsplit(config.get('url', ''))
            host = u.hostname or 'unknown'
            return f'opc.tcp://{host}:{u.port or 4840}'
        except ValueError:
            return 'opc.tcp://invalid-endpoint'
    if config.get('transport') == 'rtu':
        return f"serial:{str(config.get('serial_port', 'unknown'))[:200]} / unit {config.get('device_id', 1)}"
    return f"{str(config.get('host', 'unknown'))[:200]}:{config.get('port', 502)} / unit {config.get('device_id', 1)}"


class TransactionStore:
    def __init__(self, max_records=500, max_points=50000, performance=None):
        self.performance = performance
        self.max_records = max_records
        self.max_points = max_points
        self.rows = deque()
        self.retained_points = 0
        self.evicted = 0
        self.started = time.time()
        self.totals = Counter()
        self.latency_sum = 0.0
        self.latency_count = 0
        self.latency_min = None
        self.latency_max = None
        self.recent = deque(maxlen=10000)

    def begin(self, config, operation, tags):
        protocol = 'OPC UA' if config.get('protocol') == 'opcua' or 'url' in config else 'Modbus RTU' if config.get('transport') == 'rtu' else 'Modbus TCP'
        row = {'id': uuid.uuid4().hex, 'connection': str(config.get('id', ''))[:200],
               'protocol': protocol, 'source': 'Protocol Workbench client',
               'destination': endpoint(config), 'operation': operation,
               'request_time': time.time(), 'response_time': None, 'duration_ms': None,
               'state': 'IN_PROGRESS', 'batches': [], 'points': [
                   {'id': str(t['id']), 'tag': str(t.get('tag', t['id']))[:200],
                    'target': str(t.get('nodeid', f"{t.get('area', 'holding')}:{t.get('address', '')}"))[:250],
                    'outcome': 'NOT_ATTEMPTED', 'batch_id': None, 'status': ''} for t in tags]}
        return Transaction(self, row)

    def retain(self, row):
        from event_log import emit
        emit('communication','DEBUG' if row['state']=='SUCCEEDED' else 'WARNING','client_transaction',transaction=row['id'],connection=row['connection'],operation=row['operation'],state=row['state'],counts=row['counts'])
        if row['state'] not in ('SUCCEEDED','NOT_ATTEMPTED'):emit('protocol','ERROR' if row['counts'].get('FAILED') else 'WARNING','protocol_outcome',transaction=row['id'],state=row['state'],error=row.get('error'),statuses=list(dict.fromkeys(p['status'] for p in row['points']))[:50])
        self.totals['transactions'] += 1
        self.totals.update(row['counts'])
        self.totals['batches'] += len(row['batches'])
        for batch in row['batches']:
            self.totals['requests'] += 1
            if batch['response_time'] is not None:
                self.totals['responses'] += 1
                latency = batch['duration_ms']
                self.latency_sum += latency
                self.latency_count += 1
                self.latency_min = latency if self.latency_min is None else min(self.latency_min, latency)
                self.latency_max = latency if self.latency_max is None else max(self.latency_max, latency)
        self.recent.append((time.monotonic(), row['counts']['attempted']))
        self.rows.append(row)
        self.retained_points += len(row['points'])
        while len(self.rows) > self.max_records or self.retained_points > self.max_points:
            self.retained_points -= len(self.rows.popleft()['points'])
            self.evicted += 1

    def summary(self):
        attempted = self.totals['attempted']
        now = time.monotonic()
        while self.recent and now - self.recent[0][0] > 10:
            self.recent.popleft()
        return {**{k: self.totals[k] for k in ('transactions', 'batches', 'requests', 'responses', 'total', 'attempted', *OUTCOMES)},
                'success_percentage': 100 * self.totals['SUCCEEDED'] / attempted if attempted else None,
                'operation_rate': sum(n for _, n in self.recent) / 10,
                'average_latency_ms': self.latency_sum / self.latency_count if self.latency_count else None,
                'minimum_latency_ms': self.latency_min, 'maximum_latency_ms': self.latency_max,
                'latency_samples': self.latency_count, 'retained_transactions': len(self.rows),
                'retained_points': self.retained_points, 'evicted_transactions': self.evicted,
                'started': self.started, 'max_records': self.max_records, 'max_points': self.max_points}

    def filtered(self, protocol='', operation='', outcome='', query=''):
        if protocol and protocol not in ('OPC UA', 'Modbus TCP', 'Modbus RTU'):
            raise ValueError('Unknown protocol filter')
        if operation and operation not in ('read', 'write'):
            raise ValueError('Unknown operation filter')
        if outcome and outcome not in (*OUTCOMES, 'PARTIAL', 'RESPONSE_ERROR'):
            raise ValueError('Unknown outcome filter')
        query = query.casefold()[:200]
        return [r for r in reversed(self.rows) if (not protocol or r['protocol'] == protocol)
                and (not operation or r['operation'] == operation)
                and (not outcome or r['state'] == outcome or r['counts'].get(outcome, 0))
                and (not query or query in (' '.join((r['id'], r['connection'], r['destination']))).casefold()
                     or any(query in p['tag'].casefold() for p in r['points']))]

    def find(self, ident):
        row = next((r for r in self.rows if r['id'] == ident), None)
        if row is None:
            raise ValueError('Transaction not found or evicted')
        return row


class Transaction:
    def __init__(self, store, row):
        self.store, self.row = store, row
        self.start = time.monotonic()
        self.by_id = {p['id']: p for p in row['points']}
        self.finished = False

    def batch(self, tags):
        batch = {'id': uuid.uuid4().hex, 'request_time': time.time(), 'response_time': None,
                 'duration_ms': None, 'points': len(tags), 'response_count': None, 'error': '',
                 '_start': time.monotonic()}
        self.row['batches'].append(batch)
        if self.store.performance is not None:self.store.performance.request()
        for tag in tags:
            self.by_id[str(tag['id'])].update(outcome='UNKNOWN', batch_id=batch['id'])
        return batch

    def response(self, batch, count):
        if batch['response_time'] is None and self.store.performance is not None:self.store.performance.response()
        batch.update(response_time=time.time(), response_count=count)
        batch['duration_ms'] = (time.monotonic() - batch['_start']) * 1000

    def point(self, tag, outcome, status):
        self.by_id[str(tag['id'])].update(outcome=outcome, status=str(status)[:250])

    def finish(self, error=None):
        if self.finished:
            return
        self.finished = True
        batch_counts={}
        for point in self.row['points']:
            if point['outcome']=='UNKNOWN' and not point['status']:point['status']='No confirmed outcome'
            if point['batch_id'] is not None:batch_counts.setdefault(point['batch_id'],Counter())[point['outcome']]+=1
        for batch in self.row['batches']:
            if batch['duration_ms'] is None:
                batch['duration_ms'] = (time.monotonic() - batch['_start']) * 1000
                batch['error'] = 'Cancelled' if isinstance(error, asyncio.CancelledError) else type(error).__name__ if error else ''
            batch.pop('_start', None)
            bc=batch_counts.get(batch['id'],Counter())
            batch['counts']={k:bc[k] for k in OUTCOMES}
            kinds=[k for k in OUTCOMES if bc[k]]
            batch['state']=kinds[0] if len(kinds)==1 else 'PARTIAL'
            if batch['error'] and batch['state']=='SUCCEEDED':batch['state']='RESPONSE_ERROR'
        counts = Counter(p['outcome'] for p in self.row['points'])
        counts = {key: counts[key] for key in OUTCOMES}
        counts['total'] = len(self.row['points'])
        counts['attempted'] = counts['total'] - counts['NOT_ATTEMPTED']
        kinds = [k for k in OUTCOMES if counts[k]]
        state = kinds[0] if len(kinds) == 1 else 'PARTIAL' if kinds else 'NOT_ATTEMPTED'
        if error and state=='SUCCEEDED':state='RESPONSE_ERROR'
        self.row['timed_out']=isinstance(error,TimeoutError) or type(error).__name__ in ('BadTimeout','BadRequestTimeout') or any(b.get('timed_out') for b in self.row['batches'])
        self.row['cancelled']=isinstance(error,asyncio.CancelledError)
        self.row.update(counts=counts, state=state, duration_ms=(time.monotonic() - self.start) * 1000,
                        response_time=max((b['response_time'] for b in self.row['batches'] if b['response_time'] is not None), default=None),
                        error=type(error).__name__ if error else '')
        self.store.retain(self.row)
        if self.store.performance is not None:self.store.performance.finished(self.row)


DEFAULT_STORE = TransactionStore()
STORE_CONTEXT = ContextVar('communication_store', default=DEFAULT_STORE)
CURRENT_TRANSACTION = ContextVar('communication_transaction', default=None)


def tracked(operation):
    def decorate(func):
        @wraps(func)
        async def wrapped(self, tags, *args, **kwargs):
            transaction = self.evidence.begin(self.config, operation, tags)
            token = CURRENT_TRANSACTION.set(transaction)
            try:
                if asyncio.current_task().cancelling():raise asyncio.CancelledError()
                async with self.evidence.performance.slot() if self.evidence.performance is not None else nullcontext():
                    result = await func(self, tags, *args, **kwargs)
            except BaseException as error:
                if not isinstance(error,asyncio.CancelledError) and asyncio.current_task().cancelling():
                    cancellation=asyncio.CancelledError();transaction.finish(cancellation);raise cancellation from error
                transaction.finish(error)
                raise
            else:
                if asyncio.current_task().cancelling():
                    cancellation=asyncio.CancelledError();transaction.finish(cancellation);raise cancellation
                transaction.finish()
                return result
            finally:
                if operation=='write':self.last_write_result=transaction.row
                CURRENT_TRANSACTION.reset(token)
        return wrapped
    return decorate
