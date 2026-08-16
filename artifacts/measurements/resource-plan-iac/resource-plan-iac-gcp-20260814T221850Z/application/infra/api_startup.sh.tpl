#!/bin/bash
set -e

# Install Docker
apt-get update
apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release
curl -fsSL https://download.docker.com/linux/debian/gpg | apt-key add -
echo "deb [arch=$(dpkg --print-architecture)] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io

# Run the API container
docker run -d --name api \
  -p ${container_port}:${container_port} \
  -e PORT=${container_port} \
  ${image}

# Simple systemd health check that hits the configured health endpoint
cat <<'EOF' > /etc/systemd/system/api-health.service
[Unit]
Description=API health check
After=docker.service
[Service]
ExecStart=/usr/bin/curl -f http://localhost:${container_port}${health_path}
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now api-health.service
