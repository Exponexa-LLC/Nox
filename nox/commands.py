"""Catálogo de comandos e a lógica do autocomplete do campo inferior.

Nada aqui toca a interface: são funções puras sobre o texto digitado, para a
TUI só perguntar "o que sugerir?" e "como fica o texto ao completar?". Assim o
comportamento pode ser testado sem levantar o app.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

#: Comandos oferecidos pelo autocomplete, na ordem em que aparecem no painel.
#: Cada item é (nome, descrição curta). `/copy tudo` é uma entrada própria
#: porque é a variação útil de `/copy`, não um argumento livre.
COMMANDS: List[Tuple[str, str]] = [
    ("/help", "mostra a ajuda"),
    ("/new", "conversa nova (descarta o contexto)"),
    ("/clear", "limpa a tela, mantém o contexto"),
    ("/copy", "copia a última resposta"),
    ("/copy tudo", "copia o transcript inteiro"),
    ("/model", "abre o seletor; /model <nome> escolhe direto"),
    ("/profile", "perfil de política; /profile <nome> troca"),
    ("/provider", "lista provedores; /provider <nome> troca"),
    ("/refresh-models", "reconsulta a lista de modelos, sem chamar o Claude"),
    ("/remote", "servidores autorizados, só leitura; /remote <alias> <op>"),
    ("/status", "provedor, backend, modelo, sessão, workspace"),
    ("/workspace", "mostra a pasta; /workspace <caminho> troca"),
    ("/exit", "encerra o programa"),
]

#: Largura da coluna do nome no painel — `/workspace` é o mais longo.
NAME_WIDTH: int = max(len(name) for name, _description in COMMANDS)


def _stripped(text: str) -> str:
    """Texto sem os espaços à esquerda, que o painel ignora ao comparar."""
    return text.lstrip()


def suggest(text: str) -> List[Tuple[str, str]]:
    """Sugestões para `text`, já filtradas e na ordem de `COMMANDS`.

    Devolve lista vazia quando não há nada a sugerir: texto que não começa com
    `/`, ou comando já completo seguido de argumento (`/model opus`) — nesse
    caso o usuário está escrevendo o argumento, e o painel só atrapalharia.
    """
    probe = _stripped(text)
    if not probe.startswith("/"):
        return []
    lowered = probe.lower()
    return [
        (name, description)
        for name, description in COMMANDS
        if name.startswith(lowered)
    ]


def complete(text: str, name: str) -> Tuple[str, int]:
    """Aplica `name` a `text`, preservando o argumento já digitado.

    Devolve (novo texto, posição do cursor). O token do comando é trocado e o
    resto da linha continua intacto: "/mo opus" + "/model" -> "/model opus".
    Sem argumento, sobra um espaço no fim para o argumento vir em seguida.
    """
    leading = text[: len(text) - len(_stripped(text))]
    probe = _stripped(text)

    # O argumento é o que sobra depois do trecho que o nome do comando cobre.
    # Esse trecho é o prefixo comum entre o que foi digitado e o nome — e não
    # `len(name)`, senão "/mo opus" + "/model" comeria letras do argumento.
    covered = 0
    limit = min(len(probe), len(name))
    while covered < limit and probe[covered].lower() == name[covered].lower():
        covered += 1
    argument = probe[covered:].lstrip()

    if argument:
        completed = "{0}{1} {2}".format(leading, name, argument)
    else:
        completed = "{0}{1} ".format(leading, name)
    return completed, len(completed)


def is_exact(text: str) -> bool:
    """`text` já é um comando completo (com ou sem argumento)?

    Usado pelo Enter: com o comando pronto, enviar é o que o usuário quer;
    incompleto, o Enter completa em vez de executar.
    """
    probe = _stripped(text).rstrip()
    if not probe:
        return False
    lowered = probe.lower()
    for name, _description in COMMANDS:
        if lowered == name or lowered.startswith(name + " "):
            return True
    return False


def selected_name(items: List[Tuple[str, str]], index: int) -> Optional[str]:
    """Nome da sugestão em `index`, ou None se a lista estiver vazia."""
    if not items:
        return None
    return items[index % len(items)][0]
