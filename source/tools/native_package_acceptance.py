"""Audit a published package on a disposable native host, never a saved endpoint."""
import argparse,hashlib,json,os,platform,socket,subprocess,sys,time,urllib.request,zipfile
from pathlib import Path,PurePosixPath


def extract_verified(archive,expected,destination):
 if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:raise ValueError('Download SHA-256 mismatch')
 with zipfile.ZipFile(archive) as z:
  if z.testzip():raise ValueError('ZIP CRC failure')
  names=z.namelist()
  if len(set(n.lower() for n in names))!=len(names):raise ValueError('Duplicate archive paths')
  for entry in z.infolist():
   p=PurePosixPath(entry.filename)
   if p.is_absolute() or '..' in p.parts or '\\' in entry.filename or (entry.external_attr>>16)&0o170000==0o120000:raise ValueError('Unsafe archive path')
  manifests=[n for n in names if n.endswith('/SHA256SUMS.txt')]
  if len(manifests)!=1:raise ValueError('Expected one package manifest')
  parent=manifests[0].rsplit('/',1)[0];prefix=parent+'/'
  if any(not n.startswith(prefix) for n in names):raise ValueError('Files outside package root')
  listed=set()
  for line in z.read(manifests[0]).decode().splitlines():
   digest,name=line.split('  ',1)
   if name in listed or hashlib.sha256(z.read(prefix+name)).hexdigest()!=digest:raise ValueError('Internal manifest mismatch')
   listed.add(name)
  actual={n[len(prefix):] for n in names if not n.endswith('/') and n!=manifests[0]}
  if listed!=actual:raise ValueError('Incomplete manifest coverage')
  if destination.exists():raise ValueError('Extraction destination already exists')
  z.extractall(destination)
  return destination/parent


def audit(target,url,digest,workspace,output):
 workspace=workspace.resolve();output=output.resolve();workspace.mkdir(parents=True,exist_ok=True);output.mkdir(parents=True,exist_ok=True)
 prefix=workspace/'installation'
 if prefix.exists():raise ValueError('Use a fresh disposable workspace')
 report={'target':target,'platform':sys.platform,'machine':platform.machine(),'runner_arch':os.environ.get('RUNNER_ARCH','local'),'package_sha256':digest,'checks':[],'status':'RUNNING','scope':'Published archive lifecycle and localhost protocols only; no physical equipment or antivirus approval'}
 setup=python=None
 def run(name,args,timeout=600):
  environment=os.environ.copy()
  # PowerShell 7's module path is incompatible with child Windows PowerShell 5.1.
  # Let the latter construct its standard module path, as in Explorer/cmd launches.
  if sys.platform=='win32':
   environment={key:value for key,value in environment.items() if key.casefold()!='psmodulepath'}
  result=subprocess.run([str(x) for x in args],env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
  print('CHECK:',name,'exit',result.returncode,flush=True)
  print(result.stdout[-14000:],flush=True)
  if result.stderr:print(result.stderr[-6000:],flush=True)
  report['checks'].append({'name':name,'exit_code':result.returncode})
  if result.returncode:raise RuntimeError(name+' failed')
 def control(name,action,*extra):run(name,[python,setup,action,'--prefix',prefix,*extra])
 try:
  archive=workspace/'download.zip'
  with urllib.request.urlopen(url,timeout=90) as response:archive.write_bytes(response.read())
  package=extract_verified(archive,digest,workspace/'extracted');report['checks'].append({'name':'download/CRC/complete internal manifest','exit_code':0})
  setup=package/'setup.py';python=package/'runtime/python.exe' if sys.platform=='win32' else Path(sys.executable)
  if sys.platform=='win32':
   for name,action in [('Windows PowerShell package preflight','check-package'),('Windows bundled runtime imports','check-runtime')]:run(name,['cmd.exe','/D','/C','call',package/'Run Workbench.bat',action])
  with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
  control('fresh installation with isolated protocol self-tests','install','--port',str(port),*(['--offline'] if sys.platform=='win32' else []))
  control('installed runtime and local OPC UA/Modbus validation','validate')
  control('background app start','start','--no-ui')
  with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/api/bootstrap',timeout=10) as r:bootstrap=json.load(r)
  if bootstrap.get('version')!='2.0.0-preview.14':raise ValueError('Wrong installed application version')
  report['checks'].append({'name':'installed GUI bootstrap version','exit_code':0})
  control('background app stop','stop')
  first=json.loads((prefix/'install.json').read_text())
  marker=prefix/'data/audit-retention-marker.txt';marker.write_text('before-upgrade')
  control('upgrade with data backup','install','--port',str(port),*(['--offline'] if sys.platform=='win32' else []))
  upgraded=json.loads((prefix/'install.json').read_text())
  if upgraded['previous_release']!=first['release'] or upgraded['port']!=port or marker.read_text()!='before-upgrade':raise ValueError('Upgrade retention failed')
  marker.write_text('after-upgrade')
  control('rollback release and matching data','rollback','--confirm-data-restore')
  rolled=json.loads((prefix/'install.json').read_text())
  if rolled['release']!=first['release'] or marker.read_text()!='before-upgrade':raise ValueError('Rollback retention failed')
  if (prefix/rolled['rollback_saved_data']/'audit-retention-marker.txt').read_text()!='after-upgrade':raise ValueError('Rollback current-data backup failed')
  control('rolled-back application start','start','--no-ui');control('rolled-back application stop','stop')
  control('uninstall program while retaining data','uninstall')
  if (prefix/'install.json').exists() or (prefix/'versions').exists() or marker.read_text()!='before-upgrade':raise ValueError('Uninstall/data retention failed')
  report['checks'].append({'name':'upgrade/rollback/uninstall data retention','exit_code':0});report['status']='PASS'
 except Exception as error:
  report['status']='FAIL';report['error']=type(error).__name__+': '+str(error)
 finally:
  if setup and python and (prefix/'install.json').exists():
   try:control('failure cleanup stop','stop')
   except Exception:report['cleanup']='Review host process state; no forced deletion attempted'
  report['completed_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
  (output/'Native_Package_Acceptance.json').write_text(json.dumps(report,indent=2)+'\n')
 return report


if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--target',required=True);p.add_argument('--url',required=True);p.add_argument('--sha256',required=True);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 result=audit(a.target,a.url,a.sha256,a.workspace,a.output);print(json.dumps(result,indent=2));raise SystemExit(0 if result['status']=='PASS' else 1)
