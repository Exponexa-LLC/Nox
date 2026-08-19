"""Configuração local do 🐺 Exponexa (pacote `nox`).

Guarda **apenas preferências** — provedor ativo, modelo por provedor, timeout e
workspace inicial — em `~/.nox/config.json`, fora do projeto. Instalações
antigas guardavam isso em `~/.delet_user`: a migração é uma cópia validada, e a
pasta antiga é preservada (ver `migrate_legacy`).

Nunca guardamos credenciais: nenhuma chave é escrita no código nem gravada no
arquivo. Se um provedor exigir credencial, o config armazena no máximo o *nome*
da variável de ambiente a consultar (ex.: `"api_key_env": "GEMINI_API_KEY"`), e
qualquer campo que pareça um segredo é descartado na leitura, com aviso.

A leitura é tolerante: arquivo ausente, JSON corrompido ou pasta sem permissão
de escrita não derrubam a TUI — caímos nos padrões e registramos um aviso.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

#: Caminho padrão do arquivo de configuração.
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".nox")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

#: Pasta usada antes da renomeação para `nox`. Só é lida, nunca apagada.
LEGACY_DIR = os.path.join(os.path.expanduser("~"), ".delet_user")

#: Arquivos que a migração copia da pasta antiga, se ainda não existirem.
MIGRATED_FILES = ("config.json", "models_cache.json")

#: Preferências padrão.
DEFAULTS: Dict[str, Any] = {
    "provider": "claude",
    "models": {
        "claude": "sonnet",
        "gemini": "gemini-2.5-pro",
        "openai": "gpt-4o",
        "ollama": "llama3.1",
    },
    "timeout": 120,
    "workspace": "",  # vazio = raiz do projeto
    "profile": "conversa",  # politica ativa; invalido cai no seguro
}

#: Pedaços de nome que denunciam um segredo. Campos assim são descartados.
SECRET_HINTS = ("key", "token", "secret", "password", "senha", "credential")

#: Exceção: guardar o *nome* da variável de ambiente é permitido.
SECRET_ALLOW = ("api_key_env", "key_env", "token_env")


def looks_like_secret(name: str) -> bool:
    """Diz se um campo de config parece guardar um segredo."""
    lowered = name.lower()
    if lowered in SECRET_ALLOW or lowered.endswith("_env"):
        return False
    return any(hint in lowered for hint in SECRET_HINTS)


def migrate_legacy(new_dir: str = CONFIG_DIR, old_dir: str = LEGACY_DIR) -> List[str]:
    """Copia a config antiga de `~/.delet_user` para `~/.nox`, se fizer falta.

    É cópia, nunca movimento: a pasta antiga fica intacta até você mesmo
    apagá-la. Só copia o que ainda não existe no destino, e só depois de o
    conteúdo passar por uma leitura de JSON — arquivo corrompido não é migrado.
    Devolve os avisos a mostrar no transcript.
    """
    avisos: List[str] = []
    if not old_dir or not os.path.isdir(old_dir) or os.path.abspath(old_dir) == os.path.abspath(new_dir):
        return avisos
    for nome in MIGRATED_FILES:
        origem = os.path.join(old_dir, nome)
        destino = os.path.join(new_dir, nome)
        if not os.path.exists(origem) or os.path.exists(destino):
            continue
        try:
            with open(origem, "r", encoding="utf-8") as handle:
                bruto = json.load(handle)  # valida antes de gravar no destino
            limpo, removidos = _strip_secrets(bruto)
            if not os.path.isdir(new_dir):
                os.makedirs(new_dir)
            with open(destino, "w", encoding="utf-8") as handle:
                json.dump(limpo, handle, ensure_ascii=False, indent=2)
        except (OSError, ValueError) as erro:
            avisos.append(
                "não consegui migrar {0} de {1}: {2}".format(nome, old_dir, erro))
            continue
        avisos.append(
            "{0} migrado de {1} — a pasta antiga foi mantida.".format(nome, old_dir))
        if removidos:
            avisos.append(
                "campos com cara de segredo não foram migrados: {0}.".format(
                    ", ".join(sorted(removidos))))
    return avisos


def _strip_secrets(value, removidos=None):
    """Devolve (cópia sem campos de segredo, nomes descartados).

    A migração não pode carregar credencial de uma instalação antiga para a
    nova: o que parece segredo fica para trás, e o usuário é avisado.
    """
    if removidos is None:
        removidos = set()
    if isinstance(value, dict):
        limpo = {}
        for chave, item in value.items():
            if looks_like_secret(str(chave)):
                removidos.add(str(chave))
                continue
            limpo[chave] = _strip_secrets(item, removidos)[0]
        return limpo, removidos
    if isinstance(value, list):
        return [_strip_secrets(item, removidos)[0] for item in value], removidos
    return value, removidos


#: Faixa aceita para o timeout, em segundos.
TIMEOUT_MIN = 5.0
TIMEOUT_MAX = 1800.0


def _valid_timeout(value):
    """Valida o timeout do config. Devolve (valor ou None, aviso ou "").

    `True` é `int` em Python — aceitá-lo daria um timeout de 1 segundo e faria
    toda conversa estourar. Float precisa ser aceito, e valores fora da faixa
    precisam avisar em vez de sumir em silêncio.
    """
    if isinstance(value, bool):
        return None, "timeout inválido no config ({0!r}) — usando o padrão.".format(value)
    if not isinstance(value, (int, float)):
        return None, "timeout inválido no config ({0!r}) — usando o padrão.".format(value)
    numero = float(value)
    if numero < TIMEOUT_MIN or numero > TIMEOUT_MAX:
        return None, (
            "timeout fora da faixa {0:.0f}–{1:.0f}s ({2}) — usando o padrão.".format(
                TIMEOUT_MIN, TIMEOUT_MAX, value))
    return numero, ""


class Config(object):
    """Preferências persistidas, com leitura à prova de arquivo estragado."""

    def __init__(self, path: str = CONFIG_PATH, legacy_dir: str = LEGACY_DIR) -> None:
        self.path = path
        self.data: Dict[str, Any] = _deep_copy(DEFAULTS)
        self.warnings: List[str] = []
        self.writable = True
        # antes de ler: se houver instalação antiga, traz o que ela tinha
        self.warnings.extend(
            migrate_legacy(os.path.dirname(path) or CONFIG_DIR, legacy_dir))
        self.load()

    # -------------------------------------------------------------- leitura

    def load(self) -> None:
        if not os.path.exists(self.path):
            self.save()  # cria com os padrões; se falhar, o aviso já é gravado
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except ValueError:
            self.warnings.append(
                "config inválido em {0} — usando os padrões.".format(self.path)
            )
            return
        except OSError as exc:
            self.warnings.append("não consegui ler o config: {0}".format(exc))
            return

        if not isinstance(raw, dict):
            self.warnings.append("config não é um objeto JSON — usando os padrões.")
            return

        dropped = []
        for key, value in raw.items():
            if looks_like_secret(key):
                dropped.append(key)
                continue
            if key not in DEFAULTS:
                continue  # campo desconhecido: ignorado em silêncio
            if key == "models" and isinstance(value, dict):
                for provider, model in value.items():
                    if isinstance(model, str) and model:
                        self.data["models"][provider] = model
            elif key == "timeout":
                validado, aviso = _valid_timeout(value)
                if aviso:
                    self.warnings.append(aviso)
                if validado is not None:
                    self.data[key] = validado
            elif isinstance(value, type(DEFAULTS[key])):
                self.data[key] = value

        if dropped:
            self.warnings.append(
                "ignorei campos de credencial no config: {0} — "
                "segredos ficam em variáveis de ambiente.".format(", ".join(dropped))
            )

    # --------------------------------------------------------------- escrita

    def save(self) -> bool:
        """Grava as preferências. Nunca levanta exceção: só registra o aviso."""
        payload = {
            key: value
            for key, value in self.data.items()
            if not looks_like_secret(key)
        }
        try:
            directory = os.path.dirname(self.path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        except OSError as exc:
            self.writable = False
            self.warnings.append(
                "não consegui gravar o config ({0}) — as escolhas valem só "
                "para esta sessão.".format(exc)
            )
            return False
        self.writable = True
        return True

    # --------------------------------------------------------------- acesso

    @property
    def provider(self) -> str:
        return str(self.data.get("provider") or DEFAULTS["provider"])

    def set_provider(self, name: str) -> None:
        self.data["provider"] = name
        self.save()

    def model_for(self, provider: str) -> str:
        models = self.data.get("models") or {}
        return str(models.get(provider) or DEFAULTS["models"].get(provider, ""))

    def set_model(self, provider: str, model: str) -> None:
        self.data.setdefault("models", {})[provider] = model
        self.save()

    @property
    def timeout(self) -> float:
        # segunda barreira: mesmo que `data` seja alterado em memória, o que
        # sai daqui está sempre dentro da faixa utilizável
        validado, _aviso = _valid_timeout(self.data.get("timeout"))
        return validado if validado is not None else float(DEFAULTS["timeout"])

    @property
    def workspace(self) -> str:
        return str(self.data.get("workspace") or "")

    def set_workspace(self, path: str) -> None:
        self.data["workspace"] = path
        self.save()

    @property
    def profile(self) -> str:
        """Perfil de política salvo. Vazio ou ausente cai no padrão seguro."""
        return str(self.data.get("profile") or DEFAULTS["profile"])

    def set_profile(self, name: str) -> None:
        self.data["profile"] = name
        self.save()

    def take_warnings(self) -> List[str]:
        """Devolve e limpa os avisos acumulados, para a TUI exibir uma vez."""
        pending, self.warnings = self.warnings, []
        return pending


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return dict((key, _deep_copy(item)) for key, item in value.items())
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    return value
