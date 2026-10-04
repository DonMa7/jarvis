# -*- coding: utf-8 -*-
"""Memória estruturada persistente do JARVIS, mantida apenas no aparelho."""

import json
import os
import re
import tempfile
import threading
import unicodedata

CATEGORIAS = ("pc", "monitor", "celular", "preferencias", "projetos")

def _norm(texto):
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")

def _limpar(valor, limite=180):
    return " ".join((valor or "").strip().split())[:limite].rstrip(" ,.;:")

class MemoriaLocal:
    """Pequena memória de fatos explícitos. O JSON de dados não deve ser versionado."""

    def __init__(self, caminho=None, maximo=100):
        base = os.path.dirname(os.path.abspath(__file__))
        self.caminho = caminho or os.path.join(base, "memoria_usuario.json")
        self.maximo = max(1, int(maximo))
        self.lock = threading.RLock()
        self.dados = self._ler()

    def _vazia(self):
        return {"versao": 1, "memorias": {c: {} for c in CATEGORIAS}}

    def _ler(self):
        try:
            with open(self.caminho, encoding="utf-8") as f:
                bruto = json.load(f)
            if not isinstance(bruto, dict):
                raise ValueError
        except (OSError, ValueError, TypeError):
            return self._vazia()

        d = self._vazia()
        mem = bruto.get("memorias", {})
        if isinstance(mem, dict):
            for categoria in CATEGORIAS:
                secao = mem.get(categoria, {})
                if isinstance(secao, dict):
                    d["memorias"][categoria] = {
                        str(k)[:80]: _limpar(str(v))
                        for k, v in secao.items()
                        if str(k).strip() and str(v).strip()
                    }
        return d

    def _salvar(self):
        pasta = os.path.dirname(os.path.abspath(self.caminho))
        os.makedirs(pasta, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=pasta, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.dados, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.caminho)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def lembrar(self, categoria, chave, valor):
        categoria = _norm(categoria)
        chave = _limpar(chave, 80).lower()
        valor = _limpar(valor)
        if categoria not in CATEGORIAS or not chave or not valor:
            return False
        with self.lock:
            self.dados["memorias"][categoria][chave] = valor
            total = sum(len(v) for v in self.dados["memorias"].values())
            while total > self.maximo:
                apagou = False
                for nome in CATEGORIAS:
                    if self.dados["memorias"][nome]:
                        del self.dados["memorias"][nome][next(iter(self.dados["memorias"][nome]))]
                        apagou = True
                        total -= 1
                        break
                if not apagou:
                    break
            self._salvar()
        return True

    def esquecer(self, categoria, chave):
        categoria = _norm(categoria)
        chave = _limpar(chave, 80).lower()
        if categoria not in CATEGORIAS:
            return False
        with self.lock:
            if chave not in self.dados["memorias"][categoria]:
                return False
            del self.dados["memorias"][categoria][chave]
            self._salvar()
        return True

    def quantidade(self):
        with self.lock:
            return sum(len(v) for v in self.dados["memorias"].values())

    def todas(self):
        with self.lock:
            return {c: dict(self.dados["memorias"][c]) for c in CATEGORIAS}

    def buscar(self, texto, limite=6):
        n = _norm(texto)
        termos = {x for x in re.split(r"[^a-z0-9]+", n) if len(x) >= 2}
        resultados = []
        with self.lock:
            for categoria in CATEGORIAS:
                for chave, valor in self.dados["memorias"][categoria].items():
                    alvo = _norm(chave + " " + valor)
                    alvo_termos = set(re.findall(r"[a-z0-9]+", alvo))
                    score = len(termos & alvo_termos)
                    if _norm(chave) in n:
                        score += 4
                    if score:
                        resultados.append((score, categoria, chave, valor))
        resultados.sort(key=lambda x: (-x[0], x[1], x[2]))
        return [
            {"categoria": c, "chave": k, "valor": v}
            for _, c, k, v in resultados[:max(1, int(limite))]
        ]

    def contexto(self, texto, limite=6):
        itens = self.buscar(texto, limite)
        if not itens:
            return ""
        linhas = ["Memória local relevante:"]
        linhas.extend("- %s.%s: %s" % (x["categoria"], x["chave"], x["valor"]) for x in itens)
        return "\n".join(linhas)

    def resumo(self):
        todos = []
        with self.lock:
            for categoria in CATEGORIAS:
                for chave, valor in self.dados["memorias"][categoria].items():
                    todos.append((categoria, chave, valor))
        if not todos:
            return "Ainda não tenho memórias persistentes registradas sobre o senhor."
        nomes = {"pc": "PC", "monitor": "Monitor", "celular": "Celular", "preferencias": "Preferências", "projetos": "Projetos"}
        linhas = ["Tenho estas memórias persistentes locais:"]
        for categoria, chave, valor in todos:
            linhas.append("- %s — %s: %s." % (nomes.get(categoria, categoria), chave, valor))
        return "\n".join(linhas)

    def resposta_direta(self, texto):
        n = _norm(texto).strip(" ?!.")
        consultas = (
            (("qual meu monitor", "qual e meu monitor"), "monitor", "monitor", "monitor"),
            (("qual meu processador", "qual e meu processador"), "pc", "processador", "processador"),
            (("qual minha placa de video", "qual e minha placa de video", "qual minha placa grafica"), "pc", "gpu", "placa de vídeo"),
            (("qual meu celular", "qual e meu celular"), "celular", "modelo", "celular"),
        )
        with self.lock:
            for frases, categoria, chave, rotulo in consultas:
                if n in {_norm(f) for f in frases}:
                    valor = self.dados["memorias"][categoria].get(chave)
                    if valor:
                        return "O senhor me informou que seu %s é %s." % (rotulo, valor)
        return None

    @staticmethod
    def e_pedido_memoria(texto):
        n = _norm(texto).strip(" ?!.")
        return n in {
            "o que voce lembra de mim",
            "o que voce lembra",
            "quais coisas voce lembra",
            "mostre o que voce lembra",
        }

    def resposta_contextual(self, texto, contexto_anterior=""):
        """Resolve perguntas simples usando memória local conhecida, sem recorrer a API."""
        n = _norm(texto).strip(" ?!.")
        contexto = _norm(contexto_anterior)
        e_monitor = "monitor" in n or "monitor" in contexto
        if e_monitor:
            with self.lock:
                hz = self.dados["memorias"]["monitor"].get("monitor", "")
            if hz:
                if any(x in n for x in ("bom para jogar", "bom pra jogar", "bom para jogos", "bom pra jogos",
                                         "serve para jogar", "serve pra jogar", "suficiente para jogar",
                                         "suficiente pra jogar", "vale a pena para jogar")):
                    return (
                        "Sim. Pelo que o senhor me informou, seu monitor é de %s, então ele já oferece "
                        "uma fluidez boa para jogos. Para jogos competitivos, 144 Hz ou mais ainda "
                        "proporciona mais suavidade, mas 100 Hz já é um avanço claro sobre 60 Hz."
                    ) % hz
                if n in {"meu monitor", "minha tela", "meu display"}:
                    return "Seu monitor está registrado na minha memória como %s." % hz
                if any(x in n for x in ("quantos hz", "qual a frequencia", "qual frequencia", "qual taxa de atualizacao", "qual taxa de atualizacao")):
                    return "Seu monitor é de %s." % hz
        return None

    def aprender(self, texto):
        """Aprende apenas declarações explícitas de hardware e dispositivos."""
        original = " ".join((texto or "").strip().split())
        n = _norm(original)
        if not n:
            return []

        padroes = (
            ("monitor", "monitor", r"(?:meu|minha)\s+(?:monitor|tela)\s+(?:e|eh)\s+(.+)$"),
            ("pc", "gpu", r"minha\s+(?:placa de video|placa grafica)\s+(?:e|eh)\s+(.+)$"),
            ("pc", "processador", r"meu\s+(?:processador|cpu)\s+(?:e|eh)\s+(.+)$"),
            ("pc", "ram", r"(?:tenho|meu pc tem|meu computador tem)\s+(.{1,80}?)\s*(?:de\s+)?(?:ram|memoria ram)\b"),
            ("pc", "ssd", r"meu\s+ssd\s+(?:e|eh)\s+(.+)$"),
            ("pc", "windows", r"(?:uso|estou usando|meu pc usa)\s+(windows\s+.+)$"),
            ("celular", "modelo", r"(?:meu|minha)\s+(?:celular|smartphone|telefone)\s+(?:e|eh)\s+(.+)$"),
        )

        aprendidos = []
        for categoria, chave, rx in padroes:
            m = re.search(rx, n)
            if not m:
                continue
            valor = _limpar(m.group(1))
            if self.lembrar(categoria, chave, valor):
                aprendidos.append((categoria, chave, valor))
        return aprendidos
