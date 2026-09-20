#!/usr/bin/env bash
# Run on a Proxmox VE node. Uses the same cloud-image pattern as ScenarioForge.
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
VMID=9501
NAME=ctfd
STORAGE=local-lvm
SNIPPET_STORAGE=local
BRIDGE=vmbr0
CORES=2
IP=dhcp
GATEWAY=
DNS=
SSH_KEY=
REPO_URL=https://github.com/raistlinJ/ctfd-https.git
REPO_REF=main
WAIT_MINUTES=30
NO_WAIT=0
DRY_RUN=0
IMAGE_BASE=https://cloud.debian.org/images/cloud/bookworm/latest
IMAGE_NAME=debian-12-genericcloud-amd64.qcow2
WORK_DIR=
CONFIG_FILE=
CONFIG_LINE=0

die() { echo "Error: $*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: bash scripts/provision/proxmox/install-ctfd.sh [--config FILE] [options]

Create a minimal Debian 12 VM with 4096 MiB RAM and a 40 GiB disk, install
Docker Compose, and start CTFd HTTPS. Run as root on the target Proxmox node.

  --config FILE             Read key=value settings (also --config=FILE)
  --vmid ID                 Unused cluster VM ID (default: 9501)
  --name NAME               VM hostname (default: ctfd)
  --storage NAME            VM disk storage (default: local-lvm)
  --snippet-storage NAME    Directory storage for cloud-init (default: local)
  --bridge NAME             Existing network bridge (default: vmbr0)
  --cores N                 CPU cores (default: 2)
  --ip CIDR                 Static IPv4 address/prefix (default: dhcp)
  --gateway ADDRESS         Required with a static address
  --dns ADDRESS             Optional IPv4 DNS server
  --ssh-public-key FILE     Required OpenSSH public key; login user: ctfd
  --repo-url URL            Public HTTPS Git repo (default: this repo's origin)
  --repo-ref REF            Published branch or tag (default: main)
  --wait-minutes N          Bootstrap timeout (default: 30)
  --no-wait                 Return after starting the VM
  --dry-run                 Validate inputs and print the plan; no host changes
  -h, --help                Show this help

Existing VMs are never overwritten. Failed VMs are retained for diagnosis.
Supply ssh_public_key in the config, CTFD_SSH_PUBLIC_KEY, or --ssh-public-key.
Precedence: built-in defaults < config file < CTFD_* environment < CLI flags.
EOF
}

# Keep the config a data file: never source it or evaluate its values.
trim_config_value() {
    local value="$1"
    while [[ "$value" == [[:space:]]* ]]; do value="${value:1}"; done
    while [[ "$value" == *[[:space:]] ]]; do value="${value:0:${#value}-1}"; done
    printf '%s' "$value"
}

apply_setting() {
    local key="$1" value="$2" origin="$3" target
    case "$key" in
        vmid|name|storage|snippet_storage|bridge|cores|ip|gateway|dns|repo_url|repo_ref|wait_minutes|no_wait|dry_run)
            target=$(printf '%s' "$key" | tr '[:lower:]' '[:upper:]') ;;
        ssh_public_key) target=SSH_KEY ;;
        *) die "$origin: unknown config key: $key" ;;
    esac
    case "$key" in
        no_wait|dry_run)
            case "$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')" in
                1|true|yes|on) value=1 ;;
                0|false|no|off) value=0 ;;
                *) die "$origin: $key must be true or false" ;;
            esac ;;
    esac
    printf -v "$target" '%s' "$value"
}

load_config() {
    local raw_line key value first last
    [[ -f "$CONFIG_FILE" && -r "$CONFIG_FILE" ]] || die "config file not found or unreadable: $CONFIG_FILE"
    while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
        CONFIG_LINE=$((CONFIG_LINE + 1))
        raw_line=$(trim_config_value "${raw_line%$'\r'}")
        [[ -z "$raw_line" || "$raw_line" == \#* ]] && continue
        [[ "$raw_line" == *=* ]] || die "$CONFIG_FILE:$CONFIG_LINE: expected key=value"
        key=$(trim_config_value "${raw_line%%=*}")
        value=$(trim_config_value "${raw_line#*=}")
        [[ "$key" =~ ^[a-z][a-z0-9_]*$ ]] || die "$CONFIG_FILE:$CONFIG_LINE: invalid config key: $key"
        first="${value:0:1}"
        if [[ "$first" == \" || "$first" == "'" ]]; then
            last="${value: -1}"
            [[ ${#value} -ge 2 && "$first" == "$last" ]] || die "$CONFIG_FILE:$CONFIG_LINE: unmatched quote for $key"
            value="${value:1:${#value}-2}"
        fi
        apply_setting "$key" "$value" "$CONFIG_FILE:$CONFIG_LINE"
    done < "$CONFIG_FILE"
    printf 'Using installer config from %s\n' "$CONFIG_FILE"
}

# Find the config before applying any CLI overrides, regardless of flag order.
find_config() {
    while (($#)); do
        case "$1" in
            -h|--help) usage; exit 0 ;;
            --no-wait|--dry-run) shift ;;
            --config|--config=*)
                [[ -z "$CONFIG_FILE" ]] || die '--config may only be specified once'
                if [[ "$1" == --config=* ]]; then
                    CONFIG_FILE=${1#--config=}; shift
                else
                    (($# >= 2)) || die '--config requires a file'
                    CONFIG_FILE=$2; shift 2
                fi
                [[ -n "$CONFIG_FILE" && "$CONFIG_FILE" != --* ]] || die '--config requires a file'
                ;;
            *)
                (($# >= 2)) || die "missing value for $1"
                shift 2 ;;
        esac
    done
}

find_config "$@"
[[ -z "$CONFIG_FILE" ]] || load_config
for key in vmid name storage snippet_storage bridge cores ip gateway dns ssh_public_key repo_url repo_ref wait_minutes no_wait dry_run; do
    environment_name="CTFD_$(printf '%s' "$key" | tr '[:lower:]' '[:upper:]')"
    if declare -p "$environment_name" >/dev/null 2>&1; then
        apply_setting "$key" "${!environment_name}" "$environment_name"
    fi
done

while (($#)); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --config) shift 2; continue ;;
        --config=*) shift; continue ;;
        --no-wait) NO_WAIT=1; shift; continue ;;
        --dry-run) DRY_RUN=1; shift; continue ;;
    esac
    (($# >= 2)) || die "missing value for $1"
    [[ -n "$2" && "$2" != --* ]] || die "missing value for $1"
    case "$1" in
        --vmid) VMID=$2 ;;
        --name) NAME=$2 ;;
        --storage) STORAGE=$2 ;;
        --snippet-storage) SNIPPET_STORAGE=$2 ;;
        --bridge) BRIDGE=$2 ;;
        --cores) CORES=$2 ;;
        --ip) IP=$2 ;;
        --gateway) GATEWAY=$2 ;;
        --dns) DNS=$2 ;;
        --ssh-public-key) SSH_KEY=$2 ;;
        --repo-url) REPO_URL=$2 ;;
        --repo-ref) REPO_REF=$2 ;;
        --wait-minutes) WAIT_MINUTES=$2 ;;
        *) die "unknown option: $1" ;;
    esac
    shift 2
done

for cmd in python3 ssh-keygen; do
    command -v "$cmd" >/dev/null || die "required command not found: $cmd"
done
[[ "$VMID" =~ ^[1-9][0-9]{2,8}$ ]] || die 'VMID must be between 100 and 999999999'
[[ "$CORES" =~ ^[1-9][0-9]{0,2}$ ]] || die 'cores must be a positive integer below 1000'
[[ "$WAIT_MINUTES" =~ ^[1-9][0-9]{0,2}$ ]] || die 'wait-minutes must be between 1 and 999'
[[ "$NAME" =~ ^[a-z][a-z0-9-]{0,61}[a-z0-9]$ || "$NAME" =~ ^[a-z]$ ]] || die 'invalid hostname'
for value in "$STORAGE" "$SNIPPET_STORAGE" "$BRIDGE"; do
    [[ "$value" =~ ^[a-zA-Z][a-zA-Z0-9_-]*$ ]] || die "invalid storage or bridge name: $value"
done
[[ -f "$SSH_KEY" ]] || die '--ssh-public-key must name an existing public key file'
ssh-keygen -l -f "$SSH_KEY" >/dev/null 2>&1 || die 'invalid SSH public key'
python3 - "$SSH_KEY" <<'PY'
from pathlib import Path
import sys
keys = [line.strip() for line in Path(sys.argv[1]).read_text().splitlines() if line.strip() and not line.startswith("#")]
if not keys or any(not line.startswith(("ssh-", "ecdsa-", "sk-")) for line in keys):
    sys.exit("Expected an OpenSSH public key file (not a private key)")
PY
[[ "$REPO_URL" == https://* && "$REPO_URL" != *[$'\r\n\t ']* ]] || die 'repo-url must be a public HTTPS URL'
[[ -n "$REPO_REF" && "$REPO_REF" != -* && "$REPO_REF" != *[$'\r\n']* ]] || die 'invalid repository ref'
python3 - "$IP" "$GATEWAY" "$DNS" <<'PY'
import ipaddress
import sys
address, gateway, dns = sys.argv[1:]
try:
    if address == "dhcp":
        if gateway:
            raise ValueError("--gateway requires --ip CIDR")
    else:
        if "/" not in address or not gateway:
            raise ValueError("static --ip requires a prefix and --gateway")
        interface = ipaddress.IPv4Interface(address)
        router = ipaddress.IPv4Address(gateway)
        if router not in interface.network or router == interface.ip:
            raise ValueError("gateway must be a different address in the same subnet")
    if dns:
        ipaddress.IPv4Address(dns)
except ValueError as exc:
    sys.exit(str(exc))
PY
[[ -f "$SCRIPT_DIR/bootstrap-guest.sh" ]] || die 'bootstrap-guest.sh must be beside this script'

printf 'VM %s (%s): Debian 12 minimal, %s cores, 4096 MiB RAM, 40 GiB disk\n' "$VMID" "$NAME" "$CORES"
printf 'Storage: %s; snippets: %s; bridge: %s; IPv4: %s\n' "$STORAGE" "$SNIPPET_STORAGE" "$BRIDGE" "$IP"
printf 'Guest clones %s (%s) into /opt/ctfd-https; SSH user: ctfd\n' "$REPO_URL" "$REPO_REF"
if ((DRY_RUN)); then
    echo 'Dry run: no downloads or Proxmox commands performed. Host availability checks run during installation.'
    exit 0
fi

[[ $EUID == 0 ]] || die 'run as root on a Proxmox VE node'
for cmd in qm pvesh pvesm curl sha512sum ip flock; do
    command -v "$cmd" >/dev/null || die "required Proxmox host command not found: $cmd"
done
[[ $(uname -m) == x86_64 ]] || die 'this installer requires an x86_64 Proxmox node'
# Serialize this installer's storage/cache changes on this node.
exec 9>/run/lock/ctfd-provision.lock
flock -n 9 || die 'another CTFd provisioning run is active'
pvesh get /cluster/resources --type vm --output-format json | python3 -c '
import json, sys
vmid = int(sys.argv[1])
if any(int(vm["vmid"]) == vmid for vm in json.load(sys.stdin)):
    sys.exit(f"VMID {vmid} already exists in this cluster; nothing was changed")
' "$VMID"
ip link show "$BRIDGE" >/dev/null || die "bridge does not exist: $BRIDGE"
[[ -d "/sys/class/net/$BRIDGE/bridge" ]] || die "$BRIDGE is not a Linux bridge"

storage_field() { python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"; }
disk_config=$(pvesh get "/storage/$STORAGE" --output-format json)
snippet_config=$(pvesh get "/storage/$SNIPPET_STORAGE" --output-format json)
[[ ",$(storage_field content <<<"$disk_config")," == *,images,* ]] || die "$STORAGE must support VM images"
[[ $(storage_field type <<<"$snippet_config") == dir ]] || die 'snippet storage must be directory-backed'
PVE_NODE=$(hostname -s)
for store in "$STORAGE" "$SNIPPET_STORAGE"; do
    pvesh get "/nodes/$PVE_NODE/storage" --storage "$store" --output-format json | python3 -c '
import json, sys
rows = json.load(sys.stdin)
if not rows or not rows[0].get("active") or not rows[0].get("enabled"):
    sys.exit("Storage is not active on this node")
'
done
snippet_path=$(storage_field path <<<"$snippet_config")
[[ "$snippet_path" == /* ]] || die 'snippet storage has no absolute directory path'
SNIPPET_DIR="$snippet_path/snippets"
SNIPPET_FILE="$SNIPPET_DIR/ctfd-$VMID-user.yaml"
[[ ! -e "$SNIPPET_FILE" ]] || die "snippet already exists: $SNIPPET_FILE; review the previous installation"

WORK_DIR=$(mktemp -d)
trap 'rm -rf -- "$WORK_DIR"' EXIT
trap 'echo "Provisioning stopped at line $LINENO. Any created VM and snippet were retained for diagnosis." >&2' ERR
CACHE=/var/lib/vz/template/cache/ctfd
install -d -m 0755 "$CACHE"
curl -fsSL --retry 3 "$IMAGE_BASE/SHA512SUMS" -o "$WORK_DIR/SHA512SUMS"
expected=$(awk -v name="$IMAGE_NAME" '$2 == name || $2 == "*" name {print $1; exit}' "$WORK_DIR/SHA512SUMS")
[[ "$expected" =~ ^[[:xdigit:]]{128}$ ]] || die 'Debian checksum list did not contain the expected image'
IMAGE="$CACHE/$IMAGE_NAME"
if [[ ! -f "$IMAGE" ]] || [[ $(sha512sum "$IMAGE" | awk '{print $1}') != "$expected" ]]; then
    echo 'Downloading Debian 12 cloud image...'
    curl -fL --retry 3 "$IMAGE_BASE/$IMAGE_NAME" -o "$WORK_DIR/$IMAGE_NAME"
    [[ $(sha512sum "$WORK_DIR/$IMAGE_NAME" | awk '{print $1}') == "$expected" ]] || die 'Debian image checksum mismatch'
    mv "$WORK_DIR/$IMAGE_NAME" "$IMAGE"
fi

# JSON is valid YAML; serialization keeps key comments, URLs and refs as data.
python3 - "$NAME" "$SSH_KEY" "$SCRIPT_DIR/bootstrap-guest.sh" "$REPO_URL" "$REPO_REF" > "$WORK_DIR/user.yaml" <<'PY'
import json
from pathlib import Path
import sys
name, keyfile, bootstrap, url, ref = sys.argv[1:]
keys = [line.strip() for line in Path(keyfile).read_text().splitlines() if line.strip() and not line.startswith("#")]
if not keys or any(not line.startswith(("ssh-", "ecdsa-", "sk-")) for line in keys):
    sys.exit("Expected an OpenSSH public key file (not a private key)")
config = {
    "hostname": name,
    "manage_etc_hosts": True,
    "disable_root": True,
    "ssh_pwauth": False,
    "users": [{"name": "ctfd", "shell": "/bin/bash", "lock_passwd": True,
               "sudo": "ALL=(ALL) NOPASSWD:ALL", "ssh_authorized_keys": keys}],
    "growpart": {"mode": "auto", "devices": ["/"], "ignore_growroot_disabled": False},
    "resize_rootfs": True,
    "write_files": [{"path": "/usr/local/sbin/bootstrap-ctfd", "permissions": "0700",
                     "owner": "root:root", "content": Path(bootstrap).read_text()}],
    "runcmd": [["/usr/local/sbin/bootstrap-ctfd", url, ref]],
}
print("#cloud-config")
print(json.dumps(config, indent=2))
PY
content=$(storage_field content <<<"$snippet_config")
if [[ ",$content," != *,snippets,* ]]; then
    echo "Enabling snippets on $SNIPPET_STORAGE (preserving existing content types)."
    pvesm set "$SNIPPET_STORAGE" --content "${content:+$content,}snippets"
fi
install -d -m 0755 "$SNIPPET_DIR"
# Create the VM before claiming the snippet, so qm also arbitrates cluster races.
qm create "$VMID" --name "$NAME" --memory 4096 --balloon 0 --cores "$CORES" \
    --cpu host --ostype l26 --scsihw virtio-scsi-single --agent enabled=1 \
    --serial0 socket --vga serial0 --onboot 1 --net0 "virtio,bridge=$BRIDGE"
install -m 0600 "$WORK_DIR/user.yaml" "$SNIPPET_FILE"
qm set "$VMID" --scsi0 "$STORAGE:0,import-from=$IMAGE,discard=on,iothread=1,ssd=1"
qm resize "$VMID" scsi0 40G
qm set "$VMID" --ide2 "$STORAGE:cloudinit" --boot order=scsi0 \
    --cicustom "user=$SNIPPET_STORAGE:snippets/ctfd-$VMID-user.yaml"
ipconfig="ip=$IP"
[[ -z "$GATEWAY" ]] || ipconfig+=",gw=$GATEWAY"
qm set "$VMID" --ipconfig0 "$ipconfig"
[[ -z "$DNS" ]] || qm set "$VMID" --nameserver "$DNS"
qm start "$VMID"
echo "VM started. Guest logs: /var/log/ctfd-provision.log and /var/log/cloud-init-output.log"
echo "Find its address with: qm guest cmd $VMID network-get-interfaces"
((NO_WAIT == 0)) || exit 0

echo "Waiting up to $WAIT_MINUTES minutes for CTFd HTTPS..."
deadline=$((SECONDS + WAIT_MINUTES * 60))
while ((SECONDS < deadline)); do
    result=$(qm guest exec "$VMID" --timeout 10 -- cat /var/lib/ctfd-provision/status 2>/dev/null || true)
    status=$(python3 -c '
import json,sys
try:
    result = json.load(sys.stdin)
    print(result.get("out-data", "").strip() if result.get("exitcode") == 0 else "")
except (ValueError, TypeError):
    pass
' <<<"$result")
    case "$status" in
        ready)
            echo "CTFd is ready. SSH as ctfd; open https://<VM-IP>/ to create the initial administrator."
            qm guest cmd "$VMID" network-get-interfaces
            exit 0 ;;
        failed) die "guest bootstrap failed; inspect /var/log/ctfd-provision.log in VM $VMID" ;;
    esac
    sleep 10
done
die "timed out waiting for VM $VMID; it is still running. Inspect the guest cloud-init and provisioning logs."
