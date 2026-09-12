"""统一管理 Agent 路径，并从项目根目录读取共享环境配置。"""

from pathlib import Path

from dotenv import load_dotenv

AGENT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = AGENT_DIR.parent


def load_environment() -> None:
    """由程序入口调用；导入其他模块时不会自动读取本机密钥。"""
    load_dotenv(PROJECT_DIR / ".env")
