

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from jira_client import JiraApiError, JiraClient


APP_VERSION = "10.6-direct-current-alert-query"
DEFAULT_JQL = 'project = "BANCORE" AND parentEpic IN (BANCORE-7559) AND issuetype = Task ORDER BY duedate ASC'
DEFAULT_CURRENT_ALERT_JQL = (
    'project = "BANCORE" '
    'AND parentEpic IN (BANCORE-7559) '
    'AND issuetype = Task '
    'AND statusCategory != Done '
    'ORDER BY duedate ASC'
)


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
    if isinstance(comment_audit, dict) and not comment_audit.get("error"):
        latest = comment_audit.get("latest") or {}
        comment_date = iso_date(latest.get("created"))
        comment_author = str(latest.get("author") or "")

    # V10.4: nguồn chính thức của "Cập nhật muộn" là Jira Label = Muon.
    # Không còn phụ thuộc comment marker để phân loại BSC.
    labels = [str(x).strip() for x in (f.get("labels") or []) if str(x).strip()]
    late_update = any(label.casefold() == "muon" for label in labels)
    late_update_date = ""
    late_update_author = ""

    due = iso_date(f.get("duedate"))
    resolution_raw = str(f.get("resolutiondate") or "")
    resolution = iso_date(resolution_raw)
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
        "resolutionAt": resolution_raw,
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
        "labels": labels,
        "lateUpdateSource": "Jira Label: Muon" if late_update else "",
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
    current_alert_jql: str,
    caunn_current_alert_jql: str,
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

    fields = [
        "summary", "assignee", "status", "issuetype", "duedate",
        "resolutiondate", "created", "updated", "labels", "components", "parent",
    ]
    for fid in (complexity_id, epic_id, division_id):
        if fid and fid not in fields:
            fields.append(fid)

    # Nguồn 1 BSC: JQL chỉ xác định TẬP CÔNG VIỆC GỐC (ví dụ Epic 7559).
    # Không được âm thầm loại Task chỉ vì Division bị trống/đổi sau này, vì sẽ làm sai BSC lịch sử.
    # Nếu thực sự cần khóa theo Division, bật JIRA_STRICT_DIVISION_FILTER=true trong Secrets.
    main_all = client.search_issues(main_jql, fields, page_size=100, max_issues=10000)
    strict_division_filter = secret("JIRA_STRICT_DIVISION_FILTER", "false").strip().lower() in {"1", "true", "yes", "y"}
    if strict_division_filter and division_id:
        main_issues = [
            issue for issue in main_all
            if value_matches((issue.get("fields") or {}).get(division_id), division_value)
        ]
    else:
        main_issues = list(main_all)

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

    # V10.6 — CẢNH BÁO HIỆN TẠI dùng một truy vấn Jira RIÊNG.
    # Lý do: nếu chỉ lấy DATA đã tải cho BSC rồi lọc bằng JavaScript,
    # các Task không có trong tập BSC nguồn sẽ không thể xuất hiện trong cảnh báo.
    alert_main_issues = client.search_issues(
        current_alert_jql.strip(),
        fields,
        page_size=100,
        max_issues=10000,
    ) if current_alert_jql.strip() else []

    alert_caunn_issues = client.search_issues(
        caunn_current_alert_jql.strip(),
        fields,
        page_size=100,
        max_issues=10000,
    ) if caunn_current_alert_jql.strip() else []

    alert_keyed: dict[str, tuple[dict[str, Any], str]] = {}
    for issue in alert_main_issues:
        key = str(issue.get("key") or "")
        if key:
            alert_keyed[key] = (issue, "FUSION_QA")
    for issue in alert_caunn_issues:
        key = str(issue.get("key") or "")
        if key:
            alert_keyed[key] = (issue, "CAUNN")
    alert_combined = list(alert_keyed.values())

    comment_map: dict[str, Any] = {}
    if sync_comments and combined:
        keys = [str(issue.get("key") or "") for issue, _ in combined if issue.get("key")]
        reviewer_account_id = secret("JIRA_LATE_UPDATE_REVIEWER_ACCOUNT_ID", "").strip()
        comment_map = client.comments_audit_bulk(
            keys,
            late_update_marker=late_update_marker,
            reviewer_account_id=reviewer_account_id,
            workers=8,
        )

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

    # Alert rows không cần đọc comment vì rule cập nhật muộn dùng Jira Label = Muon.
    alert_rows = [
        build_row(
            issue,
            complexity_id=complexity_id,
            epic_id=epic_id,
            division_id=division_id,
            comment_map={},
            source=source,
            extra_default_weight=caunn_default_weight,
        )
        for issue, source in alert_combined
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
        "strict_division_filter": strict_division_filter,
        "caunn_count": len(caunn_issues),
        "combined_count": len(rows),
        "alert_rows": alert_rows,
        "alert_main_count": len(alert_main_issues),
        "alert_caunn_count": len(alert_caunn_issues),
        "alert_combined_count": len(alert_rows),
    }


base_url = secret("JIRA_BASE_URL")
email = secret("JIRA_EMAIL")
token = secret("JIRA_API_TOKEN")
jql = secret("JIRA_DEFAULT_JQL", DEFAULT_JQL)
sync_comments = secret("JIRA_SYNC_COMMENTS", "true").strip().lower() in {"1", "true", "yes", "y"}
late_update_marker = secret("JIRA_LATE_UPDATE_MARKER", "cập nhật muộn")  # chỉ giữ tương thích; BSC V10.4 dùng Label Muon

# Lọc nhóm theo custom field của Task, không lọc theo tên cán bộ nữa.
division_value = secret("JIRA_DIVISION_VALUE", "Fusion&QA")

# Cầu ở project khác. Base JQL bỏ điều kiện thời gian tuần để Dashboard tự lọc Tháng/Quý/Năm.
caunn_jql = secret(
    "JIRA_CAUNN_JQL",
    'project = "2024.PS006_Xây dựng ứng dụng tác nghiệp tập trung tại quầy" '
    'AND assignee = 712020:c282b441-9290-4c08-bc66-d834b94e17a7 '
    'AND issuetype = Sub-task '
    'ORDER BY duedate ASC'
)
caunn_default_weight = int(secret("JIRA_CAUNN_DEFAULT_WEIGHT", "1") or "1")

# V10.6: nguồn độc lập cho cảnh báo hiện tại.
# Query này cố ý không có điều kiện tháng/quý/năm và chỉ lấy Task đang chưa Done.
current_alert_jql = secret("JIRA_CURRENT_ALERT_JQL", DEFAULT_CURRENT_ALERT_JQL)

caunn_current_alert_jql = secret(
    "JIRA_CAUNN_CURRENT_ALERT_JQL",
    'project = "2024.PS006_Xây dựng ứng dụng tác nghiệp tập trung tại quầy" '
    'AND assignee = 712020:c282b441-9290-4c08-bc66-d834b94e17a7 '
    'AND issuetype = Sub-task '
    'AND statusCategory != Done '
    'ORDER BY duedate ASC'
)


# JQL nguồn BSC không nên lọc theo trạng thái/ngày hiện tại. Nếu lọc status/resolved/duedate theo thời gian,
# Task quá hạn của tháng cũ có thể biến mất sau khi Jira được Done ở tháng sau.
_jql_l = jql.lower()
_risky_tokens = ["status =", "status in", "statuscategory", "resolutiondate", "resolved >=", "resolved <=", "duedate >=", "duedate <=", "created >=", "created <="]
if any(t in _jql_l for t in _risky_tokens):
    st.warning(
        "JIRA_DEFAULT_JQL đang có điều kiện trạng thái/ngày. Với BSC lịch sử, JQL nguồn nên chỉ xác định project/epic/issuetype; "
        "việc lọc tháng và trạng thái phải để Dashboard thực hiện theo Due date + mốc BSC."
    )

if not base_url or not email or not token:
    st.error(
        "Thiếu Jira Secrets. Cần có JIRA_BASE_URL, JIRA_EMAIL và JIRA_API_TOKEN "
        "trong Streamlit Cloud → App settings → Secrets."
    )
    st.stop()

try:
    with st.spinner("Đang đồng bộ dữ liệu Jira..."):
        payload = load_dashboard_data(base_url, email, token, jql, sync_comments, late_update_marker, division_value, caunn_jql, caunn_default_weight, current_alert_jql, caunn_current_alert_jql)
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
alert_json = json.dumps(payload.get("alert_rows") or [], ensure_ascii=False).replace("</script>", "<\\/script>")
base_json = json.dumps(base_url.rstrip("/"), ensure_ascii=False)

page = page.replace("__JIRA_DATA__", data_json)
page = page.replace("__JIRA_ALERT_DATA__", alert_json)
page = page.replace("__JIRA_BASE_URL__", base_json)
page = page.replace("__SYNC_TIME__", payload["loaded_at"])

# 2550 đủ cho dashboard desktop; bên trong iframe vẫn cuộn được.
components.html(page, height=2550, scrolling=True)
