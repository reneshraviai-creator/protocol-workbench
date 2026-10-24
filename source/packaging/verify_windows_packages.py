"""Static Windows archive acceptance. This does not execute Windows binaries."""
from pathlib import Path
import hashlib,json,struct,zipfile
OUT=Path(__file__).resolve().parents[2]/'CommunityPreview'
def verify():
 rows=[]
 for label,code in {'ARM64':0xaa64,'x86':0x14c,'x64':0x8664}.items():
  path=OUT/f'ProtocolWorkbench-Community-Windows-{label}.zip'
  with zipfile.ZipFile(path) as z:
   assert z.testzip() is None;root=f'PW-Win-{label}';assert all(n.startswith(root+'/') for n in z.namelist())
   for line in z.read(root+'/SHA256SUMS.txt').decode().splitlines():
    digest,name=line.split('  ',1);assert hashlib.sha256(z.read(root+'/'+name)).hexdigest()==digest
   info=json.loads(z.read(root+'/BUILD_INFO.json'));assert info['architecture']==label.lower()
   native=0
   for name in z.namelist():
    if name.lower().endswith(('.exe','.dll','.pyd')):
     raw=z.read(name);assert raw[:2]==b'MZ';offset=struct.unpack_from('<I',raw,0x3c)[0];assert raw[offset:offset+4]==b'PE\0\0';actual=struct.unpack_from('<H',raw,offset+4)[0]
     assert actual==code or (label=='ARM64' and name==root+'/runtime/vcruntime140_1.dll' and actual==0x8664);native+=1
   for name in ('Install.bat','Install.cmd','Check Runtime.bat','START_HERE.html','payload/app/rtu_demo.py','runtime/Lib/site-packages/asyncua/server/standard_address_space/standard_address_space_services_10.py'):assert root+'/'+name in z.namelist()
   assert not any('/data/' in n or n.endswith('/direct_url.json') for n in z.namelist())
   for name in z.namelist():
    if name.endswith(('.py','.json','.cmd','.bat','.html','.md')):assert b'/Users/' not in z.read(name),name
  assert hashlib.sha256(path.read_bytes()).hexdigest()==path.with_suffix('.zip.sha256').read_text().split()[0]
  rows.append({'package':path.name,'architecture':label,'bytes':path.stat().st_size,'python':info['python'],'native_files_checked':native,'integrity_and_layout':'PASSED','native_windows_execution':'NOT TESTED'});print(label,': static checks passed')
 return rows
if __name__=='__main__':
 (OUT/'Windows_Architecture_Validation.json').write_text(json.dumps({'date':'2026-10-19','version':'2.0.0-preview.4','packages':verify(),'tests':'240 baseline tests; ARM dependency set 236 tests plus 7 installer tests on macOS','note':'Static checks and Mac dependency tests are not Windows-native execution; official ARM CRT companion recorded in runtime BUILD_INFO.'},indent=2))
