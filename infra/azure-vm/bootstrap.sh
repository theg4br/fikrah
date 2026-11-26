#!/usr/bin/env bash
# Dedicated NEW Ubuntu 24.04 host only. No application secrets are arguments.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo on the VM' >&2; exit 1; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(dpkg --print-architecture) == amd64 ]] || {
  echo 'Expected the reviewed Ubuntu 24.04 amd64 image' >&2; exit 1;
}
if [[ -e /etc/docker/daemon.json && ! -e /etc/fikrah-host-ready ]]; then
  echo 'Existing unmanaged Docker configuration: review before bootstrapping' >&2
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg python3 unattended-upgrades
install -d -m 0755 /etc/apt/keyrings
curl --fail --silent --show-error --location https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
# Check the official Docker signing key before trusting packages.
fingerprint=$(gpg --show-keys --with-colons /etc/apt/keyrings/docker.asc | awk -F: '$1=="fpr" {print $10; exit}')
[[ $fingerprint == 9DC858229FC7DD38854AE2D88D81803C0EBFCD88 ]] || {
  echo 'Unexpected Docker repository signing key' >&2; exit 1;
}
chmod a+r /etc/apt/keyrings/docker.asc
cat >/etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
install -d -m 0750 /opt/fikrah/releases
install -d -m 0700 /etc/fikrah /var/lib/fikrah/backups
install -d -m 0755 /etc/docker
cat >/etc/docker/daemon.json <<'EOF'
{"log-driver":"local","log-opts":{"max-size":"10m","max-file":"3"},"live-restore":true}
EOF
cat >/usr/local/sbin/fikrah-container-firewall <<'EOF'
#!/bin/sh
set -eu
# Host-only backup jobs may obtain managed identity tokens. Containers may not.
/usr/sbin/iptables -C DOCKER-USER -d 169.254.169.254/32 -j REJECT 2>/dev/null || \
  /usr/sbin/iptables -I DOCKER-USER 1 -d 169.254.169.254/32 -j REJECT
EOF
chmod 0750 /usr/local/sbin/fikrah-container-firewall
install -d -m 0755 /etc/systemd/system/docker.service.d
cat >/etc/systemd/system/docker.service.d/fikrah-firewall.conf <<'EOF'
[Service]
ExecStartPost=/usr/local/sbin/fikrah-container-firewall
EOF
cat >/etc/sysctl.d/60-fikrah-redis.conf <<'EOF'
vm.overcommit_memory=1
EOF
sysctl --system >/dev/null
cat >/etc/ssh/sshd_config.d/00-fikrah.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
X11Forwarding no
AllowTcpForwarding no
EOF
sshd -t
systemctl daemon-reload
systemctl enable docker
systemctl restart docker
systemctl reload ssh
docker compose version
touch /etc/fikrah-host-ready
echo 'Host initialized. Install private runtime configuration before starting FIKRAH.'
