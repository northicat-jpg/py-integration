"""Resolve TCP (robot, start_point, end_point) into HTTP payload fields via config maps."""

from __future__ import annotations

from typing import Any

from config_loader import resolve_robot_entry


class RouteResolveError(ValueError):
    pass


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _point_token(value: Any) -> str:
    text = _as_str(value)
    if text == "":
        raise RouteResolveError("point id is empty")
    return text


def tcp_field(fields: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in fields and fields.get(name) not in (None, ""):
            return fields[name]
    return None


def has_trip_fields(fields: dict[str, Any], workflow: dict[str, Any]) -> bool:
    robot_f = str(workflow.get("robot_code_field") or "robot")
    start_f = str(workflow.get("start_point_field") or "start_point_id")
    end_f = str(workflow.get("end_point_field") or "end_point_id")
    return (
        fields.get(robot_f) not in (None, "")
        and fields.get(start_f) not in (None, "")
        and fields.get(end_f) not in (None, "")
    )


def lookup_route(
    routes: dict[str, dict[str, Any]],
    key: str,
    *,
    section: str,
) -> dict[str, Any]:
    hit = routes.get(key) or routes.get(key.lower())
    if not hit:
        raise RouteResolveError(
            f"[{section}] missing route for key {key!r}; "
            f"known={sorted(routes.keys())}"
        )
    return hit


def enrich_call(cfg: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """First signal: robot + start + end → CALL payload fields.

    CALL path semantics:
      home_point(robot) → start_point_id (TCP)
    """
    workflow = cfg.get("workflow") or {}
    robot_f = str(workflow.get("robot_code_field") or "robot")
    start_f = str(workflow.get("start_point_field") or "start_point_id")
    end_f = str(workflow.get("end_point_field") or "end_point_id")

    robot_token = tcp_field(fields, robot_f, "robot")
    start_point = tcp_field(fields, start_f, "start_point", "point_id")
    end_point = tcp_field(fields, end_f, "end_point")

    if robot_token in (None, ""):
        raise RouteResolveError(f"TCP missing robot field {robot_f!r}")
    if start_point in (None, ""):
        raise RouteResolveError(f"TCP missing start point field {start_f!r}")
    if end_point in (None, ""):
        raise RouteResolveError(f"TCP missing end point field {end_f!r}")

    entry = resolve_robot_entry(cfg, robot_token)
    if not entry:
        raise RouteResolveError(f"unknown robot token {robot_token!r} in [robots]")

    code = str(entry["code"])
    start_key = _point_token(start_point)
    end_key = _point_token(end_point)
    route_key = f"{code}:{start_key}"
    route = lookup_route(cfg.get("_call_routes") or {}, route_key, section="call_routes")

    home = entry.get("home_point_id")
    if home in (None, ""):
        raise RouteResolveError(
            f"[robots] {code!r} missing home_point_id "
            f"(CALL start point = robot idle/home)"
        )

    out = dict(fields)
    out.update(
        {
            "cmd": "CALL",
            "robot": code,
            "robot_id": int(entry["robot_id"]),
            "map_id": entry.get("map_id"),
            "home_point_id": home,
            "start_point_id": int(start_key) if start_key.isdigit() else start_point,
            "end_point_id": int(end_key) if end_key.isdigit() else end_point,
            # poll arrive target for CALL = TCP start (station)
            "point_id": int(start_key) if start_key.isdigit() else start_point,
            "template_id": route["template_id"],
            "task_name": route["task_name"],
            # CALL segment endpoints (for logs / future payload use)
            "call_from_point_id": home,
            "call_to_point_id": int(start_key) if start_key.isdigit() else start_point,
            "leave_from_point_id": int(start_key) if start_key.isdigit() else start_point,
            "leave_to_point_id": int(end_key) if end_key.isdigit() else end_point,
            "_route_key_call": route_key,
            "_route_key_leave": f"{code}:{start_key}:{end_key}",
        }
    )
    return out


def enrich_leave(cfg: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """Second signal: use stored trip + [leave_routes] → DONE payload fields.

    DONE path semantics:
      start_point_id → end_point_id (both from first TCP)
    """
    code = _as_str(fields.get("robot") or fields.get("_robot_code"))
    start_point = fields.get("start_point_id")
    end_point = fields.get("end_point_id")
    if not code or start_point in (None, "") or end_point in (None, ""):
        raise RouteResolveError(
            "leave requires context robot/start_point_id/end_point_id "
            "from first TCP message"
        )

    start_key = _point_token(start_point)
    end_key = _point_token(end_point)
    route_key = f"{code}:{start_key}:{end_key}"
    routes = cfg.get("_leave_routes") or {}
    route = routes.get(route_key) or routes.get(route_key.lower())
    if not route:
        # fallback: start:end without robot
        alt = f"{start_key}:{end_key}"
        route = routes.get(alt) or routes.get(alt.lower())
        route_key = alt if route else route_key
    if not route:
        raise RouteResolveError(
            f"[leave_routes] missing route for key {route_key!r}; "
            f"known={sorted(routes.keys())}"
        )

    out = dict(fields)
    out.update(
        {
            "cmd": "DONE",
            "template_id": route["template_id"],
            "task_name": route["task_name"],
            # optional poll target after leave = end point
            "point_id": int(end_key) if end_key.isdigit() else end_point,
            "_route_key_leave": route_key,
        }
    )
    if out.get("robot_id") in (None, ""):
        entry = resolve_robot_entry(cfg, code)
        if entry:
            out["robot_id"] = int(entry["robot_id"])
            if out.get("map_id") in (None, ""):
                out["map_id"] = entry.get("map_id")
    return out
