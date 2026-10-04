# -*- coding: utf-8 -*-
"""Testes reproduzíveis (sem internet, sem chaves): python testes_orquestrador.py"""
import http.client, json, os, tempfile, threading, unittest

from capacidades import classificar, ferramenta_matematica, separar_memoria
from jarvis_config import carregar
from orquestrador import Orquestrador, M_OFFLINE
from providers import LocalModel, Provider, ProviderErro


class FakeLocal(LocalModel):
    nome = "local-fake"
    def __init__(self, ligado=True, quebra=False): self.ligado, self.quebra, self.chamadas = ligado, quebra, 0
    def capacidades(self): return frozenset({"conversation", "text_correction", "summarization", "translation"})
    def disponivel(self): return self.ligado
    def generate(self, mensagem, historico=None, sistema=None, imagem=None):
        self.chamadas += 1
        if self.quebra: raise ProviderErro("falhou")
        return "LOCAL: " + mensagem[-30:]

class FakeExterno(Provider):
    nome, requer_internet, host = "fake-ext", True, "exemplo.invalid"
    def __init__(self, visao=False, erro=None): self.visao, self.erro, self.chamadas, self.ultimo = visao, erro, 0, None
    def capacidades(self):
        base = {"conversation", "text_correction", "summarization", "translation", "advanced_reasoning", "complex_code_analysis", "advanced_document_analysis"}
        return frozenset(base | ({"image_analysis"} if self.visao else set()))
    def disponivel(self): return True
    def generate(self, mensagem, historico=None, sistema=None, imagem=None):
        self.chamadas += 1; self.ultimo = (mensagem, imagem)
        if self.erro: raise self.erro
        return "EXTERNO: " + mensagem[-30:]


def montar(local=None, ext=None, internet=True, **cfg):
    log = os.path.join(tempfile.mkdtemp(), "mc.json")
    ext = ext or FakeExterno()
    return Orquestrador(cfg=carregar(arquivo="/nao/existe.json", ambiente={}, **cfg), local=local or FakeLocal(False), externos={"nvidia": ext},
                        internet=lambda h: internet, log_path=log), ext, log


class Testes(unittest.TestCase):
    def test_classificador(self):
        casos = {"quanto é 25 vezes 18": "basic_math", "20% de 500": "basic_math", "corrija a ortografia deste texto": "text_correction", "resuma isto": "summarization",
                 "traduza para inglês": "translation", "analise este código python": "complex_code_analysis", "oi, tudo bem?": "conversation", "quanto é a capital da França": "conversation"}
        for t, c in casos.items(): self.assertEqual(classificar(t), c, t)
        self.assertEqual(classificar("o que é isso?", tem_imagem=True), "image_analysis")
        self.assertEqual(classificar("leia", tem_documento=True), "advanced_document_analysis")

    def test_matematica_local_sem_api(self):
        o, ext, _ = montar()
        for msg, esperado in (("quanto é 25 vezes 18?", "450"), ("calcule (2+3)*4", "20"), ("quanto é 20 por cento de 500", "100")):
            r = o.responder(msg); self.assertEqual(r["rota"], "ferramenta"); self.assertIn(esperado, r["resposta"])
        self.assertEqual(ext.chamadas, 0)

    def test_conversa_sem_modelo_local_usa_nvidia_sem_aviso(self):
        o, ext, _ = montar()
        r = o.responder("Como está o dia?"); self.assertEqual((r["rota"], ext.chamadas), ("externo", 1)); self.assertTrue(r["resposta"].startswith("EXTERNO"))

    def test_local_first_nao_chama_api(self):
        loc = FakeLocal(True); o, ext, _ = montar(local=loc)
        r = o.responder("Como está o dia?"); self.assertEqual((r["rota"], loc.chamadas, ext.chamadas), ("local", 1, 0))

    def test_erro_local_cai_para_externo(self):
        loc = FakeLocal(True, quebra=True); o, ext, log = montar(local=loc)
        r = o.responder("Olá"); self.assertEqual((r["rota"], ext.chamadas), ("externo", 1))
        self.assertIn("local_error+external_ok", json.load(open(log))["eventos"][-1]["resultado"])

    def test_imagem_bloqueada_por_politica(self):
        o, ext, _ = montar(ext=FakeExterno(visao=True))
        r = o.responder("O que há nesta foto?", imagem="AAAA"); self.assertEqual((r["rota"], ext.chamadas), ("bloqueado", 0)); self.assertIn("PERMITIR_ENVIO_DE_IMAGEM", r["resposta"])

    def test_imagem_permitida_avisa_e_envia(self):
        o, ext, _ = montar(ext=FakeExterno(visao=True), PERMITIR_ENVIO_DE_IMAGEM=True)
        r = o.responder("O que há nesta foto?", imagem="AAAA")
        self.assertEqual((r["rota"], ext.chamadas, ext.ultimo[1]), ("externo", 1, "AAAA")); self.assertIn("serviço externo apropriado", r["resposta"])

    def test_imagem_sem_provider_de_visao(self):
        o, ext, _ = montar(PERMITIR_ENVIO_DE_IMAGEM=True)
        r = o.responder("Analise", imagem="AAAA"); self.assertEqual((r["rota"], ext.chamadas), ("bloqueado", 0)); self.assertIn("NVIDIA_VISION_MODEL", r["resposta"])

    def test_documento_exige_permissao(self):
        o, ext, _ = montar()
        r = o.responder("Resuma o contrato", documento="texto longo"); self.assertEqual((r["rota"], ext.chamadas), ("bloqueado", 0))
        o2, ext2, _ = montar(PERMITIR_ENVIO_DE_DOCUMENTOS=True)
        self.assertEqual(o2.responder("Resuma o contrato", documento="texto longo")["rota"], "externo"); self.assertIn("[Documento]", ext2.ultimo[0])

    def test_offline_nao_diz_erro_de_conexao_e_local_segue(self):
        o, ext, _ = montar(internet=False)
        r = o.responder("Olá"); self.assertEqual((r["rota"], ext.chamadas, r["resposta"]), ("offline", 0, M_OFFLINE)); self.assertNotIn("erro de conexão", r["resposta"].lower())
        self.assertEqual(o.responder("quanto é 2+2")["rota"], "ferramenta")

    def test_provider_cai_no_meio(self):
        o, ext, _ = montar(ext=FakeExterno(erro=ProviderErro("x", offline=True)))
        self.assertEqual(o.responder("Olá")["rota"], "offline")
        o2, _, _ = montar(ext=FakeExterno(erro=ProviderErro("x", status=500)))
        self.assertEqual(o2.responder("Olá")["rota"], "erro")

    def test_fallback_desligado(self):
        o, ext, _ = montar(API_FALLBACK=False); r = o.responder("Olá"); self.assertEqual((r["rota"], ext.chamadas), ("bloqueado", 0)); self.assertIn("API_FALLBACK", r["resposta"])

    def test_memoria_externa_pode_ser_bloqueada(self):
        msg = "Informações que o usuário pediu para você lembrar (use apenas se forem relevantes):\n- meu monitor: 100 Hz\n\nMensagem do usuário: qual a taxa do meu monitor?"
        self.assertEqual(separar_memoria(msg)[1], "qual a taxa do meu monitor?")
        o, ext, _ = montar(PERMITIR_MEMORIA_EXTERNA=False); o.responder(msg); self.assertNotIn("100 Hz", ext.ultimo[0])
        o2, ext2, _ = montar(); o2.responder(msg); self.assertIn("100 Hz", ext2.ultimo[0])      # padrão preserva o comportamento atual

    def test_registro_so_tem_metadados(self):
        o, _, log = montar(); o.responder("segredo-ultra-privado 123"); d = json.load(open(log))
        self.assertNotIn("segredo-ultra-privado", json.dumps(d)); self.assertEqual(d["eventos"][-1]["tarefa"], "conversation"); self.assertTrue(d["eventos"][-1]["sucesso"])

    def test_status_consciencia_operacional(self):
        o, _, _ = montar(); c = o.status()["capacidades"]
        self.assertEqual(c["basic_math"]["onde"], "ferramenta local"); self.assertEqual(c["memory"]["onde"], "frontend")
        self.assertTrue(c["conversation"]["onde"].startswith("externo")); self.assertIn("indisponível", c["image_analysis"]["onde"])
        o2, _, _ = montar(local=FakeLocal(True)); self.assertEqual(o2.status()["capacidades"]["conversation"]["onde"], "modelo local")
        o3, _, _ = montar(ext=FakeExterno(visao=True)); self.assertIn("bloqueado", o3.status()["capacidades"]["image_analysis"]["onde"])

    def test_servidor_http_compativel_com_o_site(self):
        from server_hibrido import criar_servidor
        o, ext, _ = montar(); srv = criar_servidor(o, "127.0.0.1", 0); porta = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        def chamar(metodo, caminho, corpo=None):
            c = http.client.HTTPConnection("127.0.0.1", porta, timeout=5); c.request(metodo, caminho, json.dumps(corpo) if corpo else None, {"Content-Type": "application/json"})
            r = c.getresponse(); return r.status, dict(r.getheaders()), json.loads(r.read() or b"{}")
        st, h, d = chamar("OPTIONS", "/chat"); self.assertEqual(st, 204); self.assertEqual(h.get("Access-Control-Allow-Private-Network"), "true")
        st, _, d = chamar("POST", "/chat", {"mensagem": "quanto é 7 vezes 6"}); self.assertEqual((st, "42" in d["resposta"]), (200, True))
        st, _, d = chamar("POST", "/chat", {"mensagem": "Bom dia"}); self.assertEqual((st, d["resposta"].startswith("EXTERNO")), (200, True))
        st, _, d = chamar("POST", "/chat", {"mensagem": ""}); self.assertEqual(st, 400)
        st, _, d = chamar("GET", "/capacidades"); self.assertIn("image_analysis", d["capacidades"])
        srv.shutdown()

    def test_providers_http_reais_contra_servidor_falso(self):
        """Exercita LocalOpenAICompat e NvidiaProvider de verdade (HTTP), contra um servidor falso no formato OpenAI."""
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from providers import LocalOpenAICompat, NvidiaProvider
        vistos = []
        class F(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
            def do_POST(self):
                d = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); vistos.append((self.headers.get("Authorization"), d))
                b = json.dumps({"choices": [{"message": {"content": "<think>pensando</think>Resposta pronta."}}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        srv = HTTPServer(("127.0.0.1", 0), F); threading.Thread(target=srv.serve_forever, daemon=True).start(); url = "http://127.0.0.1:%d" % srv.server_address[1]
        loc = LocalOpenAICompat(url, "tiny", ["conversation"]); self.assertTrue(loc.disponivel()); self.assertTrue(loc.suporta("conversation"))
        self.assertEqual(loc.generate("oi", [{"role": "user", "content": "a"}], "sis"), "Resposta pronta.")
        self.assertEqual([m["role"] for m in vistos[-1][1]["messages"]], ["system", "user", "user"])
        cfg = carregar(arquivo="/nao/existe.json", ambiente={}, NVIDIA_BASE_URL=url + "/v1", NVIDIA_VISION_MODEL="modelo-visao")
        os.environ["NVIDIA_API_KEY"] = "chave-de-teste"
        try:
            nv = NvidiaProvider(cfg); self.assertTrue(nv.disponivel()); self.assertTrue(nv.suporta("image_analysis"))
            self.assertEqual(nv.generate("texto"), "Resposta pronta."); self.assertEqual(vistos[-1][0], "Bearer chave-de-teste"); self.assertEqual(vistos[-1][1]["model"], cfg["NVIDIA_MODEL"])
            nv.generate("o que é?", imagem="AAAA"); self.assertEqual(vistos[-1][1]["model"], "modelo-visao"); self.assertIn("data:image/jpeg;base64,AAAA", json.dumps(vistos[-1][1]))
            self.assertEqual(NvidiaProvider(cfg, fn=lambda m, h, s: "via-fn").generate("x"), "via-fn")     # reaproveita a função existente do server.py
        finally: del os.environ["NVIDIA_API_KEY"]
        srv.shutdown()
        with self.assertRaises(ProviderErro) as e: NvidiaProvider(carregar(arquivo="/x.json", ambiente={}, NVIDIA_BASE_URL="http://127.0.0.1:1/v1")).generate("x") if os.environ.setdefault("NVIDIA_API_KEY", "k") else None
        os.environ.pop("NVIDIA_API_KEY", None); self.assertTrue(e.exception.offline)


if __name__ == "__main__":
    unittest.main(verbosity=2)
