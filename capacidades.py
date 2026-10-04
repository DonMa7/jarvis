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
    "self_awareness":   {"tipo": "ferramenta", "desc": "Diagnóstico das próprias capacidades e rotas"},
    "web_search":       {"tipo": "ferramenta", "dados": "texto", "aviso": False, "desc": "Busca na internet"},
    "memory":           {"tipo": "frontend",   "desc": "Memória do usuário (navegador)"},
    "local_commands":   {"tipo": "frontend",   "desc": "Comandos locais, timer, cronômetro, sites"},
    "device_control":   {"tipo": "frontend",   "desc": "Controle do aparelho/navegador"},
    "local_dialogue":   {"tipo": "ferramenta", "desc": "Conversação básica e continuidade local"},
    "copyright_request":{"tipo": "ferramenta", "desc": "Detecção local de pedidos de reprodução integral"},
    "unit_conversion":  {"tipo": "ferramenta", "desc": "Conversões de unidades sem internet"},
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

def pedido_reproducao_integral(texto):
    """Detecta localmente pedidos de reprodução integral de obras protegidas."""
    n = normalizar(texto)
    tem_obra = bool(re.search(r"\b(letra|letras|poema|poesia|livro|capitulo|roteiro|script)\b", n))
    tem_integral = bool(re.search(r"\b(completa|completo|inteira|inteiro|toda|todo|integral|na integra)\b", n))
    pedido_direto = bool(re.search(r"\b(me mande|manda|me passa|passe|forneca|fornece|envie|envia|mostre|mostra|me de|me da|queria|gostaria)\b.*\b(letra|letras|poema|poesia|roteiro|script)\b", n))
    return tem_obra and (tem_integral or pedido_direto)


_LOCAL_DIALOGUE = re.compile(
    r"^(oi|ola|bom dia|boa tarde|boa noite|obrigado|obrigada|valeu|ok|certo|entendi|beleza|"
    r"ate logo|por que|porque|e por que|como assim|como assim isso|explique|explica|"
    r"continue|continua|e depois)[?!.,\s]*$"
)

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

# ---------- ferramenta local: conversão de unidades ----------
_UNIDADES = {
    "comprimento": {
        "mm": 0.001, "milimetro": 0.001, "milimetros": 0.001,
        "cm": 0.01, "centimetro": 0.01, "centimetros": 0.01,
        "m": 1.0, "metro": 1.0, "metros": 1.0,
        "km": 1000.0, "quilometro": 1000.0, "quilometros": 1000.0,
        "in": 0.0254, "inch": 0.0254, "polegada": 0.0254, "polegadas": 0.0254,
        "ft": 0.3048, "feet": 0.3048, "pe": 0.3048, "pes": 0.3048,
        "yd": 0.9144, "jarda": 0.9144, "jardas": 0.9144,
        "mi": 1609.344, "milha": 1609.344, "milhas": 1609.344,
    },
    "massa": {
        "mg": 0.001, "miligramo": 0.001, "miligramas": 0.001,
        "g": 1.0, "grama": 1.0, "gramas": 1.0,
        "kg": 1000.0, "quilo": 1000.0, "quilos": 1000.0, "quilograma": 1000.0, "quilogramas": 1000.0,
        "t": 1000000.0, "tonelada": 1000000.0, "toneladas": 1000000.0,
        "oz": 28.349523125, "onca": 28.349523125, "oncas": 28.349523125,
        "lb": 453.59237, "libra": 453.59237, "libras": 453.59237,
    },
    "volume": {
        "ml": 0.001, "mililitro": 0.001, "mililitros": 0.001,
        "l": 1.0, "litro": 1.0, "litros": 1.0,
        "m3": 1000.0, "metro cubico": 1000.0, "metros cubicos": 1000.0,
    },
    "dados": {
        "b": 1.0, "byte": 1.0, "bytes": 1.0,
        "kb": 1024.0, "kib": 1024.0,
        "mb": 1024.0**2, "mib": 1024.0**2,
        "gb": 1024.0**3, "gib": 1024.0**3,
        "tb": 1024.0**4, "tib": 1024.0**4,
    },
    "tempo": {
        "ms": 0.001, "milissegundo": 0.001, "milissegundos": 0.001,
        "s": 1.0, "seg": 1.0, "segundo": 1.0, "segundos": 1.0,
        "min": 60.0, "minuto": 60.0, "minutos": 60.0,
        "h": 3600.0, "hora": 3600.0, "horas": 3600.0,
        "d": 86400.0, "dia": 86400.0, "dias": 86400.0,
    },
}
_DESTACADOS = {
    "comprimento": {"m": "m", "km": "km", "cm": "cm", "mm": "mm", "mi": "mi", "ft": "ft", "in": "in"},
    "massa": {"g": "g", "kg": "kg", "mg": "mg", "t": "t", "lb": "lb", "oz": "oz"},
    "volume": {"l": "L", "ml": "mL", "m3": "m³"},
    "dados": {"b": "B", "kb": "KB", "mb": "MB", "gb": "GB", "tb": "TB"},
    "tempo": {"s": "s", "min": "min", "h": "h", "d": "dias"},
}


def _converter_numero(v):
    return float(str(v).replace(",", "."))


def ferramenta_conversao(texto):
    """Converte unidades comuns sem rede. Retorna None se não reconhecer uma conversão."""
    n = normalizar(texto)
    n = re.sub(r"^(?:converta|converter|conversao|conversão|transforme|transformar)\s+", "", n)
    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*([a-z0-9²³]+(?:\s+[a-z]+)?)\s+(?:para|em|pra|->|to)\s+([a-z0-9²³]+(?:\s+[a-z]+)?)\s*$", n)
    if not m:
        return None
    valor = _converter_numero(m.group(1))
    origem = m.group(2).strip()
    destino = m.group(3).strip()

    # Temperatura tem fórmula, não fator.
    if origem in ("c", "celsius", "graus celsius") and destino in ("f", "fahrenheit", "graus fahrenheit"):
        r = valor * 9 / 5 + 32
        return "O resultado é %.6g °F." % r
    if origem in ("f", "fahrenheit", "graus fahrenheit") and destino in ("c", "celsius", "graus celsius"):
        r = (valor - 32) * 5 / 9
        return "O resultado é %.6g °C." % r
    if origem in ("c", "celsius", "graus celsius") and destino in ("k", "kelvin"):
        return "O resultado é %.6g K." % (valor + 273.15)
    if origem in ("k", "kelvin") and destino in ("c", "celsius", "graus celsius"):
        return "O resultado é %.6g °C." % (valor - 273.15)

    for grupo, unidades in _UNIDADES.items():
        if origem in unidades and destino in unidades:
            base = valor * unidades[origem]
            resultado = base / unidades[destino]
            destino_fmt = _DESTACADOS.get(grupo, {}).get(destino, destino)
            return "O resultado é %.10g %s." % (resultado, destino_fmt)
    return None

# ---------- classificador de intenção (regras explícitas, sem depender de erro do modelo) ----------
_REGRAS = [
    ("self_awareness",  r"\b(o que (voce|você) (consegue|pode|sabe) fazer|quais (sao|são) (as )?suas capacidades|suas capacidades|como voce (funciona|opera)|como você (funciona|opera))\b"),
    ("text_correction", r"\b(corrij\w*|corrig\w*|revis(e|ar|ao)\b.*\b(texto|ortografia|gramatica)|ortografia|gramatica)\b"),
    ("summarization",   r"\b(resum(a|e|ir|o)|sintetiz\w*)\b"),
    ("translation",     r"\b(traduz\w*|traduc\w*)\b"),
    ("complex_code_analysis", r"\b(analis\w*|revis\w*|depur\w*|debug\w*|otimiz\w*|refator\w*|ache o bug|encontre o bug)\b.*\b(codigo|script|funcao|programa|bug|classe)\b"),
    ("advanced_reasoning", r"\b(demonstre|prove que|raciocin\w*|analise detalhada|passo a passo.*(logic|matematic|problema))\b"),
    ("web_search", r"\b(pesquis\w*|procure|busque|buscar|pesquisa|na internet|ultimas? noticias|noticias sobre|o que aconteceu hoje|cotacao|preco atual)\b"),
]

def _eh_seguimento_referencial(n):
    partes = n.strip().split()
    if not partes:
        return False
    p0 = partes[0].rstrip("?!.,")
    if p0 in {"isso", "isto", "esse", "essa", "ele", "ela", "eles", "elas"}:
        return True
    if p0 == "e" and len(partes) >= 2:
        p1 = partes[1].rstrip("?!.,")
        if p1 in {"a", "o", "as", "os"} and len(partes) > 2:
            p2 = partes[2].rstrip("?!.,")
            if p2 in {"que", "quem", "como", "quando", "onde", "mais", "menos"}:
                return False
        return p1 in {"a", "o", "as", "os", "essa", "esse", "esta", "este", "isso", "isto", "ele", "ela", "eles", "elas", "aquele", "aquela"}
    if p0 == "mas" and len(partes) >= 3 and partes[1].rstrip("?!.,") == "e":
        p2 = partes[2].rstrip("?!.,")
        return p2 in {"a", "o", "essa", "esse", "isso", "ele", "ela"}
    return False

def classificar(texto, tem_imagem=False, tem_documento=False):
    if tem_imagem: return "image_analysis"
    if tem_documento: return "advanced_document_analysis"
    if ferramenta_matematica(texto) is not None: return "basic_math"
    if ferramenta_conversao(texto) is not None: return "unit_conversion"
    if pedido_reproducao_integral(texto): return "copyright_request"
    n = normalizar(texto)
    if _LOCAL_DIALOGUE.fullmatch(n) or _eh_seguimento_referencial(n): return "local_dialogue"
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
        cls = a.get("class", "")
        if "result__a" in cls.split():
            self._item = [a.get("href", ""), ""]
            self._capturando = True

    def handle_data(self, data):
        if self._capturando and self._item:
            self._item[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._capturando and self._item:
            link, titulo = self._item
            titulo = re.sub(r"\\s+", " ", html.unescape(titulo)).strip()
            if link and titulo:
                self.itens.append((html.unescape(link), titulo))
            self._item = None
            self._capturando = False


class _ResultadosLite(HTMLParser):
    """Parser de fallback para o DuckDuckGo Lite."""
    def __init__(self):
        super().__init__()
        self.itens = []
        self._link = None
        self._titulo = ""

    def handle_starttag(self, tag, attrs):
        if tag != "a": return
        a = dict(attrs)
        href = a.get("href", "")
        if href and ("result-link" in a.get("class", "") or "uddg=" in href):
            self._link = href
            self._titulo = ""

    def handle_data(self, data):
        if self._link is not None:
            self._titulo += data

    def handle_endtag(self, tag):
        if tag == "a" and self._link is not None:
            titulo = re.sub(r"\\s+", " ", html.unescape(self._titulo)).strip()
            if titulo and len(titulo) > 2:
                self.itens.append((html.unescape(self._link), titulo))
            self._link = None
            self._titulo = ""


def _buscar_duckduckgo(url, parser_cls):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 8.1; JARVIS/1.0) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        pagina = r.read().decode("utf-8", errors="replace")
    parser = parser_cls()
    parser.feed(pagina)
    return parser.itens


def ferramenta_busca_web(texto):
    """Busca pública via DuckDuckGo HTML, com fallback para a versão Lite."""
    n = normalizar(texto)
    n = re.sub(r"^(jarvis[, ]*)?(pesquis\\w*|procure|busque|buscar)\\s*(na internet|na web|online)?\\s*", "", n).strip(" ?.!:;")
    if not n:
        return "Senhor, preciso de um termo para realizar a pesquisa."

    base = urllib.parse.urlencode({"q": n, "kl": "br-pt"})

    try:
        itens = _buscar_duckduckgo("https://html.duckduckgo.com/html/?" + base, _ResultadosBusca)
    except Exception:
        itens = []

    if not itens:
        try:
            itens = _buscar_duckduckgo("https://lite.duckduckgo.com/lite/?" + base, _ResultadosLite)
        except Exception:
            itens = []

    if not itens:
        return "Senhor, não encontrei resultados para essa pesquisa."

    linhas = ["Encontrei estas referências na internet:"]
    vistos = set()
    numero = 0
    for link, titulo in itens:
        if link.startswith("//"):
            link = "https:" + link
        if "duckduckgo.com/l/?" in link and "uddg=" in link:
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
                link = qs.get("uddg", [link])[0]
            except Exception:
                pass
        chave = link.strip()
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        numero += 1
        linhas.append(str(numero) + ". " + titulo + " — " + link)
        if numero >= 5:
            break
    return "\\n".join(linhas) if numero else "Senhor, não encontrei resultados para essa pesquisa."

FERRAMENTAS = {"basic_math": ferramenta_matematica, "unit_conversion": ferramenta_conversao}
# Ferramentas que usam internet sem IA. Etapa 3 da prioridade.
FERRAMENTAS_INTERNET = {"web_search": ferramenta_busca_web}
