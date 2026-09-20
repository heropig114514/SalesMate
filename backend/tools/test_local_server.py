"""职责：验证 Windows/macOS/POSIX 本地启动器的锁、配置保护、分离会话及协作停止边界。
实现：使用标准库、隔离临时目录和真实轻量子进程；POSIX 额外执行 Bash 入口及空依赖虚拟环境初始化。
关联：local_server.py、start-local.sh；Django 和 Homebrew 的调用使用明确模拟，不访问业务数据库或外部服务。

目录：
- LocalServerTests：启动器边界测试集合。
- LocalServerTests.test_lock_releases：检查独占锁及正常释放。
- LocalServerTests.test_missing_env_generates_template：检查首次模板初始化及明确失败。
- LocalServerTests.test_existing_env_is_not_overwritten：检查既有配置不被替换。
- LocalServerTests.test_corrupt_state_is_not_ignored：检查状态损坏保留错误语义。
- LocalServerTests.test_stop_dispatches_sigterm：检查停止文件能触发子进程主线程的 SIGTERM 处理器。
- LocalServerTests.test_lock_visible_to_other_process：验证不同进程竞争同一运行锁。
- LocalServerTests.test_spawn_detaches_session：验证日志及 POSIX 会话隔离，兼顾包含空格的目录。
- LocalServerTests.test_database_options_are_platform_specific：检查平台、互斥及 Homebrew 服务名约束。
- LocalServerTests.test_brew_run_existing_service：模拟验证显式 Homebrew 运行方式，保持迁移调用与现有配置。
- LocalServerTests.test_shell_setup_and_actions：用空依赖及受控入口验证 Bash 3.2 兼容脚本的初始化、复用与动作转发。
- LocalServerTests.test_shell_rejects_foreign_venv：验证 shell 不覆盖其他平台虚拟环境。

变量索引：
- 无
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch

import local_server


# 功能：在隔离目录验证启动器控制边界。
# 逻辑：对 Django/Homebrew 使用模拟，锁、停止信号、会话分离及 shell 入口通过真实 OS 行为验证。
# 约束：不启动业务服务、不读取仓库 .env；空依赖 shell 测试不能证明完整依赖在该平台可安装。
class LocalServerTests(unittest.TestCase):
    # 功能：验证并发启动保护能在句柄关闭后解除。
    # 输入：无外部参数；创建独立临时运行目录。
    # 输出：断言锁持有与释放状态。
    # 逻辑：实际获得当前平台的文件锁，通过 running 探测竞争。
    # 约束：finally 释放句柄；不触碰真实运行目录。
    def test_lock_releases(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(local_server, 'RUNTIME', Path(directory)):
            self.assertFalse(local_server.running())
            handle = local_server.lock_runtime()
            try:
                self.assertTrue(local_server.running())
            finally:
                handle.close()
            self.assertFalse(local_server.running())

    # 功能：验证首次生成模板不会伪造数据库已配置。
    # 输入：无外部参数；临时目录仅含受控模板。
    # 输出：断言随机密钥已生成且明确要求配置数据库。
    # 逻辑：调用 prepare 的无配置路径，禁止进入 Django 初始化。
    # 约束：无数据库、WSL 或网络访问。
    def test_missing_env_generates_template(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(local_server, 'ROOT', Path(directory)):
            root = Path(directory)
            (root / '.env.example').write_text('DJANGO_SECRET_KEY=replace-with-a-local-random-string\nDATABASE_URL=placeholder\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'Configure DATABASE_URL'):
                local_server.prepare(None)
            text = (root / '.env').read_text(encoding='utf-8')
            self.assertNotIn('replace-with-a-local-random-string', text)
            self.assertIn('DATABASE_URL=placeholder', text)

    # 功能：验证现有 .env 在初始化失败时逐字节保留。
    # 输入：无外部参数；使用虚构配置和失败的 Django setup。
    # 输出：断言原配置字节不变且错误向上传递。
    # 逻辑：以模拟 Django 模块在读取既有配置后截断初始化，隔离外部服务及第三方包安装要求。
    # 约束：只验证文件保护，不证明数据库有效；恢复模块、导入路径及环境。
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

    # 功能：验证损坏状态文件不会被当作已停止。
    # 输入：无外部参数；临时状态文件包含非法 JSON。
    # 输出：断言 ValueError。
    # 逻辑：直接读取损坏状态，不模拟解析器。
    # 约束：只验证诊断边界，不修改真实状态。
    def test_corrupt_state_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(local_server, 'RUNTIME', Path(directory)):
            (Path(directory) / 'state.json').write_text('{broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                local_server.read_state()

    # 功能：验证当前平台后台子进程可协作响应停止文件。
    # 输入：无外部参数；临时目录预置停止请求。
    # 输出：断言子进程在五秒内正常退出并执行 SIGTERM 回调。
    # 逻辑：使用实际后台进程选项；子进程注册信号处理器与 watch_stop，主线程等待回调。
    # 约束：不运行 Worker，不发业务请求；测试超时只影响自己创建的测试进程。
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

    # 功能：验证独占锁对其他进程生效而非仅在进程内生效。
    # 输入：无外部参数；隔离临时目录及轻量子进程。
    # 输出：断言持锁时其他进程读到 running，释放后读到 stopped。
    # 逻辑：子进程实际导入同一模块并尝试锁定相同文件。
    # 约束：无业务数据库或服务进程；句柄在 finally 释放。
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

    # 功能：验证跨平台 spawn 的路径传递、日志和 POSIX 独立会话。
    # 输入：无外部参数；含空格的临时目录及仅输出进程信息的测试入口。
    # 输出：断言子进程成功、参数保真；POSIX 中会话 ID 等于子进程 ID。
    # 逻辑：替换脚本路径并调用真实 spawn，以 OS 结果验证进程隔离。
    # 约束：只运行测试脚本，不导入 Django 或启动服务器。
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

    # 功能：验证 WSL 与 Homebrew 选项只能用于其明确支持的平台。
    # 输入：无外部参数；仅模拟 sys.platform。
    # 输出：断言不合法组合报错，合法 PostgreSQL 公式通过校验。
    # 逻辑：覆盖服务名注入、跨平台误用和互斥条件。
    # 约束：不执行实际 Homebrew/WSL，不代表 macOS 服务已验证。
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

    # 功能：验证显式 Homebrew 启动不会安装软件或注册登录启动。
    # 输入：无外部参数；模拟本地 rules 配置、Django 连接与 brew 可执行路径。
    # 输出：断言调用 services run、保留原配置并执行原 check/migrate。
    # 逻辑：模拟连接就绪但使用真实 prepare 控制流；不让测试访问现有数据库。
    # 约束：此测试只验证调用契约；不证明 Homebrew、pgvector 或数据库迁移实际成功。
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
            self.assertEqual(local_server.prepare(None, 'postgresql@16'), ['web', 'sales'])
            run.assert_called_once_with(['/opt/homebrew/bin/brew', 'services', 'run', 'postgresql@16'], check=True, timeout=90)
            self.assertEqual(path.read_text(encoding='utf-8'), 'existing settings')
            self.assertEqual(command.call_args_list[0].args, ('check',))
            self.assertEqual(command.call_args_list[1].args, ('migrate',))

    # 功能：验证 shell 入口从含空格目录初始化虚拟环境并正确转发所有动作。
    # 输入：无外部参数；POSIX 上隔离副本、空依赖清单及只记录 argv 的假业务入口。
    # 输出：断言首次创建环境、二次启动复用安装摘要、status/stop 正确传参。
    # 逻辑：实际执行 /bin/bash 与 venv/pip（macOS 对应系统 Bash），使用 PIP_NO_INDEX 避免外部下载。
    # 约束：Windows 跳过；不复制 .env，不安装真实业务依赖；子命令最多 90 秒。
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

    # 功能：验证 shell 入口不会在 Windows 虚拟环境上叠加 POSIX 环境。
    # 输入：无外部参数；POSIX 临时目录中的 Windows 风格标记文件。
    # 输出：断言非零退出且原环境文件不变。
    # 逻辑：系统 /bin/bash 在创建环境前检测不兼容目录并停止。
    # 约束：Windows 跳过；无依赖安装或数据库操作。
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
