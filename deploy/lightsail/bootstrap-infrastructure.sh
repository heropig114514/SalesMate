#!/usr/bin/env bash
# 职责：将已有单实例安装转换为共享数据和双 Web 发布布局。
# 实现：保留原版本启动新 Gunicorn，检查后切换入口；配置 Redis 身份及 vector 扩展，原目录归档；预建服务用户的私有 Gunicorn 控制套接字目录。
# 关联：首次由管理员执行；后续使用 root 安装的 deploy-from-git.sh，不能重复初始化。
# 目录：无函数；嵌入 Python 只处理固定配置和目录，不访问外部业务。
# 变量索引：source 为已审查代码目录；backup 为初始化备份；legacy 为原代码的候选副本；
# revision 为当前版本；attempt 为就绪观察；name 为共享数据目录；FD 9 为发布互斥锁。
set -euo pipefail
umask 027
source="${1:-}"
[[ $# -eq 1 && $(id -u) -eq 0 && -f "$source/deploy/lightsail/salesmate-web@.service" ]]
[[ -d /opt/salesmate/app && ! -L /opt/salesmate/app && ! -e /opt/salesmate/active-port ]]
exec 9>/var/lib/salesmate-deploy/deploy.lock
flock -n 9
revision=$(cat /opt/salesmate/deployed-revision)
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
backup="/opt/salesmate/backups/infrastructure-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$backup"
cp -a /etc/nginx/sites-available "$backup/nginx-sites"
cp -a /etc/redis/redis.conf "$backup/redis.conf"
cp -a /etc/systemd/system/salesmate-{web,crm,sales}.service "$backup/"
cp /usr/local/sbin/salesmate-deploy-from-git "$backup/deploy-from-git.sh"
sudo -u postgres pg_dump -Fc salesmate > "$backup/database.dump"
install -d -m 755 /opt/salesmate/releases /opt/salesmate/slots/8001 /opt/salesmate/slots/8002
install -d -m 750 -o root -g salesmate /opt/salesmate/shared /etc/salesmate
# Gunicorn 在服务用户 HOME 下创建控制套接字；部署根保持 root 所有，仅此私有目录允许服务用户写入。
install -d -m 700 -o salesmate -g salesmate /opt/salesmate/.gunicorn
python3 - <<'PY'
from pathlib import Path
import secrets, shutil, os, grp
shared = Path('/opt/salesmate/shared')
assert not (shared / 'runtime.env').exists()
password = secrets.token_hex(32)
env = Path('/opt/salesmate/app/.env').read_text()
replaced = {'TASK_EXECUTION_MODE', 'CELERY_BROKER_URL', 'CELERY_RESULT_BACKEND', 'SALESMATE_BACKEND_AGENT_URL'}
env = '\n'.join(line for line in env.splitlines() if line.split('=', 1)[0].strip() not in replaced)
env += f'\nTASK_EXECUTION_MODE=celery\nCELERY_BROKER_URL=redis://:{password}@127.0.0.1:6379/0\nCELERY_RESULT_BACKEND=redis://:{password}@127.0.0.1:6379/1\nSALESMATE_BACKEND_AGENT_URL=https://milkdragon.dev/api/v1/agent/\n'
path = shared / 'runtime.env'
path.write_text(env)
os.chown(path, 0, grp.getgrnam('salesmate').gr_gid)
path.chmod(0o640)
path = Path('/etc/redis/redis.conf')
config = '\n'.join(line for line in path.read_text().splitlines() if not line.startswith(('bind ', 'requirepass ', 'appendonly ', 'appendfsync ', 'maxmemory-policy ')))
path.write_text(config + f'\nbind 127.0.0.1 ::1\nrequirepass {password}\nappendonly yes\nappendfsync everysec\nmaxmemory-policy noeviction\n')
PY
systemctl restart redis-server
systemctl enable redis-server
sudo -u postgres psql -d salesmate -v ON_ERROR_STOP=1 -c 'CREATE EXTENSION IF NOT EXISTS vector;'
legacy="/opt/salesmate/releases/bootstrap-${revision:0:12}"
install -d -m 755 "$legacy"
cp -a /opt/salesmate/app "$legacy/app"
for name in media private_uploads; do
    install -d -o salesmate -g salesmate -m 700 "/opt/salesmate/shared/$name"
    if [[ -d "/opt/salesmate/app/backend/$name" ]]; then cp -a "/opt/salesmate/app/backend/$name/." "/opt/salesmate/shared/$name/"; fi
done
python3 - "$legacy" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve()
assert root.is_relative_to('/opt/salesmate/releases')
env = root / 'app/.env'
assert env.is_file() and not env.is_symlink()
env.unlink()
env.symlink_to('/opt/salesmate/shared/runtime.env')
for name in ('media', 'private_uploads'):
    path = root / 'app/backend' / name
    if path.exists():
        path.rename(root / ('initial-' + name))
    path.symlink_to('/opt/salesmate/shared/' + name, target_is_directory=True)
PY
chown -R salesmate:salesmate "$legacy"
sudo -u salesmate python3 -m venv "$legacy/venv"
sudo -u salesmate "$legacy/venv/bin/python" -m pip install -r "$source/backend/requirements/base.txt" -r "$source/agent/requirements.txt" > "$backup/dependencies.log" 2>&1
ln -s "$legacy/app" /opt/salesmate/slots/8001/app
ln -s "$legacy/venv" /opt/salesmate/slots/8001/venv
install -m 644 "$source/deploy/lightsail/salesmate-web@.service" /etc/systemd/system/
install -m 644 "$source/deploy/lightsail/salesmate-celery@.service" /etc/systemd/system/
printf 'CELERY_CONCURRENCY=3\n' > /etc/salesmate/celery-crm.env
printf 'CELERY_CONCURRENCY=1\n' > /etc/salesmate/celery-sales.env
systemctl daemon-reload
systemctl start salesmate-web@8001
for attempt in $(seq 1 30); do
    if curl -fs --max-time 3 -H 'Host: milkdragon.dev' -H 'X-Forwarded-Proto: https' http://127.0.0.1:8001/api/v1/health/ready/ > "$backup/candidate-health.json"; then break; fi
    [[ "$attempt" != 30 ]] || exit 1
    sleep 1
done
systemctl stop salesmate-crm salesmate-sales
install -m 644 "$source/deploy/lightsail/salesmate-crm.service" /etc/systemd/system/
install -m 644 "$source/backend/deploy/lightsail/salesmate-sales.service" /etc/systemd/system/
mv /opt/salesmate/app "$backup/original-app"
mv /opt/salesmate/venv "$backup/original-venv"
ln -s "$legacy/app" /opt/salesmate/app
ln -s "$legacy/venv" /opt/salesmate/venv
cat > /etc/nginx/snippets/salesmate-release.conf <<NGINX
# Initial release $revision on port 8001.
location /static/ { alias $legacy/app/backend/staticfiles/; }
location / {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host \$http_host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
}
NGINX
python3 - <<'PY'
from pathlib import Path
for path in Path('/etc/nginx/sites-enabled').iterdir():
    text = path.read_text()
    if 'proxy_pass http://127.0.0.1:8000;' not in text:
        continue
    start = text.index('    location /static/')
    end = text.rfind('}')
    assert start < end and text[start:end].count('proxy_pass') == 1
    path.write_text(text[:start] + '    include /etc/nginx/snippets/salesmate-release.conf;\n' + text[end:])
PY
nginx -t
systemctl reload nginx
curl -fsS --max-time 15 https://milkdragon.dev/api/v1/health/ready/ > "$backup/public-health.json"
printf '8001\n' > /opt/salesmate/active-port
systemctl daemon-reload
systemctl enable salesmate-web@8001 salesmate-celery@crm salesmate-celery@sales
# 此时运行的仍是旧代码；消费者在首个新版本发布后启动，旧调度器继续原逻辑。
systemctl start salesmate-crm salesmate-sales
install -m 755 "$source/backend/deploy/lightsail/deploy-from-git.sh" /usr/local/sbin/salesmate-deploy-from-git
printf 'Bootstrap switched successfully; backup=%s; retire legacy salesmate-web after confirming old requests drained.\n' "$backup"
