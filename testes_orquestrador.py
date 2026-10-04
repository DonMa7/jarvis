# -*- coding: utf-8 -*-
"""Testes reproduzíveis (sem internet, sem chaves, sem modelo local):  python testes_orquestrador.py"""
import http.client, json, os, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

import capacidades
from capacidades import classificar, minimizar_memoria, separar_memoria
from jarvis_config import carregar
from orquestrador import M_OFFLINE, M_SEM_PRV, Orquestrador
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
        casos = {"quanto é 25 vezes 18": "basic_math", "20% de 500": "basic_math", "corrija a ortografia deste texto": "text_correction", "resuma isto": "summarization",
                 "traduza para inglês": "translation", "analise este código python": "complex_code_analysis", "oi, tudo bem?": "conversation", "quanto é a capital da França": "conversation"}
        for t, c in casos.items(): self.assertEqual(classificar(t), c, t)
        self.assertEqual(classificar("o que é isso?", tem_imagem=True), "image_analysis"); self.assertEqual(classificar("leia", tem_documento=True), "advanced_document_analysis")

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
        # 3) INTERNET (sem IA) antes da NVIDIA
        capacidades.FERRAMENTAS_INTERNET["conversation"] = lambda t: "WEB: ok"
        try:
            o, ext, _ = montar(); r = o.responder("Como está o dia?"); self.assertEqual((r["rota"], ext.chamadas), ("internet", 0))
            o, ext, _ = montar(internet=False); self.assertEqual(o.responder("Como está o dia?")["rota"], "offline")                           # sem internet: nem tool nem NVIDIA
        finally: capacidades.FERRAMENTAS_INTERNET.clear()
        # 4) NVIDIA por último
        o, ext, _ = montar(); r = o.responder("Como está o dia?"); self.assertEqual((r["rota"], ext.chamadas), ("externo", 1)); self.assertTrue(r["resposta"].startswith("EXTERNO"))

    def test_erro_local_cai_para_externo(self):
        loc = FakeLocal(True, quebra=True); o, ext, log = montar(local=loc); r = o.responder("Olá")
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
        o, ext, _ = montar(internet=False); r = o.responder("Olá"); self.assertEqual((r["rota"], ext.chamadas, r["resposta"]), ("offline", 0, M_OFFLINE)); self.assertEqual(o.responder("quanto é 2+2")["rota"], "ferramenta")
        self.assertEqual(montar(ext=FakeExterno(erro=ProviderErro("x", offline=True)))[0].responder("Olá")["rota"], "offline")
        r = montar(ext=FakeExterno(erro=ProviderErro("x", status=500)))[0].responder("Olá"); self.assertEqual(r["rota"], "erro"); self.assertIn("500", r["detalhe"])
        o, ext, _ = montar(API_FALLBACK=False); r = o.responder("Olá"); self.assertEqual((r["rota"], ext.chamadas), ("bloqueado", 0)); self.assertIn("API_FALLBACK", r["resposta"])

    def test_memoria_so_o_necessario_vai_para_a_nvidia(self):
        self.assertEqual(separar_memoria(MEM + "qual a taxa do meu monitor?")[1], "qual a taxa do meu monitor?")
        mem, txt = separar_memoria(MEM + "qual a taxa do meu monitor?"); m = minimizar_memoria(mem, txt); self.assertIn("100 Hz", m); self.assertNotIn("azul", m)
        o, ext, _ = montar(); o.responder(MEM + "qual a taxa do meu monitor?"); self.assertIn("100 Hz", ext.ultimo[0]); self.assertNotIn("azul", ext.ultimo[0])     # só o relevante
        o, ext, _ = montar(); o.responder(MEM + "me conte uma curiosidade sobre o Brasil"); self.assertNotIn("100 Hz", ext.ultimo[0]); self.assertNotIn("azul", ext.ultimo[0])  # nada de brinde
        o, ext, _ = montar(PERMITIR_MEMORIA_EXTERNA=False); o.responder(MEM + "qual a taxa do meu monitor?"); self.assertNotIn("100 Hz", ext.ultimo[0])                 # política: nunca

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
        server.orq = orq; server.historico.clear()
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

    def test_server_historico_curto(self):
        o, ext, _ = montar(MAX_HISTORICO=4); server, chamar = self._servidor(o)
        chamar("POST", "/chat", {"mensagem": MEM + "Meu nome é Ana?"}); self.assertEqual(ext.hist, [])
        chamar("POST", "/chat", {"mensagem": "E o que mais?"}); self.assertEqual([m["role"] for m in ext.hist], ["user", "assistant"]); self.assertEqual(ext.hist[0]["content"], "Meu nome é Ana?")   # sem a memória anexada
        for i in range(5): chamar("POST", "/chat", {"mensagem": "pergunta %d" % i})
        self.assertLessEqual(len(server.historico), 4)
        chamar("POST", "/chat", {"mensagem": "quanto é 2+2"}); self.assertEqual(server.historico[-1]["content"], "O resultado é 4.")

    def test_server_inicia_e_responde_sem_chave_da_nvidia(self):
        os.environ.pop("NVIDIA_API_KEY", None)
        o = Orquestrador(cfg=carregar(arquivo="/nao/existe.json", ambiente={}), local=FakeLocal(False), internet=lambda h: True, log_path=os.path.join(tempfile.mkdtemp(), "m.json"))
        server, chamar = self._servidor(o)
        st, _, d = chamar("POST", "/chat", {"mensagem": "Bom dia"}); self.assertEqual((st, d["resposta"]), (200, M_SEM_PRV))
        self.assertIn("38", chamar("POST", "/chat", {"mensagem": "19 vezes 2"})[2]["resposta"]); self.assertFalse(chamar("GET", "/status")[2]["nvidia_configurada"])
        self.assertEqual(server.historico, [{"role": "user", "content": "19 vezes 2"}, {"role": "assistant", "content": "O resultado é 38."}])   # falha não entra no histórico

    def test_server_nao_trava_durante_resposta_lenta(self):
        o, ext, _ = montar(ext=FakeExterno(atraso=1.5)); server, chamar = self._servidor(o)
        t = threading.Thread(target=lambda: chamar("POST", "/chat", {"mensagem": "Olá"})); t.start(); time.sleep(.3)
        t0 = time.time(); st = chamar("GET", "/")[0]; dt = time.time() - t0; t.join()
        self.assertEqual(st, 200); self.assertLess(dt, 0.8, "GET / deveria responder enquanto a NVIDIA ainda processa")


if __name__ == "__main__":
    unittest.main(verbosity=2)
