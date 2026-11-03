"""Config-driven handshake: CALL -> go -> wait arrive -> notify -> DONE -> leave -> idle."""

from __future__ import annotations

import logging
import socket
import threading
import time
from enum import Enum, auto
from typing import Any, Callable

from config_loader import resolve_robot_id, task_path_from_config
from rcs_client import RcsClient, dig
from route_resolve import (
    RouteResolveError,
    enrich_call,
    enrich_leave,
    has_trip_fields,
)
from tcp_parser import ParseError, render_template

logger = logging.getLogger("rcs_bridge")

ReplyFn = Callable[[bytes], None]


class Phase(Enum):
    IDLE = auto()
    GOING = auto()
    WAIT_DONE = auto()
    LEAVING = auto()


class HandshakeError(RuntimeError):
    pass


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm_state(value: Any) -> str:
    if value is None:
        return ""
    # enum may come as int or "IDLE" or {"value":1,"message":"IDLE"}
    if isinstance(value, dict):
        return _as_str(value.get("message") or value.get("name") or value.get("value")).upper()
    return _as_str(value).upper()


def _point_matches(actual: Any, expected: Any) -> bool:
    if expected is None or expected == "":
        return True
    if actual is None:
        return False
    if isinstance(actual, dict):
        for key in ("pointId", "point_id", "id"):
            if key in actual:
                return _as_str(actual.get(key)) == _as_str(expected)
        return False
    return _as_str(actual) == _as_str(expected)


class HandshakeSession:
    def __init__(
        self,
        cfg: dict[str, Any],
        client: RcsClient | None,
        dry_run: bool,
        reply: ReplyFn | None = None,
    ):
        self.cfg = cfg
        self.workflow = cfg.get("workflow") or {}
        self.poll = cfg.get("poll") or {}
        self.notify = cfg.get("notify") or {}
        self.task_go = cfg.get("task") or {}
        self.task_leave = cfg.get("task_leave") or {}
        self.client = client
        self.dry_run = dry_run
        self.reply = reply
        self.phase = Phase.IDLE
        self.context: dict[str, Any] = {}
        self.go_task_id: Any = None
        self.leave_task_id: Any = None
        self._lock = threading.Lock()

    def set_reply(self, reply: ReplyFn | None) -> None:
        self.reply = reply

    def handle_fields(self, fields: dict[str, Any]) -> None:
        with self._lock:
            # Second signal format is TBD: while waiting, ANY message means leave.
            # No need for cmd=DONE (device may send {}, OK, empty line, etc.).
            if self.phase == Phase.WAIT_DONE:
                logger.info("second signal received (format TBD), start leave: %s", fields)
                self._on_done(fields)
                return

            signal_field = str(self.workflow.get("signal_field") or "cmd")
            signal = _as_str(fields.get(signal_field)).upper()
            call_val = _as_str(self.workflow.get("signal_call") or "CALL").upper()

            # First signal: trip fields (robot+start+end), optional cmd=CALL
            if self.phase == Phase.IDLE:
                if has_trip_fields(fields, self.workflow) or signal == call_val:
                    self._on_call(fields)
                    return
                raise HandshakeError(
                    f"first signal needs robot/start_point_id/end_point_id, got {fields}"
                )

            raise HandshakeError(
                f"ignore message in phase {self.phase.name}: {fields}"
            )

    def _resolve_robot_id(self, fields: dict[str, Any]) -> Any:
        """Resolve RCS robotId: fixed config → message code/name → numeric id."""
        rid = self.workflow.get("robot_id")
        if rid not in (None, ""):
            return rid

        code_field = self.workflow.get("robot_code_field") or "robot"
        id_field = self.workflow.get("robot_id_field") or "robot_id"
        token = None
        if code_field in fields and fields.get(code_field) not in (None, ""):
            token = fields.get(code_field)
        elif id_field in fields and fields.get(id_field) not in (None, ""):
            token = fields.get(id_field)
        else:
            raise HandshakeError(
                f"robot not found in message fields "
                f"{code_field!r}/{id_field!r} and workflow.robot_id unset"
            )

        resolved = resolve_robot_id(self.cfg, token)
        if resolved is None:
            raise HandshakeError(
                f"cannot map robot token {token!r} via [robots] aliases "
                f"(expect short code like 01, name like AGV01, or numeric robotId)"
            )
        logger.info("robot token %r → robotId %s", token, resolved)
        return resolved

    def _resolve_point_id(self, fields: dict[str, Any]) -> Any:
        pid = self.workflow.get("point_id")
        if pid not in (None, ""):
            return pid
        field = self.workflow.get("point_id_field") or "point_id"
        if field in fields:
            return fields[field]
        return None

    def _dispatch(self, task_cfg: dict[str, Any], fields: dict[str, Any], label: str) -> Any:
        if not task_cfg.get("payload_template"):
            raise HandshakeError(f"{label}: payload_template missing")
        path = task_path_from_config(task_cfg)
        payload = render_template(task_cfg["payload_template"], fields)
        logger.info("%s path=%s payload=%s", label, path, payload)
        if self.dry_run:
            logger.info("dry_run=true, skip %s HTTP", label)
            return f"dryrun-{label}"
        assert self.client is not None
        result = self.client.dispatch_task(path, payload)
        logger.info("%s response: %s", label, result)
        id_path = str(task_cfg.get("task_id_json_path") or "data.taskId")
        task_id = dig(result, id_path)
        if task_id is None:
            task_id = dig(result, "data.taskId") or dig(result, "data.task.taskId")
        return task_id

    def _on_call(self, fields: dict[str, Any]) -> None:
        if self.phase not in (Phase.IDLE,):
            raise HandshakeError(f"CALL ignored in phase {self.phase.name}")
        try:
            enriched = enrich_call(self.cfg, fields)
        except RouteResolveError as exc:
            raise HandshakeError(str(exc)) from exc
        logger.info(
            "CALL resolved: robot=%s robot_id=%s home=%s start=%s end=%s "
            "template_id=%s task_name=%s map_id=%s",
            enriched.get("robot"),
            enriched.get("robot_id"),
            enriched.get("home_point_id"),
            enriched.get("start_point_id"),
            enriched.get("end_point_id"),
            enriched.get("template_id"),
            enriched.get("task_name"),
            enriched.get("map_id"),
        )
        self.context = dict(enriched)
        robot_id = enriched["robot_id"]
        # arrive poll target = station start_point_id
        point_id = enriched.get("point_id")
        self.context["_robot_id"] = robot_id
        self.context["_robot_code"] = enriched.get("robot")
        self.context["_point_id"] = point_id
        self.go_task_id = self._dispatch(self.task_go, enriched, "task.go")
        self.phase = Phase.GOING
        self._wait_arrived(robot_id, point_id, self.go_task_id)
        self._send_notify(enriched)
        self.phase = Phase.WAIT_DONE
        logger.info("arrived & notified device; waiting DONE")

    def _on_done(self, fields: dict[str, Any]) -> None:
        if self.phase != Phase.WAIT_DONE:
            raise HandshakeError(f"DONE ignored in phase {self.phase.name}")
        # second signal may be empty; trip data comes from first TCP context
        merged = {**self.context, **fields}
        try:
            enriched = enrich_leave(self.cfg, merged)
        except RouteResolveError as exc:
            raise HandshakeError(str(exc)) from exc
        logger.info(
            "DONE resolved: robot=%s start=%s end=%s template_id=%s task_name=%s",
            enriched.get("robot"),
            enriched.get("start_point_id"),
            enriched.get("end_point_id"),
            enriched.get("template_id"),
            enriched.get("task_name"),
        )
        robot_id = enriched.get("_robot_id") or enriched.get("robot_id")
        if robot_id in (None, ""):
            robot_id = self._resolve_robot_id(enriched)
        enriched["robot_id"] = robot_id
        leave_cfg = self.task_leave if self.task_leave.get("payload_template") else self.task_go
        self.leave_task_id = self._dispatch(leave_cfg, enriched, "task.leave")
        self.phase = Phase.LEAVING
        if bool(self.poll.get("wait_idle_after_leave", True)):
            self._wait_idle(robot_id, self.leave_task_id)
        self.phase = Phase.IDLE
        self.context = {}
        logger.info("leave finished; session idle")

    def _wait_arrived(self, robot_id: Any, point_id: Any, task_id: Any) -> None:
        accept = [s.upper() for s in (self.poll.get("accept_states") or ["IDLE", "MOVED", "ACTED"])]
        require_task = bool(self.poll.get("require_task_finished", False))
        logger.info(
            "wait arrive robot=%s point=%s accept_states=%s require_task_finished=%s",
            robot_id,
            point_id,
            accept,
            require_task,
        )
        if self.dry_run:
            logger.info("dry_run=true, skip arrive polling")
            return

        def ok(status_json: dict[str, Any], task_json: dict[str, Any] | None) -> bool:
            state = _norm_state(dig(status_json, str(self.poll.get("status_state_path") or "data.state")))
            cur_point = dig(status_json, str(self.poll.get("status_point_path") or "data.currentPoint.pointId"))
            point_ok = _point_matches(cur_point, point_id)
            state_ok = (not accept) or (state in accept)
            task_ok = True
            if require_task:
                if task_json is None:
                    task_ok = False
                else:
                    tstatus = _norm_state(
                        dig(task_json, str(self.poll.get("task_status_path") or "data.status"))
                    )
                    task_ok = tstatus in ("FINISHED", "3")  # 3 may be ordinal; prefer name
            logger.debug(
                "poll arrive state=%s point=%s point_ok=%s state_ok=%s task_ok=%s",
                state,
                cur_point,
                point_ok,
                state_ok,
                task_ok,
            )
            return point_ok and state_ok and task_ok

        self._poll_until(robot_id, task_id, ok, "arrive")

    def _wait_idle(self, robot_id: Any, task_id: Any) -> None:
        idle_states = [s.upper() for s in (self.poll.get("idle_states") or ["IDLE"])]
        logger.info("wait idle robot=%s states=%s", robot_id, idle_states)
        if self.dry_run:
            logger.info("dry_run=true, skip idle polling")
            return

        def ok(status_json: dict[str, Any], task_json: dict[str, Any] | None) -> bool:
            state = _norm_state(dig(status_json, str(self.poll.get("status_state_path") or "data.state")))
            return state in idle_states

        self._poll_until(robot_id, task_id, ok, "idle")

    def _poll_until(
        self,
        robot_id: Any,
        task_id: Any,
        predicate: Callable[[dict[str, Any], dict[str, Any] | None], bool],
        label: str,
    ) -> None:
        assert self.client is not None
        interval = float(self.poll.get("interval_sec") or 1.0)
        timeout = float(self.poll.get("timeout_sec") or 300)
        status_path = str(self.poll.get("robot_status_path") or "/system/robot/status/{robot_id}")
        task_path = str(self.poll.get("task_info_path") or "/system/task/{task_id}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            status_json = self.client.get_robot_status(status_path, robot_id)
            task_json = None
            if task_id not in (None, ""):
                try:
                    task_json = self.client.get_task(task_path, task_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("get task failed: %s", exc)
            if predicate(status_json, task_json):
                logger.info("poll %s satisfied", label)
                return
            time.sleep(interval)
        raise HandshakeError(f"timeout waiting for {label} (>{timeout}s)")

    def _send_notify(self, fields: dict[str, Any]) -> None:
        message = self.notify.get("message")
        if message is None or message == "":
            logger.warning("notify.message empty, skip device notify")
            return
        text = render_template(str(message), {**self.context, **fields})
        if not isinstance(text, str):
            text = str(text)
        encoding = str(self.notify.get("encoding") or "utf-8")
        data = text.encode(encoding)
        mode = str(self.notify.get("mode") or "same_connection").lower()
        logger.info("notify device mode=%s message=%r", mode, text)
        if self.dry_run:
            logger.info("dry_run=true, skip real notify send")
            return
        if mode == "same_connection":
            if not self.reply:
                raise HandshakeError("notify.mode=same_connection but no TCP reply channel")
            self.reply(data)
            return
        if mode == "tcp_client":
            host = str(self.notify.get("host") or "")
            port = int(self.notify.get("port") or 0)
            if not host or port <= 0:
                raise HandshakeError("notify.tcp_client requires host and port")
            with socket.create_connection((host, port), timeout=10) as sock:
                sock.sendall(data)
            return
        raise HandshakeError(f"unknown notify.mode: {mode}")


class SessionHub:
    """Manage handshake sessions by connection id or business key."""

    def __init__(self, cfg: dict[str, Any], client: RcsClient | None, dry_run: bool):
        self.cfg = cfg
        self.client = client
        self.dry_run = dry_run
        self.workflow = cfg.get("workflow") or {}
        self._sessions: dict[str, HandshakeSession] = {}
        self._lock = threading.Lock()

    def _key(self, conn_id: str, fields: dict[str, Any]) -> str:
        field = self.workflow.get("session_key_field")
        if field and fields.get(field) not in (None, ""):
            return f"biz:{fields.get(field)}"
        return f"conn:{conn_id}"

    def get(self, conn_id: str, fields: dict[str, Any], reply: ReplyFn | None) -> HandshakeSession:
        key = self._key(conn_id, fields)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is None:
                sess = HandshakeSession(self.cfg, self.client, self.dry_run, reply)
                self._sessions[key] = sess
            else:
                sess.set_reply(reply)
            return sess
