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
        state = {'step': 0, 'pending': False, 'ticks': 0}

        def finish(error=None):
            evidence['error'] = error
            evidence['ok'] = error is None
            (output / 'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
            timer.stop()
            QApplication.instance().quit()

        def inspect():
            state['ticks'] += 1
            if state['ticks'] > 120:
                finish('UI acceptance timeout')
                return
            if state['pending']:
                return
            windows = QApplication.topLevelWidgets()
            view = next((w.findChild(QWebEngineView) for w in windows if w.findChild(QWebEngineView)), None)
            if view is None:
                return
            if state['step'] >= len(labels):
                view.grab().save(str(output / 'qt-react.png'))
                finish()
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
                        if not data['content'] or data['overflow']:
                            finish('Empty content or horizontal overflow in ' + label)
                            return
                        view.grab().save(str(output / ('view-%d.png' % state['step'])))
                        evidence['checks'].append({'view': label, **data})
                        state['step'] += 1
                    view.page().runJavaScript('JSON.stringify({content:document.body.innerText.length>100,overflow:document.documentElement.scrollWidth>innerWidth})', verified)
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
