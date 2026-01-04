from __future__ import annotations

import argparse
from pathlib import Path

from . import settings
from .schwab_csv.downloader import download_positions_csv
from .schwab_csv.importer import import_positions_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh Schwab holdings by downloading and importing a positions CSV")
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Import an existing Schwab positions CSV instead of downloading one",
    )
    args = parser.parse_args()

    db_path = settings.get_finagent_db_path()

    if args.csv:
        csv_path = Path(args.csv).expanduser()
        if not csv_path.exists():
            raise SystemExit(f"CSV not found: {csv_path}")
        imported = import_positions_csv(db_path=db_path, csv_path=csv_path)
        print(f"Imported {imported.rows_imported} rows into {db_path}")
        return

    downloaded = download_positions_csv(
        profile_dir=settings.get_schwab_profile_dir(),
        downloads_dir=settings.get_schwab_downloads_dir(),
        base_url="https://client.schwab.com/",
        positions_url=settings.get_schwab_positions_url(),
        export_button_selector=settings.get_schwab_export_button_selector(),
        export_csv_selector=settings.get_schwab_export_csv_selector(),
    )
    imported = import_positions_csv(db_path=db_path, csv_path=downloaded.path, as_of=downloaded.as_of)
    print(f"Downloaded {downloaded.path}")
    print(f"Imported {imported.rows_imported} rows into {db_path}")


if __name__ == "__main__":
    main()
