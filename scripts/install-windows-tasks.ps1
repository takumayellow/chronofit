<#
.SYNOPSIS
  chronofit の収集デーモンとスナップショットを Windows タスクスケジューラへ登録する。

.DESCRIPTION
  収集だけは後から取り返せない。今日動かしていない日は永遠に空白のまま残るので、
  「手で起動する」運用にはしない。管理者権限は不要（現在のユーザーとして登録する）。

  登録するタスク:
    chronofit-collect   ログオン時に常駐開始。異常終了しても 5 分後に再起動する。
                        さらに 30 分ごとの見張りトリガを持たせて、再起動回数を
                        使い切ったあとでも次の機会に自力で戻れるようにする
    chronofit-snapshot  ブラウザ履歴の日次退避。Chromium は履歴を勝手に刈るので、
                        撮り逃すと過去分は復旧できない
    chronofit-daily     前日ぶんの畳み込み + その日の進捗の記録。毎晩自動で回して、
                        日次の集計と現在地が「思い出したときに手で打つ」ものに
                        ならないようにする
    chronofit-refresh   今日の日次レポートを 30 分ごとに書き出し直す。位置はスマホから
                        常時届くので、予定と実際の突き合わせを翌朝まで待たせない
    chronofit-location  スマホから位置を受け取る常駐（`chronofit location setup` 済みのときだけ）

.PARAMETER SnapshotTime
  スナップショットを走らせる時刻 (HH:mm)。既定 13:00。
  PC が落ちていて撮り逃した場合は次回の起動直後に自動で走る。

.PARAMETER RollupTime
  前日ぶんを畳む時刻 (HH:mm)。既定 03:00。

.PARAMETER Prefix
  タスク名の接頭辞。既定 chronofit。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-windows-tasks.ps1
#>
[CmdletBinding()]
param(
    [string]$SnapshotTime = '13:00',
    [string]$RollupTime = '03:00',
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

# ログオン時だけだと、RestartCount を使い切った後は次のログオンまで空白になる。
# 30 分ごとに起動を試み、生きていれば IgnoreNew で捨てられる（＝見張りとして働く）。
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "$Prefix-collect" -Force `
    -Description 'chronofit: 前景ウィンドウと入力アイドルの収集（常駐 + 30分ごとの見張り）' `
    -Action  (New-ScheduledTaskAction -Execute $py.Windowless -Argument '-m chronofit collect') `
    -Trigger @((New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME), $watchdog) `
    -Settings $collectSettings | Out-Null

# --- ブラウザ履歴スナップショット -------------------------------------------
$snapshotSettings = New-ScheduledTaskSettingsSet @common -MultipleInstances IgnoreNew
$snapshotSettings.ExecutionTimeLimit = 'PT30M'

Register-ScheduledTask -TaskName "$Prefix-snapshot" -Force `
    -Description 'chronofit: ブラウザ履歴の日次退避（Chromium は履歴を刈るため）' `
    -Action  (New-ScheduledTaskAction -Execute $py.Windowless -Argument '-m chronofit snapshot') `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $SnapshotTime) `
    -Settings $snapshotSettings | Out-Null

# --- 前日ぶんの畳み込み + 進捗の記録 ----------------------------------------
# 収集しただけでは日次の姿が見えない。畳むのを人手に残すと、忙しい日ほど飛ぶ。
# 進捗も同じタスクで残す。現在地は毎回計算できるが、「あの日どれだけ残っていると
# 思っていたか」は後から計算できないので、その日のうちに写しておく必要がある。
$dailySettings = New-ScheduledTaskSettingsSet @common -MultipleInstances IgnoreNew
$dailySettings.ExecutionTimeLimit = 'PT10M'

Unregister-ScheduledTask -TaskName "$Prefix-rollup" -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName "$Prefix-daily" -Force `
    -Description 'chronofit: 前日ぶんを畳んで、その日の進捗を残す（日次）' `
    -Action  (New-ScheduledTaskAction -Execute $py.Windowless -Argument '-m chronofit daily') `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $RollupTime) `
    -Settings $dailySettings | Out-Null

# --- 今日のレポートの書き直し ----------------------------------------------
# 日次の畳み込みは翌朝なので、それだけだと今日の予定と実際は夜が明けるまで見えない。
# 位置は常時届いているので、30 分ごとに今日のページを作り直す（開かない）。
$refreshSettings = New-ScheduledTaskSettingsSet @common -MultipleInstances IgnoreNew
$refreshSettings.ExecutionTimeLimit = 'PT10M'
$every30 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "$Prefix-refresh" -Force `
    -Description 'chronofit: 今日の日次レポートを 30 分ごとに書き出し直す' `
    -Action  (New-ScheduledTaskAction -Execute $py.Windowless -Argument '-m chronofit report --no-open') `
    -Trigger $every30 `
    -Settings $refreshSettings | Out-Null

# --- スマホからの位置の受け口 -----------------------------------------------
# `chronofit location setup` を済ませたときだけ登録する。スマホ側のアプリは届かなかった
# 位置を溜めて再送するので、PC が起きている間だけ待ち受ければ足りる。
# Tailscale より先に起動すると IP が無くて落ちるが、再起動と見張りで拾い直す。
$receiver = Join-Path $env:LOCALAPPDATA 'chronofit\location\receiver.json'
if ($env:CHRONOFIT_HOME) { $receiver = Join-Path $env:CHRONOFIT_HOME 'location\receiver.json' }
if (Test-Path $receiver) {
    $locationSettings = New-ScheduledTaskSettingsSet @common `
        -MultipleInstances IgnoreNew `
        -RestartInterval (New-TimeSpan -Minutes 5) -RestartCount 3
    $locationSettings.ExecutionTimeLimit = 'PT0S'
    Register-ScheduledTask -TaskName "$Prefix-location" -Force `
        -Description 'chronofit: スマホ（OwnTracks）から位置を受け取る（常駐 + 30分ごとの見張り）' `
        -Action  (New-ScheduledTaskAction -Execute $py.Windowless -Argument '-m chronofit location serve') `
        -Trigger @((New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME), $watchdog) `
        -Settings $locationSettings | Out-Null
}

Get-ScheduledTask -TaskName "$Prefix-*" |
    Select-Object TaskName, State, @{n='Next';e={ ($_ | Get-ScheduledTaskInfo).NextRunTime }} |
    Format-Table -AutoSize
