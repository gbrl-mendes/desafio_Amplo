# Desafio Amplo

Este projeto implementa minha resposta ao desafio técnico do processo seletivo para a vaga de Cientista de Dados Pleno na Amplo Engenharia, desenvolvido segundo os parâmetros pré-definidos para os concorrentes.

O problema resolvido, descrito em detalhes em [Domínio e contrato](DOMINIO_E_CONTRATO.md), é hipotético: uma consultoria ambiental recebe uma tabela bruta de sequências de DNA ambiental e precisa transformá-la numa base de dados confiável sobre quais espécies existem numa área de estudo. Este é o sistema que a cientista de dados dessa consultoria executaria para resolver essa demanda, em qualquer projeto de metabarcoding que receber, independente do grupo taxonômico ou do substrato amostrado. Ele identifica cada sequência taxonomicamente, separa detecção real de contaminação, confere se o tamanho é compatível com o marcador genético usado, e, para os casos que essas três etapas não resolvem sozinhas, consulta um modelo de linguagem (LLM) com a evidência já calculada para uma segunda opinião. No final, o profissional humano ainda tem a possibilidade de definir, com base em tudo que foi gerado, quais são as identificações mais parcimoniosas.

Veja [Domínio e contrato](DOMINIO_E_CONTRATO.md) para o problema, o domínio e as limitações declaradas deste sistema; [Relatório exemplo](RELATORIO_EXEMPLO.html) para uma execução real sobre o dado de demonstração, com achados concretos; e [Documentação completa](DOCUMENTATION.md) para a arquitetura completa, todos os parâmetros de configuração e todos os exemplos de uso. Por último, o documento [Decisões e limitações](DECISOES_E_LIMITACOES.md) oferece uma visão geral sobre o que foi decido durante o desenvolvimento deste projeto.

## Requisitos

O script de instalação (ver "Instalação" abaixo) tenta resolver tudo isso sozinho: detecta o que falta e instala automaticamente (winget no Windows, Homebrew no Mac, apt/dnf no Linux). O que segue é o detalhe de cada dependência, útil se a instalação automática não for possível na sua máquina ou se algo não sair como esperado.

- **Python 3.11+**, num ambiente virtual próprio do projeto (criado automaticamente pelo instalador). Se o instalador não encontrar Python, instala a versão do [python.org](https://www.python.org/downloads/) via `winget/Homebrew/apt`,  nunca a versão da Microsoft Store, que tem um bug real com este projeto: ela roda com uma virtualização de sistema de arquivos que pode fazer pacotes R instalados depois do venv criado ficarem invisíveis para os subprocessos R deste harness, mesmo com `Rscript r/install_packages.R` rodando sem erro. Se você já tinha um Python da Store instalado antes de rodar o instalador, ele detecta isso e avisa, perguntando se quer continuar mesmo assim.
- **R 4.x** com os pacotes listados em [`r/install_packages.R`](r/install_packages.R), instalado e configurado automaticamente se não for encontrado. O harness (`python -m harness`) sempre localiza o R sozinho, mesmo sem ele estar no PATH do sistema; só um comando manual direto (`Rscript ...`, fora do instalador ou do harness) pode precisar do caminho completo, se o instalador do R não tiver adicionado `Rscript` ao PATH.
- **Um navegador baseado em Chromium** (Edge ou Chrome), necessário para gerar o relatório em PDF. Já vem instalado por padrão em praticamente qualquer Windows 10/11 (Edge) e é comum em Mac/Linux; o instalador confere e avisa se não encontrar nenhum.
- Acesso à internet durante a execução: as consultas ao NCBI (taxonomia), ao GBIF (ocorrência regional) e a geração do relatório em PDF fazem parte do pipeline, não só a curadoria assistida por LLM.
- Opcional: uma chave de API gratuita da [Groq](https://console.groq.com/keys) para a curadoria assistida e o relatório narrativo (ver [Documentação completa](DOCUMENTATION.md#configuração-da-chave-da-groq)). Sem chave, as duas etapas são puladas automaticamente e o resto do pipeline roda normalmente.

## Instalação

1. **Obtenha o projeto.**

```bash
git clone https://github.com/gbrl-mendes/desafio_Amplo.git
cd desafio_Amplo
```

Sem Git instalado, ou preferindo não instalar: baixe como ZIP pela página do repositório (botão verde **Code** → **Download ZIP**), extraia, e abra um terminal dentro da pasta extraída. Passo a passo completo, com solução de problemas comuns, em [Como baixar](COMO_BAIXAR.md).

2. **Instale as dependências.**

Um comando só: se Python ou R não estiverem instalados, tenta instalar os dois sozinho (winget no Windows, Homebrew no Mac, apt/dnf no Linux -- se nenhum gerenciador estiver disponível, pede instalação manual); depois cria o venv, instala as dependências Python, instala os pacotes R e confere se há um navegador Chromium disponível (necessário pro relatório em PDF). Idempotente, pode rodar de novo sem problema (ex. depois de um `git pull`):

```powershell
# PowerShell (Windows)
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

```bash
# bash / Git Bash / WSL / Linux / Mac
bash setup.sh
```

## Execução

Depois de instalar, de dentro da pasta `desafio_Amplo`:

```powershell
# PowerShell (Windows)
.venv\Scripts\python.exe -m harness data/example/exemplo_1/eDNA_cipo_subset.csv --reference data/example/exemplo_1/spp_tradicional.csv --ecologia --groq-api-key <chave>
```

```bash
# bash / Git Bash / WSL / Linux / Mac
.venv/bin/python -m harness data/example/exemplo_1/eDNA_cipo_subset.csv --reference data/example/exemplo_1/spp_tradicional.csv --ecologia --groq-api-key <chave>
```

O comando acima executa a curadoria completa sobre o primeiro conjunto de dados de exemplo (planilha `eDNA_cipo_subset.csv`), usa um dataset de espécies pré-detectadas na área como referência para a curadoria (parâmetro `--reference`, planilha `spp_tradicional.csv`) e por último executa um pequeno conjunto de análises ecológicas a partir dos resultados curados pela LLM (parâmetro `--ecologia`). No final é gerado um relatório em HTML, onde é possível acessar os resultados de cada etapa do processamento. 

Todos os parâmetros (`--config`, `--reference`, `--ecologia`, `--ecologia-somente`, `--llm-mode`, entre outros) e mais exemplos de execução estão detalhados em [Documentação completa](DOCUMENTATION.md#execução).

## Rodando os testes

Camada Python (`harness/`):

```powershell
# PowerShell (Windows)
.venv\Scripts\python.exe -m pytest tests/ -v
```

```bash
# bash / Git Bash / WSL / Linux / Mac
.venv/bin/python -m pytest tests/ -v
```

Camada R (`r/curadoria_deterministica.R`):

```bash
Rscript r/tests/run_tests.R
```

## Contato

Para mais informações, entre em contato comigo através do meu endereço de [e-mail](mailto:gabrielmendesbrt@outlook.com) 😊
