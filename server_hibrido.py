#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server_hibrido.py - servidor OPCIONAL para testar o núcleo híbrido, ao lado do seu server.py (que não é alterado).
Mesmo contrato do site:  POST /chat  {"mensagem": "...", "imagem": "base64 (opcional)", "documento": "texto (opcional)"}
                         ->  {"resposta": "..."}   (extras: rota, capacidade)
Também:  GET /  (vida)   GET /capacidades  (o que o JARVIS consegue fazer agora e onde)
Só biblioteca padrão (funciona no Termux e no Windows). A chave da NVIDIA vem de NVIDIA_API_KEY (variável de ambiente).

Porta padrão 8081 para rodar em paralelo ao server.py (8080). Para trocar de verdade: PORT=8080 depois de parar o antigo.
"""
import json, os, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from capacidades import separar_memoria
from orquestrador import Orquestrador

try:
    from jarvis_personalidade import montar_prompt          # personalidade JARVIS já existente
except ImportError:
    montar_prompt = lambda: "Você é o JARVIS, assistente pessoal elegante e objetivo. Responda em português do Brasil."


def criar_servidor(orq, host="127.0.0.1", porta=8081):
    historico, lock = [], threading.Lock()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _resp(self, cod=200, corpo=None):
            self.send_response(cod)
            for k, v in {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type",
                         "Access-Control-Allow-Private-Network": "true", "Content-Type": "application/json; charset=utf-8"}.items(): self.send_header(k, v)
            b = json.dumps(corpo if corpo is not None else {}, ensure_ascii=False).encode("utf-8")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_OPTIONS(self): self._resp(204)
        def do_GET(self):
            if self.path.startswith("/capacidades"): return self._resp(200, orq.status())
            self._resp(200, {"ok": True, "servico": "jarvis-hibrido"})
        def do_POST(self):
            if self.path != "/chat": return self._resp(404, {"erro": "rota desconhecida"})
            try: d = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError: return self._resp(400, {"erro": "JSON inválido"})
            msg = (d.get("mensagem") or "").strip()
            if not msg: return self._resp(400, {"erro": "mensagem vazia"})
            try:
                with lock: hist = list(historico[-orq.cfg["MAX_HISTORICO"]:])
                r = orq.responder(msg, imagem=d.get("imagem"), documento=d.get("documento"), historico=hist, sistema=montar_prompt())
                if r["rota"] in ("ferramenta", "local", "externo"):
                    with lock: historico.extend([{"role": "user", "content": separar_memoria(msg)[1]}, {"role": "assistant", "content": r["resposta"]}])
                self._resp(200, {"resposta": r["resposta"], "rota": r["rota"], "capacidade": r["capacidade"]})
            except Exception as e:
                self._resp(500, {"erro": "Senhor, ocorreu uma falha interna ao processar o pedido."})
    return ThreadingHTTPServer((host, porta), H)


if __name__ == "__main__":
    orq = Orquestrador()
    porta = int(os.environ.get("PORT", "8081")); host = os.environ.get("HOST", "0.0.0.0")
    st = orq.status()
    print("JARVIS híbrido em http://%s:%d | modelo local: %s | externo: %s" % (host, porta, "ONLINE" if st["modelo_local"] else "nenhum", st["provider_externo"]))
    if not os.environ.get("NVIDIA_API_KEY"): print("AVISO: NVIDIA_API_KEY não definida - o fallback externo ficará indisponível.")
    criar_servidor(orq, host, porta).serve_forever()
