# -*- coding: utf-8 -*-
"""Testes do empacotamento: recursos congelados, ambiente, dispatcher e setup.

Rodar com:

    python -m nox.test_packaging      (com o ambiente do projeto ativo)

Nenhuma chamada ao Claude, nenhuma sonda, nenhuma conexão. O estado "congelado"
é simulado com `sys.frozen`/`sys._MEIPASS` em pasta temporária — o PyInstaller
não precisa estar instalado para estes testes rodarem.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import sys
import tempfile

from . import backends
from . import frozen
from . import remote_ssh
from . import setup_check
from . import __main__ as principal
from . import __version__
from .test_models import fake_runner

#: Raiz do projeto e caminhos de empacotamento, derivados deste arquivo.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(PROJECT_ROOT, "packaging", "nox.spec")
VERSION_CHECK = os.path.join(PROJECT_ROOT, "packaging", "version_check.py")


class Congelado(object):
    """Finge um bundle do PyInstaller, e desfaz tudo no fim."""

    def __init__(self, com_recurso=True, ambiente=None):
        self.raiz = tempfile.mkdtemp(prefix="nox-bundle-")
        if com_recurso:
            os.makedirs(os.path.join(self.raiz, "nox"))
            with open(os.path.join(self.raiz, "nox", "theme.tcss"), "w") as h:
                h.write("Screen { background: #0e0f12; }")
        self._frozen_antes = getattr(sys, "frozen", None)
        self._meipass_antes = getattr(sys, "_MEIPASS", None)
        self._environ_antes = dict(os.environ)
        if ambiente:
            os.environ.update(ambiente)
        sys.frozen = True
        sys._MEIPASS = self.raiz

    def close(self):
        try:
            if self._frozen_antes is None:
                if hasattr(sys, "frozen"):
                    del sys.frozen
            else:
                sys.frozen = self._frozen_antes
            if self._meipass_antes is None:
                if hasattr(sys, "_MEIPASS"):
                    del sys._MEIPASS
            else:
                sys._MEIPASS = self._meipass_antes
            os.environ.clear()
            os.environ.update(self._environ_antes)
        finally:
            shutil.rmtree(self.raiz, ignore_errors=True)


# ------------------------------------------------ recursos (theme.tcss)


async def test_resource_path_fora_do_bundle():
    """Execução normal: resolve ao lado do módulo, como sempre."""
    assert not frozen.is_frozen()
    caminho = frozen.resource_path("theme.tcss", principal.__file__)
    assert os.path.isfile(caminho), caminho
    assert os.path.basename(os.path.dirname(caminho)) == "nox"


async def test_resource_path_dentro_do_bundle():
    bundle = Congelado()
    try:
        assert frozen.is_frozen()
        caminho = frozen.resource_path("theme.tcss")
        assert caminho == os.path.join(bundle.raiz, "nox", "theme.tcss")
        assert os.path.isfile(caminho)
    finally:
        bundle.close()


async def test_resource_path_na_raiz_do_bundle():
    """Se o .spec puser o recurso na raiz, ainda assim achamos."""
    bundle = Congelado(com_recurso=False)
    try:
        with open(os.path.join(bundle.raiz, "theme.tcss"), "w") as handle:
            handle.write("Screen {}")
        assert frozen.resource_path("theme.tcss") == os.path.join(
            bundle.raiz, "theme.tcss")
    finally:
        bundle.close()


async def test_css_path_do_app_e_absoluto_e_existe():
    assert os.path.isabs(str(principal.NoxApp.CSS_PATH))
    assert os.path.isfile(str(principal.NoxApp.CSS_PATH))


# --------------------------------------- ambiente do processo-filho


async def test_clean_env_fora_do_bundle_nao_remove_nada():
    ambiente = frozen.clean_env({"LD_LIBRARY_PATH": "/qualquer", "X": "1"})
    assert ambiente["LD_LIBRARY_PATH"] == "/qualquer"
    assert ambiente["X"] == "1"


async def test_clean_env_restaura_valor_anterior():
    """PyInstaller guarda o valor original em `<nome>_ORIG`."""
    bundle = Congelado(ambiente={
        "LD_LIBRARY_PATH": "/bundle/libs",
        "LD_LIBRARY_PATH_ORIG": "/usr/lib",
        "DYLD_LIBRARY_PATH": "/bundle/dyld",
        "_MEIPASS2": "/tmp/x",
        "_PYI_APPLICATION_HOME_DIR": "/tmp/y",
        "PATH": "/usr/bin",
    })
    try:
        ambiente = frozen.clean_env()
        assert ambiente["LD_LIBRARY_PATH"] == "/usr/lib", ambiente.get("LD_LIBRARY_PATH")
        assert "DYLD_LIBRARY_PATH" not in ambiente, "sem _ORIG, some"
        assert "_MEIPASS2" not in ambiente
        assert "_PYI_APPLICATION_HOME_DIR" not in ambiente
        assert ambiente["PATH"] == "/usr/bin", "o resto do ambiente é preservado"
    finally:
        bundle.close()


async def test_backend_nao_vaza_variaveis_do_bundle():
    bundle = Congelado(ambiente={
        "LD_LIBRARY_PATH": "/bundle/libs",
        "_MEIPASS2": "/tmp/x",
        "ANTHROPIC_API_KEY": "fake-para-teste",
    })
    try:
        ambiente = backends.ClaudeCLIBackend().env()
        assert "LD_LIBRARY_PATH" not in ambiente
        assert "_MEIPASS2" not in ambiente
        assert "ANTHROPIC_API_KEY" not in ambiente, "a regra antiga continua"
    finally:
        bundle.close()


async def test_ssh_nao_vaza_variaveis_do_bundle():
    bundle = Congelado(ambiente={
        "LD_LIBRARY_PATH": "/bundle/libs",
        "LD_LIBRARY_PATH_ORIG": "/usr/lib",
        "ANTHROPIC_API_KEY": "fake-para-teste",
    })
    try:
        ambiente = remote_ssh._clean_env()
        assert ambiente["LD_LIBRARY_PATH"] == "/usr/lib"
        assert "ANTHROPIC_API_KEY" not in ambiente
    finally:
        bundle.close()


# ------------------------------------------------------- dispatcher


async def test_configure_console_devolve_restaurador():
    """O alinhamento de console é reversível — a sessão não fica alterada."""
    restaurar = frozen.configure_console()
    assert callable(restaurar), restaurar
    restaurar()  # não pode levantar, mesmo chamado duas vezes
    restaurar()


async def test_configure_console_fora_do_windows_e_inocuo():
    plataforma = sys.platform
    try:
        sys.platform = "linux"
        antes = (sys.stdout, sys.stderr)
        restaurar = frozen.configure_console()
        assert (sys.stdout, sys.stderr) == antes, "mexeu nos fluxos fora do Windows"
        restaurar()
    finally:
        sys.platform = plataforma


async def test_main_restaura_o_console_mesmo_com_erro():
    """Regressão: o código de saída não pode ser engolido pelo alinhamento."""
    chamadas = []
    original = frozen.configure_console
    frozen.configure_console = lambda: (lambda: chamadas.append("restaurou"))
    try:
        saida = io.StringIO()
        antes, sys.stdout = sys.stdout, saida
        try:
            codigo = principal.main(["xyz-inexistente"])
        finally:
            sys.stdout = antes
        assert codigo == 2, codigo          # o código do comando, intacto
        assert chamadas == ["restaurou"], chamadas
    finally:
        frozen.configure_console = original


async def test_parse_command():
    casos = [
        ([], "tui"),
        (["setup"], "setup"),
        (["--version"], "version"),
        (["-V"], "version"),
        (["version"], "version"),
        (["--help"], "help"),
        (["-h"], "help"),
        (["help"], "help"),
        (["conversar"], "desconhecido"),
        (["--sonda"], "desconhecido"),
    ]
    for argv, esperado in casos:
        comando, _resto = principal.parse_command(argv)
        assert comando == esperado, (argv, comando)


async def test_version_imprime_a_versao(capturado=None):
    saida = io.StringIO()
    antes, sys.stdout = sys.stdout, saida
    try:
        codigo = principal.main(["--version"])
    finally:
        sys.stdout = antes
    assert codigo == 0
    assert __version__ in saida.getvalue(), saida.getvalue()
    assert "Exponexa" in saida.getvalue()


async def test_help_e_comando_desconhecido():
    for argv, esperado in ((["--help"], 0), (["xyz"], 2)):
        saida = io.StringIO()
        antes, sys.stdout = sys.stdout, saida
        try:
            codigo = principal.main(argv)
        finally:
            sys.stdout = antes
        assert codigo == esperado, (argv, codigo)
        assert "nox setup" in saida.getvalue(), saida.getvalue()


async def test_dispatcher_chamado_uma_unica_vez():
    """Trava de regressão: um `main`, e uma chamada só no guarda de módulo.

    Duas chamadas em sequência (`main()` seguido de `sys.exit(main())`) fariam
    o dispatcher rodar duas vezes: `--version` imprimiria duas linhas e a TUI
    abriria depois de já ter aberto.
    """
    import ast

    fonte = io.open(principal.__file__, encoding="utf-8").read()
    arvore = ast.parse(fonte)

    definicoes = [n for n in arvore.body
                  if isinstance(n, ast.FunctionDef) and n.name == "main"]
    assert len(definicoes) == 1, [d.lineno for d in definicoes]

    guardas = [n for n in arvore.body
               if isinstance(n, ast.If) and "__name__" in ast.dump(n.test)]
    assert len(guardas) == 1, len(guardas)

    chamadas = [n for n in ast.walk(guardas[0])
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "main"]
    assert len(chamadas) == 1, [c.lineno for c in chamadas]

    # e o código de saída precisa ser propagado (sys.exit ou raise SystemExit)
    corpo = guardas[0].body
    assert len(corpo) == 1, len(corpo)
    texto = ast.dump(corpo[0])
    assert "'exit'" in texto or "SystemExit" in texto, texto


async def test_saida_de_version_tem_uma_linha_so():
    """Se o dispatcher rodasse duas vezes, apareceriam duas linhas."""
    saida = io.StringIO()
    antes, sys.stdout = sys.stdout, saida
    try:
        principal.main(["--version"])
    finally:
        sys.stdout = antes
    linhas = [l for l in saida.getvalue().splitlines() if l.strip()]
    assert len(linhas) == 1, linhas
    assert linhas[0].count("Exponexa") == 1, linhas[0]


async def test_main_sem_argumento_abriria_a_tui():
    """Não abrimos a TUI aqui: basta provar que o roteamento cai nela."""
    comando, resto = principal.parse_command([])
    assert (comando, resto) == ("tui", [])


# ----------------------------------------------------------- setup


async def test_setup_checks_sem_chamar_o_modelo():
    runner = fake_runner()
    checks = setup_check.run_checks(runner=runner)
    nomes = [c.nome for c in checks]
    assert nomes == ["sistema", "comando nox", "claude cli", "autenticação",
                     "config", "provedor", "perfil padrão"], nomes
    for chamada in runner.chamadas:
        assert "-p" not in chamada, chamada


async def test_setup_nao_exibe_credencial():
    autenticado = {"loggedIn": True, "authMethod": "claude.ai",
                   "subscriptionType": "max", "email": "pessoa@exemplo.com",
                   "orgId": "org-secreto", "accessToken": "tok-123"}
    check = setup_check.check_autenticacao(autenticado)
    assert check.estado == setup_check.OK
    texto = check.detalhe + check.dica
    for proibido in ("tok-123", "org-secreto", "pessoa@exemplo.com"):
        assert proibido not in texto, proibido
    assert "max" in texto and "claude.ai" in texto


async def test_setup_sem_autenticacao_orienta_o_usuario():
    check = setup_check.check_autenticacao({"loggedIn": False})
    assert check.estado == setup_check.FALTA
    assert "claude auth login" in check.dica
    check_vazio = setup_check.check_autenticacao({})
    assert "não copia" in check_vazio.dica or "não guarda" in check_vazio.dica


async def test_setup_nao_finge_lista_de_provedores():
    check = setup_check.check_provedor()
    funcionais = setup_check.provedores_funcionais()
    assert "echo" not in funcionais, "eco local não é provedor de verdade"
    if len(funcionais) == 1:
        assert "único funcional" in check.detalhe, check.detalhe
        assert "ponto" in check.detalhe or "extensão" in check.detalhe


async def test_setup_preserva_config():
    pasta = tempfile.mkdtemp(prefix="nox-setup-")
    try:
        caminho = os.path.join(pasta, "config.json")
        with open(caminho, "w", encoding="utf-8") as handle:
            json.dump({"provider": "claude", "timeout": 300}, handle)
        antes = open(caminho, encoding="utf-8").read()
        check = setup_check.check_config(caminho)
        assert check.estado == setup_check.OK
        assert "preservado" in check.detalhe
        assert open(caminho, encoding="utf-8").read() == antes, "não pode reescrever"
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


async def test_setup_render_e_codigo_de_saida():
    ok = [setup_check.Check("a", setup_check.OK, "tudo certo")]
    falta = ok + [setup_check.Check("b", setup_check.FALTA, "falta isto")]
    assert setup_check.exit_code(ok) == 0
    assert setup_check.exit_code(falta) == 1
    texto = setup_check.render(falta)
    assert "pendências" in texto and "b" in texto
    assert "nenhuma chamada ao modelo" in texto


# --------------------------------------------------- spec e versão


async def test_spec_existe_e_e_onedir():
    assert os.path.isfile(SPEC), SPEC
    conteudo = open(SPEC, encoding="utf-8").read()
    assert "COLLECT(" in conteudo, "onedir precisa do COLLECT"
    assert "exclude_binaries=True" in conteudo, "onefile não é o modo escolhido"
    assert "theme.tcss" in conteudo, "o tema precisa ir para o bundle"
    assert "console=True" in conteudo, "é uma TUI: precisa de console"
    assert "upx=False" in conteudo


async def test_version_check_concorda():
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "packaging"))
    try:
        import version_check
    finally:
        sys.path.pop(0)
    pacote, projeto = version_check.check()
    assert pacote == projeto == __version__, (pacote, projeto, __version__)
    assert version_check.normalize_tag("v" + __version__) == __version__


def _version_check():
    """Importa o guarda de versão sem deixar `packaging/` no sys.path."""
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "packaging"))
    try:
        import version_check
        return version_check
    finally:
        sys.path.pop(0)


class Ambiente_CI(object):
    """Finge as variáveis que o GitHub Actions injeta, e desfaz no fim."""

    VARS = ("GITHUB_REF_TYPE", "GITHUB_REF_NAME")

    def __init__(self, ref_type="", ref_name=""):
        self._antes = {nome: os.environ.get(nome) for nome in self.VARS}
        for nome, valor in (("GITHUB_REF_TYPE", ref_type),
                            ("GITHUB_REF_NAME", ref_name)):
            if valor:
                os.environ[nome] = valor
            else:
                os.environ.pop(nome, None)

    def close(self):
        for nome, valor in self._antes.items():
            if valor is None:
                os.environ.pop(nome, None)
            else:
                os.environ[nome] = valor


def _rodar_main(modulo, argv):
    """Chama `main` capturando a saída; devolve (código, texto)."""
    saida = io.StringIO()
    antes, sys.stdout = sys.stdout, saida
    try:
        codigo = modulo.main(argv)
    except SystemExit as parada:
        codigo = parada.code if isinstance(parada.code, int) else 1
        saida.write(str(parada.code or ""))
    finally:
        sys.stdout = antes
    return codigo, saida.getvalue()


async def test_versoes_em_paridade():
    """As duas fontes de versão têm de dizer o mesmo, sempre.

    `__init__.py` alimenta `nox --version` e `pyproject.toml` alimenta a
    distribuição. Divergentes, o instalador baixaria uma versão e o binário se
    apresentaria como outra — daí a checagem viver também aqui, e não só
    dentro do guarda que roda no CI.
    """
    modulo = _version_check()
    pacote = modulo.package_version()
    projeto = modulo.pyproject_version()
    assert pacote == projeto, (pacote, projeto)
    assert pacote == __version__, (pacote, __version__)
    assert re.match(r"^\d+\.\d+\.\d+$", pacote), pacote


async def test_version_check_em_run_de_branch():
    """Regressão: num run de branch, GITHUB_REF_NAME=main não é uma tag.

    A reserva por ambiente derrubava o build inteiro tentando validar "main"
    como semver. Sem argumento, aqui, a conferência é só pacote × pyproject.
    """
    modulo = _version_check()
    ambiente = Ambiente_CI(ref_type="branch", ref_name="main")
    try:
        codigo, texto = _rodar_main(modulo, [])
        assert codigo == 0, texto
        assert __version__ in texto, texto
        assert "tag" not in texto.lower(), texto
    finally:
        ambiente.close()


async def test_version_check_em_run_de_tag():
    modulo = _version_check()
    ambiente = Ambiente_CI(ref_type="tag", ref_name="v" + __version__)
    try:
        codigo, texto = _rodar_main(modulo, [])
        assert codigo == 0, texto
        assert "tag v" + __version__ in texto, texto
    finally:
        ambiente.close()


async def test_version_check_em_run_de_tag_divergente():
    modulo = _version_check()
    ambiente = Ambiente_CI(ref_type="tag", ref_name="v9.9.9")
    try:
        codigo, texto = _rodar_main(modulo, [])
        assert codigo != 0, "tag divergente tem de falhar"
        assert "divergência" in texto or "diverg" in texto, texto
    finally:
        ambiente.close()


async def test_version_check_com_tag_explicita():
    """Argumento vence o ambiente, em qualquer tipo de run."""
    modulo = _version_check()
    ambiente = Ambiente_CI(ref_type="branch", ref_name="main")
    try:
        codigo, texto = _rodar_main(modulo, ["v" + __version__])
        assert codigo == 0, texto
        assert "tag v" + __version__ in texto, texto
        codigo, texto = _rodar_main(modulo, ["nao-e-semver"])
        assert codigo != 0, "tag inválida tem de falhar"
        assert "semver" in texto, texto
    finally:
        ambiente.close()


async def test_version_check_recusa_divergencia():
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "packaging"))
    try:
        import version_check
    finally:
        sys.path.pop(0)
    for tag_ruim in ("v9.9.9", "0.0.1", "release-1"):
        try:
            version_check.check(tag_ruim)
        except SystemExit:
            continue
        raise AssertionError("aceitou tag divergente: " + tag_ruim)


TESTS = [
    test_resource_path_fora_do_bundle,
    test_resource_path_dentro_do_bundle,
    test_resource_path_na_raiz_do_bundle,
    test_css_path_do_app_e_absoluto_e_existe,
    test_clean_env_fora_do_bundle_nao_remove_nada,
    test_clean_env_restaura_valor_anterior,
    test_backend_nao_vaza_variaveis_do_bundle,
    test_ssh_nao_vaza_variaveis_do_bundle,
    test_parse_command,
    test_configure_console_devolve_restaurador,
    test_configure_console_fora_do_windows_e_inocuo,
    test_main_restaura_o_console_mesmo_com_erro,
    test_version_imprime_a_versao,
    test_help_e_comando_desconhecido,
    test_dispatcher_chamado_uma_unica_vez,
    test_saida_de_version_tem_uma_linha_so,
    test_main_sem_argumento_abriria_a_tui,
    test_setup_checks_sem_chamar_o_modelo,
    test_setup_nao_exibe_credencial,
    test_setup_sem_autenticacao_orienta_o_usuario,
    test_setup_nao_finge_lista_de_provedores,
    test_setup_preserva_config,
    test_setup_render_e_codigo_de_saida,
    test_spec_existe_e_e_onedir,
    test_version_check_concorda,
    test_versoes_em_paridade,
    test_version_check_em_run_de_branch,
    test_version_check_em_run_de_tag,
    test_version_check_em_run_de_tag_divergente,
    test_version_check_com_tag_explicita,
    test_version_check_recusa_divergencia,
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
