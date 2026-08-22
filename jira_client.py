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
            "User-Agent": "Jira-BSC-Executive/6.0",
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
        keys = [str(x).strip() for x in issue_keys if str(x).strip()]
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
