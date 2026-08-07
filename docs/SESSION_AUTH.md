# Đăng nhập bằng server-side session

## Quyết định thiết kế

Ứng dụng dùng session phía server thay vì JWT cho phiên bản web hiện tại:

```text
React -> HttpOnly session cookie -> FastAPI -> PostgreSQL user_sessions
```

Lựa chọn này phù hợp với một frontend web và một backend FastAPI chính. Session
có thể bị thu hồi ngay, dễ logout toàn bộ thiết bị và không cần lưu access token
trong `localStorage`.

## Mật khẩu

Mật khẩu được hash bằng PBKDF2-HMAC-SHA256 với salt ngẫu nhiên. Giá trị mặc định:

```dotenv
PASSWORD_PBKDF2_ITERATIONS=600000
```

Không lưu mật khẩu gốc. Khi email không tồn tại, backend vẫn chạy một phép kiểm
tra hash giả để giảm khác biệt thời gian phản hồi giữa tài khoản tồn tại và không
tồn tại.

## Session token

Sau khi đăng nhập thành công, backend tạo token bằng bộ sinh số ngẫu nhiên mật
mã. Token gốc chỉ được gửi trong cookie:

```text
legal_rag_session
HttpOnly
SameSite=Lax
Path=/
```

Database chỉ lưu:

```text
SHA-256(session_token)
```

Khi triển khai HTTPS phải đặt:

```dotenv
SESSION_COOKIE_SECURE=true
```

Nếu frontend và backend nằm ở các site khác nhau, cần đánh giá lại CORS,
`SameSite=None` và bắt buộc HTTPS.

## CSRF

Các request làm thay đổi dữ liệu cần đồng thời có:

- session cookie hợp lệ;
- CSRF cookie/token thuộc cùng session;
- header `X-CSRF-Token` trùng với token đã cấp.

CSRF hash được lưu trong `user_sessions`. Request thiếu hoặc sai token trả `403`.

## Phân quyền

`user_id` luôn được lấy từ session đã xác thực, không lấy từ body, URL hoặc
`X-Client-Id`. Repository kiểm tra quyền sở hữu ngay trong truy vấn database.

```sql
SELECT ...
FROM conversations
WHERE id = :conversation_id
  AND user_id = :authenticated_user_id;
```

## Dữ liệu ẩn danh cũ

`X-Client-Id` chỉ được chấp nhận ở `/auth/register` và `/auth/login` để chuyển dữ
liệu của phiên bản cũ. Sau khi chuyển, các API hội thoại không sử dụng header này
để xác thực.

UUID ẩn danh cũ vốn không phải bí mật xác thực. Cơ chế migration chỉ nhằm tương
thích dữ liệu MVP đã tồn tại; không dùng nó làm mô hình nhận diện cho production.

## Giới hạn chưa triển khai

- xác minh email;
- quên/đặt lại mật khẩu;
- đổi mật khẩu;
- khóa tạm sau nhiều lần đăng nhập sai;
- MFA;
- OAuth/OIDC;
- trang quản lý và thu hồi từng thiết bị trong giao diện.

Backend đã có API liệt kê session và logout toàn bộ để làm nền cho bước mở rộng.
