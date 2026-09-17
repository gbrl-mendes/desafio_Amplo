Decisões, premissas e limitações
================================

Este documento reúne, num só lugar, as principais decisões de arquitetura tomadas ao longo do desenvolvimento, as premissas assumidas sem verificação automática, e as limitações conhecidas do sistema. Complementa, não substitui, os comentários seção a seção em [`r/curadoria_deterministica.qmd`](https://claude.ai/chat/r/curadoria_deterministica.qmd)  e a tabela de riscos em [Domínio e contrato](https://claude.ai/chat/DOMINIO_E_CONTRATO.md).


## Decisões de arquitetura

### Divisão em três módulos

Curadoria determinística, curadoria assistida por LLM e análise ecológica são três módulos distintos, não um script contínuo. A curadoria determinística nunca depende da assistida para produzir uma saída válida (`Curated ID` cai inteira para a identificação determinística se a LLM estiver desligada). A análise ecológica só lê o CSV já curado, nunca refaz identificação nenhuma, e pode rodar numa execução totalmente separada (`--ecologia-somente`), inclusive sobre um CSV revisado artesanalmente. Essa separação existiu desdeo início por decisão consciente: cada módulo responde a uma pergunta diferente (qual é aespécie / a identificação é confiável o bastante / o que essa comunidade de espécies revela), e misturá-los numa execução monolítica dificultaria tanto testar quanto reutilizar cada parte isoladamente. 

### R e Python possuem papéis distintos

A lógica determinística (BLAST, taxonomia, contaminação, árvore filogenética, GBIF) é escrita em R porque o material de referência que eu uso para trabalhar já existia em R. O harness que orquestra tudo, valida entrada, chama a LLM e gera relatórios é em Python. A escolha não é por preferência de linguagem em si, é subordinada a onde a lógica de domínio já existia de forma testada.

### O que vai para a LLM

A curadoria assistida por LLM só é acionada para sequências cuja identificação determinística (código em R) não foi conclusiva (não chegou a espécie, hit de BLAST não confiável, ou espécie sem nenhumregistro de ocorrência regional). Identificações já confiáveis não passam pela LLM. Isso é ao mesmo tempo a estratégia de conservar contexto do trabalho (a LLM nunca vê o lote inteiro, só a fração ambígua) e o critério de quando uma etapa é ou não necessária: a maior parte dos casos (na execução de exemplo documentada, mais da metade) já é resolvida com confiança só pelo determinístico. Também existe o objetivo de preservar recursos, já que a LLM opera em um plano gratuito.

### Parâmetros de curadoria calibrados contra dado real, não estimados

A faixa de tamanho esperada de amplicon, os limiares de pseudo-score por nível taxonômico, e o número de vizinhos filogenéticos usados como evidência (`k = 5`) não foram escolhidos por intuição: os dois primeiros seguem os valores já usados no pipeline de referência; o terceiro foi testado contra uma árvore filogenética real e 4 casos documentados de correção manual de identificação, e o valor 5 foi o que reproduziu a mesma conclusão do especialista humano nesses casos sem diluir o sinal com vizinhos genuinamente distantes.

### Área geográfica calculada a partir dos próprios dados, plausibilidade decidida pela LLM

A área usada para consultar ocorrência regional no GBIF é um retângulo calculado automaticamente a partir das coordenadas presentes na própria entrada (mínimo e máximo de latitude/longitude), não uma área fixa declarada manualmente por projeto. A escolha de por que uma espécie faz ou não sentido nessa região, porém, nunca é um cálculo geométrico: é sempre um julgamento da curadoria assistida por LLM, usando a contagem de registros do GBIF como evidência. Essa divisão existe porque a plausibilidade biológica de uma espécie numa área nunca foi, nem no processo manual que este sistema busca apoiar, uma questão de distância geométrica pura.

### `Curated ID` pré-preenchida automaticamente

O CSV final sempre traz uma coluna `Curated ID`, preenchida automaticamente (assistida por LLM quando existir, senão a determinística). O nome foi mantido de propósito igual ao que um especialista humano preencheria à mão num fluxo de curadoria tradicional deste tipo de projeto, para que a análise ecológica (que espera essa coluna) funcione tanto sobre a sugestão automática quanto sobre uma versão revisada manualmente, sem precisar de dois caminhos de código diferentes. 

### Nomes de coluna flexíveis e chave da Groq enviada separadamente

 `colunas_alias` no `--config` resolve nomes de coluna diferentes dos esperados sem exigireditar o CSV de entrada, pensado para que o mesmo sistema sirva outro projeto de metabarcodingprocessado por um pipeline de laboratório diferente. A chave de API da Groq nunca é commitada: é resolvida por `--groq-api-key` na chamada, variável de ambiente, ou um `.env` local fora do controle de versão, nessa ordem de prioridade. Essa segunda decisão foi tomada depois de uma chave commitada que foi detectada e revogada automaticamente quando o repositório foi exposto publicamente.

### Premissas adotadas

* **Uma execução corresponde a um projeto.** Se a tabela de entrada contiver mais de um valor de `Researcher`/`Project`, o sistema não separa o processamento por projeto: emite um aviso explícito e processa todas as linhas juntas, com o mesmo `--config`. Assumir múltiplos projetos com parâmetros diferentes misturados no mesmo arquivo, resolvidos automaticamente, ficou fora do escopo por decisão explícita.
* **Internet disponível durante toda a execução.** Além da curadoria assistida por LLM, a consulta de taxonomia (NCBI) e a checagem regional (GBIF)  também dependem de rede. Sem internet, essas etapas falham ou degradam, não é assumido um modo totalmente offline.
* **R e os pacotes necessários já instalados localmente.** O sistema não empacota nem containeriza o ambiente R; depende de uma instalação local (`Rscript` acessível), verificada pelos scripts de instalação (`setup.ps1`/`setup.sh`), não de um ambiente isolado gerado automaticamente.
* **Um único idioma de saída.** Toda a curadoria assistida e os relatórios narrativos sãogerados em português, independente do idioma dos dados de entrada.
* **Tabelas de referência mantidas manualmente.** O contrato de schema (`asv_input_schema.yaml`) e o registro de grupos taxonômicos-alvo são arquivos editáveis, não descobertos ou atualizados automaticamente a partir de um novo dataset.

Limitações conhecidas
---------------------

* **A camada determinística em R tem cobertura de teste automatizado parcial.** Existe uma suíte própria (`r/tests/`), mas cobre hoje só a consulta ao GBIF; as funções que mais decidem o resultado final (pseudo-score, contaminação, faixa de amplicon, seleção de hit) ainda não têm teste automatizado dedicado, só a validação indireta da execução de ponta aponta.
* **Sem cobertura para múltiplos projetos/pesquisadores misturados na mesma entrada**, além do aviso mencionado acima em "Premissas adotadas", o sistema não recusa essa entrada, só avisa e segue com um único conjunto de parâmetros para todas as linhas, o que pode não ser o comportamento desejado se os projetos misturados exigissem, de fato, configurações diferentes.
* **A curadoria assistida por LLM depende de uma cota de uso gratuita compartilhada.** Ratelimits da Groq podem interromper parte das revisões numa execução grande; o sistema já registra cada falha individualmente e não perde o restante do processamento por causa disso, mas a cobertura completa da curadoria assistida numa única execução não é garantida.


