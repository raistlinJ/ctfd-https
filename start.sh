#!/bin/sh
# Create the persistent CTFd key before Compose resolves env_file.
set -eu

ctfd_project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ctfd_project_dir"

usage() {
    cat <<'EOF'
Usage: sh start.sh [--source-url URL] [compose-up arguments...]

Start CTFd HTTPS with the official latest image, or build CTFd from a public
HTTPS Git URL. Append #branch, #tag, or #full-commit-sha to select a version.
CTFD_SOURCE_URL supplies the same option through the environment.

Example:
  sh start.sh --source-url 'https://github.com/raistlinJ/CTFd.git#feature/retract-solves'

Source mode requires Docker Compose 2.24.4+ and uses the source's bundled themes.
EOF
}

ctfd_source_url=${CTFD_SOURCE_URL:-}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --source-url)
            if [ "$#" -lt 2 ] || [ -z "$2" ]; then
                echo "--source-url requires an HTTPS Git URL." >&2
                exit 1
            fi
            ctfd_source_url=$2
            shift 2
            ;;
        --source-url=*)
            ctfd_source_url=${1#--source-url=}
            [ -n "$ctfd_source_url" ] || { echo "--source-url requires an HTTPS Git URL." >&2; exit 1; }
            shift
            ;;
        -h|--help) usage; exit 0 ;;
        *) break ;;
    esac
done

if [ -n "$ctfd_source_url" ]; then
    case "$ctfd_source_url" in
        https://?*) ;;
        *) echo "--source-url must be a public HTTPS Git URL." >&2; exit 1 ;;
    esac
    case "$ctfd_source_url" in
        *[[:space:]]*) echo "--source-url must not contain whitespace." >&2; exit 1 ;;
    esac
fi

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

if [ -n "$ctfd_source_url" ]; then
    export CTFD_SOURCE_URL="$ctfd_source_url"
    printf 'Building and starting CTFd from %s\n' "$ctfd_source_url"
    exec docker compose -f docker-compose-https.yml -f docker-compose-source.yml up -d --build "$@"
fi

echo "Starting Compose; CTFd's pull_policy checks the latest image in the registry."
exec docker compose -f docker-compose-https.yml up -d "$@"
