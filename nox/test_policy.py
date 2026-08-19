# -*- coding: utf-8 -*-
"""Testes da política declarativa: HARD_CAPS, capacidades e perfis.

Rodar com:

    python -m nox.test_policy      (com o ambiente do projeto ativo)

Nenhuma chamada ao Claude, nenhuma sonda, nenhuma conexão. Os testes de TUI
usam stubs; os de política são puros.
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
from . import policy as policy_mod
from . import profiles
from . import remote_ops
from .test_autocomplete import StubConfig
from .test_models import ClaudeLikeStub, esperar_descoberta, fake_runner
from .test_models import make_app as make_model_app
from .__main__ import NoxApp

SIZE = (80, 24)


def make_app(profile="conversa", runner=None):
    settings = StubConfig()
    settings.profile = profile
    app = NoxApp(backend=ClaudeLikeStub(), settings=settings)
    app._discovery_runner = runner if runner is not None else fake_runner()
    return app


async def send(pilot, text):
    pilot.app.query_one("#prompt").value = text
    await pilot.press("enter")
    await pilot.pause()


# --------------------------------------------------------- perfis embutidos


async def test_tres_perfis_embutidos():
    assert profiles.names() == ["conversa", "desenvolvimento",
                                "diagnostico-remoto"], profiles.names()
    assert profiles.DEFAULT_PROFILE == "conversa"
    assert profiles.default().name == "conversa"


async def test_capacidades_por_perfil():
    """A tabela combinada: quem pode o quê."""
    esperado = {
        "conversa": {"chat", "model.switch", "provider.switch",
                     "workspace.switch", "models.discover"},
        "desenvolvimento": {"chat", "model.switch", "provider.switch",
                            "workspace.switch", "models.discover",
                            "models.probe"},
        "diagnostico-remoto": {"chat", "model.switch", "provider.switch",
                               "workspace.switch", "models.discover",
                               "remote.read"},
    }
    for nome, capacidades in esperado.items():
        perfil = profiles.find(nome)
        assert set(perfil.capabilities) == capacidades, (nome,
                                                         perfil.capabilities)


async def test_conversa_e_sem_friccao():
    """O perfil padrão não atrapalha o uso normal."""
    perfil = profiles.find("conversa")
    for capacidade in ("chat", "model.switch", "provider.switch",
                       "workspace.switch", "models.discover"):
        assert perfil.allows(capacidade), capacidade
    assert not perfil.allows("remote.read")
    assert not perfil.allows("models.probe")


async def test_desenvolvimento_e_a_maior_flexibilidade_local():
    conversa = profiles.find("conversa")
    desenvolvimento = profiles.find("desenvolvimento")
    assert set(conversa.capabilities) < set(desenvolvimento.capabilities)
    # e a única diferença real hoje é a sonda, porque não há ferramentas locais
    diferenca = set(desenvolvimento.capabilities) - set(conversa.capabilities)
    assert diferenca == {"models.probe"}, diferenca
    assert not desenvolvimento.allows("local.tools")


async def test_diagnostico_remoto_tem_as_nove_leituras():
    perfil = profiles.find("diagnostico-remoto")
    assert perfil.allows("remote.read")
    assert len(perfil.remote_operations) == 9, perfil.remote_operations
    assert set(perfil.remote_operations) == set(
        op.name for op in remote_ops.OPERATIONS)
    assert perfil.require_remote_log is True


# ------------------------------------------------------------- HARD_CAPS


async def test_nenhum_perfil_liga_ferramenta_inexistente():
    for perfil in profiles.BUILTIN:
        assert not perfil.allows("local.tools"), perfil.name


async def test_json_nao_consegue_ligar_local_tools():
    base = profiles.find("desenvolvimento")
    perfil, avisos = policy_mod.from_dict(base, {
        "nome": "hostil", "capacidades": ["chat", "local.tools"]})
    assert not perfil.allows("local.tools"), perfil.capabilities
    assert any("não implementada" in aviso for aviso in avisos), avisos


async def test_json_nao_consegue_inventar_capacidade():
    base = profiles.find("conversa")
    perfil, avisos = policy_mod.from_dict(base, {
        "nome": "hostil",
        "capacidades": ["chat", "remote.write", "shell", "sudo"]})
    assert set(perfil.capabilities) == {"chat"}, perfil.capabilities
    assert len([a for a in avisos if "desconhecida" in a]) == 3, avisos


async def test_json_nao_sai_da_allowlist_remota():
    base = profiles.find("diagnostico-remoto")
    perfil, avisos = policy_mod.from_dict(base, {
        "nome": "hostil",
        "remote_operations": ["disco", "rm", "shell", "escrever"]})
    assert perfil.remote_operations == ("disco",), perfil.remote_operations
    assert any("allowlist" in aviso for aviso in avisos), avisos


async def test_operacoes_sempre_subconjunto_da_allowlist():
    permitidas = set(op.name for op in remote_ops.OPERATIONS)
    for perfil in profiles.BUILTIN:
        assert set(perfil.remote_operations) <= permitidas, perfil.name


async def test_confirmacao_de_remoto_e_sonda_nao_e_configuravel():
    """Não existe campo para desligar: é constante, não preferência."""
    assert policy_mod.CAPS.CONFIRM_REMOTE is True
    assert policy_mod.CAPS.CONFIRM_PROBE is True
    assert policy_mod.CAPS.REMOTE_WRITE is False
    base = profiles.find("diagnostico-remoto")
    perfil, avisos = policy_mod.from_dict(base, {
        "confirm_remote": False, "remote_write": True, "tools_enabled": True})
    assert not hasattr(perfil, "confirm_remote")
    assert not hasattr(perfil, "tools_enabled")
    assert perfil.allows_operation("disco"), "o perfil segue funcionando"
    assert len(avisos) >= 3, avisos  # três campos desconhecidos


async def test_hard_caps_sao_imutaveis():
    try:
        policy_mod.CAPS.CONFIRM_REMOTE = False
    except AttributeError:
        pass
    else:
        raise AssertionError("consegui alterar um HARD_CAP")
    assert policy_mod.CAPS.CONFIRM_REMOTE is True


async def test_perfil_recusa_campo_de_segredo():
    base = profiles.find("conversa")
    for campo in ("password", "token", "api_key", "passphrase"):
        perfil, avisos = policy_mod.from_dict(base, {campo: "x"})
        assert perfil is base, campo
        assert any("segredo" in aviso for aviso in avisos), (campo, avisos)


async def test_max_output_lines_entra_na_faixa():
    base = profiles.find("conversa")
    for valor, esperado in ((1, 5), (99999, 500), (60, 60)):
        perfil, _avisos = policy_mod.from_dict(base, {"max_output_lines": valor})
        assert perfil.max_output_lines == esperado, (valor,
                                                     perfil.max_output_lines)


async def test_chat_nunca_e_desligado():
    base = profiles.find("conversa")
    perfil, _avisos = policy_mod.from_dict(base, {"capacidades": []})
    assert perfil.allows("chat"), perfil.capabilities


# --------------------------------------------------- perfil personalizado


async def test_perfil_personalizado_pode_ampliar_dentro_do_teto():
    """Flexibilidade: um perfil seu pode LIGAR remote.read partindo de conversa."""
    base = profiles.find("conversa")
    perfil, avisos = policy_mod.from_dict(base, {
        "nome": "leitura-vps",
        "capacidades": ["chat", "model.switch", "remote.read"],
        "remote_operations": ["conexao", "disco", "log"],
        "require_remote_log": True,
        "max_output_lines": 60,
    })
    assert perfil.allows("remote.read"), perfil.capabilities
    assert perfil.remote_operations == ("conexao", "disco", "log")
    assert perfil.allows_operation("disco")
    assert not perfil.allows_operation("processos"), "só o que foi declarado"
    assert perfil.require_remote_log is True
    assert perfil.max_output_lines == 60
    assert not avisos, avisos


async def test_profiles_json_carregado_e_validado():
    pasta = tempfile.mkdtemp(prefix="nox-perfis-")
    try:
        caminho = os.path.join(pasta, profiles.PROFILES_FILE)
        with open(caminho, "w", encoding="utf-8") as handle:
            json.dump({"profiles": [
                {"nome": "leitura-vps", "base": "conversa",
                 "capacidades": ["chat", "remote.read"],
                 "remote_operations": ["disco"]},
                {"nome": "hostil", "base": "conversa",
                 "capacidades": ["local.tools", "shell"]},
            ]}, handle)
        perfis, avisos = profiles.load_user_profiles(caminho)
        assert [p.name for p in perfis] == ["leitura-vps", "hostil"]
        assert perfis[0].allows("remote.read")
        assert perfis[0].remote_operations == ("disco",)
        assert set(perfis[1].capabilities) == {"chat"}, perfis[1].capabilities
        assert avisos, "o perfil hostil precisa gerar aviso"
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


async def test_profiles_json_ausente_ou_corrompido():
    pasta = tempfile.mkdtemp(prefix="nox-perfis-")
    try:
        assert profiles.load_user_profiles(
            os.path.join(pasta, "nao-existe.json")) == ([], [])
        caminho = os.path.join(pasta, profiles.PROFILES_FILE)
        with open(caminho, "w", encoding="utf-8") as handle:
            handle.write("{isto nao e json")
        perfis, avisos = profiles.load_user_profiles(caminho)
        assert perfis == [] and avisos, avisos
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


async def test_base_desconhecida_cai_no_seguro():
    pasta = tempfile.mkdtemp(prefix="nox-perfis-")
    try:
        caminho = os.path.join(pasta, profiles.PROFILES_FILE)
        with open(caminho, "w", encoding="utf-8") as handle:
            json.dump({"profiles": [
                {"nome": "x", "base": "inexistente"}]}, handle)
        perfis, avisos = profiles.load_user_profiles(caminho)
        assert perfis[0].allows("chat")
        assert not perfis[0].allows("remote.read")
        assert any("base desconhecida" in aviso for aviso in avisos), avisos
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


async def test_resolve_perfil_invalido_cai_em_conversa():
    perfil, avisos = profiles.resolve("nao-existe")
    assert perfil.name == "conversa"
    assert any("desconhecido" in aviso for aviso in avisos), avisos


# ------------------------------------------------------------- na TUI


async def test_remote_bloqueado_em_conversa():
    async with make_app("conversa").run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await send(pilot, "/remote")
        assert app._picker_kind is None, "não pode nem abrir o seletor"
        assert "não está disponível no perfil conversa" in app._plain[-1], \
            app._plain[-1]
        assert "/profile diagnostico-remoto" in app._plain[-1], app._plain[-1]


async def test_sonda_bloqueada_em_conversa():
    async with make_app("conversa").run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await send(pilot, "/refresh-models --sonda")
        assert app._pending_probe is False, "nem chega a perguntar"
        assert "não está disponível" in app._plain[-1], app._plain[-1]


async def test_sonda_em_desenvolvimento_ainda_pede_confirmacao():
    """Capacidade liberada não dispensa confirmação — isso é HARD_CAP."""
    async with make_app("desenvolvimento").run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await send(pilot, "/refresh-models --sonda")
        assert app._pending_probe is True
        assert "confirmar?" in app._plain[-1], app._plain[-1]
        await send(pilot, "n")
        assert app._pending_probe is False


async def test_troca_de_perfil_pelo_comando_e_pelo_picker():
    async with make_app("conversa").run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await send(pilot, "/profile diagnostico-remoto")
        assert app.policy.name == "diagnostico-remoto", app.policy.name
        assert app.config.profile == "diagnostico-remoto"
        await send(pilot, "/profile")
        assert app._picker_kind == "profile"
        nomes = [linha[0] for linha in app._picker_rows]
        assert nomes[:3] == profiles.names(), nomes
        await pilot.press("escape")
        await send(pilot, "/profile inexistente")
        assert "desconhecido" in app._plain[-1]
        assert app.policy.name == "diagnostico-remoto", "não muda em erro"


async def test_troca_de_perfil_nao_reinicia_sessao():
    async with make_app("conversa").run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        sessao = app.backend.session_label()
        modelo = app.model
        await send(pilot, "/profile desenvolvimento")
        assert app.backend.session_label() == sessao, "a sessão continua"
        assert app.model == modelo, "o modelo continua"


async def test_pendencia_tem_precedencia_sobre_o_comando_de_perfil():
    """Pergunta aberta é modal: `/profile` não passa por cima dela.

    É o mesmo princípio da correção N-07 — enquanto há confirmação de pé,
    nenhum comando é executado às escondidas. Esc desiste, e aí sim troca.
    """
    async with make_app("desenvolvimento").run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await send(pilot, "/refresh-models --sonda")
        assert app._pending_probe is True
        await send(pilot, "/profile conversa")
        assert app._pending_probe is True, "a pergunta continua de pé"
        assert app.policy.name == "desenvolvimento", "o perfil não trocou"
        assert "responda s" in app._plain[-1], app._plain[-1]
        await pilot.press("escape")
        await pilot.pause()
        assert app._pending_probe is False
        await send(pilot, "/profile conversa")
        assert app.policy.name == "conversa", app.policy.name


async def test_troca_de_perfil_cancela_pendencia_defensivamente():
    """Se uma troca acontecer com pendência ativa, ela cai — nunca sobrevive.

    Hoje a TUI não deixa esse caminho ser alcançado pelo teclado (o teste
    acima prova), mas a guarda existe para que nenhuma via futura permita
    confirmar sob um perfil e executar sob outro.
    """
    async with make_app("desenvolvimento").run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await send(pilot, "/refresh-models --sonda")
        assert app._pending_probe is True
        app._profile_command("conversa")   # chamada direta, como um caminho novo faria
        await pilot.pause()
        assert app._pending_probe is False, "pendência tem de cair na troca"
        assert app.policy.name == "conversa"
        assert any("pendente cancelada" in linha for linha in app._plain[-3:]), \
            app._plain[-3:]


async def test_perfil_no_cabecalho_e_no_status():
    async with make_app("conversa").run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        cabecalho = app.query_one("#backend", Static).content.plain
        assert "perfil conversa" in cabecalho, cabecalho
        await send(pilot, "/status")
        texto = app._plain[-1]
        assert "perfil" in texto and "conversa" in texto, texto
        assert "sem ferramentas" in texto, texto


async def test_largura_80_colunas_com_perfil():
    async with make_app("diagnostico-remoto").run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        for selector in ("#header", "#backend", "#meta", "#workspace", "#footer"):
            regiao = app.query_one(selector).region
            assert regiao.right <= 80, (selector, regiao)
        cabecalho = app.query_one("#backend", Static).content.plain
        assert len(cabecalho) <= 74, (len(cabecalho), cabecalho)
        await send(pilot, "/profile")
        painel = app.query_one("#picker", Static)
        for linha in painel.content.plain.split(chr(10)):
            assert len(linha) <= 74, (len(linha), linha)
        await pilot.press("escape")


async def test_profile_no_autocomplete():
    from . import commands
    nomes = [nome for nome, _d in commands.suggest("/prof")]
    assert nomes == ["/profile"], nomes


async def test_tools_identico_sob_todos_os_perfis():
    """Nenhum perfil altera o argv do backend: --tools "" é intocável."""
    esperado = backends.ClaudeCLIBackend()._command("oi", "sonnet")[-2:]
    assert esperado == ["--tools", ""], esperado
    for nome in profiles.names():
        app = make_app(nome)
        async with app.run_test(size=SIZE) as pilot:
            assert await esperar_descoberta(pilot)
            args = backends.ClaudeCLIBackend()._command("oi", "sonnet")
            assert args[args.index("--tools") + 1] == "", (nome, args)


async def test_config_sem_perfil_cai_em_conversa():
    settings = StubConfig()
    settings.profile = ""
    app = NoxApp(backend=ClaudeLikeStub(), settings=settings)
    app._discovery_runner = fake_runner()
    async with app.run_test(size=SIZE) as pilot:
        assert await esperar_descoberta(pilot)
        assert app.policy.name == "conversa", app.policy.name


async def test_config_com_perfil_invalido_avisa():
    settings = StubConfig()
    settings.profile = "perfil-que-nao-existe"
    app = NoxApp(backend=ClaudeLikeStub(), settings=settings)
    app._discovery_runner = fake_runner()
    async with app.run_test(size=SIZE) as pilot:
        assert await esperar_descoberta(pilot)
        assert app.policy.name == "conversa", app.policy.name
        assert any("perfil desconhecido" in linha for linha in app._plain), \
            app._plain


TESTS = [
    test_tres_perfis_embutidos,
    test_capacidades_por_perfil,
    test_conversa_e_sem_friccao,
    test_desenvolvimento_e_a_maior_flexibilidade_local,
    test_diagnostico_remoto_tem_as_nove_leituras,
    test_nenhum_perfil_liga_ferramenta_inexistente,
    test_json_nao_consegue_ligar_local_tools,
    test_json_nao_consegue_inventar_capacidade,
    test_json_nao_sai_da_allowlist_remota,
    test_operacoes_sempre_subconjunto_da_allowlist,
    test_confirmacao_de_remoto_e_sonda_nao_e_configuravel,
    test_hard_caps_sao_imutaveis,
    test_perfil_recusa_campo_de_segredo,
    test_max_output_lines_entra_na_faixa,
    test_chat_nunca_e_desligado,
    test_perfil_personalizado_pode_ampliar_dentro_do_teto,
    test_profiles_json_carregado_e_validado,
    test_profiles_json_ausente_ou_corrompido,
    test_base_desconhecida_cai_no_seguro,
    test_resolve_perfil_invalido_cai_em_conversa,
    test_remote_bloqueado_em_conversa,
    test_sonda_bloqueada_em_conversa,
    test_sonda_em_desenvolvimento_ainda_pede_confirmacao,
    test_troca_de_perfil_pelo_comando_e_pelo_picker,
    test_troca_de_perfil_nao_reinicia_sessao,
    test_pendencia_tem_precedencia_sobre_o_comando_de_perfil,
    test_troca_de_perfil_cancela_pendencia_defensivamente,
    test_perfil_no_cabecalho_e_no_status,
    test_largura_80_colunas_com_perfil,
    test_profile_no_autocomplete,
    test_tools_identico_sob_todos_os_perfis,
    test_config_sem_perfil_cai_em_conversa,
    test_config_com_perfil_invalido_avisa,
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
