# 错误码对照表

本案例涉及的错误码分属三个不同层次，容易混为一谈。按「谁抛出来的」分类看最清楚。

## AppX / MSIX 部署层

| 码 | 名称 | 含义 | 本案例中的角色 |
|---|---|---|---|
| `0x80073D01` | `ERROR_DEPLOYMENT_BLOCKED_BY_POLICY` | 策略阻止包部署操作。微软官方说明为「原則會封鎖套件部署作業。請連絡您的系統管理員。」常见诱因：应用控制策略、特殊配置文件策略、临时配置文件登录 | 设置页下载 HK 语言功能时最先遇到的错 |
| `0x80073D28` | — | 包安装需要管理员权限 | 未出现，但排查时容易与上面混淆 |

## CBS（组件服务）层

| 码 | 名称 | 含义 | 本案例中的角色 |
|---|---|---|---|
| `0x800F800B` | —（CBS 内部） | 无法从 UUP 本地源解析 payload。`CFCAcquirerLocalRepository::Download` 失败 | 本地 FOD 仓库只有 2 个 CompDB 时触发，是回退 WU 的**直接原因** |
| `0x800F081F` | `CBS_E_SOURCE_MISSING` | 找不到源文件或包 | 常见于 FOD 在线安装找不到源；本案例未直接出现 |
| `0x800F0950` | — | 可选功能无法从 WU 取包 | 同类问题的另一常见表现 |
| `0x800F0954` | — | 无法从 WSUS / 配置管理源获取可选内容 | 企业环境常见 |
| `0x800F0821` | `CBS_E_ABORT` | 客户端中止安装（上层失败后的连带结果） | 出现在失败日志尾部，不是根因 |
| `0x00000000` | `S_OK` | 成功 | 修复后两次会话的最终状态 |

## 网络 / Windows Update 层

| 码 | 名称 | 含义 | 本案例中的角色 |
|---|---|---|---|
| `0x80072EE6` / 十进制 `12006` | `ERROR_INTERNET_NAME_NOT_RESOLVED` / `ERROR_WINHTTP_NAME_NOT_RESOLVED` | 无法解析服务器名称 | CBS 回退 WU 后 `DLWD: WU search failed`，整个安装中止。根因是 WU 被策略指向 `127.0.0.1` 且禁止连公网 |
| `0x8024002E` | `WU_E_WU_DISABLED` | Windows Update 访问被禁用 | 与 `DisableWindowsUpdateAccess=1` 相关的同类表现 |

## 一眼定位的顺序

```
设置页报错
  └─ 读 C:\Windows\Logs\DISM\dism.log  →  看命令行与 HRESULT
       └─ 读 C:\Windows\Logs\CBS\CBS.log  →  按时间段找：
            1. "Converting package task to capability task"   ← 是否被转成 capability
            2. "FCAcquirerLocalRepository: Number of CompDBs"  ← 本地仓库是否完整
            3. "Failed to resolve payload from UUP local source" ← 是否本地源解析失败
            4. "Calling WindowsUpdateDownloadFromUUP"           ← 是否回退了 WU
            5. "Reporting capability session finalize status"    ← 最终 0x0 还是错误码
            6. "Reboot required" / "start state"/"target state"  ← 是否留下半装状态
```

## 相关策略键（只作识别，本项目不修改）

`HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate`

| 值 | 作用 |
|---|---|
| `WUServer` / `WUStatusServer` / `UpdateServiceUrlAlternate` | 指定 Intranet 更新服务器。设为 `127.0.0.1` 且本机无 WSUS 时，等于指向不存在的仓库 |
| `UseWUServer` | `1` = 强制使用上述服务器 |
| `DoNotConnectToWindowsUpdateInternetLocations` | `1` = 禁止连接任何 Windows 更新 Internet 位置；组策略说明中明确提到会中断 Windows 应用商店等公共服务连接 |
| `DisableWindowsUpdateAccess` | `1` = 关闭对 Windows Update 功能的访问 |
| `TargetReleaseVersion` / `TargetReleaseVersionInfo` | 锁定功能更新版本 |

`C:\Windows\System32\GroupPolicy\Machine\Registry.pol` 不存在 ⇒ 这些值**不是**通过本地组策略编辑器
写入的，而是被直接写进注册表。本项目不追查写入者，也不改动它们。
