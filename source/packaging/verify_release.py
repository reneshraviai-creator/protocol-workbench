"""Read-only static archive validation; never executes foreign binaries."""
import hashlib,json,struct,zipfile
from pathlib import Path
from build_packages import BASE,OUT,VERSION,TARGETS


def verify(output=OUT):
 rows=[]
 for target,(label,folder,arch) in TARGETS.items():
  path=output/('production release _ '+label)/('ProtocolWorkbench-'+VERSION+'-'+target+'.zip')
  with zipfile.ZipFile(path) as z:
   assert z.testzip() is None;names=z.namelist();assert all(n.startswith(folder+'/') and '..' not in Path(n).parts for n in names)
   manifest={}
   for line in z.read(folder+'/SHA256SUMS.txt').decode().splitlines():
    digest,name=line.split('  ',1);assert hashlib.sha256(z.read(folder+'/'+name)).hexdigest()==digest,name;manifest[name]=digest
   assert {n[len(folder)+1:] for n in names if not n.endswith('/') and not n.endswith('/SHA256SUMS.txt')}==set(manifest)
   for name in ('payload/app/licensing.py','payload/app/project_library.py','payload/app/report_pdf.py','payload/guides/Administrator_Guide.html','payload/guides/Project_Management_Guide.html','payload/guides/editable/Paper_Licence_Agreement_Template_DRAFT.md','payload/app/version.py'):
    assert folder+'/'+name in names,name
   assert not any('/data/' in n or '/checkpoint-private/' in n or n.endswith(('/token.txt','/vault.key','/project.json','.sqlite3')) for n in names)
   for name in names:
    if name.endswith(('.py','.md','.html','.json','.bat','.cmd','.sh','.command')):
     raw=z.read(name);assert b'/Users/' not in raw,name
   assert ("VERSION='"+VERSION+"'").encode() in z.read(folder+'/payload/app/version.py')
   info=json.loads(z.read(folder+'/BUILD_INFO.json'));assert info['application_version']==VERSION;native=0
   if arch:
    code={'arm64':0xaa64,'x86':0x14c,'x64':0x8664}[arch]
    for name in names:
     if name.lower().endswith(('.pyd','.exe','.dll')):
      raw=z.read(name);assert raw[:2]==b'MZ';offset=struct.unpack_from('<I',raw,0x3c)[0];assert raw[offset:offset+4]==b'PE\0\0';actual=struct.unpack_from('<H',raw,offset+4)[0]
      assert actual==code or arch=='arm64' and name==folder+'/runtime/vcruntime140_1.dll' and actual==0x8664;native+=1
    for name in ('reportlab/__init__.py','PIL/__init__.py','asyncua/server/standard_address_space/standard_address_space_services_10.py'):assert folder+'/runtime/Lib/site-packages/'+name in names
    assert folder+'/Rollback.bat' in names and folder+'/Validate Installation.bat' in names
   else:
    ext='.command' if target=='macOS' else '.sh';assert folder+'/Rollback'+ext in names
  digest=hashlib.sha256(path.read_bytes()).hexdigest();assert path.with_suffix('.zip.sha256').read_text().split()[0]==digest
  rows.append({'target':target,'archive':path.relative_to(output).as_posix(),'bytes':path.stat().st_size,'sha256':digest,'manifest':'PASS','privacy_static_checks':'PASS','native_files_checked':native,'native_execution':'NOT TESTED by static verifier'})
 return rows

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=OUT);args=parser.parse_args();rows=verify(args.output);(args.output/'Distribution_Manifest.json').write_text(json.dumps({'version':VERSION,'classification':'Engineering Preview','packages':rows,'note':'Checksums/PE/static privacy checks are not target-native or commercial qualification.'},indent=2));print(json.dumps(rows,indent=2))
