#!/bin/sh
# mwr-tailscale-setup.sh - put the board on the tailnet (off-LAN access)
#
# After this, the Mac can reach the board from ANY network (cafe Wi-Fi, phone
# tethering): both ends dial out to the tailnet, so no port forwarding and no
# dependence on the home LAN's mDNS. The jetson CLI auto-switches to this
# route whenever minicar.local is out of reach.
#
# Idempotent; the only interactive part is the login URL you open on the Mac.
# Run:  ssh -t jetson 'sudo sh ~/mwr-tailscale-setup.sh'
set -eu

say()  { printf '\n=== %s ===\n' "$1"; }
fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

[ "$(id -u)" = 0 ] || fail "run with sudo"
hostname | grep -qx 'minicar' || fail "hostname is not 'minicar' - wrong machine, refusing"
id -u ruyuya >/dev/null 2>&1 || fail "user ruyuya not found (needed for --operator)"

say "step 1: install tailscale (official apt repo, verified 2026-08-30)"
if command -v tailscale >/dev/null 2>&1; then
  echo "already installed: $(tailscale version | head -1)"
else
  codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
  [ "$codename" = "jammy" ] || fail "expected Ubuntu jammy (22.04), got '$codename'"
  tmpd=$(mktemp -d)
  trap 'rm -rf "$tmpd"' EXIT
  curl -fsSL --max-time 30 "https://pkgs.tailscale.com/stable/ubuntu/${codename}.noarmor.gpg" \
    -o "$tmpd/ts.gpg"
  curl -fsSL --max-time 30 "https://pkgs.tailscale.com/stable/ubuntu/${codename}.tailscale-keyring.list" \
    -o "$tmpd/ts.list"
  install -m 0644 "$tmpd/ts.gpg"  /usr/share/keyrings/tailscale-archive-keyring.gpg
  install -m 0644 "$tmpd/ts.list" /etc/apt/sources.list.d/tailscale.list
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-remove \
    tailscale tailscale-archive-keyring
fi
# the deb postinst already enables+starts tailscaled; this is a cheap assertion
systemctl enable --now tailscaled

say "step 2: join the tailnet"
# Print the one thing that must not get lost BEFORE the only blocking call:
# if the login stalls and this script dies, the reminder has already landed.
echo "REMEMBER (one-time, after joining): in https://login.tailscale.com/admin/machines"
echo "  open the ... menu on the 'minicar' row -> 'Disable key expiry'."
echo "  Skipping this silently drops the board off the tailnet after ~180 days."
echo
if tailscale status --peers=false >/dev/null 2>&1; then
  echo "already logged in."
else
  echo "a login URL appears below. Open it in the BROWSER ON YOUR MAC, sign in,"
  echo "and approve the device. This waits up to 5 minutes; if it times out,"
  echo "just re-run this script."
  echo
  tailscale up --hostname=minicar --operator=ruyuya --timeout=5m \
    || fail "login not completed - re-run: ssh -t jetson 'sudo sh ~/mwr-tailscale-setup.sh'"
fi
# Converge settings even when the box was already joined (a bare 'up' from
# some earlier attempt would have left these unset; 'set' updates only what
# it names, so it is the right tool for every post-join change).
tailscale set --hostname=minicar --operator=ruyuya
echo
echo "state:"
tailscale status --self --peers=false || true

say "done"
echo "if you have not yet: admin console -> Machines -> minicar -> Disable key expiry."
echo "re-running this script is safe (idempotent, converges hostname/operator)."
