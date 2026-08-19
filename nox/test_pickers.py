# -*- coding: utf-8 -*-
"""Testes Pilot dos seletores visuais de modelo e de provedor, em 80x24.

Rodar com:

    python -m nox.test_pickers      (com o ambiente do projeto ativo)

Mesma abordagem do test_autocomplete: runner próprio (não há pytest no .venv),
backend stub em memória e config em memória — a CLI do Claude não é chamada e
nada é gravado em disco.
"""

from __future__ import annotations

import asyncio
import sys

from textual.widgets import Input, Static

from . import backends
from . import pickers
from .test_autocomplete import (
    KEY_NAMES,
    StubConfig,
    blocks,
    panel_open,
    type_text,
)
from .__main__ import NoxApp

SIZE = (80, 24)


class ModelStubBackend(backends.Backend):
    """Backend de teste com metadados de modelo, como o Claude tem."""

    name = "stub"
    label = "stub de teste"
    MODELS = ("sonnet", "opus", "haiku")
    MODEL_INFO = {
        "sonnet": ("stub-sonnet", "equilibrado"),
        "opus": ("stub-opus", "o mais capaz"),
        "haiku": ("stub-haiku", "o mais rápido"),
    }

    def __init__(self) -> None:
        self.sent = []
        self.session_id = "sess-teste"
        self.last_model = "sonnet"
        self.timeout = 1.0
        self.cwd = ""
        self.resets = 0

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def configure(self, settings) -> None:
        pass

    def session_label(self) -> str:
        return self.session_id

    def reset(self) -> None:
        self.resets += 1

    def cancel(self) -> bool:
        return False

    def send(self, text, model=""):
        self.sent.append(text)
        return backends.Reply(ok=True, text="eco: " + text)


def make_app():
    return NoxApp(backend=ModelStubBackend(), settings=StubConfig())


def picker_open(app):
    return not app.query_one("#picker", Static).has_class("hidden")


def picker_lines(app):
    return app.query_one("#picker", Static).content.plain.split(chr(10))


async def run_command(pilot, text):
    """Digita um comando e envia, como o usuário faria."""
    await type_text(pilot, text)
    await pilot.press("enter")
    await pilot.pause()


# ------------------------------------------------------- seletor de modelo


async def test_model_picker_abre():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await run_command(pilot, "/model")
        assert picker_open(app), "/model sem argumento abre o seletor"
        assert app._picker_kind == "model"
        # os modelos vêm do backend ativo, com nome, alias e descrição
        nomes = [row[0] for row in app._picker_rows]
        assert nomes == list(app.backend.models()), nomes
        texto = chr(10).join(picker_lines(app))
        for nome, alias, descricao in app.backend.model_rows():
            assert nome in texto and alias in texto and descricao in texto
        # começa na linha do modelo atual
        assert app._picker_rows[app._picker_index][0] == app.model


async def test_model_picker_navega():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await run_command(pilot, "/model")
        modelo_antes = app.model
        inicio = app._picker_index
        await pilot.press("down")
        assert app._picker_index == (inicio + 1) % len(app._picker_rows)
        await pilot.press("up")
        await pilot.press("up")
        assert app._picker_index == (inicio - 1) % len(app._picker_rows)
        assert app.model == modelo_antes, "navegar não aplica nada"
        assert picker_open(app)


async def test_model_picker_seleciona():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        antes = blocks(app)
        await run_command(pilot, "/model")
        await pilot.press("down")
        escolhido = app._picker_rows[app._picker_index][0]
        await pilot.press("enter")
        await pilot.pause()
        assert not picker_open(app), "Enter fecha o menu"
        assert app.model == escolhido, (app.model, escolhido)
        assert blocks(app) > antes, "o aviso deveria ir para o transcript"


async def test_model_picker_cancela():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await run_command(pilot, "/model")
        antes = blocks(app)
        modelo_antes = app.model
        await pilot.press("down")
        await pilot.press("escape")
        assert not picker_open(app), "Esc fecha o menu"
        assert app.model == modelo_antes, "Esc não altera o modelo"
        assert blocks(app) == antes, "Esc não escreve no transcript"


async def test_model_argumento_direto():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await run_command(pilot, "/model opus")
        assert not picker_open(app), "/model <nome> não abre menu"
        assert app.model == "opus", app.model


# ----------------------------------------------------- seletor de provedor


async def test_provider_picker_abre():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await run_command(pilot, "/provider")
        assert picker_open(app), "/provider sem argumento abre o seletor"
        assert app._picker_kind == "provider"
        nomes = [row[0] for row in app._picker_rows]
        assert nomes == list(backends.PROVIDERS), nomes
        estados = {row[0]: row[1] for row in app._picker_rows}
        assert estados["claude"] in ("configurado", "não configurado"), estados
        # os planejados aparecem com estado e motivo, sem serem implementados
        for planejado in ("gemini", "openai", "ollama"):
            assert estados[planejado] == "não configurado", estados[planejado]
            motivo = dict((r[0], r[2]) for r in app._picker_rows)[planejado]
            assert motivo, "faltou o motivo de " + planejado


async def test_provider_picker_marca_ativo():
    """O provedor em uso aparece como ativo, com o rótulo do backend."""
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        # o stub não está no registro: monta as linhas com claude como ativo
        claude = backends.get_backend("claude", cwd=app.workspace)
        linhas = pickers.provider_rows(
            backends.PROVIDERS,
            "claude",
            claude,
            lambda name: backends.get_backend(name, cwd=app.workspace),
        )
        estados = dict((nome, estado) for nome, estado, _motivo in linhas)
        assert estados["claude"].startswith("ativo · "), estados["claude"]
        assert estados["gemini"] == "não configurado"


async def test_provider_picker_navega_e_cancela():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await run_command(pilot, "/provider")
        antes = blocks(app)
        provedor_antes = app.provider
        backend_antes = app.backend
        await pilot.press("down")
        await pilot.press("down")
        assert app._picker_index == 2
        await pilot.press("escape")
        assert not picker_open(app)
        assert app.provider == provedor_antes
        assert app.backend is backend_antes, "Esc não troca o backend"
        assert blocks(app) == antes


async def test_provider_picker_seleciona_nao_configurado():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await run_command(pilot, "/provider")
        provedor_antes = app.provider
        backend_antes = app.backend
        # anda até um provedor planejado (não configurado) e aplica
        alvo = list(backends.PROVIDERS).index("gemini")
        while app._picker_index != alvo:
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert not picker_open(app)
        assert app.provider == provedor_antes, "nada muda se não está configurado"
        assert app.backend is backend_antes
        texto = app._plain[-1]
        assert "gemini" in texto and "Nada mudou" in texto, texto


async def test_provider_picker_seleciona_configurado():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await run_command(pilot, "/provider")
        # o eco local está sempre disponível: serve de destino real do teste
        alvo = list(backends.PROVIDERS).index("echo")
        while app._picker_index != alvo:
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert not picker_open(app)
        assert app.provider == "echo", app.provider
        assert app.backend is not None
        texto = app._plain[-1]
        assert "Conversa nova" in texto, texto
        assert app.backend.session_label(), "sessão nova deveria existir"


# ------------------------------------------------- convivência com o resto


async def test_status_continua_completo():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await run_command(pilot, "/status")
        texto = app._plain[-1]
        for rotulo in ("provedor", "backend", "modelo", "sessão",
                       "workspace", "config"):
            assert rotulo in texto, (rotulo, texto)


async def test_autocomplete_continua_vivo():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await type_text(pilot, "/mod")
        assert panel_open(app), "o autocomplete continua abrindo"
        assert not picker_open(app), "os dois painéis não abrem juntos"
        await pilot.press("tab")
        assert app.query_one("#prompt", Input).value == "/model "


async def test_picker_bloqueia_digitacao():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await run_command(pilot, "/model")
        await pilot.press("a", KEY_NAMES["/"], "b")
        assert app.query_one("#prompt", Input).value == "", "menu engole a digitação"
        assert picker_open(app), "digitar não fecha o menu"
        await pilot.press("escape")


async def test_layout_80_colunas():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await run_command(pilot, "/provider")
        painel = app.query_one("#picker", Static)
        for linha in picker_lines(app):
            assert len(linha) <= 74, (len(linha), linha)
        assert painel.region.right <= 80, painel.region
        # a barra de prompt e o rodapé continuam visíveis abaixo do menu
        promptbar = app.query_one("#promptbar")
        rodape = app.query_one("#footer")
        assert painel.region.bottom <= promptbar.region.y, (painel.region,
                                                            promptbar.region)
        assert rodape.region.bottom <= 24, rodape.region
        # o mascote e o cabeçalho seguem no lugar
        assert app.query_one("#wolf").region.y == 0
        assert app.query_one("#header").region.height == 6


TESTS = [
    test_model_picker_abre,
    test_model_picker_navega,
    test_model_picker_seleciona,
    test_model_picker_cancela,
    test_model_argumento_direto,
    test_provider_picker_abre,
    test_provider_picker_marca_ativo,
    test_provider_picker_navega_e_cancela,
    test_provider_picker_seleciona_nao_configurado,
    test_provider_picker_seleciona_configurado,
    test_status_continua_completo,
    test_autocomplete_continua_vivo,
    test_picker_bloqueia_digitacao,
    test_layout_80_colunas,
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
