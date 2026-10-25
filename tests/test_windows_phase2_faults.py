"""Windows faults use only owned processes, separate queue stores and unused ports."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from contextlib import closing

from cellflow import queue
from tests.test_queue_lifecycle import _write_shim, _shim_job, _row, _wait, _wait_state, _reap


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object/parent identity probes')
class WindowsFaults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.owned = []
        self.record = []

    def tearDown(self):
        for p in reversed(self.owned): _reap(p)
        evidence = os.environ.get('CELLFLOW_FAULT_EVIDENCE_DIR')
        if evidence:
            folder = Path(evidence)
            folder.mkdir(parents=True, exist_ok=True)
            (folder/(self._testMethodName+'.json')).write_text(json.dumps(self.record,indent=2),encoding='utf-8')
        self.tmp.cleanup()

    def track(self, role, pid):
        created = queue._process_create_time(pid)
        self.assertIsNotNone(created)
        self.record.append(dict(role=role,pid=pid,created=created))
        return created

    def supervisor(self, store):
        p = subprocess.Popen([sys.executable,'-m','cellflow.queue','--store',str(store)],
                             cwd=queue.ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        self.owned.append(p)
        self.track('supervisor',p.pid)
        return p

    def test_supervisor_death_kills_job_object_then_restart_drains_queue(self):
        store=self.root/'queue'
        shim=_write_shim(self.root/'shim',seconds=4)
        first=_shim_job(store,shim,8101)
        second=_shim_job(store,shim,8102)
        supervisor=self.supervisor(store)
        row=_wait(lambda: (_row(store,first) or {}).get('child_pid') and _row(store,first),15)
        self.assertTrue(row)
        created=self.track('job_object_wrapper',row['child_pid'])
        self.assertTrue(queue._identity_matches(supervisor.pid, self.record[0]['created']))
        supervisor.kill(); supervisor.wait(timeout=10)
        self.assertTrue(_wait(lambda:not queue._identity_matches(row['child_pid'],created),10))
        self.assertIn(first,queue.recover_orphans(store))
        self.assertEqual(_row(store,first)['state'],'failed')
        self.assertEqual(_row(store,second)['state'],'queued')
        restart=self.supervisor(store)
        self.assertEqual(_wait_state(store,second,timeout=20)['state'],'completed')
        restart.wait(timeout=10)
        self.record.append(dict(first=_row(store,first)['state'],second=_row(store,second)['state'],
                                job_object=queue.__name__+'.process_job: KILL_ON_JOB_CLOSE',restart_exit=restart.returncode))

    def test_compute_wrapper_exit_fails_job_and_continues_next(self):
        store=self.root/'queue'
        shim=_write_shim(self.root/'shim',seconds=4)
        first=_shim_job(store,shim,8201)
        second=_shim_job(store,shim,8202)
        supervisor=self.supervisor(store)
        row=_wait(lambda: (_row(store,first) or {}).get('child_pid') and _row(store,first),15)
        self.assertTrue(row)
        self.track('job_object_wrapper',row['child_pid'])
        # Terminate only the named Job Object of the first owned fixture job.
        from cellflow.process_job import terminate, job_name
        self.assertTrue(terminate(job_name(store,first)))
        self.assertEqual(_wait_state(store,first,timeout=15)['state'],'failed')
        self.assertEqual(_wait_state(store,second,timeout=20)['state'],'completed')
        supervisor.wait(timeout=10)
        self.record.append(dict(first='failed',second='completed',supervisor_exit=supervisor.returncode))

    def test_live_parent_identity_then_death_exits_only_owned_api(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
        parent=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW)
        self.owned.append(parent)
        created=self.track('launcher_fixture',parent.pid)
        env=dict(os.environ,CELLFLOW_PARENT_PID=str(parent.pid),CELLFLOW_PARENT_CREATED=str(created))
        api=subprocess.Popen([sys.executable,'-m','uvicorn','cellflow.api:app','--host','127.0.0.1','--port',str(port)],
                             cwd=queue.ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        self.owned.append(api); self.track('api',api.pid)
        def listening():
            with socket.socket() as s:
                return s.connect_ex(('127.0.0.1',port))==0
        self.assertTrue(_wait(listening,15))
        parent.kill();parent.wait(timeout=10)
        api.wait(timeout=10)
        self.assertFalse(listening())
        self.record.append(dict(port=port,api_exit=api.returncode,parent_exit=parent.returncode))

    def test_wrong_parent_creation_time_is_rejected_while_parent_survives(self):
        parent=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW)
        self.owned.append(parent)
        created=self.track('unrelated_live_identity_fixture',parent.pid)
        env=dict(os.environ,CELLFLOW_PARENT_PID=str(parent.pid),CELLFLOW_PARENT_CREATED=str(created+10))
        api=subprocess.Popen([sys.executable,'-c','import cellflow.api; import time; time.sleep(60)'],cwd=queue.ROOT,env=env,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        self.owned.append(api)
        api.wait(timeout=15)
        self.assertIsNone(parent.poll())
        self.record.append(dict(api_exit=api.returncode,unrelated_parent_still_alive=True))

    def test_native_handle_termination_fallback_only_owned_process(self):
        p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW)
        self.owned.append(p)
        created=self.track('owned_fallback_fixture',p.pid)
        self.assertTrue(queue._identity_matches(p.pid,created))
        with patch('cellflow.queue.subprocess.run',side_effect=OSError('injected taskkill unavailable')):
            self.assertTrue(queue._terminate_tree(p.pid))
        p.wait(timeout=10)
        self.record.append(dict(exit=p.returncode))
