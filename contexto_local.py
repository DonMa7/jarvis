# -*- coding: utf-8 -*-
"""
Memória de curto prazo LOCAL do JARVIS.

Guarda apenas metadados da conversa no processo do servidor.
Nada é persistido em arquivo e nada é enviado automaticamente ao provider externo.
A classe existe para dar continuidade a perguntas curtas como "por quê?", "como assim?"
e para permitir respostas determinísticas antes do uso da NVIDIA.
"""
import re


class ContextoLocal:
    def __init__(self, max_eventos=8):
        self.max_eventos = max(1, int(max_eventos))
        self.eventos = []

    def limpar(self):
        self.eventos.clear()

    @property
    def ultimo(self):
        return self.eventos[-1] if self.eventos else None

    def atualizar(self, usuario, resposta, resultado=None, capacidade=None, rota=None, detalhe=None, tipo=None):
        evento = {
            "usuario": usuario or "",
            "resposta": resposta or "",
            "resultado": resultado or "",
            "capacidade": capacidade or "",
            "rota": rota or "",
            "detalhe": detalhe or "",
            "tipo": tipo or "",
        }
        self.eventos.append(evento)
        del self.eventos[:-self.max_eventos]

    @staticmethod
    def eh_followup_curto(texto):
        n = re.sub(r"\s+", " ", (texto or "").strip().lower())
        padroes = (
            r"^por que\s*\??$",
            r"^porque\s*\??$",
            r"^por quê\s*\??$",
            r"^e por que\s*\??$",
            r"^e por quê\s*\??$",
            r"^como assim\s*\??$",
            r"^como assim\s+isso\s*\??$",
            r"^explique\s*\??$",
            r"^explica\s*\??$",
            r"^continue\s*\??$",
            r"^continua\s*\??$",
            r"^e depois\s*\??$",
        )
        return any(re.match(p, n) for p in padroes)

    def resolver_followup(self, texto):
        """
        Responde apenas quando há uma resposta local segura e contextual.
        Retorna None quando é melhor deixar o fluxo normal decidir.
        """
        if not self.eventos or not self.eh_followup_curto(texto):
            return None

        u = self.ultimo
        resultado = u.get("resultado", "")
        rota = u.get("rota", "")
        detalhe = u.get("detalhe", "")
        resposta = u.get("resposta", "").lower()

        if "copyright_refusal" in resultado:
            return "Porque não posso reproduzir integralmente uma obra protegida por direitos autorais. Posso resumir, explicar o significado ou comentar um trecho curto."

        if rota == "bloqueado" and "PERMITIR_ENVIO_DE_" in (u.get("detalhe") or ""):
            return "Porque essa tarefa exigiria enviar dados a um serviço externo, e essa categoria está bloqueada pela política de privacidade atual."

        if "fallback_external" in resultado or "serviço externo" in resposta:
            return "Porque meu núcleo local não consegue realizar essa tarefa com segurança e eu recorreria ao serviço externo configurado."

        if "provider_unsupported" in resultado:
            return "Porque o serviço disponível não oferece a capacidade necessária para essa tarefa."

        if "offline" in resultado or rota == "offline":
            return "Porque essa tarefa depende de um serviço externo que está indisponível no momento."

        if rota == "ferramenta" and u.get("capacidade") == "self_awareness":
            return "Porque a resposta anterior descrevia minhas capacidades atuais. Posso detalhar qualquer uma delas."

        if texto.strip().lower().startswith(("por que", "por quê", "porque", "e por quê", "e por que")):
            return "Estou me referindo à resposta imediatamente anterior. Posso explicar o motivo em mais detalhes, mas preciso saber qual ponto o senhor quer aprofundar."

        return None


def resposta_local_basica(texto):
    """Pequena camada de conversação que não precisa de modelo."""
    n = re.sub(r"\s+", " ", (texto or "").strip().lower())
    n_sem = n.rstrip("!?.,")
    respostas = {
        "obrigado": "À disposição.",
        "obrigada": "À disposição.",
        "valeu": "À disposição.",
        "ok": "Perfeitamente.",
        "certo": "Perfeitamente.",
        "entendi": "Perfeitamente.",
        "beleza": "Perfeitamente.",
        "ate logo": "Até logo.",
        "até logo": "Até logo.",
    }
    if n_sem in respostas:
        return respostas[n_sem]

    if re.fullmatch(r"(oi|ola|olá|bom dia|boa tarde|boa noite)", n):
        from datetime import datetime
        h = datetime.now().hour
        saudacao = "Bom dia." if h < 12 else "Boa tarde." if h < 18 else "Boa noite."
        return saudacao

    return None
