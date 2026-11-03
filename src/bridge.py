"""TCP -> parse(config) -> HTTP /system/task/* bridge (optional handshake workflow)."""

from __future__ import annotations

import argparse
import json
import logging
import socketserver
import sys
import uuid
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config_loader import load_config, task_path_from_config
from rcs_client import RcsClient
from tcp_parser import ParseError, StreamFramer, parse_message, render_template
from workflow import HandshakeError, SessionHub

logger = logging.getLogger("rcs_bridge")


class BridgeState:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.runtime = cfg.get("runtime") or {}
        self.dry_run = bool(self.runtime.get("dry_run", False))
        self.log_raw = bool(self.runtime.get("log_raw_message", True))
        self.parser_cfg = cfg.get("parser") or {}
        self.task_cfg = cfg.get("task") or {}
        self.workflow_cfg = cfg.get("workflow") or {}
        self.workflow_enabled = bool(self.workflow_cfg.get("enabled", False))
        self.payload_template = self.task_cfg.get("payload_template")
        if self.payload_template is None:
            raise ValueError("task.payload_template is required")
        self.task_path = task_path_from_config(self.task_cfg)
        self.client: RcsClient | None = None
        if not self.dry_run:
            self.client = RcsClient(cfg["rcs"])
        self.hub = SessionHub(cfg, self.client, self.dry_run)

    def handle_message_text(
        self,
        text: str,
        conn_id: str = "once",
        reply: Any | None = None,
    ) -> None:
        if self.log_raw:
            logger.info("raw message: %r", text)
        fields = parse_message(text, self.parser_cfg)
        logger.info("parsed fields: %s", fields)

        if self.workflow_enabled:
            session = self.hub.get(conn_id, fields, reply)
            session.handle_fields(fields)
            return

        # legacy: one message -> one task
        payload = render_template(self.payload_template, fields)
        logger.info(
            "task path=%s payload=%s",
            self.task_path,
            json.dumps(payload, ensure_ascii=False),
        )
        if self.dry_run:
            logger.info("dry_run=true, skip HTTP call")
            return
        assert self.client is not None
        result = self.client.dispatch_task(self.task_path, payload)
        logger.info("RCS response: %s", result)


def _make_handler(state: BridgeState, tcp_cfg: dict[str, Any]):
    encoding = tcp_cfg.get("encoding") or "utf-8"
    welcome = tcp_cfg.get("welcome") or ""

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            peer = self.client_address
            conn_id = f"{peer[0]}:{peer[1]}:{uuid.uuid4().hex[:8]}"
            logger.info("client connected: %s id=%s", peer, conn_id)
            framer = StreamFramer(tcp_cfg)

            def reply(data: bytes) -> None:
                self.request.sendall(data)

            if welcome:
                try:
                    self.request.sendall(str(welcome).encode(encoding))
                except OSError:
                    return
            try:
                while True:
                    data = self.request.recv(4096)
                    if not data:
                        break
                    for raw in framer.feed(data):
                        try:
                            text = raw.decode(encoding, errors="strict")
                        except UnicodeDecodeError as exc:
                            logger.error("decode error from %s: %s", peer, exc)
                            continue
                        try:
                            state.handle_message_text(text, conn_id=conn_id, reply=reply)
                        except ParseError as exc:
                            logger.error("parse error from %s: %s", peer, exc)
                        except HandshakeError as exc:
                            logger.error("workflow error from %s: %s", peer, exc)
                        except Exception:
                            logger.exception("failed handling message from %s", peer)
            finally:
                logger.info("client disconnected: %s", peer)

    return Handler


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server(cfg: dict[str, Any]) -> None:
    tcp_cfg = cfg.get("tcp") or {}
    host = str(tcp_cfg.get("host") or "0.0.0.0")
    port = int(tcp_cfg.get("port") or 9100)
    state = BridgeState(cfg)
    handler = _make_handler(state, tcp_cfg)
    with ThreadedTCPServer((host, port), handler) as server:
        logger.info(
            "listening tcp://%s:%s  workflow=%s dry_run=%s  config=%s",
            host,
            port,
            state.workflow_enabled,
            state.dry_run,
            (cfg.get("_meta") or {}).get("config_path"),
        )
        server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RCS TCP -> HTTP task bridge")
    parser.add_argument(
        "-c",
        "--config",
        help="path to .config (default: config/config.config or config.example.config)",
    )
    parser.add_argument(
        "--once",
        help="process message(s) without listening; raw text or @file. "
        "With workflow, file may contain multiple lines (CALL then DONE).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    # IDE / redirected stdout often block-buffers; force line flush so logs appear immediately.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    class _FlushHandler(logging.StreamHandler):
        def emit(self, record: logging.LogRecord) -> None:
            super().emit(record)
            self.flush()

    root = logging.getLogger()
    root.handlers.clear()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[_FlushHandler(sys.stdout)],
        force=True,
    )

    cfg = load_config(args.config)
    if args.once is not None:
        once = args.once
        if once.startswith("@"):
            once = Path(once[1:]).read_text(encoding="utf-8")
        state = BridgeState(cfg)
        # support multi-line handshake script in one --once file
        lines = [ln.strip() for ln in once.splitlines() if ln.strip()]
        if not lines:
            lines = [once]
        for line in lines:
            state.handle_message_text(line, conn_id="once", reply=None)
        return 0

    run_server(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
