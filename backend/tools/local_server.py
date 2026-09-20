"""职责：管理 Windows 本地 SalesMate 环境检查及 Web/Worker 生命周期。
实现：沿用根 .env，显式唤起 WSL PostgreSQL，应用迁移；后台监督器持有文件锁，按就绪检查启动子进程。
关联：start-local.ps1 准备依赖；原管理命令保持任务参数和 SIGTERM 排空语义；不安装数据库或导入演示数据。

目录：
- emit：输出不含配置秘密的阶段信息。
- lock_runtime：获得 Windows 运行目录独占锁。
- running：只读检查监督器锁是否被持有。
- read_state：读取原子发布的状态文件。
- write_state：更新状态与所管理的进程信息。
- prepare：检查配置、数据库、迁移及本地用户。
- healthy：检查 Web、数据库与静态资源响应。
- watch_stop：将停止文件转换为主线程的 SIGTERM。
- child：运行原 Web 或 Worker 入口。
- spawn：在隐藏进程中启动本脚本的指定子命令。
- supervise：持锁启动、监控并顺序排空各服务。
- control：执行用户 start/status/stop 请求。
- main：解析命令行并提供脱敏错误边界。

变量索引：
- ROOT：仓库绝对路径，避免依赖调用者工作目录。
- RUNTIME：被 Git 忽略的状态及日志目录。
- URL：保持既有 OAuth 与 Agent 地址的本地 Web 入口。
- SERVICES：子进程名称到既有模块及命令行参数的映射，保留 Worker 默认值。
"""

import _thread
import argparse
import json
import msvcrt
import os
from pathlib import Path
import runpy
import secrets
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
}


# 功能：输出可定位的启动阶段。
# 输入：`message` 为已脱敏文本。
# 输出：无返回值；写标准输出并立即刷新。
# 逻辑：固定前缀方便从后台日志识别启动器消息。
# 约束：调用方不得传入密钥、数据库 URL 或原始异常。
def emit(message):
    print(f'[local] {message}', flush=True)


# 功能：获得监督器的独占文件锁。
# 输入：无外部参数；读取 RUNTIME。
# 输出：返回必须保持打开的文件句柄；冲突抛 OSError。
# 逻辑：锁定首字节，进程退出后由操作系统自动释放。
# 约束：仅支持 Windows；不依靠可能被复用的 PID 杀进程。
def lock_runtime():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    handle = (RUNTIME / 'run.lock').open('a+b')
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise
    return handle


# 功能：查询是否已有监督器持锁。
# 输入：无外部参数；读取运行目录文件锁。
# 输出：返回布尔值。
# 逻辑：短暂尝试同一把锁；锁竞争表示运行中。
# 约束：不启动或停止进程；其他文件系统错误仍向上传播。
def running():
    try:
        handle = lock_runtime()
    except OSError as error:
        if error.errno in (13, 11, 36):
            return True
        raise
    handle.close()
    return False


# 功能：读取已发布的运行状态。
# 输入：无外部参数；读取 RUNTIME/state.json。
# 输出：状态字典；尚无文件时返回 stopped。
# 逻辑：监督器使用原子替换，读取者不会看到半个 JSON。
# 约束：损坏文件明确报错，不静默忽略。
def read_state():
    path = RUNTIME / 'state.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'status': 'stopped'}


# 功能：发布启动或停止阶段及子进程身份。
# 输入：`status` 阶段字符串；`processes` 名称到 Popen 对象的映射；`detail` 脱敏诊断文本。
# 输出：无；原子替换状态文件。
# 逻辑：PID 和退出码仅供诊断，控制通过停止文件与进程句柄执行。
# 约束：不记录命令环境或业务数据；仅监督器写状态。
def write_state(status, processes, detail=''):
    data = {'status': status, 'url': URL, 'detail': detail,
            'processes': {name: {'pid': process.pid, 'exit_code': process.poll()}
                          for name, process in processes.items()}}
    temporary = RUNTIME / 'state.tmp'
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(RUNTIME / 'state.json')
    emit(f'{status}: {detail}')


# 功能：检查既有配置并执行已授权的本地数据库迁移。
# 输入：`distro` 为显式 WSL 发行版名称或 None；读取根 .env 及进程环境。
# 输出：返回应启动的服务名称列表；不满足前提时抛 RuntimeError。
# 逻辑：缺失配置只生成带随机密钥的模板；配置存在时验证本地数据库、任务模式和密钥，随后检查并迁移。
# 约束：不覆盖 .env、不创建或重置账号、不改变 provider；仅允许本地 PostgreSQL/显式 SQLite，禁止误迁移远程数据库。
def prepare(distro):
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
    if distro:
        if not engine.endswith('postgresql'):
            raise RuntimeError('--wsl-distro requires a configured PostgreSQL database.')
        emit(f'Starting PostgreSQL in explicitly selected WSL distribution: {distro}')
        subprocess.run(['wsl.exe', '-d', distro, '-u', 'root', '--', 'service', 'postgresql', 'start'],
                       check=True, timeout=90)
        # WSL 服务启动返回后 Windows localhost 转发可能尚未就绪；这里只等待 TCP，不重试业务操作。
        deadline = time.monotonic() + 30
        while True:
            try:
                with socket.create_connection((database['HOST'], int(database.get('PORT') or 5432)), timeout=1):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('WSL PostgreSQL is not reachable from Windows. Check WSL localhost forwarding.') from None
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
    # rules 模式沿用项目的页面演示路径，不宣称已启用模型聊天；真实 Agent 模式启动三个原有 Worker。
    return ['web', 'crm', 'chat', 'sales'] if settings.ANALYSIS_PROVIDER == 'agent' else ['web', 'sales']


# 功能：验证本地 Web、数据库和模块资源已就绪。
# 输入：无外部参数；使用固定 URL。
# 输出：所有探测通过返回 True，否则返回 False。
# 逻辑：绕过系统代理访问回环地址，检查健康 JSON 和 JavaScript MIME。
# 约束：仅 GET，无业务写入；连接等待最多每项两秒，失败由上层限定就绪等待窗口。
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


# 功能：将 Windows 文件控制请求交给现有服务的 SIGTERM 处理器。
# 输入：`name` 为服务名；读取对应停止文件。
# 输出：无；向 Python 主线程投递一次 SIGTERM。
# 逻辑：后台线程等待停止文件及入口已注册信号处理器，再使用 interrupt_main 调度处理器。
# 约束：不强杀，不重试任务；长任务可延迟响应，但原 Worker 能完成当前工作单元。
def watch_stop(name):
    while True:
        if (RUNTIME / f'{name}.stop').exists() and callable(signal.getsignal(signal.SIGTERM)):
            _thread.interrupt_main(signal.SIGTERM)
            return
        time.sleep(0.25)


# 功能：运行未经改写的 Web 或管理命令。
# 输入：`name` 为 SERVICES 中的服务名。
# 输出：正常结束返回 None；服务异常维持原退出语义。
# 逻辑：安装停止文件监视线程，设置原入口所需导入路径及 argv。
# 约束：由监督器以独立隐藏进程调用；无自动重启或任务降级。
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


# 功能：启动有独立日志的隐藏 Python 进程。
# 输入：`arguments` 为本脚本子命令参数；`name` 为日志名。
# 输出：返回 Popen 对象。
# 逻辑：直接传递 argv 列表，不经过 shell，标准输出及错误合并到文件。
# 约束：只支持 Windows；不记录环境；日志每次启动重新写入。
def spawn(arguments, name):
    with (RUNTIME / f'{name}.log').open('w', encoding='utf-8') as log:
        return subprocess.Popen([sys.executable, '-X', 'utf8', '-u', str(Path(__file__).resolve()), *arguments], cwd=ROOT,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW)


# 功能：持锁管理启动、监控及协作停止。
# 输入：`distro` 为显式数据库 WSL 发行版或 None。
# 输出：正常停止返回 0，初始化或运行失败返回 1。
# 逻辑：先检查端口，显式 WSL 模式保持 stdin 会话以维持数据库生命周期；Web 就绪后启动 Workers；停止时依次排空 Worker、Web、WSL 会话。
# 约束：不终止其他进程、不执行 PostgreSQL 停止命令、不自动重启；释放最后的 WSL 会话后发行版可能自行休眠。
def supervise(distro):
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
            # systemd 服务本身不保证 WSL 保持运行；管道由监督器持有，关闭后 cat 正常退出。
            processes['database-session'] = subprocess.Popen(
                ['wsl.exe', '-d', distro, '--', 'cat'], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
        services = prepare(distro)
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
        # 观察初始导入与配置错误；之后继续监督，不能将仅获得 PID 当作持续运行成功。
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
            if name in ('crm', 'chat', 'sales'):
                (RUNTIME / f'{name}.stop').touch()
        for name, process in processes.items():
            if name in ('crm', 'chat', 'sales'):
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


# 功能：执行面向用户的启动、状态或停止命令。
# 输入：`args` 含 action、wsl_distro、no_browser；读取运行锁与状态。
# 输出：返回命令退出码；启动可打开浏览器。
# 逻辑：重复启动复用受管服务；冷启动等待初始化状态；停止仅创建控制文件并等待有界时间。
# 约束：等待超时报告尚未完成，不暗中取消或强杀；启动与状态探测不修改业务数据。
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
        emit('Stopped Web and Workers; released the managed WSL session without stopping PostgreSQL explicitly.')
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
        process = spawn(arguments, 'launcher')
        deadline = time.monotonic() + 180
        while True:
            if process.poll() is not None:
                raise RuntimeError(f'Start failed. Inspect {RUNTIME / "launcher.log"}.')
            if running() and read_state()['status'] == 'running' and healthy():
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Startup is not ready after 180 seconds. Inspect status/logs or run stop; initialization may still be running.')
            time.sleep(0.5)
    emit(f'Ready: {URL}/ ; logs: {RUNTIME}')
    if not args.no_browser:
        webbrowser.open(URL + '/')
    return 0


# 功能：解析 CLI 并限制启动器错误输出。
# 输入：无外部参数；读取 sys.argv。
# 输出：返回退出码，参数错误由 argparse 报告。
# 逻辑：私有 serve/child 子命令复用同一文件，用户命令委托 control。
# 约束：仅 RuntimeError 的受控信息原样输出，其他异常只显示类型；子进程业务异常留在私有日志。
def main():
    parser = argparse.ArgumentParser(description='Manage the local SalesMate workspace on Windows.')
    parser.add_argument('action', choices=('start', 'status', 'stop', 'serve', 'child'))
    parser.add_argument('--wsl-distro')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--service', choices=tuple(SERVICES))
    args = parser.parse_args()
    if args.action == 'child':
        if not args.service:
            parser.error('child requires --service')
        child(args.service)
        return 0
    try:
        if args.action == 'serve':
            return supervise(args.wsl_distro)
        return control(args)
    except Exception as error:
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        emit(f'ERROR: {detail}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
