#!/usr/bin/env bash
# Create Always-Free e2-micro VM for Hermes Gateway on GCP.
# Prereq: billing enabled on the project, Compute Engine API enabled.
# Usage: ./create-vm.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-gen-lang-client-0487062066}"  # hermes-minicar
VM_NAME="${VM_NAME:-hermes-gateway}"
REGION="${REGION:-us-west1}"   # Always-Free eligible (Oregon)
ZONE="${ZONE:-us-west1-a}"
MACHINE_TYPE="e2-micro"        # Always-Free eligible
IMAGE_FAMILY="ubuntu-minimal-2404-lts-amd64"
IMAGE_PROJECT="ubuntu-os-cloud"
DISK_SIZE="30GB"               # Always-Free: 30GB standard PD
DISK_TYPE="pd-standard"
NETWORK_TIER="STANDARD"        # Always-Free: STANDARD only (PREMIUM is paid)
SSH_PUB_KEY_FILE="${SSH_PUB_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}"
CLOUD_INIT_FILE="$(dirname "$0")/cloud-init.yaml"

echo "==> Setting active project: $PROJECT_ID"
gcloud config set project "$PROJECT_ID"

echo "==> Enabling Compute Engine API (idempotent)"
gcloud services enable compute.googleapis.com

# Build SSH key metadata: GCP expects "username:ssh-key"
SSH_USER="$(whoami)"
SSH_KEY_LINE="${SSH_USER}:$(cat "$SSH_PUB_KEY_FILE")"

echo "==> Creating VM: $VM_NAME in $ZONE"
gcloud compute instances create "$VM_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --image-family="$IMAGE_FAMILY" \
  --image-project="$IMAGE_PROJECT" \
  --boot-disk-size="$DISK_SIZE" \
  --boot-disk-type="$DISK_TYPE" \
  --network-tier="$NETWORK_TIER" \
  --no-shielded-secure-boot \
  --shielded-vtpm \
  --shielded-integrity-monitoring \
  --metadata=enable-oslogin=FALSE \
  --metadata-from-file=user-data="$CLOUD_INIT_FILE",ssh-keys=<(echo "$SSH_KEY_LINE") \
  --tags=hermes-gateway \
  --labels=app=hermes,env=prod

echo "==> Waiting for VM to be RUNNING"
for _ in $(seq 1 30); do
  status=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --format='value(status)')
  if [ "$status" = "RUNNING" ]; then break; fi
  sleep 2
done

EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" \
  --format='value(networkInterfaces[0].accessConfigs[0].natIP)')

cat <<EOF

==================================================
VM created: $VM_NAME
External IP: $EXTERNAL_IP
SSH:        ssh ${SSH_USER}@${EXTERNAL_IP}
==================================================

Cloud-init may still be running. Verify with:
  ssh ${SSH_USER}@${EXTERNAL_IP} 'test -f /var/log/hermes-init-done && echo READY || echo "still installing..."'
EOF
