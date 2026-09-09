import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.state = self.base / 'state'
        self.calls = self.base / 'calls.jsonl'
        self.fake = self.base / 'codex'
        self.fake.write_text('#!' + sys.executable + '\n' + '''import json, os, sys, time
from pathlib import Path
if '--help' in sys.argv:
    print('Usage: codex queue --thread <THREAD> --message <TEXT>')
    sys.exit(0)
if '--version' in sys.argv:
    print('codex-cli test')
    sys.exit(0)
with open(os.environ['TEST_CALLS'], 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
time.sleep(float(os.environ.get('TEST_QUEUE_DELAY', '0')))
sys.exit(int(os.environ.get('TEST_QUEUE_EXIT', '0')))
''')
        self.fake.chmod(0o700)
        self.env = dict(os.environ, PYTHONPATH=str(ROOT), TEST_CALLS=str(self.calls))
        self.ids = []
        self.addCleanup(self.stop_all)

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, '-m', 'openmonitor', '--state-dir',
                                 str(self.state), *args], env=self.env, cwd=ROOT,
                                capture_output=True, text=True, timeout=8)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        return result

    def start(self, code, *flags, name=None):
        result = self.cli('start', '--thread', str(uuid.uuid4()), '--name', name or 'test',
                          '--codex', str(self.fake), '--batch-seconds', '.1',
                          *flags, '--', sys.executable, '-c', code)
        self.ids.append(result['id'])
        return result['id']

    def wait(self, ident, predicate=lambda s: not s['supervisor_alive'], timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.cli('status', ident)
            if predicate(result):
                return result
            time.sleep(.03)
        self.fail('monitor did not reach expected state: ' + repr(result))

    def messages(self):
        if not self.calls.exists():
            return []
        args = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return [a[a.index('--message') + 1] for a in args]

    def stop_all(self):
        for ident in self.ids:
            self.cli('stop', ident, ok=False)
        for ident in self.ids:
            self.wait(ident)

    def test_detaches_is_quiet_and_submits_one_completion(self):
        gate = self.base / 'go'
        ident = self.start(f'import pathlib,time\np=pathlib.Path({str(gate)!r})\nwhile not p.exists(): time.sleep(.02)\nprint("done")')
        self.assertTrue(self.cli('status', ident)['supervisor_alive'])
        self.assertEqual(self.messages(), [])
        gate.touch()
        status = self.wait(ident)
        self.assertEqual(status['process_state'], 'exited')
        self.assertEqual(status['returncode'], 0)
        self.assertEqual(status['delivery_state'], 'queued')
        self.assertEqual(len(self.messages()), 1)
        self.assertIn('done', self.messages()[0])
        self.assertEqual((self.state / ident / 'stdout.log').read_bytes(), b'done\n')
        self.assertEqual((self.state.stat().st_mode & 0o777), 0o700)

    def test_nonzero_and_stderr_are_visible(self):
        ident = self.start('import sys; print("broken",file=sys.stderr); sys.exit(7)')
        status = self.wait(ident)
        self.assertEqual(status['returncode'], 7)
        self.assertIn('broken', self.messages()[0])
        self.assertIn('stderr', self.messages()[0])

    def test_missing_executable_reports_failure(self):
        value = self.cli('start', '--thread', str(uuid.uuid4()), '--name', 'missing',
                         '--codex', str(self.fake), '--', '/no/such/openmonitor-command')
        self.ids.append(value['id'])
        status = self.wait(value['id'])
        self.assertEqual(status['process_state'], 'failed')
        self.assertIn('No such file', self.messages()[0])

    def test_split_utf8_and_unterminated_line(self):
        ident = self.start('import os,time; os.write(1,b"\\xe2"); time.sleep(.1); os.write(1,b"\\x82\\xac tail")', '--mode', 'lines')
        self.wait(ident)
        events = [json.loads(m.split('\n', 1)[1]) for m in self.messages()]
        self.assertTrue(any('€ tail' in e.get('lines', '') for e in events))

    def test_line_batch_drains_even_when_producer_goes_quiet(self):
        ident = self.start('import time; print("ready",flush=True); time.sleep(10)', '--mode', 'lines')
        self.wait(ident, lambda s: len(self.messages()) == 1)
        self.assertIn('ready', self.messages()[0])
        self.cli('stop', ident)
        status = self.wait(ident)
        self.assertEqual(status['process_state'], 'cancelled')
        self.assertEqual(len(self.messages()), 1)

    def test_output_flood_is_bounded_and_reported(self):
        ident = self.start('import os\nwhile True: os.write(1,b"x"*10000)', '--max-output-bytes', '20000')
        status = self.wait(ident)
        self.assertEqual(status['stop_reason'], 'output_limit')
        self.assertLessEqual((self.state / ident / 'stdout.log').stat().st_size, 20000)
        self.assertIn('output_limit', self.messages()[0])
        self.assertLessEqual(len(self.messages()[0].encode()), 16000)

    def test_timeout_terminates_child(self):
        ident = self.start('import time; time.sleep(10)', '--timeout', '.2')
        self.assertEqual(self.wait(ident)['process_state'], 'timed_out')

    def test_duplicate_name_rejected(self):
        ident = self.start('import time; time.sleep(10)')
        status = self.cli('status', ident)
        result = self.cli('start', '--thread', status['thread'], '--name', 'test',
                          '--codex', str(self.fake), '--', 'true', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('already active', result.stderr)

    def test_uncertain_delivery_has_no_automatic_retry(self):
        self.env['TEST_QUEUE_EXIT'] = '3'
        ident = self.start('print("result")')
        status = self.wait(ident)
        self.assertEqual(status['delivery_state'], 'uncertain')
        self.assertEqual(len(self.messages()), 1)
        self.env['TEST_QUEUE_EXIT'] = '0'
        self.cli('retry', ident, '--accept-duplicate-risk')
        self.assertEqual(self.wait(ident)['delivery_state'], 'queued')
        self.assertEqual(len(self.messages()), 2)

    def test_stop_terminates_grandchild_process_group(self):
        pidfile = self.base / 'child.pid'
        ident = self.start(f'import subprocess,time,pathlib\np=subprocess.Popen(["{sys.executable}","-c","import time; time.sleep(30)"])\npathlib.Path({str(pidfile)!r}).write_text(str(p.pid))\ntime.sleep(30)')
        deadline = time.monotonic() + 3
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        child = int(pidfile.read_text())
        self.cli('stop', ident)
        self.wait(ident)
        # Zombies are dead but may await reaping by PID 1 in Linux containers.
        result = subprocess.run(['ps', '-o', 'stat=', '-p', str(child)], capture_output=True, text=True)
        self.assertTrue(result.returncode != 0 or result.stdout.strip().startswith('Z'), result.stdout)

    def test_invalid_thread_and_limits_fail_before_launch(self):
        for flags in [('--thread', 'latest'), ('--thread', str(uuid.uuid4()), '--timeout', 'nan')]:
            result = self.cli('start', *flags, '--name', 'bad', '--codex', str(self.fake), '--', 'true', ok=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('No module named', result.stderr)

    def test_both_streams_drain_while_delivery_is_slow(self):
        self.env['TEST_QUEUE_DELAY'] = '.25'
        ident = self.start('import os\nfor i in range(30):\n os.write(1,b"a"*1000+b"\\n")\n os.write(2,b"b"*1000+b"\\n")', '--mode', 'lines')
        status = self.wait(ident)
        self.assertEqual(status['returncode'], 0)
        self.assertEqual(status['bytes_read'], 60060)
        self.assertEqual((self.state / ident / 'stderr.log').stat().st_size, 30030)

    def test_event_limit_reserves_final_diagnostic(self):
        ident = self.start('import time\nwhile True:\n print("tick",flush=True)\n time.sleep(.15)',
                           '--mode', 'lines', '--max-events', '3')
        status = self.wait(ident)
        self.assertEqual(status['stop_reason'], 'event_limit')
        self.assertEqual(len(self.messages()), 3)
        self.assertIn('event_limit', self.messages()[-1])

    def test_cancel_during_delivery_is_uncertain_and_does_not_retry(self):
        self.env['TEST_QUEUE_DELAY'] = '10'
        ident = self.start('print("done")')
        self.wait(ident, lambda s: bool(self.messages()))
        self.cli('stop', ident)
        self.assertEqual(self.wait(ident)['delivery_state'], 'uncertain')
        self.assertEqual(len(self.messages()), 1)

    def test_delivery_timeout_is_bounded(self):
        self.env['TEST_QUEUE_DELAY'] = '10'
        ident = self.start('print("done")', '--delivery-timeout', '.15')
        self.assertEqual(self.wait(ident)['delivery_state'], 'uncertain')
        self.assertEqual(len(self.messages()), 1)

    def test_silent_success_still_has_completion(self):
        ident = self.start('pass')
        self.assertEqual(self.wait(ident)['bytes_read'], 0)
        self.assertEqual(len(self.messages()), 1)
        self.assertIn('completion', self.messages()[0])

    def test_arguments_are_not_interpreted_as_shell(self):
        marker = self.base / 'injected'
        literal = f'$(touch {marker}); echo oops'
        result = self.cli('start', '--thread', str(uuid.uuid4()), '--name', 'args',
                          '--codex', str(self.fake), '--', sys.executable, '-c',
                          'import sys; print(sys.argv[1])', literal)
        self.ids.append(result['id'])
        self.wait(result['id'])
        self.assertFalse(marker.exists())
        self.assertIn(literal, self.messages()[0])

    def test_normal_parent_exit_reaps_lingering_descendants(self):
        pidfile = self.base / 'descendant.pid'
        ident = self.start(f'import subprocess,pathlib\np=subprocess.Popen(["{sys.executable}","-c","import time; time.sleep(30)"])\npathlib.Path({str(pidfile)!r}).write_text(str(p.pid))')
        status = self.wait(ident)
        self.assertEqual(status['returncode'], 0)
        child = int(pidfile.read_text())
        result = subprocess.run(['ps', '-o', 'stat=', '-p', str(child)], capture_output=True, text=True)
        self.assertTrue(result.returncode != 0 or result.stdout.strip().startswith('Z'))

    def test_graceful_supervisor_termination_suppresses_wake(self):
        ident = self.start('import time; time.sleep(30)')
        info = self.wait(ident, lambda s: 'command_pid' in s)
        os.kill(info['supervisor_pid'], signal.SIGTERM)
        self.assertEqual(self.wait(ident)['process_state'], 'cancelled')
        self.assertEqual(self.messages(), [])

    def test_unsupported_codex_fails_before_running_command(self):
        self.fake.write_text('#!/bin/sh\nexit 2\n')
        result = self.cli('start', '--thread', str(uuid.uuid4()), '--name', 'unsupported',
                          '--codex', str(self.fake), '--', 'true', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not support queue', result.stderr)
        self.assertFalse(list(self.state.glob('*/config.json')))

    def test_private_directory_and_path_traversal_rejected(self):
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)
        self.assertIn('mode 700', self.cli('list', ok=False).stderr)
        self.state.chmod(0o700)
        self.assertIn('invalid monitor ID', self.cli('status', '../other', ok=False).stderr)

    def test_worker_cannot_replay_a_completed_command(self):
        ident = self.start('print("one execution")')
        self.wait(ident)
        result = self.cli('_run', ident, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.messages()), 1)

    def test_retry_worker_preserves_a_new_stop_request(self):
        from openmonitor.supervisor import Supervisor
        self.env['TEST_QUEUE_EXIT'] = '3'
        ident = self.start('print("done")')
        self.wait(ident)
        self.cli('stop', ident)
        supervisor = Supervisor(self.state / ident)
        with mock.patch('openmonitor.supervisor.submit', return_value=('queued', '')) as submit:
            supervisor.retry()
        submit.assert_not_called()
        self.assertTrue((self.state / ident / 'stop.json').exists())

    def test_delivery_survives_deleted_command_working_directory(self):
        working = self.base / 'disappearing'
        working.mkdir()
        result = subprocess.run([sys.executable, '-m', 'openmonitor', '--state-dir', str(self.state),
                                 'start', '--thread', str(uuid.uuid4()), '--name', 'deleted-cwd',
                                 '--codex', str(self.fake), '--', sys.executable, '-c',
                                 'import os; os.rmdir(os.getcwd())'], cwd=working, env=self.env,
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        ident = json.loads(result.stdout)['id']
        self.ids.append(ident)
        status = self.wait(ident)
        self.assertEqual(status['returncode'], 0)
        self.assertEqual(status['delivery_state'], 'queued')


if __name__ == '__main__':
    unittest.main()
