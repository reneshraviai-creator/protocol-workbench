"""Release gates reject stale native dependency payloads before modifying output."""
import importlib.util,json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('security_packages',ROOT/'packaging/build_packages.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


def distribution(runtime,name,version,suffix=''):
 p=runtime/'Lib/site-packages'/f'{name}-{version}{suffix}.dist-info';p.mkdir(parents=True);(p/'METADATA').write_text(f'Name: {name}\nVersion: {version}\n')


@pytest.mark.parametrize('problem',['old','extra','missing','duplicate'])
def test_stale_or_incomplete_native_payload_is_rejected(tmp_path,problem):
 runtime=tmp_path/'runtime';lock=tmp_path/'requirements.txt';lock.write_text('aiohttp==3.14.5\ncryptography==50.0.2\n')
 distribution(runtime,'aiohttp','3.14.5')
 if problem!='missing':distribution(runtime,'cryptography','46.0.3' if problem=='old' else '50.0.2')
 if problem=='extra':distribution(runtime,'unlisted','1.0')
 if problem=='duplicate':distribution(runtime,'aiohttp','3.14.5','-duplicate')
 with pytest.raises(ValueError,match='Runtime does not match|Duplicate'):builder.runtime_inventory(runtime,lock)


def test_native_runtime_matches_exact_lock_with_normalised_names(tmp_path):
 lock=tmp_path/'lock.txt';lock.write_text('typing_extensions==4.16.0\ncryptography==50.0.2\n');runtime=tmp_path/'runtime'
 distribution(runtime,'typing-extensions','4.16.0');distribution(runtime,'cryptography','50.0.2')
 assert builder.runtime_inventory(runtime,lock)=={'typing-extensions':'4.16.0','cryptography':'50.0.2'}


def test_unavailable_native_build_preserves_existing_distribution(tmp_path,monkeypatch):
 monkeypatch.setattr(builder,'OUT',tmp_path/'output');old=builder.OUT/'production release _ Windows ARM64'/'PW-Win-ARM64';old.mkdir(parents=True);(old/'keep.txt').write_text('unchanged')
 with pytest.raises(ValueError,match='No patched native runtime'):builder.build('Windows-ARM64',tmp_path/'absent')
 assert (old/'keep.txt').read_text()=='unchanged'


def test_installer_archive_and_app_share_runtime_identity():
 spec=importlib.util.spec_from_file_location('security_installer',ROOT/'packaging/setup.py');setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
 import ast
 version=next(ast.literal_eval(n.value) for n in ast.parse((ROOT/'app/version.py').read_text()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in n.targets))
 assert builder.VERSION==setup.VERSION==version
 assert builder.OUT.name=='Distribution-Installer-Preview14' and builder.SUPPORTED_TARGETS==('Windows-x64','macOS','Linux')
