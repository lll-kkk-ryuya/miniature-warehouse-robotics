#!/bin/sh
# mwr-provision.sh - Jetson "minicar" one-shot provisioning (Plan B: NVMe = data disk)
#
#   step 1  NVMe 931G -> GPT + ext4, mounted at /ssd (fstab UUID + nofail)
#   step 2  data directories on /ssd (warehouse/maps/recordings/bags owned by ruyuya)
#   step 3  docker data-root -> /ssd/docker (merged into existing daemon.json,
#           docker.service gated on the /ssd mount so it can never silently
#           write to the microSD when the SSD is missing)
#   step 4  ROS 2 Humble (ros-base + dev tools + joy + pyserial) via apt
#   step 5  rosdep init
#   step 6  /opt/warehouse -> /ssd/warehouse symlink (repo clone lands there later)
#   step 7  /etc/warehouse/warehouse.env (minimal, only if absent)
#
# Idempotent: every step checks state first; safe to re-run after a failure at
# any point (including between partitioning and formatting).
# The ONLY destructive action is formatting the blank NVMe, and it is gated:
# the script shows lsblk and refuses to proceed until you type FORMAT.
#
# Run:  ssh -t jetson 'sudo sh ~/mwr-provision.sh'
set -eu

DEV=/dev/nvme0n1
PART=/dev/nvme0n1p1
MNT=/ssd
FSLABEL=mwr-ssd
OWNER=ruyuya

say()  { printf '\n=== %s ===\n' "$1"; }
fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

confirm_format() {
  # $1 = what is about to happen
  echo
  echo "about to $1. Current disks:"
  echo
  lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS "$DEV" /dev/mmcblk0
  echo
  echo "the microSD (mmcblk0, your running Ubuntu) will NOT be touched."
  [ -t 0 ] || fail "no terminal for confirmation - run via: ssh -t jetson 'sudo sh ~/mwr-provision.sh'"
  printf 'type FORMAT to continue: '
  read -r ans || fail "aborted (EOF) - nothing was changed"
  [ "$ans" = "FORMAT" ] || fail "aborted - nothing was changed"
}

[ "$(id -u)" = 0 ] || fail "run with sudo"

say "step 0: sanity (right machine, right disks, right user)"
hostname | grep -qx 'minicar' || fail "hostname is not 'minicar' - wrong machine, refusing"
findmnt -n -o SOURCE / | grep -q 'mmcblk0p1' || fail "rootfs is not on mmcblk0p1 (microSD) - layout changed, refusing"
id "$OWNER" >/dev/null 2>&1 || fail "user $OWNER does not exist"
echo "ok: this is minicar, rootfs on microSD, user $OWNER exists"

say "step 1: NVMe data disk ($DEV -> $MNT)"
if findmnt -n "$MNT" >/dev/null 2>&1; then
  echo "already mounted: $(findmnt -n -o SOURCE,FSTYPE "$MNT")"
else
  [ -b "$DEV" ] || fail "$DEV not found"
  [ -z "$(findmnt -n -S "$DEV" 2>/dev/null)" ] || fail "$DEV is mounted somewhere - refusing"
  need_mkfs=no
  if blkid "$PART" >/dev/null 2>&1; then
    # a filesystem already exists on p1: mount only if it is ours, never reformat
    blkid "$PART" | grep -q "LABEL=\"$FSLABEL\"" \
      || fail "$PART exists but is not labeled $FSLABEL - refusing to touch it"
    echo "found existing $FSLABEL filesystem - will mount it (no format)"
  elif [ -e "$PART" ]; then
    # partition exists but carries no filesystem: this is exactly the state a
    # previous run leaves if it died between sfdisk and mkfs - offer to finish
    echo "found a bare empty partition (previous run interrupted?)"
    confirm_format "FORMAT the empty partition $PART as ext4"
    need_mkfs=yes
  else
    blkid "$DEV" >/dev/null 2>&1 \
      && fail "$DEV carries a filesystem, RAID, or partition-table signature - refusing (inspect by hand)"
    sz=$(lsblk -bdn -o SIZE "$DEV")
    [ "$sz" -gt 900000000000 ] && [ "$sz" -lt 1100000000000 ] \
      || fail "$DEV size $sz bytes is not ~1TB - wrong disk, refusing"
    confirm_format "PARTITION and FORMAT $DEV (blank ~931.5G NVMe)"
    printf 'label: gpt\n,,L\n' | sfdisk "$DEV"
    udevadm settle
    [ -b "$PART" ] || fail "$PART did not appear after partitioning"
    need_mkfs=yes
  fi
  if [ "$need_mkfs" = yes ]; then
    mkfs.ext4 -q -L "$FSLABEL" "$PART"
    udevadm settle
    echo "formatted $PART as ext4 ($FSLABEL)"
  fi
  uuid=$(blkid -s UUID -o value "$PART" || true)
  [ -n "$uuid" ] || fail "could not read UUID of $PART"
  if grep -Ev '^[[:space:]]*#' /etc/fstab | grep -Eq "[[:space:]]$MNT[[:space:]]"; then
    grep -Ev '^[[:space:]]*#' /etc/fstab | grep -q "UUID=$uuid[[:space:]]" \
      || fail "/etc/fstab already has a $MNT entry with a DIFFERENT UUID - stale entry, fix by hand"
    echo "fstab entry already present and matches UUID=$uuid"
  else
    cp -a /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d-%H%M%S)"
    # guard against a no-trailing-newline fstab: never concatenate onto the last line
    if [ -s /etc/fstab ] && [ -n "$(tail -c1 /etc/fstab)" ]; then
      printf '\n' >> /etc/fstab
    fi
    printf 'UUID=%s %s ext4 defaults,nofail,x-systemd.device-timeout=30 0 2\n' "$uuid" "$MNT" >> /etc/fstab
    findmnt --fstab -n "$MNT" >/dev/null \
      || fail "fstab entry did not parse - restore from the .bak file in /etc/"
    echo "fstab entry added (nofail: a dead SSD can never block boot)"
  fi
  mkdir -p "$MNT"
  systemctl daemon-reload
  mount "$MNT"
  findmnt -n "$MNT" >/dev/null || fail "mount $MNT failed"
  echo "mounted: $(findmnt -n -o SOURCE,SIZE,AVAIL "$MNT" 2>/dev/null || findmnt -n -o SOURCE "$MNT")"
fi

say "step 2: data directories"
for d in warehouse maps recordings bags; do
  mkdir -p "$MNT/$d"
  chown "$OWNER:$OWNER" "$MNT/$d"
done
mkdir -p "$MNT/docker"   # stays root-owned (docker daemon data)
# joy reads /dev/input/event* (group input), the M1 serial link uses
# /dev/ttyUSB* (group dialout) - grant both now, effective from next login
for g in input dialout; do
  if ! id -nG "$OWNER" | grep -qw "$g"; then
    usermod -aG "$g" "$OWNER"
    echo "added $OWNER to group $g (takes effect on next login)"
  fi
done
ls -la "$MNT"

say "step 3: docker data-root -> $MNT/docker"
# fail-closed boot ordering: docker must wait for /ssd and must NOT start
# without it (otherwise it would quietly recreate its data on the microSD)
if [ ! -f /etc/systemd/system/docker.service.d/10-mwr-ssd.conf ]; then
  mkdir -p /etc/systemd/system/docker.service.d
  cat > /etc/systemd/system/docker.service.d/10-mwr-ssd.conf <<UNITEOF
# mwr: docker data lives on /ssd; never start docker without that mount
[Unit]
RequiresMountsFor=$MNT
UNITEOF
  systemctl daemon-reload
  echo "docker.service now requires the $MNT mount (fail-closed)"
fi
if docker info 2>/dev/null | grep -q "Docker Root Dir: $MNT/docker"; then
  echo "already using $MNT/docker"
else
  # docker is empty (verified: 0 images/containers), so no data migration -
  # just point data-root at the SSD. Merge, never overwrite (nvidia runtime
  # config lives in the same daemon.json).
  python3 - "$MNT/docker" <<'PYEOF'
import json, os, sys
p = '/etc/docker/daemon.json'
d = {}
st = None
if os.path.exists(p):
    st = os.stat(p)
    try:
        with open(p) as f:
            d = json.load(f)
    except (json.JSONDecodeError, ValueError):
        sys.exit('ERROR: %s is not valid JSON - fix it by hand first' % p)
    if not isinstance(d, dict):
        sys.exit('ERROR: %s is not a JSON object - fix it by hand first' % p)
if d.get('data-root') != sys.argv[1]:
    d['data-root'] = sys.argv[1]
    tmp = p + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    if st is not None:
        os.chmod(tmp, st.st_mode & 0o7777)
    os.replace(tmp, p)
    print('daemon.json updated')
else:
    print('daemon.json already correct')
PYEOF
  systemctl restart docker || {
    journalctl -u docker -n 40 --no-pager || true
    fail "dockerd failed to start on the new data-root - see log above; restore /etc/docker/daemon.json to roll back"
  }
  docker info 2>/dev/null | grep -q "Docker Root Dir: $MNT/docker" \
    || fail "docker restarted but data-root is NOT $MNT/docker - inspect /etc/docker/daemon.json"
  docker info 2>/dev/null | grep -q "Storage Driver: overlay2" \
    || echo "WARNING: storage driver is not overlay2 - image data may live outside data-root, check 'docker info'"
  echo "docker data-root switched to $MNT/docker"
fi

say "step 4: ROS 2 Humble"
if dpkg-query -W ros-humble-ros-base ros-dev-tools ros-humble-joy python3-serial >/dev/null 2>&1; then
  echo "all packages already installed"
else
  codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
  [ "$codename" = "jammy" ] || fail "expected Ubuntu jammy (22.04), got '$codename'"
  if [ ! -f /etc/apt/sources.list.d/ros2.sources ]; then
    # preferred: official ros2-apt-source package (survives ROS key rotations).
    # Re-attempted on every run so a one-time network fallback never becomes
    # the permanent (non-self-updating) configuration.
    tmpd=$(mktemp -d)
    ver=$(curl -fsSL --max-time 15 https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest \
          | grep -m1 '"tag_name"' | cut -d'"' -f4 || true)
    if [ -n "${ver:-}" ] \
       && curl -fsSL --max-time 60 -o "$tmpd/ros2-apt-source.deb" \
            "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ver}/ros2-apt-source_${ver}.${codename}_all.deb"; then
      dpkg -i "$tmpd/ros2-apt-source.deb"
      rm -f /etc/apt/sources.list.d/ros2.list   # retire the fallback (avoid duplicate-source warning)
      echo "ros2-apt-source ${ver} installed"
    elif [ -f /etc/apt/sources.list.d/ros2.list ]; then
      echo "ros2-apt-source still unavailable - keeping the classic-keyring fallback for now"
    else
      echo "ros2-apt-source unavailable - falling back to classic keyring"
      curl -fsSL --max-time 30 https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
        -o "$tmpd/ros-archive-keyring.gpg"
      mv "$tmpd/ros-archive-keyring.gpg" /usr/share/keyrings/ros-archive-keyring.gpg
      echo "deb [arch=arm64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu ${codename} main" \
        > /etc/apt/sources.list.d/ros2.list
    fi
    rm -rf "$tmpd"
  fi
  apt-get update
  # Humble-on-22.04 official warning (ros2/ros2#1272): upgrade systemd/udev
  # BEFORE installing ROS, and never let apt solve by removing packages
  # (protects the nvidia-l4t-* BSP stack from a removal cascade)
  DEBIAN_FRONTEND=noninteractive apt-get install -y --only-upgrade systemd udev libsystemd0 libudev1
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-remove \
    ros-humble-ros-base \
    ros-dev-tools \
    ros-humble-joy \
    python3-serial
  [ -f /opt/ros/humble/setup.sh ] || fail "install finished but /opt/ros/humble/setup.sh is missing"
  echo "ROS 2 Humble installed"
fi

say "step 5: rosdep"
command -v rosdep >/dev/null 2>&1 || fail "rosdep missing (ros-dev-tools incomplete?) - re-run this script"
if [ -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then
  echo "rosdep already initialized"
else
  rosdep init
  echo "rosdep initialized ('rosdep update' runs later as $OWNER, no sudo)"
fi

say "step 6: /opt/warehouse -> $MNT/warehouse"
if [ -L /opt/warehouse ]; then
  echo "symlink exists: /opt/warehouse -> $(readlink /opt/warehouse)"
elif [ -e /opt/warehouse ]; then
  fail "/opt/warehouse exists and is not a symlink - refusing (inspect by hand)"
else
  ln -s "$MNT/warehouse" /opt/warehouse
  echo "symlink created"
fi

say "step 7: /etc/warehouse/warehouse.env"
if [ -f /etc/warehouse/warehouse.env ]; then
  echo "already present - not touching it"
else
  mkdir -p /etc/warehouse
  cat > /etc/warehouse/warehouse.env <<'ENVEOF'
# warehouse.env - minicar (Jetson Orin Nano) runtime environment
# Generated by mwr-provision.sh. Template: deploy/jetson/env/warehouse.env.example
# (with the ROS_DISTRO=jazzy template bug corrected to humble per ADR-0008).

# Never silently default to dev on the Jetson (doc19).
WAREHOUSE_ENV=prod

# ADR-0008: ROS 2 Humble on Ubuntu 22.04 / JetPack 6.
ROS_DISTRO=humble

# Repo clone root (symlink to /ssd/warehouse) and colcon workspace.
WAREHOUSE_REPO=/opt/warehouse
WAREHOUSE_WS=/opt/warehouse/ws
WAREHOUSE_CONFIG_DIR=/opt/warehouse/config

# TODO(undecided, do not set blindly):
#   WAREHOUSE_TRAFFIC_MODE - template says open-rmf, but Mode M1 requires
#     'none' (docs/mode-m1/01). Changing prod traffic config is a
#     safety-review PR; leave unset so the config default stays authoritative.
#   WAREHOUSE_MAP - template points at the diorama map; ADR-0009 replaces it
#     with a room-scale SLAM map that does not exist yet.
ENVEOF
  echo "written (TRAFFIC_MODE / MAP intentionally left undecided)"
fi

say "done"
echo "summary:"
findmnt -n "$MNT" >/dev/null 2>&1 && echo "  /ssd      : $(findmnt -n -o SOURCE "$MNT") mounted (nofail)"
docker info 2>/dev/null | grep -q "Docker Root Dir: $MNT/docker" && echo "  docker    : data-root on /ssd/docker (fail-closed on missing SSD)"
[ -f /opt/ros/humble/setup.sh ] && echo "  ROS 2     : humble at /opt/ros/humble"
[ -L /opt/warehouse ] && echo "  repo home : /opt/warehouse -> $MNT/warehouse (clone comes next, no sudo needed)"
[ -f /etc/warehouse/warehouse.env ] && echo "  env       : /etc/warehouse/warehouse.env"
echo "re-running this script is safe (idempotent)."
