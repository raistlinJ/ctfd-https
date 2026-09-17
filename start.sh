#!/bin/sh
# Create the persistent CTFd key before Compose resolves env_file.
set -eu

ctfd_project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ctfd_project_dir"

umask 077
mkdir -p secrets
if [ ! -e secrets/.env ]; then
    ctfd_key=$(openssl rand -hex 32)
    # Refuse to overwrite a key created by another concurrent invocation.
    (set -C; printf 'SECRET_KEY=%s\n' "$ctfd_key" > secrets/.env)
    unset ctfd_key
    echo "Created secrets/.env. Keep this file for subsequent starts."
fi

if ! grep -Eq '^SECRET_KEY=.+$' secrets/.env; then
    echo "secrets/.env must contain a nonempty SECRET_KEY; existing file was preserved." >&2
    exit 1
fi

echo "Starting Compose; CTFd's pull_policy checks the latest image in the registry."
exec docker compose -f docker-compose-https.yml up -d "$@"
