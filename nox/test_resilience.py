# -*- coding: utf-8 -*-
"""Testes das correções das fases A e B — resiliência e coerência.

Rodar com:

    python -m nox.test_resilience      (com o ambiente do projeto ativo)

Nenhuma chamada ao Claude, nenhuma sonda, nenhuma operação remota real: todos
os workers são exercitados com stubs que falham de propósito.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

from . import backends
from . import config as config_mod
from . import model_discovery
from .test_autocomplete import StubConfig
from .test_models import ClaudeLikeStub, esperar_descoberta, fake_runner
from .test_models import make_app as make_claude_app
from .test_remote import Ambiente, ssh_runner
from .test_remote import make_app as make_remote_app
from .__main__ import NoxApp

SIZE = (80, 24)


async def send(pilot, text):
    pilot.app.query_one("#prompt").value = text
    await pilot.press("enter")
    await pilot.pause()


# ------------------------------------------------ A: fronteira de erro


async def test_backend_que_levanta_nao_derruba_o_app():
    """Regressão do achado N-01: exceção no worker matava a TUI inteira."""
    app = make_claude_app(fake_runner())

    def explode(text, model=""):
        raise RuntimeError("falha inesperada")

    app.backend.send = explode
    async with app.run_test(size=SIZE) as pilot:
        assert await esperar_descoberta(pilot)
        await send(pilot, "oi lobo")
        for _ in range(60):
            if not app._busy:
                break
            await pilot.pause()
        assert app._busy is False, "N-02: busy ficou preso"
        assert any("erro inesperado" in linha for linha in app._plain), app._plain[-1]
        # e o app continua utilizável
        await send(pilot, "/status")
        assert "provedor" in app._plain[-1]


async def test_backend_que_devolve_none():
    app = make_claude_app(fake_runner())
    app.backend.send = lambda text, model="": None
    async with app.run_test(size=SIZE) as pilot:
        assert await esperar_descoberta(pilot)
        await send(pilot, "oi")
        for _ in range(60):
            if not app._busy:
                break
            await pilot.pause()
        assert app._busy is False
        assert "não devolveu resposta" in app._plain[-1], app._plain[-1]


async def test_descoberta_que_levanta_nao_derruba():
    def runner_explosivo(args, timeout=20.0):
        raise RuntimeError("cli quebrada")

    app = make_claude_app(runner_explosivo)
    async with app.run_test(size=SIZE) as pilot:
        for _ in range(60):
            await pilot.pause()
        # o app segue vivo e responde a comandos
        await send(pilot, "/status")
        assert "provedor" in app._plain[-1], app._plain[-1]


async def test_remoto_que_levanta_nao_derruba():
    ambiente = Ambiente()

    def runner_explosivo(comando, timeout=0):
        raise RuntimeError("transporte quebrado")

    try:
        app = make_remote_app(ambiente, runner_explosivo)
        async with app.run_test(size=SIZE) as pilot:
            assert await esperar_descoberta(pilot)
            await send(pilot, "/remote codeplay uptime")
            await send(pilot, "s")
            for _ in range(60):
                if any("não foi possível executar" in linha for linha in app._plain):
                    break
                await pilot.pause()
            assert any("não foi possível executar" in linha for linha in app._plain), \
                app._plain[-1]
            await send(pilot, "/status")
            assert "provedor" in app._plain[-1]
    finally:
        ambiente.close()


async def test_sonda_com_falha_nao_derruba():
    def runner(args, timeout=20.0):
        if "-p" in args:
            raise RuntimeError("sonda quebrada")
        return fake_runner()(args, timeout)

    app = make_claude_app(runner)
    async with app.run_test(size=SIZE) as pilot:
        assert await esperar_descoberta(pilot)
        await send(pilot, "/refresh-models --sonda")
        await send(pilot, "s")
        for _ in range(60):
            if any("sonda falhou" in linha for linha in app._plain):
                break
            await pilot.pause()
        assert any("sonda falhou" in linha for linha in app._plain), app._plain[-1]


# --------------------------------------------------- A: timeout do config


async def test_timeout_do_config_validado():
    """Regressão do N-03: True virava 1s e negativo quebrava toda conversa."""
    pasta = tempfile.mkdtemp(prefix="nox-timeout-")
    legado = os.path.join(pasta, "inexistente")
    try:
        caminho = os.path.join(pasta, "config.json")
        casos = [
            (True, 120.0, True),      # bool não é timeout
            (False, 120.0, True),
            (-5, 120.0, True),        # negativo estouraria na hora
            (0, 120.0, True),
            (1, 120.0, True),         # abaixo do mínimo
            (99999, 120.0, True),     # acima do máximo
            ("120", 120.0, True),     # string
            (90.5, 90.5, False),      # float é legítimo
            (300, 300.0, False),
        ]
        for valor, esperado, deve_avisar in casos:
            with open(caminho, "w", encoding="utf-8") as handle:
                json.dump({"timeout": valor}, handle)
            preferencias = config_mod.Config(path=caminho, legacy_dir=legado)
            assert preferencias.timeout == esperado, (valor, preferencias.timeout)
            avisou = any("timeout" in aviso for aviso in preferencias.warnings)
            assert avisou == deve_avisar, (valor, preferencias.warnings)
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


async def test_timeout_chega_saudavel_ao_backend():
    pasta = tempfile.mkdtemp(prefix="nox-timeout-")
    try:
        caminho = os.path.join(pasta, "config.json")
        with open(caminho, "w", encoding="utf-8") as handle:
            json.dump({"timeout": True}, handle)
        preferencias = config_mod.Config(
            path=caminho, legacy_dir=os.path.join(pasta, "nao-existe"))
        backend = backends.ClaudeCLIBackend()
        backend.configure(preferencias)
        assert backend.timeout == 120.0, backend.timeout
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


# ------------------------------------------------ A: aviso de log remoto


async def test_perfil_que_exige_trilha_recusa_sem_log():
    """Fase D: `diagnostico-remoto` não executa sem poder registrar."""
    ambiente = Ambiente()
    try:
        app = make_remote_app(ambiente)
        app.config.writable = False   # config somente leitura: sem log
        async with app.run_test(size=SIZE) as pilot:
            assert await esperar_descoberta(pilot)
            assert app.policy.require_remote_log is True
            assert app.remote_log_path == ""
            await send(pilot, "/remote codeplay uptime")
            assert app._pending_remote is None, "não pode nem pedir confirmação"
            assert "exige trilha" in app._plain[-1], app._plain[-1]
            assert "recusada" in app._plain[-1], app._plain[-1]
    finally:
        ambiente.close()


async def test_avisa_quando_o_log_remoto_nao_pode_ser_gravado():
    """N-04: perfil que NÃO exige trilha executa, mas avisa antes.

    Usa um perfil seu, com `remote.read` e `require_remote_log` desligado —
    exatamente a flexibilidade que a Fase D permite dentro dos tetos.
    """
    from . import policy as policy_mod
    from . import profiles

    ambiente = Ambiente()
    try:
        app = make_remote_app(ambiente)
        app.config.writable = False
        sem_exigencia, avisos = policy_mod.from_dict(
            profiles.find("diagnostico-remoto"),
            {"nome": "remoto-sem-trilha", "require_remote_log": False})
        assert not avisos, avisos
        async with app.run_test(size=SIZE) as pilot:
            assert await esperar_descoberta(pilot)
            app.policy = sem_exigencia
            assert app.remote_log_path == ""
            await send(pilot, "/remote codeplay uptime")
            assert app._pending_remote is not None, "aqui a operação segue"
            assert "não deixará trilha" in app._plain[-1], app._plain[-1]
            await send(pilot, "n")
    finally:
        ambiente.close()


# ------------------------------------------ B: confirmação unificada


async def test_workspace_nao_engole_mais_comandos():
    """N-07: /status durante a confirmação cancelava a troca e sumia."""
    app = make_claude_app(fake_runner())
    async with app.run_test(size=SIZE) as pilot:
        assert await esperar_descoberta(pilot)
        anterior = app.workspace
        await send(pilot, "/workspace " + tempfile.gettempdir())
        assert app._pending_workspace is not None
        await send(pilot, "/status")
        assert app._pending_workspace is not None, "a pergunta continua de pé"
        assert "responda s" in app._plain[-1], app._plain[-1]
        assert app.workspace == anterior
        await send(pilot, "n")
        assert app._pending_workspace is None
        assert "cancelada" in app._plain[-1]


async def test_tres_confirmacoes_respondem_igual():
    """workspace, sonda e remoto: mesma semântica de s/n/inválido.

    Precisa de um perfil com as três capacidades — nenhum embutido junta
    `models.probe` e `remote.read`, então o teste declara um perfil próprio,
    dentro dos tetos.
    """
    from . import policy as policy_mod
    from . import profiles

    ambiente = Ambiente()
    try:
        app = make_remote_app(ambiente)
        completo, avisos = policy_mod.from_dict(
            profiles.find("diagnostico-remoto"),
            {"nome": "tudo-local-e-remoto",
             "capacidades": ["chat", "workspace.switch", "models.discover",
                             "models.probe", "remote.read"]})
        assert not avisos, avisos
        async with app.run_test(size=SIZE) as pilot:
            assert await esperar_descoberta(pilot)
            app.policy = completo
            pendencias = [
                ("/workspace " + tempfile.gettempdir(),
                 lambda: app._pending_workspace is not None),
                ("/refresh-models --sonda", lambda: app._pending_probe),
                ("/remote codeplay uptime",
                 lambda: app._pending_remote is not None),
            ]
            for comando, pendente in pendencias:
                await send(pilot, comando)
                assert pendente(), comando
                await send(pilot, "quem sabe")
                assert pendente(), "inválido não pode cancelar: " + comando
                assert "responda s" in app._plain[-1], app._plain[-1]
                await send(pilot, "n")
                assert not pendente(), comando
    finally:
        ambiente.close()


async def test_esc_cancela_pendencia():
    """N-08: antes, Esc não tinha efeito e o usuário ficava preso."""
    ambiente = Ambiente()
    try:
        app = make_remote_app(ambiente)
        async with app.run_test(size=SIZE) as pilot:
            assert await esperar_descoberta(pilot)
            await send(pilot, "/remote codeplay uptime")
            assert app._pending_remote is not None
            await pilot.press("escape")
            await pilot.pause()
            assert app._pending_remote is None, "Esc deveria cancelar"
            assert "cancelado" in app._plain[-1], app._plain[-1]
            # e o input volta ao normal
            await send(pilot, "/status")
            assert "provedor" in app._plain[-1]
    finally:
        ambiente.close()


async def test_esc_sem_pendencia_continua_interrompendo():
    app = make_claude_app(fake_runner())
    async with app.run_test(size=SIZE) as pilot:
        assert await esperar_descoberta(pilot)
        antes = len(app._plain)
        await pilot.press("escape")
        await pilot.pause()
        assert len(app._plain) == antes, "Esc ocioso não deve escrever nada"


# ------------------------------------------------ B: provedor validado


async def test_provider_desconhecido_avisa_e_volta_para_claude():
    """N-06: caía no eco local em silêncio."""
    pasta = tempfile.mkdtemp(prefix="nox-provider-")
    try:
        caminho = os.path.join(pasta, "config.json")
        with open(caminho, "w", encoding="utf-8") as handle:
            json.dump({"provider": "inexistente"}, handle)
        preferencias = config_mod.Config(
            path=caminho, legacy_dir=os.path.join(pasta, "nao-existe"))
        app = NoxApp(settings=preferencias)
        app._discovery_runner = fake_runner()
        assert app.provider == "claude", app.provider
        assert app.backend.name == "claude", app.backend.name
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert any("provedor desconhecido" in linha for linha in app._plain), \
                app._plain
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


# ------------------------------------------------ B: descoberta exclusiva


async def test_descobertas_nao_se_atropelam():
    """N-09: refresh pedido enquanto outro está em voo não empilha consultas.

    Precisa de um runner LENTO: com um instantâneo os pedidos rodam em
    sequência e três consultas por rodada é o comportamento correto.
    """
    import time

    base = fake_runner()

    def lento(args, timeout=20.0):
        time.sleep(0.25)
        return base(args, timeout)

    lento.chamadas = base.chamadas
    app = make_claude_app(lento)
    async with app.run_test(size=SIZE) as pilot:
        for _ in range(200):
            if app._models_source:
                break
            await pilot.pause()
        antes = len(lento.chamadas)
        for _ in range(4):
            app.query_one("#prompt").value = "/refresh-models"
            await pilot.press("enter")
            await pilot.pause()
        assert any("já estou procurando" in linha for linha in app._plain), \
            app._plain[-3:]
        for _ in range(200):
            if not app._discovering:
                break
            await pilot.pause()
        executadas = len(lento.chamadas) - antes
        assert executadas == 3, "uma rodada só de consultas, veio {0}".format(
            executadas)


# ------------------------------------------------ B: saída limpa


async def test_saida_cancela_o_processo_filho():
    """N-05: /exit durante resposta deixava a CLI rodando."""
    app = make_claude_app(fake_runner())
    cancelados = []
    app.backend.cancel = lambda: cancelados.append(True) or True
    async with app.run_test(size=SIZE) as pilot:
        assert await esperar_descoberta(pilot)
    assert cancelados, "o backend deveria ser cancelado ao sair"


TESTS = [
    test_backend_que_levanta_nao_derruba_o_app,
    test_backend_que_devolve_none,
    test_descoberta_que_levanta_nao_derruba,
    test_remoto_que_levanta_nao_derruba,
    test_sonda_com_falha_nao_derruba,
    test_timeout_do_config_validado,
    test_timeout_chega_saudavel_ao_backend,
    test_perfil_que_exige_trilha_recusa_sem_log,
    test_avisa_quando_o_log_remoto_nao_pode_ser_gravado,
    test_workspace_nao_engole_mais_comandos,
    test_tres_confirmacoes_respondem_igual,
    test_esc_cancela_pendencia,
    test_esc_sem_pendencia_continua_interrompendo,
    test_provider_desconhecido_avisa_e_volta_para_claude,
    test_descobertas_nao_se_atropelam,
    test_saida_cancela_o_processo_filho,
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
