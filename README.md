# CTFd HTTPS and export conversion

## Provisioning on Proxmox

The [Proxmox installer](scripts/provision/proxmox/README.md) creates a minimal
Debian 12 VM with a **40 GiB disk, 4 GiB RAM, and 2 vCPUs**, installs Docker, and
starts this HTTPS deployment. Run it as root on the Proxmox node:

```sh
bash scripts/provision/proxmox/install-ctfd.sh --ssh-public-key /root/ctfd-admin.pub
```

Defaults: VM ID `9501`, disk storage `local-lvm`, snippet storage `local`,
bridge `vmbr0`, and DHCP. Use `--help` for static networking and other options,
or `--dry-run` to preview. The guest clones the published `main` branch.
For repeatable settings, copy
[`ctfd.conf.example`](scripts/provision/proxmox/ctfd.conf.example) and pass
`--config /root/ctfd.conf`. Precedence is defaults, config, `CTFD_*` environment,
then command-line flags.

## Starting the HTTPS deployment

This deployment uses one Compose file, `docker-compose-https.yml`, and a root
`.env` for configuration. The default application is your custom fork:

```dotenv
COMPOSE_FILE=docker-compose-https.yml
CTFD_SOURCE_URL='https://github.com/raistlinJ/CTFd.git#feature/retract-solves'
```

With Docker Compose v2 and OpenSSL installed, initialize the configuration:

```sh
sh start.sh --init
```

This creates `.env` from [`.env.example`](.env.example) and creates
`secrets/.env` with a random CTFd signing key if absent. Existing configuration
and signing keys are preserved. Both local files are gitignored. Edit `.env`
to change the source repository/revision, image tags, HTTP/HTTPS ports, workers,
and database connection settings. Keep the existing database credentials when
updating an initialized deployment; changing `.env` alone does not change the
accounts stored in MariaDB. For a new database, keep `MYSQL_*` and
`CTFD_DATABASE_URL` consistent, URL-encoding credentials in the URL.

Then start the stack:

```sh
sudo sh start.sh
```

Or use Compose directly from this directory; `.env` selects the single file:

```sh
sudo docker compose config --quiet
sudo docker compose up -d --build
```

The launcher also accepts Compose `up` arguments, such as
`sudo sh start.sh --force-recreate ctfd`. It builds the selected Git source and
uses its bundled themes and compiled frontend. The existing `db`, `ctfd/uploads`,
`ctfd/logs`, `certs`, and signing-key paths are retained. The host `ctfd/themes`
directory is no longer mounted over the source image. The permissions helper
runs without networking. The initial build can take several minutes.

### Changing the CTFd source

Edit `CTFD_SOURCE_URL` in `.env` and rebuild. Append `#branch`, `#tag`, or a full
commit SHA. The configurable solve-retraction revision is
`126448dceafc866a540a1dc1a44d45ee66811e5e`. To build stock CTFd, set the source to
`https://github.com/CTFd/CTFd.git#3.8.9` instead.

For compatibility, a source can also be selected with:

```sh
sudo sh start.sh --source-url 'https://github.com/raistlinJ/CTFd.git#feature/retract-solves'
```

The launcher saves this selection in `.env`, so subsequent starts use the same
source without repeating the option. An explicit option overrides a supplied
`CTFD_SOURCE_URL` environment variable. `.env` is read as configuration data,
never executed as a shell script. Verify the running application after startup:

```sh
sudo docker compose exec ctfd python -c 'from CTFd import __version__; print(__version__)'
```

Both the fork and stock CTFd can report the same release version. The selected
Git revision in `.env` identifies the custom build. Enable **Users Can Clear
Submissions** in **Config → Challenges**, or refresh DeployForge's CTFd Manager
and use its switch.

The Proxmox installer accepts the same `--source-url` option or `source_url`
config key and saves it in the guest's `.env`. `--repo-url` and `--repo-ref`
select the separate HTTPS deployment repository. There is no source overlay to
combine with the base file.

## Updating the HTTPS proxy

The proxy image is pinned to Nginx 1.30.5, which includes the fix for
[CVE-2026-42533](https://nginx.org/en/security_advisories.html) (fixed in stable
1.30.4 and later). Keep this pin current when applying future security updates.

On an existing deployment, pull the updated repository and recreate only the
proxy with the pinned image. This briefly interrupts HTTP and HTTPS access:

```sh
git pull
docker compose -f docker-compose-https.yml pull nginx
docker compose -f docker-compose-https.yml up -d --no-deps --force-recreate nginx
docker compose -f docker-compose-https.yml exec nginx nginx -t
docker compose -f docker-compose-https.yml exec nginx nginx -v
```

Verify that the running proxy reports `nginx/1.30.5`, then rerun the vulnerability
scan against the same host and port. Restarting or reloading the old container
alone does not update its image; updating this repository alone does not patch
a running deployment.

## CTFd export conversion

Convert an older CTFd export to a current export with Python 3.9+ and Docker:

```sh
python3 convert_ctfd_export.py old-format.zip -o old-format-ctfd-3.8.7.zip
```

The default target is **CTFd 3.8.7**, the latest stable release checked on
2026-09-17 ([upstream releases](https://github.com/CTFd/CTFd/releases)). The first
run downloads the CTFd and MariaDB images. Allow several GB of free disk space
for images, temporary uploads, and verification exports.

The converter uses CTFd's actual import, extraction, database migrations, and
export code. It creates a temporary internal Docker network and MariaDB database,
imports your ZIP, and exports the upgraded database and uploads as a compressed
ZIP. It then imports that ZIP again and checks that every exported table and
upload survives the second import unchanged. It also checks source record counts,
IDs, passwords, flags, submission values, solve relationships, and SHA-256 hashes
of all uploads. It leaves the source ZIP unchanged, refuses to overwrite output,
and removes its temporary containers, volumes, and network when finished.

It does not connect to your running CTFd database or expose conversion ports.
On failure, details are retained in `<output>.log` with owner-only permissions;
logs can contain private database values. Backups and these logs are gitignored.

To target another release, select its image explicitly:

```sh
python3 convert_ctfd_export.py backup.zip --image ctfd/ctfd:3.8.7
```

By default, the output is `backup-converted.zip`. Support is limited to exports
that the chosen CTFd image can migrate (normally stock CTFd 2.x/3.x). CTFd 1.x,
unknown/hosted-only schema revisions, or unavailable plugins may need a separate
migration. Custom plugin functionality requires a target image containing those
plugins; this script cannot reconstruct plugin code from a backup. Theme/config
changes made by official migrations are retained.

## Sample results

`old-format.zip` identifies itself as CTFd 3.7.6, revision `a49ad66aa0f1`.
The original ZIP **already imports successfully into stock CTFd 3.8.7** with
MariaDB 10.11. The converted export has revision `48d8250d19bd` and successfully
passes a second import. It preserves 7 challenges, 30 users, 12 teams,
179 submissions, 76 solves, and all 195 uploaded files. Password hashes are
preserved, so existing account passwords still apply.

The local verification instance is running at <http://localhost:8001>, using the
converted export and the existing account credentials. Its database is separate
from the main Compose deployment. The public home/login pages and authenticated
admin challenge, user, team, and scoreboard pages passed HTTP checks.
To remove this test instance and its imported data:

```sh
docker rm -f -v ctfd-export-conversion-app ctfd-export-conversion-db
docker network rm ctfd-export-conversion-test
```

## Importing through this repository's Nginx proxy

The sample ZIP is about 230 MiB. The proxy's default `client_max_body_size 16m`
rejects an upload that large before it reaches CTFd, typically with HTTP 413.
`nginx/conf.d/ctfd.conf` now allows 512 MiB specifically on `/admin/import`.
Apply the configuration to an already-running deployment with:

```sh
docker compose -f docker-compose-https.yml exec nginx nginx -t
docker compose -f docker-compose-https.yml exec nginx nginx -s reload
```

Then import the converted ZIP from **Admin → Config → Backup → Import**.
CTFd imports replace the destination event's database; use an empty instance
or keep a backup of that instance first.

To bypass HTTP upload limits, copy the ZIP into the running CTFd container and
invoke its importer directly:

```sh
docker compose -f docker-compose-https.yml cp old-format-ctfd-3.8.7.zip ctfd:/tmp/converted.zip
docker compose -f docker-compose-https.yml exec ctfd python manage.py import_ctf /tmp/converted.zip
docker compose -f docker-compose-https.yml exec ctfd rm /tmp/converted.zip
```

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Every successful converter run also includes a real import/export/import test
against the selected CTFd image and MariaDB 10.11.
