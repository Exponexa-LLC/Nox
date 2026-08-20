"""Exponexa — TUI do harness, no estilo do terminal do Claude Code.

Executar com:  python -m nox   (ou o comando `nox`)

Transcript textual contínuo, sem bolhas nem cartões: o que você escreve aparece
com "> ", a resposta do agente com o marcador "⏺" e markdown renderizado, e as
saídas de comando em bloco indentado. A interface fala apenas com a camada
`backends`; nenhuma ferramenta é habilitada nesta etapa.
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Optional, Tuple

import subprocess

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Input, Markdown, Static

from . import APP_TITLE
from . import __version__
from . import backends
from . import commands
from . import config
from . import frozen
from . import model_discovery
from . import pickers
from . import policy as policy_mod
from . import profiles
from . import remote_hosts
from . import remote_log
from . import remote_ops
from . import remote_ssh
from . import setup_check
from . import wolf

#: Marcadores do transcript (discretos, no estilo do terminal).
USER_MARK = ">"
AGENT_MARK = "⏺"

#: Cores usadas com parcimônia: só os marcadores e os rótulos do cabeçalho.
AMBER = "#e0a458"
MUTED = "#7a8494"
FAINT = "#4d5666"

#: Quadros do indicador de processamento (não é o mascote — o invasor fica parado).
SPINNER_FRAMES = ("✻", "✽", "✳", "✢", "·", "✢", "✳", "✽")

FOOTER_TEXT = "/help /new /clear /copy /model /provider /status /workspace /exit"

#: Pasta usada como workspace inicial (raiz do projeto).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def shorten_path(path: str, limit: int = 46) -> str:
    """Encurta um caminho pela esquerda para caber no cabeçalho."""
    if len(path) <= limit:
        return path
    return "…" + path[-(limit - 1):]


class PromptInput(Input):
    """O campo de entrada, com as teclas do autocomplete e dos seletores.

    A interceptação acontece em `_on_key`, antes do `Input` padrão: as setas
    são consumidas pelo cursor e o Tab pelo foco do Textual, então esperar o
    evento subir até o app chegaria tarde demais. Com os painéis fechados nada
    é interceptado — todas as teclas seguem exatamente como antes.
    """

    async def _on_key(self, event) -> None:
        app = self.app
        if getattr(app, "picker_open", False):
            # menu aberto: só navegação. As demais teclas são engolidas para o
            # texto do campo não se corromper enquanto o menu está na frente.
            if event.key == "down":
                app.move_picker(1)
            elif event.key == "up":
                app.move_picker(-1)
            elif event.key == "enter":
                app.accept_picker()
            elif event.key == "escape":
                app.cancel_picker()
            event.prevent_default()
            event.stop()
            return
        if getattr(app, "suggestions_open", False):
            if event.key == "down":
                app.move_suggestion(1)
            elif event.key == "up":
                app.move_suggestion(-1)
            elif event.key == "tab":
                app.accept_suggestion()
            elif event.key == "escape":
                app.close_suggestions()
            elif event.key == "enter" and not commands.is_exact(self.value):
                # comando ainda incompleto: Enter completa em vez de enviar
                app.accept_suggestion()
            else:
                await super()._on_key(event)
                return
            event.prevent_default()
            event.stop()
            return
        await super()._on_key(event)


class NoxApp(App):
    """A TUI. Toda a conversa passa por `self.backend`."""

    # caminho absoluto: dentro de um bundle PyInstaller o arquivo não fica
    # ao lado do módulo, e o Textual não teria como encontrá-lo
    CSS_PATH = frozen.resource_path("theme.tcss", __file__)
    TITLE = APP_TITLE

    BINDINGS = [
        ("escape", "interrupt", "interromper"),
        ("ctrl+c", "copy_selection", "copiar seleção"),
        ("ctrl+y", "copy_last", "copiar última resposta"),
        ("ctrl+l", "clear_transcript", "limpar"),
        ("ctrl+n", "new_session", "nova conversa"),
        ("pageup", "scroll_transcript(-1)", "rolar acima"),
        ("pagedown", "scroll_transcript(1)", "rolar abaixo"),
    ]

    def __init__(
        self,
        backend: Optional[backends.Backend] = None,
        settings: Optional[config.Config] = None,
    ) -> None:
        super().__init__()
        self.config = settings if settings is not None else config.Config()
        self._boot_warnings: list = []   # avisos do arranque, mostrados no mount

        saved_workspace = self.config.workspace
        self.workspace = (
            saved_workspace if saved_workspace and os.path.isdir(saved_workspace)
            else PROJECT_ROOT
        )

        if backend is not None:
            self.backend = backend
            self.provider = getattr(backend, "name", "custom")
        else:
            self.provider = self.config.provider
            if self.provider not in backends.REGISTRY:
                # provedor desconhecido cairia no eco local em silêncio, e o
                # cabeçalho diria "pronto" enquanto ninguém fala com o Claude
                self._boot_warnings.append(
                    "provedor desconhecido no config: {0} — voltando para "
                    "{1}.".format(self.provider, backends.DEFAULT_BACKEND))
                self.provider = backends.DEFAULT_BACKEND
            self.backend = self._make_backend(self.provider)

        # a preferência salva é guardada à parte: antes da descoberta o backend
        # só conhece a lista de partida, e um modelo novo (fable, por exemplo)
        # seria descartado aqui — a descoberta o traz de volta logo em seguida.
        self._preferred_model = self.config.model_for(self.provider) or wolf.DEFAULT_MODEL
        self.model = self._preferred_model
        self._model_forced = False
        if self.backend.models() and self.model not in self.backend.models():
            self.model = self.backend.models()[0]
            self._model_forced = True  # trocado só porque a lista ainda é a de partida
        self._busy = False
        self._started_at = 0.0
        self._spin = 0
        self._spin_timer: Optional[Timer] = None
        self._pending_workspace: Optional[str] = None
        self._plain: list = []       # espelho em texto puro, para /copy
        self._suggestions: list = []  # sugestões visíveis no painel
        self._suggestion_index = 0
        self._applying_suggestion = False
        self._picker_kind: Optional[str] = None  # "model", "provider" ou nada
        self._picker_rows: list = []
        self._picker_index = 0
        self._picker_title = ""
        self._models_source = ""          # procedência da lista, para o menu
        self._tools_flag_state = "não verificada"  # N-11: só leitura do --help
        self._user_profiles: list = []    # perfis seus, de ~/.nox/profiles.json
        self.policy = profiles.default()  # política ativa; resolvida no mount
        self._pending_probe = False       # sonda aguardando confirmação
        self._discovery_runner = None     # injetável nos testes
        self._discovering = False         # descoberta em andamento
        self._remote_runner = None        # idem, para o SSH
        self._remote_host = None          # host escolhido no seletor
        self._pending_remote = None       # (host, operação, params) aguardando s/n
        self._last_reply = ""

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(wolf.wolf_art(), id="wolf")
            with Vertical(id="ident"):
                yield Static(Text(APP_TITLE, style="bold " + AMBER), id="brand")
                yield Static(id="backend")
                yield Static(id="meta")
                yield Static(id="workspace")
        # can_focus=False: arrastar para selecionar não pode roubar o foco do
        # campo de entrada — a rolagem pelo mouse continua funcionando, e o
        # teclado rola pelo page up/down abaixo.
        yield VerticalScroll(id="transcript", can_focus=False)
        yield Static("", id="statusbar", classes="hidden")
        # painel do autocomplete: fica acima da barra de prompt e some por
        # completo quando fechado, então o layout normal não muda em nada.
        yield Static("", id="suggestions", classes="hidden")
        # seletores de modelo e de provedor: mesmo lugar, mesma discrição.
        yield Static("", id="picker", classes="hidden")
        with Horizontal(id="promptbar"):
            yield Static(Text(USER_MARK, style=AMBER), id="promptmark")
            yield PromptInput(placeholder="fale com o invasor…", id="prompt")
        yield Static(Text(FOOTER_TEXT, style=FAINT), id="footer")

    def _make_backend(self, provider: str) -> backends.Backend:
        backend = backends.get_backend(
            provider, cwd=self.workspace, timeout=self.config.timeout
        )
        backend.configure(self.config)
        return backend

    def on_mount(self) -> None:
        self._refresh_header()
        self.write_system(wolf.BANNER)
        for warning in self._boot_warnings:
            self.write_system(warning)
        self._boot_warnings = []
        for warning in self.config.take_warnings():
            self.write_system(warning)
        self._load_policy()
        if not self.backend.available():
            self.write_system(
                "backend indisponível — {0}.".format(
                    self.backend.unavailable_reason() or "motivo desconhecido"
                )
            )
        self.query_one("#prompt", Input).focus()
        self._start_discovery(announce=False)

    # --------------------------------------------------------------- política
    #
    # Duas camadas: os HARD_CAPS do `policy` (imutáveis, sem campo que os
    # viole) e as capacidades que cada perfil escolhe. A TUI só consulta —
    # nenhuma regra é reescrita aqui, e o backend não conhece política.

    @property
    def profiles_path(self) -> str:
        pasta = os.path.dirname(getattr(self.config, "path", "") or "")
        return os.path.join(pasta, profiles.PROFILES_FILE) if pasta else ""

    def _load_policy(self) -> None:
        """Resolve o perfil salvo; inválido cai em `conversa` com aviso."""
        extras, avisos = profiles.load_user_profiles(self.profiles_path)
        self._user_profiles = extras
        perfil, problemas = profiles.resolve(
            getattr(self.config, "profile", profiles.DEFAULT_PROFILE), extras)
        self.policy = perfil
        for aviso in avisos + problemas:
            self.write_system(aviso)
        self._refresh_header()

    def _requires(self, capacidade: str, comando: str) -> bool:
        """Porta única de capacidade: explica e ensina a trocar de perfil."""
        if self.policy.allows(capacidade):
            return True
        candidatos = [p.name for p in list(profiles.BUILTIN) + list(self._user_profiles)
                      if p.allows(capacidade)]
        dica = (" — use /profile {0}".format(candidatos[0]) if candidatos
                else " — nenhum perfil oferece isso nesta versão")
        self.write_system(
            "{0} não está disponível no perfil {1}{2}.".format(
                comando, self.policy.name, dica))
        return False

    def _profile_command(self, argument: str) -> None:
        """/profile — sem argumento abre o seletor; com argumento troca."""
        pedido = argument.strip()
        linhas = profiles.rows(self._user_profiles)
        if not pedido:
            atual = [linha[0] for linha in linhas]
            indice = atual.index(self.policy.name) if self.policy.name in atual else 0
            self._open_picker("profile", linhas, indice,
                              "selecione o perfil · política local")
            return
        perfil = profiles.find(pedido, self._user_profiles)
        if perfil is None:
            self.write_system("perfil desconhecido: {0} — disponíveis: {1}.".format(
                pedido, ", ".join(linha[0] for linha in linhas)))
            return
        if perfil.name == self.policy.name:
            self.write_system("o perfil já é {0}.".format(perfil.name))
            return
        # trocar de política com pergunta pendente permitiria confirmar sob um
        # perfil e executar sob outro
        if self.has_pending_confirmation:
            self._pending_workspace = None
            self._pending_probe = False
            self._pending_remote = None
            self.write_system("confirmação pendente cancelada pela troca de perfil.")
        self.policy = perfil
        self.config.set_profile(perfil.name)
        self._refresh_header()
        self.write_system("perfil: {0} — {1}.".format(perfil.name, perfil.descricao))

    # --------------------------------------------------------- acesso remoto
    #
    # Somente leitura, e só você dispara: o Claude continua com `--tools ""` e
    # nunca vê nem executa nada daqui. Toda operação passa pela allowlist do
    # `remote_ops`, pelo argv fixo do `remote_ssh` e por uma confirmação s/n.
    # A saída fica no transcript local — não é enviada ao modelo.

    @property
    def hosts_path(self) -> str:
        pasta = os.path.dirname(getattr(self.config, "path", "") or "")
        return os.path.join(pasta, remote_hosts.HOSTS_FILE) if pasta else ""

    @property
    def remote_log_path(self) -> str:
        if not getattr(self.config, "writable", False):
            return ""
        pasta = os.path.dirname(getattr(self.config, "path", "") or "")
        return os.path.join(pasta, remote_log.LOG_FILE) if pasta else ""

    def _load_hosts(self):
        hosts, problemas = remote_hosts.load_hosts(self.hosts_path)
        for problema in problemas:
            self.write_system(problema)
        return hosts

    def _remote_command(self, argument: str) -> None:
        """/remote — sem argumento abre o seletor; com argumento, vai direto."""
        if not self._requires("remote.read", "/remote"):
            return
        hosts = self._load_hosts()
        if not hosts:
            self.write_system(
                "declare os servidores em {0} — este arquivo é seu, eu não "
                "invento endereço. Modelo:".format(self.hosts_path))
            self.write_system(remote_hosts.EXAMPLE)
            return

        partes = argument.split()
        if not partes:
            linhas = [(host.alias, host.alias, host.label(), host.descricao)
                      for host in hosts]
            self._open_picker("remote_host", linhas, 0,
                              "selecione o servidor · somente leitura")
            return

        host = remote_hosts.find(hosts, partes[0])
        if host is None:
            self.write_system("servidor desconhecido: {0} — declarados: {1}.".format(
                partes[0], ", ".join(h.alias for h in hosts)))
            return
        if len(partes) == 1:
            self._remote_host = host
            self._open_picker("remote_op", remote_ops.rows(), 0,
                              "operação em {0} · somente leitura".format(host.alias))
            return
        self._prepare_remote(host, partes[1], partes[2:])

    def _prepare_remote(self, host, operacao: str, extras) -> None:
        """Valida tudo e pede confirmação — nada roda antes do seu 's'."""
        params = {}
        if not self._requires("remote.read", "/remote"):
            return
        if not self.policy.allows_operation(operacao):
            self.write_system(
                "operação {0} não está liberada no perfil {1} — "
                "liberadas: {2}.".format(
                    operacao, self.policy.name,
                    ", ".join(self.policy.remote_operations) or "nenhuma"))
            return
        definicao = remote_ops.find(operacao)
        if definicao is None:
            self.write_system("operação desconhecida: {0} — use /remote {1}.".format(
                operacao, host.alias))
            return
        # quantos tokens esta operação aceita, no máximo
        maximo = (1 if definicao.needs_param() else 0) + (1 if definicao.lines else 0)
        uso = "/remote {0} {1}{2}".format(
            host.alias, operacao,
            " " + definicao.param_label if definicao.param_label else "")
        if len(extras) > maximo:
            # nada de ignorar sobra em silêncio: o que você digitou tem de
            # corresponder ao que vai ser executado
            self.write_system(
                "argumento a mais em {0}: {1} — uso: {2}".format(
                    operacao, " ".join(extras[maximo:]), uso))
            return
        if definicao.needs_param():
            if not extras:
                self.write_system(
                    "essa operação precisa de parâmetro — uso: {0}".format(uso))
                return
            params[definicao.param] = extras[0]
        if definicao.lines and len(extras) > 1:
            params["linhas"] = extras[1]

        try:
            argv = remote_ops.build_argv(operacao, params)
            comando = remote_ssh.build_command(host, argv)
        except (remote_ops.OpError, remote_hosts.HostError, ValueError) as erro:
            self.write_system(str(erro))
            return

        self._pending_remote = (host, operacao, params, argv)
        aviso = ""
        if not self.remote_log_path:
            if self.policy.require_remote_log:
                # perfil que exige trilha não executa sem ela
                pasta = os.path.dirname(
                    getattr(self.config, "path", "") or "") or "~/.nox"
                self.write_system(
                    "o perfil {0} exige trilha, e o log não pode ser gravado "
                    "em {1} — operação recusada.".format(self.policy.name, pasta))
                self._pending_remote = None
                return
            # sem trilha não dá para auditar depois: você precisa saber ANTES
            aviso = ("\naviso: o log de operações não pode ser gravado — "
                     "esta execução não deixará trilha.")
        self.write_system(
            "executar em {0} ({1}):\n  {2}{3}\nconfirmar? responda s ou n.".format(
                host.alias, host.label(), " ".join(remote_ssh.redact(comando)),
                aviso))

    def _answer_remote(self, answer: str) -> None:
        """Confirmação da operação remota: só s ou n, e nada escapa daqui."""
        decisao = self._read_confirmation(answer)
        if decisao is None:
            return
        pendente = self._pending_remote
        self._pending_remote = None
        if not decisao:
            self.write_system("operação cancelada — nada foi executado.")
            return
        host, operacao, params, argv = pendente
        self.write_system("executando {0} em {1}…".format(operacao, host.alias))
        self._run_remote(host, operacao, params, argv)

    @work(thread=True)
    def _run_remote(self, host, operacao, params, argv) -> None:
        # fronteira de erro: o known_hosts pode ter sumido entre a confirmação
        # e a execução, e nada disso pode derrubar a TUI
        try:
            resultado = remote_ssh.run(host, argv, runner=self._remote_runner)
        except Exception as erro:
            self.call_from_thread(
                self.write_system,
                "{0} · {1}: não foi possível executar ({2}).".format(
                    host.alias, operacao, erro))
            return
        try:
            remote_log.append(
                self.remote_log_path,
                remote_log.entry(host.alias, operacao, params, resultado.argv,
                                 resultado.exit_code, resultado.duration,
                                 len(resultado.stdout or "")))
        except Exception:
            pass  # log é auxiliar: nunca derruba a operação
        self.call_from_thread(self._deliver_remote, host, operacao, resultado)

    def _deliver_remote(self, host, operacao, resultado) -> None:
        if not resultado.ok:
            self.write_system("{0} · {1}: {2}".format(
                host.alias, operacao, resultado.error))
            return
        linhas = (resultado.stdout or "").rstrip().splitlines()
        cortadas = linhas[:self.policy.max_output_lines]
        corpo = "\n".join(cortadas) or "(sem saída)"
        if len(linhas) > len(cortadas):
            corpo += "\n… (+{0} linhas)".format(len(linhas) - len(cortadas))
        self.write_system("{0} · {1} ({2:.1f}s)\n{3}".format(
            host.alias, operacao, resultado.duration, corpo))

    # ------------------------------------------------- descoberta de modelos
    #
    # Camadas 2 a 4 do `model_discovery`: `claude --help`, `claude auth status`
    # e o catálogo local. Nenhuma delas fala com o modelo. A camada 1 (sonda
    # real) só entra por `/refresh-models --sonda`, com confirmação.

    @property
    def models_cache_path(self) -> str:
        """Onde o cache de modelos mora — ao lado do config, quando gravável."""
        if not getattr(self.config, "writable", False):
            return ""
        pasta = os.path.dirname(getattr(self.config, "path", "") or "")
        return os.path.join(pasta, "models_cache.json") if pasta else ""

    def _start_discovery(self, announce: bool = True) -> None:
        """Dispara a descoberta fora da thread da interface.

        Só faz sentido para o backend do Claude: o catálogo e as camadas de
        descoberta são dele. Outro provedor mantém a própria lista.
        """
        if getattr(self.backend, "name", "") != "claude":
            if announce:
                self.write_system(
                    "a descoberta de modelos é específica do Claude; "
                    "{0} mantém a própria lista.".format(self.provider))
            return
        # `exclusive=True` não basta: cancelar um worker de thread só o marca,
        # a thread segue até o fim. A guarda evita consultas empilhadas.
        if self._discovering:
            if announce:
                self.write_system("já estou procurando modelos — aguarde.")
            return
        self._discovering = True
        self._discover_models(announce)

    @work(thread=True, exclusive=True, group="descoberta")
    def _discover_models(self, announce: bool) -> None:
        # exclusive: vários /refresh-models seguidos não disparam consultas
        # concorrentes com resultado imprevisível — vale a última pedida
        try:
            found = model_discovery.resolve(
                cache_path=self.models_cache_path, runner=self._discovery_runner)
        except Exception as erro:
            self.call_from_thread(
                self.write_system,
                "falha ao descobrir modelos ({0}) — a lista atual continua "
                "valendo.".format(erro))
            return
        finally:
            self._discovering = False
        self.call_from_thread(self._apply_discovery, found, announce)

    def _apply_discovery(self, found, announce: bool = True) -> None:
        """Instala a lista descoberta, preservando o modelo atual se der."""
        if found is None or not found.rows:
            self.write_system("não consegui montar a lista de modelos.")
            return
        setter = getattr(self.backend, "set_model_catalog", None)
        if not callable(setter):
            return
        setter(found.rows)
        self._models_source = found.source
        estado = getattr(found, "tools_flag_state", None)
        if callable(estado):
            self._tools_flag_state = estado()

        disponiveis = self.backend.models()
        # se o modelo salvo foi descartado no arranque só porque a lista de
        # partida não o conhecia, a descoberta o traz de volta. Uma escolha
        # feita durante a sessão nunca é sobrescrita: `_model_forced` some
        # assim que você aplica um modelo pelo /model.
        preferido = getattr(self, "_preferred_model", "")
        if getattr(self, "_model_forced", False) and preferido in disponiveis:
            self.model = preferido
            self._model_forced = False
        if self.model not in disponiveis:
            anterior = self.model
            self.model = disponiveis[0]
            self.write_system(
                "o modelo {0} não está mais na lista — usando {1}.".format(
                    anterior, self.model))
            self.config.set_model(self.provider, self.model)
        self._refresh_header()

        for aviso in found.warnings:
            self.write_system(aviso)
        if announce:
            self.write_system("modelos atualizados ({0}): {1}.".format(
                found.source, ", ".join(disponiveis)))

    def _refresh_models_command(self, argument: str) -> None:
        """/refresh-models — e, com --sonda, a verificação real opcional."""
        if not self._requires("models.discover", "/refresh-models"):
            return
        pedido = argument.strip().lower()
        if pedido in ("--sonda", "sonda"):
            if not self._requires("models.probe", "/refresh-models --sonda"):
                return
            self._pending_probe = True
            self.write_system(
                "a sonda envia uma mensagem curta ao Claude por modelo "
                "candidato ({0} chamadas reais, com custo). confirmar? "
                "responda s ou n.".format(len(self.backend.models())))
            return
        if pedido:
            self.write_system(
                "uso: /refresh-models [--sonda] — sem argumento, não faz "
                "nenhuma chamada ao modelo.")
            return
        self.write_system("procurando modelos disponíveis…")
        self._start_discovery(announce=True)

    def _answer_probe(self, answer: str) -> None:
        """Resposta à confirmação da sonda: só s ou n, e nada escapa daqui.

        Resposta indecifrável não cancela nem passa adiante — a confirmação
        continua de pé. Cancelar cedo demais deixava a pergunta sem dono, e a
        resposta seguinte virava mensagem para o modelo.
        """
        decisao = self._read_confirmation(answer)
        if decisao is None:
            return
        self._pending_probe = False
        if not decisao:
            self.write_system("sonda cancelada — nada foi enviado.")
            return
        self.write_system("sondando os modelos… isso faz chamadas reais.")
        self._probe_models()

    @work(thread=True)
    def _probe_models(self) -> None:
        try:
            base = model_discovery.resolve(
                cache_path=self.models_cache_path, runner=self._discovery_runner)
            resultado = model_discovery.probe_models(
                base.aliases(), runner=self._discovery_runner, confirmed=True)
        except Exception as erro:
            self.call_from_thread(
                self.write_system, "a sonda falhou: {0}".format(erro))
            return
        confirmado = model_discovery.apply_probe(base, resultado)
        if confirmado.rows:
            model_discovery.save_cache(self.models_cache_path, confirmado)
        self.call_from_thread(self._apply_discovery, confirmado, True)

    # ---------------------------------------------------------------- cabeçalho

    def _refresh_header(self, state: str = "") -> None:
        if not state:
            state = "pronto" if self.backend.available() else "não conectado"
        model = getattr(self.backend, "last_model", "") or self.model

        backend_line = Text("backend  ", style=FAINT)
        backend_line.append(
            "{0} · {1} · perfil {2}".format(
                self.backend.label, state, self.policy.name), style=MUTED)

        meta_line = Text("modelo   ", style=FAINT)
        meta_line.append(model, style=MUTED)
        meta_line.append("   sessão ", style=FAINT)
        meta_line.append(self.backend.session_label(), style=MUTED)

        ws_line = Text("workspace ", style=FAINT)
        ws_line.append(shorten_path(self.workspace), style=MUTED)

        self.query_one("#backend", Static).update(backend_line)
        self.query_one("#meta", Static).update(meta_line)
        self.query_one("#workspace", Static).update(ws_line)

    # ---------------------------------------------------------------- transcript
    #
    # Cada bloco é um widget baseado em `Static`/`Markdown`, e não linhas de um
    # `RichLog`: só assim o Textual consegue selecionar e copiar o texto com o
    # mouse (`Widget.get_selection` só devolve conteúdo de Text/Content).

    @property
    def transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def _append(self, widget) -> None:
        self.transcript.mount(widget)
        self.call_after_refresh(self._scroll_end)

    def _scroll_end(self) -> None:
        self.transcript.scroll_end(animate=False)

    def write_user(self, text: str) -> None:
        line = Text(USER_MARK + " ", style=AMBER)
        line.append(text, style=MUTED)
        self._plain.append("> " + text)
        self._append(Static(line, classes="block user-block"))

    def write_agent(self, body: str) -> None:
        """Resposta do agente: marcador + markdown renderizado e selecionável."""
        self._last_reply = body
        self._plain.append(body)
        block = Horizontal(
            Static(Text(AGENT_MARK, style=AMBER), classes="agent-mark"),
            Markdown(body, classes="agent-md"),
            classes="block agent-block",
        )
        self._append(block)

    def write_system(self, body: str) -> None:
        """Mensagens do próprio harness: bloco indentado e apagado."""
        self._plain.append(body)
        self._append(Static(Text(body, style=MUTED), classes="block sys-block"))

    def write_block(self, rows) -> None:
        """Bloco alinhado de rótulo/valor, usado por /status e /help."""
        width = max([len(label) for label, _value in rows] or [0])
        text = Text()
        plain = []
        for index, (label, value) in enumerate(rows):
            if index:
                text.append("\n")
            text.append(label.ljust(width + 2), style=FAINT)
            text.append(value, style=MUTED)
            plain.append("{0}  {1}".format(label.ljust(width), value))
        self._plain.append("\n".join(plain))
        self._append(Static(text, classes="block sys-block"))

    def clear_transcript(self) -> None:
        self.close_suggestions()
        self.cancel_picker()
        self._plain = []
        self._last_reply = ""
        self.transcript.remove_children()

    # ------------------------------------------------- indicador de processamento

    def _tick_status(self) -> None:
        self._spin += 1
        elapsed = int(time.monotonic() - self._started_at)
        mark = SPINNER_FRAMES[self._spin % len(SPINNER_FRAMES)]
        line = Text(mark + " ", style=AMBER)
        line.append("processando… ", style=MUTED)
        line.append("({0}s · esc para interromper)".format(elapsed), style=FAINT)
        self.query_one("#statusbar", Static).update(line)

    def _start_busy(self) -> None:
        self._busy = True
        self._spin = 0
        self._started_at = time.monotonic()
        self.query_one("#statusbar", Static).remove_class("hidden")
        self._tick_status()
        if self._spin_timer is None:
            self._spin_timer = self.set_interval(0.2, self._tick_status)
        else:
            self._spin_timer.resume()

    def _stop_busy(self) -> None:
        self._busy = False
        if self._spin_timer is not None:
            self._spin_timer.pause()
        self.query_one("#statusbar", Static).add_class("hidden")

    # ------------------------------------------------ seletores visuais
    #
    # Um `Static` não focável no mesmo lugar do painel de autocomplete. As
    # linhas vêm de fora (o backend ativo, o registro de provedores): a UI só
    # desenha. Aplicar passa pelos mesmos `_model_command`/`_provider_command`
    # de sempre, então nada da lógica de sessão e config é reescrito aqui.

    @property
    def picker_open(self) -> bool:
        return self._picker_kind is not None

    def open_model_picker(self) -> None:
        rows = pickers.model_rows(self.backend)
        if not rows:
            self.write_system("este provedor não expõe modelos.")
            return
        current = getattr(self.backend, "last_model", "") or self.model
        index = 0
        for position, row in enumerate(rows):
            if row[0] == current:
                index = position
                break
        titulo = "selecione o modelo"
        if self._models_source:
            # a procedência fica à vista: catálogo, cache ou sonda
            titulo += " · " + self._models_source
        self._open_picker("model", rows, index, titulo)

    def open_provider_picker(self) -> None:
        rows = pickers.provider_rows(
            backends.PROVIDERS,
            self.provider,
            self.backend,
            lambda name: backends.get_backend(name, cwd=self.workspace),
        )
        if not rows:
            self.write_system("nenhum provedor registrado.")
            return
        index = 0
        for position, row in enumerate(rows):
            if row[0] == self.provider:
                index = position
                break
        self._open_picker("provider", rows, index, "selecione o provedor")

    def _open_picker(self, kind, rows, index, title) -> None:
        self.close_suggestions()
        self._picker_kind = kind
        self._picker_rows = rows
        self._picker_index = index
        self._picker_title = title
        self._render_picker()

    def _render_picker(self) -> None:
        panel = self.query_one("#picker", Static)
        panel.update(pickers.render_menu(
            self._picker_title,
            self._picker_rows,
            self._picker_index,
            self.size.width or 80,
        ))
        panel.remove_class("hidden")

    def move_picker(self, delta: int) -> None:
        """Seta para cima/baixo: anda no menu, dando a volta. Nada é aplicado."""
        if not self._picker_rows:
            return
        total = len(self._picker_rows)
        self._picker_index = (self._picker_index + delta) % total
        self._render_picker()

    def accept_picker(self) -> None:
        """Enter: aplica a linha escolhida pelo caminho normal do comando."""
        if not self._picker_rows:
            self.cancel_picker()
            return
        kind = self._picker_kind
        chosen = self._picker_rows[self._picker_index][0]
        self.cancel_picker()
        if kind == "model":
            self._model_command(chosen)
        elif kind == "provider":
            self._provider_command(chosen)
        elif kind == "profile":
            self._profile_command(chosen)
        elif kind == "remote_host":
            self._remote_command(chosen)
        elif kind == "remote_op":
            host = self._remote_host
            if host is None:
                self.write_system("nenhum servidor selecionado.")
                return
            self._prepare_remote(host, chosen, [])

    def cancel_picker(self) -> None:
        """Esc: fecha o menu sem aplicar nem escrever nada."""
        self._picker_kind = None
        self._picker_rows = []
        self._picker_index = 0
        try:
            self.query_one("#picker", Static).add_class("hidden")
        except Exception:
            pass

    # ------------------------------------------------------- autocomplete
    #
    # O painel é um único `Static` não focável logo acima da barra de prompt:
    # o foco nunca sai do campo de entrada, e a digitação continua normal.

    @property
    def suggestions_open(self) -> bool:
        return bool(self._suggestions)

    @on(Input.Changed, "#prompt")
    def _on_prompt_changed(self, event: Input.Changed) -> None:
        if self._applying_suggestion:
            # texto trocado pelo próprio Tab/Enter: o painel já foi fechado
            self._applying_suggestion = False
            return
        self._refresh_suggestions(event.value)

    def _refresh_suggestions(self, text: str) -> None:
        found = commands.suggest(text)
        if not found:
            self.close_suggestions()
            return
        # a seleção volta ao topo sempre que a lista muda de conteúdo
        if [name for name, _ in found] != [name for name, _ in self._suggestions]:
            self._suggestion_index = 0
        self._suggestions = found
        self._render_suggestions()

    def _render_suggestions(self) -> None:
        panel = self.query_one("#suggestions", Static)
        # 2 de margem + 2 de borda + 2 de padding interno
        available = max(24, (self.size.width or 80) - 6)
        room = available - commands.NAME_WIDTH - 4
        text = Text()
        for index, (name, description) in enumerate(self._suggestions):
            if index:
                text.append(chr(10))
            chosen = index == self._suggestion_index
            text.append("▸ " if chosen else "  ", style=AMBER)
            text.append(
                name.ljust(commands.NAME_WIDTH + 1),
                style=("bold #d7dde5" if chosen else "#d7dde5"),
            )
            if room > 1 and len(description) > room:
                description = description[: room - 1] + "…"
            text.append(description[:room] if room > 0 else "", style=FAINT)
        panel.update(text)
        panel.remove_class("hidden")

    def move_suggestion(self, delta: int) -> None:
        """Seta para cima/baixo: anda na lista, dando a volta."""
        if not self._suggestions:
            return
        total = len(self._suggestions)
        self._suggestion_index = (self._suggestion_index + delta) % total
        self._render_suggestions()

    def accept_suggestion(self) -> None:
        """Tab (ou Enter com comando incompleto): completa, sem executar."""
        name = commands.selected_name(self._suggestions, self._suggestion_index)
        if name is None:
            return
        prompt = self.query_one("#prompt", Input)
        completed, cursor = commands.complete(prompt.value, name)
        self.close_suggestions()
        # a flag só é ligada quando o texto muda de fato: sem mudança não há
        # `Input.Changed` para consumi-la, e ela ficaria presa até a tecla seguinte
        if completed != prompt.value:
            self._applying_suggestion = True
            prompt.value = completed
        prompt.cursor_position = cursor

    def close_suggestions(self) -> None:
        """Esc e afins: fecha o painel sem tocar no texto nem no cursor."""
        self._suggestions = []
        self._suggestion_index = 0
        try:
            self.query_one("#suggestions", Static).add_class("hidden")
        except Exception:
            pass

    # ------------------------------------------------------------------ entrada

    @on(Input.Submitted, "#prompt")
    async def _on_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.close_suggestions()
        event.input.value = ""
        if not text:
            return
        if self._pending_workspace is not None:
            self._answer_workspace(text)
            return
        if self._pending_probe:
            self._answer_probe(text)
            return
        if self._pending_remote is not None:
            self._answer_remote(text)
            return
        if text.startswith("/"):
            self._command(text)
            return
        if self._busy:
            self.write_system("ainda estou processando — aguarde ou tecle esc.")
            return
        self.write_user(text)
        self._start_busy()
        self._ask_backend(text)

    @work(thread=True, exclusive=True)
    def _ask_backend(self, text: str) -> None:
        """Chama o backend fora da thread da interface, para a TUI não travar.

        Nenhuma exceção pode escapar daqui: um worker que levanta derruba o app
        inteiro no Textual, e um erro de programação levaria junto o transcript
        e a sessão. Erro inesperado vira resposta de falha, como qualquer outra.
        """
        try:
            reply = self.backend.send(text, self.model)
        except Exception as erro:  # fronteira de erro: nada sobe para o Textual
            reply = backends.Reply(
                ok=False,
                error="erro inesperado no backend ({0}: {1})".format(
                    type(erro).__name__, erro))
        self.call_from_thread(self._deliver, reply)

    def _deliver(self, reply: backends.Reply) -> None:
        self._stop_busy()
        if reply is None:
            self._refresh_header("erro")
            self.write_system("falha: o backend não devolveu resposta.")
            return
        if reply.ok:
            self._refresh_header("pronto")
            self.write_agent(reply.text or "_(resposta vazia)_")
        elif reply.error == "interrompido":
            self._refresh_header("pronto")
            self.write_system("interrompido.")
        else:
            self._refresh_header("erro")
            self.write_system("falha: {0}".format(reply.error))

    # ----------------------------------------------------------------- comandos

    def _command(self, raw: str) -> None:
        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""
        if cmd == "/help":
            self.write_block(wolf.HELP_ROWS)
        elif cmd == "/clear":
            self.action_clear_transcript()
        elif cmd == "/new":
            self.action_new_session()
        elif cmd == "/model":
            self._model_command(argument)
        elif cmd == "/provider":
            self._provider_command(argument)
        elif cmd == "/status":
            self._show_status()
        elif cmd == "/refresh-models":
            self._refresh_models_command(argument)
        elif cmd == "/remote":
            self._remote_command(argument)
        elif cmd == "/profile":
            self._profile_command(argument)
        elif cmd == "/workspace":
            self._workspace_command(argument)
        elif cmd == "/copy":
            self._copy_command(argument)
        elif cmd == "/exit":
            self.exit()
        else:
            self.write_system("comando desconhecido: {0} — use /help.".format(cmd))

    def _show_status(self) -> None:
        self.write_block([
            ("provedor", self.provider),
            ("backend", "{0} · {1}".format(
                self.backend.label,
                "pronto" if self.backend.available() else "não conectado",
            )),
            ("modelo", getattr(self.backend, "last_model", "") or self.model),
            ("sessão", getattr(self.backend, "session_id", "—")),
            ("workspace", self.workspace),
            ("contexto", "em andamento" if getattr(self.backend, "_started", False)
                         else "nova conversa"),
            ("timeout", "{0:.0f}s".format(getattr(self.backend, "timeout", 0.0))),
            ("perfil", "{0} · {1}".format(
                self.policy.name, self.policy.summary())),
            ("ferramentas", "desabilitadas (--tools \"\") · flag {0}".format(
                self._tools_flag_state)),
            ("config", self.config.path + ("" if self.config.writable
                                           else " (somente leitura)")),
            ("estado", "processando" if self._busy else "ocioso"),
        ])

    # ---------------------------------------------------------- provedor/modelo

    def _model_command(self, argument: str) -> None:
        if not self._requires("model.switch", "/model"):
            return
        if self._busy:
            self.write_system("aguarde a resposta antes de trocar o modelo.")
            return
        available = self.backend.models()
        wanted = argument.strip()
        if not wanted:
            # sem argumento: o seletor visual, em vez de ciclar às cegas
            self.open_model_picker()
            return
        if wanted in available:
            self.model = wanted
        else:
            self.write_system(
                "modelo desconhecido em {0}: {1} — disponíveis: {2}.".format(
                    self.provider, wanted, ", ".join(available) or "nenhum"
                )
            )
            return
        # escolha explícita: vira a preferência, e nenhuma descoberta futura
        # pode desfazê-la
        self._preferred_model = self.model
        self._model_forced = False
        self.config.set_model(self.provider, self.model)
        self._refresh_header()
        self.write_system(
            "modelo: {0} — vale a partir da próxima mensagem.".format(self.model)
        )

    # A montagem das linhas de provedor vive em `pickers.provider_rows`, que o
    # seletor usa — a TUI não mantém uma segunda cópia da mesma lógica.

    def _provider_command(self, argument: str) -> None:
        if not self._requires("provider.switch", "/provider"):
            return
        wanted = argument.strip().lower()
        if not wanted:
            # sem argumento: o seletor visual, em vez da lista estática
            self.open_provider_picker()
            return
        if self._busy:
            self.write_system("aguarde a resposta antes de trocar de provedor.")
            return
        if wanted not in backends.REGISTRY:
            self.write_system(
                "provedor desconhecido: {0} — disponíveis: {1}.".format(
                    wanted, ", ".join(backends.PROVIDERS)
                )
            )
            return
        if wanted == self.provider:
            self.write_system("o provedor já é {0}.".format(wanted))
            return

        candidate = self._make_backend(wanted)
        if not candidate.available():
            self.write_system(
                "{0} não está configurado — {1}. Nada mudou.".format(
                    wanted, candidate.unavailable_reason()
                )
            )
            return

        self.provider = wanted
        self.backend = candidate
        self.model = self.config.model_for(wanted) or (
            candidate.models()[0] if candidate.models() else ""
        )
        if candidate.models() and self.model not in candidate.models():
            self.model = candidate.models()[0]
        self.config.set_provider(wanted)
        self.config.set_model(wanted, self.model)
        self._refresh_header()
        self.write_system(
            "provedor: {0} ({1}), modelo {2}. Conversa nova — o contexto não "
            "passa de um provedor para outro.".format(
                wanted, candidate.label, self.model
            )
        )
        for warning in self.config.take_warnings():
            self.write_system(warning)

    # ------------------------------------------------------------ confirmações
    #
    # Toda pergunta de s/n do app passa por aqui: workspace, sonda e operação
    # remota respondem igual. Resposta indecifrável NÃO cancela — a pergunta
    # continua de pé, e nada escapa para o backend enquanto isso.

    SIM = ("s", "sim", "y", "yes")
    NAO = ("n", "nao", "não", "no")

    def _read_confirmation(self, answer: str):
        """True = sim, False = não, None = não entendi (pergunta continua)."""
        resposta = (answer or "").strip().lower()
        if resposta in self.SIM:
            return True
        if resposta in self.NAO:
            return False
        self.write_system("responda s para confirmar ou n para cancelar.")
        return None

    @property
    def has_pending_confirmation(self) -> bool:
        return (self._pending_workspace is not None
                or self._pending_probe
                or self._pending_remote is not None)

    def cancel_pending_confirmation(self) -> bool:
        """Esc: desiste da pergunta ativa, sem executar nada."""
        if not self.has_pending_confirmation:
            return False
        self._pending_workspace = None
        self._pending_probe = False
        self._pending_remote = None
        self.write_system("cancelado — nada foi executado.")
        return True

    # --------------------------------------------------------------- workspace

    def _workspace_command(self, argument: str) -> None:
        if not self._requires("workspace.switch", "/workspace"):
            return
        if not argument:
            self.write_block([("workspace", self.workspace)])
            return
        if self._busy:
            self.write_system("aguarde a resposta antes de trocar de workspace.")
            return

        candidate = os.path.abspath(
            os.path.expanduser(os.path.expandvars(argument.strip('"').strip("'")))
        )
        if not os.path.exists(candidate):
            self.write_system("caminho inexistente: {0}".format(candidate))
            return
        if not os.path.isdir(candidate):
            self.write_system("não é uma pasta: {0}".format(candidate))
            return
        if candidate == self.workspace:
            self.write_system("o workspace já é esse.")
            return

        self._pending_workspace = candidate
        self.write_system(
            "trocar workspace para {0}? responda s ou n.".format(candidate)
        )

    def _answer_workspace(self, answer: str) -> None:
        decisao = self._read_confirmation(answer)
        if decisao is None:
            return  # resposta indecifrável: a pergunta continua de pé
        candidate = self._pending_workspace
        self._pending_workspace = None
        if not decisao:
            self.write_system("troca cancelada.")
            return
        self.workspace = candidate or self.workspace
        # nenhum arquivo da pasta é lido ou alterado: ela só vira o cwd da CLI
        setattr(self.backend, "cwd", self.workspace)
        self.config.set_workspace(self.workspace)
        self._refresh_header()
        self.write_system("workspace: {0}".format(self.workspace))

    # ------------------------------------------------------------- copiar texto

    def _to_clipboard(self, text: str, what: str) -> None:
        """Copia via terminal (OSC 52) e, no Windows, também via `clip`."""
        if not text:
            self.write_system("nada para copiar.")
            return
        self.copy_to_clipboard(text)
        local = False
        if os.name == "nt":
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                clip = subprocess.Popen(
                    ["clip"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
                clip.communicate(text.encode("utf-16-le"), timeout=5)
                local = clip.returncode == 0
            except (OSError, subprocess.SubprocessError):
                local = False
        self.write_system(
            "{0} copiado ({1} caracteres){2}.".format(
                what, len(text), "" if local else " — via terminal"
            )
        )

    def _copy_command(self, argument: str) -> None:
        if argument.strip().lower() in ("tudo", "all"):
            self._to_clipboard("\n\n".join(self._plain), "transcript")
        else:
            self._to_clipboard(self._last_reply, "última resposta")

    def action_copy_selection(self) -> None:
        """Ctrl+C: copia a seleção do mouse, se houver."""
        selection = self.screen.get_selected_text()
        if selection:
            self._to_clipboard(selection, "seleção")
        else:
            self.write_system(
                "nada selecionado — arraste o mouse sobre o texto, "
                "ou use /copy para a última resposta."
            )

    def action_copy_last(self) -> None:
        self._to_clipboard(self._last_reply, "última resposta")

    def action_scroll_transcript(self, direction: int) -> None:
        """Rola o transcript sem tirar o foco do campo de entrada."""
        if direction < 0:
            self.transcript.scroll_page_up(animate=False)
        else:
            self.transcript.scroll_page_down(animate=False)

    # ------------------------------------------------------------------- ações

    def action_interrupt(self) -> None:
        # Esc primeiro desfaz uma pergunta pendente: ficar preso numa
        # confirmação sem saída era o comportamento anterior
        if self.cancel_pending_confirmation():
            return
        if not self._busy:
            return
        if not self.backend.cancel():
            self.write_system("nada para interromper.")

    def on_unmount(self) -> None:
        """Ao sair, não deixa a CLI rodando em segundo plano."""
        try:
            self.backend.cancel()
        except Exception:
            pass

    def action_clear_transcript(self) -> None:
        self.clear_transcript()
        self.write_system("tela limpa — o contexto da conversa continua.")

    def action_new_session(self) -> None:
        if self._busy:
            self.write_system("aguarde a resposta antes de reiniciar.")
            return
        self.backend.reset()
        self._refresh_header()
        self.clear_transcript()
        self.write_system(
            "nova conversa ({0}). contexto anterior descartado.".format(
                self.backend.session_label()
            )
        )


#: Uso mostrado por `nox --help`.
USAGE = """Exponexa — harness de terminal para o Claude Code.

  nox               abre a interface (padrão)
  nox setup         diagnóstico local: sistema, CLI do Claude, autenticação
  nox --version     mostra a versão
  nox --help        mostra esta ajuda

O diagnóstico não chama o modelo e não exibe credenciais."""


def parse_command(argv) -> Tuple[str, List[str]]:
    """Traduz argv em (comando, resto). Sem argumento, é a interface.

    Função pura, para o roteamento ser testável sem abrir a TUI.
    """
    argumentos = list(argv or [])
    if not argumentos:
        return "tui", []
    primeiro = argumentos[0]
    if primeiro in ("-V", "--version", "version"):
        return "version", argumentos[1:]
    if primeiro in ("-h", "--help", "help"):
        return "help", argumentos[1:]
    if primeiro == "setup":
        return "setup", argumentos[1:]
    return "desconhecido", argumentos


def main(argv=None) -> int:
    """Ponto de entrada do comando `nox` e de `python -m nox`.

    O alinhamento de console acontece antes de qualquer `print` e e desfeito
    no `finally`: sem ele, texto acentuado sai numa pagina de codigo e e lido
    em outra ("nao" chega como "nAo"). Nenhum codigo de saida muda aqui - o
    valor devolvido continua sendo o do comando.
    """
    restaurar_console = frozen.configure_console()
    try:
        return _executar(sys.argv[1:] if argv is None else argv)
    finally:
        restaurar_console()


def _executar(argumentos) -> int:
    """O roteamento em si, separado para o console ser sempre restaurado."""
    comando, resto = parse_command(argumentos)
    if comando == "version":
        print("Exponexa (nox) {0}".format(__version__))
        return 0
    if comando == "help":
        print(USAGE)
        return 0
    if comando == "setup":
        checks = setup_check.run_checks()
        print(setup_check.render(checks))
        return setup_check.exit_code(checks)
    if comando == "desconhecido":
        print("comando desconhecido: {0}".format(" ".join(resto)))
        print()
        print(USAGE)
        return 2
    NoxApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
