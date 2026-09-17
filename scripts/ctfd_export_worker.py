"""Run inside the disposable CTFd container; invoked by convert_ctfd_export.py."""

import shutil
import sys

from CTFd import create_app
from CTFd.utils.exports import export_ctf, import_ctf


def main():
    source, destination = sys.argv[1:]
    app = create_app()
    with app.app_context():
        # Only the converter's disposable database is reachable here.
        with open(source, "rb") as backup:
            import_ctf(backup, ignore_overrides=True)
        backup = export_ctf(ignore_overrides=True)
        try:
            with open(destination, "wb") as output:
                shutil.copyfileobj(backup, output)
        finally:
            backup.close()


if __name__ == "__main__":
    main()
