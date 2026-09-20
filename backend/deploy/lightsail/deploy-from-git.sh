#!/usr/bin/env bash
# 职责：发布当前 main 提交，不以 CI 诊断结果作为前提，使用独立环境和双 Web 实例切换。
# 实现：保护部署来源与运行文件、构建候选、排空后台、备份及实际迁移、基本就绪后切流，旧请求结束后退役。
# 关联：root 受限入口、systemd 模板、独立聊天服务及 shared 目录须由管理员初始化。
# 目录：report_failure 记录失败；phase 记录状态转换；其余为顺序部署。
# 变量索引：revision/latest/previous 为版本；state/repo 为发布目录；stage/backup 为故障定位；
# release/app/py 为候选路径；active/target 为端口；source/mode/name 为归档和共享路径处理；
# attempt/pid/old_nginx 为就绪及排空观察；result 为退出码；FD 9 为互斥发布锁。
set -euo pipefail
umask 027
revision="${1:-}"
[[ $# -eq 1 && "$revision" =~ ^[0-9a-f]{40}$ && $(id -u) -eq 0 ]] || exit 64
state=/var/lib/salesmate-deploy
repo="$state/repository.git"
stage=lock
backup=not-created
exec 9>"$state/deploy.lock"
flock -n 9 || exit 75
exec > >(tee -a "$state/deploy.log") 2>&1
# 功能：记录故障阶段并保留现场。
# 输入：退出码及 stage/revision/backup。
# 输出：失败日志和状态文件。
# 逻辑：不回滚、不重试业务、不强杀在途任务。
# 约束：失败时后台可能已停止；管理员按阶段决定恢复方式。
report_failure() {
    local result=$?
    if [[ "$result" -ne 0 ]]; then
        printf 'FAILED revision=%s stage=%s backup=%s exit=%s\n' "$revision" "$stage" "$backup" "$result"
        printf 'failed %s %s %s\n' "$revision" "$stage" "$backup" > "$state/status"
    fi
}
# 功能：记录部署状态转换。
# 输入：第一个参数为固定阶段名，读取 revision。
# 输出：日志及状态文件。
# 逻辑：在执行阶段动作前写入，便于定位长时间排空或安装。
# 约束：不记录凭证，不执行重试。
phase() {
    stage="$1"
    printf 'PHASE revision=%s stage=%s time=%s\n' "$revision" "$stage" "$(date -u +%FT%TZ)"
    printf 'running %s %s\n' "$revision" "$stage" > "$state/status"
}
trap report_failure EXIT
printf 'running %s\n' "$revision" > "$state/status"
[[ -L /opt/salesmate/app && -L /opt/salesmate/venv && -f /opt/salesmate/shared/runtime.env ]]
active=$(cat /opt/salesmate/active-port)
[[ "$active" == 8001 || "$active" == 8002 ]]
target=8001
[[ "$active" != 8001 ]] || target=8002
# 受保护的聊天服务须先由管理员安装；缺失时在排空后台前明确失败。
systemctl cat salesmate-chat.service > /dev/null 2>&1 || { printf 'Install the reviewed salesmate-chat.service before deployment.\n' >&2; exit 64; }
phase fetch
export GIT_SSH_COMMAND='ssh -i /etc/salesmate-deploy/repository_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/etc/salesmate-deploy/github_known_hosts'
git --git-dir="$repo" fetch --no-tags origin +refs/heads/main:refs/heads/main
latest=$(git --git-dir="$repo" rev-parse refs/heads/main)
if [[ "$revision" != "$latest" ]]; then
    printf 'skipped %s newer_main=%s\n' "$revision" "$latest" > "$state/status"
    exit 0
fi
previous=$(cat /opt/salesmate/deployed-revision)
[[ "$previous" =~ ^[0-9a-f]{40}$ ]]
if [[ "$revision" == "$previous" ]]; then
    systemctl is-active --quiet "salesmate-web@$active" salesmate-crm salesmate-sales salesmate-chat salesmate-celery@crm salesmate-celery@sales redis-server
    curl -fsS --max-time 15 https://milkdragon.dev/api/v1/health/ready/ > /dev/null
    printf 'healthy %s\n' "$revision" > "$state/status"
    exit 0
fi
phase validate
while read -r mode; do [[ "$mode" == 100644 || "$mode" == 100755 ]]; done < <(git --git-dir="$repo" ls-tree -r --format='%(objectmode)' "$revision")
source=$(mktemp -d "$state/source-${revision:0:12}-XXXXXX")
git --git-dir="$repo" ls-tree -r --name-only -z "$revision" > "$source/tracked.z"
python3 - "$source/tracked.z" <<'PY'
import pathlib, sys
for raw in pathlib.Path(sys.argv[1]).read_bytes().split(b'\0'):
    if not raw:
        continue
    path = pathlib.PurePosixPath(raw.decode('utf-8'))
    assert not path.is_absolute() and '..' not in path.parts
    assert path.name == '.env.example' or not (path.name == '.env' or path.name.startswith('.env.'))
    assert not {'media', 'private_uploads', 'staticfiles', 'artifacts', '.git', 'venv'} & set(path.parts)
PY
git --git-dir="$repo" archive --format=tar.gz --output="$source/source.tar.gz" "$revision"
phase build
release=$(mktemp -d "/opt/salesmate/releases/${revision:0:12}-XXXXXX")
chmod 755 "$release"
app="$release/app"
py="$release/venv/bin/python"
install -d -m 755 "$app"
tar --no-same-owner -xzf "$source/source.tar.gz" -C "$app"
ln -s /opt/salesmate/shared/runtime.env "$app/.env"
for name in media private_uploads; do ln -s "/opt/salesmate/shared/$name" "$app/backend/$name"; done
chown -R salesmate:salesmate "$release"
sudo -u salesmate python3 -m venv "$release/venv"
sudo -u salesmate "$py" -m pip install -r "$app/backend/requirements/base.txt" -r "$app/agent/requirements.txt" > "$release/dependencies.log" 2>&1
cd "$app"
# 文档、契约、依赖一致性和迁移类型只在独立诊断中检查，不作为服务器发布门禁。
sudo -u salesmate env DJANGO_SETTINGS_MODULE=config.settings.lightsail "$py" backend/manage.py collectstatic --noinput > "$release/static.log" 2>&1
sudo -u salesmate cp -a backend/frontend/assets/. backend/staticfiles/
find backend/staticfiles -type d -exec chmod 755 {} +
find backend/staticfiles -type f -exec chmod 644 {} +
phase drain
# 先排空独立聊天，再停调度器并等待结果，最后停 Celery 消费者；旧 Web 继续服务。
systemctl stop salesmate-chat
systemctl stop salesmate-crm salesmate-sales
systemctl stop salesmate-celery@crm salesmate-celery@sales
phase backup
backup="/opt/salesmate/backups/online-${revision}-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$backup"
sudo -u postgres pg_dump --format=custom --dbname=salesmate > "$backup/database.dump"
pg_restore --list "$backup/database.dump" > "$backup/database.contents"
cp /opt/salesmate/deployed-revision "$backup/previous-revision"
cp /opt/salesmate/active-port "$backup/previous-port"
readlink -f /opt/salesmate/app > "$backup/previous-app"
readlink -f /opt/salesmate/venv > "$backup/previous-venv"
cp /etc/nginx/snippets/salesmate-release.conf "$backup/nginx.conf"
sha256sum /opt/salesmate/shared/runtime.env > "$backup/environment.sha256"
phase migrate
# 不运行 Django 系统预检；实际迁移错误仍终止发布，DDL 锁等待上限保持五秒。
sudo -u salesmate env PGOPTIONS='-c lock_timeout=5s' "$py" backend/manage.py migrate --noinput --skip-checks
phase candidate
systemctl stop "salesmate-web@$target"
ln -sfnT "$app" "/opt/salesmate/slots/$target/app"
ln -sfnT "$release/venv" "/opt/salesmate/slots/$target/venv"
systemctl start "salesmate-web@$target"
for attempt in $(seq 1 30); do
    if curl -fs --max-time 3 -H 'Host: milkdragon.dev' -H 'X-Forwarded-Proto: https' "http://127.0.0.1:$target/api/v1/health/ready/" > "$backup/candidate-health.json"; then break; fi
    [[ "$attempt" != 30 ]] || exit 1
    sleep 1
done
phase switch
cat > /etc/nginx/snippets/salesmate-release.conf.new <<NGINX
# Generated release $revision on port $target.
location /static/ { alias $app/backend/staticfiles/; }
location / {
    proxy_pass http://127.0.0.1:$target;
    proxy_set_header Host \$http_host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
}
NGINX
mv /etc/nginx/snippets/salesmate-release.conf.new /etc/nginx/snippets/salesmate-release.conf
nginx -t
old_nginx=$(pgrep -P "$(cat /run/nginx.pid)" || true)
systemctl reload nginx
printf '%s\n' "$target" > /opt/salesmate/active-port.new
mv /opt/salesmate/active-port.new /opt/salesmate/active-port
ln -s "$app" /opt/salesmate/app.new
mv -Tf /opt/salesmate/app.new /opt/salesmate/app
ln -s "$release/venv" /opt/salesmate/venv.new
mv -Tf /opt/salesmate/venv.new /opt/salesmate/venv
curl -fsS --max-time 15 https://milkdragon.dev/api/v1/health/ready/ > "$backup/public-health.json"
phase workers
systemctl start salesmate-celery@crm salesmate-celery@sales
systemctl start salesmate-crm salesmate-sales salesmate-chat
systemctl is-active "salesmate-web@$target" salesmate-crm salesmate-sales salesmate-chat salesmate-celery@crm salesmate-celery@sales nginx redis-server postgresql
phase retire
# 等旧 Nginx 释放请求再停旧 Web；超时保留旧实例供排查。
for pid in $old_nginx; do
    for attempt in $(seq 1 120); do
        kill -0 "$pid" 2>/dev/null || break
        [[ "$attempt" != 120 ]] || exit 1
        sleep 1
    done
done
systemctl stop "salesmate-web@$active"
systemctl enable "salesmate-web@$target"
systemctl disable "salesmate-web@$active"
sha256sum --check "$backup/environment.sha256"
printf '%s\n' "$revision" > /opt/salesmate/deployed-revision.new
chmod 644 /opt/salesmate/deployed-revision.new
mv /opt/salesmate/deployed-revision.new /opt/salesmate/deployed-revision
printf 'succeeded %s %s\n' "$revision" "$backup" > "$state/status"
printf 'SUCCESS revision=%s port=%s backup=%s\n' "$revision" "$target" "$backup"
