"""Start/stop the independent background app; Windows uses the bundled interpreter."""
import argparse
import hashlib
from version import VERSION
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser
from runtime_lock import acquire,running
BASE=Path(__file__).resolve().parent
ROOT=BASE.parent
DATA=Path(os.environ.get('WORKBENCH_DATA',str(ROOT/'data')));DATA.mkdir(parents=True,exist_ok=True)
PORT=int(os.environ.get('WORKBENCH_PORT','8877'))
if not 1024<=PORT<=65535:raise ValueError('WORKBENCH_PORT must be 1024..65535')

def workspace_matches(result):
    visible=result.get('data_directory')
    return (bool(visible) and Path(visible).resolve()==DATA.resolve()) or result.get('workspace_id')==hashlib.sha256(str(DATA.resolve()).encode()).hexdigest()

def start(open_ui=True):
    if not running(DATA/'app.lock'):
        (DATA/'stop.request').unlink(missing_ok=True)
        executable=sys.executable
        if os.name=='nt' and Path(executable).name.lower()=='pythonw.exe':executable=str(Path(executable).with_name('python.exe'))
        with (DATA/'launcher.log').open('ab') as log:
            kw={'cwd':ROOT,'stdin':subprocess.DEVNULL,'stdout':log,'stderr':subprocess.STDOUT,'env':{**os.environ,'WORKBENCH_DATA':str(DATA)}}
            if os.name=='nt':kw['creationflags']=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP
            else:kw['start_new_session']=True
            proc=subprocess.Popen([executable,'-u',str(BASE/'server.py'),'--port',str(PORT)],**kw)
        for _ in range(100):
            if proc.poll() is not None:raise RuntimeError('Application exited; open data/launcher.log for details')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/bootstrap',timeout=0.2) as r:
                    import json
                    result=json.load(r)
                    if r.status==200 and result.get('version')==VERSION and workspace_matches(result):break
            except Exception:time.sleep(0.1)
        else:raise RuntimeError('Application did not become ready; see data/launcher.log')
    if open_ui:webbrowser.open(f'http://127.0.0.1:{PORT}')
    print(f'Application running: http://127.0.0.1:{PORT}')

def stop():
    if not running(DATA/'app.lock'):print('Already stopped');return
    (DATA/'stop.request').write_text('stop')
    for _ in range(200):
        if not running(DATA/'app.lock'):print('Stopped');return
        time.sleep(0.1)
    raise RuntimeError('Stop timed out. Review application.log; no second process was started.')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','restart','status']);parser.add_argument('--no-ui',action='store_true');args=parser.parse_args()
    try:
        with acquire(DATA/'launcher.lock'):
            if args.action in ('stop','restart'):stop()
            if args.action in ('start','restart'):start(not args.no_ui)
            if args.action=='status':print('RUNNING' if running(DATA/'app.lock') else 'STOPPED')
    except Exception as error:
        (DATA/'launcher-error.txt').write_text(str(error),encoding='utf-8')
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(0,str(error),'Protocol Workbench',0x10)
        else:print(f'ERROR: {error}',file=sys.stderr)
        sys.exit(1)
if __name__=='__main__':main()
