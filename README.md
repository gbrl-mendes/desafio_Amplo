# Desafio Amplo

Este projeto implementa minha resposta ao desafio técnico do processo seletivo para a vaga de Cientista de Dados Pleno na Amplo Engenharia, desenvolvido segundo os parâmetros pré-definidos para os concorrentes.

O problema resolvido, descrito em detalhes em [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md), é hipotético: uma consultoria ambiental recebe uma tabela bruta de sequências de DNA ambiental e precisa transformá-la numa base de dados confiável sobre quais espécies existem numa área de estudo. Este é o sistema que a cientista de dados dessa consultoria executaria para resolver essa demanda, em qualquer projeto de metabarcoding que receber, independente do grupo taxonômico ou do substrato amostrado. Ele identifica cada sequência taxonomicamente, separa detecção real de contaminação, confere se o tamanho é compatível com o marcador genético usado, e, para os casos que essas três etapas não resolvem sozinhas, consulta um modelo de linguagem (LLM) com a evidência já calculada para uma segunda opinião. O resultado dessa segunda opinião nunca sobrescreve o resultado determinístico, entra como colunas adicionais. No final, o profissional humano ainda tem a possibilidade de definir, com base em tudo que foi gerado, quais são as identificações mais parcimoniosas.

Veja [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md) para o problema, o domínio e as limitações declaradas deste sistema; [RELATORIO_EXEMPLO.md](RELATORIO_EXEMPLO.md) para uma execução real sobre o dado de demonstração, com achados concretos; e [DOCUMENTATION.md](DOCUMENTATION.md) para a arquitetura completa, todos os parâmetros de configuração e todos os exemplos de uso.

## Requisitos

- **Python 3.11+**, de preferência num ambiente virtual (`pip install -r requirements.txt`). **No Windows, não use o Python da Microsoft Store** para criar esse ambiente virtual -- ele roda com uma virtualização de sistema de arquivos que pode fazer pacotes R instalados depois do venv criado ficarem invisíveis para os subprocessos R deste harness, mesmo com `Rscript r/install_packages.R` rodando sem erro (o harness detecta esse caso e avisa na tela, mas evitar já de início é mais simples). Instale o Python direto de [python.org](https://www.python.org/downloads/) em vez disso; para conferir se um Python já instalado é da Store, rode `python -c "import sys; print(sys.executable)"` e veja se o caminho contém `WindowsApps`.
- **R 4.x** com os pacotes listados em [`r/install_packages.R`](r/install_packages.R). **No Windows, o instalador do R nem sempre adiciona `Rscript` ao PATH** -- se um comando manual der "'Rscript' is not recognized", chame pelo caminho completo ou adicione a pasta `bin` do R ao PATH do seu usuário. Isso afeta só comandos manuais: o harness (`python -m harness`) já procura o R sozinho.
- Acesso à internet durante a execução: as consultas ao NCBI (taxonomia) e ao GBIF (ocorrência regional) fazem parte do pipeline determinístico, não só da parte de LLM.
- Opcional: uma chave de API gratuita da [Groq](https://console.groq.com/keys) para a curadoria assistida e o relatório narrativo (ver [DOCUMENTATION.md](DOCUMENTATION.md#configuração-da-chave-da-groq-llm)). Sem chave, as duas etapas são puladas automaticamente e o resto do pipeline roda normalmente.

## Instalação

Um comando só, cria o venv, instala as dependências Python, instala os pacotes R e confere se há um navegador Chromium disponível (necessário pro relatório em PDF) -- idempotente, pode rodar de novo sem problema (ex. depois de um `git pull`):

```powershell
# PowerShell (Windows)
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

```bash
# bash / Git Bash / WSL / Linux / Mac
bash setup.sh
```

## Execução

```bash
.venv\Scripts\python.exe -m harness data/example/exemplo_1/dasafio_Amplo-eDNA_cipo_subset_output-2026-09-13.csv --groq-api-key <chave>
```

Isso roda a curadoria completa sobre o dado de demonstração  e grava tudo em `runs/<run_id>/`: o CSV final, o log da execução, e o relatório narrativo em PDF.

Todos os parâmetros (`--config`, `--reference`, `--ecologia`, `--ecologia-somente`, `--llm-mode`, entre outros), mais exemplos com cada um deles, estão em [DOCUMENTATION.md](DOCUMENTATION.md#execução).

Código de saída do processo: `0` sucesso, `2` entrada recusada pela validação, `3` falha na execução.

## Rodando os testes

Camada Python (`harness/`):

```bash
.venv\Scripts\python.exe -m pytest tests/ -v
```

Camada R (`r/curadoria_deterministica.R`):

```bash
Rscript r/tests/run_tests.R
```

## Contato

Para mais informações, entre em contato comigo através do meu endereço de [e-mail](mailto:gabrielmendesbrt@outlook.com) 😊
