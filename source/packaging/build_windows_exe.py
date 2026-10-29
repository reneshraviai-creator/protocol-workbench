"""Build an unsigned NSIS EXE from an already verified offline x64 archive.

NSIS cross-compilation is not target-native Windows/ARM execution qualification.
"""
import argparse,ast,hashlib,json,os,re,struct,subprocess,tempfile,zipfile
from pathlib import Path,PurePosixPath


def verified_archive(archive):
 archive=Path(archive)
 expected=Path(str(archive)+'.sha256').read_text().split()[0]
 if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:raise ValueError('Archive checksum mismatch')
 with zipfile.ZipFile(archive) as z:
  names=z.namelist()
  if len(set(n.lower() for n in names))!=len(names):raise ValueError('Duplicate archive paths')
  for info in z.infolist():
   p=PurePosixPath(info.filename)
   if p.is_absolute() or '..' in p.parts or '\\' in info.filename or (info.external_attr>>16)&0o170000==0o120000:raise ValueError('Unsafe archive path')
  roots={PurePosixPath(n).parts[0] for n in names}
  if roots!={'PW-Win-x64'}:raise ValueError('EXE requires the x64 offline package')
  if z.testzip():raise ValueError('Archive CRC failure')
  prefix='PW-Win-x64/';manifest={}
  for line in z.read(prefix+'SHA256SUMS.txt').decode().splitlines():
   match=re.fullmatch(r'([0-9a-f]{64})  (.+)',line)
   if not match or match[2] in manifest:raise ValueError('Invalid package manifest')
   digest,name=match.groups()
   if hashlib.sha256(z.read(prefix+name)).hexdigest()!=digest:raise ValueError('Internal checksum mismatch: '+name)
   manifest[name]=digest
  actual={n[len(prefix):] for n in names if not n.endswith('/') and n!=prefix+'SHA256SUMS.txt'}
  if actual!=set(manifest):raise ValueError('Incomplete internal manifest')
  required={'runtime/python.exe','setup.py','Run Workbench.bat','payload/app/server.py','payload/app/version.py'}
  if not required.issubset(actual):raise ValueError('Missing required offline package files')
  pe=z.read(prefix+'runtime/python.exe')
  if len(pe)<64 or pe[:2]!=b'MZ':raise ValueError('Invalid Windows runtime PE')
  offset=struct.unpack_from('<I',pe,0x3c)[0]
  if offset+6>len(pe) or pe[offset:offset+4]!=b'PE\0\0' or struct.unpack_from('<H',pe,offset+4)[0]!=0x8664:raise ValueError('Bundled runtime must be x64 PE')
  info=json.loads(z.read(prefix+'BUILD_INFO.json'))
  if info.get('architecture')!='x64':raise ValueError('Runtime must be x64')
  source=ast.parse(z.read(prefix+'payload/app/version.py').decode())
  version=next(ast.literal_eval(n.value) for n in source.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in n.targets))
  if not isinstance(version,str) or not re.fullmatch(r'[0-9A-Za-z.-]+',version) or info.get('application_version')!=version:raise ValueError('Payload version mismatch')
  return {'version':version,'zip_sha256':expected,'root':'PW-Win-x64','manifest_entries':len(manifest)}


def build(archive,compiler,output,nsisdir=None):
 archive=Path(archive).resolve();compiler=Path(compiler).resolve();output=Path(output).resolve()
 info=verified_archive(archive);output.mkdir(parents=True,exist_ok=True)
 exe=output/('ProtocolWorkbench-'+info['version']+'-Setup.exe')
 template=Path(__file__).resolve().parent/'windows-exe/ProtocolWorkbench.nsi'
 env={**os.environ}
 if nsisdir:env['NSISDIR']=str(Path(nsisdir).resolve())
 # Only a workspace staging directory is removed, never the installed app/data.
 with tempfile.TemporaryDirectory(prefix='nsis-stage-',dir=output) as temporary:
  with zipfile.ZipFile(archive) as z:z.extractall(temporary)
  package=Path(temporary)/info['root']
  subprocess.run([str(compiler),'-V3','-DAPP_VERSION='+info['version'],'-DPACKAGE_ROOT='+str(package),'-DOUTPUT_FILE='+str(exe),str(template)],check=True,env=env)
 raw=exe.read_bytes()
 if raw[:2]!=b'MZ':raise ValueError('Compiler did not produce a PE executable')
 offset=struct.unpack_from('<I',raw,0x3c)[0]
 if raw[offset:offset+4]!=b'PE\0\0':raise ValueError('Invalid installer PE header')
 digest=hashlib.sha256(raw).hexdigest();Path(str(exe)+'.sha256').write_text(digest+'  '+exe.name+'\n')
 report={**info,'installer':exe.name,'installer_sha256':digest,'installer_bytes':len(raw),'signing':'UNSIGNED','payload':'x64','ARM64':'Windows 11 x64 emulation only; explicit experimental choice; NOT VALIDATED','native_windows_execution':'NOT TESTED','compiler_sha256':hashlib.sha256(compiler.read_bytes()).hexdigest(),'nsis_source_sha256':hashlib.sha256(template.read_bytes()).hexdigest()}
 (output/'EXE_Build_Info.json').write_text(json.dumps(report,indent=2)+'\n');return report


if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive',type=Path,required=True);p.add_argument('--compiler',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--nsisdir',type=Path);a=p.parse_args()
 print(json.dumps(build(a.archive,a.compiler,a.output,a.nsisdir),indent=2))
