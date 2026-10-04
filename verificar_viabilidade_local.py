#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Avalia (SOMENTE LEITURA - não instala nem baixa nada) se este aparelho aguenta um modelo de linguagem local.
Rode no Termux do J7 Prime com o servidor JARVIS ligado, para medir a RAM livre de verdade:   python verificar_viabilidade_local.py
"""
import os, platform, shutil, subprocess

def prop(nome):
    try: return subprocess.run(["getprop", nome], capture_output=True, text=True, timeout=3).stdout.strip() or "?"
    except Exception: return "?"

def meminfo():
    d = {}
    try:
        for l in open("/proc/meminfo"):
            k, v = l.split(":"); d[k] = int(v.split()[0]) / 1024   # MB
    except OSError: pass
    return d

m = meminfo(); livre = m.get("MemAvailable", 0); total = m.get("MemTotal", 0)
flags = ""
try: flags = next(l for l in open("/proc/cpuinfo") if l.lower().startswith(("features", "flags")))
except (OSError, StopIteration): pass
print("Aparelho ........ %s (Android %s)" % (prop("ro.product.model"), prop("ro.build.version.release")))
print("Arquitetura ..... %s | núcleos: %s" % (platform.machine(), os.cpu_count()))
print("RAM total ....... %.0f MB | LIVRE agora: %.0f MB" % (total, livre))
print("Armazenamento ... %.1f GB livres" % (shutil.disk_usage(os.path.expanduser("~")).free / 1e9))
print("Dot-product ARM . %s | fp16 nativo: %s   (ausentes = inferência mais lenta)" % ("sim" if "asimddp" in flags else "NÃO", "sim" if "asimdhp" in flags else "NÃO"))
reserva = 400   # folga para Android + Termux + este servidor
util = livre - reserva
print("\nRAM utilizável por um modelo (livre - %d MB de folga): %.0f MB" % (reserva, util))
if util < 500: veredito = "NÃO recomendado: nem um modelo de 0,5B (~0,45 GB) cabe com segurança."
elif util < 1100: veredito = "Limite: só ~0,5B quantizado (Q4, ~0,45 GB). Lento e fraco em português; serve no máximo para tarefas simples."
elif util < 2000: veredito = "Possível: até ~1B-1,5B em Q4. Ainda lento em CPU de 1,6 GHz (A53)."
else: veredito = "Folgado: modelos de ~3B em Q4 são possíveis, ainda que lentos."
print("Veredito ........ " + veredito)
print("\nObservação: a velocidade real só se mede rodando o modelo (llama-bench). Este script NÃO instala nem executa nenhum modelo.")
