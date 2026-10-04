# -*- coding: utf-8 -*-
"""
Provider NVIDIA NIM (fallback externo). A chave vem SOMENTE da variável de ambiente NVIDIA_API_KEY (backend).

Para reaproveitar a chamada que o seu server.py já faz, passe-a em `fn`:
    NvidiaProvider(cfg, fn=lambda mensagem, historico, sistema: sua_funcao_nvidia(mensagem))
Sem `fn`, usa a API HTTP padrão do NIM (formato OpenAI), configurada em jarvis_config.py.
"""
import os, urllib.parse
from .base import Provider, ProviderErro, post_json, texto_da_resposta, montar_mensagens

BASE = {"conversation", "text_correction", "summarization", "translation",
        "advanced_reasoning", "complex_code_analysis", "advanced_document_analysis"}


class NvidiaProvider(Provider):
    nome = "nvidia"
    requer_internet = True

    def __init__(self, cfg, fn=None):
        self.cfg, self.fn = cfg, fn
        self.host = urllib.parse.urlparse(cfg["NVIDIA_BASE_URL"]).hostname

    def _chave(self): return os.environ.get("NVIDIA_API_KEY", "")
    def capacidades(self):
        caps = set(BASE)
        if self.cfg.get("NVIDIA_VISION_MODEL") and not self.fn: caps.add("image_analysis")
        return frozenset(caps)
    def disponivel(self): return bool(self.fn or self._chave())

    def generate(self, mensagem, historico=None, sistema=None, imagem=None):
        if self.fn and not imagem: return self.fn(mensagem, historico or [], sistema)
        modelo = self.cfg["NVIDIA_VISION_MODEL"] if imagem else self.cfg["NVIDIA_MODEL"]
        if not self._chave(): raise ProviderErro("NVIDIA_API_KEY ausente", status=401)
        dados = post_json(self.cfg["NVIDIA_BASE_URL"].rstrip("/") + "/chat/completions",
                          {"model": modelo, "messages": montar_mensagens(mensagem, historico, sistema, imagem), "temperature": 0.6, "max_tokens": 1024, "stream": False},
                          headers={"Authorization": "Bearer " + self._chave()}, timeout=self.cfg["TIMEOUT_EXTERNO"])
        return texto_da_resposta(dados)
