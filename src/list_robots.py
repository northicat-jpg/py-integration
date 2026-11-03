"""Login to RCS and print robot inventory + online status (HAR-aligned)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config_loader import load_config
from rcs_client import RcsClient, dig, extract_robot_rows

logger = logging.getLogger("list_robots")


def load_state_labels(client: RcsClient, path: str) -> dict[str, str]:
    payload = client.get_json(path)
    data = payload.get("data") or []
    return {str(x.get("dictValue")): str(x.get("dictLabel")) for x in data}


def load_dept_runtime(client: RcsClient, path_template: str, dept_id: Any) -> dict[Any, dict[str, Any]]:
    """Map robotId -> {online, robotStatusVo, ...} from /system/robot/dept/{deptId}."""
    path = str(path_template).replace("{dept_id}", str(dept_id))
    payload = client.get_json(path)
    items = payload.get("data") or []
    out: dict[Any, dict[str, Any]] = {}
    if not isinstance(items, list):
        return out
    for item in items:
        sys_robot = item.get("sysRobot") or {}
        rid = item.get("robotId") or sys_robot.get("robotId")
        rsv = item.get("robotStatusVo")
        # Same rule as frontend: only online when robotStatusVo.online is truthy
        online = bool(rsv and rsv.get("online"))
        out[rid] = {
            "online": online,
            "robotStatusVo": rsv,
            "currentPosition": item.get("currentPosition"),
            "dog": item.get("dog"),
            "sysRobot": sys_robot,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List RCS robots and online status")
    parser.add_argument("-c", "--config", help="path to .config")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--raw", action="store_true", help="print raw API JSON")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    cfg = load_config(args.config)
    rcs_cfg = cfg.get("rcs") or {}
    client = RcsClient(rcs_cfg)

    print(f"base_url = {client.base_url}")
    print(f"username = {client.username}")
    token = client.login()
    print(f"login ok, token length = {len(token)}")

    list_path = str(rcs_cfg.get("robot_list_path") or "/system/robot/list")
    dept_id = rcs_cfg.get("dept_id") or 100
    dept_path = str(rcs_cfg.get("robot_dept_path") or "/system/robot/dept/{dept_id}")
    state_dict_path = str(
        rcs_cfg.get("robot_state_dict_path") or "/system/dict/data/type/sys_robot_state"
    )

    list_payload = client.list_robots(list_path)
    state_labels = load_state_labels(client, state_dict_path)
    runtime = load_dept_runtime(client, dept_path, dept_id)

    if args.raw:
        print("=== list ===")
        print(json.dumps(list_payload, ensure_ascii=False, indent=2))
        print("=== dept runtime ===")
        print(json.dumps(runtime, ensure_ascii=False, indent=2, default=str))

    rows = extract_robot_rows(list_payload)
    online_n = 0
    offline_n = 0

    print(f"total robots (list) = {len(rows)}")
    print(f"runtime from dept/{dept_id}: {len(runtime)} entries")
    print("-" * 72)
    print(
        f"{'id':<6} {'name':<12} {'启用状态':<8} {'在线':<6} {'uuid'}"
    )
    print("-" * 72)

    for row in rows:
        rid = row.get("robotId")
        name = row.get("robotName") or ""
        state_code = str(row.get("state"))
        state_label = state_labels.get(state_code, state_code)
        rt = runtime.get(rid) or {}
        # if robot missing from dept payload, treat as offline (no status vo)
        if rid in runtime:
            online = bool(rt.get("online"))
        else:
            online = False
        if online:
            online_n += 1
            online_text = "在线"
        else:
            offline_n += 1
            online_text = "离线"
        print(
            f"{rid!s:<6} {name!s:<12} {state_label!s:<8} {online_text:<6} "
            f"{row.get('robotUuid')!s}"
        )

    print("-" * 72)
    print(f"online = {online_n}, offline = {offline_n}")
    print(
        "说明: 列表 state=启用/停用；在线来自 /system/robot/dept/{deptId} 的 "
        "robotStatusVo.online（null 视为离线，与网页一致）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
