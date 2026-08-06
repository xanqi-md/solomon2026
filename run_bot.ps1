# solomon2026 Discord Bot launcher
Set-Location -Path $PSScriptRoot

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log($message) {
    $logFile = Join-Path $logDir ("bot-" + (Get-Date -Format "yyyyMMdd") + ".log")
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message |
        Out-File -Append -Encoding utf8 $logFile
}

# Python の出力を UTF-8 に固定（文字化け防止）
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Log "Starting bot"

while ($true) {
    $logFile = Join-Path $logDir ("bot-" + (Get-Date -Format "yyyyMMdd") + ".log")

    # stderr をエラーレコードとして扱わせないため、外部プロセスとして起動する
    $proc = Start-Process -FilePath "python" `
        -ArgumentList "-m", "solomon.bot" `
        -NoNewWindow -PassThru -Wait `
        -RedirectStandardOutput "$logFile.out" `
        -RedirectStandardError  "$logFile.err"

    Get-Content "$logFile.out", "$logFile.err" -ErrorAction SilentlyContinue |
        Out-File -Append -Encoding utf8 $logFile
    Remove-Item "$logFile.out", "$logFile.err" -ErrorAction SilentlyContinue

    Write-Log ("Process exited with code {0}. Restarting in 30 seconds" -f $proc.ExitCode)
    Start-Sleep -Seconds 30
}
