"""Declarative TCP message framing + field extraction driven by config."""

from __future__ import annotations

import json
import re
from typing import Any


class ParseError(ValueError):
    pass


def apply_casts(data: dict[str, Any], casts: dict[str, str] | None) -> dict[str, Any]:
    if not casts:
        return data
    out = dict(data)
    for key, typ in casts.items():
        if key not in out or out[key] is None:
            continue
        value = out[key]
        t = str(typ).lower()
        try:
            if t in ("int", "integer"):
                out[key] = int(value)
            elif t in ("float", "double", "number"):
                out[key] = float(value)
            elif t in ("bool", "boolean"):
                if isinstance(value, bool):
                    out[key] = value
                else:
                    out[key] = str(value).strip().lower() in ("1", "true", "yes", "y", "on")
            elif t in ("str", "string"):
                out[key] = str(value)
            else:
                raise ParseError(f"unsupported cast type: {typ}")
        except (TypeError, ValueError) as exc:
            raise ParseError(f"cannot cast field {key}={value!r} to {typ}") from exc
    return out


def parse_message(text: str, parser_cfg: dict[str, Any]) -> dict[str, Any]:
    """Parse one message text into a flat/nested dict of fields."""
    ptype = str(parser_cfg.get("type", "json")).lower()
    text = text.strip("\ufeff").strip()
    # Second handshake signal format is TBD: allow empty / non-JSON as {}.
    allow_loose = bool(parser_cfg.get("allow_empty", True))
    if not text:
        if allow_loose:
            data = {}
            defaults = dict(parser_cfg.get("defaults") or {})
            return apply_casts({**defaults, **data}, parser_cfg.get("casts"))
        raise ParseError("empty message")

    if ptype == "json":
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            if allow_loose:
                # e.g. plain "OK" / "1" while waiting for second signal
                data = {"_raw": text}
                defaults = dict(parser_cfg.get("defaults") or {})
                return apply_casts({**defaults, **data}, parser_cfg.get("casts"))
            raise ParseError(f"invalid json: {exc}") from exc
        if not isinstance(obj, dict):
            raise ParseError("json root must be an object")
        data = obj
    elif ptype == "regex":
        pattern = parser_cfg.get("pattern")
        if not pattern:
            raise ParseError("parser.pattern is required for type=regex")
        m = re.search(pattern, text)
        if not m:
            raise ParseError(f"regex did not match: {pattern!r}")
        data = {k: v for k, v in m.groupdict().items() if v is not None}
    elif ptype == "delimiter":
        sep = parser_cfg.get("separator", ",")
        fields = parser_cfg.get("fields") or []
        if not fields:
            raise ParseError("parser.fields is required for type=delimiter")
        parts = text.split(sep)
        if len(parts) < len(fields):
            raise ParseError(
                f"expected at least {len(fields)} parts, got {len(parts)}: {parts!r}"
            )
        data = {name: parts[i].strip() for i, name in enumerate(fields)}
    elif ptype == "kv":
        pair_sep = parser_cfg.get("pair_separator", ";")
        kv_sep = parser_cfg.get("kv_separator", "=")
        data = {}
        for piece in text.split(pair_sep):
            piece = piece.strip()
            if not piece:
                continue
            if kv_sep not in piece:
                raise ParseError(f"bad kv pair: {piece!r}")
            k, v = piece.split(kv_sep, 1)
            data[k.strip()] = v.strip()
    else:
        raise ParseError(f"unknown parser.type: {ptype}")

    defaults = dict(parser_cfg.get("defaults") or {})
    # message fields override defaults
    merged = {**defaults, **data}
    return apply_casts(merged, parser_cfg.get("casts"))


_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _lookup(fields: dict[str, Any], name: str) -> Any:
    if name not in fields:
        raise ParseError(f"missing field for placeholder {{{name}}}")
    return fields[name]


def render_template(template: Any, fields: dict[str, Any]) -> Any:
    """Replace {field} in strings; recurse into dict/list.

    A string that is exactly `{name}` keeps the field's native type (int/bool/...).
    Mixed strings become text after substitution.
    """
    if isinstance(template, str):
        m = re.fullmatch(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template.strip())
        if m:
            return _lookup(fields, m.group(1))

        def repl(match: re.Match[str]) -> str:
            return str(_lookup(fields, match.group(1)))

        return _PLACEHOLDER.sub(repl, template)
    if isinstance(template, list):
        return [render_template(x, fields) for x in template]
    if isinstance(template, dict):
        return {k: render_template(v, fields) for k, v in template.items()}
    return template


class StreamFramer:
    """Accumulate TCP bytes and yield complete message payloads (bytes)."""

    def __init__(self, tcp_cfg: dict[str, Any]):
        self.framing = str(tcp_cfg.get("framing", "line")).lower()
        self.delimiter = (tcp_cfg.get("delimiter") or "\n").encode("utf-8")
        self.fixed_length = int(tcp_cfg.get("fixed_length") or 0)
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        messages: list[bytes] = []

        if self.framing in ("line", "delimiter"):
            sep = b"\n" if self.framing == "line" else self.delimiter
            # treat \r\n as line end when framing=line
            while True:
                if self.framing == "line":
                    # normalize: find \n
                    idx = self._buf.find(b"\n")
                    if idx < 0:
                        break
                    chunk = bytes(self._buf[:idx])
                    if chunk.endswith(b"\r"):
                        chunk = chunk[:-1]
                    del self._buf[: idx + 1]
                    if chunk:
                        messages.append(chunk)
                else:
                    idx = self._buf.find(sep)
                    if idx < 0:
                        break
                    chunk = bytes(self._buf[:idx])
                    del self._buf[: idx + len(sep)]
                    if chunk:
                        messages.append(chunk)
        elif self.framing == "fixed":
            if self.fixed_length <= 0:
                raise ParseError("tcp.fixed_length must be > 0 when framing=fixed")
            while len(self._buf) >= self.fixed_length:
                messages.append(bytes(self._buf[: self.fixed_length]))
                del self._buf[: self.fixed_length]
        else:
            raise ParseError(f"unknown tcp.framing: {self.framing}")

        return messages
