# TCP 报文与 parser / 握手配置示例

## 握手流程（workflow.enabled = true）

设备在同一 TCP 连接（或按 session_key_field）依次发送：

```text
{"cmd":"CALL","template_id":12,"point_id":100,"robot_id":1}
{"cmd":"DONE","template_id":12,"point_id":100,"robot_id":1}
```

脚本行为：

1. `CALL` → 按 `[task]` 下发去点任务  
2. 轮询 `/system/robot/status/{robot_id}` 等到点  
3. 按 `[notify]` 回发设备（默认同连接 `ARRIVE_OK`）  
4. `DONE` → 按 `[task_leave]` 下发离开任务  
5. 可选再等到 `IDLE`

本地演练（dry_run，跳过真实 HTTP/到点等待）：

```bash
python main.py --once @docs/examples/handshake_once.txt
```

## 关闭握手（旧逻辑：一报文一任务）

```ini
[workflow]
enabled = false
```

## parser 其它格式

### 逗号分隔

```text
CALL,12,100,1
```

```ini
[parser]
type = delimiter
separator = ,
fields = cmd,template_id,point_id,robot_id
cast.template_id = int
cast.point_id = int
cast.robot_id = int
```

### 正则

```ini
[parser]
type = regex
pattern = ^(?P<cmd>CALL|DONE),(?P<template_id>\d+),(?P<point_id>\d+),(?P<robot_id>\d+)$
cast.template_id = int
cast.point_id = int
cast.robot_id = int
```
