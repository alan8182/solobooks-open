# 重启本地开发后端（加载最新代码）。
# 只杀 main:app 的 uvicorn，不碰其它项目的进程（8000 端口那个是"行程单工具"，与此无关）。
$ErrorActionPreference = 'SilentlyContinue'

Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'solobooks\\backend' -and $_.CommandLine -match 'uvicorn\s+main:app' } |
    ForEach-Object { Write-Host "stop PID $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force }

Start-Sleep -Seconds 2

Set-Location 'F:\solobooks\backend'
Write-Host 'starting uvicorn on 127.0.0.1:8001 --reload'
& '.\venv\Scripts\python.exe' -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
