# -*- coding: utf-8 -*-
"""
Orquestrador do JARVIS. Prioridade (a primeira etapa que resolver encerra o pedido):
  1. LOCAL       modelo local (se existir, estiver no ar e declarar a capacidade em LOCAL_CAPACIDADES)
  2. FERRAMENTA  ferramentas locais determinísticas (ex.: calculadora)
  3. INTERNET    ferramentas que usam a internet sem IA (FERRAMENTAS_INTERNET; vazio por enquanto)
  4. NVIDIA      IA externa - só com API_FALLBACK ligado, chave configurada, política permitindo e internet disponível
A escolha nunca depende de um erro do modelo. Cada decisão é registrada em memoria_capacidades.json (só metadados).

Uso:  orq = Orquestrador();  r = orq.responder(mensagem, imagem=..., historico=..., sistema=...)
      r = {"resposta", "rota", "capacidade", "provider", "detalhe"}
"""
import json, os, re, socket, tempfile, threading, time

from jarvis_config import carregar
from capacidades import CAPACIDADES, FERRAMENTAS, FERRAMENTAS_INTERNET, classificar, minimizar_memoria, montar_mensagem, normalizar, separar_memoria
from provider_base import ProviderErro
from provider_local import criar_local
from provider_nvidia import NvidiaProvider
from memoria_local import MemoriaLocal

# Mensagens no estilo JARVIS
M_AVISO   = "Senhor, essa tarefa está além das capacidades do meu núcleo local neste momento. Utilizarei o serviço externo apropriado para concluí-la."
M_OFFLINE = "Senhor, essa tarefa depende de um serviço externo, e ele está indisponível agora. Meus recursos locais continuam operando normalmente; retomamos assim que a conexão voltar."
M_FB_OFF  = "Senhor, essa tarefa excede o meu núcleo local, e o uso de serviços externos está desativado. Para permitir, ative API_FALLBACK na configuração."
M_SEM_PRV = "Senhor, essa tarefa excede o meu núcleo local e nenhum serviço externo está configurado. Defina a chave do provider (NVIDIA_API_KEY) no servidor."
M_SEM_CAP = "Senhor, nenhum dos serviços externos configurados consegue realizar essa tarefa. %s"
M_BLOQ    = "Senhor, para isso eu precisaria enviar %s a um serviço externo, e esse envio está desativado por política de privacidade. Para autorizar, ative %s na configuração."
M_ERRO    = "Senhor, o serviço externo não respondeu como esperado. Se o senhor repetir o pedido, tentarei novamente."
M_LOCAL_X = "Senhor, não consegui concluir essa tarefa com os meus recursos locais."


class ContextoLocal:
    """Memória curta em RAM. Guarda somente o necessário para continuidade local."""

    _PADROES_REFERENCIA = (
        "ele", "ela", "eles", "elas", "isso", "isto", "esse", "essa", "esses", "essas",
        "dele", "dela", "deles", "delas", "nele", "nela", "nesse", "nessa",
    )

    _JOGOS_CONHECIDOS = (
        "warzone", "valorant", "minecraft", "fortnite", "apex", "cs2", "csgo",
        "overwatch", "paladins", "fifa", "elden ring", "league of legends", "lol",
    )

    _DOMINIOS = {
        "pc": ("pc", "computador", "processador", "cpu", "gpu", "placa de video", "placa de vídeo", "ram", "ssd", "hdd"),
        "monitor": ("monitor", "hz", "refresh", "resolucao", "resolução", "display"),
        "celular": ("celular", "smartphone", "android", "iphone", "xiaomi", "samsung"),
        "jogos": ("jogo", "game", "valorant", "minecraft", "lol", "fifa", "elden ring", "steam"),
        "internet": ("internet", "site", "navegador", "wifi", "wi-fi", "cloudflare"),
        "investimentos": ("investimento", "investir", "cdi", "selic", "acao", "ações", "cripto", "bitcoin"),
    }

    def __init__(self, maximo=8):
        self.maximo = max(1, int(maximo))
        self.eventos = []
        self.lock = threading.Lock()

    @staticmethod
    def _tokens(texto):
        return [x for x in normalizar(texto).split() if len(x) > 2]

    @classmethod
    def extrair_metadata(cls, texto):
        n = normalizar(texto)
        dominios = []
        for nome, palavras in cls._DOMINIOS.items():
            if any(p in n for p in palavras):
                dominios.append(nome)

        entidades = []
        padroes = (
            r"\b(?:rtx|gtx|rx)\s*\d{3,4}(?:\s*(?:ti|super|xt))?\b",
            r"\b(?:i[3579]|xeon)\s*[- ]?[a-z0-9-]+\b",
            r"\b\d+(?:[.,]\d+)?\s*(?:hz|gb|tb|mb|ddr3|ddr4|ddr5)\b",
        )
        for rx in padroes:
            for m in re.finditer(rx, n):
                valor = m.group(0).strip()
                if valor not in entidades:
                    entidades.append(valor)

        if not entidades:
            # Poucas palavras relevantes, sem copiar a frase inteira.
            stop = {"para", "como", "qual", "quais", "essa", "esse", "isso", "aquela", "aquele", "muito", "mais"}
            entidades = [x for x in cls._tokens(texto) if x not in stop][:5]

        return {"dominios": dominios[:3], "entidades": entidades[:6]}

    def atualizar(self, usuario, resposta, resultado, capacidade, rota, detalhe=None):
        meta = self.extrair_metadata(usuario)
        evento = {
            "usuario": usuario or "",
            "resposta": resposta or "",
            "resultado": resultado or "",
            "capacidade": capacidade or "",
            "rota": rota or "",
            "detalhe": detalhe or "",
            "dominios": meta["dominios"],
            "entidades": meta["entidades"],
        }
        with self.lock:
            self.eventos.append(evento)
            del self.eventos[:-self.maximo]

    @property
    def ultimo(self):
        with self.lock:
            return dict(self.eventos[-1]) if self.eventos else None

    @staticmethod
    def eh_followup_curto(texto):
        n = " ".join((texto or "").strip().lower().split())
        return n in {
            "por que?", "porquê?", "por quê?", "porque?", "e por que?", "e por quê?",
            "como assim?", "como assim isso?", "explique.", "explique", "explica",
            "continue", "continua", "e depois?", "e depois"
        }

    @classmethod
    def eh_followup_referencial(cls, texto):
        n = normalizar(" ".join((texto or "").strip().split()))
        palavras = n.split()
        if not palavras or len(palavras) > 10:
            return False
        if n.startswith(("e ", "mas e ", "e a ", "e o ")):
            return True
        return any(x in palavras for x in cls._PADROES_REFERENCIA)

    def contexto_minimo(self):
        with self.lock:
            recentes = list(reversed(self.eventos[-5:]))
        if not recentes:
            return ""
        dominios = []
        entidades = []
        objetivos = []
        for evento in recentes:
            for item in evento.get("dominios", []):
                if item not in dominios:
                    dominios.append(item)
            for item in evento.get("entidades", []):
                if item not in entidades:
                    entidades.append(item)
            n = normalizar(evento.get("usuario", ""))
            if "competitiv" in n and "competitivo" not in objetivos:
                objetivos.append("competitivo")
            elif ("jog" in n or "jogo" in n) and "jogos" not in objetivos:
                objetivos.append("jogos")
            if len(dominios) >= 3 and len(entidades) >= 6 and len(objetivos) >= 2:
                break
        partes = []
        if dominios:
            partes.append("domínio: " + ", ".join(dominios[:3]))
        if objetivos:
            partes.append("objetivo: " + ", ".join(objetivos[:2]))
        if entidades:
            partes.append("entidades: " + ", ".join(entidades[:6]))
        return "; ".join(partes)

    def eh_seguimento_contextual(self, texto):
        """Detecta continuação curta mesmo sem pronomes explícitos."""
        n = normalizar(" ".join((texto or "").strip().split()))
        partes = n.split()
        if not partes or len(partes) > 8:
            return False
        with self.lock:
            recentes = list(reversed(self.eventos[-5:]))
        if not recentes:
            return False
        ativo_monitor = any("monitor" in e.get("dominios", []) for e in recentes)
        objetivo_jogos = any(
            "competitiv" in normalizar(e.get("usuario", "")) or "jog" in normalizar(e.get("usuario", ""))
            for e in recentes
        )
        jogo_citado = "," in n or any(jogo in n for jogo in self._JOGOS_CONHECIDOS)
        retorno_competitivo = "competitiv" in n or n in {"e para competir", "e pra competir", "e no competitivo"}
        return ativo_monitor and ((retorno_competitivo and objetivo_jogos) or (objetivo_jogos and jogo_citado))

    def resolver_followup(self, texto):
        if not (self.eh_followup_curto(texto) or self.eh_followup_referencial(texto)):
            return None
        u = self.ultimo
        if not u:
            return "Não tenho contexto anterior suficiente para determinar a que o senhor se refere."

        resultado = u["resultado"]
        rota = u["rota"]
        detalhe = u["detalhe"] or ""
        referencial = self.eh_followup_referencial(texto)

        # Perguntas referenciais como "e esse?" usam o assunto anterior,
        # sem confundir com perguntas locais sobre o motivo da resposta.
        if referencial:
            ctx = self.contexto_minimo()
            if ctx:
                return "Entendi que o senhor está se referindo ao contexto anterior (" + ctx + ")."

        if resultado == "copyright_refusal":
            return (
                "Porque não posso reproduzir integralmente uma obra protegida por direitos autorais. "
                "Posso resumir a obra, explicar o significado ou comentar um trecho curto."
            )
        if "blocked_policy" in resultado or "PERMITIR_ENVIO_DE_" in detalhe:
            return "Porque essa tarefa exigiria enviar dados a um serviço externo, e esse envio está bloqueado pela política de privacidade atual."
        if "provider_unsupported" in resultado:
            return "Porque o serviço disponível não oferece a capacidade necessária para essa tarefa."
        if "offline" in resultado or rota == "offline":
            return "Porque a tarefa depende de um serviço externo que está indisponível no momento."
        if "external_ok" in resultado or rota == "externo":
            return "Porque o núcleo local não realizou essa tarefa e o provider externo foi usado como fallback."
        if u["capacidade"] == "self_awareness":
            return "Porque minha resposta anterior descrevia as capacidades que estão disponíveis neste momento."

        return "Estou me referindo à resposta imediatamente anterior. Posso detalhar o ponto específico que o senhor deseja esclarecer."

class Registro:
    """Registro de eventos de capacidade. JSON pequeno, gravação atômica, seguro entre threads. Não grava o texto das mensagens."""
    def __init__(self, caminho, maximo=100):
        self.caminho, self.maximo, self.lock = caminho, maximo, threading.Lock()

    def _ler(self):
        try:
            with open(self.caminho, encoding="utf-8") as f: return json.load(f)
        except (OSError, ValueError): return {"versao": 1, "capacidades": {}, "eventos": []}

    def evento(self, tarefa, resultado, sucesso, fallback=None, ms=0, **extra):
        e = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "tarefa": tarefa, "resultado": resultado, "fallback": fallback, "sucesso": bool(sucesso), "ms": int(ms), **extra}
        with self.lock:
            d = self._ler(); c = d["capacidades"].setdefault(tarefa, {"total": 0, "sucesso": 0, "falha": 0, "por_resultado": {}})
            c["total"] += 1; c["sucesso" if sucesso else "falha"] += 1; c["por_resultado"][resultado] = c["por_resultado"].get(resultado, 0) + 1
            if fallback: c["ultimo_fallback"] = {"provider": fallback, "ts": e["ts"], "sucesso": bool(sucesso)}
            d["eventos"] = (d["eventos"] + [e])[-self.maximo:]
            try:
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.caminho)), suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.caminho)
            except OSError: pass
        return e


class Orquestrador:
    def __init__(self, cfg=None, local=None, externos=None, nvidia_fn=None, internet=None, log_path=None, **sobrescrever):
        self.cfg = cfg or carregar(**sobrescrever)
        self.local = local or criar_local(self.cfg)
        self.externos = externos if externos is not None else {"nvidia": NvidiaProvider(self.cfg, fn=nvidia_fn)}
        self._internet = internet or self._testar_tcp
        self._cache_net = (0.0, False)
        self.registro = Registro(log_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "memoria_capacidades.json"), self.cfg["LOG_MAX_EVENTOS"])
        self.contexto_local = ContextoLocal()
        if self.cfg.get("MEMORIA_PERSISTENTE", True):
            caminho_memoria = self.cfg.get("MEMORIA_LOCAL_PATH") or None
            self.memoria_local = MemoriaLocal(caminho_memoria, self.cfg.get("MAX_MEMORIAS", 100))
        else:
            self.memoria_local = None

    # ---------- utilidades ----------
    @staticmethod
    def _testar_tcp(host):
        try:
            socket.create_connection((host, 443), timeout=3).close(); return True
        except OSError: return False

    def tem_internet(self, host):
        if time.time() < self._cache_net[0]: return self._cache_net[1]
        ok = bool(self._internet(host)); self._cache_net = (time.time() + 15, ok); return ok

    def _provider(self): return self.externos.get(self.cfg["PROVIDER_EXTERNO"])

    def _bloqueio(self, dado):
        c = self.cfg
        if dado == "imagem" and not c["PERMITIR_ENVIO_DE_IMAGEM"]: return M_BLOQ % ("a imagem", "PERMITIR_ENVIO_DE_IMAGEM")
        if dado == "documento" and not c["PERMITIR_ENVIO_DE_DOCUMENTOS"]: return M_BLOQ % ("o documento", "PERMITIR_ENVIO_DE_DOCUMENTOS")
        return None

    @staticmethod
    def _juntar(mensagem, documento):
        return mensagem + "\n\n[Documento]\n" + documento if documento else mensagem

    # ---------- onde cada capacidade seria executada AGORA ----------
    def diagnosticar_capacidade(self, nome):
        """Explica onde uma capacidade pode ser executada agora, sem expor segredos."""
        c = CAPACIDADES.get(nome)
        if not c:
            return {"capacidade": nome, "conhecida": False, "disponivel": False, "rota": "desconhecida", "motivo": "capacidade não registrada"}

        prv = self._provider()
        dado = c.get("dados")
        if c["tipo"] == "frontend":
            return {"capacidade": nome, "conhecida": True, "disponivel": True, "rota": "frontend", "motivo": "executada pelo site"}
        if nome == "self_awareness":
            return {"capacidade": nome, "conhecida": True, "disponivel": True, "rota": "nucleo_local", "motivo": "diagnóstico determinístico do orquestrador"}
        if nome in ("local_dialogue", "copyright_request", "persistent_memory"):
            return {"capacidade": nome, "conhecida": True, "disponivel": True, "rota": "nucleo_local", "motivo": "regra local determinística"}
        if self.local.disponivel() and self.local.suporta(nome) and dado == "texto":
            return {"capacidade": nome, "conhecida": True, "disponivel": True, "rota": "modelo_local", "motivo": "modelo local declara suporte"}
        if nome in FERRAMENTAS:
            return {"capacidade": nome, "conhecida": True, "disponivel": True, "rota": "ferramenta_local", "motivo": "ferramenta determinística local"}
        if nome in FERRAMENTAS_INTERNET:
            online = self.tem_internet("1.1.1.1")
            return {"capacidade": nome, "conhecida": True, "disponivel": online, "rota": "internet" if online else "offline",
                    "motivo": "ferramenta de internet" if online else "sem conexão com a internet",
                    "requer_internet": True, "fallback": "nvidia" if self.cfg["API_FALLBACK"] else None}
        if not self.cfg["API_FALLBACK"]:
            return {"capacidade": nome, "conhecida": True, "disponivel": False, "rota": "bloqueado", "motivo": "fallback externo desativado"}
        if not prv or not prv.disponivel():
            return {"capacidade": nome, "conhecida": True, "disponivel": False, "rota": "indisponivel", "motivo": "provider externo não configurado"}
        if not prv.suporta(nome):
            return {"capacidade": nome, "conhecida": True, "disponivel": False, "rota": "indisponivel", "motivo": "provider externo sem suporte"}
        if self._bloqueio(dado):
            return {"capacidade": nome, "conhecida": True, "disponivel": False, "rota": "bloqueado", "motivo": "política de privacidade bloqueia o envio",
                    "dados_externos": dado}
        if prv.requer_internet and not self.tem_internet(prv.host):
            return {"capacidade": nome, "conhecida": True, "disponivel": False, "rota": "offline", "motivo": "provider externo sem conexão",
                    "requer_internet": True}
        return {"capacidade": nome, "conhecida": True, "disponivel": True, "rota": "externo", "motivo": "provider externo disponível",
                "provider": prv.nome, "dados_externos": dado or "nenhum", "requer_internet": bool(prv.requer_internet)}

    def diagnosticar(self, texto, tem_imagem=False, tem_documento=False):
        """Classifica uma mensagem e explica a rota preferida sem executá-la."""
        cap = classificar(texto, tem_imagem, tem_documento)
        d = self.diagnosticar_capacidade(cap)
        d["texto"] = texto
        d["rota_preferida"] = d["rota"]
        return d

    def _resumo_capacidades(self):
        """Resposta curta e determinística para perguntas sobre as próprias capacidades."""
        nomes = {
            "basic_math": "cálculos simples",
            "unit_conversion": "conversão de unidades",
            "persistent_memory": "memória persistente local",
            "web_search": "pesquisa na internet",
            "conversation": "conversa e tarefas de texto",
            "text_correction": "correção de texto",
            "summarization": "resumos",
            "translation": "traduções",
            "advanced_reasoning": "raciocínio avançado",
            "complex_code_analysis": "análise de código",
            "image_analysis": "análise de imagens",
            "advanced_document_analysis": "análise de documentos",
        }
        linhas = ["Senhor, atualmente disponho de:"]
        for nome, descricao in nomes.items():
            d = self.diagnosticar_capacidade(nome)
            if d["disponivel"]:
                rota = {"modelo_local": "local", "ferramenta_local": "local", "internet": "internet",
                        "externo": "serviço externo", "frontend": "site"}.get(d["rota"], d["rota"])
                linhas.append("- %s (%s)." % (descricao, rota))
        prv = self._provider()
        if not self.local.disponivel():
            linhas.append("Meu modelo local ainda não está disponível.")
        if prv and prv.disponivel():
            linhas.append("Tenho um provider externo configurado para tarefas que exigem mais capacidade.")
        return "\n".join(linhas)

    # ---------- onde cada capacidade seria executada AGORA ----------
    def status(self):
        prv, caps = self._provider(), {}
        for nome, c in CAPACIDADES.items():
            d = self.diagnosticar_capacidade(nome)
            rotulos = {
                "modelo_local": "modelo local",
                "ferramenta_local": "ferramenta local",
                "internet": "ferramenta de internet",
                "externo": "externo (%s)" % (d.get("provider") or prv.nome if prv else "externo"),
                "frontend": "frontend",
                "nucleo_local": "núcleo local",
                "offline": "offline",
                "bloqueado": "bloqueado",
                "indisponivel": "indisponível",
            }
            caps[nome] = {
                "descricao": c["desc"],
                "onde": rotulos.get(d["rota"], d["rota"]),
                "rota": d["rota"],
                "disponivel": d["disponivel"],
                "motivo": d["motivo"],
            }
        return {"modelo_local": self.local.disponivel(), "provider_externo": prv.nome if prv else None, "provider_externo_pronto": bool(prv and prv.disponivel()),
                "modelo_externo": self.cfg.get("NVIDIA_MODEL"),
                "memoria_persistente": bool(self.memoria_local),
                "quantidade_memorias": self.memoria_local.quantidade() if self.memoria_local else 0,
                "capacidades": caps,
                "politica": {k: self.cfg[k] for k in ("API_FALLBACK", "AVISAR_USUARIO", "PERMITIR_ENVIO_DE_IMAGEM", "PERMITIR_ENVIO_DE_DOCUMENTOS", "PERMITIR_MEMORIA_EXTERNA", "PERMITIR_HISTORICO_EXTERNO")}}

    # ---------- fluxo principal ----------
    def responder(self, mensagem, imagem=None, documento=None, historico=None, sistema=None, contexto_local=None):
        t0 = time.time()
        memoria, texto = separar_memoria(mensagem)
        memoria = minimizar_memoria(memoria, texto)            # só a memória relevante à pergunta sobrevive
        cap = classificar(texto, bool(imagem), bool(documento))
        ctx = contexto_local or self.contexto_local

        # Quando há IA local disponível, continuações conversacionais deixam
        # de ser resolvidas por regras de frases e passam ao modelo semântico.
        if cap == "local_dialogue" and self.local.disponivel() and self.local.suporta("conversation"):
            cap = "conversation"

        seguimento_contextual = cap == "conversation" and ctx.eh_seguimento_contextual(texto)
        def fim(resposta, rota, resultado, sucesso, provider=None, fallback=None, detalhe=None):
            self.registro.evento(cap, resultado, sucesso, fallback=fallback, ms=(time.time() - t0) * 1000, has_image=bool(imagem), has_document=bool(documento))
            ctx.atualizar(texto, resposta, resultado, cap, rota, detalhe)
            return {"resposta": resposta, "rota": rota, "capacidade": cap, "provider": provider, "detalhe": detalhe}

        completo = self._juntar(montar_mensagem(memoria, texto), documento)
        contexto_minimo = ctx.contexto_minimo()
        memoria_estruturada = self.memoria_local.contexto(texto) if self.memoria_local else ""
        self.memoria_local.aprender(texto) if self.memoria_local else None

        # Fase 2.2: memória estruturada persistente. O dado fica no J7 e só é
        # anexado a um modelo quando ele estiver rodando localmente ou quando
        # a política permitir memória externa.
        if cap == "persistent_memory":
            if self.memoria_local:
                if self.memoria_local.e_pedido_memoria(texto):
                    return fim(self.memoria_local.resumo(), "local", "memory_summary_ok", True, "nucleo")
                direta = self.memoria_local.resposta_direta(texto)
                if direta:
                    return fim(direta, "local", "memory_lookup_ok", True, "nucleo")
            return fim("Não encontrei essa informação na minha memória persistente local.", "local", "memory_lookup_miss", True, "nucleo")

        # Fase 2: inteligência local antes de modelo, internet ou NVIDIA.
        # Pedidos de reprodução integral e continuidade curta não precisam de API.
        if cap == "copyright_request":
            resp = (
                "Não posso reproduzir integralmente essa obra. "
                "Posso resumir a música, explicar o significado, comentar o contexto "
                "ou analisar um trecho curto fornecido por você."
            )
            return fim(resp, "local", "copyright_refusal", True, "nucleo")

        if cap == "local_dialogue":
            resp = ctx.resolver_followup(texto)
            if not resp:
                n = normalizar(texto)
                if n in {"obrigado", "obrigada", "valeu"}:
                    resp = "À disposição."
                elif n in {"ok", "certo", "entendi", "beleza"}:
                    resp = "Perfeitamente."
                elif n in {"oi", "ola", "bom dia", "boa tarde", "boa noite"}:
                    import datetime
                    h = datetime.datetime.now().hour
                    resp = "Bom dia." if h < 12 else "Boa tarde." if h < 18 else "Boa noite."
            if resp:
                return fim(resp, "local", "local_dialogue_ok", True, "nucleo")
            cap = "conversation"

        # Autoconsciência: diagnóstico determinístico, sem enviar a pergunta à NVIDIA.
        if cap == "self_awareness":
            return fim(self._resumo_capacidades(), "ferramenta", "capability_status_ok", True, "nucleo")

        # 1) LOCAL: modelo local
        local_erro = detalhe_local = None
        if not imagem and not documento and self.local.disponivel() and self.local.suporta(cap):
            try:
                local_mensagem = completo
                blocos = []
                if contexto_minimo and ctx.eh_followup_referencial(texto):
                    blocos.append("Contexto da conversa: " + contexto_minimo)
                if memoria_estruturada:
                    blocos.append(memoria_estruturada)
                if blocos:
                    local_mensagem = "\n\n".join(blocos) + "\n\nPergunta atual: " + texto
                return fim(self.local.generate(local_mensagem, historico, sistema), "local", "local_ok", True, self.local.nome)
            except Exception as e: local_erro, detalhe_local = True, "local: %s" % e
        # Fallback conversacional sem modelo local: só chega aqui depois de dar
        # ao modelo local a primeira oportunidade de interpretar o contexto.
        if self.memoria_local and (seguimento_contextual or not self.local.disponivel()):
            resposta_memoria = self.memoria_local.resposta_contextual(texto, ctx.contexto_minimo())
            if resposta_memoria:
                return fim(resposta_memoria, "local", "memory_contextual_ok", True, "nucleo")

        resultado_local = "local_error" if local_erro else ("local_insufficient" if self.local.disponivel() else "local_unavailable")

        # 2) FERRAMENTA: determinística, instantânea e sem rede
        if cap in FERRAMENTAS:
            r = FERRAMENTAS[cap](texto)
            if r: return fim(r, "ferramenta", "tool_ok", True, "ferramenta")

        # 3) INTERNET: ferramentas sem IA
        if cap in FERRAMENTAS_INTERNET and self.tem_internet("1.1.1.1"):
            try: r = FERRAMENTAS_INTERNET[cap](texto)
            except Exception: r = None
            if r:
                # A busca encontrou dados; quando possível, o provider externo apenas
                # interpreta esses resultados. Nenhum histórico ou memória é enviado.
                if cap == "web_search":
                    prv = self._provider()
                    if self.cfg["API_FALLBACK"] and prv and prv.disponivel() and self.tem_internet(prv.host):
                        prompt = (
                            "Responda à pergunta do usuário usando SOMENTE as referências abaixo. "
                            "Não invente fatos, datas ou fontes. Se as fontes forem insuficientes, "
                            "diga isso claramente. Seja conciso, em português do Brasil, no estilo "
                            "JARVIS. Depois da resposta, liste as fontes realmente usadas.\\n\\n"
                            "PERGUNTA DO USUÁRIO:\n" + texto + "\\n\\n"
                            "REFERÊNCIAS ENCONTRADAS:\n" + r
                        )
                        sistema_busca = "Você é o componente de síntese de resultados web do JARVIS. Use apenas o conteúdo fornecido; não faça uma segunda busca."
                        try:
                            resp = prv.generate(prompt, [], sistema_busca)
                            return fim(resp, "internet+externo", "internet_sintese_ok", True, prv.nome, prv.nome,
                                        "Busca web feita pelo JARVIS; NVIDIA usada somente para sintetizar os resultados.")
                        except Exception:
                            pass
                return fim(r, "internet", "internet_tool_ok", True, "internet")

        # 4) NVIDIA: fallback externo
        c = CAPACIDADES[cap]; prv = self._provider()
        if not self.cfg["API_FALLBACK"]: return fim(M_LOCAL_X if local_erro else M_FB_OFF, "bloqueado", resultado_local + "+fallback_off", False, detalhe=detalhe_local)
        if not prv or not prv.disponivel(): return fim(M_SEM_PRV, "bloqueado", resultado_local + "+no_provider", False, detalhe="NVIDIA_API_KEY ausente")
        if not prv.suporta(cap):
            dica = "Defina um modelo de visão (NVIDIA_VISION_MODEL) para habilitar a análise de imagens." if cap == "image_analysis" else ""
            return fim(M_SEM_CAP % dica, "bloqueado", resultado_local + "+provider_unsupported", False, prv.nome)
        bloq = self._bloqueio(c.get("dados"))
        if bloq: return fim(bloq, "bloqueado", resultado_local + "+blocked_policy", False, prv.nome)
        if prv.requer_internet and not self.tem_internet(prv.host): return fim(M_OFFLINE, "offline", resultado_local + "+offline", False, prv.nome, prv.nome, "sem internet")

        enviar = completo if self.cfg["PERMITIR_MEMORIA_EXTERNA"] else self._juntar(texto, documento)
        if self.cfg["PERMITIR_HISTORICO_EXTERNO"] and ctx.eh_followup_referencial(texto) and contexto_minimo:
            enviar = "Contexto relevante da conversa: " + contexto_minimo + "\n\nPergunta atual: " + enviar
        if self.cfg["PERMITIR_MEMORIA_EXTERNA"] and memoria_estruturada:
            enviar = memoria_estruturada + "\n\n" + enviar
        # Privacidade: o provider externo recebe o histórico somente quando explicitamente permitido.
        historico_externo = historico if self.cfg["PERMITIR_HISTORICO_EXTERNO"] else []
        try:
            resp = prv.generate(enviar, historico_externo, sistema, imagem=imagem)
        except ProviderErro as e:
            if e.offline: return fim(M_OFFLINE, "offline", resultado_local + "+offline", False, prv.nome, prv.nome, str(e))
            return fim(M_ERRO, "erro", resultado_local + "+external_error_%s" % (e.status or "x"), False, prv.nome, prv.nome, "%s (status %s)" % (e, e.status))
        except Exception as e:
            return fim(M_ERRO, "erro", resultado_local + "+external_error", False, prv.nome, prv.nome, str(e))
        avisar = self.cfg["AVISAR_USUARIO"] and (c.get("aviso") or self.cfg["AVISAR_EM_TEXTO_SIMPLES"])
        return fim((M_AVISO + "\n\n" + resp) if avisar else resp, "externo", resultado_local + "+external_ok", True, prv.nome, prv.nome)
