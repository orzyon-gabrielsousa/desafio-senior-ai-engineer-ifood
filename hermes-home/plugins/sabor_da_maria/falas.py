"""O que a Dona Maria realmente disse: base da trava de evidência do perfil da cozinha.

O modelo costuma "completar" o perfil com o que parece razoável: técnicas que ela nunca citou,
utensílios que ninguém perguntou. Para a trava não depender da boa vontade dele, o plugin guarda as
falas dela por sessão (as mensagens e as respostas do ``clarify``) e ``perfil_cozinha_salvar`` só
grava o item que tem base nelas.

Como as falas chegam, sem tocar no core do Hermes (hooks públicos de plugin):

* ``pre_llm_call``  (início de cada turno): refaz a lista a partir do histórico + a mensagem do turno.
* ``post_tool_call`` do ``clarify``: acrescenta a resposta que acabou de chegar no meio do turno
  (na TUI é assim que ela responde às perguntas com opções).

O que vale como "base":

* a fala dela cita o item (o nome, ou um sinônimo de cozinha) e, quando o valor é um número, o número
  também aparece (``2 horas`` sustenta ``120`` minutos);
* resposta curta ou do ``clarify`` ("tenho sim") conta junto com a pergunta a que respondeu, senão ela
  nunca poderia responder "sim" a "você tem forno?".

Limite assumido: a trava impede **gravar o que ela não falou**; não confere se o sim/não bate com a fala
(isso fica para o modelo e para os evals).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .units import norm_text

logger = logging.getLogger(__name__)

MAX_SESSOES = 16
RESPOSTA_CURTA = 8  # até tantas palavras: conta como resposta à última pergunta da consultora
# O painel do carrinho (painel.py) vai junto da mensagem do turno; não são palavras dela e não podem virar "evidência".
MARCA_PAINEL = "[painel da tela"

_PALAVRAS = re.compile(r"[a-z0-9]+")
_NUMEROS = re.compile(r"\d+(?:[.,]\d+)?")
_LIGACAO = {"de", "da", "do", "das", "dos", "a", "o", "as", "os", "e", "com", "em", "na", "no", "para", "por", "um", "uma"}
_NUMEROS_POR_EXTENSO = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
                        "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10}
_IGNORAR_EM_OBSERVACAO = {"dona", "maria", "disse", "tem", "usa", "pode", "fica", "para", "como", "mais", "muito"}


def _radical(palavra: str) -> str:
    """Tira o plural e fica com 4 letras: molhos/molho, assados/assar, fritura/fritar casam."""
    if len(palavra) > 3 and palavra.endswith("s"):
        palavra = palavra[:-1]
    return palavra[:4] if len(palavra) > 4 else palavra


def _palavras(texto: str) -> list[str]:
    return _PALAVRAS.findall(norm_text(texto))


def _radicais(texto: str) -> set[str]:
    return {_radical(p) for p in _palavras(texto)}


def _numeros(texto: str) -> set[float]:
    achados = {float(n.replace(",", ".")) for n in _NUMEROS.findall(texto)}
    achados |= {float(_NUMEROS_POR_EXTENSO[p]) for p in _palavras(texto) if p in _NUMEROS_POR_EXTENSO}
    return achados


@dataclass(frozen=True)
class Fala:
    texto: str
    pergunta: str = ""
    alcance: frozenset[str] = field(default=frozenset(), compare=False, repr=False)
    numeros: frozenset[float] = field(default=frozenset(), compare=False, repr=False)


def _nova(texto: str, pergunta: str = "") -> Fala:
    alcance = _radicais(texto) | (_radicais(pergunta) if pergunta else set())
    return Fala(texto=texto, pergunta=pergunta, alcance=frozenset(alcance), numeros=frozenset(_numeros(texto)))


def _texto(conteudo: Any) -> str:
    """Conteúdo de mensagem: texto simples ou lista de partes ``{"type": "text", "text": ...}``, sem o painel da tela."""
    if isinstance(conteudo, list):
        conteudo = " ".join(str(p.get("text", "")) for p in conteudo if isinstance(p, dict) and p.get("type", "text") == "text")
    if not isinstance(conteudo, str):
        return ""
    corte = conteudo.find(MARCA_PAINEL)
    return conteudo if corte < 0 else conteudo[:corte]


def _respostas_clarify(resultado: Any) -> list[Fala]:
    """Respostas do ``clarify``: ``{"responses": [{"question": ..., "user_response": ...}]}``."""
    if isinstance(resultado, str) and '"user_response"' not in resultado:
        return []  # resultado de outra tool: nem tenta ler o JSON (pode ser enorme)
    try:
        dados = json.loads(resultado) if isinstance(resultado, str) else resultado
    except ValueError:
        return []
    if not isinstance(dados, dict) or not isinstance(dados.get("responses"), list):
        return []
    saida = []
    for resposta in dados["responses"]:
        if not isinstance(resposta, dict):
            continue
        valor = resposta.get("user_response")
        if isinstance(valor, list):
            valor = ", ".join(str(v) for v in valor)
        if isinstance(valor, str) and valor.strip():
            saida.append(_nova(valor.strip(), str(resposta.get("question") or "")))
    return saida


def extrair(mensagens: Iterable[Any], mensagem_atual: Any = None) -> list[Fala]:
    """Falas dela num histórico no formato de mensagens do Hermes (roles user/assistant/tool)."""
    falas: list[Fala] = []
    ultima_pergunta = ""
    for m in mensagens or []:
        if not isinstance(m, dict):
            continue
        papel = m.get("role")
        if papel == "assistant":
            texto = _texto(m.get("content")).strip()
            if texto:
                ultima_pergunta = texto
        elif papel == "user":
            texto = _texto(m.get("content")).strip()
            if texto:
                curta = len(_palavras(texto)) <= RESPOSTA_CURTA
                falas.append(_nova(texto, ultima_pergunta if curta else ""))
        elif papel == "tool":
            falas.extend(_respostas_clarify(m.get("content")))
    atual = _texto(mensagem_atual).strip()
    if atual and not any(f.texto == atual for f in falas[-2:]):
        curta = len(_palavras(atual)) <= RESPOSTA_CURTA
        falas.append(_nova(atual, ultima_pergunta if curta else ""))
    return falas


# ----------------------------------------------------------------------------- memória por sessão
_LOCK = threading.Lock()
_SESSOES: "OrderedDict[str, list[Fala]]" = OrderedDict()


def _guardar(sessao: str, falas: list[Fala]) -> None:
    with _LOCK:
        _SESSOES[sessao] = falas
        _SESSOES.move_to_end(sessao)
        while len(_SESSOES) > MAX_SESSOES:
            _SESSOES.popitem(last=False)


def registrar(sessao: Optional[str], mensagens: Iterable[Any], mensagem_atual: Any = None) -> None:
    """Refaz as falas da sessão a partir do histórico (idempotente: serve para todo turno)."""
    if sessao:
        _guardar(str(sessao), extrair(mensagens, mensagem_atual))


def registrar_resposta(sessao: Optional[str], resultado_clarify: Any) -> None:
    """Acrescenta a resposta de um ``clarify`` que chegou no meio do turno."""
    novas = _respostas_clarify(resultado_clarify)
    if not sessao or not novas:
        return
    with _LOCK:
        atuais = list(_SESSOES.get(str(sessao), []))
    _guardar(str(sessao), atuais + [f for f in novas if f not in atuais])


def da_sessao(sessao: Optional[str]) -> Optional[list[Fala]]:
    """Falas da sessão, ou ``None`` quando o plugin ainda não viu essa sessão."""
    if not sessao:
        return None
    with _LOCK:
        falas = _SESSOES.get(str(sessao))
        return list(falas) if falas is not None else None


def esquecer_tudo() -> None:
    with _LOCK:
        _SESSOES.clear()


# ----------------------------------------------------------------------------- hooks do Hermes
def ao_iniciar_turno(session_id: str = "", user_message: Any = None, conversation_history: Optional[list] = None, **_: Any) -> None:
    """Hook ``pre_llm_call``: só observa (devolve ``None``)."""
    try:
        registrar(session_id, conversation_history or [], user_message)
    except Exception:  # noqa: BLE001 - um hook nunca pode derrubar o turno
        logger.exception("falas: não consegui registrar o turno")


def apos_tool(tool_name: str = "", result: Any = None, session_id: str = "", **_: Any) -> None:
    """Hook ``post_tool_call``: guarda a resposta do ``clarify`` assim que ela chega."""
    if tool_name != "clarify":
        return
    try:
        registrar_resposta(session_id, result)
    except Exception:  # noqa: BLE001
        logger.exception("falas: não consegui registrar a resposta do clarify")


# ----------------------------------------------------------------------------- conferência
def _frase_presente(frase: str, alcance: frozenset[str]) -> bool:
    radicais = [_radical(p) for p in _palavras(frase) if p not in _LIGACAO]
    return bool(radicais) and all(r in alcance for r in radicais)


def apoia(alternativas: Iterable[str], falas: list[Fala]) -> bool:
    """Alguma fala dela cita o item? Basta uma das alternativas (sinônimos), com todas as palavras dela."""
    alternativas = list(alternativas)
    return any(_frase_presente(a, f.alcance) for f in falas for a in alternativas)


def apoia_numero(valor: float, falas: list[Fala], *, minutos: bool = False) -> bool:
    """O número aparece na fala dela? Em minutos, ``2 horas`` também sustenta 120."""
    alvos = {float(valor)}
    if minutos and valor:
        alvos.add(float(valor) / 60)
    return any(alvo in f.numeros for f in falas for alvo in alvos)


def cobertura(texto: str, falas: list[Fala]) -> float:
    """Fração das palavras de conteúdo de ``texto`` que aparecem nas falas dela (para observações livres)."""
    radicais = {_radical(p) for p in _palavras(texto) if len(p) >= 4 and p not in _IGNORAR_EM_OBSERVACAO}
    if not radicais:
        return 1.0
    todas = set().union(*(f.alcance for f in falas)) if falas else set()
    return len(radicais & todas) / len(radicais)
