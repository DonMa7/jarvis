# -*- coding: utf-8 -*-
"""
Registro de capacidades do JARVIS + classificador de intenção + ferramentas locais.
Para ADICIONAR uma capacidade: inclua uma entrada em CAPACIDADES (e, se for ferramenta local, em FERRAMENTAS).
tipo: "ferramenta" (código local) | "modelo" (precisa de um modelo de linguagem) | "frontend" (feito pelo site, só listado)
dados: o que sai do aparelho se um provider EXTERNO for usado: texto | imagem | documento
aviso: avisar o usuário antes de usar o serviço externo
"""
import html, re, unicodedata, urllib.parse, urllib.request
from html.parser import HTMLParser

CAPACIDADES = {
    # --- locais / frontend ---
    "basic_math":       {"tipo": "ferramenta", "desc": "Cálculos simples"},
    "web_search":       {"tipo": "ferramenta", "dados": "texto", "aviso": False, "desc": "Busca na internet"},
    "memory":           {"tipo": "frontend",   "desc": "Memória do usuário (navegador)"},
    "local_commands":   {"tipo": "frontend",   "desc": "Comandos locais, timer, cronômetro, sites"},
    "device_control":   {"tipo": "frontend",   "desc": "Controle do aparelho/navegador"},
    # --- texto: um modelo local pequeno pode resolver ---
    "conversation":     {"tipo": "modelo", "dados": "texto", "aviso": False, "desc": "Conversa geral"},
    "text_correction":  {"tipo": "modelo", "dados": "texto", "aviso": False, "desc": "Correção de ortografia e gramática"},
    "summarization":    {"tipo": "modelo", "dados": "texto", "aviso": False, "desc": "Resumo de texto"},
    "translation":      {"tipo": "modelo", "dados": "texto", "aviso": False, "desc": "Tradução"},
    # --- avançadas: normalmente exigem um provider externo ---
    "advanced_reasoning":         {"tipo": "modelo", "dados": "texto",     "aviso": True, "desc": "Raciocínio avançado"},
    "complex_code_analysis":      {"tipo": "modelo", "dados": "texto",     "aviso": True, "desc": "Análise de código complexo"},
    "advanced_document_analysis": {"tipo": "modelo", "dados": "documento", "aviso": True, "desc": "Análise detalhada de documentos"},
    "image_analysis":             {"tipo": "modelo", "dados": "imagem",    "aviso": True, "desc": "Análise de imagens"},
}

def normalizar(t):
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").strip()

# ---------- memória embutida pelo frontend na mensagem ----------
_CAB, _SEP = "Informações que o usuário pediu para você lembrar", "\n\nMensagem do usuário: "
def separar_memoria(mensagem):
    """Devolve (bloco_de_memoria, texto_do_usuário). O frontend junta os dois em `mensagem`."""
    if mensagem.startswith(_CAB) and _SEP in mensagem:
        mem, texto = mensagem.split(_SEP, 1)
        return mem, texto
    return "", mensagem

# Palavras comuns que não indicam relevância (iguais às do frontend)
_STOP = set("de do da dos das um uma que qual quais meu minha meus minhas tem com para por em no na os as eh sao como quanto".split())
def palavras(t):
    return {w for w in re.split(r"[^a-z0-9]+", normalizar(t)) if len(w) >= 2 and w not in _STOP}

def minimizar_memoria(memoria, texto):
    """Mantém só as linhas da memória que têm a ver com a pergunta (no máximo 5). Nada de memória pessoal 'de brinde'."""
    if not memoria: return ""
    linhas = memoria.splitlines(); alvo = palavras(texto)
    uteis = [l for l in linhas[1:] if l.startswith("- ") and alvo & palavras(l)][:5]
    return linhas[0] + "\n" + "\n".join(uteis) if uteis else ""

def montar_mensagem(memoria, texto):
    return memoria + _SEP + texto if memoria else texto

# ---------- classificador de intenção (regras explícitas, sem depender de erro do modelo) ----------
_REGRAS = [
    ("text_correction", r"\b(corrij\w*|corrig\w*|revis(e|ar|ao)\b.*\b(texto|ortografia|gramatica)|ortografia|gramatica)\b"),
    ("summarization",   r"\b(resum(a|e|ir|o)|sintetiz\w*)\b"),
    ("translation",     r"\b(traduz\w*|traduc\w*)\b"),
    ("complex_code_analysis", r"\b(analis\w*|revis\w*|depur\w*|debug\w*|otimiz\w*|refator\w*|ache o bug|encontre o bug)\b.*\b(codigo|script|funcao|programa|bug|classe)\b"),
    ("advanced_reasoning", r"\b(demonstre|prove que|raciocin\w*|analise detalhada|passo a passo.*(logic|matematic|problema))\b"),
    ("web_search", r"\b(pesquis\w*|procure|busque|buscar|pesquisa|na internet|ultimas? noticias|noticias sobre|o que aconteceu hoje|cotacao|preco atual)\b"),
]
def classificar(texto, tem_imagem=False, tem_documento=False):
    if tem_imagem: return "image_analysis"
    if tem_documento: return "advanced_document_analysis"
    if ferramenta_matematica(texto) is not None: return "basic_math"
    n = normalizar(texto)
    if texto.count("```") >= 2 and len(texto) > 300: return "complex_code_analysis"
    for cap, rx in _REGRAS:
        if re.search(rx, n): return cap
    return "conversation"

# ---------- ferramenta local: calculadora segura (sem eval) ----------
def _calcular(src):
    s = re.sub(r"(\d),(\d)", r"\1.\2", src).replace("×", "*").replace("÷", "/")
    s = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", s); s = re.sub(r"\s+", "", s)
    tk = re.findall(r"\d+\.?\d*|\*\*|[-+*/^%()]", s)
    if not tk or "".join(tk) != s: raise ValueError
    i = [0]
    pk = lambda: tk[i[0]] if i[0] < len(tk) else None
    def nx(): i[0] += 1; return tk[i[0] - 1] if i[0] <= len(tk) else None
    def post(v):
        while pk() == "%": nx(); v /= 100
        return v
    def prim():
        t = nx()
        if t == "(":
            v = soma()
            if nx() != ")": raise ValueError
            return post(v)
        if t == "-": return -pot()
        if t == "+": return pot()
        if not t or not t[0].isdigit(): raise ValueError
        return post(float(t))
    def pot():
        b = prim()
        if pk() in ("^", "**"): nx(); return b ** pot()
        return b
    def mul():
        v = pot()
        while pk() in ("*", "/"): v = v * pot() if nx() == "*" else v / pot()
        return v
    def soma():
        v = mul()
        while pk() in ("+", "-"): v = v + mul() if nx() == "+" else v - mul()
        return v
    r = soma()
    if i[0] < len(tk): raise ValueError
    return r

def _fmt(v):
    return ("%.10g" % v).replace(".", ",")

def ferramenta_matematica(texto):
    """Resolve contas simples. Devolve a resposta em texto ou None se a mensagem não for uma conta."""
    n = normalizar(texto).rstrip("?!. ")
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:%|por cento)\s*de\s*(\d+(?:[.,]\d+)?)", n)
    if m:
        v = float(m.group(1).replace(",", ".")) * float(m.group(2).replace(",", ".")) / 100
        return "O resultado é %s." % _fmt(v)
    mc = re.match(r"^(?:calcule|calcula|calcular|calc|quanto (?:e|eh|da))\s+(.+)$", n)
    expr = mc.group(1) if mc else n
    for a, b in ((r"\bmais\b", "+"), (r"\bmenos\b", "-"), (r"\b(?:vezes|multiplicado por)\b", "*"), (r"\b(?:dividido por|dividido)\b", "/"), (r"\belevado (?:a|ao)\b", "^")):
        expr = re.sub(a, b, expr)
    if not re.search(r"\d", expr): return None
    if not (mc or (re.fullmatch(r"[\d\s.,+\-*/x×÷^%()]+", expr) and re.search(r"[+\-*/x×÷^%]", expr))): return None
    try: return "O resultado é %s." % _fmt(_calcular(expr))
    except (ValueError, ZeroDivisionError, OverflowError): return None


class _ResultadosBusca(HTMLParser):
    def __init__(self):
        super().__init__()
        self.itens = []
        self._item = None
        self._capturando = False

    def handle_starttag(self, tag, attrs):
        if tag != "a": return
        a = dict(attrs)
        if "result__a" in a.get("class", "").split():
            self._item = [a.get("href", ""), ""]
            self._capturando = True

    def handle_data(self, data):
        if self._capturando and self._item:
            self._item[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._capturando and self._item:
            link, titulo = self._item
            self.itens.append((html.unescape(link), re.sub(r"\s+", " ", html.unescape(titulo)).strip()))
            self._item = None
            self._capturando = False

def ferramenta_busca_web(texto):
    """Busca pública simples via DuckDuckGo HTML, sem chave/API externa."""
    n = normalizar(texto)
    n = re.sub(r"^(jarvis[, ]*)?(pesquis\w*|procure|busque|buscar)\s*(na internet|na web|online)?\s*", "", n).strip(" ?.!:;")
    if not n: return "Senhor, preciso de um termo para realizar a pesquisa."
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": n, "kl": "br-pt"})
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            pagina = r.read().decode("utf-8", errors="replace")
    except Exception:
        return "Senhor, a busca na internet está indisponível neste momento."
    parser = _ResultadosBusca()
    parser.feed(pagina)
    if not parser.itens:
        return "Senhor, não encontrei resultados para essa pesquisa."
    linhas = ["Encontrei estas referências na internet:"]
    for i, (link, titulo) in enumerate(parser.itens[:5], 1):
        if link.startswith("//"): link = "https:" + link
        linhas.append(str(i) + ". " + titulo + " — " + link)
    return "\n".join(linhas)

FERRAMENTAS = {"basic_math": ferramenta_matematica}
# Ferramentas que usam internet sem IA. Etapa 3 da prioridade.
FERRAMENTAS_INTERNET = {"web_search": ferramenta_busca_web}
