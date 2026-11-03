# HAR 结论：小车列表与在线状态（191.13.16.115）

来源：`191.13.16.115.har`

## 关键结论

| 问题 | 答案 |
|------|------|
| `/system/robot/list` 有没有 `online`？ | **没有**。只有配置项 + `state` |
| 列表里的 `state` 是什么？ | **启用/停用**，不是行驶状态。字典 `sys_robot_state`：`0=启用`，`2=停用`，`4=故障` |
| 网页「在线」从哪来？ | `robotStatusVo.online`。前端：`robotStatusVo && robotStatusVo.online` 才显示在线图标 |
| 实时数据通道 | STOMP：`ws://…/prod-api/stomp`，订阅 `/all/status`（地图）或 `/topic/{robotUuid}/status`（单车） |
| HTTP 可查运行态 | `GET /system/robot/dept/{deptId}`，每项含 `sysRobot` + `robotStatusVo` |
| 本次 HAR 里在线情况 | 三台车 `robotStatusVo` 均为 `null` → 按网页逻辑视为 **离线** |

## 相关接口

- `GET /prod-api/system/robot/list`
- `GET /prod-api/system/robot/dept/100`
- `GET /prod-api/system/dict/data/type/sys_robot_state`
- `GET /prod-api/system/robot/{robotId}`
- `WS /prod-api/stomp` → `SUBSCRIBE /all/status`
