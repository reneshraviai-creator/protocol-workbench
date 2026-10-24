"""Durable scenario catalogue and bounded, cancellable test execution."""
import asyncio
import copy
import json
import math
import platform
import sys
from importlib.metadata import version
from scenario_automation import fingerprint,validate_policy,allowed,check
import os
import sqlite3
import time
import uuid
from pathlib import Path
from drivers import adapter
from engine import Engine
from resources import ResourceRegistry,scenario_plan
from model import validate_project, cast, view_value


_RESULTS=object()

def ident(): return uuid.uuid4().hex


class Catalogue:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS watchlists (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS templates (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS scenarios (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS scenario_versions (scenario TEXT, revision INTEGER, fingerprint TEXT, body TEXT, created REAL, PRIMARY KEY(scenario,revision))')
            for key, raw in db.execute('SELECT id, body FROM runs').fetchall():
                row = json.loads(raw)
                if row['state'] in ('RUNNING', 'QUEUED', 'STOPPING'):
                    row.update(state='ERROR', error='Application stopped before run completed; no automatic replay', finished=time.time())
                    db.execute('UPDATE runs SET body=? WHERE id=?', (json.dumps(row), key))

        if os.name != 'nt': self.path.chmod(0o600)

    def db(self): return sqlite3.connect(self.path)
    def all(self, table):
        assert table in ('scenarios', 'runs', 'templates', 'watchlists')
        with self.db() as db:
            rows = [json.loads(x[0]) for x in db.execute(f'SELECT body FROM {table}')]
        return sorted(rows, key=lambda r: r.get('updated', r.get('started', 0)), reverse=True)
    def get(self, table, key):
        assert table in ('scenarios', 'runs', 'templates', 'watchlists')
        with self.db() as db:
            row = db.execute(f'SELECT body FROM {table} WHERE id=?', (key,)).fetchone()
        if not row: raise ValueError('Scenario or run not found')
        return json.loads(row[0])
    def put(self, table, row):
        assert table in ('scenarios', 'runs', 'templates', 'watchlists')
        if table=='scenarios':row={k:v for k,v in row.items() if k not in ('revision','fingerprint')}
        with self.db() as db:
            db.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?)', (row['id'], json.dumps(row, allow_nan=False)))
            if table=='scenarios':self._version(db,row)

    def _version(self,db,row):
        digest=fingerprint(row)
        last=db.execute('SELECT revision,fingerprint FROM scenario_versions WHERE scenario=? ORDER BY revision DESC LIMIT 1',(row['id'],)).fetchone()
        if last and last[1]==digest:return {'revision':last[0],'fingerprint':digest}
        revision=last[0]+1 if last else 1
        db.execute('INSERT INTO scenario_versions VALUES (?,?,?,?,?)',(row['id'],revision,digest,json.dumps(row,allow_nan=False),time.time()))
        return {'revision':revision,'fingerprint':digest}
    def revision(self,row):
        with self.db() as db:return self._version(db,row)
    def versions(self,key):
        self.revision(self.get('scenarios',key))
        with self.db() as db:return [{'revision':r,'fingerprint':h,'created':t,'name':json.loads(b)['name']} for r,h,t,b in db.execute('SELECT revision,fingerprint,created,body FROM scenario_versions WHERE scenario=? ORDER BY revision DESC',(key,))]
    def historical(self,key,revision):
        with self.db() as db:row=db.execute('SELECT body FROM scenario_versions WHERE scenario=? AND revision=?',(key,int(revision))).fetchone()
        if not row:raise ValueError('Scenario revision not found')
        return json.loads(row[0])


def selected_tags(step, connection):
    scope = step.get('scope', 'single')
    tags = connection.get('tags', [])
    if scope == 'single': chosen = [step.get('tag')]
    elif scope == 'all': chosen = [t['id'] for t in tags]
    elif scope == 'selected': chosen = step.get('tags', [])
    else: raise ValueError('Tag scope must be single, selected or all')
    if not isinstance(chosen, list) or not chosen or len(set(chosen)) != len(chosen):
        raise ValueError('Select at least one unique tag')
    available = {t['id'] for t in tags}
    if any(k not in available for k in chosen): raise ValueError('Selection contains an unknown tag')
    lookup = {t['id']: t for t in tags}
    return [lookup[k] for k in chosen]


def comparison(actual, step):
    operator = step['operator']
    if operator == 'equals':
        expected = step['expected']
        from datetime import datetime
        if isinstance(actual,datetime):return cast(actual,'DateTime')==cast(expected,'DateTime')
        if isinstance(actual, bool):
            return (type(expected) is bool and actual == expected) or str(actual).lower() == str(expected).lower()
        if isinstance(actual, (int, float)):
            from decimal import Decimal
            return Decimal(str(actual)) == Decimal(str(expected))
        return actual == expected
    if operator == 'within': return abs(float(actual) - float(step['expected'])) <= float(step['tolerance'])
    return float(step['minimum']) <= float(actual) <= float(step['maximum'])


def validate_scenario(body):
    s = copy.deepcopy(body)
    if not isinstance(s.get('name'), str) or not s['name'].strip(): raise ValueError('Scenario needs a name')
    p = validate_project(s['project'])
    if not isinstance(s.get('steps'), list) or not 1 <= len(s['steps']) <= 6500: raise ValueError('Add 1..6500 test steps')
    connections = {c['id']: c for c in p['connections']}
    jobs = {j['id'] for j in p['jobs']}
    variables = set()
    for index,step in enumerate(s['steps']):
        action = step.get('action')
        validate_policy(step,index,s['steps'][:index])
        if action not in ('read', 'write', 'assert', 'wait', 'start_job', 'stop_job', 'verify_path', 'check'): raise ValueError('Unsupported step action')
        timeout = float(step.get('timeout', 15))
        if not math.isfinite(timeout) or not .1 <= timeout <= 300: raise ValueError('Step timeout must be 0.1..300 seconds')
        if action=='check':pass
        elif action=='verify_path':
            from signal_path import definition
            definition(step,p)
        elif action in ('read', 'write'):
            c = connections.get(step.get('connection'))
            if not c: raise ValueError('Select a connection')
            tags = selected_tags(step, c)
            if action == 'write':
                for tag in tags:
                    cast(step['value'], tag['dtype'])
                    if c['protocol'] == 'modbus' and tag.get('area') in ('input', 'discrete'): raise ValueError('Cannot write a read-only Modbus area')
            else:
                name = step.get('variable', '')
                if not name or not name.isidentifier(): raise ValueError('Read result needs a variable name (letters, digits, underscore)')
                variables.add(name)
        elif action == 'assert':
            if step.get('variable') not in variables: raise ValueError('Assertion must refer to a preceding read variable')
            if step.get('operator') not in ('equals', 'within', 'between'): raise ValueError('Unsupported assertion')
            if step['operator'] in ('within', 'between'):
                fields = ('expected', 'tolerance') if step['operator'] == 'within' else ('minimum', 'maximum')
                if any(not math.isfinite(float(step[k])) for k in fields): raise ValueError('Assertion limits must be finite')
                if step['operator'] == 'within' and float(step['tolerance']) < 0: raise ValueError('Tolerance cannot be negative')
                if step['operator'] == 'between' and float(step['minimum']) > float(step['maximum']): raise ValueError('Minimum exceeds maximum')
            elif 'expected' not in step: raise ValueError('Expected value is required')
        elif action == 'wait':
            seconds = float(step.get('seconds', 1))
            if not math.isfinite(seconds) or not 0 <= seconds <= 300: raise ValueError('Wait must be 0..300 seconds')
            if seconds >= timeout: raise ValueError('Wait duration must be below step timeout')
        elif step.get('job') not in jobs: raise ValueError('Select a job')
    for step in s['steps']:
        if step['action']=='verify_path':
            from resources import empty,add_writes,job_plan,conflict
            from signal_path import definition
            c,_,tag,_,_,_=definition(step,p);plan=empty();add_writes(plan,c,[tag])
            for other in (x for x in s['steps'] if x['action']=='start_job'):
                if other['action']=='start_job':
                    job=next(j for j in p['jobs'] if j['id']==other['job'])
                    if conflict(plan,job_plan(p,job)):raise ValueError('Signal-path probe conflicts with a test-owned source writer; use a static source server')
    return s


class Runner:
    def __init__(self, store, registry=None): self.store = store; self.task = None; self.current = None; self.tasks = {}; self.registry=registry if registry is not None else ResourceRegistry()
    def active(self): return any(not task.done() for task in self.tasks.values())
    async def start(self, scenario_id):
        if sum(not task.done() for task in self.tasks.values())>=8:raise ValueError('Eight concurrent runs supported; stop one before starting another')
        s = validate_scenario(self.store.get('scenarios', scenario_id))
        from event_log import CONTEXT as EVENT_LOG
        if EVENT_LOG.get():EVENT_LOG.get().learn(s['project'])
        row = {'id': ident(), 'scenario': scenario_id, 'name': s['name'], 'state': 'QUEUED', 'started': time.time(), 'steps': [], 'total_steps': len(s['steps']), 'snapshot': s, **self.store.revision(s), 'environment':{'python':sys.version.split()[0],'platform':platform.system(),'asyncua':version('asyncua'),'pymodbus':version('pymodbus')},'logs':[]}
        self.registry.acquire('run:'+row['id'],s['name'],scenario_plan(s),'scenario run')
        try:
            self.store.put('runs', row); self.current = row['id']
            self.task = asyncio.create_task(self.execute(row)); self.tasks[row['id']]=self.task
        except BaseException:
            self.registry.release('run:'+row['id']);raise
        return row['id']
    async def stop(self, run_id=None):
        keys=[run_id] if run_id else list(self.tasks)
        if run_id and run_id not in self.tasks:raise ValueError('Active run not found')
        for key in keys:
            task=self.tasks[key]
            if not task.done():
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
                row=self.store.get('runs',key)
                if row['state'] in ('QUEUED','RUNNING','STOPPING'):
                    row.update(state='CANCELLED',finished=time.time(),skipped=len(row['snapshot']['steps'])-len(row['steps']));self.store.put('runs',row)
            self.registry.release('run:'+key)
    async def execute(self, row):
        s = row['snapshot']; engine = Engine(s['project']); variables = {_RESULTS:row['steps']}
        row['state'] = 'RUNNING'; self.store.put('runs', row)
        try:
            for n, step in enumerate(s['steps']):
                result = {'index': n + 1, 'action': step['action'], 'label': step.get('label') or step['action'], 'state': 'RUNNING', 'started': time.time()}
                row['steps'].append(result); self.store.put('runs', row)
                started=time.monotonic()
                try:
                    if not allowed(step,row['steps']):
                        result.update(state='SKIPPED',reason='Prior-step condition did not match')
                        continue
                    result['attempts']=[]
                    async with asyncio.timeout(float(step.get('timeout',15))):
                        for attempt in range(1,step.get('retry',{}).get('attempts',1)+1):
                            attempt_started=time.monotonic()
                            try:
                                await self.step(step,s['project'],engine,variables,result)
                                result['state']='PASSED'
                            except asyncio.CancelledError:
                                result['state']='CANCELLED';raise
                            except AssertionError as error:result.update(state='FAILED',error=str(error))
                            except Exception as error:result.update(state='ERROR',error=f'{type(error).__name__}: {error}')
                            finally:
                                evidence={k:copy.deepcopy(v) for k,v in result.items() if k not in ('attempts','started')}
                                evidence.update(attempt=attempt,duration=time.monotonic()-attempt_started)
                                result['attempts'].append(evidence)
                            if result['state']=='PASSED':break
                            if attempt<step.get('retry',{}).get('attempts',1):
                                await asyncio.sleep(step.get('retry',{}).get('delay',0))
                                for field in list(result):
                                    if field not in ('index','action','label','state','started','attempts'):result.pop(field)
                    if result['state']=='FAILED' and not step.get('continue_on_failure',False):raise AssertionError(result['error'])
                    if result['state']=='ERROR' and not step.get('continue_on_failure',False):raise RuntimeError(result['error'])
                except AssertionError as error:
                    result.update(state='FAILED',error=str(error));raise
                except asyncio.CancelledError:
                    result.update(state='CANCELLED');raise
                except Exception as error:
                    result.update(state='ERROR',error=f'{type(error).__name__}: {error}')
                    if not step.get('continue_on_failure',False):raise
                finally:
                    result['duration']=time.monotonic()-started
                    row['logs'].append({'step':n+1,'state':result['state'],'time':time.time(),'attempts':len(result.get('attempts',[])),'error':result.get('error',''),'reason':result.get('reason','')})
                    from event_log import emit
                    emit('verification','INFO' if result['state'] in ('PASSED','SKIPPED') else 'WARNING','scenario_step',run=row['id'],step=n+1,state=result['state'],attempts=len(result.get('attempts',[])),error=result.get('error',''))
                    self.store.put('runs',row)
            row['state'] = 'ERROR' if any(x['state']=='ERROR' for x in row['steps']) else 'FAILED' if any(x['state']=='FAILED' for x in row['steps']) else 'PASSED'
        except asyncio.CancelledError: row['state'] = 'CANCELLED'
        except AssertionError as error: row.update(state='FAILED', error=str(error))
        except Exception as error: row.update(state='ERROR', error=f'{type(error).__name__}: {error}')
        finally:
            try: await engine.stop_all()
            except Exception as error: row.update(state='ERROR', error=f'Cleanup failed: {error}')
            row['finished'] = time.time(); row['skipped'] = len(s['steps']) - len(row['steps']) + sum(x['state']=='SKIPPED' for x in row['steps'])
            try:self.store.put('runs', row)
            finally:self.registry.release('run:'+row['id'])
    async def step(self, step, project, engine, variables, result):
        action = step['action']
        if action=='check':check(step,variables[_RESULTS],result)
        elif action=='verify_path':
            from signal_path import execute
            await execute(step,project,result)
        elif action == 'wait': await asyncio.sleep(float(step.get('seconds', 1)))
        elif action == 'stop_job': await engine.stop(step['job'])
        elif action == 'start_job':
            await engine.start(step['job'])
            while True:
                state = engine.states[step['job']]
                if state['state'] in ('RUNNING', 'OVERRUN'): break
                if state['state'] in ('ERROR', 'STOPPED'): raise RuntimeError(state.get('error') or 'Job stopped before becoming ready')
                await asyncio.sleep(.05)
        elif action in ('read', 'write'):
            c = next(c for c in project['connections'] if c['id'] == step['connection'])
            tags = selected_tags(step, c)
            # Validate and cast every write before the first network write.
            values = {t['id']: cast(step['value'], t['dtype']) for t in tags} if action == 'write' else {}
            result['tags'] = [{'id': t['id'], 'tag': t['tag'], 'dtype': t['dtype'], 'state': 'SKIPPED'} for t in tags]
            result['total'] = len(tags)
            client = adapter(c)
            result['transactions']=[]
            try:
                await client.connect(); await client.prepare(tags, write=action == 'write')
                for start in range(0, len(tags), 50):
                    part = tags[start:start+50]
                    try:
                        if action == 'write': await client.write(part, {t['id']: values[t['id']] for t in part})
                        else: values.update(await client.read(part))
                    except asyncio.CancelledError:
                        for item in result['tags'][start:start+len(part)]: item.update(state='UNKNOWN', error='Operation interrupted; device outcome was not confirmed')
                        raise
                    except Exception as error:
                        for item in result['tags'][start:start+len(part)]: item.update(state='ERROR', error=f'{type(error).__name__}: {error}; device outcome not confirmed')
                        raise
                    finally:
                        transaction=getattr(client,'last_write_result',None) if action=='write' else next((r for r in reversed(client.evidence.rows) if r['id']==getattr(client,'last_read_transaction',None)),None)
                        if transaction:result['transactions'].append(copy.deepcopy(transaction))
                        for item in result['tags'][start:start+len(part)]:
                            ob=getattr(client,'last_observations',{}).get(item['id'],{})
                            item.update(observed_dtype=ob.get('datatype'),quality=ob.get('quality'),source_timestamp=ob.get('source_timestamp'),datatype_scope='OPC UA observed VariantType' if c['protocol']=='opcua' else 'Configured Modbus decoding; wire datatype/process quality unavailable')
                    for t, item in zip(part, result['tags'][start:start+len(part)]):
                        item.update(state='PASSED', **{('written' if action == 'write' else 'observed'): view_value(values[t['id']], t['dtype'])})
                    result['completed'] = start + len(part)
                    await asyncio.sleep(0)
                if action == 'read':
                    variables[step['variable']] = values[tags[0]['id']] if step.get('scope', 'single') == 'single' else {'__tag_values__': True, 'values': values, 'tags': tags}
                result['written' if action == 'write' else 'observed'] = view_value(values[tags[0]['id']], tags[0]['dtype']) if len(tags) == 1 else f'{len(tags)} tags'
            finally:
                result['passed_tags'] = sum(t['state'] == 'PASSED' for t in result['tags'])
                result['unknown_tags'] = sum(t['state'] == 'UNKNOWN' for t in result['tags'])
                result['error_tags'] = sum(t['state'] == 'ERROR' for t in result['tags'])
                result['skipped_tags'] = sum(t['state'] == 'SKIPPED' for t in result['tags'])
                await client.close()
        else:
            actual = variables[step['variable']]
            expectation = {k: v for k, v in step.items() if k in ('operator', 'expected', 'tolerance', 'minimum', 'maximum')}
            result['expectation'] = expectation
            if isinstance(actual, dict) and actual.get('__tag_values__'):
                result['tags'] = []
                for tag in actual['tags']:
                    value = actual['values'][tag['id']]
                    try:
                        passed = comparison(value, step)
                        item = {'id': tag['id'], 'tag': tag['tag'], 'dtype': tag['dtype'], 'observed': view_value(value, tag['dtype']), 'state': 'PASSED' if passed else 'FAILED'}
                    except Exception as error:
                        item = {'id': tag['id'], 'tag': tag['tag'], 'state': 'ERROR', 'error': str(error)}
                    result['tags'].append(item)
                result.update(total=len(result['tags']), passed_tags=sum(t['state']=='PASSED' for t in result['tags']), failed_tags=sum(t['state']=='FAILED' for t in result['tags']), error_tags=sum(t['state']=='ERROR' for t in result['tags']))
                result['observed'] = f"{result['passed_tags']}/{result['total']} tags passed"
                if result['error_tags']: raise ValueError(f"{result['error_tags']} tag comparisons could not be evaluated")
                if result['failed_tags']: raise AssertionError(f"{result['failed_tags']}/{result['total']} tags failed verification")
            else:
                result['observed'] = str(actual)
                if not comparison(actual, step): raise AssertionError(f"{step['variable']}: observed {actual}; expected {expectation}")
