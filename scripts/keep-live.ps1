# Keeps VoltGrid reachable from the internet while this PC stays on.
# Cloudflare reconnects the same link if the network blips. If the
# tunnel process itself exits, this script starts it again.

$root = Split-Path $PSScriptRoot -Parent
$urlFile = Join-Path $root "public-url.txt"
$cf = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$python = Join-Path $root ".venv\Scripts\python.exe"

function Test-Local {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (-not (Test-Local)) {
    $env:DATABASE_URL = "sqlite:///$($root -replace '\\','/')/voltgrid.db"
    $env:SEED_ON_STARTUP = "true"
    $env:JWT_SECRET = "local-demo-secret-for-browser"
    $env:ALLOW_METER_SIMULATION = "true"
    $env:RUN_RECONCILER = "false"
    Start-Process -FilePath $python -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WorkingDirectory $root -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

Write-Output "voltgrid keep-live watching http://127.0.0.1:8000"

while ($true) {
    $log = Join-Path $env:TEMP "voltgrid-cloudflared.log"
    if (Test-Path $log) { Remove-Item $log -Force }
    $proc = Start-Process -FilePath $cf -ArgumentList @(
        "tunnel", "--url", "http://127.0.0.1:8000", "--no-autoupdate", "--protocol", "http2"
    ) -RedirectStandardError $log -PassThru -WindowStyle Hidden

    $found = $false
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline -and -not $proc.HasExited) {
        if (Test-Path $log) {
            $text = Get-Content $log -Raw -ErrorAction SilentlyContinue
            if ($text -match "https://[a-z0-9-]+\.trycloudflare\.com") {
                $url = $Matches[0]
                Set-Content -Path $urlFile -Value $url
                Write-Output "PUBLIC $url"
                $found = $true
                break
            }
        }
        Start-Sleep -Milliseconds 400
    }

    if (-not $found) {
        Write-Output "waiting for tunnel address..."
    }

    Wait-Process -Id $proc.Id
    Write-Output "$(Get-Date -Format o) tunnel stopped, starting again"
    Start-Sleep -Seconds 2
}
