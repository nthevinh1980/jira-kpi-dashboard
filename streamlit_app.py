from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from jira_client import JiraApiError, JiraClient


APP_VERSION = "5.0-streamlit-cloud"
DEFAULT_URL = ""
DEFAULT_JQL = 'project = "BANCORE" AND parentEpic IN (BANCORE-7559) AND issuetype = Task ORDER BY duedate ASC'
DONE_DEFAULT = "Done, Closed, Resolved"


st.set_page_config(
    page_title="BSC - Báo cáo hiệu suất đội nhóm",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.0rem; padding-bottom: 2rem;}
      div[data-testid="stMetric"] {background:#fff;border:1px solid #e9e9e9;padding:10px 14px;border-radius:10px;}
      .status-ok {padding:8px 10px;border:1px solid #d8eee0;border-radius:8px;background:#f5fff8;}
      .small-note {font-size:.86rem;color:#666;}
    </style>
    """,
    unsafe_allow_html=True,
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def secret_or_env(name: str, default: str = "") -> str:
    env = os.getenv(name)
    if env:
        return env
    try:
        value = st.secrets.get(name, default)
        return str(value) if value is not None else default
    except Exception:
        return default


def value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(x for x in (value_text(v) for v in value) if x)
    if isinstance(value, dict):
        for key in ("value", "name", "displayName", "key", "id"):
            if value.get(key) not in (None, ""):
                return str(value[key])
        return str(value)
    return str(value)


def parse_dates(series: pd.Series) -> pd.Series:
    if series is None:
        return pd.Series(dtype="datetime64[ns]")
    # Jira Cloud timestamps include timezone. Normalize to Vietnam time and remove tz.
    raw = series.replace({"": None})
    out = pd.to_datetime(raw, errors="coerce", utc=True)
    try:
        return out.dt.tz_convert("Asia/Ho_Chi_Minh").dt.tz_localize(None)
    except Exception:
        return pd.to_datetime(raw, errors="coerce")


def fmt_date(x: Any) -> str:
    if pd.isna(x):
        return ""
    return pd.Timestamp(x).strftime("%d/%m/%Y")


def period_bounds(kind: str, label: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    freq = "M" if kind == "Tháng" else "Q" if kind == "Quý" else "Y"
    p = pd.Period(label, freq=freq)
    return p.start_time.normalize(), p.end_time.normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)


def period_options(dates: pd.Series, kind: str) -> list[str]:
    d = dates.dropna()
    freq = "M" if kind == "Tháng" else "Q" if kind == "Quý" else "Y"
    if d.empty:
        return [str(pd.Timestamp.now().to_period(freq))]
    return sorted({str(x) for x in d.dt.to_period(freq)}, reverse=True)


def selected_point(chart_state):
    try:
        points = chart_state.selection.points
    except Exception:
        try:
            points = chart_state.get("selection", {}).get("points", [])
        except Exception:
            points = []
    return points[0] if points else None


def field_value(fields: dict[str, Any], field_id: str | None) -> Any:
    return fields.get(field_id) if field_id else None


def issues_to_df(
    issues: list[dict[str, Any]],
    *,
    complexity_id: str | None,
    epic_link_id: str | None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for issue in issues:
        f = issue.get("fields") or {}
        assignee = f.get("assignee") or {}
        status = f.get("status") or {}
        issue_type = f.get("issuetype") or {}
        components = f.get("components") or []
        parent = f.get("parent") or {}
        labels = f.get("labels") or []
        epic_val = field_value(f, epic_link_id)
        parent_text = value_text(parent) or value_text(epic_val)

        rows.append({
            "_key": str(issue.get("key") or ""),
            "_id": str(issue.get("id") or ""),
            "_summary": str(f.get("summary") or ""),
            "_assignee": str(assignee.get("displayName") or "(Chưa phân công)"),
            "_assignee_id": str(assignee.get("accountId") or ""),
            "_status": str(status.get("name") or ""),
            "_issue_type": str(issue_type.get("name") or ""),
            "_due_raw": f.get("duedate"),
            "_resolution_raw": f.get("resolutiondate"),
            "_created_raw": f.get("created"),
            "_updated_raw": f.get("updated"),
            "_labels": "; ".join(str(x) for x in labels),
            "_components": "; ".join(str(x.get("name") or "") for x in components if isinstance(x, dict)),
            "_complexity": value_text(field_value(f, complexity_id)) or "Không phân loại",
            "_parent": parent_text,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for raw, dest in [
        ("_due_raw", "_due"),
        ("_resolution_raw", "_resolution"),
        ("_created_raw", "_created"),
        ("_updated_raw", "_updated"),
    ]:
        df[dest] = parse_dates(df[raw])
    # Jira Due date is a calendar deadline. Treat the whole due date as on-time,
    # not only 00:00 at the beginning of that day.
    df["_due"] = df["_due"].dt.normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return df


def make_client(base_url: str, email: str, token: str) -> JiraClient:
    return JiraClient(base_url, email, token, timeout=45, verify_ssl=True)


def load_from_jira(client: JiraClient, jql: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    catalog = client.get_fields()
    complexity_id = client.resolve_field_id(catalog, ["Complexity", "Độ phức tạp", "Do phuc tap"])
    epic_link_id = client.resolve_field_id(catalog, ["Epic Link", "Parent Epic", "Parent Link"])

    wanted = [
        "summary", "assignee", "status", "issuetype", "duedate",
        "resolutiondate", "created", "updated", "labels", "components", "parent",
    ]
    for field_id in (complexity_id, epic_link_id):
        if field_id and field_id not in wanted:
            wanted.append(field_id)

    issues = client.search_issues(jql, wanted, page_size=100, max_issues=10000)
    df = issues_to_df(issues, complexity_id=complexity_id, epic_link_id=epic_link_id)
    meta = {
        "count": len(issues),
        "complexity_id": complexity_id,
        "epic_link_id": epic_link_id,
        "fields_count": len(catalog),
        "loaded_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }
    return df, meta


def sync_comments_for_keys(client: JiraClient, keys: list[str], force: bool = False) -> tuple[int, int]:
    cache = st.session_state.setdefault("comment_cache", {})
    need = keys if force else [k for k in keys if k not in cache]
    if not need:
        return 0, 0
    fetched = client.latest_comments_bulk(need, workers=8)
    cache.update(fetched)
    errors = sum(1 for v in fetched.values() if isinstance(v, dict) and v.get("error"))
    return len(fetched), errors


def apply_comment_cache(df: pd.DataFrame) -> pd.DataFrame:
    cache = st.session_state.get("comment_cache", {})
    out = df.copy()
    created = []
    authors = []
    texts = []
    errors = []
    for key in out["_key"].tolist():
        c = cache.get(key)
        if not c or c.get("error"):
            created.append(None)
            authors.append("")
            texts.append("")
            errors.append(c.get("error", "") if isinstance(c, dict) else "")
        else:
            created.append(c.get("created"))
            authors.append(str(c.get("author") or ""))
            texts.append(str(c.get("body") or ""))
            errors.append("")
    out["_last_comment"] = parse_dates(pd.Series(created, index=out.index))
    out["_last_comment_author"] = authors
    out["_last_comment_text"] = texts
    out["_comment_error"] = errors
    out["_comment_known"] = [bool(cache.get(k) is not None and not (isinstance(cache.get(k), dict) and cache.get(k).get("error"))) for k in out["_key"].tolist()]
    return out


def build_kpi(data: pd.DataFrame, w_complete: int, w_ontime: int) -> pd.DataFrame:
    rows = []
    for assignee, g in data.groupby("_assignee", dropna=False):
        total = len(g)
        done = int(g["_done_asof"].sum())
        ontime = int(g["_completed_on_time"].sum())
        late = int(g["_completed_late"].sum())
        overdue = int(g["_overdue"].sum())
        stale = int(g["_late_update"].sum())
        outside = int(g["_outside_bsc"].sum())
        known = int((g["_completed_on_time"] | g["_completed_late"]).sum())
        completion_rate = 100 * done / total if total else 0
        ontime_rate = 100 * ontime / known if known else (100 if done > 0 and overdue == 0 else 0)
        score = (w_complete * completion_rate + w_ontime * ontime_rate) / 100
        rows.append({
            "Cán bộ": assignee,
            "Công việc": total,
            "Hoàn thành": done,
            "Đúng hạn": ontime,
            "Hoàn thành trễ": late,
            "Quá hạn": overdue,
            "Cập nhật muộn": stale,
            "Ngoài BSC": outside,
            "Hoàn thành (%)": round(completion_rate, 1),
            "Đúng hạn (%)": round(ontime_rate, 1),
            "Hiệu suất (%)": round(score, 1),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["Hiệu suất (%)", "Công việc"], ascending=[False, False])


# -----------------------------------------------------------------------------
# Header + Jira connection — chế độ không cần Jira Admin
# -----------------------------------------------------------------------------
st.title("📊 BSC - Báo cáo hiệu suất đội nhóm")
st.caption("Web Dashboard V5 · chạy trên Streamlit Community Cloud · không cần Jira Admin.")

server_url = secret_or_env("JIRA_BASE_URL", DEFAULT_URL)
server_email = secret_or_env("JIRA_EMAIL", "")
server_token = secret_or_env("JIRA_API_TOKEN", "")
server_jql = secret_or_env("JIRA_DEFAULT_JQL", DEFAULT_JQL)
server_has_auth = bool(server_url and server_email and server_token)
cloud_locked = secret_or_env("CLOUD_LOCKED_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}

with st.sidebar:
    st.header("🔌 Kết nối Jira — không cần Admin")
    st.info(
        "Ứng dụng chỉ dùng REST API đọc dữ liệu theo đúng quyền Jira hiện có của tài khoản. "
        "Không cài plugin, không tạo app trong Jira, không thay đổi cấu hình hệ thống."
    )

    if cloud_locked:
        # Chế độ khuyến nghị khi chạy trên Streamlit Community Cloud:
        # thông tin Jira nằm trong Streamlit Secrets, không hiện ô nhập token cho người xem.
        auth_source = "Tài khoản cấu hình sẵn trên server"
        base_url = server_url
        email = server_email
        token = server_token
        st.caption("🔒 Chế độ Cloud an toàn: Jira URL / tài khoản / API Token được lấy từ Streamlit Secrets.")
        if server_has_auth:
            st.success("Đã nạp cấu hình Jira từ Secrets.")
            st.text_input("Jira URL", value=base_url, disabled=True)
        else:
            st.error("Chưa cấu hình JIRA_BASE_URL, JIRA_EMAIL và JIRA_API_TOKEN trong Streamlit Secrets.")
    else:
        auth_source = st.radio(
            "Cách đăng nhập",
            ["API Token cá nhân", "Tài khoản cấu hình sẵn trên server"],
            index=1 if server_has_auth else 0,
            help="Khi triển khai cloud, nên dùng tài khoản cấu hình sẵn trong Secrets.",
        )

        if auth_source == "Tài khoản cấu hình sẵn trên server":
            base_url = server_url
            email = server_email
            token = server_token
            st.text_input("Jira URL", value=base_url, disabled=True)
            if server_has_auth:
                st.success("Server đã có tài khoản Jira dùng để đọc dữ liệu.")
            else:
                st.warning("Server chưa được cấu hình tài khoản. Hãy dùng 'API Token cá nhân'.")
        else:
            base_url = st.text_input("Jira URL", value=server_url or DEFAULT_URL, placeholder="https://<ten-cong-ty>.atlassian.net")
            email = st.text_input("Email/User Jira", value=st.session_state.get("login_email", ""))
            token = st.text_input("API Token", value="", type="password")
            st.caption(
                "Token chỉ được giữ trong phiên Streamlit để gọi Jira; chương trình không ghi token vào CSV, log hay source code. "
                "Với Jira Cloud *.atlassian.net, dùng API Token thay cho password."
            )

    jql = st.text_area("JQL lấy dữ liệu", value=server_jql or DEFAULT_JQL, height=130)

    c1, c2 = st.columns(2)
    test_btn = c1.button("Kiểm tra quyền", use_container_width=True)
    sync_btn = c2.button("Đồng bộ", type="primary", use_container_width=True)

    logout_btn = st.button("Xóa phiên đăng nhập", use_container_width=True)

    st.divider()
    st.header("⚙️ Quy tắc KPI")
    done_text = st.text_input("Trạng thái hoàn thành", DONE_DEFAULT)
    done_statuses = {x.strip().lower() for x in done_text.split(",") if x.strip()}
    stale_days = st.number_input("Quá N ngày không comment = cập nhật muộn", 1, 60, 7)
    outside_text = st.text_input("Label công việc ngoài BSC", "ngoai_bsc,outside_bsc")
    outside_labels = {x.strip().lower() for x in outside_text.split(",") if x.strip()}
    w_complete = st.slider("Trọng số tỷ lệ hoàn thành", 0, 100, 70, 5)
    w_ontime = 100 - w_complete
    st.caption(f"Hiệu suất = {w_complete}% hoàn thành + {w_ontime}% đúng hạn")

if logout_btn:
    for k in ["jira_df", "jira_meta", "jira_info", "comment_cache", "active_auth", "diagnostic", "login_email"]:
        st.session_state.pop(k, None)
    st.success("Đã xóa dữ liệu và thông tin đăng nhập khỏi phiên hiện tại.")
    st.rerun()

if test_btn:
    if not (base_url and email and token):
        st.sidebar.error("Thiếu Jira URL, Email/User hoặc API Token.")
    else:
        try:
            client = make_client(base_url, email, token)
            with st.spinner("Đang kiểm tra đăng nhập, quyền xem project, JQL và comment..."):
                diag = client.diagnose_access(jql.strip() or DEFAULT_JQL, project_key="BANCORE")
            st.session_state["diagnostic"] = diag
            st.session_state["jira_info"] = diag["account"]
            st.session_state["login_email"] = email
            st.session_state["active_auth"] = {"base_url": base_url, "email": email, "token": token}
            if diag.get("browse_project") is False:
                st.sidebar.warning("Đăng nhập được nhưng tài khoản không có Browse Projects đối với BANCORE.")
            else:
                st.sidebar.success(f"Kết nối OK: {diag['account'].display_name}")
        except JiraApiError as exc:
            st.sidebar.error(str(exc))

if sync_btn:
    if not (base_url and email and token):
        st.sidebar.error("Thiếu Jira URL, Email/User hoặc API Token.")
    elif not jql.strip():
        st.sidebar.error("JQL đang trống.")
    else:
        try:
            client = make_client(base_url, email, token)
            info = client.test_connection()
            with st.spinner("Đang lấy field có quyền xem và toàn bộ Task từ Jira..."):
                jira_df, meta = load_from_jira(client, jql.strip())
            st.session_state["jira_df"] = jira_df
            st.session_state["jira_meta"] = meta
            st.session_state["jira_info"] = info
            st.session_state["comment_cache"] = {}
            st.session_state["active_auth"] = {"base_url": base_url, "email": email, "token": token}
            st.session_state["login_email"] = email
            st.success(f"Đã đồng bộ {len(jira_df):,} Jira lúc {meta['loaded_at']}.")
        except JiraApiError as exc:
            st.error(str(exc))

# Hiển thị chẩn đoán quyền nếu người dùng đã bấm Kiểm tra quyền.
diag = st.session_state.get("diagnostic")
if diag:
    with st.expander("🧪 Kết quả kiểm tra quyền Jira", expanded=True):
        a, b, c, d = st.columns(4)
        a.metric("Đăng nhập", "OK")
        b.metric("Browse BANCORE", "Có" if diag.get("browse_project") else "Không")
        c.metric("Field đọc được", diag.get("fields_count", 0))
        d.metric("JQL mẫu", f"{diag.get('sample_count', 0)} issue")
        if diag.get("sample_issue"):
            st.write(f"Issue thử: **{diag['sample_issue']}**")
        if diag.get("comment_test") == "ok":
            st.success("Đọc comment: OK (theo quyền của tài khoản).")
        elif diag.get("comment_test") == "no_issue":
            st.info("Chưa thử comment vì JQL không trả về issue mẫu.")
        else:
            st.warning(f"Comment chưa đọc được: {diag.get('comment_error', 'Không rõ nguyên nhân')}")
        st.caption(
            "Không có bước nào ở trên cần Jira Admin. Nếu Browse BANCORE = Không hoặc một số issue không xuất hiện, "
            "đó là do permission/issue security của tài khoản Jira hiện tại."
        )


# Keep credentials for calls made after initial sync; server secret values are also available on rerun.
active_auth = st.session_state.get("active_auth")
if not active_auth and base_url and email and token:
    active_auth = {"base_url": base_url, "email": email, "token": token}

if "jira_df" not in st.session_state:
    st.info(
        "Ở thanh bên trái, nhập **Email/User + API Token**, bấm **Kiểm tra quyền**, sau đó bấm **Đồng bộ**. "
        "Sau khi đồng bộ, dashboard sẽ chạy hoàn toàn từ dữ liệu Jira API."
    )
    st.stop()


df = st.session_state["jira_df"].copy()
meta = st.session_state.get("jira_meta", {})
info = st.session_state.get("jira_info")
if df.empty:
    st.warning("JQL không trả về Jira nào. Hãy kiểm tra lại JQL/quyền truy cập.")
    st.stop()

status_cols = st.columns([2.2, 1.2, 1.2, 1.4])
status_cols[0].markdown(
    f"**Nguồn dữ liệu:** `{base_url}`" + (f" · **Tài khoản:** {info.display_name}" if info else "")
)
status_cols[1].metric("Jira đã tải", f"{len(df):,}")
status_cols[2].metric("Cán bộ", df["_assignee"].nunique())
status_cols[3].markdown(f"**Đồng bộ:** {meta.get('loaded_at','')}")

with st.expander("Thông tin kỹ thuật lần đồng bộ", expanded=False):
    st.write({
        "JQL": jql,
        "Complexity field ID": meta.get("complexity_id") or "Không tìm thấy",
        "Epic Link field ID": meta.get("epic_link_id") or "Không tìm thấy / dùng parent",
        "Số field Jira phát hiện": meta.get("fields_count"),
    })


# -----------------------------------------------------------------------------
# Quick filters
# -----------------------------------------------------------------------------
quick = st.container(border=True)
with quick:
    q1, q2, q3, q4 = st.columns([1.0, 1.1, 1.5, 1.4])
    with q1:
        period_kind = st.selectbox("Kỳ báo cáo", ["Tháng", "Quý", "Năm"])
    with q4:
        basis = st.selectbox("Phạm vi công việc", ["Due date trong kỳ", "Hoàn thành trong kỳ", "Created trong kỳ"])

    if basis == "Due date trong kỳ":
        basis_dates = df["_due"]
    elif basis == "Hoàn thành trong kỳ":
        basis_dates = df["_resolution"]
    else:
        basis_dates = df["_created"]

    options = period_options(basis_dates, period_kind)
    with q2:
        period_label = st.selectbox("Chọn kỳ", options)
    with q3:
        staff_options = ["Tất cả"] + sorted(df["_assignee"].dropna().unique().tolist())
        staff_filter = st.selectbox("Chọn cán bộ", staff_options)

start, end = period_bounds(period_kind, period_label)
now = pd.Timestamp.now()
as_of = min(now, end)

if basis == "Due date trong kỳ":
    mask = df["_due"].between(start, end, inclusive="both")
elif basis == "Hoàn thành trong kỳ":
    mask = df["_resolution"].between(start, end, inclusive="both")
else:
    mask = df["_created"].between(start, end, inclusive="both")
period_df = df[mask].copy()

if period_df.empty:
    st.warning(f"Không có công việc trong {period_label} theo phạm vi đã chọn.")
    st.stop()


# -----------------------------------------------------------------------------
# Comment sync for selected period
# -----------------------------------------------------------------------------
with st.expander("💬 Đồng bộ comment để đánh giá việc cập nhật tiến độ", expanded=False):
    st.caption(
        "Ứng dụng gọi API comment theo từng Jira của kỳ đang xem và giữ cache trong phiên. "
        "Lần đầu có thể mất vài giây; những lần sau không gọi lại nếu không bấm Làm mới."
    )
    auto_comments = st.checkbox("Tự đồng bộ comment cho kỳ đang xem", value=True)
    force_comments = st.button("Làm mới comment của kỳ này")

if active_auth and (auto_comments or force_comments):
    keys = period_df["_key"].astype(str).tolist()
    try:
        comment_client = make_client(active_auth["base_url"], active_auth["email"], active_auth["token"])
        missing_count = sum(1 for k in keys if k not in st.session_state.get("comment_cache", {}))
        if force_comments or missing_count:
            with st.spinner(f"Đang đọc comment mới nhất cho {len(keys)} Jira..."):
                n, err = sync_comments_for_keys(comment_client, keys, force=force_comments)
            if n:
                st.caption(f"Đã đọc {n} Jira comment" + (f" · {err} lỗi quyền/API" if err else ""))
    except JiraApiError as exc:
        st.warning(f"Không đồng bộ được comment: {exc}")

period_df = apply_comment_cache(period_df)
comments_available = bool(period_df["_comment_known"].any())
comment_error_count = int((period_df["_comment_error"].fillna("") != "").sum())
if comment_error_count:
    st.warning(f"Có {comment_error_count} Jira không đọc được comment; các Jira này không bị tính là cập nhật muộn cho đến khi đọc được comment.")


# -----------------------------------------------------------------------------
# Historical KPI state
# -----------------------------------------------------------------------------
current_done = period_df["_status"].str.lower().isin(done_statuses)
done_asof = period_df["_resolution"].notna() & (period_df["_resolution"] <= as_of)
# Current period: tolerate Done without resolution date.
done_asof = done_asof | (current_done & period_df["_resolution"].isna() & (end >= now.normalize()))

period_df["_done_asof"] = done_asof
period_df["_completed_on_time"] = done_asof & period_df["_resolution"].notna() & period_df["_due"].notna() & (period_df["_resolution"] <= period_df["_due"])
period_df["_completed_late"] = done_asof & period_df["_resolution"].notna() & period_df["_due"].notna() & (period_df["_resolution"] > period_df["_due"])
period_df["_overdue"] = (~done_asof) & period_df["_due"].notna() & (period_df["_due"] < as_of)
period_df["_open_not_due"] = (~done_asof) & period_df["_due"].notna() & (period_df["_due"] >= as_of)

period_df["_days_since_comment"] = (as_of.normalize() - period_df["_last_comment"].dt.normalize()).dt.days
created_age = (as_of.normalize() - period_df["_created"].dt.normalize()).dt.days
period_df["_late_update"] = (~done_asof) & period_df["_comment_known"] & (
    (period_df["_last_comment"].notna() & (period_df["_days_since_comment"] > stale_days))
    | (period_df["_last_comment"].isna() & (created_age > stale_days))
)

labels_lower = period_df["_labels"].fillna("").str.lower()
period_df["_outside_bsc"] = False
for lab in outside_labels:
    period_df["_outside_bsc"] |= labels_lower.str.contains(re.escape(lab), na=False)

period_df["_result"] = np.select(
    [
        period_df["_completed_on_time"],
        period_df["_completed_late"],
        period_df["_overdue"],
        period_df["_open_not_due"],
        period_df["_done_asof"],
    ],
    ["Hoàn thành đúng hạn", "Hoàn thành trễ", "Quá hạn chưa xong", "Đang thực hiện", "Đã hoàn thành"],
    default="Khác",
)
period_df["_days_late"] = np.where(
    period_df["_completed_late"],
    (period_df["_resolution"].dt.normalize() - period_df["_due"].dt.normalize()).dt.days,
    np.where(
        period_df["_overdue"],
        (as_of.normalize() - period_df["_due"].dt.normalize()).dt.days,
        0,
    ),
)

view_df = period_df if staff_filter == "Tất cả" else period_df[period_df["_assignee"] == staff_filter]
kpi_df = build_kpi(period_df, w_complete, w_ontime)

st.markdown(f"**Đang xem:** {period_kind} **{period_label}** · dữ liệu tính đến **{fmt_date(as_of)}**")

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Công việc", len(view_df))
m2.metric("Hoàn thành", int(view_df["_done_asof"].sum()))
m3.metric("Quá hạn", int(view_df["_overdue"].sum()))
m4.metric("Cập nhật muộn", int(view_df["_late_update"].sum()) if comments_available else "Chưa đọc")
m5.metric("Ngoài BSC", int(view_df["_outside_bsc"].sum()))


# -----------------------------------------------------------------------------
# Main dashboard
# -----------------------------------------------------------------------------
left, right = st.columns([3.2, 1.25], gap="large")
with left:
    st.subheader("KPI Hiệu suất Cá nhân")
    show_kpi = kpi_df if staff_filter == "Tất cả" else kpi_df[kpi_df["Cán bộ"] == staff_filter]
    st.dataframe(
        show_kpi,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Hiệu suất (%)": st.column_config.ProgressColumn("Hiệu suất (%)", min_value=0, max_value=100, format="%.1f%%"),
            "Hoàn thành (%)": st.column_config.NumberColumn(format="%.1f%%"),
            "Đúng hạn (%)": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

    fig_perf = px.bar(
        show_kpi,
        x="Cán bộ",
        y="Hiệu suất (%)",
        text="Hiệu suất (%)",
        title="Hiệu suất theo cán bộ — click cột để xem Task",
        range_y=[0, 105],
    )
    fig_perf.update_layout(clickmode="event+select", xaxis_title="", yaxis_title="%")
    state = st.plotly_chart(fig_perf, use_container_width=True, on_select="rerun", selection_mode="points", key="perf")
    p = selected_point(state)
    if p and p.get("x"):
        st.session_state["detail_staff"] = str(p["x"])
        st.session_state["detail_scope"] = "Tất cả"

with right:
    st.subheader("Phân bổ Công việc - Loại")
    comp = view_df["_complexity"].replace("", "Không phân loại").value_counts().reset_index()
    comp.columns = ["Complexity", "Số lượng"]
    if len(comp):
        fig_comp = px.pie(comp, names="Complexity", values="Số lượng", hole=.52)
        fig_comp.update_layout(margin=dict(l=5, r=5, t=5, b=5))
        st.plotly_chart(fig_comp, use_container_width=True)

    st.subheader("⚠️ Tổng hợp Vấn đề Quá Hạn")
    od = period_df.groupby("_assignee")["_overdue"].sum().sort_values(ascending=True)
    od = od[od > 0]
    if od.empty:
        st.success("Không có task quá hạn trong kỳ.")
    else:
        od_df = od.reset_index(name="Quá hạn")
        fig_od = px.bar(od_df, x="Quá hạn", y="_assignee", orientation="h", text="Quá hạn")
        fig_od.update_layout(clickmode="event+select", xaxis_title="Số task", yaxis_title="")
        od_state = st.plotly_chart(fig_od, use_container_width=True, on_select="rerun", selection_mode="points", key="od")
        p = selected_point(od_state)
        if p and p.get("y"):
            st.session_state["detail_staff"] = str(p["y"])
            st.session_state["detail_scope"] = "Quá hạn chưa xong"

st.subheader("Phân bổ kết quả công việc theo cán bộ")
status_tab = period_df.groupby(["_assignee", "_result"]).size().reset_index(name="Số lượng")
fig_status = px.bar(status_tab, x="_assignee", y="Số lượng", color="_result", barmode="stack", text_auto=True)
fig_status.update_layout(clickmode="event+select", xaxis_title="", legend_title="Kết quả")
status_state = st.plotly_chart(fig_status, use_container_width=True, on_select="rerun", selection_mode="points", key="status")
p = selected_point(status_state)
if p and p.get("x"):
    st.session_state["detail_staff"] = str(p["x"])
    st.session_state["detail_scope"] = str(p.get("legendgroup") or "Tất cả")


# -----------------------------------------------------------------------------
# Drill-down
# -----------------------------------------------------------------------------
st.divider()
st.subheader("🔎 Chi tiết Task")
if "detail_staff" not in st.session_state:
    st.session_state["detail_staff"] = staff_filter if staff_filter != "Tất cả" else "Tất cả"
if "detail_scope" not in st.session_state:
    st.session_state["detail_scope"] = "Tất cả"
if staff_filter != "Tất cả":
    st.session_state["detail_staff"] = staff_filter

c1, c2, c3 = st.columns([1.3, 1.3, 2.4])
with c1:
    detail_staff_options = ["Tất cả"] + sorted(period_df["_assignee"].unique().tolist())
    current_staff = st.session_state.get("detail_staff", "Tất cả")
    idx = detail_staff_options.index(current_staff) if current_staff in detail_staff_options else 0
    detail_staff = st.selectbox("Cán bộ chi tiết", detail_staff_options, index=idx)
    st.session_state["detail_staff"] = detail_staff
with c2:
    scope_options = ["Tất cả", "Hoàn thành đúng hạn", "Hoàn thành trễ", "Quá hạn chưa xong", "Đang thực hiện", "Cập nhật muộn", "Ngoài BSC"]
    current_scope = st.session_state.get("detail_scope", "Tất cả")
    idx = scope_options.index(current_scope) if current_scope in scope_options else 0
    detail_scope = st.selectbox("Loại task", scope_options, index=idx)
    st.session_state["detail_scope"] = detail_scope
with c3:
    search_text = st.text_input("Tìm Issue / Summary / Label / Component", "")

D = period_df.copy()
if detail_staff != "Tất cả":
    D = D[D["_assignee"] == detail_staff]
if detail_scope == "Cập nhật muộn":
    D = D[D["_late_update"]]
elif detail_scope == "Ngoài BSC":
    D = D[D["_outside_bsc"]]
elif detail_scope != "Tất cả":
    D = D[D["_result"] == detail_scope]
if search_text.strip():
    q = re.escape(search_text.strip())
    D = D[
        D["_key"].str.contains(q, case=False, na=False)
        | D["_summary"].str.contains(q, case=False, na=False)
        | D["_labels"].str.contains(q, case=False, na=False)
        | D["_components"].str.contains(q, case=False, na=False)
    ]

out = pd.DataFrame({
    "Issue": D["_key"],
    "Summary": D["_summary"],
    "Cán bộ": D["_assignee"],
    "Status": D["_status"],
    "Due date": D["_due"].map(fmt_date),
    "Ngày hoàn thành": D["_resolution"].map(fmt_date),
    "Kết quả": D["_result"],
    "Chậm (ngày)": D["_days_late"].fillna(0).astype(int),
    "Comment gần nhất": D["_last_comment"].map(fmt_date),
    "Người comment": D["_last_comment_author"],
    "Nội dung comment gần nhất": D["_last_comment_text"].str.slice(0, 300),
    "Cập nhật muộn": np.where(D["_late_update"], "Có", ""),
    "Complexity": D["_complexity"],
    "Labels": D["_labels"],
    "Components": D["_components"],
    "Mở Jira": base_url.rstrip("/") + "/browse/" + D["_key"].astype(str),
})

st.caption(f"Đang hiển thị {len(out)} task. Click biểu đồ phía trên để drill-down theo cán bộ/vấn đề.")
st.dataframe(
    out,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Mở Jira": st.column_config.LinkColumn("Mở Jira", display_text="Mở"),
        "Nội dung comment gần nhất": st.column_config.TextColumn(width="large"),
    },
)

csv_bytes = out.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "⬇️ Tải danh sách đang xem (.CSV)",
    data=csv_bytes,
    file_name=f"Jira_KPI_{period_label}.csv",
    mime="text/csv",
)

st.caption(f"Jira KPI Web V{APP_VERSION}")
