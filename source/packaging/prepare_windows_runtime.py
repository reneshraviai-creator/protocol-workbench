"""Assemble native Windows runtimes from verified official Python ZIPs and PyPI wheels.
Run on a build machine after pip download --only-binary for the selected target.
"""
import argparse,csv,hashlib,json,shutil,struct,subprocess,sys,zipfile
from pathlib import Path
ARCHES={'x64':('win_amd64',0x8664),'x86':('win32',0x14c),'arm64':('win_arm64',0xaa64)}
def machine(path):
 data=path.read_bytes();assert data[:2]==b'MZ',path;offset=struct.unpack_from('<I',data,0x3c)[0];assert data[offset:offset+4]==b'PE\0\0',path;return struct.unpack_from('<H',data,offset+4)[0]
def build(arch,python_zip,wheels,output):
 platform,expected=ARCHES[arch]
 if output.exists():shutil.rmtree(output)
 output.mkdir(parents=True)
 with zipfile.ZipFile(python_zip) as z:z.extractall(output)
 assert machine(output/'python.exe')==expected
 site=output/'Lib/site-packages';site.mkdir(parents=True)
 files=sorted(wheels.glob('*.whl'));assert files,'No wheels'
 subprocess.run([sys.executable,'-m','pip','install','--no-deps','--no-index','--no-compile','--only-binary=:all:','--platform',platform,'--python-version','3.13','--implementation','cp','--abi','cp313','--target',str(site),*[str(p) for p in files]],check=True)
 # Omit build-host entry scripts and local-file provenance; app uses library APIs.
 shutil.rmtree(site/'bin',ignore_errors=True)
 for dist in site.glob('*.dist-info'):
  (dist/'direct_url.json').unlink(missing_ok=True)
  record=dist/'RECORD'
  if record.exists():
   with record.open(newline='') as handle:rows=list(csv.reader(handle))
   rows=[row for row in rows if row and not row[0].endswith('/direct_url.json') and not row[0].startswith(('bin/','../../bin/'))]
   with record.open('w',newline='') as handle:csv.writer(handle).writerows(rows)
 (output/'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../app\nimport site\n')
 native=[];exceptions=[]
 for path in output.rglob('*'):
  if path.suffix.lower() in ('.exe','.dll','.pyd'):
   actual=machine(path)
   if actual!=expected:
    # Python.org ARM64 embed contains this unchanged x64 CRT companion.
    relative=path.relative_to(output).as_posix()
    with zipfile.ZipFile(python_zip) as official:
     assert arch=='arm64' and relative=='vcruntime140_1.dll' and actual==0x8664 and path.read_bytes()==official.read(relative),f'{path}: wrong machine {actual:x}'
    exceptions.append({'file':relative,'machine':hex(actual),'provenance':'Unmodified file in SHA-256 verified official ARM64 Python ZIP'})
   native.append(path.relative_to(output).as_posix())
 info={'architecture':arch,'python':'3.13.16','python_zip_sha256':hashlib.sha256(python_zip.read_bytes()).hexdigest(),'native_files_checked':len(native),'official_runtime_companions':exceptions,'native_execution':'pending','wheels':[{'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]}
 (output/'BUILD_INFO.json').write_text(json.dumps(info,indent=2));print(arch,len(files),'wheels;',len(native),'native files verified')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--arch',choices=ARCHES,required=True);p.add_argument('--python-zip',type=Path,required=True);p.add_argument('--wheels',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();build(a.arch,a.python_zip,a.wheels,a.output)
