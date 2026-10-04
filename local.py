# -*- coding: utf-8 -*-
"""
Modelo LOCAL (J7 Prime / Termux). O orquestrador só conhece a interface LocalModel.generate();
trocar o modelo/runtime = trocar a classe criada em criar_local(), sem tocar no orquestrador.

Implementação real incluída: LocalOpenAICompat fala com qualquer servidor local no formato OpenAI
(llama.cpp `llama-server`, que roda no Termux, ou outros). Sem LOCAL_MODEL_URL, usa LocalNulo (indisponível).
"""
import time, urllib.request
from .base import Provider, ProviderErro, post_json, texto_da_resposta, montar_mensagens


class LocalModel(Provider):
    """Interface do modelo local: implemente disponivel() e generate()."""
    nome = "local"


class LocalNulo(LocalModel):
    """Nenhum modelo local instalado ainda (estado atual do projeto)."""
    nome = "local-nenhum"
    def disponivel(self): return False


class LocalOpenAICompat(LocalModel):
    def __init__(self, url, modelo="local", capacidades=(), timeout=90):
        self.url, self.modelo, self._caps, self.timeout = url.rstrip("/"), modelo, frozenset(capacidades), timeout
        self._ok, self._ate = False, 0.0
        self.nome = "local-" + modelo

    def capacidades(self): return self._caps

    def disponivel(self):            # teste rápido, com cache de 10 s
        if time.time() < self._ate: return self._ok
        for caminho in ("/health", "/v1/models"):
            try:
                with urllib.request.urlopen(self.url + caminho, timeout=1.5) as r: self._ok = r.status == 200
                if self._ok: break
            except Exception: self._ok = False
        self._ate = time.time() + 10
        return self._ok

    def generate(self, mensagem, historico=None, sistema=None, imagem=None):
        if imagem: raise ProviderErro("modelo local sem visão")
        dados = post_json(self.url + "/v1/chat/completions",
                          {"model": self.modelo, "messages": montar_mensagens(mensagem, historico, sistema), "temperature": 0.6, "stream": False},
                          timeout=self.timeout)
        return texto_da_resposta(dados)


def criar_local(cfg):
    if cfg.get("LOCAL_MODEL_URL"):
        return LocalOpenAICompat(cfg["LOCAL_MODEL_URL"], cfg.get("LOCAL_MODEL_NOME", "local"), cfg.get("LOCAL_CAPACIDADES", []), cfg.get("TIMEOUT_LOCAL", 90))
    return LocalNulo()
