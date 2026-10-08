#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read Bandai Namco ID Customer ID / DOB via the account API, without Playwright.

The old Purchase HTTP login targets parks2.bandainamco-am.co.jp. The BNID
profile page is a separate SPA that talks to account-api.bandainamcoid.com.
This script follows that SPA's API calls directly.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import Cookie
from pathlib import Path
from typing import Any


ACCOUNT_BASE = "https://account.bandainamcoid.com"
API_BASE = "https://account-api.bandainamcoid.com"
USER_PAGE_URL = (
    f"{ACCOUNT_BASE}/user.html?"
    "client_id=idportal&backto=https%3A%2F%2Fwww.bandainamcoid.com%2F&customize_id="
)
LOGIN_PAGE_URL = f"{ACCOUNT_BASE}/login.html"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


def die(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def safe_name(email: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", email.replace("@", "_at_")).strip("_")


def compact(text: Any) -> str:
    return " ".join(str(text or "").split())


def text_response(raw: bytes, headers: dict[str, str]) -> str:
    content_type = headers.get("Content-Type", "")
    match = re.search(r"charset=([^;]+)", content_type, re.I)
    encoding = match.group(1).strip() if match else "utf-8"
    return raw.decode(encoding, errors="replace")


def request(
    opener: urllib.request.OpenerDirector,
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 45,
) -> tuple[int, str, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.geturl(), dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.geturl(), dict(exc.headers), exc.read()
    except Exception as exc:
        die(f"HTTP {method} {url} failed: {type(exc).__name__}: {exc}")


def parse_json(raw: bytes, headers: dict[str, str]) -> dict[str, Any]:
    text = text_response(raw, headers)
    try:
        data = json.loads(text)
    except ValueError:
        die(f"Expected JSON but got: {compact(text)[:500]}")
    if not isinstance(data, dict):
        die(f"Expected JSON object but got: {type(data).__name__}")
    return data


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_cookie(name: str, value: str, domain: str, path: str = "/") -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=domain.startswith("."),
        domain_initial_dot=domain.startswith("."),
        path=path or "/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def set_cookie_everywhere(jar: http.cookiejar.CookieJar, name: str, value: str, path: str = "/") -> None:
    # The SPA stores cookies from JSON instructions. Setting both domains makes
    # the direct HTTP client tolerant of small server-side changes.
    for domain in (".bandainamcoid.com", "account.bandainamcoid.com", "account-api.bandainamcoid.com"):
        jar.set_cookie(make_cookie(name, value, domain, path))


def clear_cookie_everywhere(jar: http.cookiejar.CookieJar, name: str, path: str = "/") -> None:
    for domain in (".bandainamcoid.com", "account.bandainamcoid.com", "account-api.bandainamcoid.com"):
        try:
            jar.clear(domain, path or "/", name)
        except KeyError:
            pass


def apply_cookie_instructions(jar: http.cookiejar.CookieJar, data: dict[str, Any]) -> None:
    instructions = data.get("cookie") or {}
    if not isinstance(instructions, dict):
        return
    for key, info in instructions.items():
        if not isinstance(info, dict):
            continue
        name = str(info.get("name") or "").strip()
        if not name:
            continue
        path = str(info.get("path") or "/")
        if key.startswith("delete_") or "value" not in info:
            clear_cookie_everywhere(jar, name, path)
            continue
        set_cookie_everywhere(jar, name, str(info.get("value") or ""), path)


def apply_cookie_header(jar: http.cookiejar.CookieJar, cookie_header: str) -> None:
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            set_cookie_everywhere(jar, name, value)


def common_headers(referer: str = USER_PAGE_URL) -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Origin": ACCOUNT_BASE,
        "Referer": referer,
    }


def qs(params: dict[str, str]) -> str:
    return urllib.parse.urlencode(params, doseq=False, safe="")


def account_cookie_json(jar: http.cookiejar.CookieJar | None = None) -> str:
    cookies = {"language": "ja"}
    if jar is not None:
        for cookie in jar:
            # $.cookie() on account.bandainamcoid.com sees host cookies plus
            # .bandainamcoid.com cookies, then sends them as a JSON parameter.
            if cookie.domain in (".bandainamcoid.com", "account.bandainamcoid.com"):
                cookies[cookie.name] = cookie.value
    return json.dumps(cookies, ensure_ascii=False, separators=(",", ":"))


def get_user_index(
    opener: urllib.request.OpenerDirector,
    jar: http.cookiejar.CookieJar,
) -> tuple[int, dict[str, Any]]:
    params = {
        "client_id": "idportal",
        "customize_id": "",
        "language": "ja",
        "cookie": account_cookie_json(jar),
        "backto": "https://www.bandainamcoid.com/",
    }
    url = f"{API_BASE}/v3/user/index?{qs(params)}"
    status, _, headers, raw = request(opener, "GET", url, headers=common_headers())
    return status, parse_json(raw, headers)


def login_init(opener: urllib.request.OpenerDirector, jar: http.cookiejar.CookieJar) -> tuple[int, dict[str, Any]]:
    params = {
        "client_id": "idportal",
        "redirect_uri": USER_PAGE_URL,
        "customize_id": "",
        "language": "ja",
        "prompt": "",
        "sns_not_found": "",
        "lockout_child": "",
        "cookie": account_cookie_json(jar),
        "backto": "",
        "response_type": "",
        "scope": "",
        "state": "",
        "nonce": "",
    }
    url = f"{API_BASE}/v3/login/init?{qs(params)}"
    status, _, headers, raw = request(
        opener,
        "GET",
        url,
        headers=common_headers(f"{LOGIN_PAGE_URL}?client_id=idportal&redirect_uri={urllib.parse.quote(USER_PAGE_URL, safe='')}"),
    )
    data = parse_json(raw, headers)
    apply_cookie_instructions(jar, data)
    return status, data


def login_idpw(
    opener: urllib.request.OpenerDirector,
    jar: http.cookiejar.CookieJar,
    email: str,
    password: str,
) -> tuple[int, dict[str, Any]]:
    payload = {
        "client_id": "idportal",
        "redirect_uri": USER_PAGE_URL,
        "backto": "",
        "customize_id": "",
        "login_id": email,
        "password": password,
        "env_info": json.dumps({"mobile": False, "platform": "Windows"}, separators=(",", ":")),
        "retention": "1",
        "language": "ja",
        "cookie": account_cookie_json(jar),
    }
    body = urllib.parse.urlencode(payload).encode("utf-8")
    headers = common_headers(f"{LOGIN_PAGE_URL}?client_id=idportal&redirect_uri={urllib.parse.quote(USER_PAGE_URL, safe='')}")
    headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
    status, _, resp_headers, raw = request(
        opener,
        "POST",
        f"{API_BASE}/v3/login/idpw",
        data=body,
        headers=headers,
    )
    data = parse_json(raw, resp_headers)
    apply_cookie_instructions(jar, data)
    return status, data


def passkey_info(
    opener: urllib.request.OpenerDirector,
    jar: http.cookiejar.CookieJar,
    redirect_url: str,
) -> tuple[int, dict[str, Any]]:
    parsed = urllib.parse.urlparse(redirect_url)
    params_in = urllib.parse.parse_qs(parsed.query)
    params = {
        "client_id": params_in.get("client_id", ["idportal"])[0],
        "backto": params_in.get("backto", [""])[0],
        "redirect_uri": params_in.get("redirect_uri", [USER_PAGE_URL])[0],
        "customize_id": params_in.get("customize_id", [""])[0],
        "code": params_in.get("code", [""])[0],
        "language": "ja",
        "cookie": account_cookie_json(jar),
    }
    url = f"{API_BASE}/v3/passkey/info?{qs(params)}"
    status, _, headers, raw = request(opener, "GET", url, headers=common_headers(redirect_url))
    data = parse_json(raw, headers)
    apply_cookie_instructions(jar, data)
    return status, data


def reauth_init(
    opener: urllib.request.OpenerDirector,
    jar: http.cookiejar.CookieJar,
    reauth_url: str,
) -> tuple[int, dict[str, Any]]:
    parsed = urllib.parse.urlparse(reauth_url)
    params_in = urllib.parse.parse_qs(parsed.query)
    params = {
        "client_id": params_in.get("client_id", ["idportal"])[0],
        "backto": params_in.get("backto", [""])[0],
        "customize_id": params_in.get("customize_id", [""])[0],
        "language": "ja",
        "cookie": account_cookie_json(jar),
    }
    url = f"{API_BASE}/v3/reauth/init?{qs(params)}"
    status, _, headers, raw = request(opener, "GET", url, headers=common_headers(reauth_url))
    data = parse_json(raw, headers)
    apply_cookie_instructions(jar, data)
    return status, data


def reauth_idpw(
    opener: urllib.request.OpenerDirector,
    jar: http.cookiejar.CookieJar,
    reauth_url: str,
    password: str,
) -> tuple[int, dict[str, Any]]:
    parsed = urllib.parse.urlparse(reauth_url)
    params_in = urllib.parse.parse_qs(parsed.query)
    payload = {
        "client_id": params_in.get("client_id", ["idportal"])[0],
        "backto": params_in.get("backto", [""])[0],
        "customize_id": params_in.get("customize_id", [""])[0],
        "password": password,
        "language": "ja",
        "cookie": account_cookie_json(jar),
    }
    body = urllib.parse.urlencode(payload).encode("utf-8")
    headers = common_headers(reauth_url)
    headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
    status, _, resp_headers, raw = request(
        opener,
        "POST",
        f"{API_BASE}/v3/reauth/idpw",
        data=body,
        headers=headers,
    )
    data = parse_json(raw, resp_headers)
    apply_cookie_instructions(jar, data)
    return status, data


def walk_json(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            yield next_path, child
            yield from walk_json(child, next_path)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            next_path = f"{path}[{idx}]"
            yield next_path, child
            yield from walk_json(child, next_path)


def looks_like_customer_id(text: str) -> bool:
    return bool(re.fullmatch(r"B\d{10,14}", text.strip(), re.I))


def normalize_dob(text: str) -> str:
    text = text.strip()
    match = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    return ""


def extract_profile(data: dict[str, Any]) -> dict[str, str]:
    customer_id = ""
    dob = ""
    customer_candidates: list[tuple[str, str]] = []
    dob_candidates: list[tuple[str, str]] = []

    for path, value in walk_json(data):
        if isinstance(value, (int, float)):
            value = str(value)
        if not isinstance(value, str):
            continue
        value = value.strip()
        lower_path = path.lower()
        if value and ("customer" in lower_path or "member" in lower_path or "user" in lower_path):
            if looks_like_customer_id(value):
                customer_candidates.append((path, value))
        if looks_like_customer_id(value):
            customer_candidates.append((path, value))
        normalized = normalize_dob(value)
        if normalized and ("birth" in lower_path or "birthday" in lower_path or "dob" in lower_path):
            dob_candidates.append((path, normalized))
        elif normalized and not dob:
            dob_candidates.append((path, normalized))

    if customer_candidates:
        customer_id = customer_candidates[0][1]
    if dob_candidates:
        dob = dob_candidates[0][1]
    return {
        "customer_id": customer_id,
        "dob": dob,
        "customer_id_source": customer_candidates[0][0] if customer_candidates else "",
        "dob_source": dob_candidates[0][0] if dob_candidates else "",
    }


def first_error_message(data: dict[str, Any]) -> str:
    input_error = data.get("input_error") or {}
    if isinstance(input_error, dict):
        error_msg = input_error.get("error_msg") or {}
        if isinstance(error_msg, dict):
            for value in error_msg.values():
                if value:
                    return re.sub(r"<[^>]+>", " ", compact(value))
        if input_error:
            return compact(input_error)
    for path, value in walk_json(data):
        if "error" in path.lower() and isinstance(value, str) and value.strip():
            return compact(value)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Read BNID Customer ID/DOB by direct HTTP API.")
    parser.add_argument("--email", required=True, help="Bandai Namco ID email.")
    parser.add_argument("--password", default="", help="Bandai Namco ID password.")
    parser.add_argument(
        "--cookie-header",
        default="",
        help="Optional Cookie header copied from a logged-in account.bandainamcoid.com browser session.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent / "profile_http_runs"),
        help="Directory to save request/response artifacts.",
    )
    args = parser.parse_args()

    if not args.password and not args.cookie_header:
        die("Provide --password for HTTP login, or --cookie-header from an already logged-in browser session.")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir).expanduser() / f"{stamp}_{safe_name(args.email)}"
    out_dir.mkdir(parents=True, exist_ok=True)

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    set_cookie_everywhere(jar, "language", "ja")
    if args.cookie_header:
        apply_cookie_header(jar, args.cookie_header)

    print("[1/6] GET user page shell...")
    status, final_url, headers, raw = request(
        opener,
        "GET",
        USER_PAGE_URL,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        },
    )
    (out_dir / "01_user_page.html").write_bytes(raw)
    if status != 200:
        die(f"user page shell returned HTTP {status}: {final_url}")

    passkey_profile_data: dict[str, Any] = {}
    if args.cookie_header and not args.password:
        print("[2/6] Cookie mode: skip password login.")
        write_json(out_dir / "02_cookie_mode.json", {"mode": "cookie_header"})
    else:
        print("[2/6] GET login init...")
        status, init_data = login_init(opener, jar)
        write_json(out_dir / "02_login_init.json", init_data)
        if status != 200:
            die(f"login init returned HTTP {status}: {init_data}")

        print("[3/6] POST login id/password...")
        status, login_data = login_idpw(opener, jar, args.email, args.password)
        write_json(out_dir / "03_login_idpw.json", login_data)
        if status != 200:
            die(f"login idpw returned HTTP {status}: {login_data}")
        error = first_error_message(login_data)
        if error:
            print("")
            print("[LOGIN_ERROR] BNID API trả lỗi đăng nhập.")
            print(f"Reason: {error}")
            print(f"Artifacts: {out_dir}")
            return 2

        redirect_url = str(login_data.get("redirect") or "")
        if "passkeyInfo.html" in redirect_url:
            print("[4/6] GET passkey info...")
            status, _, passkey_headers, passkey_raw = request(
                opener,
                "GET",
                redirect_url,
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                    "Referer": LOGIN_PAGE_URL,
                },
            )
            (out_dir / "04_passkey_page.html").write_bytes(passkey_raw)
            if status != 200:
                die(f"passkey page returned HTTP {status}: {redirect_url}")
            status, passkey_data = passkey_info(opener, jar, redirect_url)
            write_json(out_dir / "05_passkey_info.json", passkey_data)
            if status != 200:
                die(f"passkey info returned HTTP {status}: {passkey_data}")
            passkey_profile_data = passkey_data
        else:
            write_json(out_dir / "04_no_passkey_step.json", {"redirect": redirect_url})

    reauth_profile_data: dict[str, Any] = {}

    print("[5/6] GET user index...")
    status, profile_data = get_user_index(opener, jar)
    write_json(out_dir / "04_user_index.json", profile_data)
    if status != 200:
        die(f"user index returned HTTP {status}: {profile_data}")

    reauth_url = str(profile_data.get("redirect_no-cache") or profile_data.get("redirect") or "")
    if args.password and "reauth.html" in reauth_url:
        print("[5b/6] Reauth password branch...")
        status, _, _, reauth_page_raw = request(
            opener,
            "GET",
            reauth_url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                "Referer": USER_PAGE_URL,
            },
        )
        (out_dir / "05_reauth_page.html").write_bytes(reauth_page_raw)
        if status != 200:
            die(f"reauth page returned HTTP {status}: {reauth_url}")

        status, reauth_data = reauth_init(opener, jar, reauth_url)
        write_json(out_dir / "06_reauth_init.json", reauth_data)
        if status != 200:
            die(f"reauth init returned HTTP {status}: {reauth_data}")
        reauth_profile_data = reauth_data

        status, reauth_done = reauth_idpw(opener, jar, reauth_url, args.password)
        write_json(out_dir / "07_reauth_idpw.json", reauth_done)
        if status != 200:
            die(f"reauth idpw returned HTTP {status}: {reauth_done}")
        error = first_error_message(reauth_done)
        if error:
            print("")
            print("[REAUTH_ERROR] BNID API trả lỗi xác thực lại.")
            print(f"Reason: {error}")
            print(f"Artifacts: {out_dir}")
            return 2

        status, profile_data = get_user_index(opener, jar)
        write_json(out_dir / "08_user_index_after_reauth.json", profile_data)
        if status != 200:
            die(f"user index after reauth returned HTTP {status}: {profile_data}")

    error = first_error_message(profile_data)
    profile = extract_profile(profile_data)
    if passkey_profile_data and (not profile["customer_id"] or not profile["dob"]):
        passkey_profile = extract_profile(passkey_profile_data)
        profile["customer_id"] = profile["customer_id"] or passkey_profile["customer_id"]
        profile["dob"] = profile["dob"] or passkey_profile["dob"]
        profile["customer_id_source"] = profile["customer_id_source"] or passkey_profile["customer_id_source"]
        profile["dob_source"] = profile["dob_source"] or passkey_profile["dob_source"]
    if reauth_profile_data and (not profile["customer_id"] or not profile["dob"]):
        reauth_profile = extract_profile(reauth_profile_data)
        profile["customer_id"] = profile["customer_id"] or reauth_profile["customer_id"]
        profile["dob"] = profile["dob"] or reauth_profile["dob"]
        profile["customer_id_source"] = profile["customer_id_source"] or reauth_profile["customer_id_source"]
        profile["dob_source"] = profile["dob_source"] or reauth_profile["dob_source"]
    profile.update(
        {
            "email": args.email,
            "raw_result": str(profile_data.get("result") or ""),
            "error": error,
        }
    )
    write_json(out_dir / "profile.json", profile)

    print("[6/6] Extract profile...")
    if profile["customer_id"] or profile["dob"]:
        print(f"Customer ID: {profile['customer_id'] or '(not found)'}")
        print(f"DOB: {profile['dob'] or '(not found)'}")
        print(f"profile.json: {out_dir / 'profile.json'}")
        return 0

    print("[WARN] Chưa extract được Customer ID/DOB từ user index.")
    if error:
        print(f"API message: {error}")
    print(f"Artifacts: {out_dir}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
