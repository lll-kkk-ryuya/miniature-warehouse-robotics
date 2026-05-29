#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Push Hermes Gateway config to the GCP VM and restart the service.
#
# Source of truth: this repo's deploy/hermes/gcp/{config.yaml,SOUL.md}.
# The VM's ~/.hermes/.env (secrets) is NEVER touched by this script.
#
# Runnable both locally (from a Mac with the dev SSH key) and from GitHub
# Actions (with the CI deploy key). Behaviour is identical; only the SSH
# identity differs.
#
# Required env:
#   GCP_VM_HOST   external IP / hostname of the VM
#   GCP_VM_USER   SSH login user (has passwordless sudo for systemctl)
# Optional env:
#   SSH_KEY            path to a private key (default: ssh-agent / ~/.ssh defaults)
#   SSH_KNOWN_HOSTS    path to a known_hosts file (default: ~/.ssh/known_hosts)
#   SERVICE            systemd unit to restart (default: hermes-gateway)
#   REMOTE_HERMES_DIR  remote config dir (default: ~/.hermes)
#   HEALTH_PORT        gateway loopback port for health check (default: 8642)
# =============================================================================
set -euo pipefail

GCP_VM_HOST="${GCP_VM_HOST:?GCP_VM_HOST is required}"
GCP_VM_USER="${GCP_VM_USER:?GCP_VM_USER is required}"
SERVICE="${SERVICE:-hermes-gateway}"
REMOTE_HERMES_DIR="${REMOTE_HERMES_DIR:-.hermes}"   # relative to remote $HOME
HEALTH_PORT="${HEALTH_PORT:-8642}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Build SSH/SCP option array --------------------------------------------
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15 -o IdentitiesOnly=yes)
if [[ -n "${SSH_KEY:-}" ]]; then
  SSH_OPTS+=(-i "${SSH_KEY}")
else
  # No explicit key: let ssh use agent/defaults (don't force IdentitiesOnly).
  SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15)
fi
if [[ -n "${SSH_KNOWN_HOSTS:-}" ]]; then
  SSH_OPTS+=(-o "UserKnownHostsFile=${SSH_KNOWN_HOSTS}" -o StrictHostKeyChecking=yes)
fi

REMOTE="${GCP_VM_USER}@${GCP_VM_HOST}"

# --- Validate local source files -------------------------------------------
for f in config.yaml SOUL.md; do
  [[ -f "${SCRIPT_DIR}/${f}" ]] || { echo "ERROR: missing ${SCRIPT_DIR}/${f}" >&2; exit 1; }
done

echo ">> Deploying Hermes Gateway config to ${REMOTE} (service=${SERVICE})"

# --- Stage files on the VM --------------------------------------------------
STAGE="/tmp/hermes-deploy-staging"
ssh "${SSH_OPTS[@]}" "${REMOTE}" "rm -rf ${STAGE} && mkdir -p ${STAGE}"
scp "${SSH_OPTS[@]}" \
  "${SCRIPT_DIR}/config.yaml" "${SCRIPT_DIR}/SOUL.md" \
  "${REMOTE}:${STAGE}/"

# --- Apply on the VM: backup -> install -> restart -> health check ----------
ssh "${SSH_OPTS[@]}" "${REMOTE}" \
  "REMOTE_HERMES_DIR='${REMOTE_HERMES_DIR}' SERVICE='${SERVICE}' STAGE='${STAGE}' HEALTH_PORT='${HEALTH_PORT}' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail
HERMES="${HOME}/${REMOTE_HERMES_DIR}"
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="${HERMES}/backups/${TS}"

mkdir -p "${BACKUP}"
changed=0
for f in config.yaml SOUL.md; do
  src="${STAGE}/${f}"
  dst="${HERMES}/${f}"
  if [[ -f "${dst}" ]] && cmp -s "${src}" "${dst}"; then
    echo "   = ${f} unchanged"
    continue
  fi
  [[ -f "${dst}" ]] && cp -p "${dst}" "${BACKUP}/${f}"
  install -m 0644 "${src}" "${dst}"
  echo "   + ${f} updated (backup: ${BACKUP}/${f})"
  changed=1
done
rm -rf "${STAGE}"
rmdir "${BACKUP}" 2>/dev/null || true   # remove backup dir if nothing was backed up

if [[ "${changed}" -eq 0 ]]; then
  echo ">> No changes to apply. Skipping restart."
  exit 0
fi

echo ">> Restarting ${SERVICE} ..."
sudo -n systemctl restart "${SERVICE}"

echo ">> Health check (http://127.0.0.1:${HEALTH_PORT}/health) ..."
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${HEALTH_PORT}/health" >/dev/null 2>&1; then
    echo ">> Gateway healthy after ${i} attempt(s)."
    systemctl is-active "${SERVICE}"
    exit 0
  fi
  sleep 2
done

echo "ERROR: gateway did not become healthy within ~60s" >&2
sudo -n systemctl status "${SERVICE}" --no-pager -l | tail -30 >&2
exit 1
REMOTE_SCRIPT

echo ">> Deploy complete."
