# Como baixar o projeto do GitHub

Estas instruções mostram como copiar o repositório para o seu computador antes de instalar e executar o sistema. Para instalar e rodar, veja o [README](README.md) depois de baixar.

> Se você já baixou o projeto como arquivo `.zip` e o extraiu, pule para a seção **Alternativa: baixar como ZIP** (passo 8 em diante) só se precisar relembrar onde continuar.

## Opção recomendada: clonar com Git

Clonar significa criar uma cópia local do repositório que também mantém o histórico de versões. Essa é a opção recomendada porque facilita receber atualizações posteriormente.

### 1. Instale o Git

Caso ainda não tenha o Git instalado:

- **Windows:** baixe e instale o [Git for Windows](https://git-scm.com/download/win). Durante a instalação, as opções padrão são suficientes.
- **macOS:** abra o Terminal e execute `git --version`. Se o Git não estiver instalado, o macOS oferecerá as ferramentas de linha de comando necessárias.
- **Linux:** instale o pacote `git` pelo gerenciador de pacotes da sua distribuição.

Para confirmar a instalação, abra um terminal e execute:

```bash
git --version
```

O comando deve mostrar uma versão do Git, por exemplo `git version 2.x.x`.

### 2. Abra um terminal

Escolha uma pasta do computador onde deseja guardar o projeto, por exemplo `Documentos` ou `Projetos`.

- **Windows:** abra o Explorador de Arquivos, entre na pasta escolhida, clique com o botão direito em uma área vazia e selecione **Abrir no Terminal**. Também é possível abrir o PowerShell pelo menu Iniciar.
- **macOS:** abra o aplicativo **Terminal**.
- **Linux:** abra o aplicativo **Terminal** da sua distribuição.

Se abriu o terminal em outra pasta, navegue até o local desejado com `cd`. Por exemplo:

```powershell
# Windows PowerShell
cd $HOME\Documents
```

```bash
# macOS ou Linux
cd ~/Documents
```

### 3. Clone o repositório

No terminal, execute:

```bash
git clone https://github.com/gbrl-mendes/desafio_Amplo.git
```

Esse comando cria uma pasta chamada `desafio_Amplo` e baixa nela todos os arquivos do projeto.

### 4. Entre na pasta criada

Ainda no terminal, execute:

```bash
cd desafio_Amplo
```

Para confirmar que está na pasta certa, liste os arquivos:

```powershell
# Windows PowerShell
Get-ChildItem
```

```bash
# Git Bash, macOS ou Linux
ls
```

Você deverá encontrar, entre outros, os arquivos `README.md`, `setup.ps1`, `setup.sh`, `requirements.txt` e as pastas `harness`, `r` e `data`.

### 5. Continue para a instalação

A partir desta pasta, siga as instruções de instalação do [README.md](README.md).

## Alternativa: baixar como ZIP

Use esta alternativa se não quiser instalar Git. Ela baixa uma cópia dos arquivos, mas não permite atualizar o projeto com o comando `git pull`.

1. Abra a página do repositório: [github.com/gbrl-mendes/desafio_Amplo](https://github.com/gbrl-mendes/desafio_Amplo).
2. Clique no botão verde **Code**.
3. Selecione **Download ZIP**.
4. Aguarde o download terminar.
5. Localize o arquivo baixado, normalmente em `Downloads`.
6. Clique com o botão direito no arquivo `.zip` e selecione **Extrair tudo** ou uma opção equivalente.
7. Escolha uma pasta de destino e conclua a extração.
8. Abra um terminal dentro da pasta extraída. Ela deve conter `README.md`, `setup.ps1` e `setup.sh`.
9. Siga as instruções de instalação do [README.md](README.md).

> Não tente executar os scripts diretamente dentro do arquivo `.zip`. Extraia todo o conteúdo antes.

## Atualizar uma cópia clonada

Se o projeto foi obtido com `git clone`, você pode baixar atualizações futuras sem repetir a clonagem.

1. Abra um terminal na pasta `desafio_Amplo`.
2. Execute:

```bash
git pull
```

3. Rode novamente o script de instalação. Ele é idempotente: reaproveita o ambiente virtual existente e atualiza as dependências quando necessário.

```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

```bash
# macOS, Linux, Git Bash ou WSL
bash setup.sh
```

## Problemas comuns

### `git` não é reconhecido

O Git não está instalado ou o terminal aberto ainda não reconheceu a instalação. Instale o Git, feche o terminal, abra um novo e execute novamente `git --version`.

### `Repository not found` ou erro de permissão

Confirme que o endereço digitado é exatamente:

```text
https://github.com/gbrl-mendes/desafio_Amplo.git
```

Se o repositório tiver se tornado privado, será necessário ter uma conta GitHub autorizada a acessá-lo.

### A pasta `desafio_Amplo` já existe

Isso geralmente significa que o projeto já foi clonado ou extraído antes. Entre nela com:

```bash
cd desafio_Amplo
```

Se ela for uma cópia clonada com Git, atualize-a com:

```bash
git pull
```

Evite clonar o projeto repetidamente dentro de outra pasta com o mesmo nome.

### Não sei em qual pasta estou

Use um dos comandos abaixo para ver a pasta atual:

```powershell
# Windows PowerShell
Get-Location
```

```bash
# Git Bash, macOS ou Linux
pwd
```

A instalação e a execução devem ser feitas dentro da pasta do projeto, aquela que contém `README.md`, `setup.ps1` e `setup.sh`.
