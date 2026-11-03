# RCS 路由配置编辑器

打包装成 exe 后，不想每次用记事本改 `config/*.config` 时，用这个小工具改 **小车绑定** 和 **CALL / LEAVE 模板路由**。

其余节（`[rcs]`、`[tcp]`、`[task]`…）原样保留。

## 启动

在工程根目录：

```bash
python config_editor/main.py
```

或双击同目录下的 `打开配置编辑器.bat`。

默认优先打开 `config/config.config`；没有则打开 `config/config.example.config`。建议现场另存为 `config.config`。

## 页签

| 页签 | 对应节 | 用途 |
|------|--------|------|
| RCS 登录 | `[rcs]` | `base_url` / `username` / `password`（保留原有 `${ENV:默认值}` 包装） |
| 小车绑定 | `[robots]` | 短码 → robotId / 名称 / 停靠点 / 地图 |
| CALL 路由 | `[call_routes]` | `robot:start_point_id` → 去站台模板 |
| LEAVE 路由 | `[leave_routes]` | `robot:start:end` → 去终点模板 |
| 搜索 | — | 按关键词检索上述配置；双击结果可跳转到对应页 |

快捷键：`Ctrl+F` 打开搜索页。

TCP 第一次报文里的 `robot` / `start_point_id` / `end_point_id` 靠路由表解析出 `template_id`。

## 打包成独立 exe（可选）

工程已装 Python 时：

```bash
pip install pyinstaller
pyinstaller --noconfirm --windowed --name RCSConfigEditor config_editor/main.py
```

把生成的 exe 和 `config/` 放在同一级目录使用（或启动后点「打开」选配置文件）。
