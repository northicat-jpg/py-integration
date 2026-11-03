# RCS 对接最小接口清单

> 面向本仓库 `py-integration`：TCP 信号 → HTTP 下发任务 → 轮询到点 → 回发设备。  
> 从 `default.md`（Swagger 导出）+ `RCS_INTEGRATION_GUIDE.md` + 现场 HAR/`config.example.config` 精简。  
> **路径一律写业务路径**（不含 `/dev-api` / `/prod-api`）；前缀由 `base_url` 决定。

---

## 0.1 TCP 三要素 + 配置映射

TCP **第一次**只带：`robot`、`start_point_id`、`end_point_id`。  
`robot_id` / `map_id` / `template_id` / `task_name` / `cmd` 由配置解析：

```ini
[robots]
# 短码 = 编号 | 名称 | 停靠点(home) | 地图
01 = 11 | AGV01 | 501 | 1

[call_routes]
# CALL：home → start_point
01:100 = 35610 | A01去站台

[leave_routes]
# DONE：start → end
01:100:200 = 35610 | A01送终点
```

```json
{"robot":"01","start_point_id":100,"end_point_id":200}
{}
```

第二次报文**不需要** `cmd=DONE`（格式待定）；脚本在「已到站并通知设备」之后，收到任意一行即触发离开任务。

---


## 0. 怎么连

| 方式 | `base_url` 示例 |
|------|-----------------|
| 直连后端 | `http://<host>:8888` |
| 与网页同入口 | `http://<host>:9080/prod-api` |

现网默认（见配置）：`http://191.13.16.115:9080/prod-api`。

- 统一响应：`{ "code": 200, "msg": "...", "data": ... }`，`code == 200` 才算成功。  
- 分页列表常见：`rows` + `total`。  
- 业务请求头：`Authorization: Bearer <token>`，`Content-Type: application/json`。

---

## 1. 本工程实际用到的接口

与 `config.example.config` / `src/rcs_client.py` / `workflow.py` 对齐。

| 优先级 | 方法 | 路径 | 配置键 / 用途 |
|--------|------|------|----------------|
| 必用 | POST | `/login` | `[rcs] login_path` — 取 JWT |
| 必用 | POST | `/system/task/template` | `[task]` / `[task_leave]` — **按模板下发（推荐）** |
| 备选 | POST | `/system/task/submit` | 无模板、带步骤提交 |
| 必用 | GET | `/system/robot/status/{robotId}` | `[poll] robot_status_path` — 到点/空闲 |
| 常用 | GET | `/system/task/{taskId}` | `[poll] task_info_path` — 任务状态 |
| 常用 | GET | `/system/robot/list` | `[rcs] robot_list_path` — 清点车辆 |
| 常用 | GET | `/system/robot/dept/{deptId}` | `[rcs] robot_dept_path` — 含运行态 |
| 常用 | GET | `/system/dict/data/type/sys_robot_state` | `[rcs] robot_state_dict_path` — 启用字典 |
| 辅助 | GET | `/system/template/list` | 查 `templateId` |
| 辅助 | GET | `/system/point/list` | 查 `pointId` |
| 可选 | GET | `/system/task/executing` | 执行中任务 |
| 可选 | POST | `/lms/trigger_task/trigger/{triggerTaskId}` | 信号型触发（非模板下发时） |

### 1.1 `default.md` 里有 / 没有

| 接口 | OpenAPI (`default.md`) | 说明 |
|------|------------------------|------|
| `/login` | 无 | 仍以指南 + 现网为准 |
| `/system/task/template` | 无 | **配置默认下发路径**；务必抓包确认 |
| `/system/task/temp` / `cancel` / `pause` / `finish` | 无 | 指南有，文档未导出 |
| `/system/task/submit` | 有 | 可用备选 |
| `/system/robot/status/{robotId}` | 有 | 轮询核心 |
| `/system/robot/list`、`/dept/{deptId}` | 有 | 有；dept 响应含 `robotStatusVo` |
| `/lms/trigger_task/*` | 有 | 与指南一致 |

---

## 2. 鉴权

```http
POST /login
Content-Type: application/json

{"username":"system","password":"<secret>"}
```

成功时取 `token`（配置 `token_json_path = token`；也可能在 `data.token`）。

后续：

```http
Authorization: Bearer <token>
```

401 → 重新登录再重试一次（`rcs_client` 已实现）。

---

## 3. 下发任务

### 3.1 按模板（本工程默认）

```http
POST /system/task/template
```

现场 HAR（`191.13.16.115.har`）真实请求：

```json
{
  "task": {
    "priority": 5,
    "taskName": "A01agv测试1",
    "templateId": 35610,
    "robotGroupId": 2,
    "robotId": 11
  },
  "templateList": [35610]
}
```

配置已落盘为 `config/payload_arrive.json`（占位符版）与 `docs/examples/task_template_captured.json`（原文）：

```ini
[task]
mode = template
payload_template_file = payload_arrive.json
task_id_json_path = data.taskId
```

- `templateList`：系统里已有模板主键。  
- `task`：指定车、优先级、地图等；空对象 `{}` 时由模板/调度策略决定。  
- 成功后任务 ID：优先 `data.taskId`，其次 `data.task.taskId`。

### 3.2 带步骤提交（OpenAPI 有）

```http
POST /system/task/submit
```

指南结构：

```json
{
  "task": { },
  "stepList": [ ]
}
```

`default.md` 的请求示例把 `stepList` 展成深层实体树，**不可照抄**；以抓包为准。

### 3.3 任务查询（精简字段）

| 方法 | 路径 | 轮询关注 |
|------|------|----------|
| GET | `/system/task/{taskId}` | `data.status`（配置 `task_status_path`） |
| GET | `/system/task/executing` | 执行中列表 |
| GET | `/system/task/list` | 分页历史/全部 |

任务对象常见字段（OpenAPI）：`taskId`、`taskName`、`status`、`robotId`、`templateId`、`priority`、`startTime`、`finishTime`。

---

## 4. 机器人状态（到点判断）

### 4.1 单车状态

```http
GET /system/robot/status/{robotId}
```

OpenAPI 顶层（`data`）常见：

| 字段 | 含义 |
|------|------|
| `sysRobot` | 配置：名称、uuid、分组等 |
| `robotStatusVo` | **运行态**：在线、当前点、状态机 |
| `currentTask` / `currentMap` / `routes` | 当前任务/地图/路径 |
| `lastPoint` / `nextPoint` / `destPoint` | 点位相关 |

`robotStatusVo` 内与握手相关的字段：

| 字段 | 用途 |
|------|------|
| `online` | 是否在线（HAR：网页在线图标来源） |
| `state` | 运行状态（到点/空闲判断） |
| `currentPoint` | 当前点（对象或 ID，以实包为准） |
| `taskId` / `taskName` | 当前任务 |
| `energyLevel` | 电量 |

### 4.2 配置里的 JSON 路径

```ini
[poll]
robot_status_path = /system/robot/status/{robot_id}
status_state_path = data.state
status_point_path = data.currentPoint.pointId
accept_states = IDLE,MOVED,ACTED
idle_states = IDLE
```

注意：OpenAPI/HAR 更像：

- `data.robotStatusVo.state`
- `data.robotStatusVo.currentPoint`（或 `.pointId`）
- `data.robotStatusVo.online`

**联调时用真实 `/status/{id}` 响应改 `[poll]` 路径**（见 `AGENTS.md` 第 7 条）。

### 4.3 列表 vs 运行态（HAR 结论）

| 接口 | 得到什么 |
|------|----------|
| `GET /system/robot/list` | 配置列表；`state` 是 **启用/停用**（字典 `0/2/4`），**不是**行驶态；一般无 `online` |
| `GET /system/robot/dept/{deptId}` | 每项 `sysRobot` + `robotStatusVo`；可看在线/运行态 |
| `GET /system/dict/data/type/sys_robot_state` | `0=启用`，`2=停用`，`4=故障` |

样例：`docs/examples/robot_dept_100.json`、`sys_robot_state.json`、`HAR_ROBOT_FINDINGS.md`。

---

## 5. 查模板 / 点位（组 payload 前）

```http
GET /system/template/list?pageNum=1&pageSize=200
GET /system/point/list?pageNum=1&pageSize=200
```

外部系统只使用现场已有的 `templateId` / `pointId` / `mapId` / `robotId`，不要臆造坐标协议。

---

## 6. 触发任务（可选路径）

适合「RCS 里已配好触发器，外部只弹一下」：

| 方法 | 路径 |
|------|------|
| POST | `/lms/trigger_task/trigger/{triggerTaskId}` |
| POST | `/lms/trigger_task/input` |
| POST | `/lms/trigger_task/cancel/{triggerTaskId}` |
| GET | `/lms/trigger_task/list` |

本工程默认走 **模板下发**，不是触发器。

---

## 7. 推荐调用流（与 workflow 一致）

```text
设备 TCP: CALL {template_id, robot_id, point_id}
  → POST /login（若无 token）
  → POST /system/task/template
  → 循环 GET /system/robot/status/{robot_id}
       直到 state∈accept_states 且 currentPoint≈point_id
  → TCP 回发 ARRIVE_OK
设备 TCP: DONE
  → POST /system/task/template（离开模板）
  → 可选再等到 state∈idle_states
```

`dry_run = true` 时不真正 POST，只打日志。

---

## 8. 不要用这些做主路径

| 类型 | 原因 |
|------|------|
| 浏览器 / Selenium | 网页只是同一套 API |
| `/gesac/mes/*` | 多为 RCS↔MES；控自己的车用 `/system/task/*` |
| `/system/robot/command` 等底层命令 | 需协议细节；业务优先任务 API |
| 通读 `default.md` | 26 万行 schema 膨胀；当索引即可 |

---

## 9. 相关文件

| 文件 | 作用 |
|------|------|
| `docs/RCS_INTEGRATION_GUIDE.md` | 完整对接背景与控制器索引 |
| `docs/RCS_API_MINIMAL.md` | **本文**：工程最小接口 |
| `default.md` | Swagger 全文（查缺补漏） |
| `config/config.example.config` | 路径与 payload 模板 |
| `docs/examples/*` | HAR / 部门车列表 / 字典样例 |
