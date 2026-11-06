# 完整案例：Windows 11 香港繁中「基本键入」离线安装

> 目标：在**不修改 Windows 更新策略、不改注册表、不改语言列表**的前提下，
> 让「繁体中文（香港特别行政区）」能打出中文，且不下载 6.71 GiB 的完整介质。

## 0. 环境与症状

| 项 | 值 |
|---|---|
| 系统 | Windows 11 专业版 23H2，Build 22631.6199 |
| 语言列表 | `zh-Hant-HK`（带输入法 GUID）、`zh-Hans-CN` |
| 现象 | 设置页下载「繁体中文（香港特别行政区）」基本输入法报 `0x80073D01`；手动添加后 HK 语言只能打英文 |
| 更新策略 | `WUServer=127.0.0.1`、`UseWUServer=1`、`DisableWindowsUpdateAccess=1`、`DoNotConnectToWindowsUpdateInternetLocations=1` 等 |
| 家庭位置 / 地区格式 / 时区 | 香港特别行政区（GeoId 104）/ `en-US` / `China Standard Time`（UTC+8） |

`0x80073D01` 的微软官方定义是 `ERROR_DEPLOYMENT_BLOCKED_BY_POLICY`：
「原则會封鎖套件部署作業。請連絡您的系統管理員。」这是 **AppX / MSIX 部署被策略阻止**，
不是网络超时，也不是下载损坏。

> 一个重要事实：Windows 11 **没有独立的 `zh-HK` 完整语言包**，官方语言包列表里
> `zh-HK` 标注「No longer used. See zh-TW.」。但 FOD 介质里**存在**
> `Microsoft-Windows-LanguageFeatures-Basic-zh-hk-Package`，即香港专用的「基本键入」。
> 所以 HK 语言条目是能补上输入法的，不必把语言列表改成台湾。

## 1. 思路

设置页走的是 Windows Update → 被策略挡住 → 失败。
那就完全绕开在线通道：

1. 找到微软官方的 **Languages and Optional Features ISO**（微软 Learn 的 AVD 文档公开给出直链），
   确认它适用于 22H2 **和 23H2**；
2. 不下载整包，用 HTTP Range 只读它的 UDF 目录索引，定位所需 CAB 的精确偏移；
3. 只取回这几个 CAB，组装成 DISM 认可的 **FOD 仓库**；
4. 用 `DISM /Add-Capability /Source /LimitAccess` 离线安装。

## 2. 关键技术点（容易踩的坑）

### 2.1 真正的文件在 UDF，不在 ISO9660

ISO 的 bridge 区只有一个 `README.TXT`，写明：

```
This disc contains a "UDF" file system and requires an operating system
that supports the ISO-13346 "UDF" file system specification.
```

用 `list_dir(iso_path=...)` 只能看到 bridge；必须 `list_children(udf_path=...)`。

### 2.2 UDF 文件名是原始字节

`pycdlib` 的 `file_identifier()` 返回的是 **FID 里的原始字节**，压缩标识字节已被库剥掉，
编码记在 `entry.file_ident.encoding`（`latin-1` 或 `utf-16_be`）。按 UTF-8 解码会得到
`\x00L\x00a\x00n...` 这种乱码，导致 3216 个条目一个都匹配不上。

正确做法：

```python
def decode_name(entry):
    fi = entry.file_identifier()
    enc = getattr(getattr(entry, 'file_ident', None), 'encoding', None)
    for codec in (enc, 'utf-16_be', 'latin-1'):
        try:
            return fi.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
    return fi.decode('utf-8', 'replace')
```

### 2.3 文件偏移与长度

`entry.inode.fp_offset` 就是绝对字节偏移（= extent × 2048），
`entry.inode.get_data_length()` 是长度。有了这两个值就能直接发 Range 请求。

### 2.4 CAB 头部长度比文件短约 9.7 KB，是正常的

CAB 头 `cbCabinet` 只计数据部分；其后紧跟
`30 82 … 2a 86 48 86 f7 0d 01 07 02`（PKCS#7 `SignedData` OID），
是微软附在 CAB 尾部的**数字签名**。不要误判为「取错字节」。
本案例里 CBS 日志记录的哈希与本地 SHA256 完全一致，可交叉验证。

### 2.5 FOD 仓库必须元数据齐全

微软文档明确写：「不要手动把 `.cab` 复制到文件夹就当仓库用，DISM 需要仓库里的额外元数据。」

有效的 `LanguagesAndOptionalFeatures` 仓库结构：

```
fod-repo/
├── FoDMetadata_Client.cab                      # 主元数据（capability → package 映射）
├── Microsoft-Windows-LanguageFeatures-*.cab    # 实际的 FoD 包
└── metadata/
    └── DesktopTargetCompDBForISO_*.xml.cab     # 89 个 CompDB，共约 1.14 MiB
```

**只放 2 个 CompDB 会让 CBS 报 `0x800F800B` 并回退 WU。**
反正总量才 1.14 MiB，全放进去最省事。

### 2.6 `/LimitAccess` 不是可选项

微软文档：在线镜像执行 `/Add-Capability` 默认从 Windows Update 下载；
`/LimitAccess` 让 DISM「不检查 Windows Update 或 WSUS 获取功能源文件」。
社区经验也指出，即使给了 `-Source`，不加 `-LimitAccess` 仍可能去拨 WU。

另外 `/Add-Package` 装 FOD **已被微软标记为弃用**，推荐一律用 `/Add-Capability`。

### 2.7 代理可能截断大 Range

本案例中本地代理下 4 MiB 的 Range 请求直接 180 秒超时，
`--noproxy '*'` 直连正常。工具默认直连，需要时可用 `--proxy` 指定。

## 3. 完整时间线（含失败）

| 时间 | 动作 | 结果 |
|---|---|---|
| ① | 设置页下载 HK 基本输入法 | `0x80073D01` |
| ② | 从官方 ISO 精取 3 个 CAB，用 `/Add-Package` 安装 | 进度到 100% 后 `0x80072EE6` / DISM 退出码 12006 |
| ③ | 读 CBS 日志定位根因 | 见下 |
| ④ | 补齐 89 个 CompDB，改用 `/Add-Capability /LimitAccess` | 两个 capability 均 `S_OK`，`state: Installed` |

### ③ 的日志证据链

`C:\Windows\Logs\CBS\CBS.log`：

```
TASK: Converting package task to capability task on package:
      Microsoft-Windows-LanguageFeatures-Basic-zh-hk-Package~...~10.0.22621.1
FC:   FCAcquirerLocalRepository: Searching *.xml.cab in ...\fod-repo\metadata
FC:   FCAcquirerLocalRepository: Number of CompDBs found [2]        ← 只有 2 个
Error CBS    FC: CFCAcquirerLocalRepository::Download(111): Result = 0x800F800B
Info  CBS    Failed to resolve payload from UUP local source [0x800f800b]
Info  CBS    Exec: Download qualification evaluation, business scenario: Capability
Info  CBS    FC: FCAcquirerWUClient: Calling WindowsUpdateDownloadFromUUP
Info  CBS    DLWD: WU search failed [0x80072ee6]                       ← 回退 WU 失败
Error CBS    Failed to perform operation. [0x80072ee6]
Info  CBS    Session ... finalized. Reboot required: no
```

`/Add-Package` 被 CBS **强行转换成 capability 任务**，先查本地仓库；本地元数据不全就回退
WU，而 WU 被策略指向 `127.0.0.1` 且禁止连公网，于是整体回滚。
注意 `start state: Absent / target state: Absent / Reboot required: no` —— 失败不留下半装状态。

### ④ 的成功证据

```
FC:   FCAcquirerLocalRepository: Number of CompDBs found [89]
Exec: Processing complete. Session ... [HRESULT = 0x00000000 - S_OK]
Info  CBS    Reporting capability session finalize status: 0x0 ...
             capabilities: Language.Basic~zh-hk~1.0, state: Installed,
             retryStatus: 0, download source: 48
Info  CBS    Session ... finalized. Reboot required: no [S_OK]
```

`Language.Fonts.Hant~und-Hant~1.0` 同样 `S_OK`。
落地验证：`C:\Windows\System32\IME\IMETC\imtccore.dll` 存在、目录时间戳变为安装时刻；
注册表 `HKLM\...\Component Based Servicing\Packages` 下 4 个包 `CurrentState=112`。

## 4. 最终状态

- 输入法：香港繁中「基本键入」已装，HK 语言条目可直接打中文 ✅
- 地区格式：`zh-HK` ✅（需要时用 `Set-Culture zh-HK`）
- 家庭位置：香港特别行政区（GeoId 104）✅
- 时区：`China Standard Time`，显示名含「香港特别行政区」，UTC+8 ✅
  —— Windows 没有独立的香港时区 ID，这个就是正确的
- **全程未改任何 Windows 更新策略、未改注册表、未改语言列表**

## 5. 尚未覆盖 / 已知边界

- **LXP（本地体验包）** 通过 Microsoft Store / AppX 分发，**不在 FOD ISO 内**。
  若设置页仍提示 LXP 缺失，那是另一条通道，需要单独处理；不影响打字。
- 介质是 `22621.1` 基线，装到已打累积更新的 `22631.6199` 上本案例未出现问题，
  但不同补丁级别组合未系统性验证。微软文档建议「先加语言包与 FOD，再加更新」。
- CBS 判定 `Reboot required: no`；若任务栏暂未出现输入法图标，注销重登即可。
