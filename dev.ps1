# =====================================================================
#  律所平台 · 本地开发启停
# =====================================================================
#
#  用法（在项目根目录）：
#      .\dev.ps1            起全部
#      .\dev.ps1 status     看谁在跑
#      .\dev.ps1 stop       停掉三个应用进程（容器不动）
#      .\dev.ps1 restart    停完再起
#      .\dev.ps1 backend    只重起后端
#
#  ★ 为什么要有这个脚本：
#
#    整套东西是**两层**，重启电脑后两层都得管：
#
#      ① 基础设施（Docker 容器）  Postgres / Redis / MinIO
#      ② 应用进程（裸跑）         后端 :8000 · 检索 :8100 · 前端 :3000
#
#    ①在 Docker Desktop 里看得到，②看不到 —— 这是最容易搞混的地方。
#
#  ★ 每个应用进程都用 `Start-Process` 独立开一个窗口，**不经过管道**。
#    2026-08-29 踩过：`uvicorn ... | tail` 这种管道写法会让 uvicorn
#    变成 bash 的孙进程；停掉父进程后它**继续占着 8000 端口，
#    而 taskkill 报"进程不存在"** —— 只能重启电脑才清得掉。
#
#  ★ 停止一律**按端口找 PID**，不按进程名。
#    按名字杀会连带干掉别的 python（比如那个 :8100 的检索服务）。
# =====================================================================

param([string]$Action = "start")

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$DockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"

# 端口 -> 说明
$SERVICES = [ordered]@{
  5432 = "Postgres     容器  主数据库"
  6379 = "Redis        容器  暂时没真用上"
  9000 = "MinIO        容器  案卷文件"
  8000 = "后端         进程  FastAPI"
  8100 = "法律检索     进程  RAG（第 4、6 步要用）"
  3000 = "前端         进程  Next.js"
}
$APP_PORTS = 8000, 8100, 3000

# ★ PowerShell 5.1 的坑：对**原生命令**用 `2>&1`，每行 stderr 会被包成
#   ErrorRecord，配合 $ErrorActionPreference="Stop" 就直接中断整个脚本 ——
#   哪怕那条命令返回 0。等 Docker 引擎时它必然先失败几次，正好踩上。
#   所以走 cmd 重定向到 NUL，PowerShell 完全看不到 stderr。
function Quiet([string]$Exe, [string[]]$CmdArgs) {
  $line = '"' + $Exe + '" ' + ($CmdArgs -join " ") + " >NUL 2>&1"
  & cmd.exe /c $line
  return $LASTEXITCODE
}

function PidOn([int]$Port) {
  $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($c) { return ($c | Select-Object -First 1).OwningProcess }
  return $null
}

function Show-Status {
  ""
  "  端口   服务                        状态"
  "  ----   -------------------------   ------"
  # ★ 用 GetEnumerator() 直接拿键值对 —— 遍历 .Keys 再回头索引，
  #   在 ordered 字典上取不到值（键的装箱类型对不上），出来是一列空白。
  foreach ($e in $SERVICES.GetEnumerator()) {
    $up = if (PidOn ([int]$e.Key)) { "[在跑]" } else { "[没起]" }
    "  {0,-6} {1,-30} {2}" -f $e.Key, $e.Value, $up
  }
  ""
  $dd = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
  if ($dd) { "  Docker Desktop：在跑" } else { "  Docker Desktop：没开（容器起不来）" }
  ""
}

function Stop-Apps([int[]]$Ports) {
  # ★ 只停应用进程，容器不动 —— 容器停了要等 Postgres 重新起来，慢而且没必要
  if (-not $Ports) { $Ports = $APP_PORTS }
  foreach ($p in $Ports) {
    $procId = PidOn $p
    if ($procId) {
      Write-Host "  停 :$p (PID $procId)"
      # /T 连子进程一起 —— uvicorn --reload 会 fork 一个 worker
      Quiet "taskkill.exe" @("/F", "/T", "/PID", "$procId") | Out-Null
    }
  }
  Start-Sleep -Milliseconds 800
  foreach ($p in $Ports) {
    if (PidOn $p) { Write-Host "  ⚠ :$p 还占着 —— 手工查 netstat -ano | findstr $p" }
  }
}

function Start-Infra {
  if (-not (Test-Path $Docker)) { throw "找不到 docker.exe：$Docker" }

  if (-not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue)) {
    Write-Host "  Docker Desktop 没开，正在启动…（首次要等 30–60 秒）"
    Start-Process $DockerDesktop
  }

  # 等 docker 引擎真的能用 —— Docker Desktop 的窗口先出来，引擎后就绪
  Write-Host "  等 Docker 引擎就绪…" -NoNewline
  $ok = $false
  foreach ($i in 1..60) {
    if ((Quiet $Docker @("info")) -eq 0) { $ok = $true; break }
    Start-Sleep -Seconds 2
    Write-Host "." -NoNewline
  }
  ""
  if (-not $ok) { throw "Docker 引擎 120 秒还没就绪 —— 手工打开 Docker Desktop 再跑一次" }

  Write-Host "  起容器（postgres / redis / minio）…"
  Push-Location (Join-Path $Root "infra")
  # ★ docker compose 的进度输出走的是 stderr —— 同样不能让它中断脚本
  try { Quiet $Docker @("compose", "up", "-d") | Out-Null }
  finally { Pop-Location }

  # ★ 等 Postgres 真的能连 —— 容器"起来了"不等于数据库"能用了"，
  #   这中间有几秒，后端在这几秒里启动会直接连不上库然后退出
  Write-Host "  等 Postgres 接受连接…" -NoNewline
  foreach ($i in 1..40) {
    if ((Quiet $Docker @("exec", "lfp-postgres", "pg_isready", "-U", "lfp")) -eq 0) {
      Write-Host " 好了"; return
    }
    Start-Sleep -Seconds 1
    Write-Host "." -NoNewline
  }
  ""
  Write-Host "  ⚠ Postgres 迟迟没就绪，后端可能起不来"
}

# ★ 参数名**不能叫 $Args** —— 那是 PowerShell 的自动变量（未绑定实参数组），
#   自己声明一个同名参数会被它盖掉，传进来永远是空的。
function Start-App([string]$Title, [string]$Dir, [string]$Exe, [string[]]$ArgList) {
  if (-not (Test-Path $Dir)) { Write-Host "  跳过 $Title（找不到目录 $Dir）"; return }
  Write-Host "  起 $Title"
  # ★ 独立窗口、不经过管道 —— 见文件头那条踩坑记录
  Start-Process -FilePath $Exe -ArgumentList $ArgList -WorkingDirectory $Dir `
                -WindowStyle Minimized
}

function Start-Backend {
  Start-App "后端 :8000" (Join-Path $Root "backend") `
            (Join-Path $Root "backend\.venv\Scripts\python.exe") `
            @("-m", "uvicorn", "kernel.web.app:app", "--port", "8000", "--reload")
}

function Start-All {
  Start-Infra

  # 迁移：每次起都跑一遍，已经是最新就秒过
  Write-Host "  跑数据库迁移…"
  Push-Location (Join-Path $Root "backend")
  try {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & ".\.venv\Scripts\python.exe" -X utf8 -m kernel.cli db upgrade 2>&1 |
      Select-String -Pattern "Running upgrade|ERROR" | Out-String | Write-Host
    $ErrorActionPreference = $old
  } finally { Pop-Location }

  Start-Backend

  $rag = "C:\Users\LF\Desktop\langchain-legal-RAG"
  Start-App "法律检索 :8100" $rag (Join-Path $rag ".venv\Scripts\python.exe") `
            @("-m", "uvicorn", "src.service:app", "--port", "8100")

  Start-App "前端 :3000" (Join-Path $Root "frontend") `
            "cmd.exe" @("/c", "npm", "run", "dev")

  Write-Host "  等服务就绪…" -NoNewline
  foreach ($i in 1..40) {
    Start-Sleep -Seconds 1
    Write-Host "." -NoNewline
    if ((PidOn 8000) -and (PidOn 3000)) { break }
  }
  ""
  Show-Status
  Write-Host "  打开 http://localhost:3000    账号 kangqiao / li / password123"
  ""
}

switch ($Action.ToLower()) {
  "status"  { Show-Status }
  "stop"    { Stop-Apps; Show-Status }
  "restart" { Stop-Apps; Start-Sleep -Seconds 1; Start-All }
  # ★ backend 只停 :8000 —— 原来复用了 Stop-Apps（停全部），
  #   结果"只重起后端"把检索和前端也一起干掉了
  "backend" { Stop-Apps @(8000); Start-Sleep -Seconds 1; Start-Backend
              Start-Sleep -Seconds 4; Show-Status }
  "start"   { Start-All }
  default   { "用法： .\dev.ps1 [start|stop|restart|status|backend]" }
}
