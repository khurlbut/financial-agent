from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
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
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright  # type: ignore

    # Export generation can be slow; be generous.
    export_download_timeout_ms = 120_000

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

    debug_prefix = downloads_dir / f"schwab_debug_{as_of.strftime('%Y%m%d_%H%M%S')}"

    def _looks_like_auth_flow(url: str) -> bool:
        u = url.lower()
        return any(tok in u for tok in ("/login", "logon", "mfa", "authentication", "auth"))

    def _wait_for_user_auth(page) -> None:
        # Keep this human-in-the-loop: user completes login/MFA in the real browser.
        prompt = "Complete login/MFA in the opened browser, then press Enter..."
        input(f"{prompt}\n(Current URL: {page.url})\n")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            accept_downloads=True,
            downloads_path=str(downloads_dir),
        )
        try:
            page = context.new_page()
            # Helpful, low-noise signal when a download starts.
            try:
                page.on(
                    "download",
                    lambda d: print(f"Download event: {d.suggested_filename}") or None,
                )
            except Exception:
                pass

            # Seed the session and then go straight to Positions.
            page.goto(base_url, wait_until="domcontentloaded")

            # Navigate to positions; if we get bounced back to auth, prompt and retry.
            last_err: Exception | None = None
            for attempt in range(1, 4):
                print(f"Schwab export attempt {attempt}/3: navigating to Positions…")
                page.goto(positions_url, wait_until="domcontentloaded")
                _assert_schwab_url(page.url)

                # If Schwab redirects back to auth, pause and let the user finish MFA.
                if _looks_like_auth_flow(page.url):
                    _wait_for_user_auth(page)
                    continue

                try:
                    # Schwab pages often keep long-lived connections (streaming/polling), so "networkidle"
                    # can time out forever. Instead, wait for the key UI control we need.
                    page.locator("button#positionspageheader-utility-bar-export-button").wait_for(
                        state="visible", timeout=60_000
                    )

                    def _click_export() -> None:
                        # Schwab often renders Export as an icon-only button with aria-label/title="Export".
                        # The legacy hidden <button id="export">export</button> exists too; avoid text-only.
                        export_locators = [
                            page.locator("button#positionspageheader-utility-bar-export-button"),
                            page.locator('sdps-button[sdps-id="positionspageheader-utility-bar-export"] button'),
                            page.locator(export_button_selector),
                            page.get_by_role("button", name=re.compile(r"^export$", re.I)),
                        ]

                        last: Exception | None = None
                        for loc in export_locators:
                            try:
                                loc.first.wait_for(state="visible", timeout=60_000)
                                loc.first.click(timeout=60_000)
                                return
                            except Exception as exc:
                                last = exc
                                continue

                        # As a last resort, click the legacy hidden export button.
                        try:
                            legacy = page.locator("button#export")
                            legacy.first.wait_for(state="attached", timeout=5_000)
                            legacy.first.click(timeout=60_000, force=True)
                            return
                        except Exception as exc:
                            last = exc

                        raise RuntimeError(
                            f"Could not find/click Export control. selector={export_button_selector!r}; last_error={last}"
                        )

                    def _click_csv_item(target_page) -> None:
                        candidates = []
                        if export_csv_selector:
                            candidates.append(target_page.locator(export_csv_selector))

                        candidates.extend(
                            [
                                target_page.get_by_role("menuitem", name=re.compile(r"csv", re.I)),
                                target_page.get_by_role("button", name=re.compile(r"csv", re.I)),
                                target_page.locator("text=/\\bCSV\\b/i"),
                                # Some export dialogs just say "Download".
                                target_page.get_by_role("button", name=re.compile(r"download", re.I)),
                                # Icon-based selectors (often more stable than text and can work through shadow DOM).
                                target_page.locator(
                                    'use[xlink\\:href="#icon-sch-file-csv"], use[href="#icon-sch-file-csv"]'
                                )
                                .locator("xpath=ancestor::button[1]"),
                                target_page.locator(
                                    'use[xlink\\:href="#icon-sch-file-csv"], use[href="#icon-sch-file-csv"]'
                                )
                                .locator("xpath=ancestor::*[@role='menuitem'][1]"),
                            ]
                        )

                        last: Exception | None = None
                        for loc in candidates:
                            try:
                                loc.first.wait_for(state="visible", timeout=60_000)
                                loc.first.click(timeout=60_000)
                                return
                            except Exception as exc:
                                last = exc
                                continue

                        raise RuntimeError(
                            "Export click did not start a download, and no CSV option was clickable. "
                            f"export_csv_selector={export_csv_selector!r}; last_error={last}"
                        )

                    # Preferred path: Export triggers a download directly.
                    try:
                        with page.expect_download(timeout=export_download_timeout_ms) as d:
                            _click_export()
                        download = d.value
                    except PlaywrightTimeoutError:
                        # Some UIs open a popup window/tab for Export.
                        popup = None
                        try:
                            with page.expect_popup(timeout=5_000) as pinfo:
                                _click_export()
                            popup = pinfo.value
                        except Exception:
                            popup = None

                        if popup is not None:
                            print(f"Export opened popup: {popup.url}")
                            popup.wait_for_load_state("domcontentloaded", timeout=60_000)
                            with popup.expect_download(timeout=export_download_timeout_ms) as d:
                                _click_csv_item(popup)
                            download = d.value
                            try:
                                popup.close()
                            except Exception:
                                pass
                        else:
                            # Otherwise, assume Export opened an in-page menu/modal; click export again to ensure it's open,
                            # then click a CSV item inside an expect_download.
                            try:
                                print(
                                    "No download after clicking Export. "
                                    f"visible dialogs={page.locator('[role=dialog]:visible').count()} "
                                    f"visible menus={page.locator('[role=menu]:visible').count()}"
                                )
                            except Exception:
                                pass
                            _click_export()
                            with page.expect_download(timeout=export_download_timeout_ms) as d:
                                _click_csv_item(page)
                            download = d.value

                    download.save_as(str(out_path))
                    return DownloadedFile(path=out_path, as_of=as_of)
                except Exception as exc:
                    last_err = exc
                    try:
                        page.screenshot(path=str(debug_prefix.with_suffix(f".attempt{attempt}.png")), full_page=True)
                    except Exception:
                        pass
                    try:
                        debug_prefix.with_suffix(f".attempt{attempt}.html").write_text(page.content(), encoding="utf-8")
                    except Exception:
                        pass

                    # Only prompt the user if we're actually on an auth flow.
                    if _looks_like_auth_flow(page.url):
                        _wait_for_user_auth(page)

                    # Otherwise, continue retrying without blocking on stdin.
                    continue

            raise RuntimeError(
                "Failed to download Schwab positions CSV after multiple attempts. "
                f"See debug files: {debug_prefix}.* (and check selectors/URL). "
                f"Last error: {last_err}"
            )
        finally:
            try:
                context.close()
            except Exception:
                # Common when user hits Ctrl-C while Playwright is mid-flight.
                pass
