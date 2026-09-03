

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


def _normalized_marker_text(value: Any) -> str:
    """Normalize Vietnamese text so markers such as 'cập nhật muộn' match reliably."""
    import unicodedata
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.lower()).strip()


@dataclass
class JiraConnectionInfo:
    display_name: str
    email: str
    account_id: str
    site_url: str


class JiraClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        timeout: int = 45,
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
            "User-Agent": "Jira-BSC-Executive/9.1",
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
            raise JiraApiError(f"Jira HTTP {response.status_code}: {msg}")
        return response

    def test_connection(self) -> JiraConnectionInfo:
        data = self._request("GET", "/rest/api/3/myself").json()
        return JiraConnectionInfo(
            display_name=str(data.get("displayName") or ""),
            email=str(data.get("emailAddress") or self.email),
            account_id=str(data.get("accountId") or ""),
            site_url=self.base_url,
        )

    def get_fields(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/rest/api/3/field").json()
        return data if isinstance(data, list) else []

    @staticmethod
    def resolve_field_id(
        catalog: Iterable[dict[str, Any]], names: Iterable[str]
    ) -> str | None:
        rows = list(catalog)
        targets = [_norm(x) for x in names]
        for target in targets:
            for row in rows:
                if _norm(row.get("name", "")) == target:
                    return str(row.get("id"))
        for target in targets:
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
        page_size = max(1, min(int(page_size), 100))
        issues: list[dict[str, Any]] = []
        token: str | None = None

        # Enhanced search API
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
            if "HTTP 404" not in str(exc) and "HTTP 405" not in str(exc):
                raise

        # Legacy fallback
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

    def get_all_comments(self, issue_key: str) -> list[dict[str, Any]]:
        """Return all comments of an issue, paginated, oldest -> newest."""
        comments: list[dict[str, Any]] = []
        start_at = 0
        page_size = 100
        while True:
            params = {
                "startAt": start_at,
                "maxResults": page_size,
                "orderBy": "created",
            }
            data = self._request(
                "GET", f"/rest/api/3/issue/{issue_key}/comment", params=params
            ).json()
            batch = data.get("comments") or []
            comments.extend(batch)
            total = int(data.get("total") or len(comments))
            if not batch or len(comments) >= total:
                break
            start_at += len(batch)
        return comments

    def comment_audit(
        self,
        issue_key: str,
        *,
        late_update_marker: str = "cập nhật muộn",
        reviewer_account_id: str = "",
    ) -> dict[str, Any]:
        """Summarize comment history for BSC.

        A task is marked as late-update when ANY comment contains the configured
        marker. This is the team-lead's explicit audit flag, so we do not infer
        late-update from Jira Updated timestamp or from a >7-day heuristic.
        """
        raw_comments = self.get_all_comments(issue_key)
        marker = _normalized_marker_text(late_update_marker)

        latest: dict[str, Any] | None = None
        late_marker_comment: dict[str, Any] | None = None
        parsed: list[dict[str, Any]] = []

        for c in raw_comments:
            item = {
                "created": c.get("created"),
                "updated": c.get("updated"),
                "author": (c.get("author") or {}).get("displayName", ""),
                "authorAccountId": (c.get("author") or {}).get("accountId", ""),
                "body": adf_to_text(c.get("body")),
                "id": c.get("id"),
            }
            parsed.append(item)
            latest = item
            marker_match = marker and marker in _normalized_marker_text(item["body"])
            reviewer_match = (not reviewer_account_id) or item["authorAccountId"] == reviewer_account_id
            if marker_match and reviewer_match:
                late_marker_comment = item

        return {
            "count": len(parsed),
            "latest": latest,
            "lateUpdate": late_marker_comment is not None,
            "lateUpdateComment": late_marker_comment,
        }

    def comments_audit_bulk(
        self,
        issue_keys: Iterable[str],
        *,
        late_update_marker: str = "cập nhật muộn",
        reviewer_account_id: str = "",
        workers: int = 8,
    ) -> dict[str, dict[str, Any]]:
        keys = [str(x).strip() for x in issue_keys if str(x).strip()]
        result: dict[str, dict[str, Any]] = {}
        if not keys:
            return result

        workers = max(1, min(int(workers), 12))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self.comment_audit,
                    key,
                    late_update_marker=late_update_marker,
                    reviewer_account_id=reviewer_account_id,
                ): key
                for key in keys
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    result[key] = future.result()
                except Exception as exc:
                    result[key] = {"error": str(exc)}
        return result

    # Backward-compatible helpers kept for any other code that still calls them.
    def latest_comment(self, issue_key: str) -> dict[str, Any] | None:
        audit = self.comment_audit(issue_key)
        return audit.get("latest")

    def latest_comments_bulk(
        self,
        issue_keys: Iterable[str],
        *,
        workers: int = 8,
    ) -> dict[str, dict[str, Any] | None]:
        audits = self.comments_audit_bulk(issue_keys, workers=workers)
        result: dict[str, dict[str, Any] | None] = {}
        for key, audit in audits.items():
            if audit.get("error"):
                result[key] = {"error": audit["error"]}
            else:
                result[key] = audit.get("latest")
        return result
