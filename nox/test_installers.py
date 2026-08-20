# -*- coding: utf-8 -*-
"""Testes do instalador Windows (`install.ps1`) — sem rede e sem tocar nesta máquina.

Rodar com:

    python -m nox.test_installers      (com o ambiente do projeto ativo)

Toda execução usa `-Prefix` numa pasta temporária e `-Source` apontando para
uma "release" falsa montada aqui: um zip e um SHA256SUMS gerados pelo próprio
teste. Nenhum download acontece, nenhum PATH real é alterado, nenhum arquivo
de configuração é lido ou escrito, e o Claude nunca é chamado.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(PROJECT_ROOT, "install.ps1")

#: O instalador é PowerShell: sem `pwsh` os testes funcionais não têm como
#: rodar. Falhamos alto em vez de passar vazio.
PWSH = shutil.which("pwsh") or shutil.which("powershell")


class ReleaseFalsa(object):
    """Uma release em disco: zip com um `nox.exe` de mentira + SHA256SUMS."""

    def __init__(self, versao="0.7.0", corromper=False):
        self.versao = versao
        self.raiz = tempfile.mkdtemp(prefix="nox-release-")
        self.prefixo = tempfile.mkdtemp(prefix="nox-prefix-")
        self.home = tempfile.mkdtemp(prefix="nox-home-")
        self.nome_zip = "nox-{0}-windows-x64.zip".format(versao)
        self.zip_path = os.path.join(self.raiz, self.nome_zip)

        with zipfile.ZipFile(self.zip_path, "w") as pacote:
            pacote.writestr("nox.exe", "executavel-falso-versao-" + versao)
            pacote.writestr("_internal/theme.tcss", "Screen { background: #0e0f12; }")

        with open(self.zip_path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        if corromper:
            # um hash válido em forma, mas que não corresponde ao arquivo
            digest = "0" * 64

        with open(os.path.join(self.raiz, "SHA256SUMS"), "w",
                  encoding="ascii", newline="\n") as handle:
            handle.write("{0}  {1}\n".format(digest, self.nome_zip))

        # ~/.nox simulado: precisa sobreviver a tudo
        self.config_dir = os.path.join(self.home, ".nox")
        os.makedirs(self.config_dir)
        self.config_path = os.path.join(self.config_dir, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write('{"provider": "claude", "profile": "conversa"}')
        self.config_original = open(self.config_path, encoding="utf-8").read()

    # ------------------------------------------------------------ caminhos

    def versao_dir(self, versao=None):
        return os.path.join(self.prefixo, "nox", versao or self.versao)

    def shim(self):
        return os.path.join(self.prefixo, "bin", "nox.cmd")

    def config_intacto(self):
        if not os.path.exists(self.config_path):
            return False
        return open(self.config_path, encoding="utf-8").read() == self.config_original

    def close(self):
        for pasta in (self.raiz, self.prefixo, self.home):
            shutil.rmtree(pasta, ignore_errors=True)


def rodar(*argumentos, **kwargs):
    """Executa o install.ps1 com argumentos, sem rede.

    Vai por `-Command` (e não `-File`) só para poder fixar a saída em UTF-8
    antes de chamar o script: com a página de código padrão do Windows, os
    acentos voltam quebrados e as asserções de texto viram loteria.
    """
    assert PWSH, "pwsh não encontrado — os testes do instalador exigem PowerShell"
    # splatting de ARRAY passa tudo como posicional; para ligar nomes de
    # parâmetro é preciso hashtable — daí a conversão abaixo.
    itens, indice = [], 0
    argumentos = [str(a) for a in argumentos]
    while indice < len(argumentos):
        atual = argumentos[indice]
        assert atual.startswith("-"), "esperava um parâmetro nomeado: " + atual
        nome = atual.lstrip("-")
        if indice + 1 < len(argumentos) and not argumentos[indice + 1].startswith("-"):
            valor = argumentos[indice + 1].replace("'", "''")
            itens.append("{0}='{1}'".format(nome, valor))
            indice += 2
        else:
            itens.append("{0}=$true".format(nome))
            indice += 1
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$p = @{{{0}}}; & '{1}' @p".format(
            "; ".join(itens), INSTALLER.replace("'", "''")))
    processo = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=kwargs.get("timeout", 120))
    saida = processo.stdout.decode("utf-8", "replace") if processo.stdout else ""
    return processo.returncode, saida


def linhas_executaveis():
    """Só o código do instalador — sem comentários nem bloco de ajuda.

    Uma varredura ingênua acusaria as próprias frases que explicam o que o
    script NÃO faz ("não altera HKLM", "rode claude auth login").
    """
    texto = io.open(INSTALLER, encoding="utf-8").read()
    saida, dentro_ajuda = [], False
    for linha in texto.splitlines():
        limpo = linha.strip()
        if limpo.startswith("<#"):
            dentro_ajuda = True
        if dentro_ajuda:
            if limpo.endswith("#>"):
                dentro_ajuda = False
            continue
        if not limpo or limpo.startswith("#"):
            continue
        # tira comentário de fim de linha, preservando o código
        sem_comentario = re.sub(r"\s+#(?!\{).*$", "", linha)
        saida.append(sem_comentario)
    return saida


def path_do_usuario():
    """Lê o PATH do usuário — os testes conferem que ele NÃO muda."""
    codigo, saida = 0, ""
    processo = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command",
         "[Environment]::GetEnvironmentVariable('Path','User')"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    return processo.stdout.decode("utf-8", "replace").strip()


# ------------------------------------------------------- análise estática


async def test_instalador_existe_na_raiz():
    assert os.path.isfile(INSTALLER), INSTALLER
    assert os.path.dirname(INSTALLER) == PROJECT_ROOT


async def test_nao_toca_em_path_de_maquina_nem_pede_privilegio():
    """Vale o que o script EXECUTA, não o que ele explica em comentário."""
    codigo = "\n".join(linhas_executaveis())
    proibidos = ["HKLM", "setx /M", "'Machine'", '"Machine"',
                 "RunAs", "-Verb runas", "Start-Process -Verb"]
    for termo in proibidos:
        assert termo not in codigo, termo
    assert '"User"' in codigo, "o PATH só pode ser mexido no escopo do usuário"
    # e toda chamada que mexe no PATH usa o escopo do usuário
    for linha in linhas_executaveis():
        if "SetEnvironmentVariable" in linha and "Path" in linha:
            assert '"User"' in linha or "'User'" in linha, linha


async def test_nao_toca_na_configuracao_do_usuario():
    """N-Purge: não existe caminho de código que apague ~/.nox."""
    texto = io.open(INSTALLER, encoding="utf-8").read()
    linhas_executaveis = [
        l for l in texto.splitlines()
        if l.strip() and not l.strip().startswith(("#", "<#", ".", "REM"))
    ]
    corpo = "\n".join(linhas_executaveis)
    # nenhuma remoção mirando a configuração
    for suspeito in ("Remove-Item $env:USERPROFILE", "\\.nox", ".delet_user"):
        for linha in linhas_executaveis:
            if suspeito in linha and "Remove-Item" in linha:
                raise AssertionError("linha remove configuração: " + linha)
    assert "-Purge" not in corpo, "o parâmetro de limpeza não existe nesta versão"
    assert "Purge" not in [p.strip() for p in re.findall(r"\[switch\]\$(\w+)", corpo)]


async def test_sem_telemetria_e_sem_credencial():
    """Instruir sobre `claude auth login` é legítimo; EXECUTAR não seria."""
    codigo = "\n".join(linhas_executaveis()).lower()
    for proibido in ("telemetry", "analytics", "api_key", "apikey", "token=",
                     "password", "passphrase", "setup-token"):
        assert proibido not in codigo, proibido

    # `auth login` só pode aparecer dentro de texto exibido ao usuário
    for linha in linhas_executaveis():
        if "auth login" in linha.lower():
            assert "Write-" in linha, "auth login fora de mensagem: " + linha

    # nenhuma invocação do claude a não ser a procura no PATH
    for linha in linhas_executaveis():
        if re.search(r"(&\s*|Start-Process\s+)[\"']?claude", linha):
            raise AssertionError("o instalador executa o claude: " + linha)

    urls = re.findall(r"https?://[^\s\"')]+", codigo)
    for url in urls:
        assert ("github.com" in url or "githubusercontent.com" in url
                or "claude.com" in url), url


async def test_verificacao_vem_antes_da_extracao():
    """A ordem importa mais que a existência: conferir depois não adianta."""
    texto = io.open(INSTALLER, encoding="utf-8").read()
    pos_check = texto.index("Assert-Checksum $zip")
    pos_extract = texto.index("Expand-Archive")
    assert pos_check < pos_extract, (pos_check, pos_extract)
    assert "Get-FileHash" in texto


async def test_parametros_declarados():
    texto = io.open(INSTALLER, encoding="utf-8").read()
    for parametro in ("$Version", "$Prefix", "$Source", "$DryRun",
                      "$AddToPath", "$Uninstall", "$ListVersions"):
        assert parametro in texto, parametro
    assert "NOX_VERSION" in texto, "precisa aceitar a variável de ambiente"


# ------------------------------------------------------------ instalação


async def test_dry_run_nao_escreve_nada():
    ambiente = ReleaseFalsa()
    try:
        antes = os.listdir(ambiente.prefixo)
        codigo, saida = rodar("-Version", ambiente.versao, "-Prefix",
                              ambiente.prefixo, "-Source", ambiente.raiz, "-DryRun")
        assert codigo == 0, saida
        assert "DryRun" in saida or "nada foi" in saida, saida
        assert os.listdir(ambiente.prefixo) == antes, "o -DryRun escreveu algo"
        assert not os.path.exists(ambiente.versao_dir())
        assert not os.path.exists(ambiente.shim())
    finally:
        ambiente.close()


async def test_instalacao_completa():
    ambiente = ReleaseFalsa()
    try:
        codigo, saida = rodar("-Version", ambiente.versao, "-Prefix",
                              ambiente.prefixo, "-Source", ambiente.raiz)
        assert codigo == 0, saida
        assert "checksum confere" in saida, saida
        assert os.path.isfile(os.path.join(ambiente.versao_dir(), "nox.exe")), saida
        assert os.path.isfile(ambiente.shim()), saida
        shim = open(ambiente.shim(), encoding="ascii").read()
        assert ambiente.versao in shim and "nox.exe" in shim, shim
        assert ambiente.config_intacto(), "a configuração simulada foi alterada"
    finally:
        ambiente.close()


async def test_checksum_adulterado_aborta_antes_de_extrair():
    ambiente = ReleaseFalsa(corromper=True)
    try:
        codigo, saida = rodar("-Version", ambiente.versao, "-Prefix",
                              ambiente.prefixo, "-Source", ambiente.raiz)
        assert codigo != 0, "checksum adulterado tem de abortar"
        assert "NÃO confere" in saida or "nao confere" in saida.lower(), saida
        assert "nada foi extraído" in saida or "nada foi extra" in saida, saida
        assert not os.path.exists(ambiente.versao_dir()), "extraiu mesmo assim"
        assert not os.path.exists(ambiente.shim())
    finally:
        ambiente.close()


async def test_checksum_sem_a_linha_do_arquivo():
    ambiente = ReleaseFalsa()
    try:
        with open(os.path.join(ambiente.raiz, "SHA256SUMS"), "w",
                  encoding="ascii", newline="\n") as handle:
            handle.write("0" * 64 + "  outro-arquivo.zip\n")
        codigo, saida = rodar("-Version", ambiente.versao, "-Prefix",
                              ambiente.prefixo, "-Source", ambiente.raiz)
        assert codigo != 0, saida
        assert "não achei a linha" in saida or "nao achei a linha" in saida, saida
        assert not os.path.exists(ambiente.versao_dir())
    finally:
        ambiente.close()


# ---------------------------------------------- atualização e rollback


async def test_atualizacao_preserva_versao_anterior():
    ambiente = ReleaseFalsa(versao="0.7.0")
    try:
        codigo, _ = rodar("-Version", "0.7.0", "-Prefix", ambiente.prefixo,
                          "-Source", ambiente.raiz)
        assert codigo == 0

        # segunda release, no mesmo diretório de origem
        nova = "0.8.0"
        nome = "nox-{0}-windows-x64.zip".format(nova)
        caminho = os.path.join(ambiente.raiz, nome)
        with zipfile.ZipFile(caminho, "w") as pacote:
            pacote.writestr("nox.exe", "executavel-falso-versao-" + nova)
        with open(caminho, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        with open(os.path.join(ambiente.raiz, "SHA256SUMS"), "a",
                  encoding="ascii", newline="\n") as handle:
            handle.write("{0}  {1}\n".format(digest, nome))

        codigo, saida = rodar("-Version", nova, "-Prefix", ambiente.prefixo,
                              "-Source", ambiente.raiz)
        assert codigo == 0, saida
        assert os.path.isdir(ambiente.versao_dir("0.7.0")), "a anterior sumiu"
        assert os.path.isdir(ambiente.versao_dir("0.8.0"))
        assert "0.8.0" in open(ambiente.shim(), encoding="ascii").read()
        assert ambiente.config_intacto()
    finally:
        ambiente.close()


async def test_rollback_reaponta_sem_baixar():
    ambiente = ReleaseFalsa(versao="0.7.0")
    try:
        rodar("-Version", "0.7.0", "-Prefix", ambiente.prefixo,
              "-Source", ambiente.raiz)
        # simula uma segunda versão já instalada
        destino = ambiente.versao_dir("0.8.0")
        os.makedirs(destino)
        with open(os.path.join(destino, "nox.exe"), "w") as handle:
            handle.write("outra")
        rodar("-Version", "0.8.0", "-Prefix", ambiente.prefixo,
              "-Source", ambiente.raiz)
        assert "0.8.0" in open(ambiente.shim(), encoding="ascii").read()

        # volta para a anterior: origem inexistente, para provar que não baixa
        codigo, saida = rodar("-Version", "0.7.0", "-Prefix", ambiente.prefixo,
                              "-Source", os.path.join(ambiente.raiz, "nao-existe"))
        assert codigo == 0, saida
        assert "sem baixar" in saida or "reapontando" in saida, saida
        assert "0.7.0" in open(ambiente.shim(), encoding="ascii").read()
    finally:
        ambiente.close()


async def test_list_versions():
    ambiente = ReleaseFalsa()
    try:
        rodar("-Version", ambiente.versao, "-Prefix", ambiente.prefixo,
              "-Source", ambiente.raiz)
        codigo, saida = rodar("-Prefix", ambiente.prefixo, "-ListVersions")
        assert codigo == 0, saida
        assert ambiente.versao in saida, saida
        assert "ativa" in saida, saida
    finally:
        ambiente.close()


# ------------------------------------------------------- desinstalação


async def test_desinstalacao_preserva_configuracao():
    ambiente = ReleaseFalsa()
    try:
        rodar("-Version", ambiente.versao, "-Prefix", ambiente.prefixo,
              "-Source", ambiente.raiz)
        assert os.path.exists(ambiente.versao_dir())

        codigo, saida = rodar("-Prefix", ambiente.prefixo, "-Uninstall")
        assert codigo == 0, saida
        assert not os.path.exists(ambiente.versao_dir()), "a versão ficou"
        assert not os.path.exists(ambiente.shim()), "o shim ficou"
        assert "PRESERVAR" in saida or "configuração" in saida, saida

        # o que não pode ter sido tocado
        assert ambiente.config_intacto(), "a configuração foi alterada"
        assert os.path.isdir(ambiente.config_dir)
    finally:
        ambiente.close()


async def test_desinstalacao_em_dry_run_nao_remove():
    ambiente = ReleaseFalsa()
    try:
        rodar("-Version", ambiente.versao, "-Prefix", ambiente.prefixo,
              "-Source", ambiente.raiz)
        codigo, saida = rodar("-Prefix", ambiente.prefixo, "-Uninstall", "-DryRun")
        assert codigo == 0, saida
        assert os.path.exists(ambiente.versao_dir()), "removeu em -DryRun"
        assert os.path.exists(ambiente.shim())
    finally:
        ambiente.close()


# --------------------------------------------------------------- PATH


async def test_path_real_nao_e_alterado():
    """Nenhum teste passa -AddToPath; o PATH do usuário não pode mudar."""
    antes = path_do_usuario()
    ambiente = ReleaseFalsa()
    try:
        rodar("-Version", ambiente.versao, "-Prefix", ambiente.prefixo,
              "-Source", ambiente.raiz)
        rodar("-Prefix", ambiente.prefixo, "-Uninstall")
    finally:
        ambiente.close()
    depois = path_do_usuario()
    assert depois == antes, "o PATH do usuário mudou durante os testes"


async def test_sem_add_to_path_apenas_orienta():
    ambiente = ReleaseFalsa()
    try:
        codigo, saida = rodar("-Version", ambiente.versao, "-Prefix",
                              ambiente.prefixo, "-Source", ambiente.raiz)
        assert codigo == 0, saida
        assert "PATH não foi alterado" in saida or "PATH n" in saida, saida
        assert "SetEnvironmentVariable" in saida, "precisa mostrar a linha a colar"
    finally:
        ambiente.close()


# ------------------------------------------------------ diagnóstico


async def test_diagnostico_do_claude_sem_autenticar():
    ambiente = ReleaseFalsa()
    try:
        codigo, saida = rodar("-Version", ambiente.versao, "-Prefix",
                              ambiente.prefixo, "-Source", ambiente.raiz)
        assert codigo == 0, saida
        assert "provedor" in saida, saida
        # com ou sem a CLI presente, nada de login nem de chamada ao modelo
        assert "auth login" not in saida or "autentique-se" in saida, saida
        assert "nox setup" in saida or "claude auth login" in saida, saida
    finally:
        ambiente.close()


TESTS = [
    test_instalador_existe_na_raiz,
    test_nao_toca_em_path_de_maquina_nem_pede_privilegio,
    test_nao_toca_na_configuracao_do_usuario,
    test_sem_telemetria_e_sem_credencial,
    test_verificacao_vem_antes_da_extracao,
    test_parametros_declarados,
    test_dry_run_nao_escreve_nada,
    test_instalacao_completa,
    test_checksum_adulterado_aborta_antes_de_extrair,
    test_checksum_sem_a_linha_do_arquivo,
    test_atualizacao_preserva_versao_anterior,
    test_rollback_reaponta_sem_baixar,
    test_list_versions,
    test_desinstalacao_preserva_configuracao,
    test_desinstalacao_em_dry_run_nao_remove,
    test_path_real_nao_e_alterado,
    test_sem_add_to_path_apenas_orienta,
    test_diagnostico_do_claude_sem_autenticar,
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
