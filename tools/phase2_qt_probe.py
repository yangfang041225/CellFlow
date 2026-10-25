"""Real Qt/React preview-confirm smoke probe in this checkout, on an unused port.

Uses only generated CSV data and this checkout's state; never attaches to another app.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    env = dict(os.environ)
    env.pop('CELLFLOW_PARENT_PID', None)
    env.pop('CELLFLOW_PARENT_CREATED', None)
    api_log = (output/'api.log').open('w', encoding='utf-8')
    api = subprocess.Popen([sys.executable,'-m','uvicorn','cellflow.api:app','--host','127.0.0.1',
                            '--port',str(port),'--log-level','warning'], cwd=ROOT, env=env,
                           stdout=api_log,stderr=api_log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    url = f'http://127.0.0.1:{port}'
    events = []
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(100):
            try:
                with opener.open(url+'/api/health',timeout=1) as response:
                    if json.load(response)['ok']: break
            except Exception: time.sleep(.2)
        else: raise RuntimeError('isolated API did not start')
        from PySide6.QtCore import QTimer
        from cellflow.desktop import DesktopInstance
        from cellflow.desktop_qt import run_window
        state = {'stage':0,'ticks':0,'ok':False}
        steps = [
          ("[...document.querySelectorAll('[role=menuitem]')].some(e=>e.textContent.trim()==='数据')",
           "[...document.querySelectorAll('[role=menuitem]')].find(e=>e.textContent.trim()==='数据').click()"),
          ("[...document.querySelectorAll('select option')].some(e=>e.value==='upload')",
           "(()=>{let e=[...document.querySelectorAll('select')].find(e=>[...e.options].some(o=>o.value==='upload'));e.value='upload';e.dispatchEvent(new Event('change',{bubbles:true}));})()"),
          ("!!document.querySelector('input[type=file]')",
           "(()=>{let e=document.querySelector('input[type=file]');let d=new DataTransfer();d.items.add(new File(['cell,group,g1,g2,g3,g4,g5\\nc1,A,1,2,3,4,5\\nc2,B,2,3,4,5,6\\n'],'phase2-qt-probe.csv',{type:'text/csv'}));e.files=d.files;e.dispatchEvent(new Event('change',{bubbles:true}));})()"),
          ("[...document.querySelectorAll('button')].some(e=>e.textContent.trim()==='确认导入')",
           "[...document.querySelectorAll('button')].find(e=>e.textContent.trim()==='确认导入').click()"),
          ("document.body.innerText.includes('已导入：phase2-qt-probe.csv')", "true")]
        def ready(window,page):
            timer = QTimer(window)
            timer.setInterval(300)
            def tick():
                state['ticks'] += 1
                if state['ticks'] > 200:
                    events.append({'failure':'timeout','stage':state['stage']})
                    timer.stop(); window.close(); return
                stage = state['stage']
                if stage >= len(steps): return
                check,action = steps[stage]
                def checked(value):
                    if state['stage'] != stage or value != 'true': return
                    state['stage'] += 1
                    page.runJavaScript(action)
                    events.append({'completed_stage':stage,'at':time.time()})
                    if state['stage'] == len(steps):
                        state['ok'] = True
                        timer.stop()
                        def capture():
                            window.grab().save(str(output/'qt-preview-confirm.png'))
                            page.runJavaScript('JSON.stringify({text:document.body.innerText,width:innerWidth,scrollWidth:document.documentElement.scrollWidth})',
                                lambda text: (output/'dom.json').write_text(text,encoding='utf-8'))
                            QTimer.singleShot(400,window.close)
                        QTimer.singleShot(500,capture)
                page.runJavaScript('JSON.stringify(Boolean('+check+'))',checked)
            timer.timeout.connect(tick)
            timer.start()
        instance=DesktopInstance(output/'desktop')
        if not instance.acquire(): raise RuntimeError('probe instance lock unavailable')
        (output/'desktop').mkdir(exist_ok=True)
        with (output/'qt.log').open('w',encoding='utf-8') as log:
            try: run_window(ROOT,output/'desktop',url+'/',instance,lambda s:log.write(s+'\n'),on_ready=ready)
            finally: instance.close()
        result=dict(ok=state['ok'],events=events,api_pid=api.pid,port=port,renderer='production run_window / QtWebEngine',
                    scope='React Data page: navigation, upload CSV File event, preview dialog, confirm API, dataset registration, normal Qt close')
        (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
        return 0 if state['ok'] else 1
    finally:
        if api.poll() is None:
            api.terminate()
            api.wait(timeout=10)
        api_log.close()


if __name__ == '__main__':
    raise SystemExit(main())
