"""职责：导入外部 schema/文本文件，或持续关联已有入站业务邮件。
实现：文件信封与 HTTP 相同；邮件使用内容摘要幂等键，成功来源不会重复调用模型；失败退出不自动重试。
关联：episodes 为唯一处理路径；本机 llama.cpp 需独立启动；graph_worker 继续维护源数据变化。
目录：
- email_payload：从本用户邮件构造不可变观察。
- Command：自动建图管理命令。
- Command.add_arguments：声明用户、输入与持续运行选项。
- Command.handle：读取文件或扫描新邮件并执行统一输入。
变量索引：
- Command.help：命令用途。
"""
import hashlib
import json
import time
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import close_old_connections
from common.shutdown import graceful_shutdown
from apps.crm.models import Email
from apps.knowledge_graph.episode_views import parse_input
from apps.knowledge_graph.episodes import ingest, episode_data
from apps.knowledge_graph.models import Episode
from apps.knowledge_graph.business_schema import mail_text


# 功能：把原邮件作为自然语言来源交给语义建图。
# 输入：`email` 为已按邮箱归属筛选的入站业务邮件。
# 输出：含来源键、时间和主题/正文的输入参数。
# 逻辑：键绑定邮件身份与文本摘要，保持同内容幂等；不包含附件或邮箱凭据。
# 约束：未自动猜测邮件中的实体归属；由模型从同用户候选中对齐，原文保持来源边界。
def email_payload(email):
    text = mail_text(email)
    key = hashlib.sha256((str(email.pk) + "\0" + text).encode()).hexdigest()
    return {"source_key": "mail:" + key, "observed_at": email.sent_at, "text": text, "email_id": str(email.pk)}


# 功能：运行文件输入或邮件自动关联。
# 逻辑：持续模式只处理新输入；模型或证据错误立即停止，需要显式检查并重新启动。
# 约束：不启动邮箱同步或发送邮件，不访问其他 owner，不改模型配置或已有实验。
class Command(BaseCommand):
    help = "将不完整业务 JSON 或自然语言自动关联到内部图谱；支持本用户业务邮件监听。"

    # 功能：注册显式输入范围。
    # 输入：`parser` 为 Django 命令解析器。
    # 输出：无；声明 owner、input/emails、watch 和 poll。
    # 逻辑：文件和邮箱二选一，默认单轮；持续模式默认每10秒检查新输入。
    # 约束：轮询不是失败重试；只用于本命令，不修改已有 Worker 参数。
    def add_arguments(self, parser):
        parser.add_argument("--owner", required=True, type=int)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--input", type=Path)
        group.add_argument("--emails", action="store_true")
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--poll", type=float, default=10)

    # 功能：执行自动关联工作循环。
    # 输入：`args` 为位置参数；`options` 为 CLI 选项。
    # 输出：文件模式返回完整来源 JSON，邮件模式仅输出来源 UUID 和计数；失败非零退出。
    # 逻辑：仅新邮件调用模型；SIGTERM 后不领取下一封，当前推理完成后退出。
    # 约束：已撤回来源也不自动重新导入；更新原文会形成新观察，旧观察保留，不自动覆盖。
    def handle(self, *args, **options):
        if not 0 < options["poll"] <= 60 or (options["watch"] and not options["emails"]):
            raise CommandError("--watch 仅支持 --emails；--poll 必须在 (0,60]。")
        try:
            owner = get_user_model().objects.get(pk=options["owner"])
            if options["input"]:
                payload = parse_input(json.loads(options["input"].read_text(encoding="utf-8")))
                self.stdout.write(json.dumps(episode_data(ingest(owner.pk, **payload)), ensure_ascii=False, cls=DjangoJSONEncoder))
                return
            with graceful_shutdown() as stop:
                while not stop["requested"]:
                    close_old_connections()
                    for email in Email.objects.filter(mailbox__owner=owner, direction="inbound", business_classification="business").order_by("sent_at", "pk").iterator():
                        if stop["requested"]:
                            break
                        payload = email_payload(email)
                        if Episode.objects.filter(owner=owner, source_key=payload["source_key"]).exists():
                            continue
                        episode = ingest(owner.pk, **payload)
                        self.stdout.write(f"episode={episode.pk} entities={len(episode.extraction['entities'])} facts={len(episode.extraction['facts'])}")
                    if not options["watch"] or stop["requested"]:
                        break
                    time.sleep(options["poll"])
        except Exception as exc:
            raise CommandError(f"自动建图失败（{type(exc).__name__}）；检查服务日志后显式恢复，没有自动重试。") from exc
