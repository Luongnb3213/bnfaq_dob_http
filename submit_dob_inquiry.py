#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bandai Namco ID DOB inquiry submitter over HTTP.

Default mode is dry-run: it stops at the confirm page and saves the HTML/JSON.
Use --execute only when you want to send the real inquiry.
"""

from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import re
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BASE = "https://bnfaq-support.channel.or.jp"
INPUT_URL = f"{BASE}/inquiry/www_sup/input"
DOB_CATEGORY_ID = 2500561
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


def die(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def compact(text: str) -> str:
    return " ".join(str(text or "").split())


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


def text_response(raw: bytes, headers: dict[str, str]) -> str:
    content_type = headers.get("Content-Type", "")
    match = re.search(r"charset=([^;]+)", content_type, re.I)
    encoding = match.group(1).strip() if match else "utf-8"
    return raw.decode(encoding, errors="replace")


def load_input_page(opener: urllib.request.OpenerDirector) -> tuple[str, dict]:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    }
    status, final_url, resp_headers, raw = request(opener, "GET", INPUT_URL, headers=headers)
    body = text_response(raw, resp_headers)
    if status != 200:
        die(f"GET input returned HTTP {status}: {compact(body)[:300]}")

    csrf_match = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', body)
    if not csrf_match:
        die("Cannot find csrf-token meta on input page.")

    props_match = re.search(r"<component-inquiry-input\s+:props='([^']+)'", body)
    if not props_match:
        die("Cannot find component-inquiry-input props on input page.")

    props = json.loads(html.unescape(props_match.group(1)))
    props["_csrf"] = csrf_match.group(1)
    props["_input_url"] = final_url
    return body, props


def api_headers(csrf: str, referer: str, *, content_type: str) -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
        "Content-Type": content_type,
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRF-TOKEN": csrf,
        "Origin": BASE,
        "Referer": referer,
    }


def post_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    csrf: str,
    referer: str,
    payload: dict,
) -> tuple[int, dict | str]:
    raw_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _, resp_headers, raw = request(
        opener,
        "POST",
        url,
        data=raw_payload,
        headers=api_headers(csrf, referer, content_type="application/json"),
    )
    text = text_response(raw, resp_headers)
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def multipart_payload(items: dict) -> tuple[bytes, str]:
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for key, value in items.items():
        encoded_value = json.dumps(value, ensure_ascii=False)
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n'
                "\r\n"
                f"{encoded_value}\r\n"
            ).encode("utf-8")
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def post_multipart(
    opener: urllib.request.OpenerDirector,
    url: str,
    csrf: str,
    referer: str,
    payload: dict,
) -> tuple[int, dict | str]:
    body, boundary = multipart_payload(payload)
    status, _, resp_headers, raw = request(
        opener,
        "POST",
        url,
        data=body,
        headers=api_headers(csrf, referer, content_type=f"multipart/form-data; boundary={boundary}"),
    )
    text = text_response(raw, resp_headers)
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def parse_confirm_props(confirm_html: str) -> dict:
    match = re.search(r"<component-inquiry-confirm\s+:props='([^']+)'", confirm_html)
    if not match:
        die("Cannot find component-inquiry-confirm props on confirm page.")
    return json.loads(html.unescape(match.group(1)))


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def default_reason() -> str:
    return "I entered the wrong date of birth during registration."


def default_comment(current_dob: str, new_dob: str, reason: str) -> str:
    return (
        "I would like to request a correction to the date of birth registered "
        "on my Bandai Namco ID account.\n\n"
        f"Reason: {reason.strip()}\n\n"
        f"Please change the date of birth from {current_dob} to {new_dob}.\n\n"
        "Thank you for your assistance."
    )


def default_comment_ja(current_dob: str, new_dob: str) -> str:
    return (
        "登録時に生年月日を誤って入力してしまったため、"
        "登録済みの生年月日の修正をお願いいたします。\n\n"
        f"現在登録されている生年月日：{current_dob}\n"
        f"修正後の生年月日：{new_dob}\n\n"
        "お手数をおかけいたしますが、よろしくお願いいたします。"
    )


def apply_profile_json(args: argparse.Namespace) -> None:
    if not args.profile_json:
        return
    path = Path(args.profile_json).expanduser()
    if not path.exists():
        die(f"--profile-json not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not args.customer_id:
        args.customer_id = str(data.get("customer_id") or "").strip()
    if not args.dob_current:
        args.dob_current = str(data.get("dob") or data.get("date_of_birth") or "").strip()


def build_inquiry_payload(args: argparse.Namespace, props: dict) -> dict:
    folder_id = ((props.get("fileInfo") or {}).get("folder_id") or "").strip()
    if not folder_id:
        die("Cannot find fileInfo.folder_id from input page props.")

    comment = args.comment
    if not comment:
        if args.comment_language == "ja":
            comment = default_comment_ja(args.dob_current, args.dob_new)
        else:
            comment = default_comment(args.dob_current, args.dob_new, args.reason)

    return {
        "display_form": "お問い合わせ",
        "display_title": "バンダイナムコID",
        "category_id": DOB_CATEGORY_ID,
        "comment": comment,
        "form_item_values": {
            "0": args.email,
            "0_confirm": args.email,
            "1": args.customer_id,
            "2": args.dob_current,
            "3": args.dob_new,
        },
        "folder_id": folder_id,
        "files": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit Bandai Namco ID DOB change inquiry over HTTP.")
    parser.add_argument("--email", required=True, help="Bandai Namco ID email address.")
    parser.add_argument("--customer-id", default="", help="Customer ID, e.g. B000000000000.")
    parser.add_argument("--dob-current", default="", help="Current DOB, YYYY-MM-DD.")
    parser.add_argument("--dob-new", required=True, help="DOB to request, YYYY-MM-DD.")
    parser.add_argument("--profile-json", default="", help="profile.json from bnid_profile_http.py.")
    parser.add_argument(
        "--reason",
        default=default_reason(),
        help="Reason to include in the English comment.",
    )
    parser.add_argument(
        "--comment-language",
        choices=("en", "ja"),
        default="en",
        help="Auto comment language when --comment is not provided.",
    )
    parser.add_argument("--comment", default="", help="Override English comment.")
    parser.add_argument("--execute", action="store_true", help="Actually POST the final save endpoint.")
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent / "runs"),
        help="Directory to save request/response artifacts.",
    )
    args = parser.parse_args()
    apply_profile_json(args)
    if not args.customer_id:
        die("Missing --customer-id. Provide it, or pass --profile-json from bnid_profile_http.py.")
    if not args.dob_current:
        die("Missing --dob-current. Provide it, or pass --profile-json from bnid_profile_http.py.")

    for name in ("dob_current", "dob_new"):
        value = getattr(args, name)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            die(f"--{name.replace('_', '-')} must be YYYY-MM-DD, got {value!r}")
    if args.dob_current == args.dob_new:
        die("dob-current and dob-new are identical. Refusing to submit a no-op DOB request.")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir).expanduser() / f"{stamp}_{args.email.replace('@', '_at_')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    print("[1/5] GET input page...")
    input_html, props = load_input_page(opener)
    (out_dir / "01_input.html").write_text(input_html, encoding="utf-8")
    write_json(out_dir / "01_input_props.json", props)
    csrf = props["_csrf"]
    routes = props.get("routes") or {}

    search_url = routes.get("serch_form") or f"{BASE}/api/inquiry/www_sup/search_form"
    confirm_url = routes.get("inquiry_confirm") or f"{BASE}/api/inquiry/www_sup/confirm"

    print("[2/5] POST search_form...")
    status, search_data = post_json(opener, search_url, csrf, props["_input_url"], {"category_id": DOB_CATEGORY_ID})
    write_json(out_dir / "02_search_form.json", search_data)
    if status != 200:
        die(f"search_form returned HTTP {status}: {search_data}")

    payload = build_inquiry_payload(args, props)
    write_json(out_dir / "03_payload_confirm.json", payload)

    print("[3/5] POST confirm...")
    status, confirm_data = post_multipart(opener, confirm_url, csrf, props["_input_url"], payload)
    write_json(out_dir / "04_confirm_response.json", confirm_data)
    if status != 200 or not isinstance(confirm_data, dict) or not confirm_data.get("location"):
        die(f"confirm returned HTTP {status}: {confirm_data}")

    confirm_location = str(confirm_data["location"])
    print(f"      confirm location: {confirm_location}")

    print("[4/5] GET confirm page...")
    status, _, resp_headers, raw = request(
        opener,
        "GET",
        confirm_location,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": props["_input_url"],
        },
    )
    confirm_html = text_response(raw, resp_headers)
    (out_dir / "05_confirm.html").write_text(confirm_html, encoding="utf-8")
    if status != 200:
        die(f"GET confirm returned HTTP {status}: {compact(confirm_html)[:300]}")
    confirm_props = parse_confirm_props(confirm_html)
    write_json(out_dir / "05_confirm_props.json", confirm_props)

    save_url = (confirm_props.get("routes") or {}).get("inquiry_save") or f"{BASE}/api/inquiry/www_sup/save"
    print(f"      save endpoint: {save_url}")

    if not args.execute:
        print("")
        print("[DRY-RUN] Confirm OK. Chưa gửi inquiry thật.")
        print(f"Artifacts: {out_dir}")
        print("Muốn gửi thật, chạy lại cùng tham số và thêm --execute.")
        return 0

    print("[5/5] POST save (REAL SUBMIT)...")
    save_input = confirm_props.get("input") or payload
    # The browser stores folder_id/files from the confirm page as already-stringified
    # values. Sending them back exactly as exposed by confirm props matches the Vue flow.
    status, save_data = post_multipart(opener, save_url, csrf, confirm_location, save_input)
    write_json(out_dir / "06_save_response.json", save_data)
    if status != 200 or not isinstance(save_data, dict) or not save_data.get("location"):
        die(f"save returned HTTP {status}: {save_data}")

    done_location = str(save_data["location"])
    print(f"      done location: {done_location}")
    status, _, resp_headers, raw = request(
        opener,
        "GET",
        done_location,
        headers={"User-Agent": UA, "Referer": confirm_location},
    )
    done_html = text_response(raw, resp_headers)
    (out_dir / "07_done.html").write_text(done_html, encoding="utf-8")
    if status != 200:
        die(f"GET done returned HTTP {status}: {compact(done_html)[:300]}")

    print("")
    print("[DONE] Đã gửi inquiry thật.")
    print(f"Artifacts: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
