# -*- coding: utf-8 -*-
"""Interface comum dos providers (local ou externo) + helper HTTP só com a biblioteca padrão (funciona no Termux)."""
import json, re, urllib.error, urllib.request


class ProviderErro(Exception):
    def __init__(self, msg, status=None, offline=False):
        super().__init__(msg); self.status, self.offline = status, offline


class Provider:
    nome = "base"
    requer_internet = False
    host = None                      # host para teste de internet (providers externos)

    def capacidades(self):           # conjunto de capacidades que este provider resolve
        return frozenset()
    def suporta(self, capacidade):
        return capacidade in self.capacidades()
    def disponivel(self):            # configurado e pronto para uso?
        return False
    def generate(self, mensagem, historico=None, sistema=None, imagem=None):
        raise NotImplementedError


def post_json(url, payload, headers=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ProviderErro("HTTP %s" % e.code, status=e.code)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ProviderErro("sem conexão: %s" % e, offline=True)
    except ValueError:
        raise ProviderErro("resposta inválida")


def texto_da_resposta(dados):
    """Extrai o texto de uma resposta no formato OpenAI (chat/completions) e remove blocos <think>."""
    try: t = dados["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError): raise ProviderErro("resposta sem conteúdo")
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
    if not t: raise ProviderErro("resposta vazia")
    return t


def montar_mensagens(mensagem, historico, sistema, imagem=None):
    msgs = [{"role": "system", "content": sistema}] if sistema else []
    msgs += [{"role": h["role"], "content": h["content"]} for h in (historico or []) if h.get("role") in ("user", "assistant")]
    if imagem:
        url = imagem if imagem.startswith("data:") else "data:image/jpeg;base64," + imagem
        msgs.append({"role": "user", "content": [{"type": "text", "text": mensagem}, {"type": "image_url", "image_url": {"url": url}}]})
    else:
        msgs.append({"role": "user", "content": mensagem})
    return msgs
