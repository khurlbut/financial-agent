from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class DownloadedFile:
    path: Path
    as_of: datetime


def download_positions_csv(
    *,
    profile_dir: Path,
    downloads_dir: Path,
    base_url: str,
    positions_url: str,
    export_button_selector: str,
    export_csv_selector: str | None,
) -> DownloadedFile:
    """Download a Schwab positions CSV using Playwright.

    This runs headful with a persistent profile so you can complete login/MFA manually.
    We do not attempt to bypass MFA.
    """

    # Lazy import so Playwright is only required when using this feature.
    from playwright.sync_api import sync_playwright  # type: ignore

    profile_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    def _assert_schwab_url(url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if not host.endswith("schwab.com"):
            raise RuntimeError(f"Refusing to navigate to non-Schwab domain: {url}")

    _assert_schwab_url(base_url)
    _assert_schwab_url(positions_url)

    as_of = datetime.now(timezone.utc)
    filename = f"schwab_positions_{as_of.strftime('%Y%m%d_%H%M%S')}.csv"
    out_path = downloads_dir / filename

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            accept_downloads=True,
            downloads_path=str(downloads_dir),
        )
        try:
            page = context.new_page()
            page.goto(base_url)

            input("Complete login/MFA in the opened browser if prompted, then press Enter...")

            page.goto(positions_url)
            _assert_schwab_url(page.url)

            with page.expect_download() as d:
                page.click(export_button_selector)
                if export_csv_selector:
                    page.click(export_csv_selector)

            download = d.value
            download.save_as(str(out_path))
            return DownloadedFile(path=out_path, as_of=as_of)
        finally:
            context.close()
