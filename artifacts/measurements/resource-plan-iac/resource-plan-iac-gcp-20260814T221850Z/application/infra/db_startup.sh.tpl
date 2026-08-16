#!/bin/bash
set -e

# Install Docker
apt-get update
apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release
curl -fsSL https://download.docker.com/linux/debian/gpg | apt-key add -
echo "deb [arch=$(dpkg --print-architecture)] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io

# Wait for the attached persistent disk to become visible
DEVICE="/dev/disk/by-id/google-${disk_device}"
TIMEOUT=60
while [ ! -e "$DEVICE" ] && [ $TIMEOUT -gt 0 ]; do
  sleep 1
  TIMEOUT=$((TIMEOUT - 1))
done

# Format the disk if it is not already formatted
if ! blkid "$DEVICE" >/dev/null 2>&1; then
  mkfs.ext4 -F "$DEVICE"
fi

# Mount the disk at the host mount point
mkdir -p ${mount_point}
if ! mountpoint -q ${mount_point}; then
  mount "$DEVICE" ${mount_point}
fi

# Ensure the mount persists across reboots
UUID=$(blkid -s UUID -o value "$DEVICE")
echo "UUID=$${UUID} ${mount_point} ext4 defaults 0 2" >> /etc/fstab

# Run PostgreSQL container with the mounted volume
docker run -d --name postgres \
  -p ${container_port}:5432 \
  -e POSTGRES_DB=${postgres_db} \
  -e POSTGRES_USER=${postgres_user} \
  -e POSTGRES_PASSWORD=${postgres_password} \
  -v ${mount_point}:${container_path} \
  ${image}
