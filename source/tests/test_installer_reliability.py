"""Damaged/quarantined downloads must fail without touching installed data."""
import hashlib,importlib.util,sys,zipfile
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('reliability_setup',ROOT/'packaging/setup.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)


def package(tmp_path):
 root=tmp_path/'download';(root/'payload/app').mkdir(parents=True);(root/'runtime').mkdir()
 files={'payload/app/server.py':b'# fixture','runtime/python.exe':b'fixture only','setup.py':b'# setup'}
 for name,value in files.items():(root/name).write_bytes(value)
 (root/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(v).hexdigest()+'  '+n+'\n' for n,v in files.items()))
 return root


def test_missing_runtime_reports_quarantine_and_preserves_project(tmp_path,monkeypatch):
 root=package(tmp_path);(root/'runtime/python.exe').unlink();monkeypatch.setattr(setup,'HERE',root)
 target=tmp_path/'installed';(target/'data').mkdir(parents=True);project=target/'data/project.json';project.write_text('keep')
 with pytest.raises(ValueError,match='Protection history'):setup.install(target,18880,True)
 assert project.read_text()=='keep' and not (target/'versions').exists()


def test_tampered_internal_notice_is_detected_before_install(tmp_path,monkeypatch):
 root=package(tmp_path);(root/'payload/app/server.py').write_text('changed after hashing')
 monkeypatch.setattr(setup,'HERE',root)
 with pytest.raises(ValueError,match='checksum mismatch'):setup.install(tmp_path/'installed',18880,True)
 assert not (tmp_path/'installed').exists()


@pytest.mark.parametrize('entry',['not a manifest entry','0'*64+'  ../outside','0'*64+'  /outside'])
def test_invalid_manifest_rejected_before_target_mutation(tmp_path,monkeypatch,entry):
 root=package(tmp_path);(root/'SHA256SUMS.txt').write_text(entry+'\n');monkeypatch.setattr(setup,'HERE',root)
 with pytest.raises(ValueError,match='Invalid'):setup.install(tmp_path/'installed',18880,True)
 assert not (tmp_path/'installed').exists()


def test_unlisted_runtime_library_is_rejected_but_generated_cache_is_ignored(tmp_path):
 root=package(tmp_path);cache=root/'runtime/Lib/__pycache__';cache.mkdir(parents=True);(cache/'helper.pyc').write_bytes(b'generated')
 assert setup.verify_package(root)==3
 extra=root/'runtime/extra.py';extra.write_text('# unlisted library')
 with pytest.raises(ValueError,match='every payload/runtime'):setup.verify_package(root)


def test_failure_log_survives_removal_of_extraction_directory(tmp_path,monkeypatch):
 monkeypatch.setattr(setup.tempfile,'gettempdir',lambda:str(tmp_path/'logs'))
 if setup.os.name=='nt':monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'logs'))
 root=package(tmp_path);error=ValueError('runtime file missing');log=setup.write_failure_log(error)
 import shutil
 shutil.rmtree(root)
 assert log.is_file() and 'runtime file missing' in log.read_text() and not root.exists()


def test_rebuilt_archive_covers_updated_licence_and_rejects_postbuild_edit(tmp_path,monkeypatch):
 sys.path.insert(0,str(ROOT/'packaging'))
 import build_packages,verify_release
 monkeypatch.setattr(build_packages,'OUT',tmp_path/'release');root=build_packages.build('macOS')
 assert verify_release.verify(build_packages.OUT,('macOS',))[0]['manifest']=='PASS'
 archive=next(build_packages.OUT.rglob('*.zip'))
 entries=[]
 with zipfile.ZipFile(archive) as z:
  for info in z.infolist():
   raw=z.read(info)
   if info.filename.endswith('payload/app/licensing.py'):raw+=b'\n# post-build attribution edit\n'
   entries.append((info,raw))
 with zipfile.ZipFile(archive,'w') as z:
  for info,raw in entries:z.writestr(info,raw)
 with pytest.raises(AssertionError):verify_release.verify(build_packages.OUT,('macOS',))


def test_windows_package_check_does_not_require_bundled_python():
 sys.path.insert(0,str(ROOT/'packaging'))
 from windows_bootstrap import runner,powershell_command
 command=powershell_command();script=runner()
 assert command.index("if($action -eq 'check-package')") < command.index('& $python')
 assert 'Get-FileHash' in command and 'Protection history' in command
 assert 'ProtocolWorkbenchInstallerLogs' in script and 'PYTHONDONTWRITEBYTECODE=1' in script
 assert len(command)<7000 and 'ExecutionPolicy' not in script and 'EncodedCommand' not in script
