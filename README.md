# iOSRealRun-cli-17

## 用法简介

### 安装

本 fork 使用 `uv` 管理运行环境和开发依赖：

```shell
uv sync
```

配置文件和默认路线会作为包资源安装，因此从其他工作目录运行也会使用确定的默认资源。个人路线文件不会随包发布；`--route` 可以接受任意用户路径。

### 运行

```shell
uv run iosrealrun --help
uv run iosrealrun --diagnostic
uv run iosrealrun --route <path>
```

默认路线仍然是 `HNroute.txt`，路线格式、插值和移动语义保持不变。也可以使用 `--udid <UDID>` 指定设备。

`--diagnostic` 会检查设备、Developer Mode、tunnel、RSD 和 DVT，但不启动路线模拟；`--dry-run` 也会建立连接后退出，并显示独立的 dry-run 成功提示。

如果项目中存在可选的 `routes/` 目录，可以列出其中的路线文件：

```shell
uv run iosrealrun --list-routes
```

### 本地 Web UI

启动本地 Web UI，然后打开程序打印的 localhost 地址：

```shell
uv sync
uv run iosrealrun-ui
```

默认只监听 `127.0.0.1:8765`，不会暴露到局域网。连接 USB 设备并信任此电脑，开启 Developer Mode，在页面中选择设备、编辑路线、设置速度后点击 Start；停止前请尽量点击 Stop，程序也会在取消或退出时尝试清除模拟定位。

Linux 上 iOS 17.4+ 的 userspace/CoreDeviceProxy 路径不需要 root；iOS 17.0–17.3.1 仍需要先运行 privileged `tunneld`。

### 平台和 iOS 17 tunnel 支持矩阵

| 主机 / iOS | 后端 | 权限 | 验证状态 |
| --- | --- | --- | --- |
| Linux / 17.4 及以上 | userspace / CoreDeviceProxy | 不需要 root | iOS 18.6.2 已完成实体设备验证 |
| Linux / 17.0–17.3.1 | `tunneld` / RSD | 需要 root 启动 daemon | 已通过自动化测试和 mock 验证；尚未完成实体设备验证 |
| macOS / 17.0–17.3.1 | native / remoted | 不需要 root | 已通过自动化测试和 mock 验证；尚未完成实体设备验证 |
| Windows / 17.0–17.3.1 | `tunneld` / RSD | 需要管理员权限启动 daemon | 已通过自动化测试和 mock 验证；尚未完成实体设备验证 |
| macOS 或 Windows / 17.4 及以上 | userspace / CoreDeviceProxy | 通常不需要 root 或管理员权限 | 已通过自动化测试和 mock 验证；尚未完成实体设备验证 |

Linux 或 Windows 上的 iOS 17.0–17.3.1 需要先在另一个终端启动 privileged `tunneld`：

```shell
sudo uv run python -m pymobiledevice3 remote tunneld
uv run iosrealrun --diagnostic
```

程序不会自行提权或启动 daemon。Linux/Windows 上如果 userspace 能力探测失败，会查询 `tunneld`；macOS 上则使用 pymobiledevice3 的 native/remoted 后端。诊断日志会报告最终选择的 backend。

### 开发

```shell
uv run pytest -q
uv run ruff check .
```

本 fork 不再维护单独的 `requirements.txt`；`pyproject.toml` 是唯一依赖来源，`uv.lock` 用于复现开发环境。
