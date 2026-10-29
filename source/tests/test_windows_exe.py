"""Validate offline input before any installer compiler is invoked."""
import hashlib,importlib.util,json,struct,zipfile
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('exe_builder',ROOT/'packaging/build_windows_exe.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


def package(tmp_path,change=None,manifest_change=None,arch='x64',code=0x8664):
 pe=bytearray(128);pe[:2]=b'MZ';struct.pack_into('<I',pe,0x3c,64);pe[64:68]=b'PE\0\0';struct.pack_into('<H',pe,68,code)
 files={'BUILD_INFO.json':json.dumps({'architecture':arch,'application_version':'2.0.0-preview.14'}).encode(),'runtime/python.exe':bytes(pe),'setup.py':b'# setup','Run Workbench.bat':b'@echo off','payload/app/server.py':b'# server','payload/app/version.py':b"VERSION='2.0.0-preview.14'\n"}
 if change:change(files)
 manifest=''.join(hashlib.sha256(raw).hexdigest()+'  '+name+'\n' for name,raw in files.items())
 if manifest_change:manifest=manifest_change(manifest)
 path=tmp_path/'package.zip'
 with zipfile.ZipFile(path,'w') as z:
  for name,raw in files.items():z.writestr('PW-Win-x64/'+name,raw)
  z.writestr('PW-Win-x64/SHA256SUMS.txt',manifest)
 Path(str(path)+'.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest()+'  package.zip\n')
 return path


def test_valid_offline_archive_identity(tmp_path):
 result=builder.verified_archive(package(tmp_path))
 assert result['version']=='2.0.0-preview.14' and result['root']=='PW-Win-x64'


def test_zip_digest_mismatch_prevents_build(tmp_path):
 p=package(tmp_path);Path(str(p)+'.sha256').write_text('0'*64)
 with pytest.raises(ValueError,match='Archive checksum'):builder.build(p,tmp_path/'missing-compiler',tmp_path/'output')
 assert not (tmp_path/'output').exists()


def test_internal_digest_mismatch_even_with_valid_outer_digest(tmp_path):
 p=package(tmp_path,manifest_change=lambda s:s.replace(hashlib.sha256(b'# server').hexdigest(),'0'*64))
 with pytest.raises(ValueError,match='Internal checksum'):builder.verified_archive(p)


@pytest.mark.parametrize('code',[0x14c,0xaa64])
def test_non_x64_runtime_rejected_despite_claimed_metadata(tmp_path,code):
 with pytest.raises(ValueError,match='x64 PE'):builder.verified_archive(package(tmp_path,code=code))


@pytest.mark.parametrize('name',['runtime/python.exe','Run Workbench.bat','payload/app/server.py'])
def test_incomplete_offline_payload_rejected(tmp_path,name):
 with pytest.raises(ValueError,match='Missing required'):builder.verified_archive(package(tmp_path,change=lambda f:f.pop(name)))


def test_escaping_archive_entry_rejected(tmp_path):
 p=package(tmp_path,change=lambda f:f.update({'../../outside':b'bad'}))
 with pytest.raises(ValueError,match='Unsafe archive'):builder.verified_archive(p)


def test_payload_version_must_match_build_identity(tmp_path):
 p=package(tmp_path,change=lambda f:f.update({'payload/app/version.py':b"VERSION='wrong'"}))
 with pytest.raises(ValueError,match='Payload version'):builder.verified_archive(p)


def test_architecture_label_does_not_override_payload_target(tmp_path):
 with pytest.raises(ValueError,match='Runtime must be x64'):builder.verified_archive(package(tmp_path,arch='arm64'))
