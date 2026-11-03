"""Minimal RCS HTTP client: login, task dispatch, status query."""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


def dig(obj: Any, path: str) -> Any:
    """Get nested value by dotted path; supports list index like taskStepList.0."""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
            continue
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
            continue
        return None
    return cur


class RcsClient:
    def __init__(self, rcs_cfg: dict[str, Any]):
        self.base_url = str(rcs_cfg["base_url"]).rstrip("/")
        self.username = str(rcs_cfg["username"])
        self.password = str(rcs_cfg["password"])
        self.timeout = float(rcs_cfg.get("timeout_sec") or 30)
        self.login_path = str(rcs_cfg.get("login_path") or "/login")
        self.token_json_path = str(rcs_cfg.get("token_json_path") or "token")
        self._token: str | None = None
        self._session = requests.Session()
        # Avoid system HTTP(S)_PROXY hijacking intranet RCS hosts (e.g. 127.0.0.1:7890).
        self._session.trust_env = False

    def login(self, force: bool = False) -> str:
        if self._token and not force:
            return self._token
        url = f"{self.base_url}{self.login_path}"
        payload = {"username": self.username, "password": self.password}
        logger.info("POST %s (login)", url)
        resp = self._session.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        token = dig(data, self.token_json_path)
        if not token:
            token = data.get("token") or dig(data, "data.token")
        if not token:
            raise RuntimeError(f"login ok but token missing: {data}")
        self._token = str(token)
        return self._token

    def _headers(self) -> dict[str, str]:
        token = self.login()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _request(
        self, method: str, path: str, body: Any = None, retry_on_401: bool = True
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        logger.info("%s %s", method.upper(), url)
        kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "timeout": self.timeout,
        }
        if body is not None:
            kwargs["json"] = body
        resp = self._session.request(method.upper(), url, **kwargs)
        if resp.status_code == 401 and retry_on_401:
            logger.warning("401 received, re-login and retry once")
            self.login(force=True)
            kwargs["headers"] = self._headers()
            resp = self._session.request(method.upper(), url, **kwargs)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def post_json(self, path: str, body: Any, retry_on_401: bool = True) -> dict[str, Any]:
        return self._request("POST", path, body=body, retry_on_401=retry_on_401)

    def get_json(self, path: str, retry_on_401: bool = True) -> dict[str, Any]:
        return self._request("GET", path, retry_on_401=retry_on_401)

    def dispatch_task(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.post_json(path, payload)
        code = result.get("code")
        if code is not None and code != 200:
            raise RuntimeError(f"RCS business error: {result}")
        return result

    def get_robot_status(self, path_template: str, robot_id: Any) -> dict[str, Any]:
        path = str(path_template).replace("{robot_id}", str(robot_id))
        return self.get_json(path)

    def get_task(self, path_template: str, task_id: Any) -> dict[str, Any]:
        path = str(path_template).replace("{task_id}", str(task_id))
        return self.get_json(path)

    def list_robots(self, path: str | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET robot list. RuoYi usually returns {code, rows, total} or {code, data:[...]}."""
        api = path or "/system/robot/list"
        # pageSize large enough to get all for a quick inventory
        query = {"pageNum": 1, "pageSize": 200}
        if params:
            query.update(params)
        url = f"{self.base_url}{api}"
        logger.info("GET %s params=%s", url, query)
        resp = self._session.get(url, headers=self._headers(), params=query, timeout=self.timeout)
        if resp.status_code == 401:
            self.login(force=True)
            resp = self._session.get(url, headers=self._headers(), params=query, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        code = data.get("code")
        if code is not None and code != 200:
            raise RuntimeError(f"list robots failed: {data}")
        return data


def extract_robot_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if isinstance(rows, list):
        return rows
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        return data["rows"]
    return []


def summarize_robots(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for row in rows:
        online = row.get("online")
        if online is None:
            # some APIs nest status
            online = dig(row, "status.online")
        state = row.get("state")
        if isinstance(state, dict):
            state = state.get("message") or state.get("name") or state.get("value")
        summary.append(
            {
                "robotId": row.get("robotId") or row.get("id"),
                "robotName": row.get("robotName") or row.get("name"),
                "robotUuid": row.get("robotUuid") or row.get("uuid"),
                "online": online,
                "state": state,
            }
        )
    return summary
