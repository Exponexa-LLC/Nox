# -*- coding: utf-8 -*-
"""Testes do acesso remoto por SSH — sem rede, sem servidor, sem Claude.

Rodar com:

    python -m nox.test_remote      (com o ambiente do projeto ativo)

Nenhuma conexão é aberta: o transporte é injetado por `runner`, e um teste
garante que o executor real (`remote_ssh.run_process`) nunca é chamado aqui.
Nenhuma chave é lida — os testes usam arquivos temporários no lugar delas.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile

from . import remote_hosts
from . import remote_log
from . import remote_ops
from . import remote_ssh
from .test_autocomplete import StubConfig
from .test_models import ClaudeLikeStub, esperar_descoberta, fake_runner
from .__main__ import NoxApp

SIZE = (80, 24)


class Ambiente(object):
    """Pasta temporária com chave falsa, known_hosts e hosts.json."""

    def __init__(self, hosts=None):
        self.raiz = tempfile.mkdtemp(prefix="nox-remote-")
        self.identity = os.path.join(self.raiz, "codeplay_vps")
        self.identity2 = os.path.join(self.raiz, "vellar_vps")
        for caminho in (self.identity, self.identity2):
            with open(caminho, "w", encoding="utf-8") as handle:
                handle.write("chave-falsa-de-teste")
        self.known_hosts = os.path.join(self.raiz, "known_hosts")
        with open(self.known_hosts, "w", encoding="utf-8") as handle:
            handle.write("exemplo.invalido ssh-ed25519 AAAA\n")
        self.hosts_path = os.path.join(self.raiz, "hosts.json")
        self.config_path = os.path.join(self.raiz, "config.json")
        self.log_path = os.path.join(self.raiz, "remote.log")
        self.write_hosts(hosts if hosts is not None else self.default_hosts())

    def default_hosts(self):
        return [
            {"alias": "codeplay", "user": "usuario", "hostname": "10.0.0.1",
             "port": 22, "identity": self.identity, "descricao": "VPS codeplay"},
            {"alias": "vellar", "user": "usuario", "hostname": "vps.exemplo.test",
             "port": 2222, "identity": self.identity2, "descricao": "VPS vellar"},
        ]

    def write_hosts(self, hosts):
        with open(self.hosts_path, "w", encoding="utf-8") as handle:
            json.dump({"hosts": hosts}, handle)

    def host(self, alias="codeplay"):
        carregados, problemas = remote_hosts.load_hosts(self.hosts_path)
        assert not problemas, problemas
        return remote_hosts.find(carregados, alias)

    def close(self):
        shutil.rmtree(self.raiz, ignore_errors=True)


def ssh_runner(exit_code=0, stdout="ok", stderr=""):
    """Transporte falso: registra o argv e devolve o que mandarmos."""
    chamadas = []

    def run(comando, timeout=0):
        chamadas.append(list(comando))
        return exit_code, stdout, stderr

    run.chamadas = chamadas
    return run


# ----------------------------------------------------- validação de hosts


async def test_host_valido():
    ambiente = Ambiente()
    try:
        host = ambiente.host()
        assert host.destination() == "usuario@10.0.0.1", host.destination()
        assert host.port == 22
        assert host.identity == os.path.abspath(ambiente.identity)
    finally:
        ambiente.close()


async def test_alias_invalido():
    ambiente = Ambiente()
    try:
        for ruim in ("Codeplay", "code play", "code/play", "", "code;play"):
            try:
                remote_hosts.valid_alias(ruim)
            except remote_hosts.HostError:
                continue
            raise AssertionError("alias aceito indevidamente: " + repr(ruim))
    finally:
        ambiente.close()


async def test_user_invalido():
    for ruim in ("", "com espaco", "a@b", "a\\b", "a/b", "a\nb", "a\x01b"):
        try:
            remote_hosts.valid_user(ruim)
        except remote_hosts.HostError:
            continue
        raise AssertionError("user aceito indevidamente: " + repr(ruim))
    assert remote_hosts.valid_user("usuario") == "usuario"


async def test_hostname_valido_e_invalido():
    for bom in ("10.0.0.1", "192.168.1.10", "vps.exemplo.test", "servidor1"):
        assert remote_hosts.valid_hostname(bom) == bom
    for ruim in ("", "-oProxyCommand=x", "host com espaco", "a@b", "a\nb",
                 "256.1.1.1.1", "host_com_underscore!"):
        try:
            remote_hosts.valid_hostname(ruim)
        except remote_hosts.HostError:
            continue
        raise AssertionError("hostname aceito indevidamente: " + repr(ruim))


async def test_port_invalido():
    for ruim in (0, 65536, -1, "22", 22.5, True):
        try:
            remote_hosts.valid_port(ruim)
        except remote_hosts.HostError:
            continue
        raise AssertionError("port aceito indevidamente: " + repr(ruim))
    assert remote_hosts.valid_port(22) == 22


async def test_identity_precisa_existir_e_ser_arquivo():
    ambiente = Ambiente()
    try:
        assert remote_hosts.valid_identity(ambiente.identity)
        for ruim in (os.path.join(ambiente.raiz, "nao-existe"), ambiente.raiz, ""):
            try:
                remote_hosts.valid_identity(ruim)
            except remote_hosts.HostError:
                continue
            raise AssertionError("identity aceita indevidamente: " + repr(ruim))
        # conteúdo de chave colado no lugar do caminho é recusado
        try:
            remote_hosts.valid_identity("-----BEGIN OPENSSH PRIVATE KEY-----")
        except remote_hosts.HostError:
            pass
        else:
            raise AssertionError("aceitou conteúdo de chave como identity")
    finally:
        ambiente.close()


async def test_campos_de_segredo_recusados():
    ambiente = Ambiente()
    try:
        for campo in ("password", "passphrase", "token", "secret", "private_key"):
            ambiente.write_hosts([{
                "alias": "x", "user": "u", "hostname": "10.0.0.1", "port": 22,
                "identity": ambiente.identity, campo: "valor",
            }])
            hosts, problemas = remote_hosts.load_hosts(ambiente.hosts_path)
            assert hosts == [], campo
            assert any("proibido" in p for p in problemas), (campo, problemas)
    finally:
        ambiente.close()


async def test_known_hosts_obrigatorio():
    ambiente = Ambiente()
    try:
        try:
            remote_hosts.require_known_hosts(
                os.path.join(ambiente.raiz, "nao-existe"))
        except remote_hosts.HostError as erro:
            assert "known_hosts" in str(erro)
        else:
            raise AssertionError("aceitou ausência de known_hosts")
        assert remote_hosts.require_known_hosts(ambiente.known_hosts)
    finally:
        ambiente.close()


# ------------------------------------------------------------ allowlist


async def test_operacoes_sao_somente_leitura():
    esperadas = ["conexao", "hostname", "sistema", "uptime", "disco",
                 "processos", "servico", "containers", "log"]
    assert [op.name for op in remote_ops.OPERATIONS] == esperadas
    for operacao in remote_ops.OPERATIONS:
        argv = operacao.tokens
        assert argv[0] in remote_ops.READ_ONLY_COMMANDS
        for token in argv:
            assert token.lower() not in remote_ops.FORBIDDEN_TOKENS, token


async def test_conexao_e_so_um_comando_fixo():
    """`conexao` prova autenticação, não abre shell."""
    argv = remote_ops.build_argv("conexao")
    assert argv == ["true"], argv


async def test_operacao_desconhecida():
    for ruim in ("shell", "bash", "rm", "", "conexao; ls"):
        try:
            remote_ops.build_argv(ruim)
        except remote_ops.OpError:
            continue
        raise AssertionError("operação aceita indevidamente: " + repr(ruim))


async def test_parametro_recusa_metacaracteres():
    ruins = ["nginx; rm -rf /", "nginx|cat", "nginx && id", "$(id)", "`id`",
             "../etc/passwd", "a b", "nginx>arquivo", "nginx\nid"]
    for ruim in ruins:
        try:
            remote_ops.build_argv("servico", {"unidade": ruim})
        except remote_ops.OpError:
            continue
        raise AssertionError("parâmetro aceito indevidamente: " + repr(ruim))
    assert remote_ops.build_argv("servico", {"unidade": "nginx"}) == [
        "systemctl", "status", "--no-pager", "nginx"]


async def test_log_valida_linhas():
    assert remote_ops.build_argv("log", {"unidade": "nginx"}) == [
        "journalctl", "--no-pager", "-u", "nginx", "-n", "100"]
    assert remote_ops.build_argv("log", {"unidade": "nginx", "linhas": 5})[-1] == "5"
    for ruim in (0, 501, -1, "muitas", "10; id"):
        try:
            remote_ops.build_argv("log", {"unidade": "nginx", "linhas": ruim})
        except remote_ops.OpError:
            continue
        raise AssertionError("linhas aceitas indevidamente: " + repr(ruim))


async def test_parametro_obrigatorio():
    for operacao in ("servico", "log"):
        try:
            remote_ops.build_argv(operacao)
        except remote_ops.OpError:
            continue
        raise AssertionError(operacao + " rodou sem parâmetro")


# --------------------------------------------------------------- argv ssh


async def test_argv_ssh_exato():
    ambiente = Ambiente()
    try:
        host = ambiente.host()
        argv = remote_ops.build_argv("disco")
        comando = remote_ssh.build_command(host, argv, ambiente.known_hosts)
        assert comando == [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile={0}".format(ambiente.known_hosts),
            "-o", "ConnectTimeout=10",
            "-o", "LogLevel=ERROR",
            "-n",
            "-i", os.path.abspath(ambiente.identity),
            "-p", "22",
            "usuario@10.0.0.1",
            "df", "-h",
        ], comando
    finally:
        ambiente.close()


async def test_argv_sem_separador_duplo_hifen():
    """`--` iria para o shell do servidor: o OpenSSH não o consome."""
    ambiente = Ambiente()
    try:
        comando = remote_ssh.build_command(
            ambiente.host(), remote_ops.build_argv("uptime"), ambiente.known_hosts)
        destino = comando.index("usuario@10.0.0.1")
        assert "--" not in comando, comando
        assert comando[destino + 1:] == ["uptime"], comando[destino:]
    finally:
        ambiente.close()


async def test_argv_e_lista_nunca_string():
    ambiente = Ambiente()
    try:
        comando = remote_ssh.build_command(
            ambiente.host(), remote_ops.build_argv("hostname"), ambiente.known_hosts)
        assert isinstance(comando, list)
        assert all(isinstance(token, str) for token in comando)
        # a porta do segundo host entra como token próprio, não concatenada
        segundo = remote_ssh.build_command(
            ambiente.host("vellar"), ["uptime"], ambiente.known_hosts)
        assert "-p" in segundo and segundo[segundo.index("-p") + 1] == "2222"
        assert "usuario@vps.exemplo.test" in segundo
    finally:
        ambiente.close()


async def test_execucao_usa_o_runner_injetado():
    ambiente = Ambiente()
    try:
        runner = ssh_runner(stdout="Filesystem  Size\n/dev/sda1  50G")
        resultado = remote_ssh.run(
            ambiente.host(), remote_ops.build_argv("disco"),
            runner=runner, known_hosts=ambiente.known_hosts)
        assert resultado.ok and resultado.exit_code == 0
        assert "dev/sda1" in resultado.stdout
        assert len(runner.chamadas) == 1
        assert runner.chamadas[0][0] == "ssh"
    finally:
        ambiente.close()


async def test_erros_amigaveis():
    casos = [
        ("Permission denied (publickey).", "passphrase"),
        ("Host key verification failed.", "known_hosts"),
        ("ssh: Could not resolve hostname x", "resolver"),
        ("connect to host x port 22: Connection refused", "recusada"),
        ("connection timed out", "esgotado"),
    ]
    for stderr, esperado in casos:
        mensagem = remote_ssh.friendly_error(255, stderr)
        assert esperado in mensagem, (stderr, mensagem)
        assert stderr not in mensagem, "stderr cru não vai para a tela"


async def test_redacao_do_argv():
    ambiente = Ambiente()
    try:
        comando = remote_ssh.build_command(
            ambiente.host(), ["uptime"], ambiente.known_hosts)
        redigido = remote_ssh.redact(comando)
        assert "<identity>" in redigido
        assert os.path.abspath(ambiente.identity) not in redigido
    finally:
        ambiente.close()


async def test_executor_real_nao_e_chamado():
    """Trava de segurança: nenhum teste pode abrir conexão de verdade."""
    original = remote_ssh.run_process

    def proibido(*args, **kwargs):
        raise AssertionError("tentou executar ssh de verdade")

    remote_ssh.run_process = proibido
    ambiente = Ambiente()
    try:
        resultado = remote_ssh.run(
            ambiente.host(), ["uptime"], runner=ssh_runner(),
            known_hosts=ambiente.known_hosts)
        assert resultado.ok
    finally:
        remote_ssh.run_process = original
        ambiente.close()


# ------------------------------------------------------------------ log


async def test_log_so_metadados():
    ambiente = Ambiente()
    try:
        registro = remote_log.entry(
            "codeplay", "log", {"unidade": "nginx"},
            remote_ssh.redact(["ssh", "-i", ambiente.identity, "usuario@10.0.0.1"]),
            0, 1.234, 4096)
        assert remote_log.append(ambiente.log_path, registro)
        bruto = open(ambiente.log_path, encoding="utf-8").read()
        assert "<identity>" in bruto
        assert ambiente.identity not in bruto
        for proibido in ("BEGIN", "PRIVATE", "password", "passphrase"):
            assert proibido not in bruto, proibido
        gravado = json.loads(bruto.strip())
        assert gravado["host"] == "codeplay" and gravado["exit"] == 0
        assert gravado["bytes_recebidos"] == 4096
        assert "stdout" not in gravado and "saida" not in gravado
    finally:
        ambiente.close()


async def test_log_redige_segredo_que_apareca():
    texto = remote_log.redact_text("password: hunter2")
    assert "hunter2" not in texto and "<redigido>" in texto
    texto = remote_log.redact_text("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert "BEGIN" not in texto


# ------------------------------------------------------------- na TUI


def make_app(ambiente, runner=None):
    settings = StubConfig()
    settings.path = ambiente.config_path
    settings.writable = True
    # /remote só existe no perfil que declara a capacidade remote.read
    settings.profile = "diagnostico-remoto"
    app = NoxApp(backend=ClaudeLikeStub(), settings=settings)
    app._discovery_runner = fake_runner()
    app._remote_runner = runner if runner is not None else ssh_runner()
    return app


async def send(pilot, text):
    pilot.app.query_one("#prompt").value = text
    await pilot.press("enter")
    await pilot.pause()


async def test_remote_abre_seletor_de_hosts():
    ambiente = Ambiente()
    try:
        async with make_app(ambiente).run_test(size=SIZE) as pilot:
            app = pilot.app
            assert await esperar_descoberta(pilot)
            await send(pilot, "/remote")
            assert app._picker_kind == "remote_host", app._picker_kind
            aliases = [linha[0] for linha in app._picker_rows]
            assert aliases == ["codeplay", "vellar"], aliases
            texto = app.query_one("#picker").content.plain
            assert "somente leitura" in texto, texto
            await pilot.press("escape")
    finally:
        ambiente.close()


async def test_remote_sem_hosts_mostra_modelo():
    ambiente = Ambiente()
    try:
        os.remove(ambiente.hosts_path)
        async with make_app(ambiente).run_test(size=SIZE) as pilot:
            app = pilot.app
            assert await esperar_descoberta(pilot)
            await send(pilot, "/remote")
            texto = "\n".join(app._plain[-2:])
            assert "hosts.json" in texto and "codeplay_vps" in texto
            assert app._picker_kind is None
    finally:
        ambiente.close()


async def test_confirmacao_s_executa_e_n_cancela():
    ambiente = Ambiente()
    runner = ssh_runner(stdout="Linux servidor 6.1.0")
    try:
        async with make_app(ambiente, runner).run_test(size=SIZE) as pilot:
            app = pilot.app
            assert await esperar_descoberta(pilot)
            # n cancela e nada é executado
            await send(pilot, "/remote codeplay sistema")
            assert app._pending_remote is not None
            assert "confirmar?" in app._plain[-1]
            await send(pilot, "n")
            assert app._pending_remote is None
            assert "cancelada" in app._plain[-1]
            assert not runner.chamadas, runner.chamadas
            # s executa
            await send(pilot, "/remote codeplay sistema")
            await send(pilot, "s")
            for _ in range(60):
                if any("6.1.0" in linha for linha in app._plain):
                    break
                await pilot.pause()
            assert len(runner.chamadas) == 1, runner.chamadas
            assert runner.chamadas[0][-2:] == ["uname", "-a"], runner.chamadas[0]
    finally:
        ambiente.close()


async def test_resposta_invalida_continua_aguardando():
    ambiente = Ambiente()
    runner = ssh_runner()
    try:
        async with make_app(ambiente, runner).run_test(size=SIZE) as pilot:
            app = pilot.app
            assert await esperar_descoberta(pilot)
            enviados = []
            app.backend.send = lambda texto, model="": enviados.append(texto)
            await send(pilot, "/remote codeplay uptime")
            await send(pilot, "talvez")
            assert app._pending_remote is not None, "a confirmação continua de pé"
            assert "responda s" in app._plain[-1], app._plain[-1]
            assert not runner.chamadas and not enviados
            await send(pilot, "n")
            assert app._pending_remote is None
            assert not enviados, "nada pode chegar ao backend"
    finally:
        ambiente.close()


async def test_operacao_invalida_nao_confirma():
    ambiente = Ambiente()
    runner = ssh_runner()
    try:
        async with make_app(ambiente, runner).run_test(size=SIZE) as pilot:
            app = pilot.app
            assert await esperar_descoberta(pilot)
            await send(pilot, "/remote codeplay servico nginx;id")
            assert app._pending_remote is None, "não pode pedir confirmação"
            assert not runner.chamadas
            await send(pilot, "/remote codeplay rm")
            assert app._pending_remote is None
            await send(pilot, "/remote inexistente uptime")
            assert "desconhecido" in app._plain[-1]
            assert not runner.chamadas
    finally:
        ambiente.close()


async def test_falha_remota_mostra_mensagem_amigavel():
    ambiente = Ambiente()
    runner = ssh_runner(exit_code=255, stdout="",
                        stderr="Permission denied (publickey).")
    try:
        async with make_app(ambiente, runner).run_test(size=SIZE) as pilot:
            app = pilot.app
            assert await esperar_descoberta(pilot)
            await send(pilot, "/remote codeplay conexao")
            await send(pilot, "s")
            for _ in range(60):
                if any("passphrase" in linha for linha in app._plain):
                    break
                await pilot.pause()
            assert any("ssh-agent" in linha for linha in app._plain), app._plain[-1]
            assert not any("publickey" in linha for linha in app._plain)
    finally:
        ambiente.close()


async def test_log_gravado_apos_execucao():
    ambiente = Ambiente()
    try:
        async with make_app(ambiente).run_test(size=SIZE) as pilot:
            app = pilot.app
            assert await esperar_descoberta(pilot)
            await send(pilot, "/remote codeplay uptime")
            await send(pilot, "s")
            caminho = os.path.join(ambiente.raiz, remote_log.LOG_FILE)
            for _ in range(60):
                if os.path.exists(caminho):
                    break
                await pilot.pause()
            registros = remote_log.read_last(caminho)
            assert registros, "o log deveria ter uma entrada"
            assert registros[-1]["operacao"] == "uptime"
            assert "<identity>" in registros[-1]["argv"]
    finally:
        ambiente.close()


async def test_remote_no_autocomplete_e_layout():
    from . import commands
    nomes = [nome for nome, _d in commands.suggest("/rem")]
    assert nomes == ["/remote"], nomes
    ambiente = Ambiente()
    try:
        async with make_app(ambiente).run_test(size=SIZE) as pilot:
            app = pilot.app
            assert await esperar_descoberta(pilot)
            await send(pilot, "/remote")
            painel = app.query_one("#picker")
            for linha in painel.content.plain.split(chr(10)):
                assert len(linha) <= 74, (len(linha), linha)
            assert painel.region.right <= 80
            await pilot.press("escape")
    finally:
        ambiente.close()


async def test_modelo_continua_sem_ferramentas():
    """O Claude não ganhou nenhuma ferramenta com isto."""
    from . import backends
    args = backends.ClaudeCLIBackend()._command("oi", "sonnet")
    assert args[args.index("--tools") + 1] == "", args
    assert not any("remote" in str(token) for token in args), args


# ------------------------------------------- argumentos extras (N-14)


async def test_extras_recusados_antes_da_confirmacao():
    """Token a mais não pode ser ignorado em silêncio nem chegar ao runner."""
    ambiente = Ambiente()
    runner = ssh_runner()
    try:
        async with make_app(ambiente, runner).run_test(size=SIZE) as pilot:
            app = pilot.app
            assert await esperar_descoberta(pilot)
            casos = [
                "/remote codeplay uptime lixo",
                "/remote codeplay disco a b",
                "/remote codeplay conexao agora",
                "/remote codeplay servico nginx 50 extra",
                "/remote codeplay log nginx 50 sobra",
            ]
            for comando in casos:
                await send(pilot, comando)
                assert app._pending_remote is None, comando
                assert "argumento a mais" in app._plain[-1], (comando,
                                                              app._plain[-1])
                assert "uso:" in app._plain[-1], app._plain[-1]
            assert not runner.chamadas, runner.chamadas
    finally:
        ambiente.close()


async def test_parametro_ausente_mostra_uso():
    ambiente = Ambiente()
    runner = ssh_runner()
    try:
        async with make_app(ambiente, runner).run_test(size=SIZE) as pilot:
            app = pilot.app
            assert await esperar_descoberta(pilot)
            for comando in ("/remote codeplay servico", "/remote codeplay log"):
                await send(pilot, comando)
                assert app._pending_remote is None, comando
                assert "uso:" in app._plain[-1], app._plain[-1]
            assert not runner.chamadas
    finally:
        ambiente.close()


async def test_parametros_validos_continuam_funcionando():
    ambiente = Ambiente()
    runner = ssh_runner(stdout="active (running)")
    try:
        async with make_app(ambiente, runner).run_test(size=SIZE) as pilot:
            app = pilot.app
            assert await esperar_descoberta(pilot)
            casos = [
                ("/remote codeplay uptime", ["uptime"]),
                ("/remote codeplay servico nginx",
                 ["systemctl", "status", "--no-pager", "nginx"]),
                ("/remote codeplay log nginx",
                 ["journalctl", "--no-pager", "-u", "nginx", "-n", "100"]),
                ("/remote codeplay log nginx 25",
                 ["journalctl", "--no-pager", "-u", "nginx", "-n", "25"]),
            ]
            for comando, esperado in casos:
                await send(pilot, comando)
                assert app._pending_remote is not None, comando
                await send(pilot, "s")
                for _ in range(60):
                    if len(runner.chamadas) and \
                            runner.chamadas[-1][-len(esperado):] == esperado:
                        break
                    await pilot.pause()
                assert runner.chamadas[-1][-len(esperado):] == esperado, (
                    comando, runner.chamadas[-1])
    finally:
        ambiente.close()


TESTS = [
    test_host_valido,
    test_alias_invalido,
    test_user_invalido,
    test_hostname_valido_e_invalido,
    test_port_invalido,
    test_identity_precisa_existir_e_ser_arquivo,
    test_campos_de_segredo_recusados,
    test_known_hosts_obrigatorio,
    test_operacoes_sao_somente_leitura,
    test_conexao_e_so_um_comando_fixo,
    test_operacao_desconhecida,
    test_parametro_recusa_metacaracteres,
    test_log_valida_linhas,
    test_parametro_obrigatorio,
    test_argv_ssh_exato,
    test_argv_sem_separador_duplo_hifen,
    test_argv_e_lista_nunca_string,
    test_execucao_usa_o_runner_injetado,
    test_erros_amigaveis,
    test_redacao_do_argv,
    test_executor_real_nao_e_chamado,
    test_log_so_metadados,
    test_log_redige_segredo_que_apareca,
    test_remote_abre_seletor_de_hosts,
    test_remote_sem_hosts_mostra_modelo,
    test_confirmacao_s_executa_e_n_cancela,
    test_resposta_invalida_continua_aguardando,
    test_operacao_invalida_nao_confirma,
    test_falha_remota_mostra_mensagem_amigavel,
    test_log_gravado_apos_execucao,
    test_remote_no_autocomplete_e_layout,
    test_modelo_continua_sem_ferramentas,
    test_extras_recusados_antes_da_confirmacao,
    test_parametro_ausente_mostra_uso,
    test_parametros_validos_continuam_funcionando,
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
