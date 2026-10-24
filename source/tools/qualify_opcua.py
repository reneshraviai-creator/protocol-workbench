"""Isolated loopback qualification. No workspace file or external endpoint is used."""
import argparse,asyncio,json,platform,socket,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from model import default_project
from engine import Engine
from live_monitor import MonitorHub
from drivers import OPC

async def wait(check,seconds=120):
 async with asyncio.timeout(seconds):
  while not check():await asyncio.sleep(.05)

async def qualify(count,cycles,output):
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 tags=[{'id':f'p{i}','tag':f'Point{i:05}','dtype':'Float','nodeid':f'ns=2;s=Point{i:05}','initial_value':0} for i in range(count)]
 c={'id':'qualification','name':'Loopback qualification','protocol':'opcua','url':f'opc.tcp://127.0.0.1:{port}','security':'None','timeout':5,'retry_seconds':.5,'tags':tags}
 p=default_project();p['connections']=[c];p['jobs']=[{'id':'server','name':'Qualification server','kind':'server','connection':c['id'],'interval':.5,'dynamic':False}]
 e=Engine(p);hub=MonitorHub();writer=None;start=time.monotonic();report={'platform':platform.platform(),'python':platform.python_version(),'points':count,'requested_cycles':cycles,'scope':'Loopback only, static server, explicit writes, no security','checks':[],'state':'RUNNING'}
 def mark(name,**extra):report['checks'].append({'check':name,'elapsed_seconds':round(time.monotonic()-start,3),**extra});print(name,flush=True)
 try:
  await e.start('server');await wait(lambda:e.states['server']['state'] in ('RUNNING','ERROR'))
  if e.states['server']['state']=='ERROR':raise RuntimeError(e.states['server'])
  mark('server_ready')
  m=hub.start(c,[t['id'] for t in tags],{'publishing_ms':250,'sampling_ms':250,'queue_size':10})
  await wait(lambda:len(m.values)==count and m.state=='RUNNING');mark('all_initial_values_received',events=m.events,history_depth=m.depth)
  writer=OPC(c);await writer.connect();await writer.prepare(tags,write=True)
  for cycle in range(1,cycles+1):
   expected={t['id']:float(cycle*10000+i) for i,t in enumerate(tags)};tick=time.monotonic()
   await writer.write(tags,expected)
   await wait(lambda:all(m.values.get(k,{}).get('value')==v for k,v in expected.items()))
   mark('all_values_matched',cycle=cycle,write_to_all_observed_seconds=round(time.monotonic()-tick,3));await asyncio.sleep(1)
  await writer.close();writer=None;await e.stop_all();await wait(lambda:m.state=='RECONNECTING',30)
  assert m.snapshot()['rows'][0]['connected'] is False;mark('disconnect_detected',retained_values=len(m.values))
  await e.start('server');await wait(lambda:e.states['server']['state']=='RUNNING')
  await wait(lambda:m.state=='RUNNING' and len(m.values)==count and all(v['value']==0 for v in m.values.values()))
  mark('all_values_recovered_after_server_restart',reconnects=m.reconnects)
  assert len(m.snapshot(page=(count-1)//50)['rows'])==(count%50 or 50)
  assert m.snapshot(query=tags[-1]['tag'])['total']==1
  assert all(len(h)<=m.depth for h in m.history.values());mark('pagination_search_bounded_history_passed')
  report['state']='PASSED'
 except BaseException as error:
  report.update(state='FAILED',error=f'{type(error).__name__}: {error}');raise
 finally:
  if writer:
   try:await writer.close()
   except Exception:pass
  await hub.stop_all();await e.stop_all();report['duration_seconds']=round(time.monotonic()-start,3)
  output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2))
 return report

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--points',type=int,default=6500);parser.add_argument('--cycles',type=int,default=5);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 if not 1<=args.points<=20000 or not 1<=args.cycles<=10000:parser.error('points 1..20000; cycles 1..10000')
 print(json.dumps(asyncio.run(qualify(args.points,args.cycles,args.output)),indent=2))
