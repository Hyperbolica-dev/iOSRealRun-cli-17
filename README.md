# iOSRealRun-cli-17

基于 `pymobiledevice3` 的 iOS 17+ 路线定位模拟工具，支持 CLI 和本地 Web UI。

## 环境与安装

- Python 3.12
- USB 连接并已信任此电脑的 iPhone
- iOS 17+，已开启 Developer Mode

```shell
uv sync
```

## CLI

使用默认路线 `HNroute.txt`：

```shell
uv run iosrealrun
```

常用选项：

```shell
uv run iosrealrun --help
uv run iosrealrun --udid <UDID>
uv run iosrealrun --route <路线文件>
uv run iosrealrun --diagnostic
uv run iosrealrun --dry-run
uv run iosrealrun --list-routes
```

`--diagnostic` 只检查连接；`--dry-run` 建立完整连接后退出。默认路线、文件格式、插值和移动语义保持兼容。

## Web UI

```shell
uv run iosrealrun-ui
```

默认地址：<http://127.0.0.1:17865>。可用 `--port PORT` 修改。默认只监听本机；`--host` 暴露到其他地址时没有认证保护。

Web UI 支持：

- 选择设备、查看系统版本、Developer Mode 和隧道后端
- 地图编辑、导入、自动保存、下拉框加载和导出路线
- 设置速度、开始/停止模拟和查看状态

路线和模拟定位统一使用 WGS-84；该路径已通过实体 iPhone 模拟定位验证。导入坐标格式只影响导入：默认 WGS-84，也可选择旧版 iOSRealRun（BD-09）或 GCJ-02；导入后会转换为 WGS-84 并自动保存。导出始终使用当前 WGS-84 路线。

内置 `HNroute.txt` 仍按旧版 BD-09 解释，加载到 Web UI 时只转换一次。旧 CLI 路径保持原有 BD-09→WGS-84 行为。

## 隧道兼容性

| 主机 / iOS | 后端 | 权限 | 验证状态 |
| --- | --- | --- | --- |
| Linux / iOS 17.4+ | userspace / CoreDeviceProxy | 不需要 root | iOS 18.6.2 实体设备验证 |
| Linux / iOS 17.0–17.3.1 | `tunneld` / RSD | 需要 root 启动 daemon | 自动化测试和 mock |
| macOS / iOS 17.0–17.3.1 | native / remoted | 不需要 root | 自动化测试和 mock |
| Windows / iOS 17.0–17.3.1 | `tunneld` / RSD | 需要管理员权限 | 自动化测试和 mock |
| macOS / Windows / iOS 17.4+ | userspace / CoreDeviceProxy | 通常不需要提权 | 自动化测试和 mock |

Linux 或 Windows 上的旧 iOS 17 需要先启动：

```shell
sudo uv run python -m pymobiledevice3 remote tunneld
uv run iosrealrun --diagnostic
```

程序不会自行提权或启动 `tunneld`；诊断日志会报告最终后端。

## 导入与导出路线

Web UI 支持现有 iOSRealRun 文本路线格式。导入时请选择正确的坐标格式；文件会安全命名并自动保存到应用路线目录，重名时生成 `-2`、`-3` 等名称。导入后可继续编辑。

导出使用当前编辑中的路线，不要求先保存；未保存路线默认文件名为 `route.txt`。

## 开发验证

```shell
uv run pytest -q
uv run ruff check .
uv run python -m compileall -q iosrealrun driver init util tests main.py run.py config.py
```
