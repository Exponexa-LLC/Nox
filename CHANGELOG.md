# Changelog

Todas as mudanças relevantes deste projeto. O formato segue
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e as versões
seguem [SemVer](https://semver.org/lang/pt-BR/).

## [0.7.0] — primeira distribuição

Primeira versão instalável. Antes disto o Exponexa só rodava a partir do
código; agora existe um executável para Windows x64 que dispensa Python.

### Adicionado

- **Executável Windows x64** (`nox-0.7.0-windows-x64.zip`), empacotado com
  PyInstaller em modo `onedir` — inicia mais rápido e gera menos falso
  positivo de antivírus que o `onefile`.
- **Instalador `install.ps1`**: baixa a release, **confere o SHA-256 antes de
  extrair**, instala em `%LOCALAPPDATA%\Programs\Exponexa` e cria o comando
  `nox`. Aceita `-Version`, `$env:NOX_VERSION`, `-Prefix`, `-Source`,
  `-DryRun`, `-AddToPath`, `-Uninstall` e `-ListVersions`. Não pede
  administrador, não grava credencial e não envia telemetria.
- **`SHA256SUMS`** publicado junto de cada release, para conferência manual.
- **`nox setup`**: diagnóstico local — sistema, comando, CLI do Claude,
  estado da autenticação, config, provedor e perfil. Nunca chama o modelo e
  nunca exibe token, e-mail ou identificador de organização.
- **`nox --version` e `nox --help`**; sem argumento, `nox` abre a interface
  como sempre.
- **Perfis de política** (`conversa`, `desenvolvimento`, `diagnostico-remoto`)
  sobre limites imutáveis, com `/profile` e seletor visual.
- **Diagnóstico remoto somente leitura** por SSH: nove operações de
  allowlist, argv fixo, confirmação por operação e log de metadados
  redigidos.
- **Descoberta de modelos em camadas**, com procedência visível, cache e
  `/refresh-models`.

### Modificado

- **Mascote**: o lobo branco deu lugar a um invasor roxo em pixel art,
  desenhado para o terminal — mesmas medidas de 12×5 e cabeçalho de 6 linhas.
- Nome público **Exponexa** e nome técnico **nox** separados: pacote, módulo e
  comando são `nox`; a interface se apresenta como Exponexa.
- Configuração migrada de `~/.delet_user` para `~/.nox`, por cópia validada.
  **A pasta antiga é preservada** — nada é apagado automaticamente.

### Limites conhecidos

- **Exige a CLI do Claude Code** instalada e autenticada. O bundle elimina a
  dependência de Python, não a do provedor; sem a CLI, a interface abre e
  explica o que falta.
- **Só Windows x64.** Linux e macOS rodam a partir do código; os bundles
  entram quando tiverem build e testes verdes.
- **O executável não é assinado**: o SmartScreen avisa que o editor é
  desconhecido. A verificação oferecida é o SHA-256.
- **Um único provedor funcional.** Gemini, OpenAI e Ollama existem como
  pontos de extensão declarados, sem implementação — o `setup` diz isso em vez
  de listar provedores que não funcionam.
- **O modelo não executa nada.** As chamadas usam `--tools ""`, e não há
  caminho no código para o modelo disparar ação local ou remota.
- A **lista de modelos** vem de um catálogo com data de revisão cruzado com o
  `--help` da CLI instalada: a CLI não expõe enumeração, e o seletor mostra a
  procedência de cada linha em vez de fingir que é dinâmica.
- O caminho HTTPS do instalador (API do GitHub, download real) passa a ser
  exercitável a partir desta release; até aqui só foi testado contra origem
  local.

[0.7.0]: https://github.com/Exponexa-LLC/Nox/releases/tag/v0.7.0
