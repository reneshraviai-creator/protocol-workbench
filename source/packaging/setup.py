"""Per-user preview installer. Never targets the existing development installation."""
import argparse,hashlib,json,os,platform,shutil,struct,subprocess,sys,time,uuid,zipfile,traceback,tempfile,re
from pathlib import Path
HERE=Path(__file__).resolve().parent
# Read the same literal version as the application; never execute the source.
import ast
_version_file=HERE/'payload/app/version.py'
if not _version_file.exists():_version_file=HERE.parent/'app/version.py'
VERSION=next(ast.literal_eval(n.value) for n in ast.parse(_version_file.read_text()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in n.targets))

def default_prefix():
 if sys.platform=='win32':return Path(os.environ['LOCALAPPDATA'])/'ProtocolWorkbenchCommunity'
 if sys.platform=='darwin':return Path.home()/'Library/Application Support/ProtocolWorkbenchCommunity'
 return Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'ProtocolWorkbenchCommunity'

def pe_arch(path):
 data=Path(path).read_bytes()
 if data[:2]!=b'MZ':raise ValueError('Invalid Windows runtime executable')
 offset=struct.unpack_from('<I',data,0x3c)[0]
 if data[offset:offset+4]!=b'PE\0\0':raise ValueError('Invalid Windows PE header')
 code=struct.unpack_from('<H',data,offset+4)[0]
 if code not in (0x14c,0x8664,0xaa64):raise ValueError('Unsupported Windows executable architecture')
 return {0x14c:'x86',0x8664:'x64',0xaa64:'arm64'}[code]

def validate_windows_runtime(bundled):
 actual=pe_arch(bundled/'python.exe')
 info=bundled/'BUILD_INFO.json'
 if info.exists() and json.loads(info.read_text())['architecture']!=actual:raise ValueError('Package architecture does not match bundled runtime')
 if pe_arch(sys.executable)!=actual:raise ValueError('Use this package’s bundled Python through Install.bat; external Python architecture does not match')
 return actual

def read_config(prefix):
 p=prefix/'install.json'
 if not p.exists():raise ValueError('Not installed. Run Install first.')
 return json.loads(p.read_text())

def invoke(prefix,action,no_ui=True):
 c=read_config(prefix);base,python=release_paths(prefix,c);env={**os.environ,'WORKBENCH_DATA':str(prefix/'data'),'WORKBENCH_PORT':str(c['port'])}
 args=[str(python),str(base/'app/launcher.py'),action]
 if no_ui:args.append('--no-ui')
 subprocess.run(args,env=env,check=True)

def verify_package(root):
 root=Path(root).resolve()
 manifest_path=root/'SHA256SUMS.txt'
 if not manifest_path.is_file():raise ValueError('Package manifest is missing. Extract All files into a permanent folder; check antivirus quarantine if files disappear.')
 manifest=set()
 for line in manifest_path.read_text(encoding='utf-8').splitlines():
  match=re.fullmatch(r'([0-9a-fA-F]{64})  (.+)',line)
  if not match:raise ValueError('Invalid package manifest entry')
  digest,name=match.groups();path=(root/name).resolve()
  if Path(name).is_absolute() or not path.is_relative_to(root) or name in manifest:raise ValueError('Invalid or duplicate package manifest path')
  manifest.add(name)
  if not path.is_file():raise ValueError('Package is incomplete: '+name+'. Use Extract All into a short folder such as C:\\PW, then run Install.bat outside the ZIP. Check Windows Security Protection history or antivirus quarantine if files disappear; do not disable protection.')
  if hashlib.sha256(path.read_bytes()).hexdigest().lower()!=digest.lower():raise ValueError('Package checksum mismatch: '+name+'. Obtain a complete verified replacement; do not run this copy.')
 required={p.relative_to(root).as_posix() for p in (root/'payload').rglob('*') if p.is_file()}
 if (root/'runtime').exists():required.update(p.relative_to(root).as_posix() for p in (root/'runtime').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo'))
 if (root/'setup.py').is_file():required.add('setup.py')
 if not required or not required.issubset(manifest):raise ValueError('Package manifest does not cover every payload/runtime file')
 return len(manifest)


def write_failure_log(error):
 # Local-only logs outside the downloaded/extracted package survive its removal.
 folder=(Path(os.environ.get('LOCALAPPDATA',tempfile.gettempdir()))/'ProtocolWorkbenchInstallerLogs') if os.name=='nt' else Path(tempfile.gettempdir())/'ProtocolWorkbenchInstallerLogs'
 try:
  folder.mkdir(parents=True,exist_ok=True)
  path=folder/('installer-error-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.log')
  path.write_text('Protocol Workbench '+VERSION+' installer/control failure\n'+''.join(traceback.format_exception(error)),encoding='utf-8')
  return path
 except OSError:return None

def install(prefix,port,offline=False):
 if not 1024<=port<=65535:raise ValueError('Port must be 1024..65535')
 if not (HERE/'payload/app/server.py').is_file():raise ValueError('Run setup from the extracted installation package')
 verify_package(HERE)
 prefix.mkdir(parents=True,exist_ok=True)
 if prefix.resolve()==HERE or prefix.resolve().is_relative_to(HERE/'payload'):raise ValueError('Choose an installation folder outside the extracted package')
 if not (prefix/'install.json').exists() and any(p.name!='installation.lock' for p in prefix.iterdir()):raise ValueError('Choose an empty dedicated installation folder; existing non-installation files will not be modified')
 if os.name!='nt':prefix.chmod(0o700)
 release='r-'+uuid.uuid4().hex[:8];stage=prefix/('s-'+uuid.uuid4().hex[:8]);old=read_config(prefix) if (prefix/'install.json').exists() else None
 try:
  shutil.copytree(HERE/'payload',stage)
  bundled=HERE/'runtime'
  if sys.platform=='win32' and bundled.exists():
   validate_windows_runtime(bundled)
   shutil.copytree(bundled,stage/'runtime');python=stage/'runtime/python.exe';relative='runtime/python.exe'
  else:
   if offline:raise ValueError('Offline install requires a bundled Windows runtime')
   if not (3,11)<=sys.version_info[:2]<(3,15):raise ValueError('Install Python 3.11–3.14 first')
   subprocess.run([sys.executable,'-m','venv',str(stage/'runtime')],check=True)
   relative='runtime/Scripts/python.exe' if os.name=='nt' else 'runtime/bin/python';python=stage/relative
   subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(stage/'requirements.txt')],check=True)
  subprocess.run([str(python),'-c','import asyncua,pymodbus,aiohttp,serial,cryptography,reportlab,PIL'],check=True)
  subprocess.run([str(python),str(stage/'app/diagnostics.py')],check=True)
  previous_backup=None
  if old:
   invoke(prefix,'stop')
   if (prefix/'data').exists():
    target=prefix/'backups'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])/'data'
    shutil.copytree(prefix/'data',target);previous_backup=target.relative_to(prefix).as_posix()
   port=old['port']
  (prefix/'versions').mkdir(exist_ok=True);destination=prefix/'versions'/release;stage.rename(destination)
  config={'version':VERSION,'release':release,'python':relative,'port':port,'previous_release':old['release'] if old else None,'previous_config':old,'previous_backup':previous_backup}
  temp=prefix/'install.json.tmp';temp.write_text(json.dumps(config,indent=2));temp.replace(prefix/'install.json')
  shutil.copy2(HERE/'setup.py',prefix/'control.py')
  print(f'Installed {VERSION} in {prefix}\nData: {prefix / "data"}\nDashboard: http://127.0.0.1:{port}\nUse Open App to start. Installation does not start jobs.')
 except BaseException:
  if stage.exists():shutil.rmtree(stage)
  raise

def release_paths(prefix,config):
 release=config.get('release','');relative=config.get('python','')
 if not isinstance(release,str) or not release.startswith('r-') or '/' in release or '\\' in release or '..' in release:raise ValueError('Invalid installed release metadata')
 base=(prefix/'versions'/release).resolve();python=Path(os.path.abspath(base/relative))
 if not python.is_relative_to(base) or not python.is_file() or not (base/'app/server.py').is_file():raise ValueError('Installed release/runtime is missing or invalid. Files may be incomplete or removed; check antivirus Protection history/quarantine. Saved projects are not deleted by this check.')
 return base,python

def validate_installation(prefix):
 config=read_config(prefix);base,python=release_paths(prefix,config)
 subprocess.run([str(python),'-c','import asyncua,pymodbus,aiohttp,serial,cryptography,reportlab,PIL'],check=True,timeout=30)
 subprocess.run([str(python),str(base/'app/diagnostics.py')],check=True,timeout=120)
 print('Installed runtime and isolated loopback protocol checks passed. No saved endpoints contacted.')

def rollback(prefix,confirmed=False):
 if not confirmed:raise ValueError('Rollback restores previous data. Back up current work and rerun with --confirm-data-restore')
 current=read_config(prefix);old=current.get('previous_config');relative=current.get('previous_backup')
 if not isinstance(old,dict) or not relative:raise ValueError('No matching previous release/data backup is recorded')
 release_paths(prefix,old)
 backup=(prefix/relative).resolve()
 if not backup.is_relative_to((prefix/'backups').resolve()) or not backup.is_dir():raise ValueError('Previous data backup is missing or outside the backup directory')
 stage=prefix/('restore-'+uuid.uuid4().hex[:8]);shutil.copytree(backup,stage)
 saved=prefix/'backups'/('before-rollback-'+uuid.uuid4().hex[:8])/'data';saved.parent.mkdir(parents=True)
 moved=False
 try:
  invoke(prefix,'stop')
  if (prefix/'data').exists():(prefix/'data').rename(saved);moved=True
  stage.rename(prefix/'data')
  restored={**old,'rollback_from':current['release'],'rollback_saved_data':saved.relative_to(prefix).as_posix() if moved else None}
  temp=prefix/'install.json.tmp';temp.write_text(json.dumps(restored,indent=2));temp.replace(prefix/'install.json')
 except BaseException:
  if moved:
   shutil.rmtree(prefix/'data',ignore_errors=True);saved.rename(prefix/'data')
  shutil.rmtree(stage,ignore_errors=True);raise
 print('Previous release and matching data restored. Current data preserved under backups. Start explicitly after review.')

def diagnostic_package(prefix):
 config=read_config(prefix);base,python=release_paths(prefix,config)
 query='import importlib.metadata as m,json,platform,sys; print(json.dumps({"python":sys.version.split()[0],"machine":platform.machine(),"dependencies":{n:m.version(n) for n in ("asyncua","pymodbus","aiohttp","pyserial","cryptography","reportlab","pillow")}}))'
 result=subprocess.run([str(python),'-c',query],capture_output=True,text=True,timeout=30)
 report={'software_version':config.get('version'),'release':config['release'],'runtime_check_exit':result.returncode,'scope':'No project content, logs, credentials, licence parties, paths or network requests included'}
 if result.returncode==0:
  try:report['runtime']=json.loads(result.stdout)
  except ValueError:report['runtime_error']='Invalid runtime metadata response'
 else:report['runtime_error']='Runtime dependency inspection failed; see local console'
 report['data_presence']={name:(prefix/'data'/name).is_file() for name in ('project.json','scenarios.sqlite3','project-library.sqlite3','security/users.sqlite3')}
 folder=prefix/'support';folder.mkdir(exist_ok=True);path=folder/('ProtocolWorkbench-Diagnostics-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.zip')
 with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
  z.writestr('diagnostic_summary.json',json.dumps(report,indent=2));z.writestr('README.txt','Minimal diagnostic package. Project contents/logs/secrets are deliberately excluded. Review before sharing. This package does not establish device compatibility or security qualification.')
 return path

def uninstall(prefix):
 read_config(prefix);invoke(prefix,'stop')
 shutil.rmtree(prefix/'versions')
 (prefix/'install.json').unlink()
 print(f'Application removed. Projects, logs and backups retained in {prefix}.')

def installation_lock(prefix):
 prefix.mkdir(parents=True,exist_ok=True);handle=open(prefix/'installation.lock','a+b')
 handle.write(b'0');handle.flush();handle.seek(0)
 try:
  if os.name=='nt':
   import msvcrt
   msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
  else:
   import fcntl
   fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
 except OSError:
  handle.close();raise ValueError('Another install/control operation is running for this folder')
 return handle

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['install','start','stop','restart','status','uninstall','rollback','validate','diagnostics']);p.add_argument('--prefix',type=Path,default=default_prefix());p.add_argument('--port',type=int,default=8878);p.add_argument('--offline',action='store_true');p.add_argument('--confirm-data-restore',action='store_true');p.add_argument('--no-ui',action='store_true');a=p.parse_args();prefix=a.prefix.expanduser().resolve()
 try:
  with installation_lock(prefix):
   if a.action=='install':install(prefix,a.port,a.offline)
   elif a.action=='uninstall':uninstall(prefix)
   elif a.action=='rollback':rollback(prefix,a.confirm_data_restore)
   elif a.action=='validate':validate_installation(prefix)
   elif a.action=='diagnostics':print(diagnostic_package(prefix))
   else:invoke(prefix,a.action,a.no_ui or a.action!='start')
 except Exception as error:
  print('ERROR:',error,file=sys.stderr)
  log=write_failure_log(error)
  if log:print('Diagnostic log saved to:',log,file=sys.stderr)
  return 1
 return 0
if __name__=='__main__':sys.exit(main())
