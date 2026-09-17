#!/usr/bin/env python3
"""Upgrade a CTFd 2.x/3.x export using the target release's real migrations.

Requires Python 3.9+ and Docker. No host Python packages are needed. All database
work happens in disposable containers, with no published ports or host DB access.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

DEFAULT_IMAGE = "ctfd/ctfd:3.8.7"


def rows(archive, name):
    raw = archive.read(name)
    if not raw.strip():
        return []
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError(f"Invalid CTFd table: {name}")
    if any(not isinstance(row, dict) for row in data["results"]):
        raise ValueError(f"Invalid records in {name}")
    return data["results"]


def inspect_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate ZIP member names are not supported")
        for info in archive.infolist():
            name = info.filename
            # Match CTFd's strict path rules before it extracts uploads.
            if (name.startswith("/") or ".." in name or "\\" in name
                    or "//" in name or ":" in name
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError(f"Unsafe ZIP member: {name!r}")
        revision_rows = rows(archive, "db/alembic_version.json")
        if len(revision_rows) != 1 or not revision_rows[0].get("version_num"):
            raise ValueError("Missing database revision")
        tables = {name: rows(archive, name) for name in names
                  if name.startswith("db/") and name.endswith(".json")}
        config = {r["key"]: r["value"] for r in tables.get("db/config.json", [])}
        return revision_rows[0]["version_num"], config.get("ctf_version", "unknown"), tables


def digest_member(archive, name):
    digest = hashlib.sha256()
    with archive.open(name) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def check_preservation(before, after, exact=False):
    _, _, old_tables = inspect_archive(before)
    _, _, new_tables = inspect_archive(after)
    for name, records in old_tables.items():
        if name in ("db/alembic_version.json", "db/config.json") and not exact:
            continue  # Migrations intentionally update version/configuration.
        converted = new_tables.get(name, [])
        if len(records) != len(converted):
            raise ValueError(f"Record count changed in {name}: {len(records)} -> {len(converted)}")
        if records and all("id" in row for row in records):
            if sorted(r["id"] for r in records) != sorted(r["id"] for r in converted):
                raise ValueError(f"Record IDs changed in {name}")
        if exact:
            canonical = lambda rs: sorted(json.dumps(r, sort_keys=True) for r in rs)
            if canonical(records) != canonical(converted):
                raise ValueError(f"Data changed on verification import: {name}")
    if exact and set(old_tables) != set(new_tables):
        raise ValueError("Tables changed on verification import")
    # Verify particularly sensitive values survive the migration, not just counts.
    for table, fields in {"users": ("password", "email", "team_id"),
                          "teams": ("password", "captain_id"),
                          "flags": ("content", "data"),
                          "submissions": ("provided", "type", "challenge_id", "user_id", "team_id"),
                          "solves": ("challenge_id", "user_id", "team_id")}.items():
        name = f"db/{table}.json"
        by_id = {r["id"]: r for r in new_tables.get(name, [])}
        for row in old_tables.get(name, []):
            for field in fields:
                if field in row and row[field] != by_id[row["id"]].get(field):
                    raise ValueError(f"Migration changed {table}.{field}")
    with zipfile.ZipFile(before) as old, zipfile.ZipFile(after) as new:
        uploads = lambda z: {i.filename for i in z.infolist()
                             if i.filename.startswith("uploads/") and not i.is_dir()}
        if uploads(old) != uploads(new):
            raise ValueError("Upload paths changed during conversion")
        for name in uploads(old):
            if digest_member(old, name) != digest_member(new, name):
                raise ValueError(f"Upload contents changed: {name}")
        return len(uploads(old))


def compress_archive(source, destination):
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as dst:
        for info in src.infolist():
            with src.open(info) as incoming, dst.open(info.filename, "w", force_zip64=True) as outgoing:
                shutil.copyfileobj(incoming, outgoing)


def convert(args):
    source = args.input.expanduser().resolve(strict=True)
    output = (args.output or source.with_name(source.stem + "-converted.zip")).expanduser().resolve()
    if output == source:
        raise ValueError("Input and output must be different files")
    if output.exists():
        raise ValueError(f"Output already exists: {output}; choose a new output path")
    if not output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {output.parent}")
    revision, version, tables = inspect_archive(source)
    print(f"Source: CTFd {version}, database revision {revision}", flush=True)
    if not shutil.which("docker"):
        raise ValueError("Docker is required; start Docker Desktop and retry")
    worker = Path(__file__).resolve().parent / "scripts" / "ctfd_export_worker.py"
    if not worker.is_file():
        raise ValueError(f"Missing container helper: {worker}")
    prefix = "ctfd-convert-" + uuid.uuid4().hex[:12]
    database, runner = prefix + "-db", prefix + "-app"
    containers = []
    network_created = False
    log_path = output.with_suffix(output.suffix + ".log")
    # Keep database errors (which may contain private records) in a private log.
    with log_path.open("x", encoding="utf-8") as log, tempfile.TemporaryDirectory(prefix="ctfd-convert-") as tmp:
        os.chmod(log_path, 0o600)
        work = Path(tmp)

        def docker(*command, timeout=900):
            result = subprocess.run(["docker", *command], stdout=log, stderr=log, timeout=timeout)
            log.flush()
            if result.returncode:
                raise RuntimeError(f"Docker {command[0]} failed; details: {log_path}")

        try:
            docker("info", "--format", "{{.ServerVersion}}", timeout=30)
            print(f"Preparing {args.image} and MariaDB (first run downloads images)...", flush=True)
            for image in (args.image, "mariadb:10.11"):
                available = subprocess.run(["docker", "image", "inspect", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if available.returncode:
                    docker("pull", image)
            docker("network", "create", "--internal", prefix)
            network_created = True
            password = secrets.token_hex(24)
            containers.append(database)
            docker("run", "-d", "--name", database, "--network", prefix,
                   "-e", f"MARIADB_ROOT_PASSWORD={secrets.token_hex(24)}",
                   "-e", "MARIADB_DATABASE=ctfd", "-e", "MARIADB_USER=ctfd",
                   "-e", f"MARIADB_PASSWORD={password}", "mariadb:10.11")
            deadline = time.monotonic() + 120
            while True:
                result = subprocess.run(["docker", "exec", database, "healthcheck.sh", "--connect", "--innodb_initialized"],
                                        stdout=log, stderr=log, timeout=15)
                if result.returncode == 0:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError(f"MariaDB did not become ready; see {log_path}")
                time.sleep(1)
            shutil.copyfile(worker, work / "worker.py")
            # Copy only the selected export into the temporary mount.
            shutil.copyfile(source, work / "input.zip")

            def migrate(input_name, output_name):
                containers.append(runner)
                docker("run", "--name", runner, "--network", prefix,
                       "--user", "0:0", "--entrypoint", "python",
                       "-e", f"DATABASE_URL=mysql+pymysql://ctfd:{password}@{database}/ctfd",
                       "-e", f"SECRET_KEY={secrets.token_hex(32)}",
                       "-e", "UPLOAD_FOLDER=/tmp/ctfd-uploads", "-e", "PYTHONPATH=/opt/CTFd",
                       "--mount", f"type=bind,source={work},target=/conversion",
                       args.image, "/conversion/worker.py", f"/conversion/{input_name}", f"/conversion/{output_name}")
                docker("rm", "-v", runner)
                containers.remove(runner)

            print("Importing, extracting uploads, and applying CTFd migrations...", flush=True)
            migrate("input.zip", "migrated.zip")
            upload_count = check_preservation(work / "input.zip", work / "migrated.zip")
            compress_archive(work / "migrated.zip", work / "converted.zip")
            check_preservation(work / "migrated.zip", work / "converted.zip", exact=True)
            print("Verifying the converted ZIP with a second clean import...", flush=True)
            migrate("converted.zip", "verified.zip")
            check_preservation(work / "converted.zip", work / "verified.zip", exact=True)
            target_revision, target_version, _ = inspect_archive(work / "converted.zip")
            # Exclusive creation prevents overwriting an existing backup.
            try:
                with output.open("xb") as dest:
                    os.chmod(output, 0o600)
                    with (work / "converted.zip").open("rb") as src:
                        shutil.copyfileobj(src, dest)
            except FileExistsError:
                raise ValueError(f"Output appeared during conversion: {output}") from None
            except BaseException:
                output.unlink(missing_ok=True)
                raise
            print(f"Created {output}\nTarget: CTFd {target_version}, database revision {target_revision}")
            print(f"Verified {len(tables)} source tables and {upload_count} uploads; original export unchanged.")
        finally:
            for container in reversed(containers):
                subprocess.run(["docker", "rm", "-f", "-v", container], stdout=log, stderr=log, timeout=30)
            if network_created:
                subprocess.run(["docker", "network", "rm", prefix], stdout=log, stderr=log, timeout=30)
    log_path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Original CTFd export ZIP")
    parser.add_argument("-o", "--output", type=Path, help="New ZIP (default: INPUT-converted.zip)")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help=f"Target CTFd Docker image (default: {DEFAULT_IMAGE})")
    args = parser.parse_args()
    try:
        convert(args)
    except (ValueError, OSError, RuntimeError, KeyError, zipfile.BadZipFile, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Conversion failed: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Conversion cancelled.\n")


if __name__ == "__main__":
    main()
