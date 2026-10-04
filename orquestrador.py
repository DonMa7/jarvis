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
import json, os, socket, tempfile, threading, time

from jarvis_config import carregar
from capacidades import CAPACIDADES, FERRAMENTAS, FERRAMENTAS_INTERNET, classificar, minimizar_memoria, montar_mensagem, separar_memoria
from provider_base import ProviderErro
from provider_local import criar_local
from provider_nvidia import NvidiaProvider

# Mensagens no estilo JARVIS
M_AVISO   = "Senhor, essa tarefa está além das capacidades do meu núcleo local neste momento. Utilizarei o serviço externo apropriado para concluí-la."
M_OFFLINE = "Senhor, essa tarefa depende de um serviço externo, e ele está indisponível agora. Meus recursos locais continuam operando normalmente; retomamos assim que a conexão voltar."
M_FB_OFF  = "Senhor, essa tarefa excede o meu núcleo local, e o uso de serviços externos está desativado. Para permitir, ative API_FALLBACK na configuração."
M_SEM_PRV = "Senhor, essa tarefa excede o meu núcleo local e nenhum serviço externo está configurado. Defina a chave do provider (NVIDIA_API_KEY) no servidor."
M_SEM_CAP = "Senhor, nenhum dos serviços externos configurados consegue realizar essa tarefa. %s"
M_BLOQ    = "Senhor, para isso eu precisaria enviar %s a um serviço externo, e esse envio está desativado por política de privacidade. Para autorizar, ative %s na configuração."
M_ERRO    = "Senhor, o serviço externo não respondeu como esperado. Se o senhor repetir o pedido, tentarei novamente."
M_LOCAL_X = "Senhor, não consegui concluir essa tarefa com os meus recursos locais."


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
    def status(self):
        prv, caps = self._provider(), {}
        for nome, c in CAPACIDADES.items():
            if c["tipo"] == "frontend": onde = "frontend"
            elif self.local.disponivel() and self.local.suporta(nome) and c.get("dados") == "texto": onde = "modelo local"
            elif nome in FERRAMENTAS: onde = "ferramenta local"
            elif nome in FERRAMENTAS_INTERNET: onde = "ferramenta de internet"
            elif not self.cfg["API_FALLBACK"]: onde = "indisponível (fallback externo desativado)"
            elif not prv or not prv.disponivel(): onde = "indisponível (sem provider externo configurado)"
            elif not prv.suporta(nome): onde = "indisponível (provider sem suporte)"
            elif self._bloqueio(c.get("dados")): onde = "bloqueado por política de privacidade"
            else: onde = "externo (%s)" % prv.nome
            caps[nome] = {"descricao": c["desc"], "onde": onde}
        return {"modelo_local": self.local.disponivel(), "provider_externo": prv.nome if prv else None, "provider_externo_pronto": bool(prv and prv.disponivel()),
                "capacidades": caps, "politica": {k: self.cfg[k] for k in ("API_FALLBACK", "AVISAR_USUARIO", "PERMITIR_ENVIO_DE_IMAGEM", "PERMITIR_ENVIO_DE_DOCUMENTOS", "PERMITIR_MEMORIA_EXTERNA", "PERMITIR_HISTORICO_EXTERNO")}}

    # ---------- fluxo principal ----------
    def responder(self, mensagem, imagem=None, documento=None, historico=None, sistema=None):
        t0 = time.time()
        memoria, texto = separar_memoria(mensagem)
        memoria = minimizar_memoria(memoria, texto)            # só a memória relevante à pergunta sobrevive
        cap = classificar(texto, bool(imagem), bool(documento))
        def fim(resposta, rota, resultado, sucesso, provider=None, fallback=None, detalhe=None):
            self.registro.evento(cap, resultado, sucesso, fallback=fallback, ms=(time.time() - t0) * 1000, has_image=bool(imagem), has_document=bool(documento))
            return {"resposta": resposta, "rota": rota, "capacidade": cap, "provider": provider, "detalhe": detalhe}

        completo = self._juntar(montar_mensagem(memoria, texto), documento)

        # 1) LOCAL: modelo local
        local_erro = detalhe_local = None
        if not imagem and not documento and self.local.disponivel() and self.local.suporta(cap):
            try: return fim(self.local.generate(completo, historico, sistema), "local", "local_ok", True, self.local.nome)
            except Exception as e: local_erro, detalhe_local = True, "local: %s" % e
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

        enviar = completo if self.cfg["PERMITIR_MEMORIA_EXTERNA"] else self._juntar(texto, documento)   # política: sem memória pessoal na NVIDIA
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
