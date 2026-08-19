# -*- coding: utf-8 -*-
"""Testes da descoberta de modelos, do cache e do /refresh-models.

Rodar com:

    python -m nox.test_models      (com o ambiente do projeto ativo)

NENHUM teste aqui chama o Claude. Todas as consultas à CLI passam por um
`runner` falso injetado, inclusive as da sonda: a sonda real só roda pela TUI,
com `--sonda` e confirmação do usuário. O cache é escrito em pasta temporária.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

from textual.widgets import Static

from . import backends
from . import commands
from . import model_discovery
from . import models_catalog
from .test_autocomplete import StubConfig
from .__main__ import NoxApp

SIZE = (80, 24)

#: Trecho fiel do `claude --help` real (CLI 2.1.235), usado como amostra.
HELP_REAL = """Usage: claude [options] [command] [prompt]

Options:
  --fallback-model <model>              Enable automatic fallback
  --model <model>                       Model for the current session. Provide
                                        an alias for the latest model (e.g.
                                        'fable', 'opus', or 'sonnet') or a
                                        model's full name (e.g.
                                        'claude-fable-5').
  -n, --name <name>                     Set a display name for this session
"""

AUTH_REAL = """{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "subscriptionType": "max"
}"""


def fake_runner(help_text=HELP_REAL, auth_text=AUTH_REAL, version="9.9.9 (Claude Code)",
                falhar=()):
    """Runner de CLI falso: devolve saídas fixas, sem executar nada."""
    chamadas = []

    def run(args, timeout=20.0):
        chamadas.append(list(args))
        if "--version" in args:
            return (1, "") if "version" in falhar else (0, version)
        if "--help" in args:
            return (1, "boom") if "help" in falhar else (0, help_text)
        if "auth" in args:
            return (1, "boom") if "auth" in falhar else (0, auth_text)
        if "-p" in args:
            raise AssertionError("nenhum teste pode chamar o modelo: " + str(args))
        return 1, ""

    run.chamadas = chamadas
    return run


class ClaudeLikeStub(backends.Backend):
    """Backend com o nome do Claude, para a TUI disparar a descoberta."""

    name = "claude"
    label = "claude cli (stub)"
    MODELS = ("sonnet", "opus", "haiku")
    MODEL_INFO = {"sonnet": ("claude-sonnet", "de partida")}

    def __init__(self) -> None:
        self.sent = []
        self.session_id = "sess-teste"
        self.last_model = ""
        self.timeout = 1.0
        self.cwd = ""

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def configure(self, settings) -> None:
        pass

    def session_label(self) -> str:
        return self.session_id

    def send(self, text, model=""):
        raise AssertionError("nenhum teste pode chamar o modelo")


def make_app(runner=None, profile="desenvolvimento"):
    """App de teste.

    O perfil padrão aqui é `desenvolvimento` porque estas suítes exercitam a
    sonda (`models.probe`), capacidade que só existe nele. O bloqueio da sonda
    em `conversa` é verificado por teste próprio, mais abaixo.
    """
    settings = StubConfig()
    settings.profile = profile
    app = NoxApp(backend=ClaudeLikeStub(), settings=settings)
    app._discovery_runner = runner if runner is not None else fake_runner()
    return app


async def send(pilot, text):
    pilot.app.query_one("#prompt").value = text
    await pilot.press("enter")
    await pilot.pause()


async def esperar_descoberta(pilot, tentativas=60):
    """Aguarda o worker de descoberta terminar."""
    for _ in range(tentativas):
        if pilot.app._models_source:
            return True
        await pilot.pause()
    return False


# ------------------------------------------------------- camada 2: --help


async def test_parse_dos_aliases_do_help():
    curtos, completos = model_discovery.parse_cli_aliases(HELP_REAL)
    assert curtos == ["fable", "opus", "sonnet"], curtos
    assert completos == ["claude-fable-5"], completos


async def test_parse_ignora_outras_opcoes():
    """Só o bloco do --model conta — nada de varrer o --help inteiro."""
    curtos, _completos = model_discovery.parse_cli_aliases(HELP_REAL)
    assert "gemini" not in curtos and "fallback" not in curtos, curtos


async def test_alias_novo_da_cli_aparece_marcado():
    """Alias citado pela CLI que o catálogo não conhece = modelo novo."""
    help_novo = HELP_REAL.replace("'sonnet'", "'sonnet', 'quartzo'")
    encontrado = model_discovery.discover(runner=fake_runner(help_text=help_novo))
    linhas = dict((row[0], row) for row in encontrado.rows)
    assert "quartzo" in linhas, list(linhas)
    assert linhas["quartzo"][4] == model_discovery.ORIGEM_CLI, linhas["quartzo"]
    # e nada foi inventado sobre ele
    assert linhas["quartzo"][2] == "", linhas["quartzo"]


async def test_help_ilegivel_vira_aviso():
    encontrado = model_discovery.discover(runner=fake_runner(falhar=("help",)))
    assert encontrado.rows, "sem o --help ainda resta o catálogo"
    assert any("--help" in aviso for aviso in encontrado.warnings), encontrado.warnings


# --------------------------------------------------- camada 3: auth status


async def test_plano_lido_do_auth_status():
    encontrado = model_discovery.discover(runner=fake_runner())
    assert encontrado.plan == "max", encontrado.plan
    assert "plano max" in encontrado.source, encontrado.source


async def test_sem_login_avisa():
    auth = json.dumps({"loggedIn": False, "subscriptionType": ""})
    encontrado = model_discovery.discover(runner=fake_runner(auth_text=auth))
    assert any("autenticada" in aviso for aviso in encontrado.warnings), \
        encontrado.warnings


async def test_auth_ilegivel_vira_aviso():
    encontrado = model_discovery.discover(runner=fake_runner(falhar=("auth",)))
    assert any("autenticação" in aviso for aviso in encontrado.warnings), \
        encontrado.warnings


# ------------------------------------------------- camada 4: catálogo local


async def test_nomes_amigaveis_separados():
    """Nome exibido, alias técnico e descrição são campos distintos."""
    encontrado = model_discovery.discover(runner=fake_runner())
    porNome = dict((row[1], row) for row in encontrado.rows)
    for exibido in ("Fable 5", "Opus 5", "Sonnet 5", "Haiku 4.5"):
        assert exibido in porNome, list(porNome)
    alias, exibido, identificador, descricao, origem = porNome["Opus 5"]
    assert alias == "opus", alias
    assert identificador == "claude-opus-5", identificador
    assert descricao and exibido != alias and exibido != identificador
    assert origem == model_discovery.ORIGEM_CATALOGO


async def test_catalogo_tem_data_de_revisao():
    assert models_catalog.CATALOG_REVIEWED, "o catálogo precisa de data"
    encontrado = model_discovery.discover(runner=fake_runner())
    assert models_catalog.CATALOG_REVIEWED in encontrado.source, encontrado.source


async def test_modelos_obsoletos_nao_entram():
    for velho in ("claude-opus-4-6", "claude-sonnet-4-6",
                  "claude-opus-4-6-20250101"):
        assert models_catalog.is_superseded(velho), velho
    # o snapshot datado de um modelo ATUAL não é obsoleto
    assert not models_catalog.is_superseded("claude-haiku-4-5-20251001")
    encontrado = model_discovery.discover(runner=fake_runner())
    ids = [row[2] for row in encontrado.rows]
    assert "claude-opus-4-6" not in ids, ids


# ----------------------------------------------------------------- cache


async def test_cache_grava_e_le():
    pasta = tempfile.mkdtemp(prefix="nox-cache-")
    try:
        caminho = os.path.join(pasta, "models_cache.json")
        encontrado = model_discovery.resolve(cache_path=caminho,
                                             runner=fake_runner())
        assert os.path.exists(caminho), "o cache deveria ter sido gravado"
        lido = model_discovery.load_cache(caminho)
        assert lido is not None
        assert lido.aliases() == encontrado.aliases(), lido.aliases()
        assert "cache" in lido.source, lido.source
        # nada de credencial no arquivo
        bruto = open(caminho, encoding="utf-8").read().lower()
        for proibido in ("api_key", "token", "senha", "authorization"):
            assert proibido not in bruto, proibido
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


async def test_falha_na_consulta_usa_cache():
    pasta = tempfile.mkdtemp(prefix="nox-cache-")
    try:
        caminho = os.path.join(pasta, "models_cache.json")
        model_discovery.resolve(cache_path=caminho, runner=fake_runner())
        # agora tudo falha: tem de cair no cache, avisando
        quebrado = model_discovery.resolve(
            cache_path=caminho,
            runner=fake_runner(falhar=("help", "auth", "version")))
        assert quebrado.rows, "o cache deveria salvar a lista"
        assert any("cache" in aviso for aviso in quebrado.warnings), \
            quebrado.warnings
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


async def test_sem_cache_usa_catalogo_com_aviso():
    pasta = tempfile.mkdtemp(prefix="nox-cache-")
    try:
        caminho = os.path.join(pasta, "models_cache.json")
        resultado = model_discovery.resolve(
            cache_path=caminho,
            runner=fake_runner(falhar=("help", "auth", "version")))
        assert resultado.rows, "o catálogo é a última linha de defesa"
        assert resultado.warnings, "cair no catálogo tem de avisar"
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


async def test_cache_antigo_perde_modelo_obsoleto():
    pasta = tempfile.mkdtemp(prefix="nox-cache-")
    try:
        caminho = os.path.join(pasta, "models_cache.json")
        velho = {
            "rows": [
                ["opus-4-6", "Opus 4.6", "claude-opus-4-6", "antigo", "catálogo"],
                ["opus", "Opus 5", "claude-opus-5", "atual", "catálogo"],
            ],
            "source": "catálogo antigo",
            "checked_at": 1.0,
        }
        with open(caminho, "w", encoding="utf-8") as handle:
            json.dump(velho, handle)
        lido = model_discovery.load_cache(caminho)
        assert lido is not None
        assert lido.aliases() == ["opus"], lido.aliases()
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


# ------------------------------------------------------------- sonda


async def test_sonda_desligada_por_padrao():
    """Sem confirmação explícita, a sonda se recusa a gastar qualquer coisa."""
    chamou = []

    def runner(args, timeout=20.0):
        chamou.append(args)
        return 0, ""

    try:
        model_discovery.probe_models(["opus"], runner=runner)
    except model_discovery.ProbeNotConfirmed:
        pass
    else:
        raise AssertionError("a sonda rodou sem confirmação")
    assert not chamou, "nada pode ser executado sem confirmação"


async def test_sonda_confirmada_filtra_a_lista():
    """Com confirmação (aqui, contra um runner falso), só sobra o confirmado."""
    def runner(args, timeout=20.0):
        assert "-p" in args, args
        modelo = args[args.index("--model") + 1]
        return (0, "ok") if modelo in ("opus", "sonnet") else (1, "erro")

    base = model_discovery.discover(runner=fake_runner())
    resultado = model_discovery.probe_models(
        base.aliases(), runner=runner, confirmed=True)
    confirmado = model_discovery.apply_probe(base, resultado)
    assert confirmado.aliases() == ["opus", "sonnet"], confirmado.aliases()
    assert all(row[4] == model_discovery.ORIGEM_SONDA for row in confirmado.rows)
    assert "sonda" in confirmado.source


# --------------------------------------------------------------- na TUI


async def test_descoberta_no_inicio():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot), "a descoberta não terminou"
        assert app.backend.models() == ["fable", "opus", "sonnet", "haiku"], \
            app.backend.models()
        assert models_catalog.CATALOG_REVIEWED in app._models_source


async def test_seletor_usa_a_lista_atualizada():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await send(pilot, "/model")
        texto = app.query_one("#picker", Static).content.plain
        for exibido in ("Fable 5", "Opus 5", "Sonnet 5", "Haiku 4.5"):
            assert exibido in texto, texto
        assert "claude-opus-5" in texto, texto
        # a procedência fica à vista no título
        assert models_catalog.CATALOG_REVIEWED in texto, texto
        await pilot.press("escape")


async def test_seletor_aplica_o_alias_nao_o_nome_exibido():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await send(pilot, "/model")
        alvo = app.backend.models().index("haiku")
        while app._picker_index != alvo:
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert app.model == "haiku", app.model
        assert "próxima mensagem" in app._plain[-1], app._plain[-1]


async def test_modelo_atual_preservado():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        app.model = "opus"
        await send(pilot, "/refresh-models")
        for _ in range(60):
            if "modelos atualizados" in app._plain[-1]:
                break
            await pilot.pause()
        assert app.model == "opus", "o modelo escolhido não pode se perder"


async def test_preferencia_salva_volta_apos_descoberta():
    """Modelo salvo que a lista de partida não conhece precisa ser recuperado."""
    app = make_app(fake_runner())
    # como no arranque real: o config pedia "fable", a lista de partida não o
    # conhecia e ele foi trocado pelo primeiro da lista
    app._preferred_model = "fable"
    app.model = "sonnet"
    app._model_forced = True
    async with app.run_test(size=SIZE) as pilot:
        assert await esperar_descoberta(pilot)
        assert app.model == "fable", app.model


async def test_modelo_sumido_cai_no_primeiro():
    """Modelo fora da lista nova: troca com aviso, sem deixar valor inválido."""
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        app.model = "modelo-que-saiu-de-linha"
        await send(pilot, "/refresh-models")
        for _ in range(60):
            if app.model != "modelo-que-saiu-de-linha":
                break
            await pilot.pause()
        assert app.model in app.backend.models(), app.model
        assert any("não está mais na lista" in linha for linha in app._plain[-3:]), \
            app._plain[-3:]


async def test_refresh_models_nao_chama_o_modelo():
    runner = fake_runner()
    async with make_app(runner).run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await send(pilot, "/refresh-models")
        for _ in range(60):
            if "modelos atualizados" in app._plain[-1]:
                break
            await pilot.pause()
        assert "modelos atualizados" in app._plain[-1], app._plain[-1]
        for chamada in runner.chamadas:
            assert "-p" not in chamada, chamada


async def test_sonda_pede_confirmacao_e_pode_ser_recusada():
    runner = fake_runner()
    async with make_app(runner).run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await send(pilot, "/refresh-models --sonda")
        assert "chamadas reais" in app._plain[-1], app._plain[-1]
        assert app._pending_probe is True
        await send(pilot, "n")
        assert "cancelada" in app._plain[-1], app._plain[-1]
        assert app._pending_probe is False
        for chamada in runner.chamadas:
            assert "-p" not in chamada, "nada pode ser enviado ao recusar"


def sem_backend(app):
    """Faz o teste falhar se qualquer coisa for enviada ao backend."""
    enviados = []

    def send(text, model=""):
        enviados.append(text)
        raise AssertionError("nada pode ir ao backend: " + repr(text))

    app.backend.send = send
    return enviados


def sonda_runner():
    """Runner falso que também atende a sonda, sem tocar no Claude real."""
    base = fake_runner()
    chamadas = base.chamadas

    def run(args, timeout=20.0):
        if "-p" in args:
            chamadas.append(list(args))
            return 0, "ok"
        return base(args, timeout)

    run.chamadas = chamadas
    return run


async def abrir_confirmacao(pilot):
    await send(pilot, "/refresh-models --sonda")
    assert pilot.app._pending_probe is True
    assert "chamadas reais" in pilot.app._plain[-1], pilot.app._plain[-1]


async def test_sonda_n_cancela_e_nao_envia():
    runner = sonda_runner()
    async with make_app(runner).run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        enviados = sem_backend(app)
        await abrir_confirmacao(pilot)
        await send(pilot, "n")
        assert app._pending_probe is False
        assert "cancelada" in app._plain[-1], app._plain[-1]
        assert app.query_one("#prompt").value == "", "o input deve ficar limpo"
        assert not enviados, enviados
        assert not [linha for linha in app._plain if linha.startswith("> ")]
        for chamada in runner.chamadas:
            assert "-p" not in chamada, chamada


async def test_sonda_s_inicia_apos_confirmacao():
    runner = sonda_runner()
    async with make_app(runner).run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await abrir_confirmacao(pilot)
        # antes do "s", nenhuma sondagem pode ter acontecido
        assert not [c for c in runner.chamadas if "-p" in c], runner.chamadas
        await send(pilot, "s")
        for _ in range(80):
            if [c for c in runner.chamadas if "-p" in c]:
                break
            await pilot.pause()
        sondados = [c[c.index("--model") + 1]
                    for c in runner.chamadas if "-p" in c]
        assert sondados == ["fable", "opus", "sonnet", "haiku"], sondados
        assert app._pending_probe is False
        for _ in range(80):
            if "sonda" in app._models_source:
                break
            await pilot.pause()
        assert "sonda" in app._models_source, app._models_source


async def test_sonda_resposta_invalida_continua_aguardando():
    runner = sonda_runner()
    async with make_app(runner).run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        enviados = sem_backend(app)
        await abrir_confirmacao(pilot)
        await send(pilot, "talvez")
        assert app._pending_probe is True, "a confirmação continua de pé"
        assert "responda s" in app._plain[-1], app._plain[-1]
        assert not enviados, enviados
        assert not [c for c in runner.chamadas if "-p" in c], runner.chamadas
        await send(pilot, "n")
        assert app._pending_probe is False
        assert "cancelada" in app._plain[-1], app._plain[-1]


async def test_sonda_invalida_depois_n_nao_vaza_para_o_backend():
    """Regressão da sequência que vazava: resposta inválida e depois n."""
    runner = sonda_runner()
    async with make_app(runner).run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        enviados = sem_backend(app)
        await abrir_confirmacao(pilot)
        for resposta in ("nao sei", "talvez", "n"):
            await send(pilot, resposta)
        assert app._pending_probe is False
        assert "cancelada" in app._plain[-1], app._plain[-1]
        assert not enviados, enviados
        assert not [linha for linha in app._plain if linha.startswith("> ")]
        for chamada in runner.chamadas:
            assert "-p" not in chamada, chamada


async def test_refresh_models_no_autocomplete():
    nomes = [nome for nome, _descricao in commands.suggest("/ref")]
    assert nomes == ["/refresh-models"], nomes


async def test_status_continua_completo():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await send(pilot, "/status")
        texto = app._plain[-1]
        for rotulo in ("provedor", "backend", "modelo", "sessão",
                       "workspace", "config"):
            assert rotulo in texto, (rotulo, texto)


# ------------------------------------------------- flag --tools (N-11)


async def test_flags_da_cli_sao_lidas_do_help():
    """Verificação local: sem chamar o modelo, só lendo o --help."""
    encontrado = model_discovery.discover(runner=fake_runner())
    assert encontrado.flags is not None
    assert "--model" in encontrado.flags, encontrado.flags
    assert encontrado.tools_flag_state() in ("documentada", "ausente")


async def test_tools_documentada_no_help_real():
    """O --help real desta CLI documenta --tools: sem aviso falso."""
    help_com_tools = HELP_REAL + "  --tools <tools...>   Specify the list of tools\n"
    encontrado = model_discovery.discover(
        runner=fake_runner(help_text=help_com_tools))
    assert "--tools" in encontrado.flags, encontrado.flags
    assert encontrado.tools_flag_state() == "documentada"
    assert not any("--tools" in aviso for aviso in encontrado.warnings), \
        encontrado.warnings


async def test_tools_ausente_gera_aviso_sem_mudar_comportamento():
    """Se a flag sumir do --help, avisa — e o backend continua enviando."""
    encontrado = model_discovery.discover(runner=fake_runner())
    assert encontrado.tools_flag_state() == "ausente", encontrado.flags
    assert any("--tools" in aviso for aviso in encontrado.warnings), \
        encontrado.warnings
    # o envio NÃO muda por causa disso
    from . import backends
    args = backends.ClaudeCLIBackend()._command("oi", "sonnet")
    assert args[args.index("--tools") + 1] == "", args


async def test_help_ilegivel_nao_inventa_estado_da_flag():
    encontrado = model_discovery.discover(runner=fake_runner(falhar=("help",)))
    assert encontrado.flags is None
    assert encontrado.tools_flag_state() == "não verificada"
    assert not any("--tools" in aviso for aviso in encontrado.warnings), \
        encontrado.warnings


async def test_cli_indisponivel_nao_impede_o_arranque():
    """CLI ausente não pode travar a inicialização por causa da verificação."""
    app = make_app(fake_runner(falhar=("help", "auth", "version")))
    async with app.run_test(size=SIZE) as pilot:
        assert await esperar_descoberta(pilot)
        assert app.backend.models(), "a lista de modelos precisa existir"
        await send(pilot, "/status")
        assert "provedor" in app._plain[-1]


async def test_status_mostra_estado_da_flag():
    app = make_app(fake_runner())
    async with app.run_test(size=SIZE) as pilot:
        assert await esperar_descoberta(pilot)
        await send(pilot, "/status")
        texto = app._plain[-1]
        assert "--tools" in texto, texto
        assert app._tools_flag_state in texto, (app._tools_flag_state, texto)


TESTS = [
    test_parse_dos_aliases_do_help,
    test_parse_ignora_outras_opcoes,
    test_alias_novo_da_cli_aparece_marcado,
    test_help_ilegivel_vira_aviso,
    test_plano_lido_do_auth_status,
    test_sem_login_avisa,
    test_auth_ilegivel_vira_aviso,
    test_nomes_amigaveis_separados,
    test_catalogo_tem_data_de_revisao,
    test_modelos_obsoletos_nao_entram,
    test_cache_grava_e_le,
    test_falha_na_consulta_usa_cache,
    test_sem_cache_usa_catalogo_com_aviso,
    test_cache_antigo_perde_modelo_obsoleto,
    test_sonda_desligada_por_padrao,
    test_sonda_confirmada_filtra_a_lista,
    test_descoberta_no_inicio,
    test_seletor_usa_a_lista_atualizada,
    test_seletor_aplica_o_alias_nao_o_nome_exibido,
    test_modelo_atual_preservado,
    test_preferencia_salva_volta_apos_descoberta,
    test_modelo_sumido_cai_no_primeiro,
    test_refresh_models_nao_chama_o_modelo,
    test_sonda_pede_confirmacao_e_pode_ser_recusada,
    test_sonda_n_cancela_e_nao_envia,
    test_sonda_s_inicia_apos_confirmacao,
    test_sonda_resposta_invalida_continua_aguardando,
    test_sonda_invalida_depois_n_nao_vaza_para_o_backend,
    test_refresh_models_no_autocomplete,
    test_status_continua_completo,
    test_flags_da_cli_sao_lidas_do_help,
    test_tools_documentada_no_help_real,
    test_tools_ausente_gera_aviso_sem_mudar_comportamento,
    test_help_ilegivel_nao_inventa_estado_da_flag,
    test_cli_indisponivel_nao_impede_o_arranque,
    test_status_mostra_estado_da_flag,
]


async def _run_all():
    falhas = 0
    for test in TESTS:
        nome = test.__name__
        try:
            await test()
        except Exception as erro:
            falhas += 1
            print("falhou  {0}: {1}: {2}".format(nome, type(erro).__name__, erro))
        else:
            print("ok      {0}".format(nome))
    print("")
    print("{0} testes, {1} falha(s)".format(len(TESTS), falhas))
    return falhas


def main() -> int:
    return 1 if asyncio.run(_run_all()) else 0


if __name__ == "__main__":
    sys.exit(main())
