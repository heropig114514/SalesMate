#!/usr/bin/env bash
# 职责：部署 GitHub Actions 已验证的 main 提交，并保留服务器数据与可恢复备份。
# 实现：独占锁、只读 Git 拉取、校验提交与文件类型，排空 Worker 后备份、更新、迁移并检查健康。
# 关联：由受限 SSH 入口以 systemd 临时服务运行；首次安装后受 root 保护，不从新提交自动覆盖自身。
# 目录：report_failure 记录失败阶段；其余为顺序部署步骤。
# 变量索引：revision 为请求提交；state 为私有工作目录；repo 为只读源缓存；app/py 为既有部署路径；
# work 为本次归档目录；backup 为数据库、代码及虚拟环境备份；stage 为日志阶段；
# previous 为当前部署提交；latest 为拉取到的 main；mode 为 Git 文件类型；attempt 为只读就绪轮询。
set -euo pipefail
umask 077
revision="${1:-}"
[[ $# -eq 1 && "$revision" =~ ^[0-9a-f]{40}$ && $(id -u) -eq 0 ]] || exit 64
state=/var/lib/salesmate-deploy
repo="$state/repository.git"
app=/opt/salesmate/app
py=/opt/salesmate/venv/bin/python
stage=lock
backup=not-created
exec 9>"$state/deploy.lock"
flock -n 9 || { printf 'Another deployment is active; no changes made.\n' >&2; exit 75; }
exec > >(tee -a "$state/deploy.log") 2>&1

# 功能：记录失败后保留现场。
# 输入：EXIT 触发时的 stage、revision、backup 和原退出码。
# 输出：非零退出时记录日志及 failed 状态文件，保留原退出状态。
# 逻辑：不重启服务、不回滚数据库或重试外部业务。
# 约束：运维须检查备份和实际服务状态后决定恢复方式。
report_failure() {
    local result=$?
    if [[ "$result" -ne 0 ]]; then
        printf 'FAILED revision=%s stage=%s backup=%s exit=%s\n' "$revision" "$stage" "$backup" "$result"
        printf 'failed %s %s %s\n' "$revision" "$stage" "$backup" > "$state/status"
    fi
}
trap report_failure EXIT
printf 'START revision=%s time=%s\n' "$revision" "$(date -u +%FT%TZ)"
printf 'running %s\n' "$revision" > "$state/status"
[[ -f "$app/.env" && -x "$py" && -d "$repo" ]]
stage=fetch
export GIT_SSH_COMMAND='ssh -i /etc/salesmate-deploy/repository_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/etc/salesmate-deploy/github_known_hosts'
git --git-dir="$repo" fetch --no-tags origin +refs/heads/main:refs/heads/main
latest=$(git --git-dir="$repo" rev-parse refs/heads/main)
if [[ "$revision" != "$latest" ]]; then
    printf 'SKIPPED stale_revision=%s latest_main=%s\n' "$revision" "$latest"
    printf 'skipped %s newer_main=%s\n' "$revision" "$latest" > "$state/status"
    exit 0
fi
previous=$(cat /opt/salesmate/deployed-revision)
if [[ "$revision" == "$previous" ]]; then
    systemctl is-active --quiet salesmate-web salesmate-crm salesmate-sales
    curl --fail --silent --show-error --max-time 15 https://milkdragon.dev/api/v1/health/ready/ > /dev/null
    printf 'UNCHANGED healthy_revision=%s\n' "$revision"
    printf 'healthy %s\n' "$revision" > "$state/status"
    exit 0
fi
stage=prepare
[[ "$previous" =~ ^[0-9a-f]{40}$ ]]
git --git-dir="$repo" cat-file -e "$previous^{commit}"
# 源文件仅允许普通文件，避免归档软链接修改服务器外部路径。
while read -r mode; do [[ "$mode" == 100644 || "$mode" == 100755 ]]; done < <(git --git-dir="$repo" ls-tree -r --format='%(objectmode)' "$revision")
work=$(mktemp -d "$state/release-${revision:0:12}-XXXXXX")
git --git-dir="$repo" archive --format=tar.gz --output="$work/source.tar.gz" "$revision"
git --git-dir="$repo" diff --name-only --diff-filter=D -z "$previous" "$revision" > "$work/deleted.z"
git --git-dir="$repo" ls-tree -r --name-only -z "$revision" > "$work/tracked.z"
# 此 Python 块只验证路径，不导入或执行待部署代码；.env 和运行时目录永远不能由 Git 接管。
python3 - "$work/tracked.z" "$work/deleted.z" <<'PY'
import pathlib, sys
for filename in sys.argv[1:]:
    for raw in pathlib.Path(filename).read_bytes().split(b'\0'):
        if not raw:
            continue
        path = pathlib.PurePosixPath(raw.decode('utf-8'))
        assert not path.is_absolute() and '..' not in path.parts
        assert path.name != '.env' and not path.name.startswith('.env.') or path.name == '.env.example'
        assert not {'media', 'private_uploads', 'staticfiles', 'artifacts', '.git'} & set(path.parts)
PY
stage=drain
# 先排空需要 HTTP 后端的 Worker，再停网站；不在邮件 DATA 提交中杀死进程。
systemctl stop salesmate-sales salesmate-crm
systemctl stop salesmate-web
stage=backup
backup="/opt/salesmate/backups/auto-${revision}-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$backup"
sudo -u postgres pg_dump --format=custom --dbname=salesmate > "$backup/database.dump"
pg_restore --list "$backup/database.dump" > "$backup/database.contents"
tar -czf "$backup/app.tar.gz" -C "$app" .
tar -czf "$backup/venv.tar.gz" -C /opt/salesmate venv
cp /opt/salesmate/deployed-revision "$backup/previous-revision"
sha256sum "$app/.env" > "$backup/environment.sha256"
stage=install
# 只删除上一部署中由 Git 跟踪且现已删除的文件，保留未跟踪凭证、邮件、附件和服务器运维文件。
python3 - "$app" "$work/deleted.z" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
for raw in pathlib.Path(sys.argv[2]).read_bytes().split(b'\0'):
    if raw:
        path = root / raw.decode('utf-8')
        assert path.resolve().is_relative_to(root) and path.resolve() != root
        if path.exists():
            assert path.is_file() and not path.is_symlink()
            path.unlink()
PY
tar --no-same-owner -xzf "$work/source.tar.gz" -C "$app"
chown -R salesmate:salesmate "$app"
chmod 600 "$app/.env"
sha256sum --check "$backup/environment.sha256"
cd "$app"
sudo -u salesmate "$py" -m pip install -r backend/requirements/base.txt -r agent/requirements.txt > "$backup/dependencies.log" 2>&1
sudo -u salesmate "$py" -m pip check
stage=migrate
sudo -u salesmate "$py" backend/manage.py migrate --noinput --settings=config.settings.lightsail
sudo -u salesmate "$py" backend/manage.py migrate --check --settings=config.settings.lightsail
sudo -u salesmate "$py" backend/manage.py check --deploy --settings=config.settings.lightsail
sudo -u salesmate "$py" backend/tools/check_docs.py
stage=static
sudo -u salesmate "$py" backend/manage.py collectstatic --noinput --settings=config.settings.lightsail > "$backup/static.log" 2>&1
sudo -u salesmate cp -a backend/frontend/assets/. backend/staticfiles/
find backend/staticfiles -type d -exec chmod 755 {} +
find backend/staticfiles -type f -exec chmod 644 {} +
nginx -t
stage=health
systemctl start salesmate-web
for attempt in $(seq 1 30); do
    if curl --fail --silent --max-time 5 https://milkdragon.dev/api/v1/health/ready/ > "$backup/health.json"; then break; fi
    if [[ "$attempt" == 30 ]]; then exit 1; fi
    sleep 1
done
systemctl start salesmate-crm salesmate-sales
systemctl is-active salesmate-web salesmate-crm salesmate-sales nginx postgresql
printf '%s\n' "$revision" > /opt/salesmate/deployed-revision.new
chmod 644 /opt/salesmate/deployed-revision.new
mv /opt/salesmate/deployed-revision.new /opt/salesmate/deployed-revision
printf 'succeeded %s %s\n' "$revision" "$backup" > "$state/status"
printf 'SUCCESS revision=%s backup=%s time=%s\n' "$revision" "$backup" "$(date -u +%FT%TZ)"
