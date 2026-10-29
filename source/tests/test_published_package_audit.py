"""Do not let damaged release inputs reach native execution or extraction."""
import hashlib,importlib.util,zipfile
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_audit',ROOT/'tools/native_package_acceptance.py');audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def archive(tmp_path,escape=False,bad=False):
 p=tmp_path/'input.zip';raw=b'fixture';name='../outside' if escape else 'setup.py'
 with zipfile.ZipFile(p,'w') as z:
  z.writestr('package/'+name,raw)
  z.writestr('package/SHA256SUMS.txt',('0'*64 if bad else hashlib.sha256(raw).hexdigest())+'  '+name+'\n')
 return p,hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.parametrize('problem',['outer','internal','escape'])
def test_bad_published_archive_never_extracts(tmp_path,problem):
 p,h=archive(tmp_path,escape=problem=='escape',bad=problem=='internal');dest=tmp_path/'extracted'
 with pytest.raises(ValueError):audit.extract_verified(p,'0'*64 if problem=='outer' else h,dest)
 assert not dest.exists() and not (tmp_path/'outside').exists()


def test_complete_manifest_extracts_to_fresh_disposable_directory(tmp_path):
 p,h=archive(tmp_path);root=audit.extract_verified(p,h,tmp_path/'extracted')
 assert (root/'setup.py').read_bytes()==b'fixture'
 with pytest.raises(ValueError,match='already exists'):audit.extract_verified(p,h,tmp_path/'extracted')
