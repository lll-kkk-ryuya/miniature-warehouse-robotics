#!/bin/sh
# MWR Jetson one-time setup.   sudo sh /tmp/mwr-setup.sh
# Idempotent by construction: every step is a no-op when already applied.
set -u
log()  { printf '==> %s\n' "$*"; }
skip() { printf '    (already done) %s\n' "$*"; }

# --- 1. stop the failing reverse tunnel -----------------------------------
# It dials the Mac every 5s and fails authentication, which both spams this
# box's journal and trips the Mac's "Too many authentication failures".
# Direct LAN + mDNS (minicar.local) replaces it.  The unit file is LEFT ON
# DISK, only disabled, so an off-LAN (tethering) run can re-enable it with
#   sudo systemctl enable --now mwr-tunnel
if systemctl is-enabled mwr-tunnel >/dev/null 2>&1; then
  log "disabling mwr-tunnel (direct LAN replaces it; unit kept on disk)"
  systemctl disable --now mwr-tunnel >/dev/null 2>&1 || true
else
  skip "mwr-tunnel already disabled"
fi

# --- 2. remove the unauthenticated root command poller --------------------
# Audited BEFORE deleting: no systemd unit, no .socket, no cron entry, and no
# forced-command in authorized_keys referenced it -- it had no autostart path
# at all.  A copy is kept under /root in case the audit needs re-doing.
if [ -e /usr/local/bin/mwr-agent ]; then
  log "removing /usr/local/bin/mwr-agent (polls http://192.168.11.11:8000/cmd and runs it as root)"
  cp -a /usr/local/bin/mwr-agent "/root/mwr-agent.removed.$(date +%Y%m%d)" 2>/dev/null || true
  rm -f /usr/local/bin/mwr-agent
else
  skip "mwr-agent already gone"
fi
pkill -f mwr-agent 2>/dev/null || true

# --- 3. never sleep -------------------------------------------------------
# The whole design rests on "the board is always reachable".  A suspend would
# be indistinguishable from a power failure from the Mac's side, and Wi-Fi
# cannot wake it.
if systemctl is-enabled sleep.target 2>/dev/null | grep -q masked; then
  skip "sleep targets already masked"
else
  log "masking sleep/suspend/hibernate (board must stay reachable 24/7)"
  systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true
fi

# --- 4. persistent journal ------------------------------------------------
# Without this every reboot erases the evidence of why the last one happened.
if [ -d /var/log/journal ]; then
  skip "journal already persistent"
else
  log "enabling persistent journal (capped at 100M)"
  mkdir -p /etc/systemd/journald.conf.d
  printf '[Journal]\nStorage=persistent\nSystemMaxUse=100M\n' \
    > /etc/systemd/journald.conf.d/10-mwr.conf
  mkdir -p /var/log/journal
  systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true
  systemctl restart systemd-journald >/dev/null 2>&1 || true
fi

# --- 5. safe-stop wrappers ------------------------------------------------
# Nothing may call poweroff/reboot directly.  These wrappers bring the robot
# to a CONFIRMED standstill first and fail closed: any doubt -> refuse.
log "installing /usr/local/sbin/mwr-shutdown and /usr/local/sbin/mwr-reboot"
mkdir -p /usr/local/sbin

cat > /usr/local/sbin/mwr-stop-common <<'WRAP'
# Sourced by mwr-shutdown / mwr-reboot.  Never executed on its own.
# Contract: return 0 ONLY when the robot is at a confirmed standstill.
# Any uncertainty must return non-zero -- this is the fail-closed hinge, and
# it is the one function to extend when ROS 2 starts driving the motors.
mwr_safe_stop() {
  # Phase 1: no motor stack is installed yet, so there is nothing in motion.
  # Prove that claim rather than assuming it: if a ROS 2 daemon or a container
  # is running, we are past Phase 1 and must NOT silently succeed.
  if command -v ros2 >/dev/null 2>&1 && pgrep -x ros2 >/dev/null 2>&1; then
    echo "ros2 processes are running and no stop routine is wired up yet"
    return 1
  fi
  if command -v docker >/dev/null 2>&1; then
    # An empty answer and a FAILED query look identical through $(...) alone,
    # and this is the one function that may not guess: a daemon that cannot be
    # asked might be restarting around live containers.  Ask, and treat
    # "could not ask" as "could not prove standstill" -> refuse.
    if ids=$(docker ps -q 2>/dev/null); then
      if [ -n "$ids" ]; then
        echo "docker containers are running and no stop routine is wired up yet"
        return 1
      fi
    else
      echo "docker is present but not queryable - cannot prove a standstill"
      return 1
    fi
  fi
  return 0
}
WRAP
chmod 0644 /usr/local/sbin/mwr-stop-common

cat > /usr/local/sbin/mwr-shutdown <<'WRAP'
#!/bin/sh
set -u
. /usr/local/sbin/mwr-stop-common
if ! reason=$(mwr_safe_stop); then
  echo "SAFE-STOP-FAILED: ${reason:-unknown}"
  exit 3
fi
echo "HALTING"
# Detach: the caller must receive HALTING before its ssh session is torn down.
( sleep 1; /usr/sbin/poweroff ) >/dev/null 2>&1 &
exit 0
WRAP
chmod 0755 /usr/local/sbin/mwr-shutdown

cat > /usr/local/sbin/mwr-reboot <<'WRAP'
#!/bin/sh
set -u
. /usr/local/sbin/mwr-stop-common
if ! reason=$(mwr_safe_stop); then
  echo "SAFE-STOP-FAILED: ${reason:-unknown}"
  exit 3
fi
echo "REBOOTING"
( sleep 1; /usr/sbin/reboot ) >/dev/null 2>&1 &
exit 0
WRAP
chmod 0755 /usr/local/sbin/mwr-reboot

# --- 6. sudoers: exactly these three, no arguments, no password -----------
# The trailing "" is sudoers(5) syntax for "may be run with NO arguments",
# which is what stops `mwr-shutdown --anything` from becoming a wildcard.
# Filename has no dot: sudo(8) ignores files in /etc/sudoers.d containing one.
RULE=/etc/sudoers.d/10-mwr
TMP=$(mktemp)
cat > "$TMP" <<'SUDO'
# Installed by mwr-setup.sh.  Deliberately minimal.
ruyuya ALL=(root) NOPASSWD: /usr/local/sbin/mwr-shutdown ""
ruyuya ALL=(root) NOPASSWD: /usr/local/sbin/mwr-reboot ""
ruyuya ALL=(root) NOPASSWD: /usr/sbin/nvpmodel -q
SUDO
if visudo -cf "$TMP" >/dev/null 2>&1; then
  install -m 0440 -o root -g root "$TMP" "$RULE"
  log "installed $RULE"
else
  echo "!!! sudoers draft failed validation -- NOT installed:"
  visudo -cf "$TMP" || true
fi
rm -f "$TMP"

# --- 7. docker group ------------------------------------------------------
# Without this `docker ps` fails with EACCES for ruyuya, and `jetson off`
# cannot tell "no containers" apart from "not allowed to look".
if id -nG ruyuya | tr ' ' '\n' | grep -qx docker; then
  skip "ruyuya already in the docker group"
else
  log "adding ruyuya to the docker group (takes effect on next login)"
  usermod -aG docker ruyuya || true
fi

# --- 8. Super mode --------------------------------------------------------
# MAXN_SUPER is ID=2 in /etc/nvpmodel.conf on this board (verified).  The
# setting is written to /etc/nvpmodel.conf and survives reboots.
cur=$(nvpmodel -q 2>/dev/null | awk '/^[0-9]+$/{print $1; exit}')
if [ "${cur:-}" = "2" ]; then
  skip "already in MAXN_SUPER"
else
  log "switching power mode ${cur:-?} -> 2 (MAXN_SUPER)"
  nvpmodel -m 2 >/dev/null 2>&1 || echo "!!! nvpmodel -m 2 failed"
fi

echo
log "done.  Summary:"
tun=$(systemctl is-enabled mwr-tunnel 2>/dev/null)
echo "    tunnel   : ${tun:-removed}"
echo "    mwr-agent: $([ -e /usr/local/bin/mwr-agent ] && echo STILL PRESENT || echo removed)"
echo "    journal  : $([ -d /var/log/journal ] && echo persistent || echo volatile)"
echo "    sudoers  : $([ -f /etc/sudoers.d/10-mwr ] && echo installed || echo MISSING)"
echo "    power    : $(nvpmodel -q 2>/dev/null | head -1)"
