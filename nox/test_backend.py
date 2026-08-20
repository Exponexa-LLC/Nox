# -*- coding: utf-8 -*-
"""Testes do backend Claude — sem nenhuma chamada real ao modelo.

Rodar com:

    python -m nox.test_backend      (com o ambiente do projeto ativo)

`send()` nunca é chamado: o que se verifica é a montagem do comando, a limpeza
de credenciais do ambiente do processo filho, a sessão e o estado dos pontos de
extensão. Nada é gravado em disco.
"""

from __future__ import annotations

import asyncio
import os
import sys

from . import backends

#: Raiz do projeto, deduzida deste arquivo — nada de caminho de máquina.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def claude():
    return backends.ClaudeCLIBackend(cwd=PROJECT_ROOT)


# ------------------------------------------------------ disponibilidade


async def test_disponibilidade_coerente():
    """`available()` acompanha a presença do executável, sem chamar o modelo."""
    backend = claude()
    assert backend.available() == bool(backend.executable), (
        backend.available(), backend.executable)
    if not backend.available():
        assert backend.unavailable_reason(), "indisponível tem de explicar por quê"


async def test_sem_credencial_de_api():
    """A autenticação vem da sessão do Claude Code, não de chave de API."""
    backend = claude()
    assert backend.ENV_ALLOW == (), backend.ENV_ALLOW


# ------------------------------------------------------- comando montado


async def test_primeira_mensagem_abre_sessao():
    backend = claude()
    args = backend._command("oi", "sonnet")
    assert "--session-id" in args, args
    assert "--resume" not in args, args
    assert args[args.index("--session-id") + 1] == backend.session_id


async def test_continuacao_mantem_contexto():
    backend = claude()
    backend._started = True
    args = backend._command("de novo", "opus")
    assert "--resume" in args, args
    assert "--session-id" not in args, args
    assert args[args.index("--resume") + 1] == backend.session_id


async def test_modo_print_e_json():
    args = claude()._command("oi", "sonnet")
    assert "-p" in args, args
    assert args[args.index("--output-format") + 1] == "json", args


async def test_modelo_repassado():
    args = claude()._command("oi", "opus")
    assert args[args.index("--model") + 1] == "opus", args


async def test_sem_modelo_nao_manda_flag():
    args = claude()._command("oi", "")
    assert "--model" not in args, args


async def test_ferramentas_desabilitadas():
    args = claude()._command("oi", "sonnet")
    assert args[args.index("--tools") + 1] == "", args


# ----------------------------------------------------------- ambiente


async def test_credenciais_alheias_removidas():
    """Nenhuma ANTHROPIC_* vaza para o processo filho da CLI."""
    backend = claude()
    anterior = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "fake-so-para-o-teste"
    try:
        env = backend.env()
        assert "ANTHROPIC_API_KEY" not in env, "a chave vazou para o filho"
    finally:
        if anterior is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = anterior


# -------------------------------------------------------------- sessão


async def test_reset_troca_a_sessao():
    backend = claude()
    antes = backend.session_id
    backend.reset()
    assert backend.session_id != antes, backend.session_id
    assert backend.session_label(), "a sessão precisa de rótulo para o cabeçalho"


async def test_cancel_sem_processo():
    assert claude().cancel() is False


async def test_cwd_repassado():
    backend = backends.ClaudeCLIBackend(cwd=PROJECT_ROOT)
    assert backend.cwd == PROJECT_ROOT, backend.cwd
    assert os.path.isdir(backend.cwd), backend.cwd


# -------------------------------------------------------------- modelos


async def test_modelos_e_metadados():
    backend = claude()
    assert backend.models() == ["sonnet", "opus", "haiku"], backend.models()
    linhas = backend.model_rows()
    assert len(linhas) == 3, linhas
    for nome, alias, descricao in linhas:
        assert nome and alias and descricao, (nome, alias, descricao)


async def test_model_rows_sem_metadado():
    """Backend sem MODEL_INFO ainda aparece no seletor, só com o nome."""
    planejado = backends.get_backend("gemini")
    linhas = planejado.model_rows()
    assert linhas, linhas
    for nome, alias, descricao in linhas:
        assert nome and alias == "" and descricao == "", (nome, alias, descricao)


# ------------------------------------------------- pontos de extensão


async def test_planejados_seguem_sem_implementacao():
    for nome in ("gemini", "openai", "ollama"):
        backend = backends.get_backend(nome)
        assert isinstance(backend, backends.PlannedBackend), nome
        assert not backend.available(), nome
        assert backend.unavailable_reason(), "faltou o motivo de " + nome


async def test_registro_de_provedores():
    assert backends.DEFAULT_BACKEND == "claude"
    for nome in ("claude", "gemini", "openai", "ollama", "echo"):
        assert nome in backends.REGISTRY, nome
        assert nome in backends.PROVIDERS, nome


# ------------------------------------------------ decodificacao (N-12)


async def test_decode_utf8():
    texto = "acentuação, emoji 🐺 e ideograma 日本語"
    assert backends._decode(texto.encode("utf-8")) == texto


async def test_decode_cp1252():
    """Console Windows legado devolve cp1252: acento não pode virar lixo."""
    texto = "configuração não iniciada"
    bruto = texto.encode("cp1252")
    decodificado = backends._decode(bruto)
    assert "configura" in decodificado
    assert chr(0xFFFD) not in decodificado or decodificado == texto


async def test_decode_bytes_invalidos_nao_levanta():
    bruto = b"inicio " + bytes([0xFF, 0xFE, 0x00]) + b" fim"
    saida = backends._decode(bruto)
    assert isinstance(saida, str)
    assert "inicio" in saida and "fim" in saida


async def test_decode_nao_bytes():
    assert backends._decode("já é texto") == "já é texto"
    assert backends._decode(None) == ""
    assert backends._decode(123) == "123"


async def test_decode_candidatos_comecam_por_utf8():
    candidatos = backends._decode_candidates()
    assert candidatos[0] == "utf-8", candidatos
    assert len(candidatos) == len(set(c.lower() for c in candidatos))


async def test_decode_candidatos_incluem_mbcs_no_windows():
    """No Windows, `mbcs` é a rede de segurança contra o modo UTF-8."""
    candidatos = backends._decode_candidates()
    if sys.platform == "win32":
        assert "mbcs" in candidatos, candidatos
        assert candidatos.index("mbcs") == len(candidatos) - 1, \
            "mbcs é o ÚLTIMO recurso, depois do locale"
    else:
        assert "mbcs" not in candidatos, candidatos


async def test_decode_cp1252_sobrevive_ao_modo_utf8():
    """Regressão: sob `-X utf8` o cp1252 caía no replace e perdia o acento.

    O modo UTF-8 é simulado dentro do processo — `getpreferredencoding` passa
    a devolver "UTF-8", que é exatamente o que a flag provoca. Sem `mbcs` na
    lista, este teste falha: é essa a fragilidade que ele tranca.
    """
    import locale as modulo_locale

    original = modulo_locale.getpreferredencoding
    modulo_locale.getpreferredencoding = lambda do_setlocale=True: "UTF-8"
    try:
        candidatos = backends._decode_candidates()
        assert "cp1252" not in [c.lower() for c in candidatos], candidatos
        texto = "configuração não iniciada"
        decodificado = backends._decode(texto.encode("cp1252"))
        if sys.platform == "win32":
            assert decodificado == texto, repr(decodificado)
        else:
            # fora do Windows não há página ANSI: o replace é o comportamento
            assert isinstance(decodificado, str)
    finally:
        modulo_locale.getpreferredencoding = original


# ------------------------------------------- modelo do resultado (N-13)


async def test_model_name_um_modelo():
    dados = {"modelUsage": {"claude-opus-5": {"inputTokens": 10, "outputTokens": 5}}}
    assert backends._model_name(dados) == "claude-opus-5"


async def test_model_name_escolhe_o_de_maior_uso():
    dados = {"modelUsage": {
        "claude-haiku-4-5": {"inputTokens": 10, "outputTokens": 2},
        "claude-opus-5": {"inputTokens": 900, "outputTokens": 300},
    }}
    # alfabeticamente o haiku viria antes; o que vale é o uso
    assert backends._model_name(dados) == "claude-opus-5"


async def test_model_name_empate_e_deterministico():
    dados = {"modelUsage": {
        "claude-sonnet-5": {"inputTokens": 10},
        "claude-opus-5": {"inputTokens": 10},
    }}
    primeiro = backends._model_name(dados)
    assert primeiro == "claude-opus-5", primeiro
    for _ in range(5):
        assert backends._model_name(dados) == primeiro


async def test_model_name_sem_dados():
    assert backends._model_name({}) == ""
    assert backends._model_name({"modelUsage": {}}) == ""
    assert backends._model_name({"modelUsage": None}) == ""
    assert backends._model_name({"modelUsage": []}) == ""


async def test_model_name_formato_inesperado():
    """Sem números utilizáveis, ainda devolve algo estável — nunca quebra."""
    dados = {"modelUsage": {"modelo-b": "texto", "modelo-a": None}}
    assert backends._model_name(dados) == "modelo-a"
    numerico = {"modelUsage": {"a": 5, "b": 50}}
    assert backends._model_name(numerico) == "b"


# ------------------------------------------------- flag --tools (N-11)


async def test_comando_de_verificacao_manual_nao_executa():
    """`tools_smoke_command` só MONTA o comando; rodar é decisão do usuário."""
    backend = claude()
    argv = backend.tools_smoke_command()
    assert "-p" in argv and argv[argv.index("--tools") + 1] == ""
    assert "--output-format" in argv
    # e o envio normal continua igual
    assert claude()._command("oi", "sonnet")[-2:] == ["--tools", ""]


TESTS = [
    test_disponibilidade_coerente,
    test_sem_credencial_de_api,
    test_primeira_mensagem_abre_sessao,
    test_continuacao_mantem_contexto,
    test_modo_print_e_json,
    test_modelo_repassado,
    test_sem_modelo_nao_manda_flag,
    test_ferramentas_desabilitadas,
    test_credenciais_alheias_removidas,
    test_reset_troca_a_sessao,
    test_cancel_sem_processo,
    test_cwd_repassado,
    test_modelos_e_metadados,
    test_model_rows_sem_metadado,
    test_planejados_seguem_sem_implementacao,
    test_registro_de_provedores,
    test_decode_utf8,
    test_decode_cp1252,
    test_decode_bytes_invalidos_nao_levanta,
    test_decode_nao_bytes,
    test_decode_candidatos_comecam_por_utf8,
    test_decode_candidatos_incluem_mbcs_no_windows,
    test_decode_cp1252_sobrevive_ao_modo_utf8,
    test_model_name_um_modelo,
    test_model_name_escolhe_o_de_maior_uso,
    test_model_name_empate_e_deterministico,
    test_model_name_sem_dados,
    test_model_name_formato_inesperado,
    test_comando_de_verificacao_manual_nao_executa,
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
