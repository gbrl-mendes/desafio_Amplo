#!/usr/bin/env bash
# Setup completo do projeto num comando so: cria o venv, instala as
# dependencias Python, instala os pacotes R, e confere se ha um navegador
# Chromium disponivel (necessario pro relatorio em PDF). Idempotente -- pode
# rodar de novo sem problema (ex. depois de um `git pull`).
#
# Uso (bash, Git Bash, WSL, Linux, Mac): bash setup.sh
# Para PowerShell nativo no Windows, use setup.ps1 em vez deste (inclusive
# pra instalacao automatica de Python/R via winget -- este script so tenta
# isso no Mac via Homebrew e no Linux via apt/dnf, ver install_via_pkg_manager
# abaixo; no Git Bash/WSL sobre Windows, sem nenhum dos dois disponiveis,
# cai pra pedir instalacao manual, do mesmo jeito que sempre fez).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
warn() { printf '\033[33m[aviso]\033[0m %s\n' "$1"; }
err()  { printf '\033[31m[erro]\033[0m %s\n' "$1"; }
ok()   { printf '\033[32m[ok]\033[0m %s\n' "$1"; }

OS_NAME="$(uname -s)"

# Rodar o script inteiro com sudo/como root normalmente nao e necessario --
# os passos que exigem privilegio (instalar pacotes do sistema via apt/dnf,
# ver install_via_pkg_manager abaixo) ja chamam sudo por conta propria
# quando precisam. Fazer isso no script inteiro pode deixar ".venv" com o
# dono errado pro uso normal depois (foi exatamente isso que aconteceu numa
# maquina real: a primeira tentativa sem sudo falhou no meio por falta do
# modulo venv/pip do sistema, e rodar de novo com sudo criou um .venv
# incompleto e com dono misto).
if command -v id >/dev/null 2>&1 && [ "$(id -u)" = "0" ]; then
  warn "Rodando como root/sudo -- normalmente nao precisa disso pra este script."
  warn "Os passos que exigem privilegio (instalar pacotes do sistema) ja chamam sudo"
  warn "sozinhos quando necessario. Rodar o script inteiro como root pode deixar '.venv'"
  warn "com o dono errado pro seu uso normal depois. Considere interromper (Ctrl+C) e"
  warn "rodar de novo sem sudo."
fi

# Tenta instalar $1 (nome exibido) via Homebrew (Mac) ou apt/dnf (Linux),
# com os pacotes certos pra cada gerenciador ($2 = pacotes pro apt, $3 =
# pacotes pro dnf, $4 = pacote(s) pro brew). Devolve 1 (falha) sem erro se
# nenhum gerenciador estiver disponivel -- quem chamou decide o que fazer.
install_via_pkg_manager() {
  local display_name="$1" apt_pkgs="$2" dnf_pkgs="$3" brew_pkgs="$4"
  if [ "$OS_NAME" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
    warn "$display_name nao encontrado -- tentando instalar via Homebrew ($brew_pkgs)..."
    # shellcheck disable=SC2086
    brew install $brew_pkgs && { hash -r; return 0; }
    warn "Homebrew nao conseguiu instalar $display_name."
    return 1
  elif command -v apt-get >/dev/null 2>&1; then
    warn "$display_name nao encontrado -- tentando instalar via apt ($apt_pkgs), pode pedir sua senha (sudo)..."
    # shellcheck disable=SC2086
    sudo apt-get update -qq && sudo apt-get install -y $apt_pkgs && { hash -r; return 0; }
    warn "apt nao conseguiu instalar $display_name."
    return 1
  elif command -v dnf >/dev/null 2>&1; then
    warn "$display_name nao encontrado -- tentando instalar via dnf ($dnf_pkgs), pode pedir sua senha (sudo)..."
    # shellcheck disable=SC2086
    sudo dnf install -y $dnf_pkgs && { hash -r; return 0; }
    warn "dnf nao conseguiu instalar $display_name."
    return 1
  else
    warn "Nenhum gerenciador de pacotes conhecido (Homebrew/apt/dnf) disponivel --"
    warn "nao consigo instalar $display_name sozinho nesta maquina."
    return 1
  fi
}

bold "=== 1/5: Localizando o Python ==="
PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN=python
  fi
fi

if [ -z "$PYTHON_BIN" ]; then
  if install_via_pkg_manager "Python" "python3 python3-venv python3-pip" "python3 python3-pip" "python"; then
    if command -v python3 >/dev/null 2>&1; then PYTHON_BIN=python3; fi
  fi
fi

if [ -z "$PYTHON_BIN" ]; then
  err "Python nao encontrado (nem instalavel automaticamente nesta maquina)."
  err "Instale o Python 3.11+ manualmente em https://www.python.org/downloads/ e rode"
  err "este script de novo."
  exit 1
fi

PYTHON_PATH="$("$PYTHON_BIN" -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
if [ -z "$PYTHON_PATH" ]; then
  # "$PYTHON_BIN" pode ser so o atalho da Microsoft Store (App Execution
  # Alias) sem nenhum Python de fato instalado por tras -- ele "existe" pro
  # command -v, mas rodar qualquer coisa com ele so imprime uma mensagem
  # pedindo pra instalar do Store, sem executar nada.
  err "O Python encontrado ($PYTHON_BIN) nao roda de fato -- ele e provavelmente so o"
  err "atalho da Microsoft Store (App Execution Alias), sem nenhum Python instalado por"
  err "tras. Instale o Python 3.11+ em https://www.python.org/downloads/ e rode este"
  err "script de novo (abra um terminal novo depois de instalar)."
  exit 1
fi
case "$PYTHON_PATH" in
  *WindowsApps*)
    warn "O Python encontrado ($PYTHON_PATH) parece ser o da Microsoft Store."
    warn "Isso pode causar um bug real neste projeto: pacotes R instalados depois de"
    warn "criar o venv ficam invisiveis pros subprocessos R do harness, mesmo com os"
    warn "pacotes R instalados corretamente (ver README.md, secao 'Requisitos')."
    warn "Recomendado: instale o Python via https://python.org e rode este script de novo."
    if [ -t 0 ]; then
      read -r -p "Continuar mesmo assim? [s/N] " resp
      case "$resp" in [sS]*) ;; *) exit 1 ;; esac
    else
      warn "Sem terminal interativo -- continuando mesmo assim."
    fi
    ;;
esac
ok "Usando $PYTHON_BIN ($PYTHON_PATH)"

bold "=== 2/5: Ambiente virtual (.venv) ==="
venv_python_path() {
  if [ -f .venv/Scripts/python.exe ]; then
    echo .venv/Scripts/python.exe  # Windows
  elif [ -f .venv/bin/python ]; then
    echo .venv/bin/python           # Linux/Mac
  fi
}

venv_is_functional() {
  local vp
  vp="$(venv_python_path)"
  [ -n "$vp" ] && "$vp" -m pip --version >/dev/null 2>&1
}

if [ -d .venv ] && ! venv_is_functional; then
  # Existe mas nao tem pip -- provavelmente de uma tentativa anterior que
  # falhou no meio (ex. sem o modulo venv/ensurepip do sistema, comum no
  # Debian/Ubuntu -- ver abaixo). Reaproveitar um venv assim so espalharia
  # o mesmo problema (era exatamente o "No module named pip" que apareceu
  # numa maquina real depois de uma tentativa quebrada).
  warn ".venv existe mas esta incompleto (sem pip) -- removendo e recriando do zero."
  if ! rm -rf .venv 2>/dev/null; then
    err "Nao consegui remover '.venv' (pode ter sido criado com sudo antes, com outro dono)."
    err "Rode 'sudo rm -rf .venv' manualmente e depois este script de novo, sem sudo."
    exit 1
  fi
fi

if [ ! -d .venv ]; then
  venv_err="$(mktemp)"
  if ! "$PYTHON_BIN" -m venv .venv 2>"$venv_err"; then
    cat "$venv_err" >&2
    rm -f "$venv_err"
    # No Debian/Ubuntu, o pacote "python3" base nao inclui o modulo venv/pip
    # (fica em "python3-venv"/"python3-pip" separados) -- diferente do caso
    # "python3 nao encontrado" tratado no passo 1, aqui o python3 EXISTE,
    # so esta incompleto, entao so cai aqui quando a criacao de fato falha.
    warn "Falha ao criar o venv -- tentando instalar o modulo venv/pip via gerenciador de"
    warn "pacotes do sistema..."
    install_via_pkg_manager "python3-venv/pip" "python3-venv python3-pip" "python3-pip" "python" || true
    hash -r
    rm -rf .venv
    if ! "$PYTHON_BIN" -m venv .venv; then
      err "Ainda nao consegui criar o venv. Instale manualmente o pacote 'python3-venv'"
      err "(Debian/Ubuntu) ou equivalente da sua distro e rode este script de novo."
      exit 1
    fi
  else
    rm -f "$venv_err"
  fi
  ok "venv criado."
else
  ok "venv ja existe e funciona, reaproveitando."
fi

VENV_PYTHON="$(venv_python_path)"

bold "=== 3/5: Dependencias Python ==="
"$VENV_PYTHON" -m pip install --upgrade pip --quiet
"$VENV_PYTHON" -m pip install -r requirements.txt --quiet
ok "Dependencias Python instaladas."

bold "=== 4/5: R e seus pacotes ==="
find_rscript() {
  if command -v Rscript >/dev/null 2>&1; then
    command -v Rscript
    return 0
  fi
  # Mesmos locais que harness/orchestrator.py::find_rscript() tenta quando
  # Rscript nao esta no PATH -- manter os dois em sincronia se um mudar. O
  # local por usuario (sem admin) foi confirmado numa maquina real: `winget
  # install RProject.R` sem privilegio de administrador instala em
  # %LOCALAPPDATA%\Programs\R, nao em "C:\Program Files\R". LOCALAPPDATA
  # chega aqui como caminho estilo Windows (barras invertidas) -- cygpath
  # (vem com o Git Bash) converte pro estilo /c/... que o bash entende.
  local_app_data_posix="/c/no-such-path"
  if [ -n "${LOCALAPPDATA:-}" ] && command -v cygpath >/dev/null 2>&1; then
    local_app_data_posix="$(cygpath "$LOCALAPPDATA")"
  fi
  for candidate in "/c/Program Files/R"/R-*/bin/Rscript.exe "/c/Program Files (x86)/R"/R-*/bin/Rscript.exe \
    "$local_app_data_posix/Programs/R"/R-*/bin/Rscript.exe; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

RSCRIPT_BIN="$(find_rscript || true)"
if [ -z "$RSCRIPT_BIN" ]; then
  if install_via_pkg_manager "R" "r-base" "R" "r"; then
    RSCRIPT_BIN="$(find_rscript || true)"
  fi
fi

if [ -z "$RSCRIPT_BIN" ]; then
  warn "Rscript nao encontrado (nem instalavel automaticamente nesta maquina). Instale o"
  warn "R 4.x manualmente em https://www.r-project.org e rode 'Rscript r/install_packages.R'"
  warn "depois (ou rode este script de novo)."
else
  ok "Usando $RSCRIPT_BIN"
  if [ "$OS_NAME" = "Linux" ]; then
    # Varios pacotes R deste projeto compilam contra bibliotecas C externas
    # (libxml2, openssl, libcurl, fontconfig/freetype, harfbuzz/fribidi,
    # libuv, libpng/libtiff/libjpeg) -- sem os headers de desenvolvimento
    # dessas bibliotecas instalados no sistema, a compilacao falha. No
    # Windows/Mac isso nunca aparece porque o CRAN ja tem binario pronto.
    # Confirmado numa maquina real: 39 pacotes R falharam em cascata por
    # falta exatamente disso (tidyverse/taxize/rgbif dependem de curl/xml2/
    # openssl, que dependem dessas bibliotecas de sistema).
    install_via_pkg_manager "bibliotecas de desenvolvimento (libxml2, openssl, libcurl, fontconfig, etc.)" \
      "build-essential libcurl4-openssl-dev libssl-dev libxml2-dev libfontconfig1-dev libharfbuzz-dev libfribidi-dev libfreetype6-dev libpng-dev libtiff5-dev libjpeg-dev libuv1-dev" \
      "gcc gcc-c++ make libcurl-devel openssl-devel libxml2-devel fontconfig-devel harfbuzz-devel fribidi-devel freetype-devel libpng-devel libtiff-devel libjpeg-turbo-devel libuv-devel" \
      "" || true
  fi
  "$RSCRIPT_BIN" r/install_packages.R
fi

bold "=== 5/5: Navegador Chromium (pro relatorio em PDF) ==="
# Mesmos locais que harness/pdf_report.py::find_chromium_browser() tenta.
if command -v msedge >/dev/null 2>&1 || command -v chrome >/dev/null 2>&1 \
  || command -v google-chrome >/dev/null 2>&1 || command -v chromium >/dev/null 2>&1 \
  || [ -e "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" ] \
  || [ -e "/c/Program Files/Microsoft/Edge/Application/msedge.exe" ] \
  || [ -e "/Applications/Google Chrome.app" ]; then
  ok "Navegador Chromium encontrado."
else
  warn "Nenhum navegador Chromium (Edge/Chrome) encontrado -- o relatorio em PDF vai"
  warn "falhar ao ser gerado. Instale o Microsoft Edge (padrao no Windows) ou o Google Chrome."
fi

echo
bold "Tudo pronto. Exemplo pra rodar:"
echo "  $VENV_PYTHON -m harness data/example/exemplo_1/eDNA_cipo_subset.csv --groq-api-key <chave>"
