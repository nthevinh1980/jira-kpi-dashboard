# HƯỚNG DẪN ĐƯA JIRA KPI LÊN WEB MIỄN PHÍ

## Mô hình
GitHub Private Repo -> Streamlit Community Cloud -> Jira REST API

GitHub chỉ giữ source code. Streamlit Community Cloud mới là nơi chạy Python/Streamlit.

## Bước 1 - Tạo GitHub repository
1. Đăng nhập GitHub.
2. Chọn **New repository**.
3. Tên gợi ý: `jira-kpi-dashboard`.
4. Chọn **Private**.
5. Tạo repository.
6. Chọn **Add file > Upload files**.
7. Upload toàn bộ file/thư mục của gói này vào **gốc repository**.
8. Commit changes.

Không upload file chứa API token thật.

## Bước 2 - Tạo Streamlit Community Cloud
1. Mở `https://share.streamlit.io`.
2. Đăng nhập bằng GitHub.
3. Cho phép Streamlit truy cập repository private vừa tạo.
4. Chọn **Create app** -> **Yup, I have an app**.
5. Repository: chọn `jira-kpi-dashboard`.
6. Branch: `main`.
7. Main file path: `streamlit_app.py`.
8. Mở **Advanced settings**.

## Bước 3 - Dán Jira Secrets
Trong **Secrets**, dán nội dung sau và thay giá trị thật:

```toml
CLOUD_LOCKED_MODE = "true"
JIRA_BASE_URL = "https://TEN-MIEN-JIRA-CUA-BAN.atlassian.net"
JIRA_EMAIL = "email-dang-nhap-jira-cua-ban"
JIRA_API_TOKEN = "API_TOKEN_CUA_BAN"
JIRA_DEFAULT_JQL = '''project = "BANCORE" AND parentEpic IN (BANCORE-7559) AND issuetype = Task ORDER BY duedate ASC'''
```

Lưu ý: URL Jira phải là URL thực tế bạn đang mở trên trình duyệt. Không dùng URL ví dụ nếu không đúng.

## Bước 4 - Deploy
1. Bấm **Deploy**.
2. Đợi ứng dụng build vài phút.
3. Khi mở app, bấm **Kiểm tra quyền**.
4. Nếu OK, bấm **Đồng bộ**.

## Bước 5 - Đặt app ở chế độ private
Trong App settings -> Sharing:
- Chọn **Only specific people can view this app**.
- Chỉ mời email cần sử dụng.

## Nếu lỗi
- HTTP 401: email/token sai hoặc token không còn hiệu lực.
- HTTP 403: tài khoản thiếu quyền đọc project/issue/comment tương ứng.
- JQL lỗi: thử chạy chính JQL đó trong Jira trước.
- Không thấy Complexity: field có tên khác hoặc tài khoản không được nhìn field đó.
- Connection timeout: môi trường cloud không truy cập được Jira do chính sách mạng/IP allowlist của tổ chức.

## Không dùng GitHub Pages
GitHub Pages chỉ chạy website tĩnh và không chạy Python server-side. Vì vậy dashboard này phải chạy bằng Streamlit Community Cloud (hoặc dịch vụ web Python khác), còn GitHub dùng làm kho source code.
