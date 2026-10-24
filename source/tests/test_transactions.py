import asyncio
import csv
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from asyncua import ua
from drivers import OPC,Modbus
from transactions import TransactionStore,STORE_CONTEXT


def tags(n=4):
    return [{'id':f't{i}','tag':f'Tag{i}','dtype':'Float','nodeid':f'ns=2;s=T{i}','area':'holding','address':i*2,'order':'ABCD'} for i in range(n)]


def opc(statuses=None,error=None,n=4):
    c=OPC({'protocol':'opcua','id':'plc','url':'opc.tcp://user:secret@127.0.0.1:4840/private?password=hidden'})
    c.evidence=TransactionStore();ts=tags(n);c.nodes={t['id']:t['nodeid'] for t in ts};c.types={t['id']:ua.VariantType.Float for t in ts}
    class Fake:
        async def write_values(self,*args,**kw):
            assert kw['raise_on_partial_error'] is False
            if error:raise error
            return statuses if statuses is not None else [ua.StatusCode() for _ in args[0]]
    c.client=Fake()
    return c,ts


def test_all_partial_statuses_inspected_and_later_batch_unsent():
    async def run():
        statuses=[ua.StatusCode(ua.StatusCodes.BadNotWritable),ua.StatusCode(),ua.StatusCode(ua.StatusCodes.BadTypeMismatch)]
        c,ts=opc(statuses,n=5);c.write_batch=3
        with pytest.raises(ValueError,match='Partial write'):await c.write(ts,{t['id']:1 for t in ts})
        row=c.evidence.rows[-1]
        assert [p['outcome'] for p in row['points']]==['FAILED','SUCCEEDED','FAILED','NOT_ATTEMPTED','NOT_ATTEMPTED']
        assert row['counts']['attempted']==3 and len(row['batches'])==1 and row['state']=='PARTIAL'
        assert len(row['batches'][0]['response_statuses'])==3
    asyncio.run(run())


@pytest.mark.parametrize('length',[0,2,5])
def test_opc_response_length_preserves_known_statuses(length):
    async def run():
        c,ts=opc([ua.StatusCode() for _ in range(length)])
        with pytest.raises(ValueError,match='response length'):await c.write(ts,{t['id']:1 for t in ts})
        row=c.evidence.rows[-1]
        assert row['counts']['SUCCEEDED']==min(length,4)
        assert row['counts']['UNKNOWN']==max(0,4-length)
        assert row['batches'][0]['response_count']==length
        assert len(row['batches'][0]['response_statuses'])==length
    asyncio.run(run())


@pytest.mark.parametrize('error',[TimeoutError(),ConnectionError(),asyncio.CancelledError()])
def test_uncertain_dispatched_batch_and_unsent_remainder(error):
    async def run():
        c,ts=opc(error=error);c.write_batch=2
        with pytest.raises(type(error)):await c.write(ts,{t['id']:1 for t in ts})
        row=c.evidence.rows[-1]
        assert row['counts']['UNKNOWN']==2 and row['counts']['NOT_ATTEMPTED']==2
        assert row['response_time'] is None and row['error']==type(error).__name__
        assert row['timed_out']==isinstance(error,TimeoutError)
    asyncio.run(run())


def test_ua_service_rejection_is_failure():
    async def run():
        c,ts=opc(error=ua.UaStatusCodeError(ua.StatusCodes.BadUserAccessDenied))
        with pytest.raises(ua.UaStatusCodeError):await c.write(ts,{t['id']:1 for t in ts})
        assert c.evidence.rows[-1]['counts']['FAILED']==4
        assert c.evidence.summary()['responses']==1
    asyncio.run(run())


def test_preflight_invalid_value_never_dispatched():
    async def run():
        c,ts=opc()
        with pytest.raises(ValueError):await c.write(ts,{t['id']:'NaN' for t in ts})
        assert c.evidence.rows[-1]['counts']['NOT_ATTEMPTED']==4
        assert c.evidence.summary()['requests']==0
    asyncio.run(run())


def test_success_counts_batch_ids_stats_and_redaction():
    async def run():
        c,ts=opc();c.write_batch=2
        assert await c.write(ts,{t['id']:1 for t in ts})==4
        row=c.evidence.rows[-1];s=c.evidence.summary()
        assert len({b['id'] for b in row['batches']})==2
        assert s['SUCCEEDED']==4 and s['success_percentage']==100 and s['requests']==s['responses']==2
        assert s['minimum_latency_ms']<=s['average_latency_ms']<=s['maximum_latency_ms']
        assert row['response_time']>=row['request_time']
        raw=json.dumps(row);assert 'secret' not in raw and 'password' not in raw and 'private?' not in raw
    asyncio.run(run())


def test_read_statuses_after_bad_quality_inspected():
    async def run():
        c,ts=opc();good=ua.DataValue(ua.Variant(1.0,ua.VariantType.Float));bad=ua.DataValue(ua.Variant(0.0),StatusCode=ua.StatusCode(ua.StatusCodes.BadNotReadable))
        async def read_attributes(*a):return [bad,good,bad,good]
        c.client=SimpleNamespace(read_attributes=read_attributes)
        with pytest.raises(ValueError):await c.read(ts)
        assert [p['outcome'] for p in c.evidence.rows[-1]['points']]==['FAILED','SUCCEEDED','FAILED','SUCCEEDED']
    asyncio.run(run())


def test_bounded_history_counters_and_filters():
    store=TransactionStore(max_records=2,max_points=5)
    for i in range(4):
        tx=store.begin({'protocol':'modbus','transport':'rtu','id':'serial','serial_port':'/dev/test'},'write',tags(2));b=tx.batch(tags(2));tx.response(b,1)
        for t in tags(2):tx.point(t,'SUCCEEDED','Good')
        tx.finish()
    assert len(store.rows)==2 and store.summary()['total']==8 and store.evicted==2
    assert len(store.filtered(protocol='Modbus RTU',query='tag1'))==2
    assert not store.filtered(operation='read')
    with pytest.raises(ValueError):store.filtered(outcome='bad')
    with pytest.raises(ValueError):store.find('absent')


@pytest.mark.parametrize('operation',['read','write'])
@pytest.mark.parametrize('kind',['good','exception','deferred','gateway-timeout','short','wrong-ack','no-response'])
def test_modbus_response_evidence(operation,kind):
    async def run():
        c=Modbus({'protocol':'modbus','host':'127.0.0.1'});c.evidence=TransactionStore();ts=tags(2)
        response=SimpleNamespace(isError=lambda:kind in ('exception','deferred','gateway-timeout','no-response'),registers=[0]* (1 if kind=='short' else 4),address=99 if kind=='wrong-ack' else 0,count=4)
        if kind in ('exception','deferred','gateway-timeout'):response.exception_code={'exception':2,'deferred':5,'gateway-timeout':11}[kind]
        async def call(*a,**kw):return response
        c.client=SimpleNamespace(read_holding_registers=call,read_input_registers=call,read_coils=call,read_discrete_inputs=call,write_coils=call,write_registers=call)
        invalid=kind in ('exception','deferred','gateway-timeout','no-response') or operation=='read' and kind=='short' or operation=='write' and kind=='wrong-ack'
        try:
            if invalid:
                with pytest.raises((ValueError,ConnectionError)):await (c.read(ts) if operation=='read' else c.write(ts,{t['id']:2 for t in ts}))
            else:await (c.read(ts) if operation=='read' else c.write(ts,{t['id']:2 for t in ts}))
            row=c.evidence.rows[-1]
            outcome='FAILED' if kind=='exception' else 'UNKNOWN' if invalid else 'SUCCEEDED'
            assert row['counts'][outcome]==2
            assert len(row['batches'])==1
        finally:pass
    asyncio.run(run())


def test_api_auth_paging_filter_export_and_context(tmp_path,monkeypatch):
    async def run():
        import server
        from model import default_project
        from aiohttp.test_utils import TestClient,TestServer
        monkeypatch.setattr(server,'DATA',tmp_path);monkeypatch.setattr(server,'PROJECT',tmp_path/'project.json')
        app=server.create_app(default_project());store=app[server.TRANSACTIONS_KEY]
        tx=store.begin({'protocol':'opcua','url':'opc.tcp://localhost:1','id':'c'},'write',tags(55));tx.row['points'][0]['tag']='=malicious';tx.finish()
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/api/transactions')).status==403
            init=await(await client.get('/api/bootstrap')).json();h={'X-Workbench-Token':init['token']}
            r=await(await client.get('/api/transactions?operation=write',headers=h)).json();assert r['total']==1 and 'points' not in r['rows'][0]
            detail=await(await client.get('/api/transactions/'+tx.row['id']+'?page=1',headers=h)).json();assert len(detail['points'])==5
            assert (await client.get('/api/transactions?outcome=nonsense',headers=h)).status==400
            assert (await client.get('/api/transactions?page=-1',headers=h)).status==400
            assert (await client.get('/api/transactions/absent',headers=h)).status==400
            data=await(await client.get('/api/transactions/export?format=json',headers=h)).json();assert len(data['transactions'][0]['points'])==55
            text=await(await client.get('/api/transactions/export?format=csv',headers=h)).text();rows=list(csv.reader(io.StringIO(text)));assert rows[1][11]=="'=malicious"
            assert len(rows)==56
            assert (await client.get('/api/transactions/export?format=pdf',headers=h)).status==400
        assert STORE_CONTEXT.get() is not store
    asyncio.run(run())


def test_large_sparse_batches_are_retained_with_point_results():
    store=TransactionStore();ts=tags(6500);tx=store.begin({'protocol':'modbus','host':'localhost'},'write',ts)
    for tag in ts:
        batch=tx.batch([tag]);tx.response(batch,1);tx.point(tag,'SUCCEEDED','Good')
    tx.finish()
    assert tx.row['counts']['SUCCEEDED']==6500
    assert all(b['counts']['SUCCEEDED']==1 for b in tx.row['batches'])


def test_concurrent_tasks_keep_transaction_context_isolated():
    async def run():
        c,ts=opc();c.write_batch=2
        async def write_values(nodes,*a,**kw):await asyncio.sleep(.001);return [ua.StatusCode() for _ in nodes]
        c.client=SimpleNamespace(write_values=write_values)
        await asyncio.gather(c.write(ts,{t['id']:1 for t in ts}),c.write(ts,{t['id']:2 for t in ts}))
        assert len(c.evidence.rows)==2 and c.evidence.summary()['SUCCEEDED']==8
        ids=[b['id'] for row in c.evidence.rows for b in row['batches']]
        assert len(ids)==len(set(ids))==4
    asyncio.run(run())
