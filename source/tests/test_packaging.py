import importlib.util,json
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('community_setup',Path(__file__).resolve().parents[1]/'packaging/setup.py');setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)

def test_installer_lock_rejects_concurrent_mutation(tmp_path):
 with setup.installation_lock(tmp_path):
  with pytest.raises(ValueError,match='Another'):setup.installation_lock(tmp_path)
 with setup.installation_lock(tmp_path):pass

def test_damaged_package_does_not_touch_installed_data(tmp_path,monkeypatch):
 package=tmp_path/'package';(package/'payload/app').mkdir(parents=True);(package/'payload/app/server.py').write_text('changed');(package/'SHA256SUMS.txt').write_text('0'*64+'  payload/app/server.py\n');prefix=tmp_path/'installed';(prefix/'data').mkdir(parents=True);project=prefix/'data/project.json';project.write_text('preserved');monkeypatch.setattr(setup,'HERE',package)
 with pytest.raises(ValueError,match='checksum'):setup.install(prefix,18880)
 assert project.read_text()=='preserved' and not (prefix/'versions').exists()

def test_offline_missing_runtime_preserves_existing_install(tmp_path,monkeypatch):
 package=tmp_path/'package';(package/'payload/app').mkdir(parents=True);(package/'payload/app/server.py').write_text('');(package/'SHA256SUMS.txt').write_text(__import__('hashlib').sha256(b'').hexdigest()+'  payload/app/server.py\n');prefix=tmp_path/'installed';prefix.mkdir();(prefix/'install.json').write_text(json.dumps({'release':'old','port':18880,'python':'runtime/bin/python'}));(prefix/'versions/old').mkdir(parents=True);monkeypatch.setattr(setup,'HERE',package)
 with pytest.raises(ValueError,match='Offline'):setup.install(prefix,18880,offline=True)
 assert json.loads((prefix/'install.json').read_text())['release']=='old' and (prefix/'versions/old').exists() and not list(prefix.glob('stage-*'))

@pytest.mark.parametrize('code,arch',[(0x14c,'x86'),(0x8664,'x64'),(0xaa64,'arm64')])
def test_windows_architecture_uses_pe_header_not_host_name(tmp_path,monkeypatch,code,arch):
 import struct
 data=bytearray(128);data[:2]=b'MZ';struct.pack_into('<I',data,0x3c,64);data[64:68]=b'PE\0\0';struct.pack_into('<H',data,68,code)
 bundle=tmp_path/'runtime';bundle.mkdir();exe=bundle/'python.exe';exe.write_bytes(data);(bundle/'BUILD_INFO.json').write_text(json.dumps({'architecture':arch}));monkeypatch.setattr(setup.sys,'executable',str(exe))
 monkeypatch.setattr(setup.platform,'machine',lambda:'ARM64')
 assert setup.validate_windows_runtime(bundle)==arch
 (bundle/'BUILD_INFO.json').write_text(json.dumps({'architecture':'incorrect'}))
 with pytest.raises(ValueError,match='does not match'):setup.validate_windows_runtime(bundle)

def test_missing_extracted_file_has_actionable_error(tmp_path,monkeypatch):
 package=tmp_path/'package';(package/'payload/app').mkdir(parents=True);(package/'payload/app/server.py').write_text('');(package/'SHA256SUMS.txt').write_text('0'*64+'  missing-library.py\n');monkeypatch.setattr(setup,'HERE',package)
 with pytest.raises(ValueError,match='Extract All'):setup.install(tmp_path/'target',18880)
 assert not (tmp_path/'target').exists()

def test_manifest_rejects_unlisted_payload_file(tmp_path,monkeypatch):
 import hashlib
 package=tmp_path/'package';(package/'payload/app').mkdir(parents=True);(package/'payload/app/server.py').write_text('');(package/'payload/extra.py').write_text('unlisted');(package/'SHA256SUMS.txt').write_text(hashlib.sha256(b'').hexdigest()+'  payload/app/server.py\n');monkeypatch.setattr(setup,'HERE',package)
 with pytest.raises(ValueError,match='every payload'):setup.install(tmp_path/'installed',18880)
 assert not (tmp_path/'installed').exists()


def test_rollback_preserves_current_data_and_restores_matching_backup(tmp_path,monkeypatch):
 for release in ('r-old','r-new'):
  base=tmp_path/'versions'/release;(base/'app').mkdir(parents=True);(base/'app/server.py').write_text('');(base/'python').write_text('')
 old={'release':'r-old','python':'python','port':18880,'version':'old'};current={'release':'r-new','python':'python','port':18880,'version':'new','previous_config':old,'previous_backup':'backups/before/data'}
 (tmp_path/'install.json').write_text(json.dumps(current));(tmp_path/'data').mkdir();(tmp_path/'data/project.json').write_text('current');(tmp_path/'backups/before/data').mkdir(parents=True);(tmp_path/'backups/before/data/project.json').write_text('previous')
 monkeypatch.setattr(setup,'invoke',lambda *args:None)
 with pytest.raises(ValueError,match='confirm-data-restore'):setup.rollback(tmp_path)
 setup.rollback(tmp_path,True);restored=setup.read_config(tmp_path);assert restored['release']=='r-old' and (tmp_path/'data/project.json').read_text()=='previous';assert (tmp_path/restored['rollback_saved_data']/'project.json').read_text()=='current'


def test_rollback_rejects_missing_or_external_backup(tmp_path):
 old={'release':'r-old','python':'python'};(tmp_path/'versions/r-old/app').mkdir(parents=True);(tmp_path/'versions/r-old/app/server.py').write_text('');(tmp_path/'versions/r-old/python').write_text('')
 (tmp_path/'install.json').write_text(json.dumps({'release':'r-new','previous_config':old,'previous_backup':'../elsewhere'}))
 with pytest.raises(ValueError,match='backup'):setup.rollback(tmp_path,True)


def test_minimal_diagnostics_excludes_data_and_logs(tmp_path,monkeypatch):
 import subprocess,zipfile
 base=tmp_path/'versions/r-test';(base/'app').mkdir(parents=True);(base/'app/server.py').write_text('');(base/'python').write_text('');(tmp_path/'install.json').write_text(json.dumps({'release':'r-test','python':'python','version':'test'}));(tmp_path/'data').mkdir();(tmp_path/'data/project.json').write_text('{"password":"DO-NOT-EXPORT"}')
 monkeypatch.setattr(setup.subprocess,'run',lambda *args,**kwargs:subprocess.CompletedProcess([],0,'{"python":"3.13","dependencies":{}}',''))
 path=setup.diagnostic_package(tmp_path)
 with zipfile.ZipFile(path) as z:
  text=''.join(z.read(n).decode() for n in z.namelist());assert 'DO-NOT-EXPORT' not in text and str(tmp_path) not in text;assert len(z.namelist())==2


def test_installer_refuses_nonempty_noninstallation_target(tmp_path,monkeypatch):
 import hashlib
 package=tmp_path/'package';(package/'payload/app').mkdir(parents=True);(package/'payload/app/server.py').write_text('');(package/'SHA256SUMS.txt').write_text(hashlib.sha256(b'').hexdigest()+'  payload/app/server.py\n');prefix=tmp_path/'personal';prefix.mkdir();(prefix/'keep.txt').write_text('keep');monkeypatch.setattr(setup,'HERE',package)
 with pytest.raises(ValueError,match='dedicated'):setup.install(prefix,18880)
 assert (prefix/'keep.txt').read_text()=='keep'


def test_legitimate_venv_interpreter_symlink_is_supported(tmp_path):
 import os,sys
 if os.name=='nt':pytest.skip('POSIX virtual environment symlink test')
 base=tmp_path/'versions/r-venv';(base/'app').mkdir(parents=True);(base/'app/server.py').write_text('');(base/'runtime/bin').mkdir(parents=True);(base/'runtime/bin/python').symlink_to(sys.executable)
 selected,python=setup.release_paths(tmp_path,{'release':'r-venv','python':'runtime/bin/python'})
 assert selected==base and python==base/'runtime/bin/python'
 with pytest.raises(ValueError):setup.release_paths(tmp_path,{'release':'r-venv','python':'../../outside'})
