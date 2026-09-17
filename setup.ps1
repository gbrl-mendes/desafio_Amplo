# Setup completo do projeto num comando so -- ve setup.sh para a versao
# bash/Git Bash/Linux/Mac equivalente. Idempotente: pode rodar de novo sem
# problema (ex. depois de um `git pull`).
#
# Uso: powershell -ExecutionPolicy Bypass -File .\setup.ps1
# (o bypass so vale pra este processo do PowerShell, nao muda nada
# permanentemente na sua maquina)
#
# Tenta instalar Python e R sozinho via winget quando nao encontra nenhum
# dos dois (window Package Manager, ja vem com qualquer Windows 10/11
# atualizado) -- a pessoa nao precisa ter instalado nada antes de rodar
# este script. Se o instalador especifico pedir elevacao (UAC), o proprio
# Windows mostra esse prompt; nao ha como evitar isso em todo caso sem
# flags de instalacao "por usuario" que nem todo pacote aceita bem.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Write-Step($msg) { Write-Host "=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[aviso] $msg" -ForegroundColor Yellow }
function Write-ErrMsg($msg) { Write-Host "[erro] $msg" -ForegroundColor Red }

# Depois de instalar algo via winget, o processo atual do PowerShell ainda
# tem o PATH de quando foi aberto -- sem isto, o resto do script nao acharia
# o que acabou de instalar sem reabrir o terminal.
function Refresh-Path {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machinePath;$userPath"
}

function Install-Via-Winget($wingetId, $displayName) {
    # Nao trata codigo de saida != 0 do winget como falha definitiva --
    # "ja instalado, sem atualizacao disponivel" (comum quando o pacote
    # existe mas nao esta no PATH desta sessao) tambem sai com codigo != 0.
    # Quem chamou sempre confere de novo se o programa ficou disponivel
    # depois, em vez de confiar so no codigo de saida.
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Warn "winget nao encontrado -- nao consigo instalar $displayName sozinho nesta maquina."
        return
    }
    Write-Warn "$displayName nao encontrado -- tentando instalar via winget ($wingetId)..."
    Write-Warn "Se aparecer um prompt de permissao do Windows (UAC), aceite pra continuar."
    winget install --id $wingetId --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "winget terminou com codigo $LASTEXITCODE (pode significar so 'ja instalado, sem"
        Write-Warn "atualizacao disponivel') -- conferindo se $displayName ja esta disponivel mesmo assim."
    }
    Refresh-Path
}

Write-Step "1/5: Localizando o Python"
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
$pythonPath = $null
if ($pythonCmd) {
    $pythonPath = & $pythonCmd.Source -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0) { $pythonPath = $null }
    # "python" no PATH pode ser so o atalho da Microsoft Store (App Execution
    # Alias) sem nenhum Python de fato instalado por tras -- ele "existe" pro
    # Get-Command, mas rodar qualquer coisa com ele so imprime uma mensagem
    # pedindo pra instalar do Store, sem executar nada. Trata como "nao
    # encontrado" pra cair no mesmo caminho de instalacao automatica abaixo.
}

if (-not $pythonPath) {
    Install-Via-Winget "Python.Python.3.13" "Python"
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $pythonPath = & $pythonCmd.Source -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -ne 0) { $pythonPath = $null }
    }
}

if (-not $pythonPath) {
    Write-ErrMsg "Nao foi possivel obter um Python funcional (nem encontrado, nem instalavel via winget)."
    Write-ErrMsg "Instale o Python 3.11+ manualmente em https://www.python.org/downloads/ e rode este"
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
function Find-Rscript {
    $rscript = Get-Command Rscript -ErrorAction SilentlyContinue
    if ($rscript) { return $rscript.Source }
    # Mesmos locais que harness/orchestrator.py::find_rscript() tenta quando
    # Rscript nao esta no PATH (o instalador do R nem sempre adiciona --
    # manter os dois em sincronia se um mudar.
    $candidates = @(Get-ChildItem "C:\Program Files\R\R-*\bin\Rscript.exe" -ErrorAction SilentlyContinue) +
                  @(Get-ChildItem "C:\Program Files (x86)\R\R-*\bin\Rscript.exe" -ErrorAction SilentlyContinue)
    if ($candidates.Count -gt 0) { return $candidates[0].FullName }
    return $null
}

$rscriptPath = Find-Rscript
if (-not $rscriptPath) {
    Install-Via-Winget "RProject.R" "R"
    $rscriptPath = Find-Rscript
}

if (-not $rscriptPath) {
    Write-Warn "Rscript nao encontrado (nem instalavel via winget). Instale o R 4.x manualmente em"
    Write-Warn "https://www.r-project.org e rode 'Rscript r/install_packages.R' depois (ou rode"
    Write-Warn "este script de novo)."
} else {
    Write-Ok "Usando $rscriptPath"
    & $rscriptPath r\install_packages.R
}

Write-Step "5/5: Navegador Chromium (pro relatorio em PDF)"
# Mesmos locais que harness/pdf_report.py::find_chromium_browser() tenta.
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
if (-not $browserFound) {
    # Raro (Edge vem por padrao em qualquer Windows 10/11) -- so acontece em
    # instalacoes corporativas que removeram o Edge de proposito.
    Install-Via-Winget "Microsoft.Edge" "Microsoft Edge"
    foreach ($name in @("msedge", "chrome")) {
        if (Get-Command $name -ErrorAction SilentlyContinue) { $browserFound = $true; break }
    }
    if (-not $browserFound -and (Test-Path "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) {
        $browserFound = $true
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
Write-Host "  $venvPython -m harness data\example\exemplo_1\eDNA_cipo_subset.csv --groq-api-key <chave>"
