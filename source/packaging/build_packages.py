"""Isolated preview archives with target-lock and runtime identity checks."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import argparse,hashlib,json,re,shutil,zipfile
BASE=Path(__file__).resolve().parents[1]
OUT=BASE.parent/'Distribution-Installer-Preview14'
VERSION=re.search(r"^VERSION=['\"]([^'\"]+)['\"]",(BASE/'app/version.py').read_text(),re.M).group(1)
TARGETS={'Windows-ARM64':('Windows ARM64','PW-Win-ARM64','arm64'),'Windows-x86':('Windows 32 bit','PW-Win-x86','x86'),'Windows-x64':('Windows 64 bit','PW-Win-x64','x64'),'macOS':('macOS','ProtocolWorkbench-Community-macOS',None),'Linux':('Linux','ProtocolWorkbench-Community-Linux',None)}
SUPPORTED_TARGETS=('Windows-x64','macOS','Linux')
GUIDES=['System_Requirements.html','OPC_UA_Server_Import_Guide.html','Next_Guide.html','Bridge_Guide.html','Communication_Health_Guide.html','Communication_Verification_Guide.html','Data_Verification_Guide.html','Logging_Reporting_Guide.html','Mixed_Tags_Guide.html','Performance_Guide.html','Project_Management_Guide.html','Scenario_Automation_Guide.html','Security_Guide.html']


def runtime_inventory(runtime,lock):
 from email.parser import Parser
 expected={}
 for line in lock.read_text().splitlines():
  if '==' in line:
   name,version=line.split('==',1);expected[re.sub(r'[-_.]+','-',name).lower()]=version
 actual={}
 for path in (runtime/'Lib/site-packages').glob('*.dist-info/METADATA'):
  meta=Parser().parsestr(path.read_text());key=re.sub(r'[-_.]+','-',meta['Name']).lower()
  if key in actual:raise ValueError('Duplicate runtime distribution: '+key)
  actual[key]=meta['Version']
 if actual!=expected:
  mismatch={k:{'expected':expected.get(k),'actual':actual.get(k)} for k in expected.keys()|actual.keys() if expected.get(k)!=actual.get(k)}
  raise ValueError('Runtime does not match security lock: '+json.dumps(mismatch,sort_keys=True))
 return actual


def build(platform,runtime_root=None):
 label,folder,arch=TARGETS[platform]
 runtime=None
 if arch:
  runtime_root=runtime_root or BASE.parent.parent/'work/protocol-app/security-build';runtime=runtime_root/('runtime-'+arch)
  if not (runtime/'BUILD_INFO.json').is_file():raise ValueError('No patched native runtime available for '+platform+'; do not reuse an older vulnerable package')
  runtime_inventory(runtime,BASE/'packaging/windows'/('requirements-security-'+arch+'.txt'))
 destination=OUT/('production release _ '+label);destination.mkdir(parents=True,exist_ok=True);root=destination/folder
 if root.exists():shutil.rmtree(root)
 root.mkdir();shutil.copy2(BASE/'packaging/setup.py',root/'setup.py');payload=root/'payload';payload.mkdir()
 for source in ('app','examples'):shutil.copytree(BASE/source,payload/source,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 for guide_root in (root/'guides',payload/'guides'):
  guide_root.mkdir()
  for name in GUIDES:shutil.copy2(BASE/'guides'/name,guide_root/name)
  next_guide=guide_root/'Next_Guide.html';next_guide.write_text(next_guide.read_text().replace('Local UI on port 8877','Packaged UI on port 8878'))
  for source in (BASE/'packaging/guides').glob('*.html'):shutil.copy2(source,guide_root/source.name)
  shutil.copy2(BASE/'packaging/guides/Installation.html',guide_root/'Installation_Guide.html');shutil.copy2(BASE/'packaging/guides/Troubleshooting.html',guide_root/'Troubleshooting_Guide.html')
  shutil.copytree(BASE/'packaging/release-docs',guide_root/'editable')
  shutil.copy2(BASE/'guides/OPC_UA_Server_Import_Guide.md',guide_root/'editable'/'OPC_UA_Server_Import_Guide.md')
 shutil.copy2(BASE/'packaging/requirements-security-posix.txt',payload/'requirements.txt');shutil.copy2(BASE/'packaging/guides/Installation.html',root/'START_HERE.html')
 shutil.copytree(BASE.parent/'ProtocolWorkbench/licenses',payload/'licenses');shutil.copy2(BASE/'packaging/pyserial-3.5-LICENSE.txt',payload/'licenses/pyserial-3.5-LICENSE.txt')
 if (BASE.parent/'PERMITTED_USE.md').is_file():
  shutil.copy2(BASE.parent/'PERMITTED_USE.md',root/'PERMITTED_USE.md');shutil.copy2(BASE.parent/'PERMITTED_USE.md',payload/'licenses/PERMITTED_USE.md')
 for source in (BASE/'packaging/reporting-dependencies').glob('*LICENSE.txt'):shutil.copy2(source,payload/'licenses'/source.name)
 actions=[('Install','install --offline' if arch else 'install'),('Open App','start'),('Stop App','stop'),('Status','status'),('Validate Installation','validate'),('Create Diagnostics','diagnostics'),('Rollback','rollback --confirm-data-restore'),('Uninstall','uninstall')]
 if arch:
  shutil.copytree(runtime,root/'runtime',ignore=shutil.ignore_patterns('__pycache__','*.pyc'));shutil.copy2(BASE/'packaging/windows'/('requirements-security-'+arch+'.txt'),payload/'requirements.txt')
  info=json.loads((runtime/'BUILD_INFO.json').read_text());info['application_version']=VERSION;(root/'BUILD_INFO.json').write_text(json.dumps(info,indent=2))
  for dist in (runtime/'Lib/site-packages').glob('*.dist-info'):
   for notice in dist.rglob('*'):
    if notice.is_file() and any(word in notice.name.lower() for word in ('license','copying','notice')):shutil.copy2(notice,payload/'licenses'/(dist.name+'-'+notice.relative_to(dist).as_posix().replace('/','_')))
  # Generated wrappers check package files with built-in PowerShell before Python.
  # No execution-policy overrides, encoded commands or antivirus exclusions.
  from windows_bootstrap import runner,wrapper
  (root/'Run Workbench.bat').write_bytes(runner().encode('ascii'))
  for name,action in actions:
   verb=action.split()[0]
   for suffix in ('.cmd','.bat'):
    (root/(name+suffix)).write_bytes(wrapper(verb,rollback=name=='Rollback').encode('ascii'))
  for name,action in [('Check Package','check-package'),('Check Runtime','check-runtime')]:
   (root/(name+'.bat')).write_bytes(wrapper(action,always_pause=True).encode('ascii'))

 else:
  (root/'BUILD_INFO.json').write_text(json.dumps({'application_version':VERSION,'platform':platform,'runtime':'Online Python venv installer; Python 3.11–3.14','native_execution':'pending until release validation'},indent=2))
  for name,action in actions:
   file=root/(name+('.command' if platform=='macOS' else '.sh'))
   confirmation='printf "%s" "Restore previous release AND saved data? Current data is backed up. Type RESTORE: "\nread -r PW_CONFIRM\n[ "$PW_CONFIRM" = RESTORE ] || exit 1\n' if name=='Rollback' else ''
   file.write_text('#!/bin/sh\nset -eu\nTASK_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'+confirmation+'exec python3 "$TASK_DIR/setup.py" '+action+'\n');file.chmod(0o755)
 (root/'RELEASE_STATUS.txt').write_text(VERSION+' · Engineering Preview\nWindows x64: offline bundled runtime; native Windows execution NOT TESTED. Native ARM64/x86 security builds are BLOCKED until compatible patched dependencies are available.\nmacOS/Linux: Python 3.11–3.14 online installer. Qualification results supplied separately. Linux native execution NOT TESTED.\nUnsigned archive installers, not MSI/DMG/DEB. No online activation or feature/expiry restrictions.\nPaper agreement template is DRAFT, not an executed agreement. See the current public permitted-use notice for limited evaluation permission.\nDefault dashboard port 8878; development port 8877 remains separate.\n')
 files=sorted(p for p in root.rglob('*') if p.is_file());manifest=''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(root).as_posix()+'\n' for p in files);(root/'SHA256SUMS.txt').write_text(manifest)
 archive=destination/('ProtocolWorkbench-'+VERSION+'-'+platform+'.zip')
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for file in files+[root/'SHA256SUMS.txt']:z.write(file,root.name+'/'+file.relative_to(root).as_posix())
 with zipfile.ZipFile(archive) as z:
  assert z.testzip() is None
  for line in manifest.splitlines():
   digest,name=line.split('  ',1);assert hashlib.sha256(z.read(root.name+'/'+name)).hexdigest()==digest
 archive.with_suffix('.zip.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n');shutil.copy2(root/'START_HERE.html',destination/'START_HERE.html');print(archive,archive.stat().st_size)
 return root

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--targets',nargs='+',choices=list(TARGETS),default=list(SUPPORTED_TARGETS));parser.add_argument('--runtime-root',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args()
 if args.output:OUT=args.output.resolve()
 for target in args.targets:build(target,args.runtime_root)
