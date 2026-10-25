"""Force-exit probes against this checkout's own native launcher and API only."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cellflow import queue


def wait(predicate,timeout=20):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=predicate()
        if value:return value
        time.sleep(.2)
    raise TimeoutError('owned desktop probe timed out')


def children(pid):
    # Read only PID/parent/name; no command lines, paths, credentials or foreign mutations.
    command=f"@(Get-CimInstance Win32_Process -Filter 'ParentProcessId = {int(pid)}' | Select-Object ProcessId,Name) | ConvertTo-Json -Compress"
    p=subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True,text=True,
                     timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
    data=json.loads(p.stdout or '[]')
    return data if isinstance(data,list) else [data]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    records=[]
    for scenario in ('kill_native_launcher_qt','kill_api_only','restart_after_faults'):
        with socket.socket() as s:
            s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
        log=(args.output/(scenario+'.log')).open('w',encoding='utf-8')
        # Bypass the venv redirector so Popen.pid is the actual native Qt owner.
        launcher=subprocess.Popen([sys._base_executable,str(ROOT/'cellflow_app.pyw')],cwd=ROOT,
                                  env=dict(os.environ,CELLFLOW_PORT=str(port),__PYVENV_LAUNCHER__=sys.executable),stdout=log,stderr=log,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        owned=[]
        record={'scenario':scenario,'port':port,'policy':'Qt and launcher are the same process; their death exits owned API; independently supervised computations persist'}
        try:
            created=queue._process_create_time(launcher.pid)
            assert created is not None
            record.update(launcher_pid=launcher.pid,launcher_created=created)
            def ready():
                p=ROOT/'logs/desktop-ready.json'
                if launcher.poll() is not None:raise RuntimeError('launcher failed: '+str(launcher.returncode))
                if p.exists():
                    data=json.loads(p.read_text(encoding='utf-8'))
                    return data if data.get('pid')==launcher.pid else None
            record['ready']=wait(ready,35)
            direct=children(launcher.pid)
            for row in direct:
                pid=int(row['ProcessId']); when=queue._process_create_time(pid)
                if when is not None: owned.append((pid,when,row['Name']))
            record['children']=[dict(pid=p,created=c,name=n) for p,c,n in owned]
            apis=[p for p,c,n in owned if n.lower() in ('pythonw.exe','python.exe')]
            assert len(apis)==1, 'cannot uniquely identify owned API'
            api_pid=apis[0]
            api_created=next(c for p,c,n in owned if p==api_pid)
            if scenario=='kill_api_only':
                assert queue._identity_matches(api_pid,api_created)
                # Kill the API handle only, leaving native Qt and any independent supervisor.
                assert queue._terminate_tree(api_pid)
                wait(lambda:not queue._identity_matches(api_pid,api_created),10)
                record['qt_survived_api_death']=launcher.poll() is None
                assert record['qt_survived_api_death']
            assert queue._identity_matches(launcher.pid,created)
            launcher.kill();launcher.wait(timeout=10)
            wait(lambda:not queue._identity_matches(api_pid,api_created),10)
            record.update(ok=True,launcher_exit=launcher.returncode,api_exited=True)
        except Exception as exc:
            record.update(ok=False,error=f'{type(exc).__name__}: {exc}')
        finally:
            if launcher.poll() is None:launcher.kill();launcher.wait(timeout=10)
            for pid,created,name in owned:
                if queue._identity_matches(pid,created):
                    # Only an enumerated direct child of the self-created launcher.
                    queue._terminate_tree(pid)
            log.close()
            records.append(record)
    report=dict(ok=all(r.get('ok') for r in records),scenarios=records,
                limitations='Qt renderer-only crash, multiple DPI and clean installation are not included')
    (args.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
