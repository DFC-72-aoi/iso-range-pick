# iso-range-pick

**从远程 ISO / UDF 镜像里只取需要的几个文件，不下载整个镜像。**

一个 6.71 GiB 的官方镜像，如果只需要其中 3 个 CAB（约 83 MiB），没必要整包下下来。
本项目把远端大文件当成一个可随机读的本地文件：先用 HTTP 字节范围请求（Range）读它的
目录索引，拿到目标文件的精确偏移和长度，再只对这几个区间发请求，多路并行写盘。

---

## 这个仓库解决什么

两条路径，按需选一条：

- **只想修输入法**：直接看 [完整案例：香港繁中输入法离线安装](docs/01-case-hk-ime.md)，
  照着做即可，不必理解工具实现。
- **想用工具本身**：`tools/iso_pick.py` 是一个通用命令行，支持列出镜像内容、
  按通配符取文件、一键组装 FOD 离线仓库。

首个真实案例是 Windows 11 23H2 上「繁体中文（香港特别行政区）基本键入」的安装，
它同时验证了这个工具不是玩具：从解析 UDF 目录到 CBS 最终 `S_OK`，全链路跑通。

---

## 快速开始

环境：Python 3.11+，装一个依赖：

```bash
python -m pip install pycdlib
```

列出镜像里有哪些文件（只读元数据，通常十几 MiB）：

```bash
python tools/iso_pick.py list <ISO_URL> --filter "*Basic-zh-HK*"
```

只取需要的文件：

```bash
python tools/iso_pick.py fetch <ISO_URL> \
    --pattern "Microsoft-Windows-LanguageFeatures-Basic-zh-HK-Package~*.cab" \
    --out ./fod-repo
```

一键组装可直接给 DISM 用的 FOD 仓库（自动包含主元数据 CAB 与全部 CompDB）：

```bash
python tools/iso_pick.py repo <ISO_URL> --language zh-HK --fonts Hant --out ./fod-repo
```

然后在**管理员** PowerShell 里离线安装（`/LimitAccess` 禁止联系 Windows Update）：

```powershell
.\tools\install_fod.ps1 -Repo .\fod-repo `
    -Capability "Language.Basic~~~zh-HK~0.0.1.0","Language.Fonts.Hant~~~und-Hant~0.0.1.0"
```

完整可复现配置见 [`examples/zh-hk-23h2.toml`](examples/zh-hk-23h2.toml)。

---

## 适用范围与边界

**适用**

- 服务器返回 `Accept-Ranges: bytes` 且对 Range 请求回 `206 Partial Content`；
- 容器格式带可随机访问的索引：ISO9660 / UDF（本项目已实现）、ZIP（`remotezip` 等已有成熟实现）、
  WIM / ESD、VHD / VHDX、Parquet / HDF5 / Zarr / COG 等为 Range 读取而设计的格式；
- 带宽受限、按流量计费、硬盘空间小、CI 里只需镜像中一个驱动或固件、取证只提一个 hive；
- 多连接并行可以显著提速（本案例从单连接约 47 KiB/s 提升到 8 路约 650–800 KiB/s）。

**不适用**

- 纯流式压缩（gzip / tar.gz / xz）没有中央索引，无法随机定位；
- 服务器不支持 Range，或代理截断大 Range（本案例中本地代理在 4 MiB 请求上直接超时，
  直连正常）；
- 高频小 Range 请求对服务端不友好，请复用连接、缓存元数据、控制并发。

**这个思想不新**。`remotezip`、`unzip-http`、`http-range-reader`、`fsspec` 系列都做远程
ZIP 单文件提取；Go 的 `ejfkdev/udf` 能解析 UDF / WIM / VHD 等大量镜像格式，但它处理的是
**本地文件**。本项目的具体组合是「远程 UDF 目录解析 + 微软 FOD 仓库语义」，
重点也在于把踩过的坑写清楚（见排障手册）。

---

## 法律与许可声明

> **本仓库不包含、也不分发任何微软二进制文件（`.cab` / `.iso` / `.msu` / `.wim`）。**

FOD 介质是微软面向 OEM、批量授权与 Visual Studio 订阅等渠道提供的软件，
微软软件许可条款禁止「共享、发布、分发本软件」。本仓库只提供**方法、脚本与文档**，
使用者需自行从微软官方渠道获取介质，并遵守适用许可。`.gitignore` 已排除相关二进制。

脚本与文档采用 [MIT 许可](LICENSE)。案例中的链接来自微软公开文档，可能随版本更新失效，
请以官方文档为准。

**企业环境**：本项目不提供绕过 IT 管理策略（WSUS / MDM / 组策略）的做法。
离线仓库方案本身就是在「不改策略」的前提下完成的；在受管设备上请先咨询管理员。

---

## 文档

| 文档 | 内容 |
|---|---|
| [完整案例：香港繁中输入法离线安装](docs/01-case-hk-ime.md) | 从 `0x80073D01` 到 `S_OK` 的完整排障复现 |
| [错误码对照表](docs/02-error-codes.md) | `0x80073D01` / `0x800F800B` / `0x80072EE6` 等 |
| [概念手册](docs/03-concepts.md) | FoD、capability、LXP、WSUS、组策略、`/LimitAccess` 是什么 |
| [参与改进](CONTRIBUTING.md) | 如何反馈问题、补充案例，以及不提交微软二进制的红线 |

---

## 目录结构

```
tools/iso_pick.py       通用命令行：list / fetch / repo
tools/install_fod.ps1   通用离线安装脚本（参数化，无硬编码路径）
examples/              可复现的案例配置
docs/                  排障手册、错误码、概念手册
.gitattributes         统一 LF 换行，避免跨平台整文件差异
.gitignore             排除微软二进制、缓存与日志
LICENSE                MIT
```
