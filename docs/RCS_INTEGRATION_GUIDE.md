# RCS 对接参考（Python / 外部系统 / 大模型）

> 来源：从部署包 `rcs-admin-gesac.jar`、模块 JAR、前端 `dist` source map、`docker-compose` / nginx 逆向整理。  
> **不是官方 OpenAPI 全文**；字段级细节请以浏览器抓包或 Swagger 为准。  
> 生成日期参考上下文：2026-10。

---

## 1. 系统是什么

东晖 RCS（Robot Control System / 智能机器人调度系统），技术栈接近 **若依 RuoYi-Vue-Plus**：

| 层 | 技术 |
|----|------|
| 后端 | Java 8、Spring Boot 2.3、Undertow、Spring Security + JWT、MyBatis-Plus |
| 前端 | Vue 2、Element UI、Axios、STOMP WebSocket |
| 数据 | MySQL（库名 `rcs`）、Redis |
| 设备通信 | MQTT（Eclipse Paho）、机器人/充电桩等 Communication 接口、WebSocket/STOMP |

当前工作区多为 **部署包**（JAR + `dist` + Docker），不是完整源码仓库。Python 集成 **只依赖 HTTP/MQTT 等运行时接口**，不依赖改 JAR。

---

## 2. 部署拓扑与访问地址

### 2.1 Docker 服务（`东晖rcs部署/rcs/docker-compose.yml`）

| 服务 | 作用 | 常见端口 |
|------|------|----------|
| `rcs-admin` | 后端 JAR | **8888** |
| `rcs-ui` | 前端静态 + nginx | 容器内 80 |
| `gateway` | 对外入口 | **9080** HTTP / **9843** HTTPS |
| `mysql` | 数据库 | 3306 |
| `redissrv` | Redis | 6379（内网） |
| `phpmyadmin` | DB 管理 | 9090 |

### 2.2 Python 应连哪里

优先直连后端（内网）：

```text
RCS_BASE_URL = http://<rcs-host>:8888
```

若必须走网关/前端代理（与浏览器一致）：

```text
RCS_BASE_URL = http://<gateway-or-ui-host>:9080/prod-api
```

说明：

- 浏览器里 Axios `baseURL` 为 `/prod-api`，nginx 会去掉前缀再转到后端。
- **直连 8888 时不要加 `/prod-api` 前缀。**
- 现网 `rcs-ui/nginx.conf` 可能把 `/prod-api/` 指到固定 IP（如 `191.13.14.175:8888`），换环境需改代理。

### 2.3 实时通道

| 通道 | 路径/配置 | 用途 |
|------|-----------|------|
| STOMP/WebSocket | `/stomp`（网关常代理 `/prod-api/stomp`） | 网页收机器人状态等 |
| MQTT | `mqtt.url`（例：`tcp://10.30.150.80:1883`） | 设备侧发布/订阅；`mqtt.enable` |

Python 控车 **不必须** 连 STOMP；下发任务用 HTTP 即可。网页会自己收到状态更新。

---

## 3. 鉴权（必读）

### 3.1 机制

- JWT；请求头：`Authorization: Bearer <token>`
- 配置键：`token.header=Authorization`，`token.expireTime`（分钟，常见 1440）
- 登录成功后响应体中带 `token` 字段（若依惯例；若字段名不同以实际 JSON 为准）

### 3.2 登录接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/login` | 标准登录（`LoginBody`） |
| POST | `/login/simple` | 简化登录 |
| POST | `/login/pcm` | PCM 场景登录 |
| GET | `/captchaImage` | 验证码（匿名） |

`LoginBody` 常见字段：`username`、`password`；若开启验证码还有 `code`、`uuid`。

配置中 `captcha.enabled` 可为 `false`，关闭后登录可不带验证码。

**安全配置中明确匿名放行的路径包括（节选）：**

- `/login`、`/login/simple`、`/login/pcm`
- `/captchaImage`
- `/stomp`
- `/swagger-ui.html`、`/swagger-resources/**`、`/*/api-docs`
- `/druid/**`、`/jmreport/**`
- 静态资源 `/**/*.js` 等

其余业务接口（含任务下发）**通常需要登录后的 Token**。

### 3.3 Python 伪代码

```python
import requests

BASE = "http://127.0.0.1:8888"  # 或带 /prod-api 的网关地址

def login(username: str, password: str) -> str:
    r = requests.post(f"{BASE}/login", json={
        "username": username,
        "password": password,
    }, timeout=30)
    r.raise_for_status()
    data = r.json()
    # 若依常见：{"code":200,"token":"..."} 或 token 在 data 内
    token = data.get("token") or (data.get("data") or {}).get("token")
    if not token:
        raise RuntimeError(f"login failed: {data}")
    return token

def api_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
```

建议：Token 缓存到过期前；收到 401 再重新登录。

---

## 4. 统一响应格式

若依风格：

```json
{
  "code": 200,
  "msg": "操作成功",
  "data": {}
}
```

- `code == 200` 通常表示成功（以现场为准）。
- 分页列表多为 `TableDataInfo`：`rows` + `total`。

---

## 5. 任务创建与下发（控车核心）

控制器：`SysTaskController`，前缀 **`/system/task`**。

### 5.1 下发相关

| 方法 | 路径 | 作用 | 请求体类型（类名） |
|------|------|------|-------------------|
| POST | `/system/task/submit` | 提交任务（含步骤） | `SysTaskSubmitBo` |
| POST | `/system/task/temp` | 临时任务 | `SysTempTaskAddBo` |
| POST | `/system/task/template` | **按模板列表下发**（外部对接优先尝试） | `SysTemplateListTaskBo` |
| POST | `/system/task/cancel` | 取消 | `Map` |
| POST | `/system/task/pause` | 暂停 | `Map` |
| POST | `/system/task/finish` | 完成 | `Map` |
| POST | `/system/task/history` | 历史相关提交 | `Map` |

### 5.2 查询相关

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/system/task/list` | 任务分页列表 |
| GET | `/system/task/executing` | 执行中任务 |
| GET | `/system/task/entryTask` | 入口/队列类任务查询 |
| GET | `/system/task/{taskId}` | 详情 |
| GET | `/system/task/utilization` | 利用率 |
| GET | `/system/task/export` | 导出 |

### 5.3 请求体结构（从 class 反推）

**`SysTemplateListTaskBo`（推荐）**

```json
{
  "task": { },
  "templateList": [1, 2]
}
```

- `task`：`SysTaskVo` / 任务头信息（地图、优先级、指定车等，以抓包为准）
- `templateList`：模板主键 ID 列表（须为系统已配置模板）

**`SysTaskSubmitBo`**

```json
{
  "task": { },
  "stepList": [ ]
}
```

- `stepList` 元素类型：`SysStepSubmitVo`

**`SysTempTaskAddBo`**

```json
{
  "task": { },
  "stepList": [ ]
}
```

> **重要：** 具体字段名（如 `mapId`、`robotId`、`priority`）请在浏览器打开 RCS 网页，手动下发一次任务，在 Network 里复制 Request Payload，作为 Python 的金标准。

### 5.4 外部信号 → 下发任务（推荐架构）

```text
TCP/MQTT/HTTP 收到信号
  → 解析出：模板ID / 起终点点位 / 优先级 …
  → login() 获取 token
  → POST /system/task/template 或 /submit 或 /temp
  → 检查 code==200
  → （可选）轮询 /system/task/{id} 或 /executing
```

**不要**用 Selenium 点网页；网页只是同一套 API 的 UI。

---

## 6. LMS 触发 / 托盘任务（信号型对接）

适合“外部 IO / 触发器”场景。

### 6.1 触发任务 `LmsTriggerTaskController` → `/lms/trigger_task`

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/lms/trigger_task/input` | 输入触发值（`LmsTriggerTaskEditBo`） |
| POST | `/lms/trigger_task/trigger/{triggerTaskId}` | 触发指定任务 |
| POST | `/lms/trigger_task/cancel/{triggerTaskId}` | 取消 |
| GET | `/lms/trigger_task/list` | 列表 |
| GET | `/lms/trigger_task/listTriggerTask` | 触发任务列表（另一查询） |
| POST | `/lms/trigger_task` | 新增 |
| PUT | `/lms/trigger_task` | 编辑 |
| PUT/POST | `/lms/trigger_task/stateAllChange` | 状态批量变更 |
| DELETE | `/lms/trigger_task/{triggerTaskIds}` | 删除 |

### 6.2 托盘任务 `LmsPalletTaskController` → `/lms/pallet_task`

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/lms/pallet_task/submit` | 提交 |
| POST | `/lms/pallet_task/cancel/{palletTaskId}` | 取消 |
| GET | `/lms/pallet_task/list` | 列表 |
| POST | `/lms/pallet_task` | 新增 |
| PUT | `/lms/pallet_task` | 编辑 |

---

## 7. 机器人与设备通信接口

### 7.1 机器人 `RobotCommunicationController`（前缀与 `/system/robot` 相关）

常见路径（从 class 字符串提取）：

| 方法倾向 | 路径 | 作用 |
|----------|------|------|
| 上报 | `/system/robot/status` | 状态上报（车→RCS） |
| 上报 | `/system/robot/message` | 消息上报 |
| 下发 | `/system/robot/command` | 命令 |
| | `/system/robot/action` | 动作 |
| | `/system/robot/idle/{robotId}` | 空闲 |
| | `/system/robot/continueMove/{robotId}` | 继续移动 |
| | `/system/robot/pass/{robotId}` | 放行 |
| | `/system/robot/mode/{robotId}/{ms}` | 模式 |
| | `/system/robot/release/{robotId}` | 释放 |
| | `/system/robot/cancel/{robotId}` | 取消任务 |
| | `/system/robot/switchMap` | 切图 |
| | `/system/robot/relocation` | 重定位 |
| | `/system/robot/charge` / `/uncharge` | 充/断电 |

调度侧还有 `RobotCommandDispatcher`：`sendCommandToRobot`、`sendRoutesToRobot` 等（内部，非给 Python 直调优先）。

### 7.2 机器人 CRUD / 查询 `SysRobotController` → `/system/robot`

| 路径 | 作用 |
|------|------|
| GET `/system/robot/list` | 列表 |
| GET `/system/robot/{robotId}` | 详情 |
| GET `/system/robot/status/{robotId}` | 状态 |
| GET `/system/robot/dept/{deptId}` | 按部门 |
| GET `/system/robot/pointresource/list` | 点资源 |
| POST/PUT/DELETE | 增改删（标准若依） |

### 7.3 充电桩等

| 前缀 | 说明 |
|------|------|
| `/wcs/charger` | 充电桩管理；另有 `ChargerCommunicationController`：`/charger/status`、`/wcs/charger/command` 等（以抓包为准） |
| `/wcs/lifter` | 提升机 |
| `/wcs/robotArm` 等 | 机械臂 |

Python **控业务任务**优先走 `/system/task/*`；直接 `/command` 偏底层，需清楚协议。

---

## 8. 地图 / 点位 / 模板（组任务时常查）

| 前缀 | 常用路径 | 用途 |
|------|----------|------|
| `/system/map` | `/list`, `/{mapId}`, 上传/刷新等 | 地图 |
| `/system/point` | `/list`, `/{pointId}`, `/checkPoint/{mapId}`, `/occupy`, `/release` | 点位 |
| `/system/template` | `/list`, `/{templateId}`, CRUD | 任务模板（下发前查 ID） |
| `/system/path` | CRUD | 路径 |
| `/system/region` | CRUD | 区域 |
| `/system/step` | CRUD | 步骤定义 |

外部系统应使用 **系统内已有的 templateId / pointId / mapId**，不要臆造坐标协议。

---

## 9. Gesac MES 相关（本 JAR 客户化）

前缀：`/gesac/mes`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/gesac/mes/agv/move` | 封装 MES AGV 移动相关；内部常 **RCS→MES** 调 `WebClient` |
| GET | `/gesac/mes/order/detail` | 订单详情 |
| GET | `/gesac/mes/inventory/location` | 库位库存 |
| GET | `/gesac/mes/notice/detail` | 通知 |
| GET | `/gesac/mes/extrusionOrderBom` | 挤出订单 BOM |
| GET/POST | `/gesac/mes/currentMaterial` | ERP 当前物料 |

配置示例键（`application.yml`）：

```yaml
mes:
  base-url: http://<mes-host>:8000
  auth:
    username: <mes-user>
  redis:
    token-key: mes:token
    timeout: 7200
  instock: true
```

注意：`GesacMesApiService.doMesAgvMove` 会 POST 到 MES 的 `/Wms/BizItemBatch/AgvMove`。  
**若目标是“外部信号 → RCS 派自己的小车”，优先用 `/system/task/*`，不要误用成只调 MES 的接口。**

---

## 10. MQTT（可选）

`application.yml`：

```yaml
mqtt:
  enable: true          # 或 false
  url: tcp://<host>:1883
  username: admin
  password: <secret>
  clientid: rcs
  timeout: 5000
  keepalive: 60
  laser: false
```

后端类：`MqttClientService`（`publish` / `subscribe`）、`MqttClientHandler.messageArrived`。

Python 可以：

- **A.** 自己监听外部 TCP，再 HTTP 调 RCS（推荐、简单）
- **B.** 与 RCS 共用 MQTT Broker，收同一类 topic（需摸清 topic 命名，部署包中未必明文列出全部 topic）

---

## 11. 配置清单（对接常用）

| 配置项 | 含义 | 示例/默认 |
|--------|------|-----------|
| `server.port` | 后端端口 | `8888`（dev profile 可能 `18080`） |
| `spring.datasource.druid.master.url` | MySQL | `jdbc:mysql://mysql:3306/rcs?...` |
| `spring.redis.host` | Redis | `redissrv` / `127.0.0.1` |
| `token.header` | Token 头 | `Authorization` |
| `token.expireTime` | 有效期（分钟） | `1440` |
| `captcha.enabled` | 验证码 | 可为 `false` |
| `mqtt.*` | MQTT | 见上 |
| `swagger.enabled` | 接口文档 | `true` |
| `swagger.pathMapping` | 文档前缀提示 | `/dev-api`（与生产 `/prod-api` 习惯不同） |
| `mes.base-url` | MES 地址 | 客户环境相关 |

Swagger UI（若未关防火墙）：

```text
http://<rcs-host>:8888/swagger-ui.html
```

---

## 12. 模块边界（便于判断接口归属）

| 模块 JAR | 内容 |
|----------|------|
| `rcs-system` | 任务、机器人、地图、点位、模板、LMS location、命令/事件 |
| `rcs-wcs` | 充电桩、提升机、机械臂、MQTT、trigger/pallet task |
| `rcs-core` / web | 登录、用户、菜单、字典等 |
| `rcs-framework` | Security、WebSocket、Redis 等 |
| fat JAR 内 `gesac.mes` | Gesac 客户 MES/库存扩展 |

---

## 13. Python 目录约定（本仓库）

```text
py-integration/
  main.py
  config/config.example.config    ← INI 风格主配置
  docs/RCS_INTEGRATION_GUIDE.md   ← 本文件
  .env.example
  requirements.txt
  src/
```

主配置为 **`.config`（INI）**：`[rcs]` / `[tcp]` / `[parser]` / `[task]` / `[runtime]`。  
复杂 JSON 放在 `payload_template = {...}`；类型转换用 `cast.template_id = int`。  
也可用 `.env` 注入 `${RCS_BASE_URL}` 等。

---

## 14. 给大模型的实现约束

1. **控车 = 调后端 HTTP API**，不要操作浏览器 DOM。  
2. 所有业务请求带 `Authorization: Bearer <token>`。  
3. 下发任务优先 `POST /system/task/template`；字段以用户提供的抓包 JSON 为准。  
4. `templateId` / `pointId` / `mapId` 必须来自现场系统数据。  
5. 区分：  
   - `/system/task/*`：RCS 内建任务调度  
   - `/lms/trigger_task/*`：触发器任务  
   - `/gesac/mes/*`：MES 集成（部分是 RCS 调外部）  
6. 直连 `8888` 与走 `/prod-api` **二选一**，不要重复加前缀。  
7. 不要把生产密码写进源码；用 `.env`。  
8. 若接口 401/403：检查账号权限与菜单权限（若依 `sys_menu` 权限字）。  
9. 不确定的 payload：先让用户从浏览器 Network 复制，再生成代码。

---

## 15. 建议的下一步（标准做法）

1. 浏览器登录 RCS → 手动下发一笔任务 → 导出 `submit`/`template` 的 Request/Response。  
2. 把该 JSON 样例存到 `docs/examples/`（可后续添加）。  
3. 在 `src/` 实现：`tcp_listener.py` + `rcs_client.py`。  
4. 有条件时打开 Swagger，用 OpenAPI 补全字段类型。  
5. 联调环境使用专用账号，限制网络只通内网 RCS。

---

## 16. 控制器索引（便于检索）

调度相关：`SysTaskController`, `SysTemplateController`, `SysRobotController`, `RobotCommunicationController`, `RobotStatusController`, `LmsTriggerTaskController`, `LmsPalletTaskController`, `SysMapController`, `SysPointController`, `SysBillController`, `SysDispatchStrategyController` …

设备：`WcsChargerController`, `WcsLifterController`, `WcsRobotArmController`, `ChargerCommunicationController`, `LifterCommunicationController`, `RobotArmCommunicationController`, `MqttClientService` …

系统：`SysLoginController`, `SysUserController`, `SysMenuController`, `SysConfigController` …

客户：`GesacMesApiController`, `Gesac*Stock*Controller` …

完整类名包前缀：`cn.ac.casqiem.rcs.*`。
