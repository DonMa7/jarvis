# -*- coding: utf-8 -*-
"""
Modelo LOCAL. O orquestrador só conhece a interface LocalModel (disponivel / suporta / generate).
Trocar de modelo ou de runtime = mudar a classe criada em criar_local(), sem tocar no orquestrador.

NENHUM modelo é instalado por este projeto. Sem LOCAL_MODEL_URL, usa-se LocalNulo (indisponível) e o JARVIS
funciona só com ferramentas locais + NVIDIA. Se algum dia houver um servidor no formato OpenAI (llama.cpp
`llama-server`, no J7 ou em outro aparelho/PC da rede), basta apontar LOCAL_MODEL_URL para ele.
"""
import time, urllib.request
from provider_base import Provider, ProviderErro, post_json, texto_da_resposta, montar_mensagens


class LocalModel(Provider):
    """Interface do modelo local: implemente disponivel() e generate()."""
    nome = "local"


class LocalNulo(LocalModel):
    nome = "local-nenhum"
    def disponivel(self): return False


class LocalOpenAICompat(LocalModel):
    def __init__(self, url, modelo="local", capacidades=(), timeout=90):
        self.url, self.modelo, self._caps, self.timeout = url.rstrip("/"), modelo, frozenset(capacidades), timeout
        self._ok, self._ate = False, 0.0
        self.nome = "local-" + modelo

    def capacidades(self): return self._caps

    def disponivel(self):            # teste rápido (1,5 s), com cache de 10 s: um servidor desligado não atrasa as respostas
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
