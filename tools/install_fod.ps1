<#
.SYNOPSIS
  用本地 FOD 仓库离线安装 Windows「按需功能」（language capability），完全不联系 Windows Update。

.DESCRIPTION
  /LimitAccess 是关键：微软文档定义为「不让 DISM 检查 Windows Update 或 WSUS 获取功能源文件」。
  不带这个参数时，即使指定了 /Source，CBS 在仓库元数据不全的情况下仍会回退到 WU；
  在 WU 被组策略指向不可达服务器、或离线机器上，就会以 0x80072ee6 失败。

  本脚本不修改注册表、不修改 Windows 更新策略、不修改用户语言列表。

.PARAMETER Repo
  由 iso_pick.py repo 生成的 FOD 仓库目录（根含 FoDMetadata_Client.cab 与 .cab 包，
  子目录 metadata/ 含 DesktopTargetCompDBForISO_*.xml.cab）。

.PARAMETER Capability
  要安装的 capability 名，可重复。例如：
    Language.Basic~~~zh-HK~0.0.1.0
    Language.Fonts.Hant~~~und-Hant~0.0.1.0

.PARAMETER FallbackCab
  可选。主命令失败时，改用 /Add-Package 安装本地 CAB 全路径。
  微软已把 /Add-Package 装 FOD 标记为弃用，仅在排障时作为回退。

.EXAMPLE
  .\install_fod.ps1 -Repo "C:\fod-repo" -Capability "Language.Basic~~~zh-HK~0.0.1.0"

.NOTES
  需要管理员权限。失败时请查看 C:\Windows\Logs\CBS\CBS.log 与 C:\Windows\Logs\DISM\dism.log。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(Mandatory = $true)][string[]]$Capability,
    [string[]]$FallbackCab = @()
)

$ErrorActionPreference = 'Stop'
$dism = Join-Path $env:WINDIR 'System32\dism.exe'

# ---------- 1. 管理员检查 ----------
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '需要管理员权限：请右键「开始」→「终端(管理员)」后重新运行。'
}

# ---------- 2. 仓库完整性校验 ----------
if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
    throw "仓库目录不存在：$Repo"
}
$metaCab = Join-Path $Repo 'FoDMetadata_Client.cab'
if (-not (Test-Path -LiteralPath $metaCab)) {
    throw '仓库缺少 FoDMetadata_Client.cab（FOD 主元数据），无法作为 /Source 使用。'
}
$metaDir = Join-Path $Repo 'metadata'
$compDb = @(Get-ChildItem -LiteralPath $metaDir -Filter '*.xml.cab' -File -ErrorAction SilentlyContinue)
if ($compDb.Count -eq 0) {
    throw '仓库缺少 metadata/ 下的 CompDB 元数据 CAB；只有 2 个时 CBS 会报 0x800F800B 并回退 WU。'
}
Write-Host "[1/3] 仓库校验通过：$($compDb.Count) 个 CompDB + 主元数据 CAB" -ForegroundColor Green

# ---------- 3. 安装 ----------
foreach ($cap in $Capability) {
    Write-Host "[2/3] 安装 $cap （/LimitAccess 强制离线）…" -ForegroundColor Cyan
    # 参数显式拼接，避免 PowerShell 对含 ~ 的裸参数做解析
    & $dism /online /Add-Capability ("/CapabilityName:" + $cap) ("/Source:" + $Repo) /LimitAccess
    $ok = ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 3010)
    if (-not $ok -and $FallbackCab.Count -gt 0) {
        foreach ($cab in $FallbackCab) {
            Write-Host "    主命令未成功，回退 /Add-Package：$cab" -ForegroundColor Yellow
            & $dism /online /Add-Package ("/PackagePath:" + $cab)
            if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 3010) { $ok = $true; break }
        }
    }
    if (-not $ok) {
        throw "安装失败：$cap。请查看 C:\Windows\Logs\CBS\CBS.log 中本时间段的记录。"
    }
    Write-Host "    $cap OK" -ForegroundColor Green
}

# ---------- 4. 复查 ----------
Write-Host '[3/3] 复查 capability 状态：' -ForegroundColor Cyan
foreach ($cap in $Capability) {
    Get-WindowsCapability -Online |
        Where-Object { $_.Name -eq $cap } |
        Select-Object Name, State | Format-Table -AutoSize
}
Write-Host '完成。若输入法图标未立即出现，注销后重新登录即可。' -ForegroundColor Green
Write-Host '回滚：dism /online /Remove-Capability /CapabilityName:<上面的 Name>' -ForegroundColor Yellow
