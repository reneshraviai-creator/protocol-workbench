"""Phase 11 distribution archives; no user data or historical checkpoints."""
from pathlib import Path
import argparse,hashlib,json,re,shutil,zipfile
BASE=Path(__file__).resolve().parents[1]
OUT=BASE.parent/'Distribution-Phase11'
VERSION='2.0.0-preview.11'
TARGETS={'Windows-ARM64':('Windows ARM64','PW-Win-ARM64','arm64'),'Windows-x86':('Windows 32 bit','PW-Win-x86','x86'),'Windows-x64':('Windows 64 bit','PW-Win-x64','x64'),'macOS':('macOS','ProtocolWorkbench-Community-macOS',None),'Linux':('Linux','ProtocolWorkbench-Community-Linux',None)}
GUIDES=['Next_Guide.html','Bridge_Guide.html','Communication_Health_Guide.html','Communication_Verification_Guide.html','Data_Verification_Guide.html','Logging_Reporting_Guide.html','Mixed_Tags_Guide.html','Performance_Guide.html','Project_Management_Guide.html','Scenario_Automation_Guide.html','Security_Guide.html']


def build(platform,runtime_root=None):
 label,folder,arch=TARGETS[platform];destination=OUT/('production release _ '+label);destination.mkdir(parents=True,exist_ok=True);root=destination/folder
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
 shutil.copy2(BASE/'requirements.txt',payload/'requirements.txt');shutil.copy2(BASE/'packaging/guides/Installation.html',root/'START_HERE.html')
 shutil.copytree(BASE.parent/'ProtocolWorkbench/licenses',payload/'licenses');shutil.copy2(BASE/'packaging/pyserial-3.5-LICENSE.txt',payload/'licenses/pyserial-3.5-LICENSE.txt')
 for source in (BASE/'packaging/reporting-dependencies').glob('*LICENSE.txt'):shutil.copy2(source,payload/'licenses'/source.name)
 actions=[('Install','install --offline' if arch else 'install'),('Open App','start'),('Stop App','stop'),('Status','status'),('Validate Installation','validate'),('Create Diagnostics','diagnostics'),('Rollback','rollback --confirm-data-restore'),('Uninstall','uninstall')]
 if arch:
  runtime_root=runtime_root or BASE.parent.parent/'work/protocol-app/phase11-build';runtime=runtime_root/('runtime-'+arch)
  if not (runtime/'BUILD_INFO.json').is_file():raise ValueError('Prepare native runtime first: '+str(runtime))
  shutil.copytree(runtime,root/'runtime',ignore=shutil.ignore_patterns('__pycache__','*.pyc'));shutil.copy2(BASE/'packaging/windows'/('requirements-phase11-'+arch+'.txt'),payload/'requirements.txt')
  info=json.loads((runtime/'BUILD_INFO.json').read_text());info['application_version']=VERSION;(root/'BUILD_INFO.json').write_text(json.dumps(info,indent=2))
  for dist in (runtime/'Lib/site-packages').glob('*.dist-info'):
   for notice in dist.rglob('*'):
    if notice.is_file() and any(word in notice.name.lower() for word in ('license','copying','notice')):shutil.copy2(notice,payload/'licenses'/(dist.name+'-'+notice.relative_to(dist).as_posix().replace('/','_')))
  for name,action in actions:
   confirmation='set /p PW_CONFIRM="Restore previous release AND its saved data? Current data will be backed up. Type RESTORE: "\r\nif not "%PW_CONFIRM%"=="RESTORE" exit /b 1\r\n' if name=='Rollback' else ''
   command='@echo off\r\nif not exist "%~dp0payload\\app\\server.py" (\r\n echo Extract ALL files from the ZIP into C:\\PW first. Do not run inside the ZIP.\r\n pause\r\n exit /b 1\r\n)\r\n'+confirmation+'"%~dp0runtime\\python.exe" "%~dp0setup.py" '+action+'\r\nif errorlevel 1 pause\r\n'
   for suffix in ('.cmd','.bat'):(root/(name+suffix)).write_text(command)
  (root/'Check Runtime.bat').write_text('@echo off\r\n"%~dp0runtime\\python.exe" -c "import platform,struct,asyncua,pymodbus,aiohttp,serial,cryptography,reportlab,PIL; print(platform.machine(),struct.calcsize(\'P\')*8,\'bit runtime imports OK\')"\r\npause\r\n')
 else:
  (root/'BUILD_INFO.json').write_text(json.dumps({'application_version':VERSION,'platform':platform,'runtime':'Online Python venv installer; Python 3.11–3.14','native_execution':'pending until release validation'},indent=2))
  for name,action in actions:
   file=root/(name+('.command' if platform=='macOS' else '.sh'))
   confirmation='printf "%s" "Restore previous release AND saved data? Current data is backed up. Type RESTORE: "\nread -r PW_CONFIRM\n[ "$PW_CONFIRM" = RESTORE ] || exit 1\n' if name=='Rollback' else ''
   file.write_text('#!/bin/sh\nset -eu\nTASK_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'+confirmation+'exec python3 "$TASK_DIR/setup.py" '+action+'\n');file.chmod(0o755)
 (root/'RELEASE_STATUS.txt').write_text(VERSION+' · Engineering Preview\nWindows ARM64/x86/x64: offline bundled architecture-specific Python and dependencies; native Windows execution NOT TESTED.\nmacOS/Linux: Python 3.11–3.14 online installer. Qualification results supplied separately. Linux native execution NOT TESTED.\nUnsigned archive installers, not MSI/DMG/DEB. No online activation or feature/expiry restrictions.\nPaper agreement template is DRAFT for legal review; no licence is issued by this package or notice.\nDefault dashboard port 8878; development port 8877 remains separate.\n')
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
 parser=argparse.ArgumentParser();parser.add_argument('--targets',nargs='+',choices=list(TARGETS),default=list(TARGETS));parser.add_argument('--runtime-root',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args()
 if args.output:OUT=args.output.resolve()
 for target in args.targets:build(target,args.runtime_root)
