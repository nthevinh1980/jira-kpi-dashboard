from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from jira_client import JiraApiError, JiraClient


APP_VERSION = "9.0-bsc-period-lock"
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



def value_matches(value: Any, expected: str) -> bool:
    """So khớp custom-field dạng text / option / list, không phân biệt hoa thường."""
    actual = as_text(value).strip().lower()
    target = str(expected or "").strip().lower()
    return bool(target) and target in actual


def build_row(
    issue: dict[str, Any],
    *,
    complexity_id: str | None,
    epic_id: str | None,
    division_id: str | None,
    comment_map: dict[str, Any],
    source: str,
    extra_default_weight: int,
) -> dict[str, Any]:
    f = issue.get("fields") or {}
    assignee = f.get("assignee") or {}
    status = f.get("status") or {}
    status_category = status.get("statusCategory") or {}
    issue_type = f.get("issuetype") or {}
    components_data = f.get("components") or []

    parent = f.get("parent") or {}
    epic_value = ""
    if isinstance(parent, dict):
        epic_value = str(parent.get("key") or "")
    if not epic_value and epic_id:
        epic_value = as_text(f.get(epic_id))

    complexity = as_text(f.get(complexity_id)) if complexity_id else ""
    if not complexity:
        complexity = "Không phân loại"

    comment_audit = comment_map.get(str(issue.get("key") or ""))
    comment_date = ""
    comment_author = ""
    late_update = False
    late_update_date = ""
    late_update_author = ""
    if isinstance(comment_audit, dict) and not comment_audit.get("error"):
        latest = comment_audit.get("latest") or {}
        late_marker = comment_audit.get("lateUpdateComment") or {}
        comment_date = iso_date(latest.get("created"))
        comment_author = str(latest.get("author") or "")
        late_update = bool(comment_audit.get("lateUpdate"))
        late_update_date = iso_date(late_marker.get("created"))
        late_update_author = str(late_marker.get("author") or "")

    due = iso_date(f.get("duedate"))
    resolution = iso_date(f.get("resolutiondate"))
    created = iso_date(f.get("created"))

    # BSC PERIOD LOCK:
    # Mỗi Task chỉ thuộc duy nhất kỳ BSC theo Due date.
    # Resolution date / Created KHÔNG được dùng để chuyển Task sang tháng khác.
    report_date = due
    period_basis = "Due date (BSC locked)" if due else "Không có Due date"

    return {
        "key": str(issue.get("key") or ""),
        "summary": str(f.get("summary") or ""),
        "assignee": str(assignee.get("displayName") or "(Chưa phân công)"),
        "assigneeAccountId": str(assignee.get("accountId") or ""),
        "team": "Fusion&QA" if source == "FUSION_QA" else "External assignment",
        "source": source,
        "division": as_text(f.get(division_id)) if division_id else "",
        "complexity": complexity,
        "weightOverride": extra_default_weight if source == "CAUNN" and complexity == "Không phân loại" else 0,
        "component": "; ".join(
            str(x.get("name") or "")
            for x in components_data
            if isinstance(x, dict) and x.get("name")
        ),
        "epic": epic_value,
        "type": str(issue_type.get("name") or ""),
        "due": due,
        "resolution": resolution,
        "created": created,
        "reportDate": report_date,
        "periodBasis": period_basis,
        "status": str(status.get("name") or ""),
        "statusCategory": str(status_category.get("key") or ""),
        "updated": iso_date(f.get("updated")),
        "comment": comment_date,
        "commentAuthor": comment_author,
        "lateUpdate": late_update,
        "lateUpdateDate": late_update_date,
        "lateUpdateAuthor": late_update_author,
        "labels": [str(x) for x in (f.get("labels") or [])],
    }


@st.cache_data(ttl=300, show_spinner=False)
def load_dashboard_data(
    base_url: str,
    email: str,
    token: str,
    main_jql: str,
    sync_comments: bool,
    late_update_marker: str,
    division_value: str,
    caunn_jql: str,
    caunn_default_weight: int,
):
    client = JiraClient(base_url, email, token)
    me = client.test_connection()
    catalog = client.get_fields()

    complexity_id = client.resolve_field_id(
        catalog, ["Complexity", "Độ phức tạp", "Do phuc tap"]
    )
    epic_id = client.resolve_field_id(
        catalog, ["Epic Link", "Parent Epic", "Parent Link"]
    )
    division_id = client.resolve_field_id(
        catalog,
        [
            "Division of CoreBanking",
            "Division of Corebanking",
            "Division CoreBanking",
            "Division",
        ],
    )

    if not division_id:
        raise JiraApiError(
            "Không tìm thấy custom field 'Division of CoreBanking' trên Jira. "
            "Hãy kiểm tra đúng tên field đang hiển thị trên Task."
        )

    fields = [
        "summary", "assignee", "status", "issuetype", "duedate",
        "resolutiondate", "created", "updated", "labels", "components", "parent",
    ]
    for fid in (complexity_id, epic_id, division_id):
        if fid and fid not in fields:
            fields.append(fid)

    # Nguồn 1: Epic BANCORE nhưng CHỈ lấy Task có Division of CoreBanking = Fusion&QA.
    main_all = client.search_issues(main_jql, fields, page_size=100, max_issues=10000)
    main_issues = [
        issue for issue in main_all
        if value_matches((issue.get("fields") or {}).get(division_id), division_value)
    ]

    # Nguồn 2: Cầu ở project khác.
    # JQL này nên là base query, không giới hạn tuần, để Dashboard tự lọc Tháng/Quý/Năm.
    caunn_issues = []
    if caunn_jql.strip():
        caunn_issues = client.search_issues(
            caunn_jql.strip(), fields, page_size=100, max_issues=10000
        )

    # Gộp 2 nguồn, loại trùng theo Issue Key.
    keyed: dict[str, tuple[dict[str, Any], str]] = {}
    for issue in main_issues:
        key = str(issue.get("key") or "")
        if key:
            keyed[key] = (issue, "FUSION_QA")
    for issue in caunn_issues:
        key = str(issue.get("key") or "")
        if key:
            keyed[key] = (issue, "CAUNN")

    combined = list(keyed.values())

    comment_map: dict[str, Any] = {}
    if sync_comments and combined:
        keys = [str(issue.get("key") or "") for issue, _ in combined if issue.get("key")]
        comment_map = client.comments_audit_bulk(keys, late_update_marker=late_update_marker, reviewer_account_id=me.account_id, workers=8)

    rows = [
        build_row(
            issue,
            complexity_id=complexity_id,
            epic_id=epic_id,
            division_id=division_id,
            comment_map=comment_map,
            source=source,
            extra_default_weight=caunn_default_weight,
        )
        for issue, source in combined
    ]

    return {
        "rows": rows,
        "display_name": me.display_name,
        "loaded_at": datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S"),
        "complexity_id": complexity_id or "",
        "epic_id": epic_id or "",
        "division_id": division_id or "",
        "division_value": division_value,
        "main_before_division": len(main_all),
        "main_after_division": len(main_issues),
        "caunn_count": len(caunn_issues),
        "combined_count": len(rows),
    }


base_url = secret("JIRA_BASE_URL")
email = secret("JIRA_EMAIL")
token = secret("JIRA_API_TOKEN")
jql = secret("JIRA_DEFAULT_JQL", DEFAULT_JQL)
sync_comments = secret("JIRA_SYNC_COMMENTS", "true").strip().lower() in {"1", "true", "yes", "y"}
late_update_marker = secret("JIRA_LATE_UPDATE_MARKER", "cập nhật muộn")

# Lọc nhóm theo custom field của Task, không lọc theo tên cán bộ nữa.
division_value = secret("JIRA_DIVISION_VALUE", "Fusion&QA")

# Cầu ở project khác. Base JQL bỏ điều kiện thời gian tuần để Dashboard tự lọc Tháng/Quý/Năm.
caunn_jql = secret(
    "JIRA_CAUNN_JQL",
    'project = "2024.PS006_Xây dựng ứng dụng tác nghiệp tập trung tại quầy" '
    'AND assignee = 712020:c282b441-9290-4c08-bc66-d834b94e17a7 '
    'AND issuetype = Sub-task '
    'AND status IN (Done, Closed, Resolved) '
    'ORDER BY resolved DESC'
)
caunn_default_weight = int(secret("JIRA_CAUNN_DEFAULT_WEIGHT", "1") or "1")


if not base_url or not email or not token:
    st.error(
        "Thiếu Jira Secrets. Cần có JIRA_BASE_URL, JIRA_EMAIL và JIRA_API_TOKEN "
        "trong Streamlit Cloud → App settings → Secrets."
    )
    st.stop()

try:
    with st.spinner("Đang đồng bộ dữ liệu Jira..."):
        payload = load_dashboard_data(base_url, email, token, jql, sync_comments, late_update_marker, division_value, caunn_jql, caunn_default_weight)
except JiraApiError as exc:
    st.error(f"Lỗi Jira API: {exc}")
    st.stop()

if not payload["rows"]:
    st.error(
        "Không có công việc nào sau khi gộp Division Fusion&QA và nguồn của Cầu. "
        "Hãy kiểm tra JIRA_DIVISION_VALUE và JIRA_CAUNN_JQL trong Streamlit Secrets."
    )
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
page = page.replace("__SYNC_TIME__", payload["loaded_at"])

# 2550 đủ cho dashboard desktop; bên trong iframe vẫn cuộn được.
components.html(page, height=2550, scrolling=True)
