"""Real isolated application-process crash/restart, never the user's app."""
import json,os,socket,subprocess,sys,time,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from model import default_project
from scenarios import Catalogue


def test_real_process_crash_and_restart_preserves_project_and_does_not_replay(tmp_path):
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 app=Path(__file__).resolve().parents[1]/'app/server.py';project=default_project();project['name']='Crash-recovery qualification';(tmp_path/'project.json').write_text(json.dumps(project))
 store=Catalogue(tmp_path/'scenarios.sqlite3');store.put('scenarios',{'id':'waiting','name':'Interrupted wait','project':project,'steps':[{'action':'wait','seconds':60,'timeout':90}]})
 base=f'http://127.0.0.1:{port}';proc=None
 def launch():return subprocess.Popen([sys.executable,str(app),'--port',str(port)],env={**os.environ,'WORKBENCH_DATA':str(tmp_path)},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 def request(path,token=None,body=None):
  headers={'X-Workbench-Token':token} if token else {}
  if body is not None:headers['Content-Type']='application/json'
  req=urllib.request.Request(base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
  return json.load(urllib.request.urlopen(req,timeout=2))
 def ready():
  deadline=time.monotonic()+15
  while time.monotonic()<deadline:
   try:return request('/api/bootstrap')['token']
   except Exception:
    assert proc.poll() is None,'Isolated application exited';time.sleep(.05)
  raise AssertionError('Isolated app did not become ready')
 try:
  proc=launch();token=ready();result=request('/api/scenarios/run',token,{'id':'waiting'});key=result['id']
  deadline=time.monotonic()+5
  while request('/api/runs/'+key,token)['state']!='RUNNING':
   assert time.monotonic()<deadline;time.sleep(.05)
  proc.kill();proc.wait(timeout=5);proc=launch();new_token=ready()
  assert new_token!=token
  row=request('/api/runs/'+key,new_token);assert row['state']=='ERROR' and 'no automatic replay' in row['error']
  assert request('/api/project',new_token)['name']==project['name']
  status=request('/api/status',new_token);assert status['resources']==[] and status['monitors']==[]
  assert len(request('/api/runs',new_token))==1
 finally:
  if proc and proc.poll() is None:
   (tmp_path/'stop.request').write_text('stop')
   try:proc.wait(timeout=10)
   except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
