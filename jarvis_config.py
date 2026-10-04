# -*- coding: utf-8 -*-
"""
Configuração central do JARVIS híbrido (local-first).
Ordem de prioridade:  padrões abaixo  <  jarvis_config.json (opcional, mesma pasta)  <  variáveis de ambiente JARVIS_<NOME>
Exemplo:  export JARVIS_PERMITIR_ENVIO_DE_IMAGEM=true

SEGREDOS (chaves de API) NUNCA ficam aqui nem no frontend: somente em variáveis de ambiente do backend
(ex.: NVIDIA_API_KEY). Este arquivo não lê nem guarda chaves.
"""
import json, os

PADRAO = {
    # ---- política de uso da IA externa ----
    "API_FALLBACK": True,                     # False = nunca usar IA externa
    "AVISAR_USUARIO": True,                   # avisa (no estilo JARVIS) quando vai usar o serviço externo em tarefas avançadas
    "AVISAR_EM_TEXTO_SIMPLES": False,         # True = avisa também em conversa/texto comum
    "PERMITIR_ENVIO_DE_IMAGEM": False,        # imagens só saem do aparelho com esta permissão
    "PERMITIR_ENVIO_DE_DOCUMENTOS": False,    # idem para documentos
    "PERMITIR_MEMORIA_EXTERNA": True,         # False = os fatos da memória local NÃO vão para a IA externa
    # ---- providers ----
    "PROVIDER_EXTERNO": "nvidia",
    "LOCAL_MODEL_URL": "",                    # ex.: http://127.0.0.1:8088  (llama-server do llama.cpp). Vazio = sem modelo local
    "LOCAL_MODEL_NOME": "local",
    "LOCAL_CAPACIDADES": ["conversation", "text_correction", "summarization", "translation"],
    "NVIDIA_BASE_URL": "https://integrate.api.nvidia.com/v1",
    "NVIDIA_MODEL": "nvidia/nemotron-3-ultra-550b-a55b",
    "NVIDIA_VISION_MODEL": "",                # vazio = sem análise de imagem externa
    # ---- limites ----
    "TIMEOUT_EXTERNO": 60, "TIMEOUT_LOCAL": 90, "MAX_HISTORICO": 20, "LOG_MAX_EVENTOS": 100,
}

def _converter(valor, modelo):
    if isinstance(modelo, bool): return str(valor).strip().lower() in ("1", "true", "sim", "yes", "on")
    if isinstance(modelo, int): return int(valor)
    if isinstance(modelo, list): return [x.strip() for x in str(valor).split(",") if x.strip()]
    return str(valor)

def carregar(arquivo=None, ambiente=None, **sobrescrever):
    cfg = dict(PADRAO)
    arquivo = arquivo or os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_config.json")
    try:
        with open(arquivo, encoding="utf-8") as f:
            for k, v in json.load(f).items():
                if k in cfg: cfg[k] = v
    except (OSError, ValueError):
        pass
    env = os.environ if ambiente is None else ambiente
    for k, modelo in PADRAO.items():
        if "JARVIS_" + k in env:
            try: cfg[k] = _converter(env["JARVIS_" + k], modelo)
            except ValueError: pass
    cfg.update(sobrescrever)
    return cfg
