# -*- coding: utf-8 -*-
"""
Provider NVIDIA NIM (fallback externo). A chave vem SOMENTE da variável de ambiente NVIDIA_API_KEY (como no server.py original).
Os parâmetros da chamada são os mesmos do server.py original (temperature, max_tokens, reasoning_effort, timeout),
agora configuráveis em jarvis_config.py. Sem chave, disponivel() é False: o servidor inicia normalmente.
"""
import os, urllib.parse
from provider_base import Provider, ProviderErro, post_json, texto_da_resposta, montar_mensagens

BASE = {"conversation", "text_correction", "summarization", "translation",
        "advanced_reasoning", "complex_code_analysis", "advanced_document_analysis"}


class NvidiaProvider(Provider):
    nome = "nvidia"
    requer_internet = True

    def __init__(self, cfg, fn=None):
        self.cfg, self.fn = cfg, fn                       # fn opcional: fn(mensagem, historico, sistema) -> texto
        self.host = urllib.parse.urlparse(cfg["NVIDIA_BASE_URL"]).hostname

    def _chave(self): return os.environ.get("NVIDIA_API_KEY", "")
    def capacidades(self):
        caps = set(BASE)
        if self.cfg.get("NVIDIA_VISION_MODEL") and not self.fn: caps.add("image_analysis")
        return frozenset(caps)
    def disponivel(self): return bool(self.fn or self._chave())

    def generate(self, mensagem, historico=None, sistema=None, imagem=None):
        if self.fn and not imagem: return self.fn(mensagem, historico or [], sistema)
        c = self.cfg
        if not self._chave(): raise ProviderErro("NVIDIA_API_KEY ausente", status=401)
        corpo = {"model": c["NVIDIA_VISION_MODEL"] if imagem else c["NVIDIA_MODEL"],
                 "messages": montar_mensagens(mensagem, historico, sistema, imagem),
                 "max_tokens": c["NVIDIA_MAX_TOKENS"], "temperature": c["NVIDIA_TEMPERATURE"]}
        if c.get("NVIDIA_REASONING_EFFORT"): corpo["reasoning_effort"] = c["NVIDIA_REASONING_EFFORT"]
        dados = post_json(c["NVIDIA_BASE_URL"].rstrip("/") + "/chat/completions", corpo,
                          headers={"Authorization": "Bearer " + self._chave(), "Accept": "application/json"}, timeout=c["TIMEOUT_EXTERNO"])
        return texto_da_resposta(dados)
