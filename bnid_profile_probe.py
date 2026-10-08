#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read Bandai Namco ID profile data with Playwright.

This script is read-only. It logs in, opens the ID portal user page, captures
HTML/network evidence, and extracts Customer ID + date of birth when visible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


USER_URL = (
    "https://account.bandainamcoid.com/user.html"
    "?client_id=idportal"
    "&backto=https%3A%2F%2Fwww.bandainamcoid.com%2F"
    "&customize_id="
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

BNID_RE = re.compile(r"\bB\d{12}\b")
DOB_RE = re.compile(r"\b(19\d{2}|20\d{2})[/-](0?[1-9]|1[0-2])[/-](0?[1-9]|[12]\d|3[01])\b")
COOKIE_SELECTORS = (
    "#onetrust-reject-all-handler",
    "button:has-text('すべて不同意')",
    "#onetrust-accept-btn-handler",
    "button:has-text('すべて同意')",
)


def die(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def norm_dob(value: str) -> str:
    match = DOB_RE.search(value or "")
    if not match:
        return ""
    y, m, d = match.groups()
    return f"{y}-{int(m):02d}-{int(d):02d}"


def extract_from_text(text: str) -> dict:
    compact = "\n".join(line.strip() for line in str(text or "").splitlines() if line.strip())
    customer_id = ""
    dob = ""

    found = BNID_RE.search(compact)
    if found:
        customer_id = found.group(0)

    lines = compact.splitlines()
    for idx, line in enumerate(lines):
        hay = line.lower()
        if "生年月日" in line or "date of birth" in hay or "birthday" in hay:
            window = "\n".join(lines[idx: idx + 4])
            dob = norm_dob(window)
            if dob:
                break
    if not dob:
        dob = norm_dob(compact)

    return {"customer_id": customer_id, "dob": dob}


async def dismiss_cookie_banner(page) -> None:
    for selector in COOKIE_SELECTORS:
        try:
            button = page.locator(selector).first
            if await button.count() and await button.is_visible(timeout=1000):
                await button.click(timeout=3000)
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def page_text(page) -> str:
    try:
        return await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        return ""


async def fill_login_if_needed(page, email: str, password: str, manual: bool) -> None:
    await dismiss_cookie_banner(page)
    text = await page_text(page)
    url = page.url
    login_like = "login.html" in url or await page.locator("input#mail, input[name='mail']").count()
    if not login_like:
        return

    if manual:
        print("[LOGIN] Manual mode: hãy login/OTP trong browser. Script sẽ chờ portal user page...")
        return

    if not email or not password:
        die("Page requires login. Provide --email and --password, or use --manual-login.")

    print("[LOGIN] Filling BNID login form...")
    await page.wait_for_selector("input#mail, input[name='mail']", timeout=45000)
    email_field = page.locator("input#mail, input[name='mail']").first
    pass_field = page.locator("input#pass, input[name='pass']").first
    await email_field.fill(email)
    await email_field.blur()
    await page.wait_for_timeout(300)
    await pass_field.fill(password)
    await pass_field.blur()
    await page.wait_for_timeout(500)

    button = page.locator("button#btn-idpw-login").first
    for _ in range(80):
        try:
            if await button.is_enabled():
                break
        except Exception:
            pass
        await page.wait_for_timeout(250)
    else:
        raise RuntimeError("Login button did not become enabled.")
    await button.click(timeout=30000)


async def passkey_later_if_needed(page) -> None:
    for _ in range(15):
        if "passkeyInfo" not in page.url:
            return
        try:
            later = page.locator("#btn-next, button:has-text('あとで')").first
            if await later.count():
                await later.click(timeout=10000)
                await page.wait_for_timeout(1500)
                continue
        except Exception:
            pass
        await page.wait_for_timeout(1000)


async def wait_until_profile_or_manual(page, args: argparse.Namespace, timeout_ms: int) -> None:
    deadline = time.time() + timeout_ms / 1000
    login_submitted = False
    while time.time() < deadline:
        await dismiss_cookie_banner(page)
        await passkey_later_if_needed(page)
        url = page.url
        text = await page_text(page)

        if "login.html" in url and not login_submitted:
            await fill_login_if_needed(page, args.email, args.password, args.manual_login)
            login_submitted = not args.manual_login
            await page.wait_for_timeout(1500)
            continue

        if "authCode.html" in url or await page.locator("input[name='authenticationCode']").count():
            if args.manual_login:
                print("[OTP] Đang ở màn OTP. Nhập OTP trong browser để tiếp tục...")
            else:
                print("[OTP] Site yêu cầu OTP. Chạy lại với --manual-login để nhập OTP thủ công.")
                return

        if "user.html" in url and ("login.html" not in url):
            return
        if BNID_RE.search(text) and ("生年月日" in text or "Date of birth" in text or "Birthday" in text):
            return
        await page.wait_for_timeout(1000)


async def main_async(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError:
        die(
            "Missing playwright. Install with: python3 -m pip install playwright "
            "&& python3 -m playwright install chromium"
        )

    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe_email = (args.email or "manual").replace("@", "_at_")
    out_dir = Path(args.out_dir).expanduser() / f"{stamp}_{safe_email}"
    out_dir.mkdir(parents=True, exist_ok=True)
    network_path = out_dir / "network.jsonl"

    async with async_playwright() as pw:
        launch_kwargs = {
            "headless": not args.headful and not args.manual_login,
            "args": ["--disable-gpu", "--disable-dev-shm-usage", "--no-first-run"],
        }
        if args.channel:
            launch_kwargs["channel"] = args.channel
        browser = await pw.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            user_agent=UA,
            viewport={"width": 1365, "height": 900},
        )
        page = await context.new_page()

        def record(event: dict) -> None:
            with network_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

        page.on("request", lambda req: (
            record({
                "type": "request",
                "method": req.method,
                "url": req.url,
                "post_data": req.post_data[:1000] if req.post_data else "",
            })
            if "bandainamcoid.com" in req.url else None
        ))

        async def on_response(resp):
            if "bandainamcoid.com" not in resp.url:
                return
            item = {"type": "response", "status": resp.status, "url": resp.url}
            ctype = resp.headers.get("content-type", "")
            if any(mark in resp.url for mark in ("/v2/", "/v3/", "/api", "user.html", "portal.html")):
                try:
                    body = await resp.text()
                    item["body_preview"] = body[:3000]
                except Exception as exc:
                    item["body_error"] = str(exc)[:200]
            item["content_type"] = ctype
            record(item)

        page.on("response", on_response)

        print("[1/4] Open BNID user page...")
        await page.goto(USER_URL, wait_until="domcontentloaded", timeout=args.timeout)
        await page.wait_for_timeout(1200)
        await wait_until_profile_or_manual(page, args, args.timeout)

        if "user.html" not in page.url:
            print("[2/4] Navigate to user page after login...")
            await page.goto(USER_URL, wait_until="domcontentloaded", timeout=args.timeout)
            await wait_until_profile_or_manual(page, args, args.timeout)

        await dismiss_cookie_banner(page)
        await page.wait_for_timeout(1500)

        print("[3/4] Capture page...")
        html = await page.content()
        text = await page_text(page)
        await page.screenshot(path=str(out_dir / "profile.png"), full_page=True)
        (out_dir / "profile.html").write_text(html, encoding="utf-8")
        (out_dir / "profile.txt").write_text(text, encoding="utf-8")
        await context.storage_state(path=str(out_dir / "storage_state.json"))

        data = extract_from_text(text)
        data.update({"email": args.email, "url": page.url, "artifacts": str(out_dir)})
        (out_dir / "profile.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        print("[4/4] Result:")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        await context.close()
        await browser.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Read Customer ID and DOB from Bandai Namco ID portal.")
    parser.add_argument("--email", default="", help="BNID email.")
    parser.add_argument("--password", default="", help="BNID password.")
    parser.add_argument("--manual-login", action="store_true", help="Open headed browser and wait for manual login/OTP.")
    parser.add_argument("--headful", action="store_true", help="Show browser even without manual mode.")
    parser.add_argument("--channel", default="chrome", help="Playwright browser channel. Use empty string for bundled Chromium.")
    parser.add_argument("--timeout", type=int, default=120000, help="Timeout in milliseconds.")
    parser.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "profile_runs"))
    args = parser.parse_args()
    if not args.channel:
        args.channel = None
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
