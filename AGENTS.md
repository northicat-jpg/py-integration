# Agent notes for py-integration

1. Read `docs/RCS_API_MINIMAL.md`（工程最小接口）, then `docs/RCS_INTEGRATION_GUIDE.md` and `config/config.example.config`.
2. Prefer changing `config/*.config` for TCP formats, `[robots]` / `[call_routes]` / `[leave_routes]` maps, poll paths, notify text.
3. TCP first message only needs `robot` + `start_point_id` + `end_point_id`; `route_resolve` fills template/task/robot_id before HTTP.
4. Handshake logic lives in `workflow.py`; keep `bridge.py` as TCP I/O + routing.
5. Control AGVs via HTTP + JWT (`rcs_client.py`), not browser automation.
6. Keep secrets in `.env` or local `config.config`; use `${ENV}` placeholders.
7. `task` / `task_leave` payload_template must match browser Network captures.
8. Adjust `[poll]` JSON paths after capturing real `/system/robot/status/{id}` responses.
