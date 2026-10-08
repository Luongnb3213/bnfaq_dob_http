# Bandai Namco ID DOB Inquiry HTTP Tool

Tool này gọi form `https://bnfaq-support.channel.or.jp/inquiry/www_sup/input` bằng HTTP.

Mặc định là dry-run: dừng ở trang confirm, chưa gửi ticket thật.

```bash
python3 submit_dob_inquiry.py \
  --email account@example.com \
  --customer-id B000000000000 \
  --dob-current 1992-07-04 \
  --dob-new 1992-07-05 \
  --reason "I entered the wrong date of birth during registration."
```

Gửi thật:

```bash
python3 submit_dob_inquiry.py \
  --email account@example.com \
  --customer-id B000000000000 \
  --dob-current 1992-07-04 \
  --dob-new 1992-07-05 \
  --reason "I entered the wrong date of birth during registration." \
  --execute
```

Response/HTML sẽ lưu vào `runs/`.

Nếu muốn tự viết toàn bộ nội dung gửi support, dùng `--comment`. Khi có
`--comment`, script sẽ bỏ qua `--reason`.

## Tự lấy Customer ID và DOB hiện tại bằng HTTP

Trang profile BNID không dùng `parks2.bandainamco-am.co.jp/top_login.html`.
Nó là SPA gọi `account-api.bandainamcoid.com/v3/...`, nên script dưới đây
đăng nhập bằng API đó, không mở browser:

```bash
python3 bnid_profile_http.py \
  --email account@example.com \
  --password 'MAT_KHAU_BNID'
```

Kết quả sẽ nằm trong `profile_http_runs/.../profile.json`, ví dụ:

```json
{
  "email": "account@example.com",
  "customer_id": "B000000000000",
  "dob": "1992-07-04"
}
```

Sau đó gửi inquiry bằng profile JSON:

```bash
python3 submit_dob_inquiry.py \
  --profile-json profile_http_runs/.../profile.json \
  --email account@example.com \
  --dob-new 1992-07-05
```

Nếu HTTP login bị BNID chặn hoặc bắt xác minh thêm, có thể copy Cookie từ
browser đã đăng nhập rồi chạy:

```bash
python3 bnid_profile_http.py \
  --email account@example.com \
  --cookie-header 'PASTE_COOKIE_HEADER_HERE'
```

## Fallback soi bằng Playwright

Script này cần Playwright:

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
```

Chỉ dùng khi cần soi network/HTML, không phải flow chính:

```bash
python3 bnid_profile_probe.py \
  --email account@example.com \
  --password 'MAT_KHAU_BNID'
```

Nếu bị hỏi OTP hoặc muốn tự login trong browser:

```bash
python3 bnid_profile_probe.py --manual-login --headful
```

Kết quả sẽ nằm trong `profile_runs/.../profile.json`, ví dụ:

```json
{
  "customer_id": "B000000000000",
  "dob": "1992-07-04"
}
```

Sau đó vẫn có thể gửi inquiry bằng profile JSON:

```bash
python3 submit_dob_inquiry.py \
  --profile-json profile_runs/.../profile.json \
  --email account@example.com \
  --dob-new 1992-07-05
```
