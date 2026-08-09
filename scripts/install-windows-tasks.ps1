<#
.SYNOPSIS
  chronofit の収集デーモンとスナップショットを Windows タスクスケジューラへ登録する。

.DESCRIPTION
  収集だけは後から取り返せない。今日動かしていない日は永遠に空白のまま残るので、
  「手で起動する」運用にはしない。管理者権限は不要（現在のユーザーとして登録する）。

  登録するタスク:
    chronofit-collect   ログオン時に常駐開始。異常終了しても 5 分後に再起動する
    chronofit-snapshot  ブラウザ履歴の日次退避。Chromium は履歴を勝手に刈るので、
                        撮り逃すと過去分は復旧できない

.PARAMETER SnapshotTime
  スナップショットを走らせる時刻 (HH:mm)。既定 13:00。
  PC が落ちていて撮り逃した場合は次回の起動直後に自動で走る。

.PARAMETER Prefix
  タスク名の接頭辞。既定 chronofit。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-windows-tasks.ps1
#>
[CmdletBinding()]
param(
    [string]$SnapshotTime = '13:00',
    [string]$Prefix = 'chronofit'
)

$ErrorActionPreference = 'Stop'

function Resolve-PythonPair {
    # 収集は常駐なのでコンソール窓を出さない pythonw を使う。
    # スナップショットは短命なので、どちらでも構わないが揃えておく。
    $console = (Get-Command python.exe -ErrorAction SilentlyContinue |
                Where-Object { $_.Source -notlike '*WindowsApps*' } |
                Select-Object -First 1).Source
    if (-not $console) { throw 'python.exe が PATH に見つからない。' }
    $windowless = Join-Path (Split-Path $console) 'pythonw.exe'
    if (-not (Test-Path $windowless)) { $windowless = $console }
    return @{ Console = $console; Windowless = $windowless }
}

function Assert-ChronofitImportable([string]$python) {
    # 登録してから「実は import できなかった」を避ける。タスクは黙って死ぬので
    # ここで落としたほうが早い。
    & $python -c 'import chronofit' 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "chronofit を import できない。先に 'pip install -e .' を実行すること。"
    }
}

$py = Resolve-PythonPair
Assert-ChronofitImportable $py.Console

$common = @{
    AllowStartIfOnBatteries    = $true
    DontStopIfGoingOnBatteries = $true
    StartWhenAvailable         = $true   # 撮り逃した回を次の機会に回収する
}

# --- 収集デーモン -----------------------------------------------------------
$collectSettings = New-ScheduledTaskSettingsSet @common `
    -MultipleInstances IgnoreNew `
    -RestartInterval (New-TimeSpan -Minutes 5) -RestartCount 3
# 既定の 72 時間で打ち切られると常駐が黙って死ぬ。Zero = 無制限。
$collectSettings.ExecutionTimeLimit = 'PT0S'

Register-ScheduledTask -TaskName "$Prefix-collect" -Force `
    -Description 'chronofit: 前景ウィンドウと入力アイドルの収集（常駐）' `
    -Action  (New-ScheduledTaskAction -Execute $py.Windowless -Argument '-m chronofit collect') `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
    -Settings $collectSettings | Out-Null

# --- ブラウザ履歴スナップショット -------------------------------------------
$snapshotSettings = New-ScheduledTaskSettingsSet @common -MultipleInstances IgnoreNew
$snapshotSettings.ExecutionTimeLimit = 'PT30M'

Register-ScheduledTask -TaskName "$Prefix-snapshot" -Force `
    -Description 'chronofit: ブラウザ履歴の日次退避（Chromium は履歴を刈るため）' `
    -Action  (New-ScheduledTaskAction -Execute $py.Windowless -Argument '-m chronofit snapshot') `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $SnapshotTime) `
    -Settings $snapshotSettings | Out-Null

Get-ScheduledTask -TaskName "$Prefix-*" |
    Select-Object TaskName, State, @{n='Next';e={ ($_ | Get-ScheduledTaskInfo).NextRunTime }} |
    Format-Table -AutoSize
