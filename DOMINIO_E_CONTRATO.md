## O problema

Uma empresa de consultoria ambiental atende clientes com diferentes demandas de monitoramento por metabarcoding: identificar quais organismos existem numa área a partir do DNA presente em amostras coletadas ali (água, solo, raízes, dependendo do grupo de interesse do cliente). A cientista de dados da consultoria é quem recebe o resultado bruto do sequenciamento de cada projeto e precisa transformá-lo numa base de dados confiável sobre quais organismos foram detectados.

Dois projetos reais ilustram esse trabalho neste repositório: um monitoramento de peixes por eDNA de água na Serra do Cipó (MG), para atender exigência de órgão ambiental sobre rios e córregos da região; e um inventário florestal por metabarcoding de raízes, identificando espécies de plantas presentes no solo, numa área de preservação da Fazenda Tanguro (MT).

O método, de forma geral, é o mesmo nos dois casos: em vez de identificar os organismos morfologicamente, a equipe de campo coleta um substrato ambiental e extrai o DNA presente nele, deixado ou pertencente a qualquer organismo do grupo-alvo que esteve ali. Essas amostras são enviadas para uma facility de biologia molecular, que extrai o DNA, amplifica uma região genética específica (o marcador varia por grupo taxonômico: uma região do DNA mitocondrial para peixes, uma região do DNA ribossômico para plantas, por exemplo) e sequencia o material amplificado.

A facility entrega, para cada projeto, uma única tabela, com uma linha para cada trecho de DNA identificado em cada amostra coletada. Cada um desses trechos é chamado de ASV (sigla em inglês para variante de sequência de amplicon): uma sequência específica de DNA, curta, que apareceu um número mínimo de vezes na leitura da amostra para não ser considerada erro do sequenciador. Para cada sequência, a tabela já traz as três espécies mais parecidas encontradas num banco de referência genético, com metadados de qualidade associados a essa identificação (percentual de identidade e de cobertura do alinhamento com a referência).

Esse dado bruto vem com problemas conhecidos do método: contaminação de laboratório (DNA humano, de outro organismo comum no ambiente de processamento, de outra amostra processada no mesmo lote), amplificação de DNA de organismos fora do grupo de interesse, e casos em que a espécie mais parecida no banco de referência não tem qualidade suficiente para confiar naquele resultado.

Cabe à cientista de dados decidir, para cada sequência de DNA da tabela:

1. Qual identificação taxonômica ela sustenta, e até que nível (espécie, gênero, família...), sem afirmar mais do que a evidência permite.
2. Se ela representa a detecção real de um organismo ou uma provável contaminação, comparando com os controles negativos processados junto com as amostras.
3. Se o tamanho da sequência é compatível com o marcador genético usado no sequenciamento.
4. Quando essas três decisões não dão uma resposta clara sozinhas, uma segunda camada, com apoio de um modelo de linguagem, avalia a plausibilidade biológica e geográfica da identificação usando evidência adicional: sequências parecidas dentro do próprio lote, registros de ocorrência pública da espécie na região (GBIF), e espécimes já coletados fisicamente na mesma área em campanhas anteriores, quando disponível (segundo input opcional, formato descrito em "Domínio e família de dados").

O resultado é uma base de dados curada: a identificação de cada sequência, por ponto de coleta, com o nível de confiança de cada identificação e a evidência que a sustenta. É essa base que embasa o relatório técnico entregue ao cliente de cada projeto.

## O usuário do resultado

A cientista de dados da consultoria responsável por transformar a tabela bruta entregue pela facility de sequenciamento numa base de dados confiável, antes de essa base entrar no relatório técnico de cada projeto. Hoje esse trabalho é feito manualmente, sequência por sequência, decidindo o que é ruído, contaminação ou identificação válida, seja o projeto sobre peixes, plantas, ou outro grupo. O sistema organiza a evidência disponível (resultado do BLAST, sequências parecidas dentro do próprio lote, registros de ocorrência da espécie na região) num formato que essa pessoa revisa mais rápido e com menos chance de erro, no mesmo formato independente do projeto.

## Domínio e família de dados

Identificação taxonômica a partir de tabelas de metabarcoding: saída de um pipeline de bioinformática (DADA2 seguido de BLAST) sobre sequências de DNA obtidas de amostras ambientais, independente do substrato ou do grupo taxonômico. O sistema é parametrizado (`--config`), não fixo a um projeto: outro marcador genético, outro grupo taxonômico ou outra área de estudo é configuração, não código novo.

Dois exemplos reais de domínios diferentes demonstram essa generalidade:

- [`data/example/exemplo_1/`](data/example/exemplo_1/): subconjunto do projeto `eDNA_Cipo`, já publicado (Valentine, 2025). Peixes, eDNA de água, marcador MiFish2 (gene 12S mitocondrial), 7 pontos amostrais na Serra do Cipó, bacia do rio São Francisco (MG). O subconjunto de demonstração tem 20 amostras reais e 1 controle (extração); o projeto original completo (815 linhas) tem 21 amostras e 2 controles (extração e filtragem).
- [`data/example/exemplo_2/`](data/example/exemplo_2/): subconjunto do projeto `roots_metabar`. Plantas, metabarcoding de raízes, marcador ITS2 (região ribossômica), numa área de preservação da Fazenda Tanguro (MT), para inventário florestal. Sem dados de latitude/longitude nos metadados; a checagem regional por GBIF é pulada com aviso nesse caso.

**Formato de entrada esperado:** tabela em formato *long* (uma linha por combinação de sequência e amostra), saída típica de um pipeline DADA2 seguido de BLAST.

- CSV delimitado por `;`, decimal `,` (inclusive em notação científica, ex. `1,92e-80`), UTF-8, aspas padrão CSV.
- Contrato completo de colunas (obrigatórias, opcionais, e as que nunca podem estar presentes) em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml).
- Chave primária composta: `ASV (Sequence)` mais `Unique_File_name` (a mesma sequência se repete em várias amostras).
- Cada amostra referencia seus controles diretamente por colunas de controle (`Ext. Control`, `PCR Control`, `Filt. Control`, ou equivalentes mapeados via `colunas_alias`), com o nome do `Unique_File_name` do controle correspondente.

Nomes de coluna diferentes dos esperados (outro pipeline de origem, outra convenção de laboratório) são resolvidos por `colunas_alias` no `--config`, sem precisar editar o CSV. Grupos taxonômicos além dos já registrados (peixes, plantas, bentos, zooplâncton, fitoplâncton, perifíton) podem ser adicionados ao registro interno conforme necessário.

**Segundo input, opcional:** tabela de espécies obtidas por métodos tradicionais de monitoramento (não metabarcoding) nos mesmos pontos amostrais, usada só como evidência adicional na camada assistida por LLM (decisão 4 acima); nunca entra na curadoria determinística. CSV `;`-delimitado, UTF-8, uma linha por combinação espécie×ponto, pelo menos 2 colunas (ponto amostral na primeira; taxon na última, ou numa coluna chamada `Taxon_binomial` se houver colunas extras no meio). Exemplos em [`data/example/exemplo_1/spp_tradicional.csv`](data/example/exemplo_1/spp_tradicional.csv) e [`data/example/exemplo_2/roots_metabar_spp_tradicional.csv`](data/example/exemplo_2/roots_metabar_spp_tradicional.csv). Repassado via `--reference` (ver README). Sem essa tabela, a curadoria assistida roda normalmente, só sem essa evidência extra.
