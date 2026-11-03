"""Load INI-style .config with ${ENV} / ${ENV:default} expansion."""

from __future__ import annotations

import configparser
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


def project_root() -> Path:
    """Repo root in source tree; directory of the .exe when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")

_BOOL_TRUE = {"1", "true", "yes", "y", "on"}
_BOOL_FALSE = {"0", "false", "no", "n", "off"}
_INT_KEYS = {
    ("tcp", "port"),
    ("tcp", "fixed_length"),
    ("rcs", "timeout_sec"),
    ("rcs", "dept_id"),
    ("notify", "port"),
    ("workflow", "robot_id"),
    ("workflow", "point_id"),
}
_FLOAT_KEYS = {
    ("poll", "interval_sec"),
    ("poll", "timeout_sec"),
}
_BOOL_KEYS = {
    ("runtime", "dry_run"),
    ("runtime", "log_raw_message"),
    ("workflow", "enabled"),
    ("poll", "require_task_finished"),
    ("poll", "wait_idle_after_leave"),
    ("parser", "allow_empty"),
}
_CSV_KEYS = {
    ("poll", "accept_states"),
    ("poll", "idle_states"),
}
_PAYLOAD_SECTIONS = ("task", "task_leave")


def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _expand_string(value: str, environ: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        default = match.group(2)
        if key in environ and environ[key] != "":
            return environ[key]
        if default is not None:
            return _strip_wrapping_quotes(default)
        return match.group(0)

    return _strip_wrapping_quotes(_ENV_PATTERN.sub(repl, value))


def _unescape_common(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
    )


def _parse_scalar(section: str, key: str, value: str) -> Any:
    raw = value.strip()
    if (section, key) in _BOOL_KEYS:
        low = raw.lower()
        if low in _BOOL_TRUE:
            return True
        if low in _BOOL_FALSE:
            return False
    if (section, key) in _INT_KEYS:
        if raw == "":
            return None
        return int(raw)
    if (section, key) in _FLOAT_KEYS:
        return float(raw)
    if (section, key) in _CSV_KEYS:
        return [x.strip() for x in raw.split(",") if x.strip()]
    if raw[:1] in "{[":
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return _unescape_common(raw)


def load_dotenv_if_present(project_root: Path) -> None:
    env_path = project_root / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path)


def _resolve_payload_template(
    task: dict[str, Any], config_path: Path, project_root: Path
) -> None:
    file_key = task.get("payload_template_file")
    if file_key:
        path = Path(str(file_key))
        if not path.is_file():
            cand = config_path.parent / path
            if cand.is_file():
                path = cand
            else:
                path = project_root / path
        text = path.read_text(encoding="utf-8")
        task["payload_template"] = json.loads(text)
        return
    pt = task.get("payload_template")
    if isinstance(pt, str):
        task["payload_template"] = json.loads(pt)


def _section_to_dict(
    parser: configparser.ConfigParser, section: str, environ: dict[str, str]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    casts: dict[str, str] = {}
    defaults: dict[str, Any] = {}
    section_l = section.lower()

    for key, value in parser.items(section):
        expanded = _expand_string(value, environ)
        if section_l == "parser" and key.startswith("cast."):
            casts[key[5:]] = expanded.strip()
            continue
        if section_l == "parser" and key.startswith("default."):
            defaults[key[8:]] = _parse_scalar(section_l, key[8:], expanded)
            continue
        if section_l == "parser" and key == "fields":
            out["fields"] = [x.strip() for x in expanded.split(",") if x.strip()]
            continue
        out[key] = _parse_scalar(section_l, key, expanded)

    if casts:
        out["casts"] = casts
    if defaults:
        out["defaults"] = defaults
    return out


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    root = project_root()
    load_dotenv_if_present(root)

    if config_path is None:
        candidate = root / "config" / "config.config"
        if not candidate.is_file():
            candidate = root / "config" / "config.example.config"
        config_path = candidate
    else:
        config_path = Path(config_path)

    if not config_path.is_file():
        raise FileNotFoundError(f"config not found: {config_path}")

    # delimiters=('=',) so route keys like 01:100 keep the colon
    cp = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    cp.optionxform = str.lower  # type: ignore[method-assign]

    read_ok = cp.read(config_path, encoding="utf-8")
    if not read_ok:
        raise FileNotFoundError(f"config not readable: {config_path}")

    environ = dict(os.environ)
    cfg: dict[str, Any] = {}
    for section in cp.sections():
        cfg[section.lower()] = _section_to_dict(cp, section, environ)

    for name in _PAYLOAD_SECTIONS:
        if name in cfg:
            _resolve_payload_template(cfg[name], config_path, root)

    cfg["_robot_aliases"] = build_robot_alias_index(cfg.get("robots") or {})
    cfg["_call_routes"] = build_route_index(cfg.get("call_routes") or {}, "call_routes")
    cfg["_leave_routes"] = build_route_index(cfg.get("leave_routes") or {}, "leave_routes")

    cfg["_meta"] = {
        "config_path": str(config_path.resolve()),
        "project_root": str(root),
    }
    return cfg


def build_robot_alias_index(robots_section: dict[str, Any]) -> dict[str, Any]:
    """Parse [robots] bindings.

    Format:
      short_code = robotId | display_name | home_point_id | map_id
    Optional named form after robotId:
      01 = 11 | AGV01 | home_point_id=501 | map_id=1
    """
    by_code: dict[str, dict[str, Any]] = {}
    name_overrides: dict[str, str] = {}

    for key, value in robots_section.items():
        k = str(key).strip().lower()
        if k.startswith("name."):
            code = k[5:].strip()
            if code:
                name_overrides[code] = str(value).strip()
            continue
        if k.startswith("_"):
            continue
        code = k
        parts = [p.strip() for p in str(value).split("|")]
        if not parts or parts[0] == "":
            continue
        try:
            robot_id = int(parts[0])
        except ValueError as exc:
            raise ValueError(
                f"[robots] {code!r} robotId must be int, got {parts[0]!r}"
            ) from exc

        entry: dict[str, Any] = {
            "code": code,
            "robot_id": robot_id,
            "name": code,
            "home_point_id": None,
            "map_id": None,
        }
        positional = []
        for part in parts[1:]:
            if "=" in part:
                pk, pv = part.split("=", 1)
                pk = pk.strip().lower()
                pv = pv.strip()
                if pk in ("name", "display", "display_name"):
                    entry["name"] = pv
                elif pk in ("home", "home_point", "home_point_id"):
                    entry["home_point_id"] = int(pv)
                elif pk in ("map", "map_id"):
                    entry["map_id"] = int(pv)
                else:
                    raise ValueError(f"[robots] {code!r} unknown key {pk!r}")
            else:
                positional.append(part)
        if len(positional) >= 1 and positional[0]:
            entry["name"] = positional[0]
        if len(positional) >= 2 and positional[1]:
            entry["home_point_id"] = int(positional[1])
        if len(positional) >= 3 and positional[2]:
            entry["map_id"] = int(positional[2])
        by_code[code] = entry

    for code, name in name_overrides.items():
        if code in by_code:
            by_code[code]["name"] = name
        else:
            raise ValueError(
                f"[robots] name.{code} has no matching short-code entry "
                f"(add `{code} = <robotId>` first)"
            )

    by_name: dict[str, dict[str, Any]] = {}
    by_id: dict[int, dict[str, Any]] = {}
    for entry in by_code.values():
        by_id[int(entry["robot_id"])] = entry
        name = str(entry.get("name") or "").strip()
        if name:
            by_name[_normalize_robot_token(name)] = entry
        by_name[_normalize_robot_token(entry["code"])] = entry

    return {"by_code": by_code, "by_name": by_name, "by_id": by_id}


def build_route_index(section: dict[str, Any], section_name: str) -> dict[str, dict[str, Any]]:
    """Parse route maps: key = template_id | task_name."""
    out: dict[str, dict[str, Any]] = {}
    for key, value in section.items():
        k = str(key).strip()
        if not k or k.startswith("_"):
            continue
        raw = str(value).strip()
        if "|" in raw:
            tid_s, tname = (p.strip() for p in raw.split("|", 1))
        else:
            tid_s, tname = raw, k
        if tid_s == "":
            raise ValueError(f"[{section_name}] {k!r} missing template_id")
        try:
            template_id = int(tid_s)
        except ValueError as exc:
            raise ValueError(
                f"[{section_name}] {k!r} template_id must be int, got {tid_s!r}"
            ) from exc
        out[k] = {"template_id": template_id, "task_name": tname or k}
        out[k.lower()] = out[k]
    return out


def _normalize_robot_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def resolve_robot_entry(cfg: dict[str, Any], token: Any) -> dict[str, Any] | None:
    """Map short code / name / robotId → full [robots] entry."""
    if token is None or token == "":
        return None
    if isinstance(token, bool):
        return None
    index = cfg.get("_robot_aliases") or {}
    by_code = index.get("by_code") or {}
    by_name = index.get("by_name") or {}
    by_id = index.get("by_id") or {}

    if isinstance(token, int):
        return by_id.get(token) or {
            "code": str(token),
            "robot_id": token,
            "name": str(token),
            "home_point_id": None,
            "map_id": None,
        }

    text = str(token).strip()
    if text.isdigit():
        candidates = [text, text.zfill(2)] if len(text) < 2 else [text]
        for c in candidates:
            if c in by_code:
                return by_code[c]
        rid = int(text)
        return by_id.get(rid) or {
            "code": text,
            "robot_id": rid,
            "name": text,
            "home_point_id": None,
            "map_id": None,
        }

    return by_name.get(_normalize_robot_token(text))


def resolve_robot_id(cfg: dict[str, Any], token: Any) -> int | None:
    """Map short code (01), display name (agv01), or numeric id → robotId."""
    entry = resolve_robot_entry(cfg, token)
    if not entry:
        return None
    return int(entry["robot_id"])


def task_path_from_config(task_cfg: dict[str, Any]) -> str:
    if task_cfg.get("path"):
        return str(task_cfg["path"])
    mode = str(task_cfg.get("mode", "template")).lower()
    mapping = {
        "template": "/system/task/template",
        "submit": "/system/task/submit",
        "temp": "/system/task/temp",
    }
    if mode not in mapping:
        raise ValueError(f"unknown task.mode: {mode}")
    return mapping[mode]
