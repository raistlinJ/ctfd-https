#!/bin/sh
# Create the persistent CTFd key before Compose resolves env_file.
set -eu

ctfd_project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ctfd_project_dir"

usage() {
    cat <<'EOF'
Usage: sh start.sh [--init] [--source-url URL] [compose-up arguments...]

Start CTFd HTTPS using .env and docker-compose-https.yml.
The default source is the raistlinJ/CTFd feature/retract-solves branch.
Edit .env to configure the source, ports, images, workers, and database.
--source-url or CTFD_SOURCE_URL saves a source selection into .env for later starts.
--init creates missing configuration/signing-key files without starting containers.

Example:
  sh start.sh --source-url 'https://github.com/raistlinJ/CTFd.git#feature/retract-solves'

Local themes/ and plugins/ are mounted into CTFd. Missing bundled files are seeded
from the selected source image before startup. Only Docker Compose v2 is required.
EOF
}

ctfd_source_url=${CTFD_SOURCE_URL:-}
ctfd_init_only=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --init) ctfd_init_only=1; shift ;;
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
if [ ! -e .env ]; then
    (set -C; cat .env.example > .env)
    echo "Created .env from .env.example. Edit it to configure this deployment."
fi

if [ -n "$ctfd_source_url" ]; then
    # Quote literal dotenv data; never execute .env as a shell script.
    ctfd_source_quoted=$(printf '%s' "$ctfd_source_url" | sed 's/\\/\\\\/g; s/'"'"'/\\'"'"'/g')
    ctfd_config_tmp=$(mktemp .env.XXXXXX)
    trap 'rm -f -- "$ctfd_config_tmp"' 0
    awk '!/^[[:space:]]*(export[[:space:]]+)?CTFD_SOURCE_URL[[:space:]]*=/' .env > "$ctfd_config_tmp"
    printf "\nCTFD_SOURCE_URL='%s'\n" "$ctfd_source_quoted" >> "$ctfd_config_tmp"
    mv -- "$ctfd_config_tmp" .env
    export CTFD_SOURCE_URL="$ctfd_source_url"
    echo "Saved CTFd source selection in .env."
fi

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

if [ "$ctfd_init_only" -eq 1 ]; then exit 0; fi

echo "Building and starting CTFd using .env and docker-compose-https.yml."
exec docker compose --env-file .env -f docker-compose-https.yml up -d --build "$@"
