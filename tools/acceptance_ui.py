"""Exercise the production Qt shell and built React navigation against a real API.

Run from an isolated checkout: python -m tools.acceptance_ui --output .tmp/ui-acceptance
Uses a free loopback port; never connects to or closes an existing service.
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not (root / 'web/dist/index.html').is_file():
        raise RuntimeError('Build web/dist first')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    server_log = (output / 'api.log').open('w', encoding='utf-8')
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'cellflow.api:app',
                               '--host', '127.0.0.1', '--port', str(port)], cwd=root,
                              stdout=server_log, stderr=server_log,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                urllib.request.urlopen(origin + '/api/meta', timeout=3).close()
                break
            except OSError:
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Acceptance API did not start')
                time.sleep(.2)
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from cellflow.desktop_qt import run_window

        evidence = {'renderer': 'production QtWebEngine + built React', 'checks': [], 'ok': False}
        labels = ['概览', '数据', '模型分析', '分析结果', '报告']
        state = {'step': 0, 'pending': False, 'ticks': 0, 'dataset': False, 'submitted': False}

        def finish(error=None):
            evidence['error'] = error
            evidence['ok'] = error is None
            (output / 'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
            timer.stop()
            QApplication.instance().quit()

        def inspect():
            state['ticks'] += 1
            if state['ticks'] > 240:
                finish('UI acceptance timeout')
                return
            if state['pending']:
                return
            windows = QApplication.topLevelWidgets()
            view = next((w.findChild(QWebEngineView) for w in windows if w.findChild(QWebEngineView)), None)
            if view is None:
                return
            if not state['dataset']:
                state['pending'] = True
                expression = """(() => {
                  const select = document.querySelector('.cf-side select');
                  const option = select && [...select.options].find(x=>x.textContent.includes('模拟'));
                  if (!option) return false;
                  select.value=option.value; select.dispatchEvent(new Event('change',{bubbles:true}));
                  const original=window.fetch;
                  window.fetch=async (...args)=>{
                    const response=await original(...args);
                    if (String(args[0]).endsWith('/api/runs') && args[1]?.method==='POST')
                      response.clone().json().then(x=>window.__acceptanceJob=x.id);
                    return response;
                  };
                  return true;
                })()"""
                def selected(ok):
                    state['pending'] = False
                    state['dataset'] = bool(ok)
                view.page().runJavaScript(expression, selected)
                return
            if state['step'] >= len(labels):
                state['pending'] = True
                expression = """(() => {
                  if (window.__acceptanceDetail) return JSON.stringify(window.__acceptanceDetail);
                  if (window.__acceptanceJob) {
                    fetch('/api/runs/'+window.__acceptanceJob).then(x=>x.json()).then(x=>window.__acceptanceDetail=x);
                    return '{}';
                  }
                  [...document.querySelectorAll('[role=menuitem]')].find(x=>x.textContent.trim()==='模型分析')?.click();
                  const declaration=[...document.querySelectorAll('label')].find(x=>x.textContent.trim()==='X 为原始计数');
                  const input=declaration?.querySelector('input');
                  if (input && !input.checked) { input.click(); return '{}'; }
                  const button=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='运行分析');
                  if (button && !button.disabled && !window.__acceptanceSubmitted) {
                    window.__acceptanceSubmitted=true; button.click();
                  }
                  return '{}';
                })()"""
                def submitted(raw):
                    state['pending'] = False
                    detail = json.loads(raw or '{}')
                    status = detail.get('state')
                    if status in ('failed', 'cancelled'):
                        finish('UI-submitted computation failed: ' + str(detail.get('error')))
                    elif status == 'completed':
                        if not detail.get('quality', {}).get('ok') or not detail.get('figures'):
                            finish('UI-submitted computation lacks quality/figures')
                            return
                        evidence['checks'].append(dict(scenario='UI declaration and run submission through real queue',
                                                       job=detail['id'], state=status, quality_ok=True))
                        view.grab().save(str(output / 'qt-react.png'))
                        finish()
                    else:
                        view.page().runJavaScript('window.__acceptanceDetail=null')
                view.page().runJavaScript(expression, submitted)
                return
            label = labels[state['step']]
            state['pending'] = True
            expression = '''(() => {
              const label = %s;
              const e = [...document.querySelectorAll('[role="menuitem"]')].find(x=>x.textContent.trim()===label);
              if (!e) return false;
              e.click(); return true;
            })()''' % json.dumps(label)

            def clicked(ok):
                if not ok:
                    state['pending'] = False
                    return

                def check():
                    def verified(raw):
                        state['pending'] = False
                        data = json.loads(raw)
                        if label == '模型分析' and not data['model_ready']:
                            return
                        if not data['content'] or data['overflow']:
                            finish('Empty content or horizontal overflow in ' + label)
                            return
                        view.grab().save(str(output / ('view-%d.png' % state['step'])))
                        evidence['checks'].append({'view': label, **data})
                        state['step'] += 1
                    view.page().runJavaScript('JSON.stringify({content:document.body.innerText.length>100,overflow:document.documentElement.scrollWidth>innerWidth,model_ready:[...document.querySelectorAll('button')].some(x=>x.textContent.trim()==='运行分析')})', verified)
                QTimer.singleShot(600, check)
            view.page().runJavaScript(expression, clicked)

        class Instance:
            def publish(self, **values):
                if values.get('state') == 'shown':
                    timer.start(500)

        timer = QTimer()
        timer.timeout.connect(inspect)
        try:
            run_window(root, output, origin, Instance(), lambda msg: None)
        except SystemExit:
            pass
        if not evidence['ok']:
            raise RuntimeError(str(evidence))
    finally:
        server.terminate()
        server.wait(timeout=15)
        server_log.close()


if __name__ == '__main__':
    main()
