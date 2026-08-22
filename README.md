# Jira KPI Dashboard V5 - Streamlit Community Cloud

Bản triển khai web miễn phí, lấy dữ liệu Jira qua REST API theo quyền của tài khoản cấu hình.

## Cấu trúc
- `streamlit_app.py`: ứng dụng chính
- `jira_client.py`: Jira REST API client
- `requirements.txt`: thư viện Python
- `.streamlit/config.toml`: cấu hình Streamlit
- `SECRETS_MAU.txt`: mẫu để dán vào Streamlit Secrets (không chứa token thật)

## Nguyên tắc bảo mật
- Dùng GitHub repository **Private**.
- Dùng Streamlit app **Private / Only specific people can view**.
- Không commit API Token vào GitHub.
- Đặt token trong Streamlit Community Cloud > App settings > Secrets.
- `CLOUD_LOCKED_MODE=true` sẽ ẩn ô nhập token trên giao diện và chỉ dùng Secrets.

Xem `HUONG_DAN_DEPLOY.md` để triển khai.
