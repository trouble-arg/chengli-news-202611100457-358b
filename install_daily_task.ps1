$ErrorActionPreference = "Stop"
$projectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $pythonPath = $pythonCommand.Source
    $pythonArguments = "`"$projectPath\aggregator.py`" --refresh-only"
} else {
    $pythonPath = (Get-Command py -ErrorAction Stop).Source
    $pythonArguments = "-3 `"$projectPath\aggregator.py`" --refresh-only"
}
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $pythonArguments -WorkingDirectory $projectPath
$trigger = New-ScheduledTaskTrigger -Daily -At "08:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName "ChengLiDianXunDailyNews" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "已注册每日 08:00 更新任务：ChengLiDianXunDailyNews"
