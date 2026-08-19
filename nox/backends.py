"""Camada de backends do Exponexa (pacote `nox`).

A TUI nunca fala com um provedor diretamente: ela conversa com um `Backend`.
Isso mantém a interface intacta e deixa espaço para plugar outros provedores
(Gemini, OpenAI, Ollama…) depois, bastando registrar uma nova classe em
`REGISTRY`.

O backend padrão é o `ClaudeCLIBackend`, que reaproveita a **sessão já
autenticada do Claude Code** chamando o comando oficial `claude -p`.
Não existe SDK `anthropic` aqui, nenhuma chamada HTTP própria e a variável
`ANTHROPIC_API_KEY` é explicitamente removida do ambiente do processo filho.

Nesta etapa nenhuma ferramenta é habilitada: as chamadas usam `--tools ""`,
então o modelo não executa comandos nem toca em arquivos.
"""

from __future__ import annotations

import json
import locale
import os
import shutil
import subprocess
import sys
import uuid
from typing import Dict, List, Optional, Tuple, Type

from . import frozen
from . import wolf

#: Tempo máximo, em segundos, de espera por uma resposta.
DEFAULT_TIMEOUT: float = 120.0

#: Variáveis de ambiente removidas antes de chamar a CLI, para garantir que a
#: autenticação usada é a sessão do Claude Code e não uma chave de API.
BLOCKED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")

#: Todas as variáveis de credencial conhecidas, de todos os provedores.
#: Cada backend só enxerga as que declarar em `ENV_ALLOW`; as demais são
#: removidas do ambiente do processo filho, para que a credencial de um
#: provedor nunca chegue a outro.
CREDENTIAL_ENV = BLOCKED_ENV + (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OLLAMA_HOST",
)

#: Sentinela: procurar o executável no PATH.
AUTO = object()


class Reply(object):
    """Resultado normalizado de uma chamada a qualquer backend."""

    __slots__ = ("text", "ok", "error", "model", "session_id", "duration_ms")

    def __init__(
        self,
        text: str = "",
        ok: bool = True,
        error: str = "",
        model: str = "",
        session_id: str = "",
        duration_ms: int = 0,
    ) -> None:
        self.text = text
        self.ok = ok
        self.error = error
        self.model = model
        self.session_id = session_id
        self.duration_ms = duration_ms

    def __repr__(self) -> str:  # pragma: no cover - only for debugging
        return "Reply(ok={0!r}, model={1!r}, error={2!r})".format(
            self.ok, self.model, self.error
        )


class Backend(object):
    """Interface comum a todos os provedores."""

    #: Identificador curto usado em `REGISTRY` e no comando /provider.
    name = "base"

    #: Rótulo exibido no cabeçalho da TUI.
    label = "backend"

    #: Variáveis de credencial que ESTE backend pode enxergar. Todas as outras
    #: da lista `CREDENTIAL_ENV` são removidas do ambiente do processo filho.
    ENV_ALLOW: tuple = ()

    #: Modelos oferecidos por este provedor (rótulos, na ordem do /model).
    MODELS: tuple = ()

    #: Metadados de exibição dos modelos: nome -> (alias, descrição curta).
    #: Serve ao seletor visual do /model — a TUI não guarda lista própria, ela
    #: pergunta ao backend ativo. Ausente aqui, o modelo aparece só com o nome.
    MODEL_INFO: dict = {}

    #: Lista descoberta em tempo de execução, quando houver: cada item é
    #: (valor do --model, nome exibido, id técnico, descrição, origem). Vazia,
    #: valem `MODELS`/`MODEL_INFO`, que são só o ponto de partida.
    _discovered: list = []

    def set_model_catalog(self, rows) -> None:
        """Instala a lista descoberta (ver `model_discovery`)."""
        self._discovered = [tuple(row) for row in rows or []]

    def models(self) -> List[str]:
        """Modelos disponíveis neste provedor."""
        if self._discovered:
            return [row[0] for row in self._discovered]
        return list(self.MODELS)

    def model_choices(self) -> List[Tuple[str, str, str, str, str]]:
        """Modelos como (valor, exibido, id técnico, descrição, origem)."""
        if self._discovered:
            return [tuple(row) for row in self._discovered]
        rows = []
        for name in self.models():
            alias, description = self.MODEL_INFO.get(name, ("", ""))
            rows.append((name, name, alias, description, ""))
        return rows

    def model_rows(self) -> List[Tuple[str, str, str]]:
        """Modelos como (nome, alias, descrição), na ordem de exibição."""
        return [(row[0], row[2], row[3]) for row in self.model_choices()]

    def configure(self, config: object) -> None:
        """Aplica preferências vindas do `config.Config`."""

    def env(self) -> Dict[str, str]:
        """Ambiente do processo filho, sem credenciais alheias nem rastros do bundle.

        Congelado pelo PyInstaller, o runtime reescreve variáveis de biblioteca
        que o filho herdaria — e o `claude` poderia carregar a biblioteca
        errada. `frozen.clean_env` desfaz isso antes de tirarmos as credenciais.
        """
        env = frozen.clean_env()
        for key in CREDENTIAL_ENV:
            if key not in self.ENV_ALLOW:
                env.pop(key, None)
        return env

    def available(self) -> bool:
        """Diz se o backend pode ser usado agora."""
        raise NotImplementedError

    def unavailable_reason(self) -> str:
        """Explica por que o backend não está disponível."""
        return ""

    def send(self, text: str, model: str) -> Reply:
        """Envia `text` e devolve a resposta. Bloqueante — chame numa thread."""
        raise NotImplementedError

    def reset(self) -> None:
        """Descarta o contexto e começa uma conversa nova."""

    def cancel(self) -> bool:
        """Interrompe uma chamada em andamento. Devolve True se havia algo."""
        return False

    def session_label(self) -> str:
        """Identificação curta da sessão, para o cabeçalho."""
        return "—"

    def describe(self) -> str:
        """Descrição multilinha usada pelo comando /status."""
        return self.label


class ClaudeCLIBackend(Backend):
    """Fala com o Claude pela CLI oficial, usando a sessão do Claude Code."""

    name = "claude"
    label = "claude cli"

    #: Nenhuma: a autenticação vem da sessão do Claude Code, e as variáveis
    #: ANTHROPIC_* são removidas justamente para garantir isso.
    ENV_ALLOW = ()

    MODELS = ("sonnet", "opus", "haiku")

    #: Alias e descrição de cada modelo, como a própria CLI os trata: são
    #: apelidos estáveis, e a CLI resolve a versão exata na hora da chamada.
    MODEL_INFO = {
        "sonnet": ("claude-sonnet", "equilíbrio entre rapidez e profundidade"),
        "opus": ("claude-opus", "o mais capaz, para tarefas difíceis"),
        "haiku": ("claude-haiku", "o mais rápido e barato, para respostas curtas"),
    }

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        cwd: Optional[str] = None,
        executable: object = AUTO,
    ) -> None:
        self.timeout = timeout
        self.cwd = cwd or os.getcwd()
        # `AUTO` procura no PATH; qualquer outro valor (inclusive None) é usado
        # como está, o que permite simular a ausência da CLI nos testes.
        if executable is AUTO:
            self.executable = shutil.which("claude")
        else:
            self.executable = executable  # type: ignore[assignment]
        self.session_id = str(uuid.uuid4())
        self.last_model = ""
        self._started = False
        self._process = None  # type: Optional[subprocess.Popen]
        self._cancelled = False

    # ------------------------------------------------------------ disponibilidade

    def available(self) -> bool:
        return bool(self.executable)

    def unavailable_reason(self) -> str:
        if self.executable:
            return ""
        return "comando `claude` não encontrado no PATH"

    # ------------------------------------------------------------------- sessão

    def reset(self) -> None:
        self.session_id = str(uuid.uuid4())
        self._started = False

    def session_label(self) -> str:
        return self.session_id.split("-")[0]

    def cancel(self) -> bool:
        """Mata o processo da CLI em andamento, se houver (tecla Esc na TUI)."""
        process = self._process
        if process is None or process.poll() is not None:
            return False
        self._cancelled = True
        return _kill_tree(process)

    def describe(self) -> str:
        return (
            "backend: {0} ({1})\n"
            "executável: {2}\n"
            "sessão da CLI: {3}\n"
            "contexto: {4}\n"
            "timeout: {5:.0f}s\n"
            "ferramentas: desabilitadas (--tools \"\")\n"
            "autenticação: sessão do Claude Code (sem ANTHROPIC_API_KEY)"
        ).format(
            self.label,
            "pronto" if self.available() else self.unavailable_reason(),
            self.executable or "—",
            self.session_id,
            "conversa em andamento" if self._started else "nova conversa",
            self.timeout,
        )

    # -------------------------------------------------------------------- envio

    def _command(self, text: str, model: str) -> List[str]:
        args = [self.executable or "claude", "-p", text, "--output-format", "json"]
        if self._started:
            # mantém a conversa e todo o contexto acumulado na mesma sessão
            args += ["--resume", self.session_id]
        else:
            args += ["--session-id", self.session_id]
        if model:
            args += ["--model", model]
        # nenhuma ferramenta nesta etapa: sem execução de comandos nem edições
        args += ["--tools", ""]
        return args

    def tools_smoke_command(self, model: str = "") -> List[str]:
        """Comando de verificação REAL da flag `--tools`, para uso manual.

        Devolve o argv e não executa nada: rodar isto gasta uma chamada de
        verdade ao Claude, então a decisão é sua. A verificação automática do
        harness é apenas local (lê o `--help`, sem falar com o modelo).
        """
        return [self.executable or "claude", "-p", "responda ok",
                "--output-format", "json", "--model", model or "haiku",
                "--tools", ""]

    def _env(self) -> Dict[str, str]:
        # `Backend.env()` já remove tudo que não está em ENV_ALLOW — para o
        # Claude, isso inclui as ANTHROPIC_*, forçando a sessão do Claude Code.
        return self.env()

    def configure(self, config: object) -> None:
        timeout = getattr(config, "timeout", None)
        if timeout:
            self.timeout = float(timeout)

    def send(self, text: str, model: str) -> Reply:
        if not self.available():
            return Reply(ok=False, error=self.unavailable_reason())

        kwargs = {}
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if creation:  # esconde a janela do console no Windows
            kwargs["creationflags"] = creation
        self._cancelled = False
        try:
            process = subprocess.Popen(
                self._command(text, model),
                cwd=self.cwd,
                env=self._env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **kwargs
            )
        except OSError as exc:
            return Reply(ok=False, error="falha ao executar a CLI: {0}".format(exc))

        self._process = process
        try:
            raw_out, raw_err = process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            process.communicate()
            return Reply(
                ok=False,
                error="tempo esgotado: a CLI não respondeu em {0:.0f}s".format(
                    self.timeout
                ),
            )
        finally:
            self._process = None

        if self._cancelled:
            return Reply(ok=False, error="interrompido")

        stdout = _decode(raw_out)
        stderr = _decode(raw_err)

        if process.returncode != 0:
            return Reply(
                ok=False,
                error="a CLI terminou com código {0}{1}".format(
                    process.returncode, _tail(stderr)
                ),
            )
        if not stdout.strip():
            return Reply(ok=False, error="a CLI não devolveu nada" + _tail(stderr))

        try:
            data = json.loads(stdout)
        except ValueError:
            return Reply(
                ok=False,
                error="resposta da CLI não é JSON válido" + _tail(stdout),
            )

        if data.get("is_error"):
            return Reply(
                ok=False,
                error="a CLI relatou erro: {0}".format(
                    data.get("result") or data.get("subtype") or "motivo desconhecido"
                ),
            )

        self.session_id = data.get("session_id") or self.session_id
        self._started = True
        self.last_model = _model_name(data) or model
        return Reply(
            text=str(data.get("result", "")).strip(),
            ok=True,
            model=self.last_model,
            session_id=self.session_id,
            duration_ms=int(data.get("duration_ms") or 0),
        )


# --------------------------------------------------------------------------
# Pontos de extensão — ainda NÃO implementados.
#
# Existem para que /provider liste os provedores e explique exatamente o que
# falta em cada um, em vez de quebrar. Nenhum deles envia nada a lugar nenhum
# nesta etapa: `available()` é sempre False, com um motivo honesto.
# --------------------------------------------------------------------------


class PlannedBackend(Backend):
    """Base dos provedores planejados: anuncia o que falta, sem fingir que roda."""

    #: Executável esperado no PATH, se o provedor for baseado em CLI.
    EXECUTABLE = ""

    #: Variável de ambiente que carrega a credencial, se houver.
    CREDENTIAL = ""

    #: Como o usuário obtém/liga o provedor.
    SETUP_HINT = ""

    def __init__(self, cwd: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT,
                 **_ignored: object) -> None:
        self.cwd = cwd or os.getcwd()
        self.timeout = timeout
        self.session_id = ""
        self.last_model = ""

    def available(self) -> bool:
        return False

    def missing(self) -> List[str]:
        """O que impede este provedor de funcionar agora."""
        gaps = []
        if self.EXECUTABLE and not shutil.which(self.EXECUTABLE):
            gaps.append("`{0}` não está no PATH".format(self.EXECUTABLE))
        if self.CREDENTIAL and not os.environ.get(self.CREDENTIAL):
            gaps.append("a variável {0} não está definida".format(self.CREDENTIAL))
        gaps.append("a integração ainda não foi implementada")
        return gaps

    def unavailable_reason(self) -> str:
        return "; ".join(self.missing())

    def send(self, text: str, model: str) -> Reply:
        return Reply(ok=False, error=self.unavailable_reason())

    def session_label(self) -> str:
        return "—"

    def describe(self) -> str:
        lines = [
            "backend: {0} (não configurado)".format(self.label),
            "pendências: {0}".format(self.unavailable_reason()),
        ]
        if self.SETUP_HINT:
            lines.append("como ligar: {0}".format(self.SETUP_HINT))
        if self.CREDENTIAL:
            lines.append(
                "credencial: lida de {0} no ambiente — nunca gravada no "
                "config".format(self.CREDENTIAL)
            )
        return "\n".join(lines)


class GeminiCLIBackend(PlannedBackend):
    """Google Gemini pela CLI oficial."""

    name = "gemini"
    label = "gemini cli"
    ENV_ALLOW = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS")
    MODELS = ("gemini-2.5-pro", "gemini-2.5-flash")
    EXECUTABLE = "gemini"
    CREDENTIAL = "GEMINI_API_KEY"
    SETUP_HINT = "instale a CLI `gemini` e exporte GEMINI_API_KEY"


class OpenAIBackend(PlannedBackend):
    """OpenAI pela API HTTP."""

    name = "openai"
    label = "openai api"
    ENV_ALLOW = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID")
    MODELS = ("gpt-4o", "gpt-4o-mini", "o3-mini")
    CREDENTIAL = "OPENAI_API_KEY"
    SETUP_HINT = "exporte OPENAI_API_KEY no ambiente"


class OllamaBackend(PlannedBackend):
    """Modelos locais via Ollama."""

    name = "ollama"
    label = "ollama local"
    ENV_ALLOW = ("OLLAMA_HOST",)
    MODELS = ("llama3.1", "qwen2.5", "mistral")
    EXECUTABLE = "ollama"
    SETUP_HINT = "instale o Ollama e rode `ollama serve`"


class EchoBackend(Backend):
    """Respostas simuladas locais — usado quando a CLI não está disponível."""

    name = "echo"
    label = "eco local"

    MODELS = ("local-echo",)

    def __init__(self, cwd: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT,
                 **_ignored: object) -> None:
        self.session_id = wolf.new_session_id()
        self.cwd = cwd or os.getcwd()
        self.timeout = timeout
        self.last_model = ""

    def available(self) -> bool:
        return True

    def send(self, text: str, model: str) -> Reply:
        return Reply(text=wolf.fake_reply(text, model), model=model, ok=True)

    def reset(self) -> None:
        self.session_id = wolf.new_session_id()

    def session_label(self) -> str:
        return self.session_id

    def describe(self) -> str:
        return (
            "backend: {0} (simulado, sem rede)\n"
            "sessão: {1}"
        ).format(self.label, self.session_id)


# --------------------------------------------------------------------------
# Registro de provedores — ponto de extensão para Gemini, OpenAI, Ollama…
# --------------------------------------------------------------------------

REGISTRY: Dict[str, Type[Backend]] = {
    ClaudeCLIBackend.name: ClaudeCLIBackend,
    GeminiCLIBackend.name: GeminiCLIBackend,
    OpenAIBackend.name: OpenAIBackend,
    OllamaBackend.name: OllamaBackend,
    EchoBackend.name: EchoBackend,
}

#: Ordem de exibição no comando /provider.
PROVIDERS = (
    ClaudeCLIBackend.name,
    GeminiCLIBackend.name,
    OpenAIBackend.name,
    OllamaBackend.name,
    EchoBackend.name,
)

DEFAULT_BACKEND = ClaudeCLIBackend.name


def get_backend(name: str = DEFAULT_BACKEND, **kwargs: object) -> Backend:
    """Instancia um backend do registro; cai no eco local se o nome não existir."""
    factory = REGISTRY.get(name, EchoBackend)
    return factory(**kwargs)  # type: ignore[arg-type]


def models_for(name: str) -> List[str]:
    """Modelos de um provedor, sem precisar instanciá-lo."""
    factory = REGISTRY.get(name)
    return list(getattr(factory, "MODELS", ()) or ())


# --------------------------------------------------------------------------
# Utilitários internos
# --------------------------------------------------------------------------


def _kill_tree(process: "subprocess.Popen") -> bool:
    """Encerra o processo e seus filhos.

    A CLI cria subprocessos que herdam os canos de saída; matar só o pai deixa
    `communicate()` bloqueado esperando esses filhos. No Windows isso exige
    `taskkill /T`; nos demais sistemas basta matar o grupo de processos.
    """
    try:
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.call(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        else:
            process.kill()
    except OSError:
        return False
    return True


def _decode(raw: object) -> str:
    """Bytes da CLI viram texto sem perder acento.

    A CLI emite UTF-8, mas um console Windows legado pode devolver a saída na
    página de código local (cp1252 por aqui). Tentamos UTF-8 estrito, caímos
    para o encoding do sistema e só então recorremos ao `replace`, que nunca
    levanta. Isto traduz bytes e nada mais: código de saída e stderr continuam
    sendo tratados por quem chamou.
    """
    if not isinstance(raw, bytes):
        return str(raw or "")
    for encoding in _decode_candidates():
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _decode_candidates() -> List[str]:
    """UTF-8 primeiro; depois o encoding do console/locale, sem repetir."""
    candidatos = ["utf-8"]
    for nome in (locale.getpreferredencoding(False), sys.getfilesystemencoding()):
        if nome and nome.lower().replace("-", "") not in (
                c.lower().replace("-", "") for c in candidatos):
            candidatos.append(nome)
    return candidatos


def _tail(text: str, limit: int = 200) -> str:
    """Resume a saída de erro para caber numa mensagem do transcript."""
    clean = " ".join(text.split())
    if not clean:
        return ""
    if len(clean) > limit:
        clean = clean[: limit - 1] + "…"
    return ": " + clean


#: Campos de `modelUsage` que contam como "uso" ao decidir o modelo dominante.
_USAGE_FIELDS = ("inputTokens", "outputTokens", "cacheReadInputTokens",
                 "cacheCreationInputTokens", "totalTokens", "tokens")


def _usage_weight(value: object) -> float:
    """Quanto um modelo foi usado, a partir do que a CLI reportar.

    Aceita tanto um número solto quanto um objeto com contagens de tokens;
    formatos desconhecidos pesam zero, sem quebrar.
    """
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        total = 0.0
        for campo in _USAGE_FIELDS:
            numero = value.get(campo)
            if isinstance(numero, (int, float)) and not isinstance(numero, bool):
                total += float(numero)
        return total
    return 0.0


def _model_name(data: dict) -> str:
    """Modelo realmente usado: o de maior uso quando a CLI reporta vários.

    Empate (inclusive quando ninguém tem uso mensurável) resolve por ordem
    alfabética, para a mesma resposta dar sempre o mesmo nome. Sem `modelUsage`
    utilizável, devolve "" e quem chamou usa o modelo pedido.
    """
    usage = data.get("modelUsage")
    if not isinstance(usage, dict) or not usage:
        return ""
    nomes = [nome for nome in usage if isinstance(nome, str) and nome]
    if not nomes:
        return ""
    # -peso para ordenar do maior uso para o menor; nome como desempate estável
    return sorted(nomes, key=lambda nome: (-_usage_weight(usage[nome]), nome))[0]
