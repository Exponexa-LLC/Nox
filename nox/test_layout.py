# -*- coding: utf-8 -*-
"""Testes de layout 80x24 e do comando /workspace.

Rodar com:

    python -m nox.test_layout      (com o ambiente do projeto ativo)

Backend stub e config em memória: a CLI do Claude não é chamada e nada é
gravado em disco. A troca de workspace usa a pasta temporária do sistema como
destino — nenhum arquivo dela é lido ou alterado, ela só vira o cwd do backend.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

from textual.widgets import Static

from .test_autocomplete import make_app

SIZE = (80, 24)


async def send(pilot, text):
    """Envia um comando pelo campo de entrada."""
    pilot.app.query_one("#prompt").value = text
    await pilot.press("enter")
    await pilot.pause()


def region(app, selector):
    return app.query_one(selector).region


# ------------------------------------------------------------- layout


async def test_cabecalho_e_mascote():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        header = region(app, "#header")
        wolf = region(app, "#wolf")
        assert header.y == 0 and header.height == 6, header
        assert wolf.width == 14 and wolf.height == 5, wolf
        assert wolf.y == 0, wolf


async def test_ordem_vertical():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        header = region(app, "#header")
        transcript = region(app, "#transcript")
        promptbar = region(app, "#promptbar")
        footer = region(app, "#footer")
        assert transcript.y == header.bottom, (header, transcript)
        assert transcript.bottom <= promptbar.y, (transcript, promptbar)
        assert promptbar.height == 3, promptbar
        assert promptbar.bottom <= footer.y, (promptbar, footer)
        assert footer.bottom == 24, footer


async def test_nada_passa_de_80_colunas():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        for selector in ("#header", "#wolf", "#transcript", "#promptbar",
                         "#footer", "#statusbar"):
            assert region(app, selector).right <= 80, (selector,
                                                       region(app, selector))


async def test_statusbar_oculto_em_repouso():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        assert app.query_one("#statusbar", Static).has_class("hidden")
        assert app._busy is False


async def test_foco_inicial_no_campo():
    async with make_app().run_test(size=SIZE) as pilot:
        assert pilot.app.focused.id == "prompt", pilot.app.focused


async def test_paineis_fechados_no_inicio():
    """Sem autocomplete nem seletor abertos, o layout é o de sempre."""
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        assert app.query_one("#suggestions", Static).has_class("hidden")
        assert app.query_one("#picker", Static).has_class("hidden")


async def test_cabecalho_mostra_backend_modelo_workspace():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        assert app.query_one("#backend", Static).content.plain.strip()
        assert app.query_one("#meta", Static).content.plain.strip()
        assert app.query_one("#workspace", Static).content.plain.strip()


# ---------------------------------------------------------- workspace


async def test_workspace_mostra_pasta_atual():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await send(pilot, "/workspace")
        assert app.workspace in app._plain[-1], app._plain[-1]


async def test_workspace_recusa_inexistente():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        antes = app.workspace
        await send(pilot, "/workspace D:/pasta-que-nao-existe-xyz")
        assert "inexistente" in app._plain[-1], app._plain[-1]
        assert app.workspace == antes


async def test_workspace_recusa_arquivo():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        antes = app.workspace
        arquivo = os.path.abspath(__file__)
        await send(pilot, "/workspace " + arquivo)
        assert "não é uma pasta" in app._plain[-1], app._plain[-1]
        assert app.workspace == antes


async def test_workspace_pede_confirmacao_e_cancela():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        antes = app.workspace
        await send(pilot, "/workspace " + tempfile.gettempdir())
        assert "responda s ou n" in app._plain[-1], app._plain[-1]
        assert app.workspace == antes, "não muda antes de confirmar"
        await send(pilot, "n")
        assert "cancelada" in app._plain[-1], app._plain[-1]
        assert app.workspace == antes


async def test_workspace_aplica_e_repassa_ao_backend():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        alvo = os.path.abspath(tempfile.gettempdir())
        await send(pilot, "/workspace " + alvo)
        await send(pilot, "s")
        assert os.path.normcase(app.workspace) == os.path.normcase(alvo), app.workspace
        assert os.path.normcase(app.backend.cwd) == os.path.normcase(app.workspace)
        cabecalho = app.query_one("#workspace", Static).content.plain
        assert cabecalho.strip(), cabecalho
        # o mesmo caminho de novo é recusado como redundante
        await send(pilot, "/workspace " + alvo)
        assert "já é esse" in app._plain[-1], app._plain[-1]


async def test_status_traz_workspace():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await send(pilot, "/status")
        texto = app._plain[-1]
        for rotulo in ("provedor", "backend", "modelo", "sessão",
                       "workspace", "config"):
            assert rotulo in texto, (rotulo, texto)


TESTS = [
    test_cabecalho_e_mascote,
    test_ordem_vertical,
    test_nada_passa_de_80_colunas,
    test_statusbar_oculto_em_repouso,
    test_foco_inicial_no_campo,
    test_paineis_fechados_no_inicio,
    test_cabecalho_mostra_backend_modelo_workspace,
    test_workspace_mostra_pasta_atual,
    test_workspace_recusa_inexistente,
    test_workspace_recusa_arquivo,
    test_workspace_pede_confirmacao_e_cancela,
    test_workspace_aplica_e_repassa_ao_backend,
    test_status_traz_workspace,
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
