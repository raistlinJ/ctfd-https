#!/usr/bin/env bash
# Invoked by cloud-init inside the new Debian VM, not on the Proxmox host.
set -Eeuo pipefail
exec > >(tee -a /var/log/ctfd-provision.log) 2>&1
install -d -m 0755 /var/lib/ctfd-provision
trap 'echo failed > /var/lib/ctfd-provision/status' ERR
echo running > /var/lib/ctfd-provision/status
export DEBIAN_FRONTEND=noninteractive

repo_url=$1
repo_ref=$2
# shellcheck disable=SC1091
. /etc/os-release
[[ "$ID" == debian && "$VERSION_ID" == 12 ]]
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git openssl qemu-guest-agent
systemctl enable --now qemu-guest-agent

install -d -m 0755 /etc/apt/keyrings
curl -fsSL --retry 3 https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: bookworm
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker

# Clone only a fresh deployment. Never replace an existing event's data.
git clone --depth 1 --branch "$repo_ref" -- "$repo_url" /opt/ctfd-https
cd /opt/ctfd-https
test -f start.sh
test -f docker-compose-https.yml
test -d ctfd/themes/core
test -d ctfd/themes/admin

# The repository includes example certificates. Each new VM gets its own key.
rm -f certs/fullchain.pem certs/privkey.pem
# Finish the one-time certificate job before Nginx starts using its files.
# start.sh must create the env_file before any Compose invocation.
sh start.sh cert-init
test "$(docker wait ctfd-cert-init)" = 0
test -s certs/fullchain.pem
test -s certs/privkey.pem
sh start.sh

# Wait for a real response from CTFd through the HTTPS proxy.
for ((attempt = 0; attempt < 120; attempt++)); do
    http_code=$(curl -ksS --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code}' https://127.0.0.1/ || true)
    case "$http_code" in
        200|301|302|303|307|308)
            echo ready > /var/lib/ctfd-provision/status
            echo 'CTFd HTTPS is ready. Open the VM address to complete initial setup.'
            exit 0
            ;;
    esac
    sleep 5
done
docker compose -f docker-compose-https.yml ps
docker compose -f docker-compose-https.yml logs --tail 60
echo 'CTFd did not become ready; see /var/log/ctfd-provision.log.' >&2
echo failed > /var/lib/ctfd-provision/status
exit 1
