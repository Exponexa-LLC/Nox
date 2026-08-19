# -*- coding: utf-8 -*-
"""Testes de seleção e cópia do transcript.

Rodar com:

    python -m nox.test_copy      (com o ambiente do projeto ativo)

A área de transferência REAL do sistema não é tocada: os dois caminhos de cópia
(OSC 52 pelo terminal e o utilitário `clip` do Windows) são interceptados
durante os testes e restaurados no fim. O backend é o stub, sem chamar a CLI.
"""

from __future__ import annotations

import asyncio
import sys

from . import __main__ as tui
from .test_autocomplete import make_app

SIZE = (80, 24)

#: Tudo que "seria copiado" durante um teste, como (canal, texto).
COPIADO = []


class FakePopen(object):
    """Substitui o `clip` do Windows: registra em vez de copiar de verdade."""

    def __init__(self, *args, **kwargs):
        self.returncode = 0

    def communicate(self, data=None, timeout=None):
        COPIADO.append(("clip", data))
        return (b"", b"")


def _fake_osc52(self, text):
    COPIADO.append(("osc52", text))


def instalar_espiao():
    """Troca os dois caminhos de cópia e devolve o que estava lá antes."""
    anterior = (tui.subprocess.Popen, tui.NoxApp.copy_to_clipboard)
    tui.subprocess.Popen = FakePopen
    tui.NoxApp.copy_to_clipboard = _fake_osc52
    return anterior


def remover_espiao(anterior):
    tui.subprocess.Popen, tui.NoxApp.copy_to_clipboard = anterior


def copiado_por_osc52():
    return [texto for canal, texto in COPIADO if canal == "osc52"]


async def send(pilot, text):
    pilot.app.query_one("#prompt").value = text
    await pilot.press("enter")
    await pilot.pause()


async def com_conversa(pilot):
    """Deixa uma pergunta e uma resposta no transcript."""
    app = pilot.app
    app.write_user("primeira pergunta")
    app.write_agent("**resposta** do agente")
    await pilot.pause()
    return app


# ------------------------------------------------------------ transcript


async def test_transcript_guarda_texto_puro():
    async with make_app().run_test(size=SIZE) as pilot:
        app = await com_conversa(pilot)
        assert app._plain[-2:] == ["> primeira pergunta",
                                   "**resposta** do agente"], app._plain[-2:]
        assert app._last_reply == "**resposta** do agente"


async def test_blocos_sao_widgets_selecionaveis():
    """Os blocos precisam ser widgets de texto: é o que o mouse consegue copiar."""
    async with make_app().run_test(size=SIZE) as pilot:
        app = await com_conversa(pilot)
        assert len(app.transcript.children) >= 3, len(app.transcript.children)
        assert hasattr(app.screen, "get_selected_text")


async def test_transcript_nao_rouba_o_foco():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        assert app.transcript.can_focus is False
        assert app.focused.id == "prompt"


# ---------------------------------------------------------------- cópia


async def test_copy_ultima_resposta():
    async with make_app().run_test(size=SIZE) as pilot:
        app = await com_conversa(pilot)
        del COPIADO[:]
        await send(pilot, "/copy")
        assert "**resposta** do agente" in copiado_por_osc52(), COPIADO
        assert "copiado" in app._plain[-1], app._plain[-1]


async def test_copy_tudo():
    async with make_app().run_test(size=SIZE) as pilot:
        app = await com_conversa(pilot)
        del COPIADO[:]
        await send(pilot, "/copy tudo")
        textos = copiado_por_osc52()
        assert textos, COPIADO
        assert "primeira pergunta" in textos[0], textos[0]
        assert "**resposta** do agente" in textos[0], textos[0]
        assert "transcript" in app._plain[-1], app._plain[-1]


async def test_copy_sem_nada():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        del COPIADO[:]
        await send(pilot, "/copy")
        assert not COPIADO, COPIADO
        assert "nada para copiar" in app._plain[-1], app._plain[-1]


async def test_ctrl_y_copia_ultima_resposta():
    async with make_app().run_test(size=SIZE) as pilot:
        await com_conversa(pilot)
        del COPIADO[:]
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert "**resposta** do agente" in copiado_por_osc52(), COPIADO


async def test_ctrl_c_sem_selecao_avisa():
    async with make_app().run_test(size=SIZE) as pilot:
        app = await com_conversa(pilot)
        del COPIADO[:]
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert not COPIADO, "sem seleção não se copia nada"
        assert "nada selecionado" in app._plain[-1], app._plain[-1]


async def test_copia_passa_pelos_dois_caminhos():
    """No Windows a cópia tenta o terminal (OSC 52) e também o `clip`."""
    async with make_app().run_test(size=SIZE) as pilot:
        await com_conversa(pilot)
        del COPIADO[:]
        await send(pilot, "/copy")
        canais = set(canal for canal, _texto in COPIADO)
        assert "osc52" in canais, canais
        if sys.platform.startswith("win"):
            assert "clip" in canais, canais


# ------------------------------------------------------- limpar a tela


async def test_clear_zera_transcript_e_ultima_resposta():
    async with make_app().run_test(size=SIZE) as pilot:
        app = await com_conversa(pilot)
        await send(pilot, "/clear")
        assert app._last_reply == "", app._last_reply
        del COPIADO[:]
        await send(pilot, "/copy")
        assert not COPIADO, "depois do /clear não há resposta para copiar"


TESTS = [
    test_transcript_guarda_texto_puro,
    test_blocos_sao_widgets_selecionaveis,
    test_transcript_nao_rouba_o_foco,
    test_copy_ultima_resposta,
    test_copy_tudo,
    test_copy_sem_nada,
    test_ctrl_y_copia_ultima_resposta,
    test_ctrl_c_sem_selecao_avisa,
    test_copia_passa_pelos_dois_caminhos,
    test_clear_zera_transcript_e_ultima_resposta,
]


async def _run_all():
    anterior = instalar_espiao()
    falhas = 0
    try:
        for test in TESTS:
            nome = test.__name__
            del COPIADO[:]
            try:
                await test()
            except Exception as erro:
                falhas += 1
                print("falhou  {0}: {1}: {2}".format(
                    nome, type(erro).__name__, erro))
            else:
                print("ok      {0}".format(nome))
    finally:
        remover_espiao(anterior)
    print("")
    print("{0} testes, {1} falha(s)".format(len(TESTS), falhas))
    return falhas


def main() -> int:
    return 1 if asyncio.run(_run_all()) else 0


if __name__ == "__main__":
    sys.exit(main())
