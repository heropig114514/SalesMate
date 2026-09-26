#!/usr/bin/env bash
# Responsibility: Release the current main commit independently of CI diagnostics using isolated environments and dual Web-instance cutover.
# Implementation: Protect deployment source/runtime files, build candidate, drain background work, back up and migrate, cut traffic after basic readiness, and retire old instance after requests end.
# Relationships: An administrator initializes root restricted entry, systemd templates, standalone chat service, and shared directory.
# Directory: report_failure records failure; phase records state transition; remaining steps deploy sequentially.
# Variable index: revision/latest/previous are versions; state/repo are release directories; stage/backup locate failures.
# release/app/py are candidate paths; active/target are ports; source/mode/name handle archive/shared paths.
# attempt/pid/old_nginx observe readiness/draining; result is exit code; FD 9 is mutual-exclusion release lock.
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
# Function: Record failure stage and preserve the scene.
# Inputs: Exit code plus stage/revision/backup.
# Outputs: Failure log and state file.
# Logic: Does not roll back, retry business work, or force-kill in-flight tasks.
# Constraints: Background services may already be stopped; administrators choose recovery by stage.
report_failure() {
    local result=$?
    if [[ "$result" -ne 0 ]]; then
        printf 'FAILED revision=%s stage=%s backup=%s exit=%s\n' "$revision" "$stage" "$backup" "$result"
        printf 'failed %s %s %s\n' "$revision" "$stage" "$backup" > "$state/status"
    fi
}
# Function: Record deployment state transition.
# Inputs: First parameter is fixed stage name; reads revision.
# Outputs: Log and state file.
# Logic: Writes before stage action to locate long draining or installation.
# Constraints: Does not record credentials or retry.
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
# An administrator must install the protected chat service first; absence fails explicitly before background draining.
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
# Documentation, contract, dependency consistency, and migration types are checked only by independent diagnostics, not server-release gates.
sudo -u salesmate env DJANGO_SETTINGS_MODULE=config.settings.lightsail "$py" backend/manage.py collectstatic --noinput > "$release/static.log" 2>&1
sudo -u salesmate cp -a backend/frontend/assets/. backend/staticfiles/
find backend/staticfiles -type d -exec chmod 755 {} +
find backend/staticfiles -type f -exec chmod 644 {} +
phase drain
# Drain standalone chat first, then stop schedulers and wait for results, and finally stop Celery consumers; old Web continues serving.
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
# Do not run Django system prechecks; actual migration errors still end release and DDL lock wait remains five seconds.
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
# Wait for old Nginx requests to drain before stopping old Web; retain the old instance for investigation on timeout.
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
