# Setup completo do projeto num comando so -- ve setup.sh para a versao
# bash/Git Bash/Linux/Mac equivalente. Idempotente: pode rodar de novo sem
# problema (ex. depois de um `git pull`).
#
# Uso: powershell -ExecutionPolicy Bypass -File .\setup.ps1
# (o bypass so vale pra este processo do PowerShell, nao muda nada
# permanentemente na sua maquina)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Write-Step($msg) { Write-Host "=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[aviso] $msg" -ForegroundColor Yellow }
function Write-ErrMsg($msg) { Write-Host "[erro] $msg" -ForegroundColor Red }

Write-Step "1/5: Localizando o Python"
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-ErrMsg "Python nao encontrado. Instale o Python 3.11+ em https://www.python.org/downloads/ e rode este script de novo."
    exit 1
}
$pythonPath = & $pythonCmd.Source -c "import sys; print(sys.executable)"
if (-not $pythonPath -or $LASTEXITCODE -ne 0) {
    # "python" no PATH pode ser so o atalho da Microsoft Store (App Execution
    # Alias) sem nenhum Python de fato instalado por tras -- ele "existe" pro
    # Get-Command, mas rodar qualquer coisa com ele so imprime uma mensagem
    # pedindo pra instalar do Store, sem executar nada.
    Write-ErrMsg "O Python encontrado ($($pythonCmd.Source)) nao roda de fato -- ele e provavelmente"
    Write-ErrMsg "so o atalho da Microsoft Store (App Execution Alias), sem nenhum Python instalado"
    Write-ErrMsg "por tras. Instale o Python 3.11+ em https://www.python.org/downloads/ e rode este"
    Write-ErrMsg "script de novo (abra um terminal novo depois de instalar)."
    exit 1
}
if ($pythonPath -like "*WindowsApps*") {
    Write-Warn "O Python encontrado ($pythonPath) parece ser o da Microsoft Store."
    Write-Warn "Isso pode causar um bug real neste projeto: pacotes R instalados depois de"
    Write-Warn "criar o venv ficam invisiveis pros subprocessos R do harness, mesmo com os"
    Write-Warn "pacotes R instalados corretamente (ver README.md, secao 'Requisitos')."
    Write-Warn "Recomendado: instale o Python via https://python.org e rode este script de novo."
    $resp = $null
    $interactive = $true
    try { $resp = Read-Host "Continuar mesmo assim? [s/N]" } catch { $interactive = $false }
    if ($interactive) {
        if ($resp -notmatch '^[sS]') { exit 1 }
    } else {
        Write-Warn "Sem terminal interativo -- continuando mesmo assim."
    }
}
Write-Ok "Usando Python: $pythonPath"

Write-Step "2/5: Ambiente virtual (.venv)"
if (-not (Test-Path .venv)) {
    & $pythonCmd.Source -m venv .venv
    Write-Ok "venv criado."
} else {
    Write-Ok "venv ja existe, reaproveitando."
}
$venvPython = ".venv\Scripts\python.exe"

Write-Step "3/5: Dependencias Python"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt --quiet
Write-Ok "Dependencias Python instaladas."

Write-Step "4/5: R e seus pacotes"
$rscript = Get-Command Rscript -ErrorAction SilentlyContinue
$rscriptPath = $null
if ($rscript) {
    $rscriptPath = $rscript.Source
} else {
    # Mesmos locais que harness/orchestrator.py::find_rscript() tenta quando
    # Rscript nao esta no PATH -- manter os dois em sincronia se um mudar.
    $candidates = @(Get-ChildItem "C:\Program Files\R\R-*\bin\Rscript.exe" -ErrorAction SilentlyContinue) +
                  @(Get-ChildItem "C:\Program Files (x86)\R\R-*\bin\Rscript.exe" -ErrorAction SilentlyContinue)
    if ($candidates.Count -gt 0) { $rscriptPath = $candidates[0].FullName }
}

if (-not $rscriptPath) {
    Write-Warn "Rscript nao encontrado. Instale o R 4.x em https://www.r-project.org e rode"
    Write-Warn "'Rscript r/install_packages.R' manualmente depois (ou rode este script de novo)."
} else {
    Write-Ok "Usando $rscriptPath"
    & $rscriptPath r\install_packages.R
}

Write-Step "5/5: Navegador Chromium (pro relatorio em PDF)"
# Mesmos locais que harness/pdf_report.py::_find_chromium_browser() tenta.
$browserFound = $false
foreach ($name in @("msedge", "chrome", "google-chrome", "chromium")) {
    if (Get-Command $name -ErrorAction SilentlyContinue) { $browserFound = $true; break }
}
if (-not $browserFound) {
    $winCandidates = @(
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    )
    foreach ($c in $winCandidates) {
        if (Test-Path $c) { $browserFound = $true; break }
    }
}
if ($browserFound) {
    Write-Ok "Navegador Chromium encontrado."
} else {
    Write-Warn "Nenhum navegador Chromium (Edge/Chrome) encontrado -- o relatorio em PDF vai"
    Write-Warn "falhar ao ser gerado. Instale o Microsoft Edge (padrao no Windows) ou o Google Chrome."
}

Write-Host ""
Write-Host "Tudo pronto. Exemplo pra rodar:" -ForegroundColor Cyan
Write-Host "  $venvPython -m harness data\example\exemplo_1\dasafio_Amplo-eDNA_cipo_subset_output-2026-09-13.csv --groq-api-key <chave>"
