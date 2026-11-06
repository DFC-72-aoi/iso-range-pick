# 概念手册

这份手册不假设你懂 Windows 部署。尽量用「它是什么 / 为什么存在 / 和这次的问题什么关系」来讲。

## Windows 里的「语言」不是一个东西

设置页点一下「添加语言」，背后其实有好几类彼此独立的组件，缺哪个表现都不一样：

| 组件 | 装的是什么 | 分发方式 | 缺了会怎样 |
|---|---|---|---|
| **完整语言包**（Language Pack / `Client-Language-Pack`） | 系统界面翻译（菜单、对话框） | FOD ISO 里的 `.cab`，或 WU | 显示语言切不过去；不影响打字 |
| **本地体验包 LXP**（Local Experience Pack） | Windows 11 部分界面的补充本地化资源 | Microsoft Store / AppX（`.appx` / `.msixbundle`） | 设置页可能提示「语言包未完全安装」 |
| **语言界面包 LIP** | 部分本地化，通常依赖父语言 | `.cab` | 界面翻译不全 |
| **基本键入 / Basic typing**（Language FoD） | **输入法引擎、词典、断词、拼写检查** | FOD ISO 里的 `.cab`，或 WU | **能切到该语言但只能打英文** ← 本案例 |
| **键盘布局 / 输入法** | 一个指向 IME 的 GUID 列表 | 用户配置（`Get-WinUserLanguageList`） | 任务栏不显示输入法 |

本案例的现象正是「键盘布局在、基本键入不在」：语言列表里 `zh-Hant-HK` 带着输入法 GUID，
但打字只出英文。所以补的是 `Language.Basic~~~zh-HK~0.0.1.0` 这个 capability，不是语言包。

> 顺带：`zh-HK` 在 Windows 11 已不作为独立完整语言包提供（官方列表标注
> 「No longer used. See zh-TW.」），但香港专用的 **Basic / Handwriting / OCR / Speech /
> TextToSpeech** FoD 包都还在。所以「没有 HK 完整语言包」≠「HK 输入法装不了」。

## FoD（Features on Demand，按需功能）

Windows 把很多非核心功能做成「用时再装」的包，称为 FoD。语言资源（手写、OCR、
语音、字型、基本键入）都是 FoD，也包含 .NET Framework 3.5 这类东西。

微软文档把它分两类：

- **无 satellite（附属包）**：所有资源打进一个 `.cab`，语言类 FoD 属于这类；
- **有 satellite**：语言无关主体 + 若干语言/架构附属包，安装时只挑适用的，省空间。
  这类**必须**用 well-formed FOD 仓库，靠 `/Add-Capability` 一次指定 capability 名来装。

FoD 有两种获取通道：Windows Update（在线），或**离线介质**
（Windows 11 起叫 *Languages and Optional Features ISO*，旧版叫 *Features on Demand ISO*）。

## Capability（功能标识）

`Language.Basic~~~zh-HK~0.0.1.0` 这样的字符串是 DISM 认识的功能标识，
而介质里的 `.mum` 清单写的是 `Language.Basic~zh-hk~1.0`。两者是同一个东西的不同写法：

```
Language.Basic~~~zh-HK~0.0.1.0
└── 功能名 ──┘   └语言┘ └版本┘
```

FOD 仓库里的 `FoDMetadata_Client.cab` 就是「capability 名 → 具体 CAB 包」的映射表，
`metadata/*.xml.cab`（CompDB）提供组成信息。少了它们，CBS 不知道该拿哪个包。

## DISM 与 CBS

- **DISM**（Deployment Image Servicing and Management）：命令行工具，你敲的是它。
- **CBS**（Component Based Servicing）：真正干活的系统服务，日志在 `C:\Windows\Logs\CBS\CBS.log`。
- **CSI**：更底层的组件安装引擎，日志里也会出现。

DISM 只是「前台」，CBS 会按自己的规则改写任务。本案例最反直觉的一点就是：
你执行 `/Add-Package`，CBS 日志里却出现 `Converting package task to capability task`
——它被**强行转成按需功能任务**，于是开始查 FOD 仓库、回退 WU。

### `/Add-Package` vs `/Add-Capability`

| | `/Add-Package` | `/Add-Capability` |
|---|---|---|
| 输入 | 单个 `.cab` 全路径 | capability 名 + 可选 `/Source` 仓库 |
| 依赖处理 | 不解析 capability 依赖 | 自动拉取依赖包 |
| 微软态度 | **装 FOD 已弃用**（但无 satellite 的 FoD 技术上仍可用） | **推荐**，两类 FoD 都能正确安装 |
| 本案例 | 被转成 capability 任务并失败 | 一次成功 |

### `/Source` 与 `/LimitAccess`

- `/Source:<目录>`：告诉 DISM 先去哪里找源文件；
- `/LimitAccess`：**禁止 DISM 检查 Windows Update 或 WSUS**。

微软文档给出的查找顺序是：
1. `/Source` 指定的位置；
2. 组策略指定的位置；
3. 仍找不到、且是在线镜像、**且没指定 `/LimitAccess`** → 去 Windows Update。

所以只给 `/Source` 不给 `/LimitAccess`，在仓库不完整时依然会去连 WU。
在 WU 被策略挡住的机器上，这一步就是 `0x80072EE6` 的来源。

## WSUS 与组策略

- **WSUS**（Windows Server Update Services）：公司内网的「更新中转站」。管理员先审核，
  再统一分发给内网电脑；至少一台 WSUS 需要连微软更新。
  普通家用电脑**没有** WSUS，直接连微软更新服务器。
- **组策略 / 注册表策略**：`HKLM\SOFTWARE\Policies\...` 下的管理员级开关，
  优先级高于设置界面。`gpedit.msc` 的落地文件是
  `C:\Windows\System32\GroupPolicy\Machine\Registry.pol`；该文件不存在说明策略值是
  被直接写进注册表的，不是通过组策略编辑器写的。
- `127.0.0.1` 是**本机回环地址**。把更新服务器写成它，等于让电脑去「自己身上」
  找一个并不存在的更新服务。

> 本项目不修改、也不建议修改这些策略。离线仓库方案的全部意义就在于：
> **在策略原样不动的前提下把组件装上。**

## HTTP Range（字节范围请求）

普通下载是「把这个文件整个发给我」。Range 请求是「只发第 N 到第 M 字节」：

```
GET /big.iso HTTP/1.1
Range: bytes=4003840000-4075763711
```

服务器支持时回 `206 Partial Content`（而不是 `200 OK`），并带 `Content-Range`。
先发 `HEAD` 可以确认 `Accept-Ranges: bytes` 和总长度 `Content-Length`。

只要容器格式有**可随机访问的索引**（ISO9660/UDF 的目录记录、ZIP 尾部中央目录、
Parquet 的 footer），就能只读索引区、算出目标文件的偏移，再只取那一段。
这就是「6.71 GiB 的镜像只下 83 MiB」的全部原理。

配套工程细节：
- **块缓存**：元数据区反复随机读，把 256 KiB 块落盘，重复解析零网络请求；
- **并行**：单连接可能被限速，多路 Range 可显著提升吞吐（本案例约 8–13 倍）；
- **`If-Range` + ETag**：防止远端文件在解析和下载之间被替换导致偏移错位；
- **断点续传**：按分片记录进度，失败后从断点继续。
