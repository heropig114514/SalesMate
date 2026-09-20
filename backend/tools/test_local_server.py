"""职责：验证 Windows 本地启动器的锁、配置保护和协作停止边界。
实现：使用隔离临时目录和真实轻量 Python 子进程，不访问业务数据库或外部服务。
关联：local_server.py；实机 Web/Worker、WSL 和浏览器检查另行执行。

目录：
- LocalServerTests：启动器边界测试集合。
- LocalServerTests.test_lock_releases：检查独占锁及正常释放。
- LocalServerTests.test_missing_env_generates_template：检查首次模板初始化及明确失败。
- LocalServerTests.test_existing_env_is_not_overwritten：检查既有配置不被替换。
- LocalServerTests.test_corrupt_state_is_not_ignored：检查状态损坏保留错误语义。
- LocalServerTests.test_stop_dispatches_sigterm：检查停止文件能触发子进程主线程的 SIGTERM 处理器。

变量索引：
- 无
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import local_server


# 功能：在隔离目录验证启动器控制边界。
# 逻辑：只对配置入口使用模拟，锁与停止信号通过真实 OS 行为验证。
# 约束：不启动 Django 服务，不读取仓库 .env，不声明真实模型或邮箱验证通过。
class LocalServerTests(unittest.TestCase):
    # 功能：验证并发启动保护能在句柄关闭后解除。
    # 输入：无外部参数；创建独立临时运行目录。
    # 输出：断言锁持有与释放状态。
    # 逻辑：实际获得 Windows 文件锁，通过 running 探测竞争。
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
    # 逻辑：在读取既有配置后截断真实初始化，以隔离外部服务。
    # 约束：只验证文件保护，不证明数据库有效；恢复导入路径及环境。
    def test_existing_env_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(local_server, 'ROOT', Path(directory)), \
                patch('django.setup', side_effect=RuntimeError('setup boundary')), \
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

    # 功能：验证隐藏 Windows 子进程可协作响应停止文件。
    # 输入：无外部参数；临时目录预置停止请求。
    # 输出：断言子进程在五秒内正常退出并执行 SIGTERM 回调。
    # 逻辑：轻量子进程注册信号处理器与实际 watch_stop，主线程等待回调。
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
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('graceful-stop-observed', result.stdout)


if __name__ == '__main__':
    unittest.main()
