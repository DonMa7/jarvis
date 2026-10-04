# -*- coding: utf-8 -*-
"""Testes reproduzíveis (sem internet, sem chaves, sem modelo local):  python testes_orquestrador.py"""
import http.client, json, os, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

import capacidades
from capacidades import classificar, ferramenta_conversao, minimizar_memoria, separar_memoria
from jarvis_config import carregar
from orquestrador import M_OFFLINE, M_SEM_PRV, Orquestrador
from memoria_local import MemoriaLocal
from provider_base import Provider, ProviderErro
from provider_local import LocalModel

MEM = "Informações que o usuário pediu para você lembrar (use apenas se forem relevantes):\n- meu monitor: 100 Hz\n- cor favorita: azul\n\nMensagem do usuário: "


class FakeLocal(LocalModel):
    nome = "local-fake"
    def __init__(self, ligado=True, quebra=False, extras=()): self.ligado, self.quebra, self.extras, self.chamadas = ligado, quebra, set(extras), 0
    def capacidades(self): return frozenset({"conversation", "text_correction", "summarization", "translation"} | self.extras)
    def disponivel(self): return self.ligado
    def generate(self, mensagem, historico=None, sistema=None, imagem=None):
        self.chamadas += 1
        if self.quebra: raise ProviderErro("falhou")
        return "LOCAL: " + mensagem[-30:]

class FakeExterno(Provider):
    nome, requer_internet, host = "fake-ext", True, "exemplo.invalid"
    def __init__(self, visao=False, erro=None, atraso=0): self.visao, self.erro, self.atraso, self.chamadas, self.ultimo, self.hist = visao, erro, atraso, 0, None, None
    def capacidades(self):
        base = {"conversation", "text_correction", "summarization", "translation", "advanced_reasoning", "complex_code_analysis", "advanced_document_analysis"}
        return frozenset(base | ({"image_analysis"} if self.visao else set()))
    def disponivel(self): return True
    def generate(self, mensagem, historico=None, sistema=None, imagem=None):
        self.chamadas += 1; self.ultimo = (mensagem, imagem); self.hist = historico
        if self.atraso: time.sleep(self.atraso)
        if self.erro: raise self.erro
        return "EXTERNO: " + mensagem[-30:]


def montar(local=None, ext=None, internet=True, **cfg):
    log = os.path.join(tempfile.mkdtemp(), "mc.json"); ext = ext or FakeExterno()
    return Orquestrador(cfg=carregar(arquivo="/nao/existe.json", ambiente={}, **cfg), local=local or FakeLocal(False), externos={"nvidia": ext},
                        internet=lambda h: internet, log_path=log), ext, log


class Testes(unittest.TestCase):
    # ---------- núcleo ----------
    def test_classificador(self):
        casos = {"JARVIS, o que você consegue fazer?": "self_awareness", "JARVIS, pesquise na internet quem é o atual presidente do Brasil": "web_search", "quanto é 25 vezes 18": "basic_math", "20% de 500": "basic_math", "corrija a ortografia deste texto": "text_correction", "resuma isto": "summarization",
                 "traduza para inglês": "translation", "analise este código python": "complex_code_analysis", "oi, tudo bem?": "conversation", "quanto é a capital da França": "conversation", "por quê?": "local_dialogue", "como assim?": "local_dialogue", "obrigado": "local_dialogue", "me mande a letra de Asa Branca": "copyright_request", "queria a letra de Asa Branca": "copyright_request", "gostaria da letra de Asa Branca": "copyright_request", "10 km para milhas": "unit_conversion", "o que você lembra de mim": "persistent_memory", "qual é meu monitor": "persistent_memory"}
        for t, c in casos.items(): self.assertEqual(classificar(t), c, t)
        self.assertEqual(classificar("o que é isso?", tem_imagem=True), "image_analysis"); self.assertEqual(classificar("leia", tem_documento=True), "advanced_document_analysis")
        self.assertIn("web_search", capacidades.FERRAMENTAS_INTERNET)
        self.assertNotIn("web_search", capacidades.FERRAMENTAS)

    def test_diagnostico_e_autoconsciencia(self):
        o, ext, _ = montar()
        d = o.diagnosticar("JARVIS, pesquise na internet o preço atual")
        self.assertEqual((d["capacidade"], d["rota"], d["disponivel"]), ("web_search", "internet", True))
        self.assertTrue(d["requer_internet"])
        self.assertEqual(d["fallback"], "nvidia")

        d = o.diagnosticar("quanto é 25 vezes 18")
        self.assertEqual((d["capacidade"], d["rota"]), ("basic_math", "ferramenta_local"))

        r = o.responder("JARVIS, o que você consegue fazer?")
        self.assertEqual((r["rota"], r["capacidade"], ext.chamadas), ("ferramenta", "self_awareness", 0))
        self.assertIn("pesquisa na internet", r["resposta"])
        self.assertIn("provider externo", r["resposta"].lower())

        st = o.status()
        self.assertEqual(st["modelo_externo"], o.cfg["NVIDIA_MODEL"])
        self.assertIn("rota", st["capacidades"]["web_search"])
        self.assertIn("motivo", st["capacidades"]["conversation"])

    def test_fase2_dialogo_local_e_continuidade(self):
        o, ext, _ = montar()
        r = o.responder("me mande a letra de Asa Branca")
        self.assertEqual(r["rota"], "local")
        self.assertEqual(r["capacidade"], "copyright_request")
        self.assertEqual(ext.chamadas, 0)
        self.assertIn("reproduzir integralmente", r["resposta"])

        r = o.responder("por quê?")
        self.assertEqual((r["rota"], r["capacidade"], ext.chamadas), ("local", "local_dialogue", 0))
        self.assertIn("direitos autorais", r["resposta"])

        r = o.responder("obrigado")
        self.assertEqual((r["rota"], r["capacidade"]), ("local", "local_dialogue"))
        self.assertEqual(r["resposta"], "À disposição.")

        r = o.responder("oi")
        self.assertEqual((r["rota"], r["capacidade"]), ("local", "local_dialogue"))

    def test_fase2_contexto_nao_vaza_para_externo(self):
        o, ext, _ = montar()
        o.responder("me mande a letra de Asa Branca")
        r = o.responder("por quê?")
        self.assertEqual(r["rota"], "local")
        self.assertEqual(ext.chamadas, 0)

    def test_ia_local_assume_conversacao_contextual(self):
        caminho = os.path.join(tempfile.mkdtemp(), "memoria.json")
        mem = MemoriaLocal(caminho)
        mem.lembrar("monitor", "monitor", "100 Hz")
        local = FakeLocal(True)
        o, ext, _ = montar(local=local, MEMORIA_LOCAL_PATH=caminho)
        r = o.responder("por quê?")
        self.assertEqual((r["rota"], r["capacidade"], local.chamadas, ext.chamadas), ("local", "conversation", 1, 0))

    def test_fase22_memoria_estruturada_persistente(self):
        caminho = os.path.join(tempfile.mkdtemp(), "memoria.json")
        mem = MemoriaLocal(caminho)
        self.assertTrue(mem.lembrar("monitor", "monitor", "100 Hz"))
        self.assertTrue(mem.lembrar("pc", "gpu", "RTX 2060"))
        self.assertIn("100 Hz", mem.contexto("qual a taxa do meu monitor"))
        mem2 = MemoriaLocal(caminho)
        self.assertEqual(mem2.todas()["pc"]["gpu"], "RTX 2060")

        local = FakeLocal(True)
        o, ext, _ = montar(local=local, MEMORIA_LOCAL_PATH=caminho)
        r = o.responder("ele é bom para jogar?")
        self.assertEqual((r["rota"], local.chamadas, ext.chamadas), ("local", 1, 0))
        self.assertNotEqual(r["resposta"], "Não encontrei essa informação na minha memória persistente local.")

        o, ext, _ = montar(MEMORIA_LOCAL_PATH=caminho)
        r = o.responder("qual é meu monitor")
        self.assertEqual((r["rota"], r["capacidade"], ext.chamadas), ("local", "persistent_memory", 0))
        self.assertIn("100 Hz", r["resposta"])

        r = o.responder("o que você lembra de mim")
        self.assertEqual(r["rota"], "local")
        self.assertIn("RTX 2060", r["resposta"])

        o, ext, _ = montar(MEMORIA_LOCAL_PATH=caminho)
        o.responder("Qual a vantagem de 120 Hz para meu monitor?")
        self.assertEqual(ext.chamadas, 1)
        self.assertNotIn("RTX 2060", ext.ultimo[0])

        r = o.responder("meu monitor")
        self.assertEqual((r["rota"], r["capacidade"], ext.chamadas), ("local", "persistent_memory", 1))
        self.assertIn("100 Hz", r["resposta"])

        r = o.responder("meu processador é um i5 3570k")
        self.assertEqual(r["capacidade"], "conversation")
        self.assertNotIn("monitor", r["resposta"].lower())

        r = o.responder("ele é bom para jogar?")
        self.assertEqual((r["rota"], r["capacidade"], ext.chamadas), ("local", "local_dialogue", 1))
        self.assertIn("100 Hz", r["resposta"])
        self.assertIn("144 Hz", r["resposta"])

        r = o.responder("e para competitivo?")
        self.assertEqual((r["rota"], r["capacidade"], ext.chamadas), ("local", "persistent_memory", 1))
        self.assertIn("jogos competitivos", r["resposta"])

        r = o.responder("warzone, valorant")
        self.assertEqual((r["rota"], r["capacidade"], ext.chamadas), ("local", "persistent_memory", 1))
        self.assertIn("Warzone", r["resposta"])
        self.assertIn("Valorant", r["resposta"])

        o, ext, _ = montar(MEMORIA_LOCAL_PATH=caminho, PERMITIR_MEMORIA_EXTERNA=True)
        o.responder("Qual a vantagem de 120 Hz para meu monitor?")
        self.assertIn("100 Hz", ext.ultimo[0])

    def test_fase2_contexto_semantico_e_conversao(self):
        self.assertEqual(ferramenta_conversao("10 km para milhas"), "O resultado é 6.213711922 milhas.")
        self.assertEqual(classificar("E o que mais?"), "conversation")
        self.assertEqual(classificar("E a 2060?"), "local_dialogue")
        o, ext, _ = montar()
        o.responder("Meu monitor é 100 Hz.")
        self.assertEqual(o.contexto_local.contexto_minimo(), "domínio: monitor; entidades: 100 hz")
        r = o.responder("e esse?")
        self.assertEqual(r["rota"], "local")
        self.assertEqual(ext.chamadas, 1)
        self.assertIn("monitor", r["resposta"].lower())

    def test_ferramenta_local_sem_api(self):
        o, ext, _ = montar()
        for msg, esperado in (("quanto é 25 vezes 18?", "450"), ("calcule (2+3)*4", "20"), ("quanto é 20 por cento de 500", "100")):
            r = o.responder(msg); self.assertEqual(r["rota"], "ferramenta"); self.assertIn(esperado, r["resposta"])
        self.assertEqual(ext.chamadas, 0)

    def test_prioridade_local_ferramenta_internet_nvidia(self):
        # 1) LOCAL vence quando existe e declara a capacidade
        loc = FakeLocal(True); o, ext, _ = montar(local=loc); r = o.responder("Como está o dia?"); self.assertEqual((r["rota"], loc.chamadas, ext.chamadas), ("local", 1, 0))
        loc = FakeLocal(True, extras={"basic_math"}); o, ext, _ = montar(local=loc); self.assertEqual(o.responder("2+2")["rota"], "local")      # LOCAL antes de FERRAMENTA
        # 2) sem modelo local: FERRAMENTA
        o, ext, _ = montar(); self.assertEqual(o.responder("2+2")["rota"], "ferramenta")
        # 2b) busca web: INTERNET antes da NVIDIA
        capacidades.FERRAMENTAS_INTERNET["web_search"] = lambda t: "WEB: resultado"
        try:
            o, ext, _ = montar(); r = o.responder("JARVIS, pesquise na internet quem é o presidente do Brasil")
            self.assertEqual((r["rota"], r["provider"], ext.chamadas), ("internet+externo", "fake-ext", 1))
            self.assertTrue(r["resposta"].startswith("EXTERNO"))
            self.assertIn("WEB: resultado", ext.ultimo[0])
            self.assertEqual(ext.hist, [])
        finally:
            capacidades.FERRAMENTAS_INTERNET["web_search"] = capacidades.ferramenta_busca_web
        # 3) INTERNET (sem IA) antes da NVIDIA
        capacidades.FERRAMENTAS_INTERNET["conversation"] = lambda t: "WEB: ok"
        try:
            o, ext, _ = montar(); r = o.responder("Como está o dia?"); self.assertEqual((r["rota"], ext.chamadas), ("internet", 0))
            o, ext, _ = montar(internet=False); self.assertEqual(o.responder("Como está o dia?")["rota"], "offline")                           # sem internet: nem tool nem NVIDIA
        finally:
            capacidades.FERRAMENTAS_INTERNET.clear()
            capacidades.FERRAMENTAS_INTERNET["web_search"] = capacidades.ferramenta_busca_web
        # 4) NVIDIA por último
        o, ext, _ = montar(); r = o.responder("Como está o dia?"); self.assertEqual((r["rota"], ext.chamadas), ("externo", 1)); self.assertTrue(r["resposta"].startswith("EXTERNO"))

    def test_erro_local_cai_para_externo(self):
        loc = FakeLocal(True, quebra=True); o, ext, log = montar(local=loc); r = o.responder("Qual é o estado atual do núcleo?")
        self.assertEqual((r["rota"], ext.chamadas), ("externo", 1)); self.assertIn("local_error+external_ok", json.load(open(log))["eventos"][-1]["resultado"])

    def test_imagem_e_documento_exigem_permissao(self):
        o, ext, _ = montar(ext=FakeExterno(visao=True)); r = o.responder("Foto?", imagem="AAAA")
        self.assertEqual((r["rota"], ext.chamadas), ("bloqueado", 0)); self.assertIn("PERMITIR_ENVIO_DE_IMAGEM", r["resposta"])
        o, ext, _ = montar(ext=FakeExterno(visao=True), PERMITIR_ENVIO_DE_IMAGEM=True); r = o.responder("Foto?", imagem="AAAA")
        self.assertEqual((r["rota"], ext.chamadas, ext.ultimo[1]), ("externo", 1, "AAAA")); self.assertIn("serviço externo apropriado", r["resposta"])
        o, ext, _ = montar(PERMITIR_ENVIO_DE_IMAGEM=True); r = o.responder("Analise", imagem="AAAA"); self.assertIn("NVIDIA_VISION_MODEL", r["resposta"])
        o, ext, _ = montar(); self.assertEqual(o.responder("Resuma", documento="texto")["rota"], "bloqueado")
        o, ext, _ = montar(PERMITIR_ENVIO_DE_DOCUMENTOS=True); o.responder("Resuma", documento="texto"); self.assertIn("[Documento]", ext.ultimo[0])

    def test_offline_e_falhas_do_provider(self):
        o, ext, _ = montar(internet=False); r = o.responder("Qual é o estado atual do núcleo?"); self.assertEqual((r["rota"], ext.chamadas, r["resposta"]), ("offline", 0, M_OFFLINE)); self.assertEqual(o.responder("quanto é 2+2")["rota"], "ferramenta")
        self.assertEqual(montar(ext=FakeExterno(erro=ProviderErro("x", offline=True)))[0].responder("Qual é o estado atual do núcleo?")["rota"], "offline")
        r = montar(ext=FakeExterno(erro=ProviderErro("x", status=500)))[0].responder("Qual é o estado atual do núcleo?"); self.assertEqual(r["rota"], "erro"); self.assertIn("500", r["detalhe"])
        o, ext, _ = montar(API_FALLBACK=False); r = o.responder("Qual é o estado atual do núcleo?"); self.assertEqual((r["rota"], ext.chamadas), ("bloqueado", 0)); self.assertIn("API_FALLBACK", r["resposta"])

    def test_memoria_so_o_necessario_vai_para_a_nvidia(self):
        self.assertEqual(separar_memoria(MEM + "qual a taxa do meu monitor?")[1], "qual a taxa do meu monitor?")
        mem, txt = separar_memoria(MEM + "qual a taxa do meu monitor?"); m = minimizar_memoria(mem, txt); self.assertIn("100 Hz", m); self.assertNotIn("azul", m)

        # Privacidade por padrão: mesmo a memória relevante não sai para a NVIDIA.
        o, ext, _ = montar(); o.responder(MEM + "qual a taxa do meu monitor?")
        self.assertNotIn("100 Hz", ext.ultimo[0]); self.assertNotIn("azul", ext.ultimo[0])

        # Memória relevante pode ser liberada explicitamente.
        o, ext, _ = montar(PERMITIR_MEMORIA_EXTERNA=True); o.responder(MEM + "qual a taxa do meu monitor?")
        self.assertIn("100 Hz", ext.ultimo[0]); self.assertNotIn("azul", ext.ultimo[0])

        # Memória irrelevante nunca deve entrar, mesmo com permissão explícita.
        o, ext, _ = montar(PERMITIR_MEMORIA_EXTERNA=True); o.responder(MEM + "me conte uma curiosidade sobre o Brasil")
        self.assertNotIn("100 Hz", ext.ultimo[0]); self.assertNotIn("azul", ext.ultimo[0])

        # Política explícita continua garantindo que a memória nunca seja enviada.
        o, ext, _ = montar(PERMITIR_MEMORIA_EXTERNA=False); o.responder(MEM + "qual a taxa do meu monitor?")
        self.assertNotIn("100 Hz", ext.ultimo[0])

    def test_registro_so_tem_metadados_e_status(self):
        o, _, log = montar(); o.responder("segredo-ultra-privado 123"); d = json.load(open(log))
        self.assertNotIn("segredo-ultra-privado", json.dumps(d)); self.assertTrue(d["eventos"][-1]["sucesso"])
        c = o.status()["capacidades"]; self.assertEqual((c["basic_math"]["onde"], c["memory"]["onde"]), ("ferramenta local", "frontend")); self.assertTrue(c["conversation"]["onde"].startswith("externo"))
        self.assertEqual(montar(local=FakeLocal(True))[0].status()["capacidades"]["conversation"]["onde"], "modelo local")

    def test_providers_http_reais_contra_servidor_falso(self):
        from provider_local import LocalOpenAICompat
        from provider_nvidia import NvidiaProvider
        vistos = []
        class F(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
            def do_POST(self):
                d = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); vistos.append((self.headers.get("Authorization"), d))
                b = json.dumps({"choices": [{"message": {"content": "<think>x</think>Resposta pronta."}}]}).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        srv = HTTPServer(("127.0.0.1", 0), F); threading.Thread(target=srv.serve_forever, daemon=True).start(); url = "http://127.0.0.1:%d" % srv.server_address[1]
        loc = LocalOpenAICompat(url, "tiny", ["conversation"]); self.assertTrue(loc.disponivel()); self.assertEqual(loc.generate("oi", [{"role": "user", "content": "a"}], "sis"), "Resposta pronta.")
        cfg = carregar(arquivo="/nao/existe.json", ambiente={}, NVIDIA_BASE_URL=url + "/v1", NVIDIA_VISION_MODEL="visao"); os.environ["NVIDIA_API_KEY"] = "chave-teste"
        try:
            nv = NvidiaProvider(cfg); self.assertEqual(nv.generate("texto"), "Resposta pronta."); h, corpo = vistos[-1]
            self.assertEqual((h, corpo["model"], corpo["temperature"], corpo["max_tokens"], corpo["reasoning_effort"]), ("Bearer chave-teste", cfg["NVIDIA_MODEL"], 1.0, 2048, "medium"))   # = server.py original
            nv.generate("o que é?", imagem="AAAA"); self.assertEqual(vistos[-1][1]["model"], "visao"); self.assertEqual(NvidiaProvider(cfg, fn=lambda m, h, s: "via-fn").generate("x"), "via-fn")
        finally: os.environ.pop("NVIDIA_API_KEY", None)
        srv.shutdown()
        os.environ["NVIDIA_API_KEY"] = "k"
        try:
            with self.assertRaises(ProviderErro) as e: NvidiaProvider(carregar(arquivo="/x.json", ambiente={}, NVIDIA_BASE_URL="http://127.0.0.1:1/v1")).generate("x")
        finally: os.environ.pop("NVIDIA_API_KEY", None)
        self.assertTrue(e.exception.offline)

    # ---------- server.py real ----------
    def _servidor(self, orq):
        os.environ.pop("NVIDIA_API_KEY", None)
        import server
        server.orq = orq; server.historico.clear(); server.historicos_sessao.clear(); server.contextos_sessao.clear()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.JarvisServer); self.addCleanup(srv.shutdown); threading.Thread(target=srv.serve_forever, daemon=True).start()
        porta = srv.server_address[1]
        def chamar(metodo, caminho, corpo=None, bruto=None):
            c = http.client.HTTPConnection("127.0.0.1", porta, timeout=10); c.request(metodo, caminho, bruto if bruto is not None else (json.dumps(corpo) if corpo else None), {"Content-Type": "application/json"})
            r = c.getresponse(); return r.status, dict(r.getheaders()), json.loads(r.read() or b"{}")
        return server, chamar

    def test_server_contrato_original_preservado(self):
        o, ext, _ = montar(); server, chamar = self._servidor(o)
        st, h, d = chamar("GET", "/"); self.assertEqual((st, d["servidor"], d["status"]), (200, "JARVIS", "online"))
        st, h, d = chamar("GET", "/status"); self.assertEqual((d["status"], d["dispositivo"], d["modelo"]), ("online", "Samsung J7 Prime", server.MODEL))
        st, h, d = chamar("OPTIONS", "/chat"); self.assertEqual((st, h["Access-Control-Allow-Origin"], h["Access-Control-Allow-Headers"]), (204, "*", "Content-Type"))
        st, h, d = chamar("POST", "/chat", {"mensagem": "quanto é 7 vezes 6"}); self.assertEqual((st, list(d), h["Access-Control-Allow-Origin"]), (200, ["resposta"], "*")); self.assertIn("42", d["resposta"])
        self.assertEqual(chamar("POST", "/chat", {"mensagem": ""})[0], 400); self.assertEqual(chamar("POST", "/chat", bruto="{nao e json")[0], 400)
        self.assertEqual(chamar("POST", "/outra", {"mensagem": "x"})[0], 404); self.assertEqual(chamar("GET", "/capacidades")[0], 200)

    def test_server_isola_contexto_por_sessao(self):
        o, ext, _ = montar()
        server, chamar = self._servidor(o)

        st, _, _ = chamar("POST", "/chat", {"mensagem": "me mande a letra de Asa Branca", "sessao": "A"})
        self.assertEqual(st, 200)

        r_b = chamar("POST", "/chat", {"mensagem": "por quê?", "sessao": "B"})[2]
        self.assertIn("contexto anterior", r_b["resposta"])

        r_a = chamar("POST", "/chat", {"mensagem": "por quê?", "sessao": "A"})[2]
        self.assertIn("direitos autorais", r_a["resposta"])
        self.assertEqual(ext.chamadas, 0)

    def test_server_historico_externo_isolado_por_sessao(self):
        o, ext, _ = montar(PERMITIR_HISTORICO_EXTERNO=True)
        server, chamar = self._servidor(o)
        chamar("POST", "/chat", {"mensagem": "Meu assunto é monitor 100 Hz.", "sessao": "A"})
        chamar("POST", "/chat", {"mensagem": "Meu assunto é RTX 2060.", "sessao": "B"})
        chamar("POST", "/chat", {"mensagem": "Compare isso com o que você mencionou antes.", "sessao": "A"})
        # A lista enviada ao provider deve conter apenas o histórico da sessão A.
        self.assertTrue(ext.hist)
        self.assertEqual(ext.hist[0]["content"], "Meu assunto é monitor 100 Hz.")

    def test_server_historico_curto(self):
        o, ext, _ = montar(MAX_HISTORICO=4); server, chamar = self._servidor(o)
        chamar("POST", "/chat", {"mensagem": MEM + "Meu nome é Ana?"})
        self.assertEqual(ext.hist, [])
        chamar("POST", "/chat", {"mensagem": "E o que mais?"})
        self.assertEqual(ext.hist, [])  # histórico não sai para a NVIDIA por padrão

        # O histórico pode ser liberado explicitamente.
        o, ext, _ = montar(MAX_HISTORICO=4, PERMITIR_HISTORICO_EXTERNO=True); server, chamar = self._servidor(o)
        chamar("POST", "/chat", {"mensagem": MEM + "Meu nome é Ana?"})
        self.assertEqual(ext.hist, [])
        chamar("POST", "/chat", {"mensagem": "E o que mais?"})
        self.assertEqual([m["role"] for m in ext.hist], ["user", "assistant"])
        self.assertEqual(ext.hist[0]["content"], "Meu nome é Ana?")

        for i in range(5): chamar("POST", "/chat", {"mensagem": "pergunta %d" % i})
        self.assertLessEqual(len(server.historico), 4)

    def test_server_inicia_e_responde_sem_chave_da_nvidia(self):
        os.environ.pop("NVIDIA_API_KEY", None)
        o = Orquestrador(cfg=carregar(arquivo="/nao/existe.json", ambiente={}), local=FakeLocal(False), internet=lambda h: True, log_path=os.path.join(tempfile.mkdtemp(), "m.json"))
        server, chamar = self._servidor(o)
        st, _, d = chamar("POST", "/chat", {"mensagem": "Qual é o estado atual do núcleo?"}); self.assertEqual((st, d["resposta"]), (200, M_SEM_PRV))
        self.assertIn("38", chamar("POST", "/chat", {"mensagem": "19 vezes 2"})[2]["resposta"]); self.assertFalse(chamar("GET", "/status")[2]["nvidia_configurada"])
        self.assertEqual(server.historico, [{"role": "user", "content": "19 vezes 2"}, {"role": "assistant", "content": "O resultado é 38."}])   # falha não entra no histórico

    def test_server_nao_trava_durante_resposta_lenta(self):
        o, ext, _ = montar(ext=FakeExterno(atraso=1.5)); server, chamar = self._servidor(o)
        t = threading.Thread(target=lambda: chamar("POST", "/chat", {"mensagem": "Olá"})); t.start(); time.sleep(.3)
        t0 = time.time(); st = chamar("GET", "/")[0]; dt = time.time() - t0; t.join()
        self.assertEqual(st, 200); self.assertLess(dt, 0.8, "GET / deveria responder enquanto a NVIDIA ainda processa")


    def test_busca_web_sintetiza_sem_historico(self):
        capacidades.FERRAMENTAS_INTERNET["web_search"] = lambda t: "Encontrei estas referências na internet:\\n1. Fonte A — https://exemplo.com/a"
        try:
            o, ext, _ = montar()
            r = o.responder("JARVIS, pesquise na internet o preço atual")
            self.assertEqual(r["rota"], "internet+externo")
            self.assertIn("Fonte A", ext.ultimo[0])
            self.assertNotIn("segredo", ext.ultimo[0])
            self.assertEqual(ext.hist, [])
        finally:
            capacidades.FERRAMENTAS_INTERNET["web_search"] = capacidades.ferramenta_busca_web

    def test_busca_web_parseia_resultados(self):
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'<a class="result__a" href="https://exemplo.com">Titulo de teste</a>'
        original = capacidades.urllib.request.urlopen
        capacidades.urllib.request.urlopen = lambda *a, **k: FakeResp()
        try:
            r = capacidades.ferramenta_busca_web("JARVIS, pesquise na internet teste")
            self.assertIn("Titulo de teste", r)
            self.assertIn("https://exemplo.com", r)
        finally:
            capacidades.urllib.request.urlopen = original

if __name__ == "__main__":
    unittest.main(verbosity=2)
