# fn-r8125 — RTL8125 2.5G 网卡驱动管理工具

> 飞牛 NAS (fnOS) 第三方应用 | 一站式管理 RTL8125 网卡驱动

![应用图标](ICON_256.PNG)

## 功能

| 功能 | 说明 |
|------|------|
| 📦 **安装驱动** | 自动安装编译环境 → 从社区 DKMS 或 Realtek 官方源码编译驱动 → 持久化配置 |
| ✅ **验证安装** | 6 项全面检查：PCI 设备识别、内核模块状态、驱动绑定、网络接口、连接速度、驱动版本 |
| ⚙️ **节能优化** | 关闭 EEE（节能以太网）、WOL（网络唤醒），增大 Ring Buffer，写入 systemd 持久化 |

## 适用场景

- 飞牛 NAS 安装后 RTL8125 2.5G 网卡不被识别
- 系统内核较新（如 `6.18.18-trim`），官方驱动不兼容
- r8169 驱动与 r8125 冲突，导致网卡不工作
- 网卡速度显示 100Mbps 而非 2500Mbps
- 需要关闭网卡节能特性降低网络延迟

## 快速开始

### 前置条件

- 飞牛 NAS (fnOS) x86_64 架构
- 应用商店已安装 `python3`（系统自带）

### 安装方式

#### 方式一：通过 FPK 包安装（推荐）

1. 在 [Releases](https://github.com/RROrg/fn-r8125/releases) 下载 `fn-r8125.fpk`
2. 打开飞牛 Web 管理 → **应用中心** → **手动安装** → 选择 `.fpk` 文件

#### 方式二：通过 SSH 命令行安装

```bash
# 上传 fpk 到 NAS，然后执行
sudo appcenter-cli install-fpk --volume 1 /path/to/fn-r8125.fpk
sudo appcenter-cli start fn-r8125
```

### 使用方法

安装后，在飞牛桌面点击 **RTL8125 驱动管理** 图标即可打开。

**建议操作顺序：**

1. **第一步** — 安装编译环境（等待 apt 下载完成，约 2-5 分钟）
2. **第二步** — 下载并编译安装 r8125 驱动
3. **第三步** — 持久化配置（屏蔽 r8169 冲突、更新 initramfs）
4. **第四步** — 验证驱动安装状态
5. **第五步（可选）** — 关闭网卡节能优化性能

> ⚠️ 前三步执行完后建议重启 NAS 确保所有配置生效

## 技术架构

```
用户浏览器                   飞牛 NAS
┌─────────┐    5666/443    ┌──────────────────────────────┐
│ 桌面 iframe │ ─────────→  │ nginx 反向代理               │
│           │              │  /app/r8125/ → 127.0.0.1:5300  │
└─────────┘              └──────────┬───────────────────┘
                                    │
                           ┌────────▼──────────┐
                           │  Python HTTP 服务   │
                           │  (127.0.0.1:5300)  │
                           │  SSE 流式输出      │
                           └────────┬──────────┘
                                    │
                           ┌────────▼──────────┐
                           │  Shell 命令执行    │
                           │  apt / git / make  │
                           │  modprobe / ethtool│
                           └───────────────────┘
```

- **后端**: Python 3 标准库 `http.server`，无外部依赖
- **前端**: 纯 HTML/CSS/JS，暗色主题终端风格
- **输出**: SSE (Server-Sent Events) 实时流式输出命令执行日志
- **安全**: 服务监听 `127.0.0.1`，通过 nginx 反向代理暴露

## 项目结构

```
fn-r8125/
├── manifest                 # 应用信息（名称、版本、开发者）
├── ICON.PNG / ICON_256.PNG  # 应用图标
├── app/
│   ├── server/server.py     # 后端服务（HTTP API + SSE 流式执行）
│   ├── www/
│   │   ├── index.html       # 前端主页面
│   │   ├── css/app.css      # 暗色主题样式
│   │   └── js/app.js        # 前端逻辑
│   └── ui/
│       ├── config           # 桌面入口配置（nginx 反向代理路径）
│       └── images/          # 入口图标
├── cmd/                     # 生命周期脚本（start/stop/install/uninstall）
├── config/                  # 权限（root）/ 资源配置
└── wizard/                  # 安装/卸载向导
```

## 开发

### 本地开发

```bash
# 需要 Python 3
cd fn-r8125-app
python3 app/server/server.py
# 访问 http://127.0.0.1:5300
```

### 打包

```bash
# 在飞牛 NAS 上执行
fnpack build
# 生成 fn-r8125.fpk
```

## nginx 反向代理配置

项目需要在飞牛 nginx 中增加反向代理规则（已包含在安装流程中）：

```nginx
location /app/r8125/ {
    proxy_pass http://127.0.0.1:5300/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 600s;
    proxy_buffering off;
}
```

## 常见问题

### 安装时 apt 卡住了怎么办？

apt 下载包需要时间，请等待 2-5 分钟。如果超过 10 分钟没反应，可以关闭页面重试，系统会自动清理残留的 apt 进程。

### 如何确认驱动安装成功？

在应用中点击 **第四步: 验证安装**，会显示完整的 6 项检查报告。如果 r8125 显示 ✓ 已加载，即表示成功。

### 端口 5300 是否安全？

服务只监听 `127.0.0.1`，外部无法直接访问。所有请求通过飞牛 nginx 的 `/app/r8125/` 路径转发，与飞牛 Web 管理共用 5666/443 端口。

## 许可证

MIT License

## 作者

**dalingo** 🐾 — 数字伙伴
