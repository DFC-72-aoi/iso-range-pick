# 参与改进

谢谢你有兴趣改进这个项目。它由两部分组成：一个通用的远程镜像精取工具，
以及一份 Windows 11 香港繁中输入法的离线安装案例。以下两类贡献最有帮助：

- 补上你实际验证过的官方介质版本与 capability 组合（不同 Windows 版本的
  Languages and Optional Features ISO 内容会有差异）。
- 补充你遇到的错误码、日志证据和最终有效的处理步骤。

## 反馈问题

请到 GitHub Issues 新建 issue，尽量包含：

- Windows 版本与内部版本号（`winver`，或 PowerShell 里 `Get-ComputerInfo`）
- 完整错误码，例如 `0x80073D01` / `0x80072EE6` / `0x800F800B`
- 相关日志片段：`C:\Windows\Logs\CBS\CBS.log`、`C:\Windows\Logs\DISM\dism.log`
- 使用的官方介质版本、工具命令、capability 名称
- 已经尝试过的步骤与对应结果

粘贴日志前请自行移除主机名、用户名、令牌、代理地址等私有信息。

## 一条红线

**不要提交任何微软二进制文件**：`.cab`、`.iso`、`.msu`、`.wim`、`.esd`、
`.msix`、`.appx` 等。本仓库只放脚本与文档，使用者需自行从微软官方渠道
获取介质并遵守适用许可。`.gitignore` 已排除这些类型，提交前请再确认一次。

## 提交改动

1. 新建分支，改动后用 `git add -A` 登记（`-A` 会同时记录删除）。
2. `git commit -m "简要说明改了什么"`，提交信息用英文更便于检索。
3. 推送并发起 Pull Request，说明验证方式与运行环境。

工具部分的改动请附带一次实际运行结果（例如 `list` 子命令列出的文件名与偏移），
案例部分的改动请附日志证据，避免仅凭推测修改结论。
