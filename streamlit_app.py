from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from jira_client import JiraApiError, JiraClient


APP_VERSION = "8.0"
DEFAULT_JQL = 'project = "BANCORE" AND parentEpic IN (BANCORE-7559) AND issuetype = Task ORDER BY duedate ASC'

st.set_page_config(
    page_title="Jira BSC Executive Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Ẩn giao diện mặc định của Streamlit để HTML chiếm toàn bộ màn hình.
st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    background:#f6f8fb !important;
}
[data-testid="stHeader"], [data-testid="stToolbar"], footer {
    visibility:hidden !important;
    height:0 !important;
}
.block-container {
    max-width:none !important;
    padding:0 !important;
    margin:0 !important;
}
iframe { border:0 !important; }
</style>
""", unsafe_allow_html=True)


def secret(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value:
        return value
    try:
        return str(st.secrets.get(name, default) or default)
    except Exception:
        return default


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        values = [as_text(v) for v in value]
        return "; ".join(v for v in values if v)
    if isinstance(value, dict):
        for key in ("value", "name", "displayName", "key", "id"):
            if value.get(key) not in (None, ""):
                return str(value[key])
    return str(value)


def iso_date(value: Any) -> str:
    if not value:
        return ""
    return str(value)[:10]


@st.cache_data(ttl=900, show_spinner=False)
def load_dashboard_data(base_url: str, email: str, token: str, jql: str, sync_comments: bool):
    client = JiraClient(base_url, email, token)
    me = client.test_connection()
    catalog = client.get_fields()

    complexity_id = client.resolve_field_id(
        catalog, ["Complexity", "Độ phức tạp", "Do phuc tap"]
    )
    epic_id = client.resolve_field_id(
        catalog, ["Epic Link", "Parent Epic", "Parent Link"]
    )

    fields = [
        "summary",
        "assignee",
        "status",
        "issuetype",
        "duedate",
        "resolutiondate",
        "created",
        "updated",
        "labels",
        "components",
        "parent",
    ]
    for fid in (complexity_id, epic_id):
        if fid and fid not in fields:
            fields.append(fid)

    issues = client.search_issues(jql, fields, page_size=100, max_issues=10000)

    comment_map = {}
    if sync_comments and issues:
        keys = [str(issue.get("key") or "") for issue in issues if issue.get("key")]
        comment_map = client.latest_comments_bulk(keys, workers=8)

    rows = []
    for issue in issues:
        fields_data = issue.get("fields") or {}
        assignee = fields_data.get("assignee") or {}
        status = fields_data.get("status") or {}
        status_category = status.get("statusCategory") or {}
        issue_type = fields_data.get("issuetype") or {}
        components_data = fields_data.get("components") or []

        parent = fields_data.get("parent") or {}
        epic_value = ""
        if isinstance(parent, dict):
            epic_value = str(parent.get("key") or "")
        if not epic_value and epic_id:
            epic_value = as_text(fields_data.get(epic_id))

        complexity = as_text(fields_data.get(complexity_id)) if complexity_id else ""
        if not complexity:
            complexity = "Không phân loại"

        comment = comment_map.get(str(issue.get("key") or ""))
        comment_date = ""
        if isinstance(comment, dict) and not comment.get("error"):
            comment_date = iso_date(comment.get("created"))

        rows.append({
            "key": str(issue.get("key") or ""),
            "summary": str(fields_data.get("summary") or ""),
            "assignee": str(assignee.get("displayName") or "(Chưa phân công)"),
            "team": "",
            "complexity": complexity,
            "component": "; ".join(
                str(x.get("name") or "")
                for x in components_data
                if isinstance(x, dict) and x.get("name")
            ),
            "epic": epic_value,
            "type": str(issue_type.get("name") or ""),
            "due": iso_date(fields_data.get("duedate")),
            "resolution": iso_date(fields_data.get("resolutiondate")),
            "status": str(status.get("name") or ""),
            "statusCategory": str(status_category.get("key") or ""),
            "updated": iso_date(fields_data.get("updated")),
            # Nếu đọc được Comment thì dùng Comment; nếu không, JS tự fallback sang Updated.
            "comment": comment_date,
            "labels": [str(x) for x in (fields_data.get("labels") or [])],
        })

    return {
        "rows": rows,
        "display_name": me.display_name,
        "loaded_at": datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S"),
        "complexity_id": complexity_id or "",
        "epic_id": epic_id or "",
    }


base_url = secret("JIRA_BASE_URL")
email = secret("JIRA_EMAIL")
token = secret("JIRA_API_TOKEN")
jql = secret("JIRA_DEFAULT_JQL", DEFAULT_JQL)
sync_comments = secret("JIRA_SYNC_COMMENTS", "true").strip().lower() in {"1", "true", "yes", "y"}
target_workload = int(secret("JIRA_TARGET_WORKLOAD_MONTH", "20") or "20")

if not base_url or not email or not token:
    st.error(
        "Thiếu Jira Secrets. Cần có JIRA_BASE_URL, JIRA_EMAIL và JIRA_API_TOKEN "
        "trong Streamlit Cloud → App settings → Secrets."
    )
    st.stop()

try:
    with st.spinner("Đang đồng bộ dữ liệu Jira..."):
        payload = load_dashboard_data(base_url, email, token, jql, sync_comments)
except JiraApiError as exc:
    st.error(f"Lỗi Jira API: {exc}")
    st.stop()

template_path = Path(__file__).with_name("dashboard_template.html")
if not template_path.exists():
    st.error("Thiếu file dashboard_template.html trên GitHub.")
    st.stop()

page = template_path.read_text(encoding="utf-8")
data_json = json.dumps(payload["rows"], ensure_ascii=False).replace("</script>", "<\\/script>")
base_json = json.dumps(base_url.rstrip("/"), ensure_ascii=False)

page = page.replace("__JIRA_DATA__", data_json)
page = page.replace("__JIRA_BASE_URL__", base_json)
page = page.replace("__TARGET_WORKLOAD_MONTH__", str(target_workload))
page = page.replace("__SYNC_TIME__", payload["loaded_at"])

# 2550 đủ cho dashboard desktop; bên trong iframe vẫn cuộn được.
components.html(page, height=2550, scrolling=True)
