#!/usr/bin/env bash
# =============================================================================
# install.sh — install the warehouse systemd units + env on the Jetson (prod).
#
# Idempotent and re-runnable. Installs unit files, the ROS wrapper env, and a
# dedicated service account. It deliberately does NOT enable or start anything:
# motion units must clear the SAFETY GATE first (Layer 0 speed clamp <= 0.3 m/s
# + Emergency Guardian tests — .claude/rules/safety.md, doc16 §11, doc19:21).
#
# Unit ExecStart paths are rewritten from the /opt/warehouse convention to the
# actual repo clone location, so this works wherever the release tag was cloned
# (doc19:94). Run as root:  sudo deploy/jetson/bin/install.sh
#
# Source of truth: docs/setup/jetson-deploy.md, docs/architecture/19-environments-and-config.md.
# =============================================================================
set -euo pipefail

CANONICAL_PREFIX="/opt/warehouse"          # path baked into the committed unit files
SERVICE_USER="warehouse"
SYSTEMD_DIR="/etc/systemd/system"
ENV_DIR="/etc/warehouse"
ENV_FILE="${ENV_DIR}/warehouse.env"

if [[ "${EUID}" -ne 0 ]]; then
  echo "install.sh: must run as root (use sudo)." >&2
  exit 1
fi

# Repo root = three levels up from this script (deploy/jetson/bin/install.sh).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
JETSON_DIR="${REPO_ROOT}/deploy/jetson"

echo ">> Repo root: ${REPO_ROOT}"

# --- Service account (no login shell; dialout for USB serial: RPLiDAR etc.) ---
if ! getent group "${SERVICE_USER}" >/dev/null; then
  groupadd --system "${SERVICE_USER}"
  echo "   + group ${SERVICE_USER} created"
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --gid "${SERVICE_USER}" --no-create-home \
    --shell /usr/sbin/nologin "${SERVICE_USER}"
  echo "   + user ${SERVICE_USER} created"
fi
usermod -aG dialout "${SERVICE_USER}" || true

# --- Env file (do not clobber an admin-customised one) ----------------------
install -d -m 0755 "${ENV_DIR}"
if [[ -f "${ENV_FILE}" ]]; then
  echo "   = ${ENV_FILE} exists (left as-is)"
else
  sed "s#${CANONICAL_PREFIX}#${REPO_ROOT}#g" \
    "${JETSON_DIR}/env/warehouse.env.example" >"${ENV_FILE}"
  chmod 0644 "${ENV_FILE}"
  echo "   + ${ENV_FILE} installed (review WAREHOUSE_MAP / traffic_mode before start)"
fi

# --- Wrapper + scripts executable -------------------------------------------
chmod 0755 \
  "${JETSON_DIR}/bin/ros-exec.sh" \
  "${JETSON_DIR}/bin/healthcheck.sh" \
  "${JETSON_DIR}/bin/preflight.sh"

# --- Unit files (rewrite the /opt/warehouse prefix to the real clone) -------
# NOTE: this is a GLOBAL substitution of ${CANONICAL_PREFIX}. Every /opt/warehouse
# occurrence in the units today is a clone-relative path that SHOULD be relocated
# (ExecStart wrapper, bridge EnvironmentFile). If a unit ever needs a literal
# /opt/warehouse that must NOT move, anchor this rewrite (e.g. to "=${CANONICAL_PREFIX}/").
shopt -s nullglob
for src in "${JETSON_DIR}/systemd/"*.service "${JETSON_DIR}/systemd/"*.target; do
  dst="${SYSTEMD_DIR}/$(basename "${src}")"
  sed "s#${CANONICAL_PREFIX}#${REPO_ROOT}#g" "${src}" >"${dst}"
  chmod 0644 "${dst}"
  echo "   + $(basename "${src}")"
done

systemctl daemon-reload
echo ">> daemon-reload done."

cat <<EOF

>> Units installed but NOT enabled/started (safety gate).
   Before enabling motion units, confirm:
     - Layer 0 MCU speed clamp <= 0.3 m/s and e-stop verified (safety.md, doc12:75-78)
     - Emergency Guardian unit tests pass (doc16 §11)
   Then (prod):
     sudo systemctl enable --now warehouse.target
   Verify:
     ${JETSON_DIR}/bin/healthcheck.sh
   See docs/setup/jetson-deploy.md for the full procedure.
EOF
