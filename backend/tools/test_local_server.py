"""Responsibility: Verify lock, configuration-protection, detached-session, and cooperative-stop boundaries of the Windows/macOS/POSIX local launcher.
Implementation: Use the standard library, isolated temporary directories, and real lightweight child processes; POSIX additionally executes the Bash entry point and empty-dependency virtual-environment setup.
Relationships: Tests `local_server.py` and `start-local.sh`; Django and Homebrew calls use explicit mocks and never access business databases or external services.

Directory:
- LocalServerTests: Collection of launcher-boundary tests.
- LocalServerTests.test_lock_releases: Check the exclusive lock and normal release.
- LocalServerTests.test_missing_env_generates_template: Check initial template creation and explicit failure.
- LocalServerTests.test_existing_env_is_not_overwritten: Check that existing configuration is not replaced.
- LocalServerTests.test_corrupt_state_is_not_ignored: Check that a corrupt state preserves error semantics.
- LocalServerTests.test_stop_dispatches_sigterm: Check that a stop file triggers the child main-thread SIGTERM handler.
- LocalServerTests.test_lock_visible_to_other_process: Verify that separate processes contend for the same runtime lock.
- LocalServerTests.test_spawn_detaches_session: Verify logs and POSIX session isolation, including directories with spaces.
- LocalServerTests.test_database_options_are_platform_specific: Check platform, mutual-exclusion, and Homebrew service-name constraints.
- LocalServerTests.test_brew_run_existing_service: Mock and verify explicit Homebrew execution while preserving migration calls and existing configuration.
- LocalServerTests.test_shell_setup_and_actions: Verify initialization, reuse, and action forwarding in the Bash 3.2-compatible script with empty dependencies and a controlled entry point.
- LocalServerTests.test_shell_rejects_foreign_venv: Verify that the shell does not overwrite a virtual environment from another platform.
- LocalServerTests.test_start_reports_current_failure: Verify that startup failures show diagnostics from the current run while excluding historical errors.

Variable index:
- None
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch

import local_server


# Function: Verify launcher control boundaries in isolated directories.
# Logic: Mock Django and Homebrew, while validating locks, stop signals, session isolation, and shell entry points through real OS behavior.
# Constraints: Do not start business services or read the repository `.env`; empty-dependency shell tests cannot prove full dependencies install on that platform.
class LocalServerTests(unittest.TestCase):
    # Function: Verify that a terminal shows the current safe reason when background startup fails and does not misreport old state.
    # Inputs: No external parameters; mocks an exited supervisor, current and historical state, and a fixed time.
    # Outputs: Asserts that the current failure detail is visible and historical detail is absent from the terminal exception.
    # Logic: Use the real `control` path while mocking process exit without starting an actual service.
    # Constraints: Do not read logs or `.env` or access a database; this test verifies diagnostic propagation only.
    def test_start_reports_current_failure(self):
        args = SimpleNamespace(action='start', wsl_distro=None, brew_service=None, no_browser=True)
        process = Mock()
        process.poll.return_value = 1
        for timestamp, expected in ((101, 'Configure DATABASE_URL'), (99, 'before a current diagnostic')):
            state = {'status': 'failed', 'updated_at': timestamp, 'detail': 'Configure DATABASE_URL'}
            with self.subTest(timestamp=timestamp), patch('local_server.running', return_value=False), \
                    patch('local_server.spawn', return_value=process), patch('local_server.read_state', return_value=state), \
                    patch('local_server.time.time', return_value=100):
                with self.assertRaisesRegex(RuntimeError, expected) as caught:
                    local_server.control(args)
                if timestamp < 100:
                    self.assertNotIn('Configure DATABASE_URL', str(caught.exception))

    # Function: Verify that concurrent-start protection is removed after the handle closes.
    # Inputs: No external parameters; creates an independent temporary runtime directory.
    # Outputs: Asserts lock-held and lock-released states.
    # Logic: Actually acquire the current-platform file lock and use `running` to detect contention.
    # Constraints: Release the handle in `finally`; do not touch the real runtime directory.
    def test_lock_releases(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(local_server, 'RUNTIME', Path(directory)):
            self.assertFalse(local_server.running())
            handle = local_server.lock_runtime()
            try:
                self.assertTrue(local_server.running())
            finally:
                handle.close()
            self.assertFalse(local_server.running())

    # Function: Verify that initial template creation does not pretend the database is configured.
    # Inputs: No external parameters; the temporary directory contains only a controlled template.
    # Outputs: Asserts that a random key is generated and database configuration is explicitly required.
    # Logic: Call the unconfigured `prepare` path and prevent Django initialization.
    # Constraints: No database, WSL, or network access.
    def test_missing_env_generates_template(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(local_server, 'ROOT', Path(directory)):
            root = Path(directory)
            (root / '.env.example').write_text('DJANGO_SECRET_KEY=replace-with-a-local-random-string\nDATABASE_URL=placeholder\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'Configure DATABASE_URL'):
                local_server.prepare(None)
            text = (root / '.env').read_text(encoding='utf-8')
            self.assertNotIn('replace-with-a-local-random-string', text)
            self.assertIn('DATABASE_URL=placeholder', text)

    # Function: Verify that an existing `.env` is preserved byte-for-byte when initialization fails.
    # Inputs: No external parameters; uses fabricated configuration and failed Django setup.
    # Outputs: Asserts unchanged original bytes and propagated error.
    # Logic: Use a mocked Django module to stop initialization after reading existing configuration, isolating external services and third-party-package installation requirements.
    # Constraints: Verify file protection only and do not prove database validity; restore modules, import paths, and environment.
    def test_existing_env_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(local_server, 'ROOT', Path(directory)), \
                patch.dict(sys.modules, {'django': Mock(setup=Mock(side_effect=RuntimeError('setup boundary')))}), \
                patch.object(sys, 'path', list(sys.path)), patch.dict(os.environ):
            path = Path(directory) / '.env'
            original = b'DATABASE_URL=existing\nANALYSIS_PROVIDER=agent\n'
            path.write_bytes(original)
            with self.assertRaisesRegex(RuntimeError, 'setup boundary'):
                local_server.prepare(None)
            self.assertEqual(path.read_bytes(), original)

    # Function: Verify that a corrupt state file is not treated as stopped.
    # Inputs: No external parameters; a temporary state file contains invalid JSON.
    # Outputs: Asserts `ValueError`.
    # Logic: Read corrupt state directly without mocking the parser.
    # Constraints: Verify the diagnostic boundary only and do not modify real state.
    def test_corrupt_state_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(local_server, 'RUNTIME', Path(directory)):
            (Path(directory) / 'state.json').write_text('{broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                local_server.read_state()

    # Function: Verify that a current-platform background child process cooperatively responds to a stop file.
    # Inputs: No external parameters; the temporary directory contains a pre-created stop request.
    # Outputs: Asserts that the child exits normally within five seconds and runs its SIGTERM callback.
    # Logic: Use actual background-process options; the child registers a signal handler and `watch_stop`, while its main thread waits for the callback.
    # Constraints: Do not run a Worker or issue business requests; a test timeout affects only its own test process.
    def test_stop_dispatches_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'probe.stop').touch()
            code = """
import pathlib, signal, sys, threading, time
import local_server
local_server.RUNTIME = pathlib.Path(sys.argv[1])
done = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: done.set())
threading.Thread(target=local_server.watch_stop, args=('probe',), daemon=True).start()
while not done.is_set():
    time.sleep(0.05)
print('graceful-stop-observed')
"""
            result = subprocess.run([sys.executable, '-c', code, directory], cwd=Path(__file__).parent,
                                    capture_output=True, text=True, timeout=5,
                                    **local_server.background_options())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('graceful-stop-observed', result.stdout)

    # Function: Verify that the exclusive lock affects other processes rather than only the current process.
    # Inputs: No external parameters; an isolated temporary directory and lightweight child process.
    # Outputs: Asserts that another process reads running while held and stopped after release.
    # Logic: The child actually imports the same module and attempts to lock the same file.
    # Constraints: No business database or service process; release the handle in `finally`.
    def test_lock_visible_to_other_process(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(local_server, 'RUNTIME', Path(directory)):
            code = 'import pathlib,sys,local_server; local_server.RUNTIME=pathlib.Path(sys.argv[1]); print(local_server.running())'
            command = [sys.executable, '-c', code, directory]
            handle = local_server.lock_runtime()
            try:
                held = subprocess.run(command, cwd=Path(__file__).parent, capture_output=True, text=True, timeout=5)
            finally:
                handle.close()
            released = subprocess.run(command, cwd=Path(__file__).parent, capture_output=True, text=True, timeout=5)
            self.assertEqual(held.returncode, 0, held.stderr)
            self.assertEqual(released.returncode, 0, released.stderr)
            self.assertEqual(held.stdout.strip(), 'True')
            self.assertEqual(released.stdout.strip(), 'False')

    # Function: Verify cross-platform `spawn` path passing, logs, and POSIX independent sessions.
    # Inputs: No external parameters; a temporary directory with spaces and a test entry point that outputs process information only.
    # Outputs: Asserts child success and argument fidelity; on POSIX, the session ID equals the child PID.
    # Logic: Replace the script path and call the real `spawn`, using OS results to verify process isolation.
    # Constraints: Run only the test script and do not import Django or start a server.
    def test_spawn_detaches_session(self):
        with tempfile.TemporaryDirectory(prefix='salesmate launch ') as directory:
            root = Path(directory)
            script = root / 'probe script.py'
            script.write_text('import os,sys,json; print(json.dumps({"pid":os.getpid(), "sid":os.getsid(0) if os.name!="nt" else None, "args":sys.argv[1:]}))', encoding='utf-8')
            with patch.object(local_server, 'ROOT', root), patch.object(local_server, 'RUNTIME', root), \
                    patch.object(local_server, '__file__', str(script)):
                process = local_server.spawn(['value with spaces'], 'probe')
                self.assertEqual(process.wait(timeout=5), 0)
            result = json.loads((root / 'probe.log').read_text(encoding='utf-8'))
            self.assertEqual(result['args'], ['value with spaces'])
            if os.name != 'nt':
                self.assertEqual(result['sid'], result['pid'])

    # Function: Verify that WSL and Homebrew options are limited to their explicitly supported platforms.
    # Inputs: No external parameters; mocks `sys.platform` only.
    # Outputs: Asserts errors for invalid combinations and validation for a valid PostgreSQL formula.
    # Logic: Cover service-name injection, cross-platform misuse, and mutual-exclusion conditions.
    # Constraints: Do not execute real Homebrew or WSL and do not represent macOS services as verified.
    def test_database_options_are_platform_specific(self):
        with patch.object(sys, 'platform', 'darwin'):
            local_server.validate_database_options(None, 'postgresql@16')
            for formula in ('redis', '--all', 'postgresql@16;touch x'):
                with self.assertRaises(RuntimeError):
                    local_server.validate_database_options(None, formula)
            with self.assertRaisesRegex(RuntimeError, 'only on Windows'):
                local_server.validate_database_options('Ubuntu', None)
        with patch.object(sys, 'platform', 'win32'):
            local_server.validate_database_options('Ubuntu', None)
            with self.assertRaisesRegex(RuntimeError, 'only on macOS'):
                local_server.validate_database_options(None, 'postgresql@16')
        with self.assertRaisesRegex(RuntimeError, 'cannot be combined'):
            local_server.validate_database_options('Ubuntu', 'postgresql@16')

    # Function: Verify that explicit Homebrew startup neither installs software nor registers login startup.
    # Inputs: No external parameters; mocks local rules configuration, Django connections, and the brew executable path.
    # Outputs: Asserts the `services run` call, preserved existing configuration, and original `check`/`migrate` execution; PostgreSQL services include graph.
    # Logic: Mock a ready connection while using the real `prepare` control flow and preventing access to an existing database.
    # Constraints: This test verifies the call contract only and does not prove Homebrew, pgvector, or database migrations actually succeed.
    def test_brew_run_existing_service(self):
        settings = Mock(DEBUG=True, TASK_EXECUTION_MODE='local', ANALYSIS_PROVIDER='rules',
                        LOCAL_DEBUG_AUTO_LOGIN=False,
                        DATABASES={'default': {'ENGINE': 'django.db.backends.postgresql', 'HOST': '127.0.0.1', 'PORT': 5432}})
        connection = MagicMock()
        command = Mock()
        modules = {'django': Mock(), 'django.conf': Mock(settings=settings),
                   'django.core.management': Mock(call_command=command),
                   'django.db': Mock(connections=connection), 'django.contrib.auth': Mock()}
        with tempfile.TemporaryDirectory() as directory, patch.object(local_server, 'ROOT', Path(directory)), \
                patch.dict(sys.modules, modules), patch.object(sys, 'path', list(sys.path)), patch.dict(os.environ), \
                patch.object(sys, 'platform', 'darwin'), patch('local_server.shutil.which', return_value='/opt/homebrew/bin/brew'), \
                patch('local_server.subprocess.run') as run, patch('local_server.socket.create_connection', return_value=MagicMock()):
            path = Path(directory) / '.env'
            path.write_text('existing settings', encoding='utf-8')
            self.assertEqual(local_server.prepare(None, 'postgresql@16'), ['web', 'sales', 'graph'])
            run.assert_called_once_with(['/opt/homebrew/bin/brew', 'services', 'run', 'postgresql@16'], check=True, timeout=90)
            self.assertEqual(path.read_text(encoding='utf-8'), 'existing settings')
            self.assertEqual(command.call_args_list[0].args, ('check',))
            self.assertEqual(command.call_args_list[1].args, ('migrate',))

    # Function: Verify that the shell entry point initializes a virtual environment from a directory with spaces and correctly forwards every action.
    # Inputs: No external parameters; on POSIX, an isolated copy, empty dependency lists, and a fake business entry point that records argv only.
    # Outputs: Asserts initial environment creation, installation-summary reuse on the second startup, and correct arguments for status and stop.
    # Logic: Actually execute `/bin/bash` with venv and pip, using the system Bash on macOS and `PIP_NO_INDEX` to avoid external downloads.
    # Constraints: Skip on Windows; do not copy `.env` or install actual business dependencies; each subcommand runs at most 90 seconds.
    @unittest.skipIf(os.name == 'nt', 'Bash/POSIX entry is exercised on macOS and Linux.')
    def test_shell_setup_and_actions(self):
        with tempfile.TemporaryDirectory(prefix='salesmate shell ') as directory:
            root = Path(directory)
            shutil.copyfile(local_server.ROOT / 'start-local.sh', root / 'start-local.sh')
            for name in ('requirements.txt', 'backend/requirements/dev.txt', 'backend/requirements/base.txt', 'agent/requirements.txt'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# empty isolated dependency fixture\n', encoding='utf-8')
            launcher = root / 'backend/tools/local_server.py'
            launcher.parent.mkdir(parents=True)
            launcher.write_text('import json,sys; print("ARGS="+json.dumps(sys.argv[1:]))', encoding='utf-8')
            environment = dict(os.environ, PIP_NO_INDEX='1')
            start = ['/bin/bash', str(root / 'start-local.sh'), 'start', '--python', sys.executable, '--no-browser']
            initial = subprocess.run(start, cwd=directory, env=environment, capture_output=True, text=True, timeout=90)
            self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
            self.assertIn('ARGS=["start", "--no-browser"]', initial.stdout)
            installed = (root / '.venv/salesmate-install.log').stat().st_mtime_ns
            repeated = subprocess.run(start, cwd=directory, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
            self.assertEqual((root / '.venv/salesmate-install.log').stat().st_mtime_ns, installed)
            for action in ('status', 'stop'):
                result = subprocess.run(['/bin/bash', str(root / 'start-local.sh'), action], cwd=directory,
                                        env=environment, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'ARGS=["{action}"]', result.stdout)

    # Function: Verify that the shell entry point does not layer a POSIX environment over a Windows virtual environment.
    # Inputs: No external parameters; a Windows-style marker file in a POSIX temporary directory.
    # Outputs: Asserts nonzero exit and unchanged original environment file.
    # Logic: The system `/bin/bash` detects the incompatible directory and stops before environment creation.
    # Constraints: Skip on Windows; no dependency installation or database operations.
    @unittest.skipIf(os.name == 'nt', 'Bash/POSIX entry is exercised on macOS and Linux.')
    def test_shell_rejects_foreign_venv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(local_server.ROOT / 'start-local.sh', root / 'start-local.sh')
            marker = root / '.venv/Scripts/python.exe'
            marker.parent.mkdir(parents=True)
            marker.write_bytes(b'keep existing environment')
            result = subprocess.run(['/bin/bash', str(root / 'start-local.sh')], capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('.venv exists', result.stderr)
            self.assertEqual(marker.read_bytes(), b'keep existing environment')


if __name__ == '__main__':
    unittest.main()
