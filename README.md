# CTFd HTTPS and export conversion

## Starting the HTTPS deployment

With Docker Compose and OpenSSL installed, run:

```sh
sh start.sh
```

This creates `secrets/.env` with a random CTFd `SECRET_KEY` before starting
Compose. Subsequent starts reuse the existing key. Keep this file with your
deployment; it is excluded from Git. Extra arguments are passed to `compose up`,
for example `sh start.sh --build`.

Compose reads `env_file` before starting any containers, so a Compose init
service cannot create a required env file on the first run. The key is supplied
only through `env_file`: an explicit `environment.SECRET_KEY` would override it.

The deployment is pinned to CTFd 3.8.7, matching the converted backups. After
copying the updated Compose file to an existing server, upgrade its app with:

```sh
docker compose -f docker-compose-https.yml pull ctfd
docker compose -f docker-compose-https.yml up -d --no-deps ctfd
docker compose -f docker-compose-https.yml exec ctfd python -c 'from CTFd import __version__; print(__version__)'
```

Confirm that the last command prints `3.8.7`, then retry the Web UI import.
An older image (including a locally cached `latest` tag) may reject these ZIPs
with “The target migration in this backup is not available in this version of
CTFd.” Restarting an existing container alone does not upgrade its image.

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
