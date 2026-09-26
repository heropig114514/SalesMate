#!/usr/bin/env bash
# Responsibility: Provide the sole restricted deployment entry point for the dedicated SSH key.
# Implementation: Accept only one complete SHA and pass it to an independent systemd service; SSH disconnection does not interrupt the server deployment transaction.
# Relationships: sudoers permits only this entry point; deploy-from-git.sh additionally verifies that the SHA is current repository main.
# Directory:
# - None
# Variable index:
# - revision: Raw text passed by the forced SSH command; it cannot contain options or Shell fragments.
set -euo pipefail
revision="${1:-}"
[[ $# -eq 1 && "$revision" =~ ^[0-9a-f]{40}$ && $(id -u) -eq 0 ]] || exit 64
exec systemd-run --unit=salesmate-deploy --collect --wait --pipe \
    --property=Type=exec /usr/local/sbin/salesmate-deploy-from-git "$revision"
