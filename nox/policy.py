# -*- coding: utf-8 -*-
"""Política declarativa do 🐺 Exponexa: tetos absolutos e capacidades.

O desenho tem duas camadas, e a diferença entre elas é proposital:

**HARD_CAPS** são limites que nenhum perfil, JSON ou comando alcança. A forma
mais forte de garantir um teto não é validar um campo — é **não existir campo**
para violá-lo. Por isso não há `tools_enabled`, não há `remote_write` e não há
`confirm_remote` configurável: ferramentas do modelo são assunto do backend
(que segue com `--tools ""`), escrita remota não tem código, e a confirmação
de remoto/sonda é constante consultada pela TUI.

**Capacidades** são o que a aplicação sabe fazer, enumerado em `CAPABILITIES`.
Perfis escolhem livremente dentro desse registro — inclusive um perfil seu pode
LIGAR capacidades que os embutidos não ligam. Isso é flexibilidade dentro do
teto, e é o objetivo: pouca fricção onde não há risco.

Este módulo é puro: não lê rede, não executa nada, não conhece a TUI.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import remote_ops


class Capability(object):
    """Uma capacidade que a aplicação declara saber fazer."""

    def __init__(self, name, descricao, implemented=True,
                 requires_confirmation=False):
        self.name = name
        self.descricao = descricao
        #: Falso = ponto de extensão declarado, que nenhum perfil pode ligar.
        self.implemented = implemented
        #: Confirmação obrigatória — teto, não preferência.
        self.requires_confirmation = requires_confirmation


#: Registro do que existe. Perfil que peça algo fora daqui é recusado com aviso.
CAPABILITIES: Tuple[Capability, ...] = (
    Capability("chat", "conversar com o modelo"),
    Capability("model.switch", "trocar de modelo (/model)"),
    Capability("provider.switch", "trocar de provedor (/provider)"),
    Capability("workspace.switch", "trocar a pasta de trabalho (/workspace)"),
    Capability("models.discover", "descobrir modelos localmente (/refresh-models)"),
    Capability("models.probe", "sonda real de modelos (--sonda)",
               requires_confirmation=True),
    Capability("remote.read", "diagnóstico remoto somente leitura (/remote)",
               requires_confirmation=True),
    # ---------------------------------------------------------------- futuro
    # Declarada para ficar explícito que NÃO existe: nenhum perfil consegue
    # ligá-la enquanto `implemented` for False.
    Capability("local.tools", "ferramentas locais de desenvolvimento",
               implemented=False),
)

#: Capacidade que todo perfil tem, sempre: sem ela não há aplicação.
ALWAYS = "chat"


class HardCaps(object):
    """Limites imutáveis. Nenhum caminho de código os afrouxa."""

    #: Operação remota exige confirmação sua, sempre.
    CONFIRM_REMOTE = True

    #: Sonda de modelos exige confirmação explícita, sempre.
    CONFIRM_PROBE = True

    #: Escrita remota não existe nesta fase — nem como capacidade.
    REMOTE_WRITE = False

    #: Operações remotas nunca ultrapassam a allowlist do `remote_ops`.
    REMOTE_OPERATIONS = tuple(op.name for op in remote_ops.OPERATIONS)

    #: Faixa aceita para o corte da saída remota exibida.
    MIN_OUTPUT_LINES = 5
    MAX_OUTPUT_LINES = 500

    def __setattr__(self, name, value):  # pragma: no cover - proteção
        raise AttributeError("HARD_CAPS são imutáveis")


CAPS = HardCaps()

#: Campos aceitos num perfil declarado em JSON. Qualquer outro é recusado.
PROFILE_FIELDS = ("nome", "name", "base", "descricao", "description",
                  "capacidades", "capabilities", "remote_operations",
                  "require_remote_log", "max_output_lines")

#: Pedaços de nome que denunciam tentativa de guardar segredo no perfil.
SECRET_HINTS = ("key", "token", "secret", "password", "senha", "passphrase",
                "credential")


def capability(name: str) -> Optional[Capability]:
    for item in CAPABILITIES:
        if item.name == name:
            return item
    return None


def capability_names(implemented_only: bool = True) -> List[str]:
    return [c.name for c in CAPABILITIES if c.implemented or not implemented_only]


class Policy(object):
    """O que está liberado nesta sessão, já dentro dos tetos."""

    def __init__(self, name="conversa", descricao="", capabilities=None,
                 remote_operations=None, require_remote_log=False,
                 max_output_lines=40):
        self.name = name
        self.descricao = descricao
        self.capabilities = frozenset(capabilities or (ALWAYS,))
        self.remote_operations = tuple(
            remote_operations if remote_operations is not None
            else CAPS.REMOTE_OPERATIONS)
        self.require_remote_log = bool(require_remote_log)
        self.max_output_lines = int(max_output_lines)

    # ------------------------------------------------------------ consultas

    def allows(self, cap: str) -> bool:
        return cap in self.capabilities

    def allows_operation(self, operation: str) -> bool:
        """Operação remota permitida? Exige a capacidade e a allowlist."""
        if not self.allows("remote.read"):
            return False
        return operation in self.remote_operations

    def needs_confirmation(self, cap: str) -> bool:
        item = capability(cap)
        return bool(item and item.requires_confirmation)

    def summary(self) -> str:
        """Resumo curto para o cabeçalho e o /status."""
        partes = []
        partes.append("remoto" if self.allows("remote.read") else "sem remoto")
        partes.append("sonda" if self.allows("models.probe") else "sem sonda")
        return "sem ferramentas, " + ", ".join(partes)

    def __repr__(self) -> str:  # pragma: no cover - depuração
        return "<Policy {0} {1}>".format(self.name, sorted(self.capabilities))


# ------------------------------------------------------------------ tetos


def enforce(policy: "Policy") -> Tuple["Policy", List[str]]:
    """Aplica os HARD_CAPS. Devolve (política ajustada, avisos).

    É a última barreira: rode isto depois de qualquer merge, venha de onde
    vier. Capacidade inexistente ou não implementada cai fora; operação remota
    fora da allowlist cai fora; o corte de saída entra na faixa.
    """
    avisos: List[str] = []

    permitidas = set()
    for nome in sorted(policy.capabilities):
        item = capability(nome)
        if item is None:
            avisos.append("capacidade desconhecida ignorada: {0}".format(nome))
            continue
        if not item.implemented:
            avisos.append(
                "capacidade ainda não implementada, ignorada: {0}".format(nome))
            continue
        permitidas.add(nome)
    permitidas.add(ALWAYS)  # conversa nunca é desligada

    operacoes = tuple(
        nome for nome in policy.remote_operations
        if nome in CAPS.REMOTE_OPERATIONS)
    if len(operacoes) != len(policy.remote_operations):
        avisos.append("operações remotas fora da allowlist foram descartadas.")

    linhas = policy.max_output_lines
    if linhas < CAPS.MIN_OUTPUT_LINES or linhas > CAPS.MAX_OUTPUT_LINES:
        avisos.append(
            "max_output_lines fora da faixa {0}–{1}; ajustado.".format(
                CAPS.MIN_OUTPUT_LINES, CAPS.MAX_OUTPUT_LINES))
        linhas = min(max(linhas, CAPS.MIN_OUTPUT_LINES), CAPS.MAX_OUTPUT_LINES)

    ajustada = Policy(
        name=policy.name,
        descricao=policy.descricao,
        capabilities=permitidas,
        remote_operations=operacoes,
        require_remote_log=policy.require_remote_log,
        max_output_lines=linhas,
    )
    return ajustada, avisos


# ------------------------------------------------------- perfil declarado


def from_dict(base: "Policy", raw) -> Tuple["Policy", List[str]]:
    """Constrói um perfil a partir de JSON, partindo de `base`.

    O JSON pode LIGAR capacidades que a base não tem — é flexibilidade dentro
    do teto. O que ele não pode é inventar capacidade, sair da allowlist ou
    guardar segredo.
    """
    avisos: List[str] = []
    if not isinstance(raw, dict):
        return base, ["perfil inválido: esperava um objeto JSON."]

    for campo in raw:
        minusculo = str(campo).lower()
        if any(pista in minusculo for pista in SECRET_HINTS):
            return base, [
                "perfil recusado: campo {0!r} parece guardar segredo — "
                "credenciais não moram em perfil.".format(campo)]
        if minusculo not in PROFILE_FIELDS:
            avisos.append("campo desconhecido no perfil: {0}".format(campo))

    nome = str(raw.get("nome") or raw.get("name") or base.name)
    descricao = str(raw.get("descricao") or raw.get("description")
                    or base.descricao)

    declaradas = raw.get("capacidades", raw.get("capabilities"))
    if declaradas is None:
        capacidades = set(base.capabilities)
    elif isinstance(declaradas, list):
        capacidades = set()
        for item in declaradas:
            if isinstance(item, str):
                capacidades.add(item)
            else:
                avisos.append("capacidade inválida ignorada: {0!r}".format(item))
    else:
        avisos.append("capacidades deve ser uma lista; usando as da base.")
        capacidades = set(base.capabilities)

    operacoes = raw.get("remote_operations")
    if operacoes is None:
        operacoes = base.remote_operations
    elif isinstance(operacoes, list):
        operacoes = tuple(str(item) for item in operacoes)
    else:
        avisos.append("remote_operations deve ser uma lista; usando a base.")
        operacoes = base.remote_operations

    exige_log = raw.get("require_remote_log", base.require_remote_log)
    if not isinstance(exige_log, bool):
        avisos.append("require_remote_log deve ser booleano; usando a base.")
        exige_log = base.require_remote_log

    linhas = raw.get("max_output_lines", base.max_output_lines)
    if isinstance(linhas, bool) or not isinstance(linhas, int):
        avisos.append("max_output_lines deve ser inteiro; usando a base.")
        linhas = base.max_output_lines

    candidata = Policy(
        name=nome, descricao=descricao, capabilities=capacidades,
        remote_operations=operacoes, require_remote_log=exige_log,
        max_output_lines=linhas,
    )
    ajustada, avisos_teto = enforce(candidata)
    return ajustada, avisos + avisos_teto
