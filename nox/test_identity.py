# -*- coding: utf-8 -*-
"""Testes da identidade Exponexa/nox, da migração de config e do launcher.

Rodar com:

    python -m nox.test_identity      (com o ambiente do projeto ativo)

Nenhuma chamada ao Claude, nenhuma sonda, nenhuma escrita no `~` real: a
migração é exercida em pastas temporárias, e o launcher é apenas lido — nunca
executado.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

from textual.widgets import Static

import nox

from . import config as config_mod
from . import wolf
from .test_autocomplete import make_app, type_text
from .test_models import esperar_descoberta, fake_runner
from .test_models import make_app as make_claude_app

SIZE = (80, 24)

#: Raiz do projeto e launcher, deduzidos deste arquivo.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(PROJECT_ROOT, "nox.cmd")


# ------------------------------------------------------------- identidade


async def test_nomes_da_identidade():
    """Público é Exponexa; técnico é nox. Nenhum ocupa o lugar do outro."""
    assert nox.APP_NAME == "nox", nox.APP_NAME
    assert nox.APP_TITLE == "Exponexa", nox.APP_TITLE
    assert nox.APP_COMMAND == "nox", nox.APP_COMMAND


async def test_pacote_importa_pelo_nome_novo():
    assert nox.__name__ == "nox", nox.__name__
    caminho = os.path.dirname(os.path.abspath(nox.__file__))
    assert os.path.basename(caminho) == "nox", caminho
    # os módulos internos acompanham o pacote
    from . import backends, commands, model_discovery, models_catalog, pickers
    for modulo in (backends, commands, model_discovery, models_catalog, pickers):
        assert modulo.__name__.startswith("nox."), modulo.__name__


async def test_modulo_principal_e_executavel():
    """`python -m nox` tem de ter um `main` para rodar."""
    from . import __main__ as principal
    assert callable(principal.main), principal.main
    assert principal.NoxApp.TITLE == "Exponexa", principal.NoxApp.TITLE


async def test_config_aponta_para_nox():
    assert config_mod.CONFIG_DIR.endswith(".nox"), config_mod.CONFIG_DIR
    assert config_mod.CONFIG_PATH.endswith(os.path.join(".nox", "config.json"))
    assert config_mod.LEGACY_DIR.endswith(".delet_user"), config_mod.LEGACY_DIR


# --------------------------------------------------------------- migração


async def test_migracao_copia_config_e_cache():
    velho = tempfile.mkdtemp(prefix="nox-legacy-")
    novo = tempfile.mkdtemp(prefix="nox-novo-")
    try:
        shutil.rmtree(novo)  # o destino ainda não existe, como na vida real
        dados = {"provider": "claude", "models": {"claude": "opus"},
                 "timeout": 120, "workspace": ""}
        with open(os.path.join(velho, "config.json"), "w", encoding="utf-8") as h:
            json.dump(dados, h)
        with open(os.path.join(velho, "models_cache.json"), "w", encoding="utf-8") as h:
            json.dump({"rows": [], "source": "catálogo"}, h)

        avisos = config_mod.migrate_legacy(novo, velho)
        assert len(avisos) == 2, avisos
        assert all("mantida" in aviso for aviso in avisos), avisos
        for nome in ("config.json", "models_cache.json"):
            assert os.path.exists(os.path.join(novo, nome)), nome
            # a original continua lá: migração é cópia, não mudança de lugar
            assert os.path.exists(os.path.join(velho, nome)), nome

        migrado = json.load(open(os.path.join(novo, "config.json"), encoding="utf-8"))
        assert migrado == dados, migrado
    finally:
        shutil.rmtree(velho, ignore_errors=True)
        shutil.rmtree(novo, ignore_errors=True)


async def test_migracao_nao_sobrescreve_destino():
    velho = tempfile.mkdtemp(prefix="nox-legacy-")
    novo = tempfile.mkdtemp(prefix="nox-novo-")
    try:
        with open(os.path.join(velho, "config.json"), "w", encoding="utf-8") as h:
            json.dump({"timeout": 1}, h)
        atual = {"timeout": 999}
        with open(os.path.join(novo, "config.json"), "w", encoding="utf-8") as h:
            json.dump(atual, h)
        avisos = config_mod.migrate_legacy(novo, velho)
        assert avisos == [], avisos
        assert json.load(open(os.path.join(novo, "config.json"), encoding="utf-8")) == atual
    finally:
        shutil.rmtree(velho, ignore_errors=True)
        shutil.rmtree(novo, ignore_errors=True)


async def test_migracao_ignora_json_corrompido():
    velho = tempfile.mkdtemp(prefix="nox-legacy-")
    novo = tempfile.mkdtemp(prefix="nox-novo-")
    try:
        with open(os.path.join(velho, "config.json"), "w", encoding="utf-8") as h:
            h.write("{isto nao e json")
        avisos = config_mod.migrate_legacy(novo, velho)
        assert not os.path.exists(os.path.join(novo, "config.json"))
        assert any("não consegui migrar" in aviso for aviso in avisos), avisos
        assert os.path.exists(os.path.join(velho, "config.json")), "não pode apagar"
    finally:
        shutil.rmtree(velho, ignore_errors=True)
        shutil.rmtree(novo, ignore_errors=True)


async def test_migracao_sem_pasta_antiga_e_silenciosa():
    novo = tempfile.mkdtemp(prefix="nox-novo-")
    try:
        avisos = config_mod.migrate_legacy(
            novo, os.path.join(novo, "nao-existe"))
        assert avisos == [], avisos
    finally:
        shutil.rmtree(novo, ignore_errors=True)


async def test_config_migrada_sem_credenciais():
    velho = tempfile.mkdtemp(prefix="nox-legacy-")
    novo = tempfile.mkdtemp(prefix="nox-novo-")
    try:
        with open(os.path.join(velho, "config.json"), "w", encoding="utf-8") as h:
            json.dump({"provider": "claude", "api_key": "segredo-nao-migrar"}, h)
        config_mod.migrate_legacy(novo, velho)
        alvo = os.path.join(novo, "config.json")
        preferencias = config_mod.Config(path=alvo, legacy_dir=velho)
        assert "api_key" not in preferencias.data, preferencias.data
        gravado = open(alvo, encoding="utf-8").read()
        assert "segredo-nao-migrar" not in gravado, gravado
    finally:
        shutil.rmtree(velho, ignore_errors=True)
        shutil.rmtree(novo, ignore_errors=True)


# --------------------------------------------------------------- launcher


async def test_launcher_existe_dentro_do_projeto():
    assert os.path.exists(LAUNCHER), LAUNCHER
    assert os.path.dirname(LAUNCHER) == PROJECT_ROOT, LAUNCHER


async def test_launcher_monta_o_comando_certo():
    """O launcher é lido, nunca executado."""
    texto = open(LAUNCHER, encoding="utf-8", errors="replace").read()
    assert "-m nox %*" in texto, "precisa repassar os argumentos"
    assert ".venv\\Scripts\\python.exe" in texto, texto
    assert "PYTHONPATH" in texto, "precisa achar o pacote de qualquer pasta"


async def test_launcher_nao_toca_no_claude_nem_em_credencial():
    """Nada de login, chave ou invocação do `claude` — só o Python do .venv."""
    texto = open(LAUNCHER, encoding="utf-8", errors="replace").read().lower()
    for proibido in ("anthropic_api_key", "setup-token", "auth login",
                     "shell=true", "claude.exe", "claude.cmd"):
        assert proibido not in texto, proibido
    # nenhuma linha executável chama o claude. O nome da pasta do projeto pode
    # conter "claude", então ele é neutralizado antes da checagem — e o nome
    # vem do próprio caminho, não de uma máquina específica.
    pasta = os.path.basename(PROJECT_ROOT).lower()
    for linha in texto.splitlines():
        limpo = linha.strip()
        if not limpo or limpo.startswith(("rem", "@echo", "::")):
            continue
        assert not limpo.startswith("claude"), limpo
        assert " claude " not in limpo.replace(pasta, "projeto"), limpo


# ------------------------------------------------------- Pilot 80x24


async def test_cabecalho_mostra_exponexa():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        marca = app.query_one("#brand", Static).content.plain
        assert "Exponexa" in marca, marca
        assert app.title == "Exponexa", app.title
        assert "nox" not in marca.lower(), "o nome técnico não vai para a marca"


async def test_tela_sem_nome_antigo():
    async with make_app().run_test(size=SIZE) as pilot:
        app = pilot.app
        await type_text(pilot, "/help")
        await pilot.press("enter")
        await pilot.pause()
        for selector in ("#brand", "#backend", "#meta", "#workspace", "#footer"):
            texto = app.query_one(selector, Static).content.plain
            assert "delet" not in texto.lower(), (selector, texto)
        for linha in app._plain:
            assert "delet user" not in linha.lower(), linha


async def test_mascote_inalterado():
    """O invasor tem de sair idêntico ao desenho de sempre — nada redesenhado."""
    async with make_app().run_test(size=SIZE) as pilot:
        desenhado = pilot.app.query_one("#wolf", Static).content
        esperado = wolf.wolf_art()
        assert desenhado.plain == esperado.plain, desenhado.plain
        assert len(desenhado.plain.split(chr(10))) == wolf.WOLF_HEIGHT
        assert wolf.WOLF_WIDTH == 12 and wolf.WOLF_HEIGHT == 5
        # a grade e a paleta do mascote continuam as mesmas
        assert len(wolf.WOLF_PIXELS) == 10
        assert all(len(linha) == 12 for linha in wolf.WOLF_PIXELS)
        assert wolf.WOLF_PALETTE["P"] == "#a970ff"
        assert set(wolf.WOLF_PALETTE) == set("PMDWKC")


async def test_modelo_workspace_e_sessao_preservados():
    async with make_claude_app(fake_runner()).run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        assert app.model in app.backend.models(), app.model
        assert app.workspace and os.path.isdir(app.workspace), app.workspace
        assert app.backend.session_label(), "a sessão continua identificada"
        app.query_one("#prompt").value = "/status"
        await pilot.press("enter")
        await pilot.pause()
        texto = app._plain[-1]
        for rotulo in ("provedor", "backend", "modelo", "sessão",
                       "workspace", "config"):
            assert rotulo in texto, (rotulo, texto)
        # o /status mostra o config em uso — aqui o de teste, em memória
        assert app.config.path in texto, (app.config.path, texto)
        # e o caminho de produção é o novo
        assert config_mod.CONFIG_PATH.endswith(
            os.path.join(".nox", "config.json")), config_mod.CONFIG_PATH


async def test_menus_e_autocomplete_vivos():
    async with make_claude_app(fake_runner()).run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        await type_text(pilot, "/mod")
        assert not app.query_one("#suggestions", Static).has_class("hidden")
        await pilot.press("escape")
        app.query_one("#prompt").value = "/model"
        await pilot.press("enter")
        await pilot.pause()
        assert not app.query_one("#picker", Static).has_class("hidden")
        assert "Opus 5" in app.query_one("#picker", Static).content.plain
        await pilot.press("escape")
        app.query_one("#prompt").value = "/provider"
        await pilot.press("enter")
        await pilot.pause()
        assert not app.query_one("#picker", Static).has_class("hidden")
        await pilot.press("escape")


async def test_nada_passa_de_80_colunas():
    async with make_claude_app(fake_runner()).run_test(size=SIZE) as pilot:
        app = pilot.app
        assert await esperar_descoberta(pilot)
        app.query_one("#prompt").value = "/model"
        await pilot.press("enter")
        await pilot.pause()
        for selector in ("#header", "#wolf", "#transcript", "#promptbar",
                         "#footer", "#picker"):
            regiao = app.query_one(selector).region
            assert regiao.right <= 80, (selector, regiao)
        for linha in app.query_one("#picker", Static).content.plain.split(chr(10)):
            assert len(linha) <= 74, (len(linha), linha)
        await pilot.press("escape")
        await type_text(pilot, "/")
        for linha in app.query_one("#suggestions", Static).content.plain.split(chr(10)):
            assert len(linha) <= 74, (len(linha), linha)


TESTS = [
    test_nomes_da_identidade,
    test_pacote_importa_pelo_nome_novo,
    test_modulo_principal_e_executavel,
    test_config_aponta_para_nox,
    test_migracao_copia_config_e_cache,
    test_migracao_nao_sobrescreve_destino,
    test_migracao_ignora_json_corrompido,
    test_migracao_sem_pasta_antiga_e_silenciosa,
    test_config_migrada_sem_credenciais,
    test_launcher_existe_dentro_do_projeto,
    test_launcher_monta_o_comando_certo,
    test_launcher_nao_toca_no_claude_nem_em_credencial,
    test_cabecalho_mostra_exponexa,
    test_tela_sem_nome_antigo,
    test_mascote_inalterado,
    test_modelo_workspace_e_sessao_preservados,
    test_menus_e_autocomplete_vivos,
    test_nada_passa_de_80_colunas,
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
