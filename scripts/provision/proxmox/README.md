# Provision CTFd on Proxmox

Run `install-ctfd.sh` as root **on the target Proxmox VE node**. It creates one
headless Debian 12 (Bookworm) VM with **40 GiB disk, 4096 MiB RAM, and 2 vCPUs**.
There is no desktop environment. Cloud-init installs Docker Engine, Compose,
and the QEMU guest agent, clones this repository into `/opt/ctfd-https`, and
starts the existing HTTPS stack. This follows ScenarioForge's Proxmox
cloud-image/import/cloud-init approach without requiring ScenarioForge files.

## Quick start

Copy this directory (both shell scripts) to the node, or clone the repository
there. Copy your **public** SSH key to the node, then from the repository root:

```sh
bash scripts/provision/proxmox/install-ctfd.sh \
  --vmid 9501 \
  --ssh-public-key /root/ctfd-admin.pub
```

Defaults are `local-lvm` for disks, `local` for snippets, and `vmbr0` with DHCP.
The selected bridge must already exist and provide Internet access and DNS.
Both the node and guest need outbound HTTPS; the guest also needs access to
Debian package mirrors. Allow inbound TCP 22, 80, and 443 to the guest as needed.
The node needs its normal Proxmox tools plus `curl`, `python3`, and `ssh-keygen`.

The script verifies the official Debian genericcloud amd64 image against its
published SHA-512 checksum, caches it, imports it, grows the disk to 40 GiB, and
configures the VM to start at host boot. Memory ballooning is disabled. If
necessary, it enables `snippets` on the selected directory storage while
preserving its other content types. Keep that snippet available for the VM;
when migrating between nodes, make it available on the destination as well.

The guest clones the **published** `main` branch by default. Local uncommitted
files are not copied. Select a published branch/tag or another public HTTPS
repository with `--repo-ref` and `--repo-url`. The selected version must include
`start.sh`, the Compose file, Nginx configuration, and the complete themes.

## Configuration file

Copy [ctfd.conf.example](ctfd.conf.example), set `ssh_public_key` to your public
key file on the node, and adjust the other settings:

```sh
cp scripts/provision/proxmox/ctfd.conf.example /root/ctfd.conf
# Edit /root/ctfd.conf, then preview:
bash scripts/provision/proxmox/install-ctfd.sh --config /root/ctfd.conf --dry-run
# Provision:
bash scripts/provision/proxmox/install-ctfd.sh --config /root/ctfd.conf
```

`--config=FILE` is also supported. Configuration is loaded only when explicitly
selected; specify at most one file. Precedence is **built-in defaults < config
file < `CTFD_*` environment variables < command-line flags**, regardless of where
`--config` appears among the flags. Each config key has an environment equivalent
formed by uppercasing and prefixing `CTFD_`, such as `CTFD_STORAGE` or
`CTFD_SSH_PUBLIC_KEY`. For example, `--config /root/ctfd.conf --vmid 9502` overrides
the configured VM ID. The disk and memory remain fixed at the requested sizes.

The format matches ScenarioForge: lowercase `key=value`, optional matching single
or double quotes, whitespace around keys/values, and full-line `#` comments.
Windows line endings are accepted. Boolean settings accept `true/false`,
`yes/no`, `on/off`, or `1/0`, case-insensitively. Unknown keys and malformed lines
are rejected with the file and line number; repeated keys use the last value.
Values are literal data: shell commands, variables, `~`, and backslash escapes
are not expanded. Relative paths use the installer's current working directory.
Use full-line comments; inline comments are not stripped.

## Network and storage options

```sh
bash scripts/provision/proxmox/install-ctfd.sh \
  --vmid 9502 --name ctfd-event \
  --storage local-lvm --snippet-storage local --bridge vmbr0 \
  --ip 192.168.1.50/24 --gateway 192.168.1.1 --dns 192.168.1.1 \
  --ssh-public-key /root/ctfd-admin.pub
```

Use your own reserved address, gateway, and DNS server. With DHCP, omit
`--ip` and `--gateway`. `--cores` changes the CPU count. Run `--help` for all
options. Add `--dry-run` to validate inputs and print the plan without downloads
or host changes; this also works outside Proxmox and does not check live storage,
network bridges, or VM availability.

## Access and operation

By default the installer waits up to 30 minutes for an HTTP response from CTFd
through Nginx over HTTPS. Use `--wait-minutes 60` on a slow connection or
`--no-wait` to return after boot. Retrieve the guest address once the agent starts:

```sh
qm guest cmd 9501 network-get-interfaces
ssh ctfd@192.168.1.50
```

The `ctfd` account uses the supplied SSH key and has passwordless `sudo`.
Password SSH authentication and root SSH login are disabled. Open
`https://<VM-IP>/` and complete CTFd's initial administrator/event setup. The
installer replaces the repository's example certificates with a fresh
self-signed certificate and private key for this VM, so expect a browser
certificate warning until you install a trusted certificate.

Inside the VM:

```sh
sudo tail -n 100 /var/log/ctfd-provision.log
sudo tail -n 100 /var/log/cloud-init-output.log
sudo cloud-init status --long
sudo cat /var/lib/ctfd-provision/status
cd /opt/ctfd-https
sudo docker compose -f docker-compose-https.yml ps
sudo sh start.sh
```

`start.sh` keeps the existing secret key and checks for the latest CTFd image.
Container restart policies bring the services back after a reboot. Back up
the deployment's `secrets`, `db`, `ctfd/uploads`, and `certs` along with any
custom themes. The provisioner uses the repository's existing Compose settings,
including its database credentials and image tags.

Existing VM IDs (including containers and VMs elsewhere in the cluster) and
existing installer snippets are rejected. A failed run or timeout leaves its VM
and snippet in place for diagnosis; the installer never destroys a VM. If
package installation fails before the agent is available, inspect the guest via
SSH. The serial console (`qm terminal 9501`) shows boot/cloud-init output, but
the SSH-only account has no console password. To retry from scratch, explicitly
remove the failed VM and its `ctfd-<VMID>-user.yaml` snippet after reviewing them,
or choose a new VM ID. Do not rerun the guest bootstrap against an existing event.

References: [Proxmox cloud-init](https://pve.proxmox.com/wiki/Cloud-Init_Support),
[Debian cloud images](https://cloud.debian.org/images/cloud/bookworm/latest/),
and [Docker's Debian installation instructions](https://docs.docker.com/engine/install/debian/).
