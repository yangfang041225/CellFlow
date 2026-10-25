"""用 Edge 无头模式 + DevTools 协议，给四个页面拍真实截图（等渲染完成再截）。

用法：
    1) 先启动界面： .venv\\Scripts\\python.exe -m streamlit run app.py --server.port 8597
    2) 再启动 Edge： msedge --headless=new --remote-debugging-port=9223 --user-data-dir=<临时目录> about:blank
    3) 运行本脚本： .venv\\Scripts\\python.exe tools\\capture_screens.py 9223 docs\\screenshots

截图会覆盖 docs/screenshots/ 下的 manual.png / models.png / analysis.png / results.png。
"""
import asyncio
import base64
import json
import sys
import time

import requests
import websockets

import os

CDP = 'http://127.0.0.1:9223'
APP = os.environ.get('CELLFLOW_APP', 'http://127.0.0.1:8597')
PAGES = [('manual', ''), ('models', 'models'), ('analysis', 'analysis'), ('results', 'results')]


def new_target(url):
    try:
        response = requests.put(f'{CDP}/json/new?{url}', timeout=10)
    except Exception:
        response = requests.get(f'{CDP}/json/new?{url}', timeout=10)
    response.raise_for_status()
    return response.json()


async def shoot(ws_url, out, height=2600, width=1440, wait=12.0):
    async with websockets.connect(ws_url, max_size=200 * 1024 * 1024) as ws:
        counter = {'id': 0}

        async def call(method, params=None):
            counter['id'] += 1
            await ws.send(json.dumps({'id': counter['id'], 'method': method, 'params': params or {}}))
            while True:
                message = json.loads(await ws.recv())
                if message.get('id') == counter['id']:
                    return message

        await call('Page.enable')
        await call('Emulation.setDeviceMetricsOverride',
                   {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': False})
        await asyncio.sleep(wait)
        result = await call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
        data = base64.b64decode(result['result']['data'])
        with open(out, 'wb') as handle:
            handle.write(data)
        return len(data)


async def main(port, outdir):
    global CDP
    CDP = f'http://127.0.0.1:{port}'
    for name, path in PAGES:
        target = new_target(f'{APP}/{path}')
        out = f'{outdir}/{name}.png'
        size = await shoot(target['webSocketDebuggerUrl'], out)
        print(name, size, 'bytes')
        requests.get(f"{CDP}/json/close/{target['id']}", timeout=5)
        time.sleep(1)


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else '9223',
                     sys.argv[2] if len(sys.argv) > 2 else 'docs/screenshots'))
