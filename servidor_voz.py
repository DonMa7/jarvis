#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
servidor_voz.py - servidor de voz do J.A.R.V.I.S. (roda no PC, porta 8765).

Mantém a MESMA API que o site já usa:
    POST /falar   {"texto": "...", "proximas": ["...", "..."]}   (responde quando terminar de falar)
    POST /parar                                                   (interrompe a fala na hora)
    GET  /                                                        (teste de vida)

Por que não tem mais atraso:
  1. O modelo do Piper é carregado UMA vez (antes: processo/modelo novo a cada frase).
  2. Enquanto a frase atual toca, as próximas ("proximas") já são sintetizadas em segundo plano.
  3. O áudio toca direto da memória (sounddevice), sem gravar arquivo temporário.

Voz: o Piper "faber" (livre de direitos) afinado para o estilo do JARVIS: tom médio perto de 115 Hz,
fala calma e pausada, timbre quente. A voz NÃO é clone de nenhum dublador.

Instalação:   pip install piper-tts sounddevice numpy pedalboard
Execução:     python servidor_voz.py
Teste de som: python servidor_voz.py --teste
"""
import io, json, os, sys, threading, time
from concurrent.futures import CancelledError, ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

# ===================== CONFIGURAÇÃO (ajuste aqui) =====================
MODELO        = os.environ.get("JARVIS_MODELO", "pt_BR-faber-medium.onnx")  # caminho do .onnx (o .onnx.json fica ao lado)
HOST, PORTA   = "127.0.0.1", 8765        # use "0.0.0.0" para aceitar conexões de outros aparelhos da rede
VELOCIDADE    = 1.10    # length_scale do Piper: acima de 1 = mais lento e calmo (1.0 = normal)
VARIACAO      = 0.55    # noise_scale: menor = entonação mais estável, menos emotiva
VARIACAO_RIT  = 0.60    # noise_w: menor = ritmo mais regular
ALVO_F0       = 115.0   # Hz: tom médio medido no áudio de referência (faixa 93-142 Hz)
TOM_MANUAL    = None    # em semitons (ex.: -1.5). None = calibra sozinho para chegar em ALVO_F0
VOLUME        = 0.95    # 0 a 1
CALIDEZ_DB    = 2.5     # reforço de graves (180 Hz). Mais = voz mais "encorpada"
BRILHO_DB     = -1.5    # ajuste de agudos (5,5 kHz). Negativo = mais suave
REVERB        = 0.05    # 0 desliga; ~0.05 dá a sensação de "sistema" sem eco perceptível
MAX_PROXIMAS  = 2       # quantas frases à frente sintetizar
FAKE          = os.environ.get("JARVIS_FAKE") == "1"   # modo de teste sem Piper/placa de som
# ======================================================================

CALIBRACAO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibracao.json")
FRASES_CAL = "Senhor, detectei uma inconsistência nos dados. Os sistemas estão operando normalmente."
log = lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------- análise de tom ----------
def f0_mediano(x, sr):
    """Tom médio (Hz) por autocorrelação; None se não achar voz."""
    fl, hop = int(.04 * sr), int(.01 * sr); lo, hi = int(sr / 300), int(sr / 70)
    if len(x) < fl * 3: return None
    rms = np.array([np.sqrt(np.mean(x[i:i + fl] ** 2)) for i in range(0, len(x) - fl, hop)])
    vals = []
    for k, i in enumerate(range(0, len(x) - fl, hop)):
        if rms[k] < rms.max() * .15: continue
        f = x[i:i + fl] - x[i:i + fl].mean(); F = np.fft.rfft(f, 2 * fl); ac = np.fft.irfft(F * np.conj(F))[:fl]
        if ac[0] <= 0: continue
        ac = ac / ac[0]; p = lo + int(np.argmax(ac[lo:hi]))
        if ac[p] > .55: vals.append(sr / p)
    return float(np.median(vals)) if vals else None


# ---------- síntese ----------
class Voz:
    def __init__(self):
        self.lock = threading.Lock()
        self.semitons = 0.0
        self.board = None
        if FAKE:
            self.sr = 22050; return
        from piper import PiperVoice
        log("Carregando modelo", MODELO, "...")
        self.voice = PiperVoice.load(MODELO)
        self.sr = self.voice.config.sample_rate
        try:
            from piper import SynthesisConfig
            self.cfg = SynthesisConfig(length_scale=VELOCIDADE, noise_scale=VARIACAO, noise_w_scale=VARIACAO_RIT)
        except ImportError:
            self.cfg = None   # piper-tts antigo: usa a API antiga em _bruto()
        self._calibrar()

    def _bruto(self, texto):
        """Áudio float32 mono, sem pós-processamento."""
        if self.cfg is not None:
            partes = [c.audio_float_array for c in self.voice.synthesize(texto, syn_config=self.cfg)]
        else:
            partes = [np.frombuffer(b, np.int16).astype(np.float32) / 32768 for b in self.voice.synthesize_stream_raw(
                texto, length_scale=VELOCIDADE, noise_scale=VARIACAO, noise_w=VARIACAO_RIT)]
        return np.concatenate(partes).astype(np.float32) if partes else np.zeros(1, np.float32)

    def _calibrar(self):
        """Mede o tom natural da voz e calcula quantos semitons faltam para ALVO_F0 (guardado em calibracao.json)."""
        if TOM_MANUAL is not None: self.semitons = TOM_MANUAL
        else:
            try: cache = json.load(open(CALIBRACAO))
            except Exception: cache = {}
            if cache.get("modelo") == MODELO and cache.get("alvo") == ALVO_F0 and cache.get("vel") == VELOCIDADE:
                self.semitons = cache["semitons"]
            else:
                f0 = f0_mediano(self._bruto(FRASES_CAL), self.sr)
                if f0:
                    self.semitons = float(np.clip(12 * np.log2(ALVO_F0 / f0), -6, 3))
                    log("Tom natural da voz: %.0f Hz -> alvo %.0f Hz (ajuste %+.1f semitons)" % (f0, ALVO_F0, self.semitons))
                json.dump({"modelo": MODELO, "alvo": ALVO_F0, "vel": VELOCIDADE, "semitons": self.semitons}, open(CALIBRACAO, "w"))
        try:
            from pedalboard import (Pedalboard, PitchShift, HighpassFilter, LowShelfFilter, HighShelfFilter, Compressor, Reverb, Limiter)
            b = [HighpassFilter(70)]
            if abs(self.semitons) >= .3: b.append(PitchShift(self.semitons))
            b += [LowShelfFilter(180, gain_db=CALIDEZ_DB), HighShelfFilter(5500, gain_db=BRILHO_DB),
                  Compressor(threshold_db=-20, ratio=2.5, attack_ms=8, release_ms=120)]
            if REVERB > 0: b.append(Reverb(room_size=.07, damping=.6, wet_level=REVERB, dry_level=1 - REVERB))
            self.board = Pedalboard(b + [Limiter(threshold_db=-1)])
        except ImportError:
            log("AVISO: 'pedalboard' não instalado - sem ajuste de tom/timbre. Instale com: pip install pedalboard")

    def gerar(self, texto):
        """Texto -> (áudio pronto, taxa). Roda em uma thread só (uma síntese por vez)."""
        t0 = time.time()
        with self.lock:
            if FAKE:
                time.sleep(.3); a = (np.sin(np.linspace(0, 440 * 2 * np.pi, self.sr)) * .1).astype(np.float32)
            else:
                a = self._bruto(preparar(texto))
                if self.board is not None: a = self.board(a, self.sr).astype(np.float32)
                a = np.clip(a * VOLUME, -1, 1)
        log("síntese %4d ms | %s" % ((time.time() - t0) * 1000, texto[:50]))
        return a, self.sr


def preparar(t):
    """Pequenos ajustes de texto para o Piper ler melhor."""
    return t.replace("%", " por cento").replace("R$", " reais ").replace("&", " e ").replace("  ", " ").strip()


# ---------- reprodução e fila ----------
class Falante:
    def __init__(self, voz):
        self.voz, self.pool = voz, ThreadPoolExecutor(max_workers=1)
        self.cache, self.mu, self.tocando = {}, threading.Lock(), threading.Lock()
        self.geracao, self.parar_ev = 0, threading.Event()

    def _futuro(self, texto):
        with self.mu:
            f = self.cache.get(texto)
            if f is None:
                f = self.cache[texto] = self.pool.submit(self.voz.gerar, texto)
                while len(self.cache) > 12: self.cache.pop(next(iter(self.cache)))
            return f

    def falar(self, texto, proximas=()):
        gen = self.geracao
        fut = self._futuro(texto)
        for p in list(proximas)[:MAX_PROXIMAS]:
            if p.strip(): self._futuro(p.strip())          # pré-síntese enquanto esta frase toca
        try: audio, sr = fut.result()
        except CancelledError: return False                 # /parar cancelou a síntese pendente
        with self.mu: self.cache.pop(texto, None)
        with self.tocando:                                  # uma frase por vez
            if gen != self.geracao: return False            # /parar chegou no meio do caminho
            self.parar_ev.clear()
            if FAKE: self.parar_ev.wait(len(audio) / sr)
            else:
                import sounddevice as sd
                sd.play(audio, sr)
                fim = time.time() + len(audio) / sr + .3
                while time.time() < fim and not self.parar_ev.is_set(): time.sleep(.01)
                sd.stop()
        return True

    def parar(self):
        self.geracao += 1; self.parar_ev.set()
        with self.mu:
            for f in self.cache.values(): f.cancel()
            self.cache.clear()
        if not FAKE:
            try:
                import sounddevice as sd; sd.stop()
            except Exception: pass


# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    falante = None
    def log_message(self, *a): pass
    def _resp(self, cod=200, corpo=None):
        self.send_response(cod)
        for k, v in {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                     "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Private-Network": "true",
                     "Content-Type": "application/json"}.items(): self.send_header(k, v)
        b = json.dumps(corpo or {}).encode(); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_OPTIONS(self): self._resp(204)
    def do_GET(self): self._resp(200, {"ok": True, "servico": "jarvis-voz", "modelo": os.path.basename(MODELO)})
    def do_POST(self):
        try: d = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except Exception: return self._resp(400, {"erro": "json inválido"})
        if self.path == "/parar": self.falante.parar(); return self._resp(200, {"ok": True})
        if self.path == "/falar":
            t = (d.get("texto") or "").strip()
            if not t: return self._resp(400, {"erro": "texto vazio"})
            t0 = time.time(); ok = self.falante.falar(t, d.get("proximas") or [])
            return self._resp(200, {"ok": ok, "ms": int((time.time() - t0) * 1000)})
        self._resp(404, {"erro": "rota desconhecida"})


if __name__ == "__main__":
    voz = Voz(); falante = Falante(voz); H.falante = falante
    if "--teste" in sys.argv:
        falante.falar("Senhor, detectei uma inconsistência nos dados. Os sistemas estão operando normalmente."); sys.exit()
    falante.voz.gerar("Sistemas de voz online.")   # aquece o modelo: a primeira frase real já sai rápida
    log("Servidor de voz pronto em http://%s:%d" % (HOST, PORTA))
    ThreadingHTTPServer((HOST, PORTA), H).serve_forever()
