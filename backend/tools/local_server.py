"""Responsibility: Manage Windows/macOS local SalesMate environment checks and the Web/Worker lifecycle.
Implementation: Reuse the root `.env`, optionally start WSL or Homebrew PostgreSQL, manage the graph Worker in PostgreSQL mode, and apply platform-specific locking, detached background processes, and readiness checks.
Relationships: `start-local.ps1` and `start-local.sh` prepare dependencies; existing management commands retain their task arguments and SIGTERM drain semantics; this module neither installs databases nor imports demo data.

Directory:
- emit: Write phase messages without configuration secrets.
- background_options: Choose Windows hidden-window or POSIX detached-session parameters.
- validate_database_options: Validate platform and service-name boundaries for database startup parameters.
- lock_runtime: Acquire the exclusive Windows/POSIX runtime-directory lock.
- running: Read-only check of whether the supervisor lock is held.
- read_state: Read the atomically published state file.
- write_state: Update the state and managed-process information.
- prepare: Check configuration, database, migrations, and local user.
- healthy: Check Web, database, and static-resource responses.
- watch_stop: Convert a stop file into SIGTERM for the main thread.
- child: Run the unchanged Web or management-command entry point.
- spawn: Start this script's specified subcommand in a hidden process.
- supervise: Start, monitor, and drain services sequentially while holding the lock.
- control: Execute the user's start, status, or stop request.
- main: Parse the command line and provide a redacted error boundary.

Variable index:
- ROOT: Absolute repository path, independent of the caller's working directory.
- RUNTIME: Git-ignored directory for state and logs.
- URL: Local Web endpoint retaining existing OAuth and Agent addresses.
- SERVICES: Entry-point mapping for Web, existing Workers, and the graph Worker, preserving their established defaults.
"""

import _thread
import argparse
import errno
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'artifacts' / 'local-server'
URL = 'http://127.0.0.1:8000'
SERVICES = {
    'web': ('uvicorn', ['config.asgi:application', '--host', '127.0.0.1', '--port', '8000']),
    'crm': ('manage', ['crm_worker']),
    'chat': ('manage', ['chat_worker']),
    'sales': ('manage', ['sales_worker']),
    'graph': ('manage', ['graph_worker']),
}


# Function: Write an identifiable startup phase.
# Inputs: `message` is redacted text.
# Outputs: No return value; writes and immediately flushes standard output.
# Logic: Use a fixed prefix so launcher messages are recognizable in background logs.
# Constraints: Callers must not pass secrets, database URLs, or raw exceptions.
def emit(message):
    print(f'[local] {message}', flush=True)


# Function: Choose the background-process isolation method for the current platform.
# Inputs: No external parameters; reads `os.name`.
# Outputs: A keyword dictionary for expansion into `Popen` or `run`.
# Logic: Windows uses `CREATE_NO_WINDOW`; POSIX creates an independent session to avoid terminal-hangup signals when the launch terminal closes.
# Constraints: Platform branches are explicit implementations rather than fallback after failure; callers still redirect standard streams.
def background_options():
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {'start_new_session': True}


# Function: Validate database-startup options before changing any service.
# Inputs: `distro` is a WSL distribution or no value; `brew_service` is a Homebrew PostgreSQL formula name or no value.
# Outputs: None; raises `RuntimeError` for invalid values.
# Logic: Restrict WSL to Windows and Homebrew to macOS, make the options mutually exclusive, and accept only PostgreSQL formula names.
# Constraints: Do not execute external commands or automatically detect or choose a database version.
def validate_database_options(distro, brew_service):
    if distro and brew_service:
        raise RuntimeError('--wsl-distro and --brew-service cannot be combined.')
    if distro and sys.platform != 'win32':
        raise RuntimeError('--wsl-distro is available only on Windows.')
    if brew_service and sys.platform != 'darwin':
        raise RuntimeError('--brew-service is available only on macOS.')
    if brew_service and not re.fullmatch(r'postgresql(?:@[0-9]+)?', brew_service):
        raise RuntimeError('--brew-service must be an installed PostgreSQL formula, e.g. postgresql@16.')


# Function: Acquire the supervisor's exclusive file lock.
# Inputs: No external parameters; reads the RUNTIME directory.
# Outputs: Returns a file handle that must remain open; raises `OSError` on contention.
# Logic: Windows locks the first byte and macOS/POSIX uses `flock`; the operating system releases the lock when the handle closes or the process exits.
# Constraints: Do not kill a process based on a potentially reused PID; import platform-specific modules only in their matching branches.
def lock_runtime():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    handle = (RUNTIME / 'run.lock').open('a+b')
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise
    return handle


# Function: Determine whether a supervisor already holds the lock.
# Inputs: No external parameters; reads the runtime-directory file lock.
# Outputs: A Boolean value.
# Logic: Briefly attempt the same lock and use current-platform errno constants to identify contention, including macOS's `EAGAIN` number.
# Constraints: Do not start or stop processes; propagate other filesystem errors.
def running():
    try:
        handle = lock_runtime()
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return True
        raise
    handle.close()
    return False


# Function: Read the published runtime state.
# Inputs: No external parameters; reads `RUNTIME/state.json`.
# Outputs: A state dictionary; returns `stopped` when the file does not yet exist.
# Logic: The supervisor uses atomic replacement, so readers never observe a partial JSON document.
# Constraints: Report a corrupt file explicitly rather than silently ignoring it.
def read_state():
    path = RUNTIME / 'state.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'status': 'stopped'}


# Function: Publish a startup or shutdown phase and child-process identities.
# Inputs: `status` is a phase string; `processes` maps names to Popen objects; `detail` is redacted diagnostic text.
# Outputs: None; atomically replaces the state file.
# Logic: PIDs and return codes are diagnostic only; write an update time so launchers reject historical failures, while control still operates through stop files and process handles.
# Constraints: Do not record command environments or business data; only the supervisor writes state.
def write_state(status, processes, detail=''):
    data = {'status': status, 'url': URL, 'detail': detail, 'updated_at': time.time(),
            'processes': {name: {'pid': process.pid, 'exit_code': process.poll()}
                          for name, process in processes.items()}}
    temporary = RUNTIME / 'state.tmp'
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(RUNTIME / 'state.json')
    emit(f'{status}: {detail}')


# Function: Check existing configuration and run authorized local database migrations.
# Inputs: `distro` is an explicit WSL distribution name or no value; `brew_service` is an explicit Homebrew formula or no value; reads the root `.env` and process environment.
# Outputs: A list of service names to start; raises `RuntimeError` when prerequisites are unmet.
# Logic: Validate platform options; generate only a template when configuration is missing; start the database through an explicit option and check migrations; add the graph Worker for PostgreSQL.
# Constraints: Do not overwrite `.env` or reset accounts or providers; allow only local PostgreSQL or explicit SQLite; Homebrew `run` neither registers a login startup item nor installs software.
def prepare(distro, brew_service=None):
    validate_database_options(distro, brew_service)
    env_path = ROOT / '.env'
    if not env_path.exists():
        template = (ROOT / '.env.example').read_text(encoding='utf-8')
        template = template.replace('replace-with-a-local-random-string', secrets.token_urlsafe(48))
        with env_path.open('x', encoding='utf-8') as handle:
            handle.write(template)
        raise RuntimeError('Created .env with a random Django key. Configure DATABASE_URL before starting; existing databases are never replaced.')
    sys.path.insert(0, str(ROOT / 'backend'))
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections
    from django.contrib.auth import get_user_model

    if not settings.DEBUG or settings.TASK_EXECUTION_MODE != 'local':
        raise RuntimeError('This launcher requires DEBUG and TASK_EXECUTION_MODE=local; production settings are not changed.')
    database = settings.DATABASES['default']
    engine = database['ENGINE']
    if engine.endswith('postgresql'):
        if database.get('HOST') not in ('localhost', '127.0.0.1', '::1'):
            raise RuntimeError('Only loopback PostgreSQL is supported by this local launcher.')
    elif not engine.endswith('sqlite3'):
        raise RuntimeError('Unsupported local database engine; DATABASE_URL is not changed.')
    if settings.ANALYSIS_PROVIDER == 'agent':
        if not os.getenv('DASHSCOPE_API_KEY') or not os.getenv('BAILIAN_MODEL'):
            raise RuntimeError('Agent mode requires DASHSCOPE_API_KEY and BAILIAN_MODEL in .env. No rules fallback is applied.')
        if os.getenv('SALESMATE_BACKEND_AGENT_URL', URL + '/api/v1/agent/').rstrip('/') != URL + '/api/v1/agent':
            raise RuntimeError('SALESMATE_BACKEND_AGENT_URL must point to this launcher at 127.0.0.1:8000.')
    if distro or brew_service:
        if not engine.endswith('postgresql'):
            raise RuntimeError('Database startup options require a configured PostgreSQL database.')
    if distro:
        emit(f'Starting PostgreSQL in explicitly selected WSL distribution: {distro}')
        subprocess.run(['wsl.exe', '-d', distro, '-u', 'root', '--', 'service', 'postgresql', 'start'],
                       check=True, timeout=90)
    if brew_service:
        brew = shutil.which('brew')
        if not brew:
            raise RuntimeError('Homebrew is not on PATH. Configure your shell or start the database yourself.')
        emit(f'Starting explicitly selected Homebrew PostgreSQL service: {brew_service}')
        subprocess.run([brew, 'services', 'run', brew_service], check=True, timeout=90)
    if distro or brew_service:
        # The listener or WSL forwarding may still be unavailable after the service command returns; wait only for TCP and do not retry business operations.
        deadline = time.monotonic() + 30
        while True:
            try:
                with socket.create_connection((database['HOST'], int(database.get('PORT') or 5432)), timeout=1):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Local PostgreSQL is not reachable. Check the selected service, port and WSL forwarding if applicable.') from None
                time.sleep(0.5)
    emit('Checking database and Django configuration; applying pending migrations.')
    connection = connections['default']
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            if engine.endswith('postgresql'):
                cursor.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
                if cursor.fetchone() is None:
                    raise RuntimeError('PostgreSQL requires the pgvector extension package before migrations.')
        call_command('check')
        call_command('migrate', interactive=False)
        if settings.LOCAL_DEBUG_AUTO_LOGIN:
            if not get_user_model().objects.filter(username=settings.LOCAL_DEBUG_USER, is_active=True,
                                                   is_staff=False, is_superuser=False).exists():
                raise RuntimeError('Configured local auto-login user is missing or privileged. Run provision_local with your chosen mailbox, or explicitly disable auto-login.')
    finally:
        connections.close_all()
    # Graph capture depends on PostgreSQL; SQLite previews retain existing services, and the graph does not require an Agent or LLM.
    services = ['web', 'crm', 'chat', 'sales'] if settings.ANALYSIS_PROVIDER == 'agent' else ['web', 'sales']
    return services + (['graph'] if engine.endswith('postgresql') else [])


# Function: Verify that local Web, database, and module resources are ready.
# Inputs: No external parameters; uses the fixed URL.
# Outputs: Returns `True` when every probe passes, otherwise `False`.
# Logic: Bypass system proxies for loopback access and check health JSON and the JavaScript MIME type.
# Constraints: GET requests only and no business writes; each connection waits at most two seconds, while the caller bounds the overall readiness window.
def healthy():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for path in ('/api/v1/health/live/', '/api/v1/health/ready/', '/static/app.js'):
            with opener.open(URL + path, timeout=2) as response:
                if path.endswith('.js'):
                    if 'javascript' not in response.headers.get('Content-Type', ''):
                        return False
                elif json.load(response).get('status') != 'ok':
                    return False
        return True
    except (OSError, ValueError, urllib.error.URLError):
        return False


# Function: Hand a cross-platform file-control request to the existing service SIGTERM handler.
# Inputs: `name` is a service name; reads its corresponding stop file.
# Outputs: None; delivers one SIGTERM to the Python main thread.
# Logic: A background thread waits for the stop file and for the entry point to register its signal handler, then uses `interrupt_main` to schedule that handler.
# Constraints: Do not force-kill or retry tasks; long tasks may delay response while the existing Worker completes its current work unit.
def watch_stop(name):
    while True:
        if (RUNTIME / f'{name}.stop').exists() and callable(signal.getsignal(signal.SIGTERM)):
            _thread.interrupt_main(signal.SIGTERM)
            return
        time.sleep(0.25)


# Function: Run the unmodified Web or management-command entry point.
# Inputs: `name` is a service name from SERVICES.
# Outputs: Returns `None` on normal completion; preserves the service's original exit behavior on error.
# Logic: Install a stop-file watcher and set the import path and `argv` required by the original entry point.
# Constraints: Invoked by the supervisor in a detached hidden process; no automatic restart or task fallback.
def child(name):
    os.chdir(ROOT)
    sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
    module, arguments = SERVICES[name]
    sys.argv = [module, *arguments]
    threading.Thread(target=watch_stop, args=(name,), daemon=True).start()
    if module == 'manage':
        runpy.run_path(str(ROOT / 'backend' / 'manage.py'), run_name='__main__')
    else:
        runpy.run_module(module, run_name='__main__', alter_sys=True)


# Function: Start a Python process with an independent log and detached from the launch terminal.
# Inputs: `arguments` are this script's subcommand arguments; `name` is the log name.
# Outputs: A `Popen` object.
# Logic: Pass the argv list directly, redirect standard streams to a file, and hide the window or create an independent POSIX session by platform.
# Constraints: Windows, macOS, and POSIX use the same service entry point; do not record the environment; rewrite the log on every startup.
def spawn(arguments, name):
    with (RUNTIME / f'{name}.log').open('w', encoding='utf-8') as log:
        return subprocess.Popen([sys.executable, '-X', 'utf8', '-u', str(Path(__file__).resolve()), *arguments], cwd=ROOT,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                **background_options())


# Function: Manage startup, monitoring, and cooperative shutdown while holding the lock.
# Inputs: `distro` is an explicit database WSL distribution or no value; `brew_service` is a macOS Homebrew PostgreSQL formula or no value.
# Outputs: Returns 0 after normal shutdown and 1 after initialization or runtime failure.
# Logic: Validate the platform and port first; WSL keeps a stdin session while Homebrew startup is delegated to `prepare`; start selected Workers after Web readiness, and drain the graph Worker together with existing Workers.
# Constraints: Do not terminate other processes, issue a PostgreSQL stop command, or automatically restart; a distribution may sleep after its final WSL session is released.
def supervise(distro, brew_service=None):
    validate_database_options(distro, brew_service)
    handle = lock_runtime()
    processes = {}
    failure = ''
    try:
        for name in ('all', *SERVICES):
            (RUNTIME / f'{name}.stop').unlink(missing_ok=True)
        write_state('starting', processes)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 8000))
        if distro:
            # A systemd service does not itself keep WSL running; the supervisor holds the pipe, and `cat` exits normally when it closes.
            processes['database-session'] = subprocess.Popen(
                ['wsl.exe', '-d', distro, '--', 'cat'], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
        services = prepare(distro, brew_service)
        if (RUNTIME / 'all.stop').exists():
            return 0
        processes['web'] = spawn(['child', '--service', 'web'], 'web')
        deadline = time.monotonic() + 60
        while not healthy():
            if processes['web'].poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Web readiness failed. Inspect web.log; no Worker was started.')
            if (RUNTIME / 'all.stop').exists():
                return 0
            time.sleep(0.5)
        for name in services[1:]:
            processes[name] = spawn(['child', '--service', name], name)
        # Observe initial import and configuration failures, then keep supervising; acquiring a PID alone does not prove continued successful operation.
        time.sleep(2)
        if any(process.poll() is not None for process in processes.values()):
            raise RuntimeError('A service exited during initialization. Inspect service logs.')
        write_state('running', processes, 'Services started; runtime supervision remains active.')
        while not (RUNTIME / 'all.stop').exists():
            exited = [name for name, process in processes.items() if process.poll() is not None]
            if exited:
                raise RuntimeError(f'Service exited unexpectedly: {", ".join(exited)}. Inspect its log; no automatic restart.')
            time.sleep(0.5)
    except Exception as error:
        failure = str(error) if isinstance(error, RuntimeError) else f'{type(error).__name__}; inspect setup/service logs and configured local dependencies.'
        emit(f'Failed: {failure}')
    finally:
        write_state('stopping', processes, failure)
        for name in processes:
            if name in ('crm', 'chat', 'sales', 'graph'):
                (RUNTIME / f'{name}.stop').touch()
        for name, process in processes.items():
            if name in ('crm', 'chat', 'sales', 'graph'):
                process.wait()
        if 'web' in processes:
            (RUNTIME / 'web.stop').touch()
            processes['web'].wait()
        if 'database-session' in processes:
            processes['database-session'].stdin.close()
            processes['database-session'].wait()
        write_state('failed' if failure else 'stopped', processes, failure)
        handle.close()
    return 1 if failure else 0


# Function: Execute a user-facing start, status, or stop command.
# Inputs: `args` contains action, wsl_distro, brew_service, and no_browser attributes; reads runtime locks and state.
# Outputs: Returns the command exit code; startup may open a browser.
# Logic: Reuse managed services for repeated starts; hand database options to a cold start and show the redacted cause from this run's state when its child fails; use bounded waiting for stop.
# Constraints: Do not read or print full logs, credentials, or historical failure causes; a wait timeout does not silently cancel or force-kill, and status probes do not modify business data.
def control(args):
    active = running()
    if args.action == 'status':
        state = read_state()
        state['supervisor_active'] = active
        state['healthy'] = active and state['status'] == 'running' and healthy()
        print(json.dumps(state, indent=2))
        return 0 if state['healthy'] else 1
    if args.action == 'stop':
        if not active:
            emit('No managed server is running.')
            return 0
        (RUNTIME / 'all.stop').touch()
        deadline = time.monotonic() + 30
        while running() and time.monotonic() < deadline:
            time.sleep(0.5)
        if running():
            emit('Stop requested; current work is still draining. Check status/logs; nothing was force-killed.')
            return 1
        emit('Stopped Web and Workers; released any managed WSL session. PostgreSQL was not stopped explicitly.')
        return 0
    if active:
        state = read_state()
        if state['status'] != 'running' or not healthy():
            raise RuntimeError(f'Managed supervisor is {state["status"]}; inspect status and logs before starting again.')
        emit('Reusing the existing managed server.')
    else:
        arguments = ['serve']
        if args.wsl_distro:
            arguments += ['--wsl-distro', args.wsl_distro]
        if args.brew_service:
            arguments += ['--brew-service', args.brew_service]
        started_at = time.time()
        process = spawn(arguments, 'launcher')
        deadline = time.monotonic() + 180
        while True:
            if process.poll() is not None:
                state = read_state()
                reason = state.get('detail', '') if state.get('status') == 'failed' and state.get('updated_at', 0) >= started_at else ''
                message = f'Start failed: {reason}' if reason else 'Start failed before a current diagnostic was recorded.'
                raise RuntimeError(f'{message} Inspect {RUNTIME / "launcher.log"}.')
            if running() and read_state()['status'] == 'running' and healthy():
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Startup is not ready after 180 seconds. Inspect status/logs or run stop; initialization may still be running.')
            time.sleep(0.5)
    emit(f'Ready: {URL}/ ; logs: {RUNTIME}')
    if not args.no_browser:
        webbrowser.open(URL + '/')
    return 0


# Function: Parse the CLI and limit launcher error output.
# Inputs: No external parameters; reads `sys.argv`.
# Outputs: Returns an exit code; `argparse` reports argument errors.
# Logic: Private `serve` and `child` subcommands reuse this file; validate startup options for platform and mutual exclusion before delegating user commands to `control`.
# Constraints: Invalid platform arguments fail before writing state or configuration; show only controlled `RuntimeError` messages verbatim, show only types for other exceptions, and retain business exceptions in private logs.
def main():
    parser = argparse.ArgumentParser(description='Manage the local SalesMate workspace on Windows and macOS.')
    parser.add_argument('action', choices=('start', 'status', 'stop', 'serve', 'child'))
    parser.add_argument('--wsl-distro')
    parser.add_argument('--brew-service')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--service', choices=tuple(SERVICES))
    args = parser.parse_args()
    if args.action == 'child':
        if not args.service:
            parser.error('child requires --service')
        child(args.service)
        return 0
    try:
        validate_database_options(args.wsl_distro, args.brew_service)
        if args.action == 'serve':
            return supervise(args.wsl_distro, args.brew_service)
        return control(args)
    except Exception as error:
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        emit(f'ERROR: {detail}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
