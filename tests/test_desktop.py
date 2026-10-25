"""Windows launcher regression: real GUI executable and cross-process ownership."""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from cellflow.desktop import DesktopInstance, pe_subsystem, repair_console_python, repair_gui_python

ROOT = Path(__file__).resolve().parents[1]


class DesktopOwnership(unittest.TestCase):
    def test_stale_metadata_does_not_block_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            instance = DesktopInstance(folder)
            instance.path.write_text('{"pid": 999999, "title": "CellFlow"}', encoding='utf-8')
            self.assertTrue(instance.acquire())
            try:
                instance.publish(state='starting')
                self.assertEqual(instance.owner()['pid'], os.getpid())
            finally:
                instance.close()
            self.assertFalse(instance.path.exists())

    def test_only_owner_can_publish(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                DesktopInstance(folder).publish(state='ready')

    def test_second_process_cannot_acquire_live_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            instance = DesktopInstance(folder)
            self.assertTrue(instance.acquire())
            code = ('from cellflow.desktop import DesktopInstance; import sys; '
                    'x=DesktopInstance(sys.argv[1]); ok=x.acquire(); x.close(); sys.exit(1 if ok else 0)')
            env = dict(os.environ, __PYVENV_LAUNCHER__=sys.executable)
            try:
                result = subprocess.run([sys._base_executable, '-c', code, folder], cwd=ROOT,
                                        env=env, capture_output=True, timeout=15,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.assertEqual(result.returncode, 0, result.stderr)
            finally:
                instance.close()
            self.assertTrue(instance.acquire(), 'closing must release the OS lock')
            instance.close()


@unittest.skipUnless(os.name == 'nt', 'Windows PE / pythonw lifecycle')
class WindowsGuiLauncher(unittest.TestCase):
    def test_console_redirector_preserves_no_window_and_environment(self):
        code = ('import ctypes,json,sys; print(json.dumps({'
                '"console":int(ctypes.windll.kernel32.GetConsoleWindow()),'
                '"prefix":sys.prefix}))')
        result = subprocess.run([str(ROOT / '.venv/Scripts/python.exe'), '-c', code],
                                capture_output=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['console'], 0, 'a hidden analysis must not allocate a console')
        self.assertEqual(Path(data['prefix']).resolve(), (ROOT / '.venv').resolve())

    def test_console_redirector_matches_distribution(self):
        official = Path(sys.base_prefix) / 'Lib/venv/scripts/nt/python.exe'
        actual = ROOT / '.venv/Scripts/python.exe'
        self.assertEqual(actual.read_bytes(), official.read_bytes())
        self.assertFalse(repair_console_python(ROOT / '.venv'))

    def test_installed_pythonw_is_gui_executable(self):
        self.assertEqual(pe_subsystem(ROOT / '.venv/Scripts/pythonw.exe'), 2)

    def test_console_shim_is_repaired_from_same_python_distribution(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder)
            (env / 'Scripts').mkdir()
            (env / 'pyvenv.cfg').write_text('home = ' + sys.base_prefix, encoding='utf-8')
            target = env / 'Scripts/pythonw.exe'
            target.write_bytes(Path(sys.executable).read_bytes())
            self.assertEqual(pe_subsystem(target), 3)
            self.assertTrue(repair_gui_python(env))
            official = Path(sys.base_prefix) / 'Lib/venv/scripts/nt/pythonw.exe'
            self.assertEqual(hashlib.sha256(target.read_bytes()).digest(), hashlib.sha256(official.read_bytes()).digest())
            self.assertFalse(repair_gui_python(env), 'valid GUI redirector must remain unchanged')

    def test_invalid_executable_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pythonw.exe'
            path.write_bytes(b'not an executable')
            with self.assertRaises(ValueError):
                pe_subsystem(path)

    def test_owned_api_stops_and_releases_port(self):
        loader = importlib.machinery.SourceFileLoader('cellflow_desktop_test', str(ROOT / 'cellflow_app.pyw'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        app = importlib.util.module_from_spec(spec)
        loader.exec_module(app)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        process = None
        try:
            process, ok = app.start_api(port)
            self.assertTrue(ok, 'API must really answer health from this repository')
            self.assertTrue(app.healthy(port))
        finally:
            app.stop_api(process)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and app.healthy(port, 0.2):
            time.sleep(0.1)
        with socket.socket() as sock:
            self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0, 'API runtime child leaked')

class InstallCheck(unittest.TestCase):
    def test_install_check_mode_reports_ready(self):
        proc = subprocess.run([sys.executable, 'install.py', '--check'], cwd=str(ROOT),
                              capture_output=True, text=True, encoding='utf-8', timeout=180)
        self.assertEqual(proc.returncode, 0, (proc.stdout or '') + (proc.stderr or ''))
        self.assertIn('ENVIRONMENT_CHECK_OK', proc.stdout)
        self.assertIn('.venv', proc.stdout)
