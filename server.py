# -*- coding: utf-8 -*-

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading

from jarvis_personalidade import montar_prompt
from capacidades import separar_memoria
from orquestrador import Orquestrador


# A chave da NVIDIA agora é OPCIONAL para iniciar o servidor.
# Sem ela, o JARVIS continua funcionando com as ferramentas locais.
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

MODEL = "nvidia/nemotron-3-ultra-550b-a55b"

# =========================================
# NÚCLEO HÍBRIDO
# Prioridade: LOCAL > FERRAMENTA > INTERNET > NVIDIA
# =========================================

orq = Orquestrador(NVIDIA_MODEL=MODEL)

# =========================================
# HISTÓRICO CURTO DA CONVERSA (em memória)
# Guarda só o texto do usuário (sem a memória pessoal anexada pelo site) e a resposta.
# Tamanho em jarvis_config.py: MAX_HISTORICO (mensagens). 0 desliga.
# =========================================

historico = []
historico_lock = threading.Lock()


class JarvisServer(BaseHTTPRequestHandler):

    def send_json(self, data, status=200):
        response = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

        self.send_header(
            "Content-Length",
            str(len(response))
        )

        self.end_headers()

        self.wfile.write(response)

    def do_OPTIONS(self):

        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

        self.end_headers()

    def do_GET(self):

        if self.path == "/":

            self.send_json({
                "servidor": "JARVIS",
                "status": "online",
                "mensagem": "Olá. Estou funcionando."
            })

        elif self.path == "/status":

            status = orq.status()

            self.send_json({
                "status": "online",
                "dispositivo": "Samsung J7 Prime",
                "modelo": MODEL,
                "nvidia_configurada": status["provider_externo_pronto"],
                "modelo_local": status["modelo_local"]
            })

        elif self.path == "/capacidades":

            # O que o JARVIS consegue fazer agora e onde cada tarefa seria executada
            self.send_json(orq.status())

        else:

            self.send_json({
                "erro": "Rota não encontrada"
            }, 404)

    def do_POST(self):

        if self.path != "/chat":

            self.send_json({
                "erro": "Rota não encontrada"
            }, 404)

            return

        try:

            # Recebe o tamanho da requisição
            tamanho = int(
                self.headers.get("Content-Length", 0)
            )

            # Lê os dados
            dados = self.rfile.read(tamanho)

            # Converte JSON
            dados = json.loads(
                dados.decode("utf-8")
            )

            # Pega a mensagem
            mensagem = dados.get("mensagem", "").strip()

            if not mensagem:

                self.send_json({
                    "erro": "Mensagem vazia"
                }, 400)

                return

            # Não imprime a memória pessoal anexada pelo site
            texto_usuario = separar_memoria(mensagem)[1]

            print("USUÁRIO:", texto_usuario)

            # =========================================
            # PERSONALIDADE DO JARVIS
            # =========================================

            prompt = montar_prompt()

            # =========================================
            # HISTÓRICO RECENTE
            # =========================================

            limite = orq.cfg["MAX_HISTORICO"]

            with historico_lock:
                recente = list(historico[-limite:]) if limite > 0 else []

            # Sempre começa por uma fala do usuário
            if recente and recente[0]["role"] == "assistant":
                recente = recente[1:]

            # =========================================
            # ORQUESTRADOR: LOCAL > FERRAMENTA > INTERNET > NVIDIA
            # =========================================

            resultado = orq.responder(
                mensagem,
                imagem=dados.get("imagem"),
                documento=dados.get("documento"),
                historico=recente,
                sistema=prompt
            )

            resposta = resultado["resposta"]

            print(
                "ROTA:", resultado["rota"],
                "| capacidade:", resultado["capacidade"],
                "| provider:", resultado["provider"]
            )

            if resultado["detalhe"]:
                print("DETALHE:", resultado["detalhe"])

            # Só respostas bem-sucedidas entram no histórico
            if limite > 0 and resultado["rota"] in ("ferramenta", "local", "internet", "externo"):

                with historico_lock:

                    historico.append({"role": "user", "content": texto_usuario})
                    historico.append({"role": "assistant", "content": resposta})

                    del historico[:-limite]

            print("JARVIS:", resposta)

            # =========================================
            # ENVIA PARA O FRONTEND
            # =========================================

            self.send_json({
                "resposta": resposta
            })

        except json.JSONDecodeError:

            print("ERRO: JSON inválido recebido.")

            self.send_json({
                "erro": "JSON inválido."
            }, 400)

        except Exception as erro:

            print(
                "ERRO NO SERVIDOR:",
                erro
            )

            self.send_json({
                "erro": "Erro ao processar mensagem.",
                "detalhes": str(erro)
            }, 500)


# =========================================
# SERVIDOR
# =========================================

def main():

    # ThreadingHTTPServer: uma resposta lenta da NVIDIA não trava mais as outras requisições
    servidor = ThreadingHTTPServer(
        ("0.0.0.0", 8080),
        JarvisServer
    )

    status = orq.status()

    print("================================")
    print("       JARVIS SERVER")
    print("================================")
    print("Servidor iniciado.")
    print("Porta: 8080")
    print("IP: 192.168.0.115")
    print("IA: NVIDIA Nemotron 3 Ultra (fallback)")
    print("Personalidade: JARVIS MCU")
    print("Núcleo: LOCAL > FERRAMENTA > INTERNET > NVIDIA")
    print("Modelo local:", "ONLINE" if status["modelo_local"] else "nenhum")

    if not NVIDIA_API_KEY:
        print("AVISO: NVIDIA_API_KEY não definida. Ferramentas locais funcionam;")
        print("       conversas que dependem da NVIDIA responderão com um aviso.")

    print("================================")

    servidor.serve_forever()


if __name__ == "__main__":
    main()
