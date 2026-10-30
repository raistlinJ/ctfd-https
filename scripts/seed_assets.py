"""Populate host bind mounts from the selected image without overwriting edits."""
import argparse
import os
from pathlib import Path
import shutil


def seed_assets(source: Path, destination: Path) -> int:
    if not source.is_dir():
        raise RuntimeError(f'Bundled CTFd assets not found: {source}')
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for original in sorted(source.rglob('*')):
        relative = original.relative_to(source)
        if '__pycache__' in relative.parts or original.suffix == '.pyc':
            continue
        target = destination / relative
        if target.exists() or target.is_symlink():
            continue
        if original.is_symlink():
            target.symlink_to(os.readlink(original))
        elif original.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            copied += 1
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/opt/CTFd/CTFd'))
    parser.add_argument('--destination', type=Path, default=Path('/host'))
    args = parser.parse_args()
    for name in ('themes', 'plugins'):
        count = seed_assets(args.source / name, args.destination / name)
        print(f'{name}: added {count} missing files; existing local files preserved.')


if __name__ == '__main__':
    main()
