
from __future__ import annotations

import html
import os
import re
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from jira_client import JiraApiError, JiraClient


APP_VERSION = "6.0"
DEFAULT_JQL = 'project = "BANCORE" AND parentEpic IN (BANCORE-7559) AND issuetype = Task ORDER BY duedate ASC'
DONE_DEFAULT = "Done, Closed, Resolved"
COMPLEXITY_WEIGHT = {
    "Very Complex": 5,
    "Complex": 4,
    "Medium": 3,
    "Simple": 2,
    "Very Simple": 1,
    "Không phân loại": 1,
}

st.set_page_config(
    page_title="Jira BSC Executive Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------
# STYLE
# ---------------------------------------------------------------------
st.markdown("""
<style>
:root{
  --navy:#0b1f33; --navy2:#123f64; --blue:#177ddc; --cyan:#11a7b8;
  --green:#13966a; --amber:#d98b17; --red:#d94b57; --violet:#7257b5;
  --bg:#f4f7fb; --card:#fff; --line:#e5edf4; --text:#173249; --muted:#7b91a4;
}
html,body,[data-testid="stAppViewContainer"]{background:#f4f7fb}
[data-testid="stHeader"]{background:transparent}
.block-container{max-width:1600px;padding-top:1rem;padding-bottom:2rem}
.bsc-hero{
  background:linear-gradient(118deg,#071c31 0%,#0d3d61 43%,#0a7897 76%,#17a184 100%);
  color:white;border-radius:24px;padding:22px 24px;box-shadow:0 18px 50px rgba(11,31,51,.18);
  position:relative;overflow:hidden;margin-bottom:14px;
}
.bsc-hero:after{content:"";position:absolute;width:360px;height:360px;border-radius:50%;background:rgba(255,255,255,.07);right:-100px;top:-180px}
.bsc-hero-title{font-size:29px;font-weight:900;letter-spacing:-.03em;position:relative;z-index:1}
.bsc-hero-sub{font-size:13px;color:#d8eef5;margin-top:7px;position:relative;z-index:1}
.bsc-live{display:inline-flex;align-items:center;gap:8px;margin-top:13px;padding:7px 10px;border:1px solid rgba(255,255,255,.20);background:rgba(255,255,255,.10);border-radius:999px;font-size:11px;font-weight:750;position:relative;z-index:1}
.bsc-dot{width:8px;height:8px;background:#67f0ad;border-radius:50%;box-shadow:0 0 0 5px rgba(103,240,173,.14)}
.bsc-period{display:inline-block;background:white;color:#0a5673;padding:7px 11px;border-radius:999px;font-size:11px;font-weight:900;margin-left:7px}
.kpi-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:14px 0}
.kpi-card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:14px;box-shadow:0 7px 24px rgba(20,54,83,.05);min-width:0}
.kpi-top{display:flex;justify-content:space-between;align-items:center;gap:8px}
.kpi-label{font-size:11px;font-weight:800;color:#71869a}
.kpi-icon{width:34px;height:34px;border-radius:11px;display:grid;place-items:center;background:var(--soft);color:var(--c)}
.kpi-icon svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.9}
.kpi-value{font-size:29px;font-weight:950;letter-spacing:-.04em;color:var(--c);margin-top:8px}
.kpi-meta{font-size:10px;color:#8a9bab;margin-top:6px}
.delta{display:inline-block;padding:3px 6px;border-radius:999px;font-weight:850;margin-right:4px}
.delta-up{background:#e8f8ef;color:#15804e}.delta-down{background:#ffecee;color:#c13f4c}.delta-neutral{background:#edf4fb;color:#316b9d}
.c-blue{--c:#177ddc;--soft:#eaf4ff}.c-green{--c:#13966a;--soft:#e9f8ef}.c-teal{--c:#0e8fa1;--soft:#e7f7f8}
.c-amber{--c:#d98b17;--soft:#fff4e4}.c-red{--c:#d94b57;--soft:#ffedef}.c-violet{--c:#7257b5;--soft:#f1ecfb}
.c-navy{--c:#285778;--soft:#eaf0f6}.c-cyan{--c:#158ab8;--soft:#e9f7fc}.c-gold{--c:#a97812;--soft:#fff7df}
.exec-summary{background:white;border:1px solid var(--line);border-radius:18px;padding:14px;display:grid;grid-template-columns:1.35fr repeat(4,1fr);gap:10px;box-shadow:0 7px 22px rgba(22,48,75,.04);margin-bottom:14px}
.exec-intro{padding:3px 7px}.exec-intro b{font-size:14px}.exec-intro p{margin:5px 0 0;font-size:10px;line-height:1.45;color:var(--muted)}
.exec-target{border-left:1px solid #e8eef3;padding-left:12px}.exec-target span{font-size:9px;color:#7f93a5}.exec-target strong{display:block;font-size:20px;margin-top:4px}.exec-target small{font-size:9px;font-weight:800}
.t-good{color:#13966a}.t-warn{color:#d98b17}.t-bad{color:#d94b57}
.section-title{font-size:17px;font-weight:900;color:#17354c;margin:8px 0 2px}.section-sub{font-size:11px;color:#879aaa;margin-bottom:10px}
[data-testid="stDataFrame"]{background:white;border:1px solid #e6edf3;border-radius:16px;padding:5px;box-shadow:0 7px 24px rgba(19,48,75,.04)}
div[data-testid="stPlotlyChart"]{background:white;border:1px solid #e6edf3;border-radius:18px;padding:8px;box-shadow:0 7px 24px rgba(19,48,75,.04)}
div[data-testid="stExpander"]{background:white;border-radius:14px;border:1px solid #e6edf3}
[data-testid="stSidebar"]{background:#f8fbfd}
.stButton>button,.stDownloadButton>button{border-radius:10px;font-weight:800}
.attention{border-radius:14px;padding:12px 13px;border:1px solid #e9eef3;background:white;margin:6px 0}
.attention.red{border-left:4px solid #d94b57}.attention.amber{border-left:4px solid #d98b17}.attention.blue{border-left:4px solid #177ddc}
.attention b{font-size:12px}.attention p{font-size:10px;color:#7b91a4;margin:4px 0 0}
@media(max-width:1200px){.kpi-grid{grid-template-columns:repeat(3,1fr)}.exec-summary{grid-template-columns:1fr 1fr 1fr}.exec-intro{grid-column:1/-1}}
@media(max-width:700px){.kpi-grid{grid-template-columns:1fr 1fr}.exec-summary{grid-template-columns:1fr 1fr}}
@media(max-width:450px){.kpi-grid,.exec-summary{grid-template-columns:1fr}.bsc-hero-title{font-size:23px}}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------
def secret(name: str, default: str = "") -> str:
    env = os.getenv(name)
    if env:
        return env
    try:
        return str(st.secrets.get(name, default) or default)
    except Exception:
        return default


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "; ".join(txt(v) for v in value if txt(v))
    if isinstance(value, dict):
        for k in ("value", "name", "displayName", "key", "id"):
            if value.get(k) not in (None, ""):
                return str(value[k])
    return str(value)


def parse_dates(s: pd.Series) -> pd.Series:
    out = pd.to_datetime(s.replace({"": None}), errors="coerce", utc=True)
    try:
        return out.dt.tz_convert("Asia/Ho_Chi_Minh").dt.tz_localize(None)
    except Exception:
        return pd.to_datetime(s, errors="coerce")


def fmt_date(x: Any) -> str:
    if pd.isna(x):
        return ""
    return pd.Timestamp(x).strftime("%d/%m/%Y")


def field_value(fields: dict[str, Any], fid: str | None) -> Any:
    return fields.get(fid) if fid else None


def issues_to_df(issues, complexity_id=None, epic_id=None):
    rows = []
    for issue in issues:
        f = issue.get("fields") or {}
        assignee = f.get("assignee") or {}
        status = f.get("status") or {}
        itype = f.get("issuetype") or {}
        comps = f.get("components") or []
        parent = f.get("parent") or {}
        rows.append({
            "_key": str(issue.get("key") or ""),
            "_summary": str(f.get("summary") or ""),
            "_assignee": str(assignee.get("displayName") or "(Chưa phân công)"),
            "_status": str(status.get("name") or ""),
            "_issue_type": str(itype.get("name") or ""),
            "_due_raw": f.get("duedate"),
            "_resolution_raw": f.get("resolutiondate"),
            "_created_raw": f.get("created"),
            "_updated_raw": f.get("updated"),
            "_labels": "; ".join(str(x) for x in (f.get("labels") or [])),
            "_components": "; ".join(str(x.get("name") or "") for x in comps if isinstance(x, dict)),
            "_complexity": txt(field_value(f, complexity_id)) or "Không phân loại",
            "_parent": txt(parent) or txt(field_value(f, epic_id)),
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
    # Due date là hạn hết ngày
    df["_due"] = df["_due"].dt.normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return df


def load_jira(base_url: str, email: str, token: str, jql: str):
    client = JiraClient(base_url, email, token)
    info = client.test_connection()
    catalog = client.get_fields()
    complexity_id = client.resolve_field_id(catalog, ["Complexity", "Độ phức tạp", "Do phuc tap"])
    epic_id = client.resolve_field_id(catalog, ["Epic Link", "Parent Epic", "Parent Link"])

    fields = [
        "summary", "assignee", "status", "issuetype", "duedate",
        "resolutiondate", "created", "updated", "labels", "components", "parent",
    ]
    for fid in (complexity_id, epic_id):
        if fid and fid not in fields:
            fields.append(fid)

    issues = client.search_issues(jql, fields, page_size=100, max_issues=10000)
    return issues_to_df(issues, complexity_id, epic_id), {
        "user": info.display_name,
        "loaded_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "complexity_id": complexity_id,
        "epic_id": epic_id,
    }


def sync_comments(base_url, email, token, keys, force=False):
    cache = st.session_state.setdefault("comment_cache", {})
    need = list(keys) if force else [k for k in keys if k not in cache]
    if not need:
        return
    client = JiraClient(base_url, email, token)
    cache.update(client.latest_comments_bulk(need, workers=8))


def apply_comments(df: pd.DataFrame) -> pd.DataFrame:
    cache = st.session_state.get("comment_cache", {})
    out = df.copy()
    dates, authors, bodies, known = [], [], [], []
    for key in out["_key"]:
        in_cache = key in cache
        c = cache.get(key)
        if not in_cache or (isinstance(c, dict) and c.get("error")):
            dates.append(None); authors.append(""); bodies.append(""); known.append(False)
        elif c is None:
            # Đã gọi API và Jira xác nhận issue không có comment.
            dates.append(None); authors.append(""); bodies.append(""); known.append(True)
        else:
            dates.append(c.get("created"))
            authors.append(c.get("author", ""))
            bodies.append(c.get("body", ""))
            known.append(True)
    out["_last_comment"] = parse_dates(pd.Series(dates, index=out.index))
    out["_last_comment_author"] = authors
    out["_last_comment_text"] = bodies
    out["_comment_known"] = known
    return out


def period_bounds(kind: str, label: str):
    freq = {"Tháng": "M", "Quý": "Q", "Năm": "Y"}[kind]
    p = pd.Period(label, freq=freq)
    return p.start_time.normalize(), p.end_time.normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)


def period_options(dates: pd.Series, kind: str):
    freq = {"Tháng": "M", "Quý": "Q", "Năm": "Y"}[kind]
    d = dates.dropna()
    if d.empty:
        return [str(pd.Timestamp.now().to_period(freq))]
    return sorted({str(x) for x in d.dt.to_period(freq)}, reverse=True)


def previous_bounds(kind: str, label: str):
    freq = {"Tháng": "M", "Quý": "Q", "Năm": "Y"}[kind]
    p = pd.Period(label, freq=freq) - 1
    return p.start_time.normalize(), p.end_time.normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)


def icon_svg(name: str) -> str:
    icons = {
        "tasks": '<svg viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>',
        "done": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16.5 8"/></svg>',
        "time": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
        "late": '<svg viewBox="0 0 24 24"><path d="M5 18 18 5M10 5h8v8"/></svg>',
        "over": '<svg viewBox="0 0 24 24"><path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 9v5M12 17h.01"/></svg>',
        "doing": '<svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 3-6"/><path d="M4 4v5h5"/></svg>',
        "comment": '<svg viewBox="0 0 24 24"><path d="M4 5h16v11H8l-4 4V5Z"/><path d="M8 9h8M8 12h5"/></svg>',
        "folder": '<svg viewBox="0 0 24 24"><path d="M4 6h7l2 3h7v9H4V6Z"/></svg>',
        "workload": '<svg viewBox="0 0 24 24"><path d="M5 18V9M12 18V5M19 18v-7"/></svg>',
        "star": '<svg viewBox="0 0 24 24"><path d="m12 3 2.5 5 5.5.8-4 3.9.9 5.5-4.9-2.6-4.9 2.6.9-5.5-4-3.9 5.5-.8L12 3Z"/></svg>',
    }
    return icons[name]


def kpi_card(label, value, meta, css, icon, delta=None, delta_kind="neutral"):
    # QUAN TRỌNG: trả HTML dạng một dòng, không có blank line/indent đầu dòng.
    # Nếu có dòng trống + 4 khoảng trắng, Markdown của Streamlit có thể
    # hiểu phần HTML kế tiếp là code block và hiển thị nguyên thẻ <div>.
    delta_html = ""
    if delta is not None:
        cls = {"up": "delta-up", "down": "delta-down", "neutral": "delta-neutral"}[delta_kind]
        delta_html = f'<span class="delta {cls}">{html.escape(str(delta))}</span>'
    return (
        f'<div class="kpi-card {css}">'
        f'<div class="kpi-top">'
        f'<span class="kpi-label">{html.escape(label)}</span>'
        f'<span class="kpi-icon">{icon_svg(icon)}</span>'
        f'</div>'
        f'<div class="kpi-value">{html.escape(str(value))}</div>'
        f'<div class="kpi-meta">{delta_html}{meta}</div>'
        f'</div>'
    )


def calc_metrics(data: pd.DataFrame, as_of: pd.Timestamp, done_statuses, stale_days, outside_labels):
    g = data.copy()
    current_done = g["_status"].str.lower().isin(done_statuses)
    done_asof = g["_resolution"].notna() & (g["_resolution"] <= as_of)
    done_asof |= current_done & g["_resolution"].isna()

    g["_done_asof"] = done_asof
    g["_completed_on_time"] = done_asof & g["_resolution"].notna() & g["_due"].notna() & (g["_resolution"] <= g["_due"])
    g["_completed_late"] = done_asof & g["_resolution"].notna() & g["_due"].notna() & (g["_resolution"] > g["_due"])
    g["_overdue"] = (~done_asof) & g["_due"].notna() & (g["_due"] < as_of)
    g["_doing"] = (~done_asof) & ~g["_overdue"]

    g["_days_since_comment"] = (as_of.normalize() - g["_last_comment"].dt.normalize()).dt.days
    g["_days_since_update"] = (as_of.normalize() - g["_updated"].dt.normalize()).dt.days

    # Nếu đã đọc comment: dùng comment; chưa đọc comment: dùng Updated làm proxy.
    freshness_days = g["_days_since_update"].copy()
    freshness_days.loc[g["_comment_known"]] = g.loc[g["_comment_known"], "_days_since_comment"]
    g["_freshness_days"] = freshness_days
    g["_late_update"] = (~done_asof) & (freshness_days > stale_days)

    labels = g["_labels"].fillna("").str.lower()
    g["_outside_bsc"] = False
    for lab in outside_labels:
        g["_outside_bsc"] |= labels.str.contains(re.escape(lab), na=False)

    g["_weight"] = g["_complexity"].map(COMPLEXITY_WEIGHT).fillna(1).astype(float)
    g["_workload"] = g["_weight"]

    g["_result"] = np.select(
        [g["_completed_on_time"], g["_completed_late"], g["_overdue"], g["_doing"], g["_done_asof"]],
        ["Hoàn thành đúng hạn", "Hoàn thành trễ", "Quá hạn chưa xong", "Đang thực hiện", "Đã hoàn thành"],
        default="Khác",
    )
    g["_days_late"] = np.where(
        g["_completed_late"],
        (g["_resolution"].dt.normalize() - g["_due"].dt.normalize()).dt.days,
        np.where(g["_overdue"], (as_of.normalize() - g["_due"].dt.normalize()).dt.days, 0),
    )

    total = len(g)
    done = int(g["_done_asof"].sum())
    ontime = int(g["_completed_on_time"].sum())
    late = int(g["_completed_late"].sum())
    overdue = int(g["_overdue"].sum())
    doing = int(g["_doing"].sum())
    stale = int(g["_late_update"].sum())
    outside = int(g["_outside_bsc"].sum())
    workload = float(g["_workload"].sum())

    completion_rate = 100 * done / total if total else 0
    known_done = ontime + late
    ontime_rate = 100 * ontime / known_done if known_done else 0
    score = 0.70 * completion_rate + 0.30 * ontime_rate

    return g, {
        "total": total, "done": done, "ontime": ontime, "late": late,
        "overdue": overdue, "doing": doing, "stale": stale, "outside": outside,
        "workload": workload, "completion_rate": completion_rate,
        "ontime_rate": ontime_rate, "score": score,
    }


def staff_kpi(g: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for staff, d in g.groupby("_assignee"):
        total = len(d)
        done = int(d["_done_asof"].sum())
        ontime = int(d["_completed_on_time"].sum())
        late = int(d["_completed_late"].sum())
        overdue = int(d["_overdue"].sum())
        doing = int(d["_doing"].sum())
        stale = int(d["_late_update"].sum())
        workload = float(d["_workload"].sum())
        c_rate = 100 * done / total if total else 0
        known = ontime + late
        o_rate = 100 * ontime / known if known else 0
        score = .70 * c_rate + .30 * o_rate
        rows.append({
            "Cán bộ": staff, "Tổng": total, "Done": done, "Đúng hạn": ontime,
            "HT trễ": late, "Quá hạn": overdue, "Đang làm": doing,
            "Update muộn": stale, "Workload": round(workload, 1),
            "HT %": round(c_rate, 1), "Đúng hạn %": round(o_rate, 1), "BSC %": round(score, 1),
        })
    return pd.DataFrame(rows).sort_values(["BSC %", "Workload"], ascending=[False, False])


def period_mask(df, basis, start, end):
    if basis == "Due date trong kỳ":
        return df["_due"].between(start, end, inclusive="both")
    if basis == "Hoàn thành trong kỳ":
        return df["_resolution"].between(start, end, inclusive="both")
    return df["_created"].between(start, end, inclusive="both")


def pct_delta(cur, prev):
    if prev == 0:
        return None
    return 100 * (cur - prev) / prev


# ---------------------------------------------------------------------
# CONFIG / DATA
# ---------------------------------------------------------------------
BASE_URL = secret("JIRA_BASE_URL")
EMAIL = secret("JIRA_EMAIL")
TOKEN = secret("JIRA_API_TOKEN")
DEFAULT_QUERY = secret("JIRA_DEFAULT_JQL", DEFAULT_JQL)

if not (BASE_URL and EMAIL and TOKEN):
    st.error("Chưa có Jira Secrets. Vào Streamlit Cloud → App settings → Secrets và cấu hình JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN.")
    st.stop()

with st.sidebar:
    st.markdown("### ⚙️ Cấu hình BSC")
    jql = st.text_area("JQL", value=DEFAULT_QUERY, height=130)
    done_text = st.text_input("Trạng thái hoàn thành", DONE_DEFAULT)
    stale_days = st.number_input("Ngưỡng cập nhật muộn (ngày)", 1, 60, 7)
    outside_text = st.text_input("Label ngoài BSC", "ngoai_bsc,outside_bsc")
    st.caption("Complexity: Very Complex=5, Complex=4, Medium=3, Simple=2, Very Simple=1")
    sync = st.button("🔄 Đồng bộ Jira", type="primary", use_container_width=True)
    clear = st.button("🧹 Xóa cache phiên", use_container_width=True)

if clear:
    for k in ["jira_df", "jira_meta", "comment_cache"]:
        st.session_state.pop(k, None)
    st.rerun()

if sync or "jira_df" not in st.session_state:
    try:
        with st.spinner("Đang đồng bộ dữ liệu từ Jira..."):
            df, meta = load_jira(BASE_URL, EMAIL, TOKEN, jql)
        st.session_state["jira_df"] = df
        st.session_state["jira_meta"] = meta
        st.session_state.setdefault("comment_cache", {})
    except JiraApiError as exc:
        st.error(str(exc))
        st.stop()

df = st.session_state["jira_df"].copy()
meta = st.session_state["jira_meta"]

if df.empty:
    st.warning("JQL không trả về Task nào.")
    st.stop()

done_statuses = {x.strip().lower() for x in done_text.split(",") if x.strip()}
outside_labels = {x.strip().lower() for x in outside_text.split(",") if x.strip()}


# ---------------------------------------------------------------------
# HERO / FILTERS
# ---------------------------------------------------------------------
st.markdown(f"""
<div class="bsc-hero">
  <div class="bsc-hero-title">BSC Executive Performance</div>
  <div class="bsc-hero-sub">Jira BANCORE · Epic 7559 · Báo cáo hiệu suất quản trị theo Tháng / Quý / Năm</div>
  <div class="bsc-live"><span class="bsc-dot"></span> Jira API · Đồng bộ {html.escape(meta.get("loaded_at",""))} · {html.escape(meta.get("user",""))}</div>
</div>
""", unsafe_allow_html=True)

f1, f2, f3, f4 = st.columns([1, 1.15, 1.5, 1.3])
with f1:
    period_kind = st.selectbox("Kỳ báo cáo", ["Tháng", "Quý", "Năm"])
with f4:
    basis = st.selectbox("Căn cứ", ["Due date trong kỳ", "Hoàn thành trong kỳ", "Created trong kỳ"])

basis_dates = df["_due"] if basis == "Due date trong kỳ" else df["_resolution"] if basis == "Hoàn thành trong kỳ" else df["_created"]
opts = period_options(basis_dates, period_kind)

with f2:
    period_label = st.selectbox("Chọn kỳ", opts)
with f3:
    staff_filter = st.selectbox("Cán bộ", ["Tất cả cán bộ"] + sorted(df["_assignee"].dropna().unique().tolist()))

start, end = period_bounds(period_kind, period_label)
as_of = min(pd.Timestamp.now(), end)
mask = period_mask(df, basis, start, end)
period_raw = df[mask].copy()

if period_raw.empty:
    st.warning("Không có dữ liệu trong kỳ đã chọn.")
    st.stop()

# comment sync only for period
with st.expander("💬 Dữ liệu Comment / tiến độ", expanded=False):
    c1, c2 = st.columns([2.5, 1])
    with c1:
        st.caption("Nếu chưa đồng bộ Comment, dashboard dùng trường Updated làm dữ liệu thay thế cho độ tươi tiến độ.")
    with c2:
        refresh_comments = st.button("Đồng bộ Comment kỳ này", use_container_width=True)

if refresh_comments:
    with st.spinner(f"Đang đọc comment mới nhất của {len(period_raw)} Jira..."):
        sync_comments(BASE_URL, EMAIL, TOKEN, period_raw["_key"].tolist(), force=True)

period_raw = apply_comments(period_raw)
period_df, metrics = calc_metrics(period_raw, as_of, done_statuses, stale_days, outside_labels)

# Previous period for comparison
pstart, pend = previous_bounds(period_kind, period_label)
prev_raw = apply_comments(df[period_mask(df, basis, pstart, pend)].copy())
_, prev_metrics = calc_metrics(prev_raw, min(pd.Timestamp.now(), pend), done_statuses, stale_days, outside_labels) if not prev_raw.empty else (prev_raw, {
    "total":0,"done":0,"ontime":0,"late":0,"overdue":0,"doing":0,"stale":0,"outside":0,"workload":0,
    "completion_rate":0,"ontime_rate":0,"score":0
})

if staff_filter != "Tất cả cán bộ":
    period_df = period_df[period_df["_assignee"] == staff_filter].copy()
    period_df, metrics = calc_metrics(period_df, as_of, done_statuses, stale_days, outside_labels)

st.markdown(
    f'<span class="bsc-period">BSC {html.escape(period_kind.upper())} {html.escape(period_label)}</span>',
    unsafe_allow_html=True
)


# ---------------------------------------------------------------------
# KPI CARDS
# ---------------------------------------------------------------------
d_total = pct_delta(metrics["total"], prev_metrics["total"])
d_work = pct_delta(metrics["workload"], prev_metrics["workload"])
d_score = metrics["score"] - prev_metrics["score"]

def delta_text(v, suffix="%"):
    if v is None:
        return "Kỳ đầu"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}{suffix}"

cards = [
    kpi_card("Tổng công việc", f'{metrics["total"]:,}', "so với kỳ trước", "c-blue", "tasks", delta_text(d_total), "up" if (d_total or 0)>=0 else "down"),
    kpi_card("Đã hoàn thành", f'{metrics["done"]:,}', f'{metrics["completion_rate"]:.1f}% tổng Task', "c-green", "done"),
    kpi_card("Đúng hạn", f'{metrics["ontime"]:,}', f'{metrics["ontime_rate"]:.1f}% số Done', "c-teal", "time"),
    kpi_card("Hoàn thành trễ", f'{metrics["late"]:,}', f'{100*metrics["late"]/metrics["total"] if metrics["total"] else 0:.1f}% tổng Task', "c-amber", "late"),
    kpi_card("Quá hạn chưa Done", f'{metrics["overdue"]:,}', f'{100*metrics["overdue"]/metrics["total"] if metrics["total"] else 0:.1f}% cần xử lý', "c-red", "over"),
    kpi_card("Đang thực hiện", f'{metrics["doing"]:,}', f'{100*metrics["doing"]/metrics["total"] if metrics["total"] else 0:.1f}% tổng Task', "c-violet", "doing"),
    kpi_card("Cập nhật muộn", f'{metrics["stale"]:,}', f'quá {stale_days} ngày', "c-gold", "comment"),
    kpi_card("Ngoài BSC", f'{metrics["outside"]:,}', "theo Labels", "c-cyan", "folder"),
    kpi_card("Workload điểm", f'{metrics["workload"]:.0f}', "theo Complexity", "c-navy", "workload", delta_text(d_work), "up" if (d_work or 0)>=0 else "down"),
    kpi_card("BSC hiệu suất", f'{metrics["score"]:.1f}%', "70% hoàn thành + 30% đúng hạn", "c-green", "star", delta_text(d_score, "đ"), "up" if d_score>=0 else "down"),
]
st.markdown('<div class="kpi-grid">' + ''.join(cards) + '</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------
# EXECUTIVE SUMMARY
# ---------------------------------------------------------------------
completion_gap = metrics["completion_rate"] - 90
ontime_gap = metrics["ontime_rate"] - 90
over_rate = 100 * metrics["overdue"] / metrics["total"] if metrics["total"] else 0
over_gap = over_rate - 5

def cls_gap(gap, inverse=False):
    ok = gap <= 0 if inverse else gap >= 0
    return "t-good" if ok else "t-bad"

st.markdown(f"""
<div class="exec-summary">
  <div class="exec-intro"><b>Executive BSC Summary</b><p>Đánh giá nhanh mức đạt mục tiêu và các điểm cần can thiệp. Dashboard luôn hiển thị đồng thời số lượng tuyệt đối và tỷ lệ.</p></div>
  <div class="exec-target"><span>Hoàn thành / mục tiêu</span><strong class="{cls_gap(completion_gap)}">{metrics["completion_rate"]:.1f}% / 90%</strong><small class="{cls_gap(completion_gap)}">{completion_gap:+.1f} điểm %</small></div>
  <div class="exec-target"><span>Đúng hạn / mục tiêu</span><strong class="{cls_gap(ontime_gap)}">{metrics["ontime_rate"]:.1f}% / 90%</strong><small class="{cls_gap(ontime_gap)}">{ontime_gap:+.1f} điểm %</small></div>
  <div class="exec-target"><span>Quá hạn / ngưỡng</span><strong class="{cls_gap(over_gap, True)}">{over_rate:.1f}% / ≤5%</strong><small class="{cls_gap(over_gap, True)}">{over_gap:+.1f} điểm %</small></div>
  <div class="exec-target"><span>BSC tổng hợp</span><strong class="t-good">{metrics["score"]:.1f}%</strong><small class="{'t-good' if d_score>=0 else 't-bad'}">{d_score:+.1f} điểm kỳ trước</small></div>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------
# STAFF + STATUS
# ---------------------------------------------------------------------
st.markdown('<div class="section-title">Hiệu suất BSC theo cán bộ</div><div class="section-sub">Số lượng, tiến độ, đúng hạn, cập nhật và workload trong cùng một bảng.</div>', unsafe_allow_html=True)

staff_df = staff_kpi(period_df)
st.dataframe(
    staff_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "BSC %": st.column_config.ProgressColumn("BSC %", min_value=0, max_value=100, format="%.1f%%"),
        "HT %": st.column_config.NumberColumn("HT %", format="%.1f%%"),
        "Đúng hạn %": st.column_config.NumberColumn("Đúng hạn %", format="%.1f%%"),
    }
)

c1, c2 = st.columns([1.4, 1])
with c1:
    result_counts = period_df["_result"].value_counts().reset_index()
    result_counts.columns = ["Kết quả", "Số lượng"]
    fig = px.pie(
        result_counts, names="Kết quả", values="Số lượng", hole=.62,
        color="Kết quả",
        color_discrete_map={
            "Hoàn thành đúng hạn":"#13966a",
            "Hoàn thành trễ":"#d98b17",
            "Quá hạn chưa xong":"#d94b57",
            "Đang thực hiện":"#177ddc",
            "Đã hoàn thành":"#7257b5",
            "Khác":"#8fa0ad",
        },
        title="Cơ cấu trạng thái công việc"
    )
    fig.update_layout(margin=dict(l=10,r=10,t=55,b=10), legend_title="", height=400)
    fig.update_traces(textinfo="label+value+percent")
    st.plotly_chart(fig, use_container_width=True)
with c2:
    overdue_staff = period_df.groupby("_assignee")["_overdue"].sum().sort_values(ascending=True)
    overdue_staff = overdue_staff[overdue_staff > 0].reset_index(name="Quá hạn")
    if overdue_staff.empty:
        st.success("Không có Task quá hạn trong kỳ.")
    else:
        fig = px.bar(
            overdue_staff, x="Quá hạn", y="_assignee", orientation="h",
            text="Quá hạn", title="Ranking quá hạn theo cán bộ",
            color="Quá hạn", color_continuous_scale=["#f6c867","#d94b57"]
        )
        fig.update_layout(margin=dict(l=10,r=10,t=55,b=10), yaxis_title="", height=400, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------
# DEEP ANALYTICS
# ---------------------------------------------------------------------
st.markdown('<div class="section-title">Phân tích phục vụ báo cáo Tháng / Quý / Năm</div><div class="section-sub">Xu hướng, aging, workload, comment và cơ cấu công việc.</div>', unsafe_allow_html=True)

tab1, tab2, tab3, tab4 = st.tabs(["📈 Xu hướng", "⚠️ Rủi ro", "🧩 Workload", "💬 Cập nhật"])

with tab1:
    year = start.year
    ydf = df[df["_due"].dt.year == year].copy()
    if not ydf.empty:
        ydf = apply_comments(ydf)
        trend_rows = []
        for month in range(1, 13):
            ms = pd.Timestamp(year=year, month=month, day=1)
            me = ms + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            md = ydf[ydf["_due"].between(ms, me)]
            if md.empty:
                trend_rows.append({"Tháng": f"T{month}", "Tổng": 0, "Hoàn thành": 0, "Đúng hạn": 0, "Quá hạn": 0})
                continue
            mg, mm = calc_metrics(md, min(pd.Timestamp.now(), me), done_statuses, stale_days, outside_labels)
            trend_rows.append({"Tháng": f"T{month}", "Tổng": mm["total"], "Hoàn thành": mm["done"], "Đúng hạn": mm["ontime"], "Quá hạn": mm["overdue"]})
        tdf = pd.DataFrame(trend_rows)
        fig = px.line(
            tdf, x="Tháng", y=["Tổng","Hoàn thành","Đúng hạn","Quá hạn"],
            markers=True, title=f"Xu hướng công việc năm {year}",
            color_discrete_map={"Tổng":"#285778","Hoàn thành":"#13966a","Đúng hạn":"#0e8fa1","Quá hạn":"#d94b57"}
        )
        fig.update_layout(height=430, legend_title="")
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    aging = pd.cut(
        period_df.loc[period_df["_overdue"], "_days_late"],
        bins=[0,3,7,14,np.inf],
        labels=["1–3 ngày","4–7 ngày","8–14 ngày",">14 ngày"],
        include_lowest=True
    ).value_counts().reindex(["1–3 ngày","4–7 ngày","8–14 ngày",">14 ngày"], fill_value=0).reset_index()
    aging.columns = ["Nhóm chậm", "Số Task"]
    a1, a2 = st.columns([1.2,1])
    with a1:
        fig = px.bar(aging, x="Nhóm chậm", y="Số Task", text="Số Task", title="Aging Task quá hạn", color="Số Task", color_continuous_scale=["#f6c867","#d94b57"])
        fig.update_layout(height=360, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)
    with a2:
        top_late = period_df[period_df["_overdue"] | period_df["_completed_late"]].sort_values("_days_late", ascending=False).head(10)
        st.dataframe(
            pd.DataFrame({
                "Issue": top_late["_key"],
                "Cán bộ": top_late["_assignee"],
                "Due": top_late["_due"].map(fmt_date),
                "Kết quả": top_late["_result"],
                "Chậm (ngày)": top_late["_days_late"].astype(int),
            }),
            hide_index=True, use_container_width=True
        )

with tab3:
    w1, w2 = st.columns(2)
    with w1:
        comp = period_df["_complexity"].value_counts().reset_index()
        comp.columns = ["Complexity","Số Task"]
        fig = px.bar(comp, x="Số Task", y="Complexity", orientation="h", text="Số Task", title="Phân bổ Complexity", color="Số Task", color_continuous_scale=["#8bc5e8","#285778"])
        fig.update_layout(height=390, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)
    with w2:
        work = period_df.groupby("_assignee")["_workload"].sum().sort_values().reset_index(name="Workload")
        fig = px.bar(work, x="Workload", y="_assignee", orientation="h", text="Workload", title="Workload điểm theo cán bộ", color="Workload", color_continuous_scale=["#9fe0d2","#13966a"])
        fig.update_layout(height=390, coloraxis_showscale=False, yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    # Components
    comps = period_df["_components"].fillna("").str.split("; ").explode()
    comps = comps[comps.str.strip() != ""].value_counts().head(12).reset_index()
    comps.columns = ["Component","Số Task"]
    if not comps.empty:
        fig = px.bar(comps, x="Component", y="Số Task", text="Số Task", title="Top Component theo số lượng Task", color="Số Task", color_continuous_scale=["#b9d8f3","#177ddc"])
        fig.update_layout(height=380, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

with tab4:
    fresh = pd.cut(
        period_df["_freshness_days"],
        bins=[-np.inf,3,7,14,np.inf],
        labels=["≤3 ngày","4–7 ngày","8–14 ngày",">14 ngày"]
    ).value_counts().reindex(["≤3 ngày","4–7 ngày","8–14 ngày",">14 ngày"], fill_value=0).reset_index()
    fresh.columns = ["Độ tươi cập nhật","Số Task"]
    f1, f2 = st.columns([1.2,1])
    with f1:
        fig = px.bar(fresh, x="Độ tươi cập nhật", y="Số Task", text="Số Task", title="Độ tươi Comment / Updated", color="Độ tươi cập nhật",
                     color_discrete_map={"≤3 ngày":"#13966a","4–7 ngày":"#0e8fa1","8–14 ngày":"#d98b17",">14 ngày":"#d94b57"})
        fig.update_layout(height=360, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    with f2:
        stale_staff = period_df.groupby("_assignee")["_late_update"].sum().sort_values(ascending=False).reset_index(name="Update muộn")
        stale_staff = stale_staff[stale_staff["Update muộn"] > 0]
        if stale_staff.empty:
            st.success("Không có cán bộ nào có Task cập nhật muộn.")
        else:
            st.dataframe(stale_staff, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------
# MANAGEMENT ATTENTION
# ---------------------------------------------------------------------
st.markdown('<div class="section-title">Management Attention</div><div class="section-sub">Tự động nêu các điểm cần đưa vào báo cáo quản trị.</div>', unsafe_allow_html=True)
a1, a2, a3 = st.columns(3)
with a1:
    st.markdown(f'<div class="attention red"><b>⚠ {metrics["overdue"]} Task đang quá hạn</b><p>{int((period_df["_days_late"]>7).sum())} Task chậm trên 7 ngày. Cần xác định nguyên nhân và kế hoạch xử lý.</p></div>', unsafe_allow_html=True)
with a2:
    st.markdown(f'<div class="attention amber"><b>💬 {metrics["stale"]} Task cập nhật muộn</b><p>Các Task chưa có Comment/Updated trong hơn {stale_days} ngày cần được cập nhật tiến độ.</p></div>', unsafe_allow_html=True)
with a3:
    st.markdown(f'<div class="attention blue"><b>▥ Workload {metrics["workload"]:.0f} điểm</b><p>Đánh giá đồng thời số Task và Complexity để tránh thiên lệch do chỉ đếm số lượng.</p></div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------
# DETAIL DRILL-DOWN
# ---------------------------------------------------------------------
st.markdown('<div class="section-title">Drill-down Jira chi tiết</div><div class="section-sub">Lọc trực tiếp từng nhóm vấn đề để đưa vào báo cáo hoặc mở lại Jira.</div>', unsafe_allow_html=True)

d1, d2, d3 = st.columns([1.3,1.3,2])
with d1:
    detail_staff = st.selectbox("Cán bộ chi tiết", ["Tất cả"] + sorted(period_df["_assignee"].unique().tolist()), key="detail_staff")
with d2:
    detail_kind = st.selectbox("Nhóm công việc", ["Tất cả","Hoàn thành đúng hạn","Hoàn thành trễ","Quá hạn chưa xong","Đang thực hiện","Cập nhật muộn","Ngoài BSC"])
with d3:
    q = st.text_input("Tìm Issue / Summary / Component / Label")

detail = period_df.copy()
if detail_staff != "Tất cả":
    detail = detail[detail["_assignee"] == detail_staff]
if detail_kind == "Cập nhật muộn":
    detail = detail[detail["_late_update"]]
elif detail_kind == "Ngoài BSC":
    detail = detail[detail["_outside_bsc"]]
elif detail_kind != "Tất cả":
    detail = detail[detail["_result"] == detail_kind]
if q.strip():
    pat = re.escape(q.strip())
    detail = detail[
        detail["_key"].str.contains(pat, case=False, na=False)
        | detail["_summary"].str.contains(pat, case=False, na=False)
        | detail["_components"].str.contains(pat, case=False, na=False)
        | detail["_labels"].str.contains(pat, case=False, na=False)
    ]

out = pd.DataFrame({
    "Issue": detail["_key"],
    "Summary": detail["_summary"],
    "Cán bộ": detail["_assignee"],
    "Complexity": detail["_complexity"],
    "Component": detail["_components"],
    "Labels": detail["_labels"],
    "Due date": detail["_due"].map(fmt_date),
    "Resolution": detail["_resolution"].map(fmt_date),
    "Status": detail["_status"],
    "Kết quả": detail["_result"],
    "Chậm (ngày)": detail["_days_late"].fillna(0).astype(int),
    "Comment gần nhất": detail["_last_comment"].map(fmt_date),
    "Người comment": detail["_last_comment_author"],
    "Nội dung cập nhật": detail["_last_comment_text"].str.slice(0, 250),
    "Mở Jira": BASE_URL.rstrip("/") + "/browse/" + detail["_key"],
})

st.dataframe(
    out,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Mở Jira": st.column_config.LinkColumn("Mở Jira", display_text="Mở"),
        "Nội dung cập nhật": st.column_config.TextColumn(width="large"),
    }
)

st.download_button(
    "⬇️ Tải dữ liệu chi tiết đang lọc",
    out.to_csv(index=False).encode("utf-8-sig"),
    file_name=f"BSC_Jira_{period_kind}_{period_label}.csv",
    mime="text/csv"
)

st.caption(f"Jira BSC Executive Dashboard V{APP_VERSION} · Không có chức năng Upload CSV · Dữ liệu lấy trực tiếp từ Jira API.")
