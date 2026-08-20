# Exponexa

Um harness de terminal para conversar com o Claude pela CLI oficial do Claude
Code — com interface própria, perfis de política e confirmação explícita para
tudo que sai da conversa.

O nome é dividido de propósito:

| | |
|---|---|
| **Exponexa** | o nome público, o que aparece na interface |
| **nox** | o nome técnico: pacote Python, módulo e comando de terminal |

```
Exponexa
backend  claude cli · pronto · perfil conversa
modelo   sonnet   sessão 25dd6014
workspace  ~/projetos/meu-app

⏺ resposta do modelo, em markdown renderizado
> sua mensagem

/help /new /clear /copy /model /provider /status /workspace /exit
```

## O que ele é

Uma TUI terminal-native (Textual) que conversa com o Claude **pela sessão já
autenticada do Claude Code**. Sem SDK, sem `ANTHROPIC_API_KEY`, sem chamadas
HTTP próprias: toda conversa passa por `claude -p --output-format json`.

## O que ele não é

Não é um agente autônomo. **O modelo não executa nada** — nem comandos locais,
nem remotos, nem edições de arquivo. O backend envia `--tools ""` em todas as
chamadas, e não existe caminho no código para o modelo disparar uma ação.

## Requisitos

- Python 3.9 ou mais novo
- [Textual](https://textual.textualize.io/) 8+ (única dependência)
- A [CLI do Claude Code](https://claude.com/claude-code) instalada e autenticada
  (`claude auth status` deve responder `loggedIn: true`)

Sem a CLI, a interface abre e explica o que falta, em vez de quebrar.

## Instalação — Windows x64

```powershell
irm https://raw.githubusercontent.com/Exponexa-LLC/Nox/main/install.ps1 | iex
```

O instalador baixa a release, **confere o SHA-256 antes de extrair qualquer
coisa** e instala em `%LOCALAPPDATA%\Programs\Exponexa`. Não pede
administrador, não grava credencial e não envia telemetria.

Para passar opções, o `iex` não serve — use:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Exponexa-LLC/Nox/main/install.ps1))) -AddToPath
```

| Opção | O que faz |
|---|---|
| `-Version 0.7.0` | fixa a versão; usar uma já instalada volta para ela sem baixar nada |
| `-AddToPath` | acrescenta o comando ao PATH do **usuário** (sem isso, o script só mostra a linha a colar) |
| `-DryRun` | mostra o plano e não escreve nada |
| `-ListVersions` | lista o que está instalado e qual está ativa |
| `-Uninstall` | remove versões, shim e a entrada de PATH — **sua configuração em `~/.nox` é preservada** |

Também dá para usar `$env:NOX_VERSION` em vez de `-Version`.

### Se preferir não confiar num script vindo da rede

Baixe os arquivos da [release](https://github.com/Exponexa-LLC/Nox/releases),
confira o hash você mesmo e extraia onde quiser:

```powershell
$v = "0.7.0"
$zip = "nox-$v-windows-x64.zip"
irm "https://github.com/Exponexa-LLC/Nox/releases/download/v$v/$zip" -OutFile $zip
irm "https://github.com/Exponexa-LLC/Nox/releases/download/v$v/SHA256SUMS" -OutFile SHA256SUMS

(Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()   # compare com a linha do arquivo
Get-Content SHA256SUMS

Expand-Archive $zip -DestinationPath .\nox
.\nox\nox.exe setup
```

O `install.ps1` faz exatamente isso, e você pode lê-lo antes de executar — ele
vive na raiz do repositório, comentado.

**O executável não é assinado.** O SmartScreen vai avisar que o editor é
desconhecido; é esperado, e a verificação de integridade que oferecemos é o
SHA-256 acima.

### A partir do código

```bash
git clone https://github.com/Exponexa-LLC/Nox.git
cd Nox
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS
pip install textual
python -m nox
```

No Windows há também `nox.cmd` na raiz, que descobre o próprio diretório e
funciona de qualquer pasta.

### Linux e macOS

Ainda **não há executável publicado** — use o caminho a partir do código
acima, que funciona nos três sistemas. Os bundles entram quando tiverem build
e testes verdes; nada é anunciado antes disso.

### Primeiro uso

```
nox setup     # diagnóstico local: sistema, CLI do Claude, autenticação, perfil
nox           # abre a interface
```

O `setup` não chama o modelo e não exibe credenciais.

## Perfis de política

O que a aplicação pode fazer é declarado por perfil. O padrão é o mais contido.

| Perfil | Conversa | Modelo/Provedor | Workspace | Sonda de modelos | Diagnóstico remoto |
|---|:---:|:---:|:---:|:---:|:---:|
| **conversa** (padrão) | ✓ | ✓ | ✓ | ✗ | ✗ |
| **desenvolvimento** | ✓ | ✓ | ✓ | ✓ (com confirmação) | ✗ |
| **diagnostico-remoto** | ✓ | ✓ | ✓ | ✗ | ✓ (leitura, confirmação, log) |

Troque com `/profile` — abre um seletor — ou `/profile <nome>`. A troca é
política local: não reinicia a sessão nem muda o modelo.

Você pode declarar perfis próprios em `~/.nox/profiles.json`, escolhendo entre
as capacidades que a aplicação declara. O arquivo é opcional e nunca é criado
automaticamente.

### Limites que nenhum perfil ultrapassa

Estes não são campos configuráveis — não existe campo para violá-los:

- o modelo nunca ganha ferramentas (`--tools ""` é constante);
- não há execução remota disparada pelo modelo;
- não há shell remoto, pipe, redirecionamento ou escrita remota;
- operações remotas só saem de uma ação sua **e** exigem confirmação;
- a sonda de modelos fica desligada por padrão e exige confirmação explícita;
- nenhuma credencial é gravada em configuração, cache ou log.

## Comandos

| | |
|---|---|
| `/help` | ajuda |
| `/new` `/clear` | nova conversa · limpar a tela |
| `/model` `/provider` | seletores visuais; aceitam nome direto |
| `/profile` | perfil de política |
| `/workspace` | pasta usada como `cwd` do backend (com confirmação) |
| `/refresh-models` | reconsulta a lista de modelos, sem chamar o modelo |
| `/status` | provedor, backend, modelo, sessão, workspace, perfil e config |
| `/copy` `/copy tudo` | copia a última resposta · o transcript inteiro |
| `/remote` | diagnóstico remoto somente leitura (só no perfil próprio) |
| `/exit` | sair |

Digitar `/` abre o autocomplete. `Esc` cancela uma confirmação pendente ou
interrompe uma resposta em andamento; `Ctrl+Y` copia a última resposta.

## Modelos

A CLI do Claude Code não expõe um comando para listar os modelos disponíveis
ao seu plano. O Exponexa é honesto sobre isso: mantém um catálogo local com
data de revisão, cruza com os aliases que o `--help` da CLI instalada
documenta e com o plano lido de `claude auth status`, e **mostra a procedência
de cada lista** no seletor. Nada é inventado. A confirmação real (`/refresh-models
--sonda`) faz chamadas de verdade e por isso é opt-in.

## Diagnóstico remoto

O perfil `diagnostico-remoto` habilita `/remote`: nove operações de leitura
(conexão, hostname, sistema, uptime, disco, processos, serviço, containers,
log) executadas por SSH com argv fixo — sem shell, sem escrita, sem entrada
livre. Cada operação mostra o comando exato e pede confirmação. Os servidores
são declarados por você em `~/.nox/hosts.json` (só usuário, host, porta e o
**caminho** da chave — nunca a chave). O log em `~/.nox/remote.log` guarda
apenas metadados redigidos.

## Configuração

`~/.nox/config.json` — preferências apenas (provedor, modelo, timeout,
workspace, perfil). Campos que parecem segredo são descartados na leitura, com
aviso.

## Testes

```bash
python -m nox.test_policy      # e as demais suítes
```

Suítes: `policy`, `resilience`, `remote`, `identity`, `models`, `backend`,
`layout`, `copy`, `autocomplete`, `pickers`. Nenhuma delas chama o Claude, roda
a sonda ou abre conexão de rede — tudo passa por runners injetados.

## Licença

MIT — veja [LICENSE](LICENSE).
