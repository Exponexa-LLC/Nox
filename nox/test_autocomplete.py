# -*- coding: utf-8 -*-
"""Testes Pilot do autocomplete de comandos da TUI.

Rodar com:

    python -m nox.test_autocomplete      (com o ambiente do projeto ativo)

Não há pytest no .venv, então este arquivo traz o próprio runner; as funções
continuam sendo `async def test_*`, compatíveis com pytest-asyncio se um dia
ele for instalado. O backend real nunca é chamado: os testes usam um stub em
memória, e nenhum arquivo de configuração é lido ou escrito.
"""

from __future__ import annotations

import asyncio
import sys

from textual.widgets import Input, Static

from . import backends
from . import commands
from .__main__ import NoxApp


class StubBackend(backends.Backend):
    """Backend de teste: responde na hora, sem processo nem rede."""

    name = "stub"
    label = "stub de teste"

    def __init__(self) -> None:
        self.sent = []
        self.session_id = "sess-teste"
        self.last_model = "sonnet"
        self.timeout = 1.0
        self.cwd = ""

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def models(self):
        return ["sonnet", "opus", "haiku"]

    def configure(self, settings) -> None:
        pass

    def session_label(self) -> str:
        return self.session_id

    def reset(self) -> None:
        self.sent = []

    def cancel(self) -> bool:
        return False

    def send(self, text, model=""):
        self.sent.append(text)
        return backends.Reply(ok=True, text="eco: " + text)


class StubConfig(object):
    """Config em memória: nada é lido nem gravado em disco."""

    path = "(memoria)"
    writable = False
    provider = "stub"
    workspace = ""
    timeout = 1.0
    # perfil padrão dos testes: o mesmo do app, o mais contido
    profile = "conversa"

    def model_for(self, provider):
        return "sonnet"

    def set_model(self, provider, model):
        pass

    def set_provider(self, provider):
        pass

    def set_workspace(self, path):
        pass

    def set_profile(self, name):
        self.profile = name

    def take_warnings(self):
        return []


def make_app():
    return NoxApp(backend=StubBackend(), settings=StubConfig())


def panel_open(app):
    return not app.query_one("#suggestions", Static).has_class("hidden")


def names(app):
    return [name for name, _description in app._suggestions]


def blocks(app):
    return len(app.transcript.children)


#: Nomes de tecla do Textual para os caracteres que os testes digitam.
KEY_NAMES = {
    "/": "slash",
    " ": "space",
    "\\": "backslash",
    ":": "colon",
    ".": "full_stop",
    "-": "minus",
    "_": "underscore",
}


async def type_text(pilot, text, clear=True):
    """Digita `text` de verdade, tecla por tecla, como o usuário faria."""
    if clear:
        pilot.app.query_one("#prompt", Input).value = ""
        await pilot.pause()
    for char in text:
        await pilot.press(KEY_NAMES.get(char, char))


# ---------------------------------------------------------------- os testes


async def test_abre_ao_digitar_barra():
    async with make_app().run_test() as pilot:
        await type_text(pilot, "/")
        assert panel_open(pilot.app), "o painel deveria abrir ao digitar /"
        esperado = [name for name, _ in commands.COMMANDS]
        assert names(pilot.app) == esperado, names(pilot.app)
        assert len(esperado) == len(commands.COMMANDS)


async def test_filtra_prefixo():
    async with make_app().run_test() as pilot:
        await type_text(pilot, "/st")
        assert names(pilot.app) == ["/status"], names(pilot.app)
        await type_text(pilot, "/co")
        assert names(pilot.app) == ["/copy", "/copy tudo"], names(pilot.app)
        await type_text(pilot, "/zzz")
        assert not panel_open(pilot.app), "prefixo sem match nao abre painel"


async def test_sem_barra_nao_abre():
    async with make_app().run_test() as pilot:
        await type_text(pilot, "oi lobo")
        assert not panel_open(pilot.app), "texto normal nao mostra sugestoes"
        assert names(pilot.app) == []


async def test_navega_setas():
    async with make_app().run_test() as pilot:
        app = pilot.app
        await type_text(pilot, "/")
        assert app._suggestion_index == 0
        await pilot.press("down")
        assert app._suggestion_index == 1
        await pilot.press("down")
        assert app._suggestion_index == 2
        await pilot.press("up")
        assert app._suggestion_index == 1
        # dá a volta para o fim da lista
        await pilot.press("up")
        await pilot.press("up")
        assert app._suggestion_index == len(app._suggestions) - 1
        assert app.query_one("#prompt", Input).value == "/", "texto nao muda"


async def test_tab_completa_sem_executar():
    async with make_app().run_test() as pilot:
        app = pilot.app
        antes = blocks(app)
        await type_text(pilot, "/st")
        await pilot.press("tab")
        prompt = app.query_one("#prompt", Input)
        assert prompt.value == "/status ", repr(prompt.value)
        assert not panel_open(app), "Tab fecha o painel"
        assert blocks(app) == antes, "Tab nao pode executar o comando"
        assert app.focused is prompt, "Tab nao pode mudar o foco"


async def test_tab_preserva_argumento():
    async with make_app().run_test() as pilot:
        app = pilot.app
        prompt = app.query_one("#prompt", Input)
        # argumento já digitado: o painel fica fechado e o texto intacto
        await type_text(pilot, "/model opus")
        assert not panel_open(app)
        assert prompt.value == "/model opus"
        # completar com argumento presente preserva o argumento
        texto, _cursor = commands.complete("/mo opus", "/model")
        assert texto == "/model opus", texto
        # workspace com caminho do Windows
        await type_text(pilot, "/works")
        await pilot.press("tab")
        assert prompt.value == "/workspace "
        await type_text(pilot, "D:\\pasta", clear=False)
        assert prompt.value == "/workspace D:\\pasta", repr(prompt.value)
        assert not panel_open(app)
        # e /provider claude
        await type_text(pilot, "/prov")
        await pilot.press("tab")
        await type_text(pilot, "claude", clear=False)
        assert prompt.value == "/provider claude", repr(prompt.value)


async def test_esc_fecha_sem_apagar():
    async with make_app().run_test() as pilot:
        app = pilot.app
        await type_text(pilot, "/st")
        assert panel_open(app)
        await pilot.press("escape")
        assert not panel_open(app), "Esc fecha o painel"
        prompt = app.query_one("#prompt", Input)
        assert prompt.value == "/st", "Esc nao pode apagar o texto"
        assert prompt.cursor_position == len("/st")


async def test_enter_executa_normal():
    async with make_app().run_test() as pilot:
        app = pilot.app
        antes = blocks(app)
        await type_text(pilot, "/status")
        assert panel_open(app)
        await pilot.press("enter")
        await pilot.pause()
        assert not panel_open(app)
        assert blocks(app) > antes, "/status deveria escrever no transcript"
        assert app.query_one("#prompt", Input).value == ""


async def test_enter_completa_comando_incompleto():
    async with make_app().run_test() as pilot:
        app = pilot.app
        antes = blocks(app)
        await type_text(pilot, "/sta")
        await pilot.press("enter")
        assert app.query_one("#prompt", Input).value == "/status "
        assert blocks(app) == antes, "comando incompleto nao executa no Enter"


async def test_texto_normal_intacto():
    async with make_app().run_test() as pilot:
        app = pilot.app
        await type_text(pilot, "oi lobo")
        await pilot.press("enter")
        for _ in range(60):
            if app.backend.sent:
                break
            await pilot.pause()
        assert app.backend.sent == ["oi lobo"], app.backend.sent


async def test_largura_80_colunas():
    async with make_app().run_test(size=(80, 24)) as pilot:
        app = pilot.app
        await type_text(pilot, "/")
        assert panel_open(app)
        painel = app.query_one("#suggestions", Static)
        linhas = painel.content.plain.split(chr(10))
        assert len(linhas) == len(commands.COMMANDS), len(linhas)
        for linha in linhas:
            assert len(linha) <= 74, (len(linha), linha)
        assert painel.size.width <= 80, painel.size.width
        assert painel.region.right <= 80, painel.region


TESTS = [
    test_abre_ao_digitar_barra,
    test_filtra_prefixo,
    test_sem_barra_nao_abre,
    test_navega_setas,
    test_tab_completa_sem_executar,
    test_tab_preserva_argumento,
    test_esc_fecha_sem_apagar,
    test_enter_executa_normal,
    test_enter_completa_comando_incompleto,
    test_texto_normal_intacto,
    test_largura_80_colunas,
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
