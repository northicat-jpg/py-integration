"""Read/write robots, routes, and selected [rcs] keys while preserving other text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_KV_RE = re.compile(r"^([^=;#]+?)\s*=\s*(.*)$")
_ENV_DEFAULT_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*):(.*)\}$")

EDITABLE_SECTIONS = ("robots", "call_routes", "leave_routes")
RCS_EDIT_KEYS = ("base_url", "username", "password")


@dataclass
class RobotRow:
    code: str = ""
    robot_id: str = ""
    name: str = ""
    home_point_id: str = ""
    map_id: str = ""

    def to_line(self) -> str:
        code = self.code.strip()
        rid = self.robot_id.strip()
        name = self.name.strip()
        home = self.home_point_id.strip()
        mid = self.map_id.strip()
        parts = [rid]
        if name or home or mid:
            parts.append(name)
        if home or mid:
            parts.append(home)
        if mid:
            parts.append(mid)
        return f"{code} = {' | '.join(parts)}"

    def search_blob(self) -> str:
        return " ".join(
            [self.code, self.robot_id, self.name, self.home_point_id, self.map_id]
        ).lower()


@dataclass
class CallRouteRow:
    robot: str = ""
    start_point_id: str = ""
    template_id: str = ""
    task_name: str = ""

    def key(self) -> str:
        return f"{self.robot.strip()}:{self.start_point_id.strip()}"

    def to_line(self) -> str:
        tid = self.template_id.strip()
        name = self.task_name.strip()
        value = f"{tid} | {name}" if name else tid
        return f"{self.key()} = {value}"

    def search_blob(self) -> str:
        return " ".join(
            [self.key(), self.template_id, self.task_name]
        ).lower()


@dataclass
class LeaveRouteRow:
    robot: str = ""
    start_point_id: str = ""
    end_point_id: str = ""
    template_id: str = ""
    task_name: str = ""

    def key(self) -> str:
        robot = self.robot.strip()
        start = self.start_point_id.strip()
        end = self.end_point_id.strip()
        if robot:
            return f"{robot}:{start}:{end}"
        return f"{start}:{end}"

    def to_line(self) -> str:
        tid = self.template_id.strip()
        name = self.task_name.strip()
        value = f"{tid} | {name}" if name else tid
        return f"{self.key()} = {value}"

    def search_blob(self) -> str:
        return " ".join(
            [self.key(), self.template_id, self.task_name]
        ).lower()


@dataclass
class RcsSettings:
    base_url: str = ""
    username: str = ""
    password: str = ""
    # Remember original ${ENV:default} wrappers so save can keep env form when unchanged shape
    base_url_env: str | None = None
    username_env: str | None = None
    password_env: str | None = None


@dataclass
class SearchHit:
    kind: str  # robots | call_routes | leave_routes | rcs
    index: int
    title: str
    detail: str


@dataclass
class EditableConfig:
    path: Path
    robots: list[RobotRow] = field(default_factory=list)
    call_routes: list[CallRouteRow] = field(default_factory=list)
    leave_routes: list[LeaveRouteRow] = field(default_factory=list)
    rcs: RcsSettings = field(default_factory=RcsSettings)
    raw_text: str = ""


def default_config_candidates(project_root: Path) -> list[Path]:
    cfg_dir = project_root / "config"
    return [
        cfg_dir / "config.config",
        cfg_dir / "config.example.config",
    ]


def resolve_default_config(project_root: Path) -> Path | None:
    for path in default_config_candidates(project_root):
        if path.is_file():
            return path
    return None


def _split_sections(text: str) -> list[tuple[str | None, list[str]]]:
    """Split file into [(section_name|None for preamble, body_lines including header)]."""
    lines = text.splitlines(keepends=True)
    chunks: list[tuple[str | None, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []

    for line in lines:
        m = _SECTION_RE.match(line.rstrip("\r\n"))
        if m:
            if current_lines or current_name is not None:
                chunks.append((current_name, current_lines))
            current_name = m.group(1).strip().lower()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines or current_name is not None:
        chunks.append((current_name, current_lines))
    return chunks


def _iter_kv_lines(section_lines: list[str]) -> Iterable[tuple[str, str]]:
    for line in section_lines[1:]:  # skip [header]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        m = _KV_RE.match(stripped)
        if not m:
            continue
        yield m.group(1).strip(), m.group(2).strip()


def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _unwrap_env_default(raw: str) -> tuple[str, str | None]:
    """Return (display_value, env_name|None). Keeps ${ENV:default} editable as default."""
    raw = raw.strip()
    m = _ENV_DEFAULT_RE.match(raw)
    if not m:
        return _strip_wrapping_quotes(raw), None
    return _strip_wrapping_quotes(m.group(2)), m.group(1)


def _quote_ini_if_needed(value: str) -> str:
    if any(ch in value for ch in "#;=") or value != value.strip():
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _wrap_env_or_plain(value: str, env_name: str | None) -> str:
    value = value.strip()
    if env_name:
        return f"${{{env_name}:{_quote_ini_if_needed(value)}}}"
    return _quote_ini_if_needed(value)


def _parse_robot(key: str, value: str) -> RobotRow:
    parts = [p.strip() for p in value.split("|")]
    return RobotRow(
        code=key,
        robot_id=parts[0] if parts else "",
        name=parts[1] if len(parts) > 1 else "",
        home_point_id=parts[2] if len(parts) > 2 else "",
        map_id=parts[3] if len(parts) > 3 else "",
    )


def _parse_call(key: str, value: str) -> CallRouteRow:
    robot, _, start = key.partition(":")
    tid, sep, name = value.partition("|")
    return CallRouteRow(
        robot=robot.strip(),
        start_point_id=start.strip(),
        template_id=tid.strip(),
        task_name=name.strip() if sep else "",
    )


def _parse_leave(key: str, value: str) -> LeaveRouteRow:
    parts = [p.strip() for p in key.split(":")]
    tid, sep, name = value.partition("|")
    if len(parts) >= 3:
        robot, start, end = parts[0], parts[1], parts[2]
    elif len(parts) == 2:
        robot, start, end = "", parts[0], parts[1]
    else:
        robot, start, end = "", parts[0] if parts else "", ""
    return LeaveRouteRow(
        robot=robot,
        start_point_id=start,
        end_point_id=end,
        template_id=tid.strip(),
        task_name=name.strip() if sep else "",
    )


def _parse_rcs(section_lines: list[str]) -> RcsSettings:
    settings = RcsSettings()
    for key, value in _iter_kv_lines(section_lines):
        k = key.lower()
        if k not in RCS_EDIT_KEYS:
            continue
        display, env = _unwrap_env_default(value)
        setattr(settings, k, display)
        setattr(settings, f"{k}_env", env)
    return settings


def load_editable(path: Path) -> EditableConfig:
    text = path.read_text(encoding="utf-8")
    cfg = EditableConfig(path=path, raw_text=text)
    for name, lines in _split_sections(text):
        if name == "robots":
            cfg.robots = [_parse_robot(k, v) for k, v in _iter_kv_lines(lines)]
        elif name == "call_routes":
            cfg.call_routes = [_parse_call(k, v) for k, v in _iter_kv_lines(lines)]
        elif name == "leave_routes":
            cfg.leave_routes = [_parse_leave(k, v) for k, v in _iter_kv_lines(lines)]
        elif name == "rcs":
            cfg.rcs = _parse_rcs(lines)
    return cfg


def _section_body(section: str, value_lines: list[str]) -> str:
    header_comments = {
        "robots": (
            "# ---------- 小车绑定：短码 → 编号(robotId) / 名称 / 停靠点(home) / 地图 ----------\n"
            "# 写法：短码 = robotId | 名称 | home_point_id | map_id\n"
            "# CALL 段：home_point_id → start_point_id（TCP 起点=站台）\n"
            "# DONE 段：start_point_id → end_point_id（TCP 终点）\n"
        ),
        "call_routes": (
            "# ---------- CALL 路由：robot:start_point_id = template_id | task_name ----------\n"
            "# 第一段任务（停车点 → 站台起点）选用哪套 RCS 任务模板\n"
        ),
        "leave_routes": (
            "# ---------- DONE 路由：robot:start:end = template_id | task_name ----------\n"
            "# 第二段任务（站台起点 → 货物终点）；也可用 start:end（不写 robot）\n"
        ),
    }
    comment = header_comments.get(section, "")
    body = "".join(line if line.endswith("\n") else line + "\n" for line in value_lines)
    if body and not body.endswith("\n"):
        body += "\n"
    return f"{comment}[{section}]\n{body}"


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_trailing_intro_comments(lines: list[str]) -> list[str]:
    """Remove trailing blanks/comments that introduce the next editable [section]."""
    result = list(lines)
    while result:
        stripped = result[-1].strip()
        if stripped == "" or stripped.startswith("#") or stripped.startswith(";"):
            result.pop()
            continue
        break
    if result and not result[-1].endswith("\n"):
        result[-1] += "\n"
    return result


def _ensure_blank_separator(parts: list[str]) -> None:
    if not parts:
        return
    if not parts[-1].endswith("\n"):
        parts[-1] += "\n"
    if not parts[-1].endswith("\n\n"):
        parts.append("\n")


def _patch_rcs_section(section_lines: list[str], rcs: RcsSettings) -> list[str]:
    """Update base_url/username/password in-place; keep other [rcs] keys/comments."""
    updates = {
        "base_url": _wrap_env_or_plain(rcs.base_url, rcs.base_url_env),
        "username": _wrap_env_or_plain(rcs.username, rcs.username_env),
        "password": _wrap_env_or_plain(rcs.password, rcs.password_env),
    }
    seen: set[str] = set()
    out: list[str] = []
    if not section_lines:
        out.append("[rcs]\n")
    else:
        out.append(section_lines[0] if section_lines[0].startswith("[") else "[rcs]\n")
        start = 1 if section_lines and _SECTION_RE.match(section_lines[0].rstrip("\r\n")) else 0
        for line in section_lines[start:]:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith(";"):
                m = _KV_RE.match(stripped)
                if m:
                    key = m.group(1).strip().lower()
                    if key in updates:
                        nl = "\n" if line.endswith("\n") else ""
                        # preserve indentation before key if any
                        indent = line[: len(line) - len(line.lstrip())] if line.lstrip() else ""
                        out.append(f"{indent}{key} = {updates[key]}{nl}")
                        seen.add(key)
                        continue
            out.append(line if line.endswith("\n") else line + "\n")

    missing = [k for k in RCS_EDIT_KEYS if k not in seen]
    if missing:
        # insert before trailing blank lines
        trailing: list[str] = []
        while out and out[-1].strip() == "":
            trailing.append(out.pop())
        for key in missing:
            out.append(f"{key} = {updates[key]}\n")
        out.extend(reversed(trailing))
    return out


def save_editable(cfg: EditableConfig, path: Path | None = None) -> Path:
    target = path or cfg.path
    text = cfg.raw_text if cfg.raw_text else (target.read_text(encoding="utf-8") if target.is_file() else "")
    text = _normalize_newlines(text)

    replacements = {
        "robots": [r.to_line() for r in cfg.robots if r.code.strip()],
        "call_routes": [
            r.to_line()
            for r in cfg.call_routes
            if r.robot.strip() and r.start_point_id.strip()
        ],
        "leave_routes": [
            r.to_line()
            for r in cfg.leave_routes
            if r.start_point_id.strip() and r.end_point_id.strip()
        ],
    }

    chunks = _split_sections(text if text.endswith("\n") or text == "" else text + "\n")
    present = {name for name, _ in chunks if name in EDITABLE_SECTIONS}
    present_rcs = any(name == "rcs" for name, _ in chunks)
    out: list[str] = []

    for idx, (name, lines) in enumerate(chunks):
        next_name = chunks[idx + 1][0] if idx + 1 < len(chunks) else None
        if name == "rcs":
            patched = _patch_rcs_section(lines, cfg.rcs)
            out.append("".join(patched))
            continue
        if name in replacements:
            _ensure_blank_separator(out)
            block = _section_body(name, replacements[name])
            out.append(block if block.endswith("\n") else block + "\n")
            out.append("\n")
        else:
            kept = list(lines)
            if next_name in replacements:
                kept = _strip_trailing_intro_comments(kept)
            out.append("".join(kept))

    if not present_rcs:
        # insert a minimal [rcs] near the top after preamble
        rcs_block = _patch_rcs_section(["[rcs]\n"], cfg.rcs)
        if out and out[0].startswith("["):
            out = ["".join(rcs_block), "\n"] + out
        else:
            # after preamble chunk
            inserted = False
            rebuilt: list[str] = []
            for part in out:
                rebuilt.append(part)
                if not inserted and ("[" in part or part.strip() == ""):
                    rebuilt.append("".join(rcs_block))
                    rebuilt.append("\n")
                    inserted = True
            if not inserted:
                rebuilt.insert(0, "".join(rcs_block) + "\n")
            out = rebuilt

    for name in EDITABLE_SECTIONS:
        if name not in present:
            _ensure_blank_separator(out)
            out.append(_section_body(name, replacements[name]))
            out.append("\n")

    result = "".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result)
    if not result.endswith("\n"):
        result += "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result, encoding="utf-8", newline="\n")
    cfg.path = target
    cfg.raw_text = result
    return target


def validate_rows(
    robots: list[RobotRow],
    call_routes: list[CallRouteRow],
    leave_routes: list[LeaveRouteRow],
    rcs: RcsSettings | None = None,
) -> list[str]:
    errors: list[str] = []
    if rcs is not None:
        if not rcs.base_url.strip():
            errors.append("RCS：base_url 不能为空")
        if not rcs.username.strip():
            errors.append("RCS：username 不能为空")
        if not rcs.password.strip():
            errors.append("RCS：password 不能为空")

    codes: set[str] = set()
    for i, row in enumerate(robots, 1):
        if not row.code.strip():
            errors.append(f"小车第 {i} 行：短码不能为空")
            continue
        if row.code.strip() in codes:
            errors.append(f"小车短码重复：{row.code.strip()}")
        codes.add(row.code.strip())
        if not row.robot_id.strip().isdigit():
            errors.append(f"小车 {row.code.strip()}：robotId 必须是整数")

    call_keys: set[str] = set()
    for i, row in enumerate(call_routes, 1):
        if not row.robot.strip() or not row.start_point_id.strip():
            errors.append(f"CALL 第 {i} 行：robot / start_point_id 不能为空")
            continue
        if not row.template_id.strip().isdigit():
            errors.append(f"CALL {row.key()}：template_id 必须是整数")
        if row.key() in call_keys:
            errors.append(f"CALL 路由键重复：{row.key()}")
        call_keys.add(row.key())

    leave_keys: set[str] = set()
    for i, row in enumerate(leave_routes, 1):
        if not row.start_point_id.strip() or not row.end_point_id.strip():
            errors.append(f"LEAVE 第 {i} 行：start / end 不能为空")
            continue
        if not row.template_id.strip().isdigit():
            errors.append(f"LEAVE {row.key()}：template_id 必须是整数")
        if row.key() in leave_keys:
            errors.append(f"LEAVE 路由键重复：{row.key()}")
        leave_keys.add(row.key())
    return errors


def search_config(
    cfg: EditableConfig,
    query: str,
    scopes: Iterable[str] | None = None,
) -> list[SearchHit]:
    q = query.strip().lower()
    if not q:
        return []
    wanted = set(scopes or ("robots", "call_routes", "leave_routes", "rcs"))
    hits: list[SearchHit] = []

    if "rcs" in wanted:
        for key in RCS_EDIT_KEYS:
            val = getattr(cfg.rcs, key, "") or ""
            blob = f"{key} {val}".lower()
            if q in blob:
                show = val if key != "password" else ("*" * min(8, len(val)) if val else "")
                hits.append(SearchHit("rcs", 0, f"RCS.{key}", show))

    if "robots" in wanted:
        for i, row in enumerate(cfg.robots):
            if q in row.search_blob():
                hits.append(
                    SearchHit(
                        "robots",
                        i,
                        f"小车 {row.code}",
                        f"id={row.robot_id} name={row.name} home={row.home_point_id} map={row.map_id}",
                    )
                )

    if "call_routes" in wanted:
        for i, row in enumerate(cfg.call_routes):
            if q in row.search_blob():
                hits.append(
                    SearchHit(
                        "call_routes",
                        i,
                        f"CALL {row.key()}",
                        f"template={row.template_id} name={row.task_name}",
                    )
                )

    if "leave_routes" in wanted:
        for i, row in enumerate(cfg.leave_routes):
            if q in row.search_blob():
                hits.append(
                    SearchHit(
                        "leave_routes",
                        i,
                        f"LEAVE {row.key()}",
                        f"template={row.template_id} name={row.task_name}",
                    )
                )
    return hits
