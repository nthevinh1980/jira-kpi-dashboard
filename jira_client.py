from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Iterable
import re
import unicodedata

import requests
from requests.auth import HTTPBasicAuth


class JiraApiError(RuntimeError):
    pass


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def adf_to_text(value: Any) -> str:
    """Extract readable text from Atlassian Document Format (ADF)."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text" and node.get("text"):
                parts.append(str(node["text"]))
            for child in node.get("content", []) or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, str):
            parts.append(node)

    walk(value)
    return " ".join(x.strip() for x in parts if x and x.strip()).strip()


@dataclass
class JiraConnectionInfo:
    display_name: str
    email: str
    account_id: str
    site_url: str


class JiraClient:
    """Small Jira Cloud REST client used by the Streamlit dashboard.

    Credentials are held only in the Python process. They are never embedded in
    client-side JavaScript or written to a file by this class.
    """

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        timeout: int = 30,
        verify_ssl: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email.strip()
        self.api_token = api_token.strip()
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(self.email, self.api_token)
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Jira-KPI-Dashboard/4.0-no-admin",
        })

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        try:
            response = self.session.request(
                method,
                url,
                timeout=self.timeout,
                verify=self.verify_ssl,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise JiraApiError(f"Không kết nối được Jira: {exc}") from exc

        if response.status_code >= 400:
            msg = response.text[:1500]
            try:
                data = response.json()
                errors = data.get("errorMessages") or data.get("errors") or data.get("message")
                if errors:
                    msg = str(errors)
            except Exception:
                pass
            raise JiraApiError(
                f"Jira trả về HTTP {response.status_code} tại {path}: {msg}"
            )
        return response

    def test_connection(self) -> JiraConnectionInfo:
        data = self._request("GET", "/rest/api/3/myself").json()
        return JiraConnectionInfo(
            display_name=str(data.get("displayName") or ""),
            email=str(data.get("emailAddress") or self.email),
            account_id=str(data.get("accountId") or ""),
            site_url=self.base_url,
        )


    def get_my_permissions(self, project_key: str) -> dict[str, Any]:
        params = {"permissions": "BROWSE_PROJECTS", "projectKey": project_key}
        data = self._request("GET", "/rest/api/3/mypermissions", params=params).json()
        return data if isinstance(data, dict) else {}

    def diagnose_access(self, jql: str, *, project_key: str = "BANCORE") -> dict[str, Any]:
        """Read-only diagnostics that do not require Jira administrator permission."""
        info = self.test_connection()

        browse_project = None
        try:
            perms = self.get_my_permissions(project_key)
            browse = (perms.get("permissions") or {}).get("BROWSE_PROJECTS") or {}
            browse_project = bool(browse.get("havePermission"))
        except JiraApiError:
            browse_project = None

        fields_count = 0
        try:
            fields_count = len(self.get_fields())
        except JiraApiError:
            fields_count = 0

        sample_issue = ""
        sample_count = 0
        comment_test = "no_issue"
        comment_error = ""
        try:
            issues = self.search_issues(jql, ["summary"], page_size=1, max_issues=1)
            sample_count = len(issues)
            if issues:
                sample_issue = str(issues[0].get("key") or "")
                try:
                    self.latest_comment(sample_issue)
                    comment_test = "ok"
                except JiraApiError as exc:
                    comment_test = "error"
                    comment_error = str(exc)
        except JiraApiError as exc:
            raise JiraApiError(f"Đăng nhập được nhưng JQL/API tìm issue không chạy: {exc}") from exc

        return {
            "account": info,
            "browse_project": browse_project,
            "fields_count": fields_count,
            "sample_count": sample_count,
            "sample_issue": sample_issue,
            "comment_test": comment_test,
            "comment_error": comment_error,
        }

    def get_fields(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/rest/api/3/field").json()
        return data if isinstance(data, list) else []

    @staticmethod
    def resolve_field_id(
        catalog: Iterable[dict[str, Any]], names: Iterable[str]
    ) -> str | None:
        rows = list(catalog)
        candidate_norms = [_norm(x) for x in names]
        for target in candidate_norms:
            for row in rows:
                if _norm(row.get("name", "")) == target:
                    return str(row.get("id"))
        for target in candidate_norms:
            if not target:
                continue
            for row in rows:
                n = _norm(row.get("name", ""))
                if target in n or n in target:
                    return str(row.get("id"))
        return None

    def search_issues(
        self,
        jql: str,
        fields: list[str],
        *,
        page_size: int = 100,
        max_issues: int = 10000,
    ) -> list[dict[str, Any]]:
        """Search all issues with enhanced JQL API; fallback to legacy search if needed."""
        page_size = max(1, min(int(page_size), 100))
        max_issues = max(1, int(max_issues))
        issues: list[dict[str, Any]] = []
        token: str | None = None

        try:
            while True:
                body: dict[str, Any] = {
                    "jql": jql,
                    "fields": fields,
                    "maxResults": page_size,
                    "fieldsByKeys": False,
                }
                if token:
                    body["nextPageToken"] = token
                data = self._request("POST", "/rest/api/3/search/jql", json=body).json()
                batch = data.get("issues") or []
                issues.extend(batch)
                if len(issues) >= max_issues:
                    return issues[:max_issues]
                if data.get("isLast") is True:
                    break
                token = data.get("nextPageToken")
                if not token or not batch:
                    break
            return issues
        except JiraApiError as exc:
            # Some tenants may not yet accept the enhanced endpoint/body shape.
            # Fall back to the older search endpoint while it remains available.
            if "HTTP 404" not in str(exc) and "HTTP 405" not in str(exc):
                raise

        issues = []
        start_at = 0
        while True:
            body = {
                "jql": jql,
                "fields": fields,
                "maxResults": page_size,
                "startAt": start_at,
            }
            data = self._request("POST", "/rest/api/3/search", json=body).json()
            batch = data.get("issues") or []
            issues.extend(batch)
            if len(issues) >= max_issues:
                return issues[:max_issues]
            total = int(data.get("total") or len(issues))
            if not batch or len(issues) >= total:
                break
            start_at += len(batch)
        return issues

    def latest_comment(self, issue_key: str) -> dict[str, Any] | None:
        params = {"maxResults": 1, "orderBy": "-created"}
        data = self._request(
            "GET", f"/rest/api/3/issue/{issue_key}/comment", params=params
        ).json()
        comments = data.get("comments") or []
        if not comments:
            return None
        c = comments[0]
        return {
            "created": c.get("created"),
            "updated": c.get("updated"),
            "author": (c.get("author") or {}).get("displayName", ""),
            "body": adf_to_text(c.get("body")),
            "id": c.get("id"),
        }

    def latest_comments_bulk(
        self,
        issue_keys: Iterable[str],
        *,
        workers: int = 8,
    ) -> dict[str, dict[str, Any] | None]:
        keys = [str(x) for x in issue_keys if str(x).strip()]
        result: dict[str, dict[str, Any] | None] = {}
        if not keys:
            return result
        workers = max(1, min(int(workers), 12))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self.latest_comment, key): key for key in keys}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    result[key] = future.result()
                except Exception as exc:
                    result[key] = {"error": str(exc)}
        return result
