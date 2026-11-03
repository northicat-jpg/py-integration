# RCS Python 集成目录

TCP 收设备信号 → 解析 → 下发任务 → 轮询小车状态到点 → 回发设备 → 再收信号 → 驶离。

可在配置里关闭握手，退回「一报文一任务」。

## 配置格式

INI 风格 **`.config`**：

| 节 | 含义 |
|----|------|
| `[rcs]` | 后端登录与 HTTP |
| `[tcp]` | 本机监听设备 TCP |
| `[parser]` | 报文解析 |
| `[task]` / `[task_leave]` | 去点 / 离开任务请求体 |
| `[workflow]` | 握手开关与信号字段 |
| `[poll]` | 到点/空闲轮询 |
| `[notify]` | 到点后通知设备 |
| `[runtime]` | dry_run 等 |

## 快速开始

```bash
cd d:\dhrcs\py-integration
python -m pip install -r requirements.txt

copy config\config.example.config config\config.config
# 编辑 payload、robot/point 字段路径；联调前保持 dry_run = true

# 握手演练（CALL + DONE 两行）
python main.py --once @docs/examples/handshake_once.txt

python main.py
```


也可用 `.env` 提供 `${RCS_BASE_URL}` 等占位符（见 `.env.example`）。

## 目录

| 路径 | 说明 |
|------|------|
| `main.py` | 入口：`python main.py` |
| `config/config.example.config` | 环境 + TCP 解析 + 任务模板 |
| `config_editor/` | 小工具：可视化改 `[robots]` / CALL·LEAVE 路由（免手改 config） |
| `build_exe.bat` | 一键打成 `dist/RCSBridge.exe` + `dist/RCSConfigEditor.exe` |
| `dist/` | 发布目录（exe + `config/`，见其中说明） |
| `src/bridge.py` | TCP 服务 / `--once` |
| `src/tcp_parser.py` | 成帧与声明式解析 |
| `src/rcs_client.py` | 登录 + 任务 POST |
| `src/config_loader.py` | 加载 `.config`、展开 `${ENV}` |
| `docs/RCS_API_MINIMAL.md` | **对接最小接口清单**（登录/下发/轮询） |
| `docs/RCS_INTEGRATION_GUIDE.md` | 后端接口总览与背景 |
| `docs/examples/tcp_message_samples.md` | 多种报文配置示例 |

## 给大模型

写本目录代码前阅读 `docs/RCS_API_MINIMAL.md`、`docs/RCS_INTEGRATION_GUIDE.md` 与 `AGENTS.md`。控车只调后端 API，不操作浏览器。
