#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FAZ / FAS → Kindle via GitHub Actions
Mo–Sa: FAZ, So: FAS
Zugangsdaten kommen aus Umgebungsvariablen (GitHub Secrets).
"""

import datetime
import logging
import os
import smtplib
import subprocess
import sys
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ──────────────────────────────────────────────────────────
# KONFIGURATION – alle Werte kommen aus Umgebungsvariablen
# ──────────────────────────────────────────────────────────

FAZ_USERNAME  = os.environ["FAZ_USERNAME"]
FAZ_PASSWORD  = os.environ["FAZ_PASSWORD"]
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_PASS   = os.environ["GMAIL_PASS"]
KINDLE_EMAIL  = os.environ["KINDLE_EMAIL"]

DOWNLOAD_DIR  = Path("/tmp/faz")

# ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M",
)
log = logging.getLogger(__name__)


def today_info():
    today = datetime.date.today()
    wd = today.weekday()   # 0=Mo … 5=Sa, 6=So
    log.info("Heute: %s  Wochentag: %d", today, wd)
    if wd == 6:
        return today, "Frankfurter Allgemeine Sonntagszeitung", "FAS"
    return today, "Frankfurter Allgemeine Zeitung", "FAZ"


def dismiss_cookie_banner(page):
    iframe_loc = page.frame_locator("iframe[title='Cookiebanner']")
    for btn_text in ["Alles ablehnen", "Alle ablehnen", "Ablehnen"]:
        btn = iframe_loc.get_by_text(btn_text, exact=True)
        if btn.count() > 0:
            log.info("Cookie-Banner: klicke '%s' …", btn_text)
            btn.first.click()
            page.wait_for_timeout(2_000)
            return


def download_epub(ausgabe_typ: str) -> Path:
    from playwright.sync_api import sync_playwright

    EPUB_URL = "https://aktion.faz.net/epub"
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx     = browser.new_context(accept_downloads=True)
        page    = ctx.new_page()

        log.info("Öffne %s …", EPUB_URL)
        page.goto(EPUB_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(3_000)

        dismiss_cookie_banner(page)
        page.wait_for_load_state("networkidle", timeout=15_000)

        if page.locator("input[type='password']").count() > 0:
            log.info("Login als %s …", FAZ_USERNAME)
            for sel in ["input[name='loginName']", "input[type='email']",
                        "input[name='email']", "input[name='username']"]:
                if page.locator(sel).count():
                    page.fill(sel, FAZ_USERNAME)
                    break
            page.fill("input[type='password']", FAZ_PASSWORD)
            page.keyboard.press("Enter")
            page.wait_for_load_state("networkidle", timeout=30_000)
            page.wait_for_timeout(2_000)
            log.info("Login abgeschlossen.")

        dismiss_cookie_banner(page)

        if ausgabe_typ == "FAS":
            tab = page.get_by_text("F.A.S.-Ausgaben anzeigen", exact=False)
            if tab.count() > 0:
                log.info("Klicke Tab: F.A.S.-Ausgaben anzeigen")
                tab.first.click()
                page.wait_for_timeout(3_000)
                page.wait_for_load_state("networkidle", timeout=15_000)
        else:
            log.info("FAZ-Tab ist Standard, kein Klick nötig.")

        dl_link = page.locator(f"a.epub_download_button[data-heft='{ausgabe_typ}']")
        if dl_link.count() == 0:
            raise RuntimeError(f"Kein Download-Link für {ausgabe_typ} gefunden!")

        epub_url = dl_link.first.get_attribute("href")
        log.info("Download-URL: %s", epub_url)

        with page.expect_download(timeout=120_000) as dl_info:
            dl_link.first.dispatch_event("click")

        download = dl_info.value
        fname = download.suggested_filename or f"{ausgabe_typ}_{datetime.date.today()}.epub"
        dest = DOWNLOAD_DIR / fname
        download.save_as(dest)

        size_kb = dest.stat().st_size // 1024
        log.info("Gespeichert: %s (%d KB)", dest, size_kb)

        if size_kb < 100:
            raise RuntimeError(f"Datei zu klein ({size_kb} KB) – Download fehlgeschlagen.")

        browser.close()
        return dest


def send_to_kindle(epub_path: Path, title: str):
    """Sendet die ePub-Datei per SMTP direkt an die Kindle-Adresse."""
    log.info("Sende '%s' an %s …", epub_path.name, KINDLE_EMAIL)

    msg = MIMEMultipart()
    msg["From"]    = GMAIL_USER
    msg["To"]      = KINDLE_EMAIL
    msg["Subject"] = title
    msg.attach(MIMEText("Ihre heutige Ausgabe im Anhang.", "plain"))

    with open(epub_path, "rb") as f:
        part = MIMEBase("application", "epub+zip")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{epub_path.name}"')
    msg.attach(part)

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(GMAIL_USER, GMAIL_PASS)
        smtp.sendmail(GMAIL_USER, KINDLE_EMAIL, msg.as_string())

    log.info("E-Mail erfolgreich verschickt! ✓")


def main():
    today, title, ausgabe_typ = today_info()
    log.info("=== %s – %s ===", title, today)
    epub_path = download_epub(ausgabe_typ)
    send_to_kindle(epub_path, title)
    log.info("Fertig! ✓")


if __name__ == "__main__":
    main()
