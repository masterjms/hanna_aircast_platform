#Requires -Version 5.1
<#
  xWIFI 마을방송 — 개발 환경 실행기

    .\dev.ps1 setup     최초 1회 (venv · 의존성 · DB 마이그레이션 · 시드)
    .\dev.ps1 up        전부 실행 (인프라 + 백엔드 + 프론트 + 목단말)
    .\dev.ps1 down      전부 종료
    .\dev.ps1 status    상태 확인
    .\dev.ps1 logs      도커 로그 따라가기

  백엔드 · 프론트 · 목단말은 각각 별도 창으로 뜬다. 로그를 따로 보고
  Ctrl+C 로 개별 종료하기 위해서다. 창을 다 닫았으면 `down` 으로 정리한다.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'up', 'down', 'status', 'logs', 'infra', 'restart', 'help')]
    [string]$Command = 'help',

    # 목 단말 수. 기본은 0 — 안 띄운다.
    # 가짜 단말이 섞이면 실물 단말 로그를 읽을 수 없고, 방송 대상에도 끼어든다.
    # 실물 없이 시험할 때만 -Mock 5 처럼 명시한다.
    [int]$Mock = 0,

    # 프론트를 빼고 띄운다 (API 만 만질 때).
    [switch]$NoFront
)

# 'Stop' 을 쓰면 안 된다. PowerShell 5.1 은 네이티브 exe(docker·npm)의 stderr 를
# 리다이렉트할 때 각 줄을 ErrorRecord 로 감싸는데, 'Stop' 이면 그게 곧바로
# 스크립트를 죽인다 — docker info 의 무해한 WARNING 한 줄에도 멈춘다.
# 대신 네이티브 명령은 아래에서 $LASTEXITCODE 로 성패를 직접 확인한다.
$ErrorActionPreference = 'Continue'

$Root     = $PSScriptRoot
$Compose  = Join-Path $Root 'docker-compose.dev.yml'
$Py       = Join-Path $Root 'backend\.venv\Scripts\python.exe'
$BackDir  = Join-Path $Root 'backend'
$FrontDir = Join-Path $Root 'frontend'

$PORT_API   = 8080
$PORT_WEB   = 5173
$PORT_PG    = 5432
$PORT_MQTT  = 1883
$PORT_ICE   = 8000

# ── 출력 ─────────────────────────────────────────────────────────────
function Step($m) { Write-Host "`n▶ $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  OK   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  주의 $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  실패 $m" -ForegroundColor Red }

# ── 포트 ─────────────────────────────────────────────────────────────
function Get-PortPid([int]$Port) {
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
         Select-Object -First 1
    if ($c) { return $c.OwningProcess }
    return $null
}

function Test-Port([int]$Port) {
    return $null -ne (Get-PortPid $Port)
}

function Stop-Port([int]$Port, [string]$Label) {
    $procId = Get-PortPid $Port
    if (-not $procId) { Write-Host "  - $Label : 이미 꺼져 있음"; return }
    try {
        Stop-Process -Id $procId -Force -ErrorAction Stop
        Ok "$Label 종료 (PID $procId)"
    } catch {
        Fail "$Label 종료 실패: $($_.Exception.Message)"
    }
}

function Wait-Port([int]$Port, [string]$Label, [int]$Seconds = 40) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (Test-Port $Port) { Ok "$Label 준비됨 ($($i+1)초)"; return $true }
        Start-Sleep -Seconds 1
    }
    Fail "$Label 이 ${Seconds}초 안에 안 떴다"
    return $false
}

# ── 사전 점검 ────────────────────────────────────────────────────────
function Test-Prereq {
    $missing = @()
    foreach ($c in 'docker', 'npm') {
        if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { $missing += $c }
    }
    if ($missing.Count -gt 0) {
        Fail "필요한 명령이 없다: $($missing -join ', ')"
        Write-Host "    Docker Desktop / Node.js 설치 후 다시 실행하세요."
        return $false
    }

    # 네이티브 exe 는 $? 대신 $LASTEXITCODE 로 본다. PowerShell 5.1 은 exe 가
    # stderr 에 한 줄만 써도 $? 를 false 로 만들어서, 성공을 실패로 오판한다.
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker 엔진에 연결할 수 없다 — Docker Desktop 이 실행 중인지 확인하세요."
        Write-Host "    소켓 오류로 안 뜨면: %LOCALAPPDATA%\Docker\run 폴더를 지우고 재시작"
        return $false
    }

    if (-not (Test-Path (Join-Path $Root '.env'))) {
        Warn ".env 가 없다 — .env.example 을 복사한다"
        Copy-Item (Join-Path $Root '.env.example') (Join-Path $Root '.env')
    }
    return $true
}

<#
  localhost:5432 가 우리 컨테이너로 가는지 확인한다.

  WSL(Ubuntu)에 네이티브 postgres/mosquitto 가 깔려 있으면 그쪽이 같은 포트를
  먼저 잡는다. 둘 다 DB 이름과 비밀번호가 같으면 오류 없이 조용히 엉뚱한 DB 에
  붙어서, 데이터가 반쪽씩 갈린다. 실제로 한 번 당했다 — 그래서 여기서 막는다.
#>
function Test-DbShadow {
    if (-not (Test-Path $Py)) { return }   # setup 전이면 검사 불가

    $inside = (docker exec xwifi-postgres-dev psql -U xwifi -d xwifi -tAc `
               "SELECT pg_postmaster_start_time()" 2>$null)
    if (-not $inside) { return }
    $inside = $inside.Trim()

    $outside = & $Py -c @"
import asyncio, asyncpg
async def m():
    c = await asyncpg.connect('postgresql://xwifi:xwifi-dev-pw@localhost:5432/xwifi')
    print((await c.fetchval('SELECT pg_postmaster_start_time()')).isoformat())
    await c.close()
asyncio.run(m())
"@ 2>$null
    if (-not $outside) { return }

    # 초 단위까지만 비교한다(포맷이 서로 조금 다르다).
    $a = ([datetime]::Parse($inside)).ToUniversalTime().ToString('s')
    $b = ([datetime]::Parse($outside)).ToUniversalTime().ToString('s')

    if ($a -eq $b) {
        Ok "DB 경로 확인 — localhost:5432 가 컨테이너로 간다"
    } else {
        Fail "localhost:$PORT_PG 가 우리 컨테이너가 아닌 다른 Postgres 로 간다!"
        Write-Host "    컨테이너 기동: $a" -ForegroundColor Red
        Write-Host "    localhost   : $b" -ForegroundColor Red
        Write-Host ""
        Write-Host "    WSL 에 네이티브 postgres/mosquitto 가 떠 있을 가능성이 높다." -ForegroundColor Yellow
        Write-Host "    확인 : wsl -e bash -lc `"systemctl is-active postgresql mosquitto`"" -ForegroundColor Yellow
        Write-Host "    해제 : wsl -e bash -lc `"sudo systemctl disable --now postgresql mosquitto`"" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "    그냥 두면 데이터가 두 DB 로 갈린다. 먼저 해결하세요." -ForegroundColor Red
    }
}

# ── 실행 ─────────────────────────────────────────────────────────────
function Start-InNewWindow([string]$Title, [string]$WorkDir, [string]$CommandLine) {
    $inner = "`$host.UI.RawUI.WindowTitle = '$Title'; $CommandLine"
    Start-Process powershell -WorkingDirectory $WorkDir `
        -ArgumentList '-NoExit', '-NoProfile', '-Command', $inner | Out-Null
}

function Start-Infra {
    Step '인프라 (postgres · mosquitto · icecast)'
    docker compose -f $Compose up -d
    if ($LASTEXITCODE -ne 0) { Fail '컨테이너 기동 실패'; return $false }

    # postgres 는 healthcheck 가 있어서 그걸 기다린다.
    for ($i = 0; $i -lt 60; $i++) {
        $h = (docker inspect xwifi-postgres-dev --format '{{.State.Health.Status}}' 2>$null)
        if ($h -eq 'healthy') { Ok "postgres 준비됨 ($($i+1)초)"; break }
        Start-Sleep -Seconds 1
    }
    Test-DbShadow
    return $true
}

function Start-Backend {
    Step "백엔드 (:$PORT_API)"
    if (Test-Port $PORT_API) { Warn "이미 :$PORT_API 사용 중 — 건너뜀"; return }
    if (-not (Test-Path $Py)) {
        Fail "venv 가 없다 — 먼저 .\dev.ps1 setup 을 실행하세요"
        return
    }
    Start-InNewWindow 'xWIFI 백엔드' $BackDir ".\.venv\Scripts\python.exe run.py"
    if (Wait-Port $PORT_API '백엔드' 45) {
        Ok "API 문서 http://localhost:$PORT_API/docs"
    }
}

function Start-Frontend {
    Step "프론트 (:$PORT_WEB)"
    if (Test-Port $PORT_WEB) { Warn "이미 :$PORT_WEB 사용 중 — 건너뜀"; return }
    if (-not (Test-Path (Join-Path $FrontDir 'node_modules'))) {
        Warn 'node_modules 가 없다 — npm install 을 먼저 돌린다'
        Push-Location $FrontDir
        npm install
        Pop-Location
    }
    Start-InNewWindow 'xWIFI 프론트' $FrontDir 'npm run dev'
    if (Wait-Port $PORT_WEB '프론트' 45) {
        Ok "화면 http://localhost:$PORT_WEB"
    }
}

function Start-Mock([int]$Count) {
    Step "목 단말 $Count 대"
    if ($Count -le 0) {
        Write-Host '  - 안 띄움 (실물 단말용). 가짜 단말이 필요하면 -Mock 5'
        return
    }
    if (-not (Test-Path $Py)) { Fail 'venv 가 없다 — setup 먼저'; return }
    Start-InNewWindow 'xWIFI 목단말' $BackDir `
        ".\.venv\Scripts\python.exe ..\scripts\mock_device.py --count $Count"
    Ok "$Count 대 기동 요청 (창에서 로그 확인)"
}

function Stop-Mock {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -like '*mock_device*' }
    if (-not $procs) { Write-Host '  - 목단말 : 이미 꺼져 있음'; return }
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Ok "목단말 $($procs.Count) 개 종료"
}

# ── 명령 ─────────────────────────────────────────────────────────────
function Invoke-Setup {
    if (-not (Test-Prereq)) { return }

    Step 'venv · 의존성'
    if (-not (Test-Path $Py)) {
        python -m venv (Join-Path $BackDir '.venv')
        Ok 'venv 생성'
    } else {
        Ok 'venv 이미 있음'
    }
    & $Py -m pip install -e "$BackDir[dev]" --quiet
    Ok '백엔드 의존성'

    Push-Location $FrontDir
    npm install
    Pop-Location
    Ok '프론트 의존성'

    Start-Infra | Out-Null

    Step 'DB 마이그레이션 · 시드'
    Push-Location $BackDir
    & $Py -m alembic upgrade head
    & $Py (Join-Path $Root 'scripts\seed.py')
    Pop-Location
    Ok '완료'

    Write-Host ''
    Write-Host '이제 .\dev.ps1 up 으로 실행하세요.' -ForegroundColor Green
    Write-Host '시드 계정: admin / admin1234!   ·   sindong / village1234!'
}

function Invoke-Up {
    if (-not (Test-Prereq)) { return }
    if (-not (Start-Infra)) { return }
    Start-Backend
    if (-not $NoFront) { Start-Frontend }
    Start-Mock $Mock
    Write-Host ''
    Show-Status
}

function Invoke-Down {
    Step '종료'
    Stop-Port $PORT_API '백엔드'
    Stop-Port $PORT_WEB '프론트'
    Stop-Mock
    docker compose -f $Compose down
    Ok '인프라 종료'
    Write-Host ''
    Write-Host 'DB 데이터는 볼륨에 남는다. 완전히 지우려면:' -ForegroundColor DarkGray
    Write-Host '  docker compose -f docker-compose.dev.yml down -v' -ForegroundColor DarkGray
}

function Show-Status {
    Step '상태'
    docker compose -f $Compose ps --format 'table {{.Name}}\t{{.Status}}'

    Write-Host ''
    foreach ($x in @(
        @{ p = $PORT_API;  n = '백엔드   ' },
        @{ p = $PORT_WEB;  n = '프론트   ' },
        @{ p = $PORT_PG;   n = 'postgres ' },
        @{ p = $PORT_MQTT; n = 'mosquitto' },
        @{ p = $PORT_ICE;  n = 'icecast  ' }
    )) {
        if (Test-Port $x.p) {
            Write-Host ("  {0} :{1}  떠 있음" -f $x.n, $x.p) -ForegroundColor Green
        } else {
            Write-Host ("  {0} :{1}  꺼짐"   -f $x.n, $x.p) -ForegroundColor DarkGray
        }
    }

    if (Test-Port $PORT_API) {
        try {
            $h = Invoke-RestMethod "http://localhost:$PORT_API/health" -TimeoutSec 5
            Write-Host ''
            Write-Host "  /health  db=$($h.database)  mqtt=$($h.mqtt)" -ForegroundColor Green
        } catch {
            Write-Host ''
            Warn '/health 응답 없음'
        }
    }
}

function Show-Help {
@'
xWIFI 마을방송 — 개발 환경 실행기

  .\dev.ps1 setup     최초 1회. venv · 의존성 · DB 마이그레이션 · 시드까지.
  .\dev.ps1 up        전부 실행. 백엔드/프론트/목단말이 각각 새 창으로 뜬다.
  .\dev.ps1 down      전부 종료 (컨테이너 포함). DB 데이터는 남는다.
  .\dev.ps1 status    무엇이 떠 있는지 확인.
  .\dev.ps1 infra     도커 컨테이너만 실행.
  .\dev.ps1 logs      도커 로그 따라가기 (Ctrl+C 로 빠져나옴).
  .\dev.ps1 restart   down 후 up.

옵션
  -Mock <N>     가짜 단말 수 (기본 0 = 안 띄움). 실물 없이 시험할 때만 쓴다.
  -NoFront      프론트 없이 백엔드만

예시
  .\dev.ps1 up               실물 단말로 테스트 (가짜 단말 없음)
  .\dev.ps1 up -Mock 5       실물 없이 가짜 단말 5대로 시험
  
  # 모니터링 스크립트 실행(실물 단말이 MQTT 로 보내는 메시지 확인)
  cd xwifi-server\backend
  .\.venv\Scripts\python.exe ..\scripts\mqtt_monitor.py

  .\dev.ps1 up -NoFront       API 만 만질 때

주소
  화면      http://localhost:5173
  API 문서  http://localhost:8080/docs
  상태      http://localhost:8080/health

시드 계정
  admin   / admin1234!     super_admin (전체)
  sindong / village1234!   village_admin (신동마을만)
'@ | Write-Host
}

switch ($Command) {
    'setup'   { Invoke-Setup }
    'up'      { Invoke-Up }
    'down'    { Invoke-Down }
    'status'  { Show-Status }
    'infra'   { Test-Prereq | Out-Null; Start-Infra | Out-Null }
    'logs'    { docker compose -f $Compose logs -f }
    'restart' { Invoke-Down; Start-Sleep -Seconds 2; Invoke-Up }
    default   { Show-Help }
}
