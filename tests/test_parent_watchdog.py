"""API 父进程看门狗：启动器被强制关闭后不能留下孤儿 uvicorn。"""
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ParentWatchdog(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows parent watchdog')
    def test_api_exits_when_parent_is_gone(self):
        dummy = subprocess.Popen([sys.executable, '-c', 'pass'])
        dummy.wait(timeout=10)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, CELLFLOW_PARENT_PID=str(dummy.pid))
        proc = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'cellflow.api:app',
             '--host', '127.0.0.1', '--port', str(port), '--log-level', 'warning'],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 20
            while time.time() < deadline and proc.poll() is None:
                time.sleep(0.2)
            self.assertIsNotNone(proc.poll(), '父进程已死，API 仍在运行')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
