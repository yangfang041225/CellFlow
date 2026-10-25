"""用 Edge 无头 + DevTools 协议给 React 工作站拍真实截图（含各视图切换）。"""
import asyncio
import base64
import json
import sys
import time

import requests
import websockets

CDP = 'http://127.0.0.1:9225'
APP = 'http://127.0.0.1:8600'
VIEWS = ['overview', 'data', 'model', 'results', 'reports']


def new_target(url):
    response = requests.put(f'{CDP}/json/new?{url}', timeout=10)
    if response.status_code >= 400:
        response = requests.get(f'{CDP}/json/new?{url}', timeout=10)
    response.raise_for_status()
    return response.json()


async def call(ws, counter, method, params=None):
    counter[0] += 1
    await ws.send(json.dumps({'id': counter[0], 'method': method, 'params': params or {}}))
    while True:
        message = json.loads(await ws.recv())
        if message.get('id') == counter[0]:
            return message


async def shoot(ws_url, outdir):
    counter = [0]
    async with websockets.connect(ws_url, max_size=200 * 1024 * 1024) as ws:
        await call(ws, counter, 'Page.enable')
        await call(ws, counter, 'Emulation.setDeviceMetricsOverride',
                   {'width': 1440, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False})
        await call(ws, counter, 'Page.navigate', {'url': APP + '/'})
        await asyncio.sleep(6)

        for index, name in enumerate(VIEWS):
            expression = (
                '(() => { const items = document.querySelectorAll(".bp6-menu-item");'
                f' if (items.length > {index}) items[{index}].click();'
                ' return items.length; })()'
            )
            await call(ws, counter, 'Runtime.evaluate', {'expression': expression})
            await asyncio.sleep(3)
            result = await call(ws, counter, 'Page.captureScreenshot',
                                {'format': 'png', 'captureBeyondViewport': False})
            data = base64.b64decode(result['result']['data'])
            path = f'{outdir}/{name}.png'
            with open(path, 'wb') as handle:
                handle.write(data)
            print(name, len(data), 'bytes')


async def main(targets_dir):
    target = new_target(APP + '/')
    await shoot(target['webSocketDebuggerUrl'], targets_dir)
    requests.get(f"{CDP}/json/close/{target['id']}", timeout=5)


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'docs/screenshots'))
