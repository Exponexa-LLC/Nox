# -*- coding: utf-8 -*-
"""Descoberta da lista de modelos, em camadas, sem chamar o modelo.

Camadas, da mais confiável para a menos:

1. **sonda real** — `claude -p ping --model <alias>` por candidato. É a única
   prova de disponibilidade para a sessão e o plano, mas faz chamadas de
   verdade: fica DESLIGADA por padrão e só roda por `/refresh-models --sonda`
   com confirmação explícita do usuário (`probe_models(..., confirmed=True)`).
2. **`claude --help`** — os aliases que a CLI instalada documenta. Local, sem
   chamada, e muda sozinho quando a CLI é atualizada. Um alias citado ali que o
   catálogo não conhece entra na lista marcado como novidade.
3. **`claude auth status`** — JSON oficial com o estado da autenticação e o
   plano. Não lista modelos; diz se há sessão e qual é o plano.
4. **catálogo local** (`models_catalog`) — a lista mantida à mão, com data.

Falhou tudo? Usa o último cache válido; sem cache, o catálogo. Em qualquer
caso a procedência acompanha o resultado, para o seletor poder exibi-la.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Callable, Dict, List, Optional, Tuple

from . import models_catalog

#: Quanto tempo um cache é considerado fresco (segundos). Só afeta o rótulo.
CACHE_TTL = 24 * 60 * 60

#: Origem de cada linha, exibida no seletor.
ORIGEM_CATALOGO = "catálogo"
ORIGEM_CLI = "novo na CLI"
ORIGEM_SONDA = "sonda"
ORIGEM_CACHE = "cache"


class Discovery(object):
    """Resultado de uma descoberta: linhas, procedência e avisos."""

    def __init__(self, rows=None, source="", warnings=None, checked_at=0.0,
                 cli_version="", plan="", flags=None):
        #: (alias, nome exibido, id técnico, descrição, origem)
        self.rows: List[Tuple[str, str, str, str, str]] = list(rows or [])
        self.source = source
        self.warnings: List[str] = list(warnings or [])
        self.checked_at = checked_at
        self.cli_version = cli_version
        self.plan = plan
        #: Opções longas documentadas pelo `--help`; None = não verificado.
        self.flags: Optional[List[str]] = list(flags) if flags is not None else None

    def tools_flag_state(self) -> str:
        """`documentada`, `ausente` ou `não verificada` — para o /status."""
        if self.flags is None:
            return "não verificada"
        return "documentada" if REQUIRED_FLAG in self.flags else "ausente"

    def aliases(self) -> List[str]:
        return [row[0] for row in self.rows]

    def to_dict(self) -> Dict:
        return {
            "rows": [list(row) for row in self.rows],
            "source": self.source,
            "checked_at": self.checked_at,
            "cli_version": self.cli_version,
            "plan": self.plan,
        }

    @classmethod
    def from_dict(cls, data) -> "Discovery":
        rows = []
        for row in data.get("rows") or []:
            if len(row) == 5:
                rows.append(tuple(row))
        return cls(
            rows=rows,
            source=data.get("source", ""),
            checked_at=data.get("checked_at", 0.0) or 0.0,
            cli_version=data.get("cli_version", ""),
            plan=data.get("plan", ""),
        )


# ------------------------------------------------------------------ runner


def run_cli(args: List[str], timeout: float = 20.0) -> Tuple[int, str]:
    """Executa a CLI localmente e devolve (código, saída).

    Só é usado para `--version`, `--help` e `auth status`: nenhum desses fala
    com o modelo. A sonda tem o seu próprio caminho, explícito.
    """
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # no Windows o executável é `claude.CMD`: sem resolver pelo PATHEXT, o
        # Popen não acha o nome pelado e toda a descoberta falharia à toa.
        alvo = list(args)
        resolvido = shutil.which(alvo[0]) if alvo else ""
        if resolvido:
            alvo[0] = resolvido
        process = subprocess.Popen(
            alvo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        saida, _ = process.communicate(timeout=timeout)
        texto = saida.decode("utf-8", "replace") if saida else ""
        return process.returncode, texto
    except (OSError, subprocess.SubprocessError) as erro:
        return 1, str(erro)


# ------------------------------------------------- camada 2: claude --help


#: Captura os tokens entre aspas simples do bloco do `--model` no --help.
_QUOTED = re.compile(r"'([A-Za-z0-9][A-Za-z0-9.\-]*)'")


def _model_help_section(help_text: str) -> str:
    """Recorta o trecho do `--help` que descreve a opção `--model`."""
    linhas = help_text.splitlines()
    recorte = []
    dentro = False
    for linha in linhas:
        if re.match(r"^\s{2}--model\b", linha):
            dentro = True
            recorte.append(linha)
            continue
        if dentro:
            # o bloco termina quando começa outra opção
            if re.match(r"^\s{2}(-\w|--\w)", linha):
                break
            recorte.append(linha)
    return "\n".join(recorte)


#: Opções longas documentadas pelo `--help` (`--tools`, `--model`, …).
_FLAG = re.compile(r"^\s{2,}(?:-\w,\s*)?(--[a-z][a-z0-9-]*)", re.MULTILINE)

#: Flag da qual o backend depende para manter o modelo sem ferramentas.
REQUIRED_FLAG = "--tools"


def parse_cli_flags(help_text: str) -> List[str]:
    """Opções longas que a CLI instalada documenta.

    Serve para conferir localmente, sem chamar o modelo, se uma flag da qual
    dependemos ainda existe — hoje, `--tools`.
    """
    encontradas = []
    for flag in _FLAG.findall(help_text or ""):
        if flag not in encontradas:
            encontradas.append(flag)
    return encontradas


def parse_cli_aliases(help_text: str) -> Tuple[List[str], List[str]]:
    """Aliases e ids completos citados pelo `--help` da CLI instalada.

    Devolve (aliases curtos, ids completos). A lista é EXEMPLIFICATIVA: a CLI
    escreve "e.g.", então a ausência de um modelo aqui não prova nada — por
    isso ela só acrescenta, nunca remove linhas do catálogo.
    """
    secao = _model_help_section(help_text or "")
    curtos, completos = [], []
    for token in _QUOTED.findall(secao):
        alvo = completos if token.lower().startswith("claude-") else curtos
        if token not in alvo:
            alvo.append(token)
    return curtos, completos


# ------------------------------------------- camada 3: claude auth status


def parse_auth_status(saida: str) -> Dict:
    """Lê o JSON do `claude auth status`; devolve {} se não der para ler."""
    texto = (saida or "").strip()
    inicio = texto.find("{")
    fim = texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        return {}
    try:
        dados = json.loads(texto[inicio:fim + 1])
    except ValueError:
        return {}
    return dados if isinstance(dados, dict) else {}


# ----------------------------------------------------------- montagem


def build_rows(cli_aliases, cli_ids) -> List[Tuple[str, str, str, str, str]]:
    """Junta catálogo e aliases novos citados pela CLI, nessa ordem."""
    rows = []
    for alias, nome, identificador, descricao in models_catalog.current_rows():
        if models_catalog.is_superseded(identificador):
            continue
        rows.append((alias, nome, identificador, descricao, ORIGEM_CATALOGO))

    conhecidos = set(models_catalog.aliases())
    for alias in cli_aliases or []:
        if alias in conhecidos or models_catalog.is_superseded(alias):
            continue
        conhecidos.add(alias)
        # a CLI cita um alias que o catálogo não conhece: é sinal de modelo
        # novo. Entra na lista marcado como tal, sem descrição inventada.
        rows.append((alias, alias, "", "citado pelo --help da CLI", ORIGEM_CLI))
    return rows


def discover(runner: Optional[Callable] = None) -> Discovery:
    """Descoberta das camadas 2 a 4. Nunca chama o modelo."""
    import time

    run = runner or run_cli
    avisos = []

    codigo, versao_txt = run(["claude", "--version"])
    cli_version = versao_txt.strip().splitlines()[0] if (codigo == 0 and versao_txt) else ""

    codigo, help_txt = run(["claude", "--help"])
    flags = None
    if codigo == 0 and help_txt:
        cli_aliases, cli_ids = parse_cli_aliases(help_txt)
        flags = parse_cli_flags(help_txt)
        if REQUIRED_FLAG not in flags:
            # não muda nada no envio: o backend segue mandando --tools "".
            # É um alerta técnico para você conferir antes que vire surpresa.
            avisos.append(
                "atenção: a CLI instalada não documenta {0} no --help. O "
                "backend continua enviando {0} \"\" (fallback). Confira com: "
                "claude --help".format(REQUIRED_FLAG))
    else:
        cli_aliases, cli_ids = [], []
        avisos.append("não consegui ler o --help da CLI; usando só o catálogo.")

    plano = ""
    codigo, auth_txt = run(["claude", "auth", "status"])
    dados = parse_auth_status(auth_txt) if codigo == 0 else {}
    if dados:
        plano = str(dados.get("subscriptionType") or "")
        if dados.get("loggedIn") is False:
            avisos.append("a CLI não está autenticada — nenhum modelo disponível.")
    else:
        avisos.append("não consegui ler o estado da autenticação.")

    rows = build_rows(cli_aliases, cli_ids)
    fonte = "catálogo {0}".format(models_catalog.CATALOG_REVIEWED)
    if cli_version:
        fonte += " + CLI {0}".format(cli_version.split()[0])
    if plano:
        fonte += " · plano {0}".format(plano)
    return Discovery(rows=rows, source=fonte, warnings=avisos,
                     checked_at=time.time(), cli_version=cli_version,
                     plan=plano, flags=flags)


# --------------------------------------------------------------- cache


def load_cache(path: str) -> Optional[Discovery]:
    """Último resultado válido gravado, já sem os modelos que saíram de linha."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            dados = json.load(handle)
    except (OSError, ValueError):
        return None
    cache = Discovery.from_dict(dados if isinstance(dados, dict) else {})
    cache.rows = [
        row for row in cache.rows
        if not (models_catalog.is_superseded(row[2])
                or models_catalog.is_superseded(row[0]))
    ]
    if not cache.rows:
        return None
    cache.source = (cache.source or "cache") + " (cache)"
    return cache


def save_cache(path: str, discovery: "Discovery") -> bool:
    """Grava o resultado. Só nomes e datas — nada de credencial."""
    if not path or discovery is None or not discovery.rows:
        return False
    try:
        pasta = os.path.dirname(path)
        if pasta and not os.path.isdir(pasta):
            os.makedirs(pasta)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(discovery.to_dict(), handle, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def catalog_only(motivo: str = "") -> Discovery:
    """Última linha de defesa: o catálogo local, avisando que é ele mesmo."""
    import time

    avisos = [motivo] if motivo else []
    rows = build_rows([], [])
    return Discovery(
        rows=rows,
        source="catálogo {0} (sem confirmação)".format(
            models_catalog.CATALOG_REVIEWED),
        warnings=avisos,
        checked_at=time.time(),
    )


def resolve(cache_path: str = "", runner: Optional[Callable] = None) -> Discovery:
    """Descobre; falhando, cai no cache; falhando, no catálogo local."""
    try:
        encontrado = discover(runner=runner)
    except Exception as erro:  # nenhuma falha de descoberta derruba a TUI
        encontrado = None
        falha = "falha na descoberta de modelos: {0}".format(erro)
    else:
        falha = ""

    if encontrado is not None and encontrado.rows and not encontrado.warnings:
        save_cache(cache_path, encontrado)
        return encontrado

    cache = load_cache(cache_path)
    if cache is not None:
        cache.warnings = list(
            (encontrado.warnings if encontrado else [falha])
        ) + ["usando a última lista válida em cache."]
        return cache

    if encontrado is not None and encontrado.rows:
        save_cache(cache_path, encontrado)
        return encontrado
    return catalog_only(falha or "sem cache — usando o catálogo local.")


# ------------------------------------------------------ camada 1: sonda
#
# DESLIGADA POR PADRÃO. Faz chamadas reais ao Claude (uma por candidato), então
# só roda com `confirmed=True`, que a TUI só passa depois de o usuário
# confirmar `/refresh-models --sonda`. Nenhum teste automático a executa.


class ProbeNotConfirmed(RuntimeError):
    """A sonda foi chamada sem confirmação explícita do usuário."""


def probe_models(aliases, runner=None, confirmed: bool = False,
                 prompt: str = "ping") -> Dict[str, bool]:
    """Confirma, um a um, quais aliases a sessão aceita de verdade.

    Cada alias custa uma chamada curta ao Claude. Sem `confirmed=True` isto
    levanta `ProbeNotConfirmed` em vez de gastar qualquer coisa.
    """
    if not confirmed:
        raise ProbeNotConfirmed(
            "a sonda faz chamadas reais ao Claude e precisa de confirmação.")
    run = runner or run_cli
    resultado = {}
    for alias in aliases or []:
        codigo, _saida = run(
            ["claude", "-p", prompt, "--model", alias, "--tools", ""])
        resultado[alias] = codigo == 0
    return resultado


def apply_probe(discovery: "Discovery", resultado: Dict[str, bool]) -> "Discovery":
    """Mantém só o que a sonda confirmou, marcando a origem."""
    rows = []
    for alias, nome, identificador, descricao, _origem in discovery.rows:
        if resultado.get(alias):
            rows.append((alias, nome, identificador, descricao, ORIGEM_SONDA))
    confirmado = Discovery(
        rows=rows,
        source="sonda real · {0}".format(discovery.cli_version or "CLI"),
        warnings=[] if rows else ["a sonda não confirmou nenhum modelo."],
        checked_at=discovery.checked_at,
        cli_version=discovery.cli_version,
        plan=discovery.plan,
    )
    return confirmado
