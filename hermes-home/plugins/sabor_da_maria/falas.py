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

Para equipamentos e técnicas, a menção mais recente também precisa sustentar o
sim/não/aprendendo solicitado. Respostas ambíguas pedem confirmação em vez de
substituir o cadastro por uma interpretação do modelo.
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
    resposta_a: str = field(default="", compare=False, repr=False)
    alcance: frozenset[str] = field(default=frozenset(), compare=False, repr=False)
    numeros: frozenset[float] = field(default=frozenset(), compare=False, repr=False)


def _nova(texto: str, pergunta: str = "", *, resposta_a: str = "") -> Fala:
    alcance = _radicais(texto) | (_radicais(pergunta) if pergunta else set())
    return Fala(texto=texto, pergunta=pergunta, resposta_a=resposta_a or pergunta,
                alcance=frozenset(alcance), numeros=frozenset(_numeros(texto)))


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
    ultimo_usuario = ""
    for m in mensagens or []:
        if not isinstance(m, dict):
            continue
        papel = m.get("role")
        if papel == "assistant":
            texto = _texto(m.get("content")).strip()
            if texto:
                ultima_pergunta = texto
                ultimo_usuario = ""
        elif papel == "user":
            texto = _texto(m.get("content")).strip()
            if texto:
                curta = len(_palavras(texto)) <= RESPOSTA_CURTA
                falas.append(_nova(texto, ultima_pergunta if curta else "", resposta_a=ultima_pergunta))
                ultimo_usuario = texto
        elif papel == "tool":
            falas.extend(_respostas_clarify(m.get("content")))
    atual = _texto(mensagem_atual).strip()
    if atual and atual != ultimo_usuario:
        curta = len(_palavras(atual)) <= RESPOSTA_CURTA
        falas.append(_nova(atual, ultima_pergunta if curta else "", resposta_a=ultima_pergunta))
    return falas


def pergunta_unica(texto: str) -> str:
    """Uma resposta curta só herda uma pergunta real, sem o painel nem o formulário inteiro."""
    perguntas = re.findall(r"[^.!?;\n]+\?", _texto(texto))
    return perguntas[0].strip() if len(perguntas) == 1 else ""


def bocas_fogao_declaradas(fala: Fala) -> Optional[int]:
    """Contagem literal de bocas; um número isolado exige a pergunta do histórico."""
    literal = _texto(fala.texto)
    if re.search(r"[?`\"'“”«»<>]|(?<!\w)-\s*\d", literal):
        return None
    texto = norm_text(literal).strip(" .!")
    texto = re.sub(r"\s*\.?\s*(?:continue|vamos continuar) o prato \d+$", "", texto).strip(" .!")
    numero = r"(?:\d+|" + "|".join(_NUMEROS_POR_EXTENSO) + r")"
    funcionamento = r"(?: funcionando| que funcionam| e todas funcionam)?"
    explicita = re.fullmatch(
        rf"(?:(?:eu )?tenho (?:um )?fogao (?:de|com) |(?:o )?meu fogao tem )({numero}) bocas?{funcionamento}", texto)
    if not explicita:
        explicita = re.fullmatch(rf"(?:eu )?tenho ({numero}) bocas? funcionando no (?:meu )?fogao", texto)
    if not explicita:
        pergunta = norm_text(pergunta_unica(fala.pergunta or fala.resposta_a))
        if not (re.search(r"\bquantas\b", pergunta) and re.search(r"\bbocas\b", pergunta)
                and re.search(r"\bfogao\b", pergunta)):
            return None
        explicita = re.fullmatch(rf"(?:(?:eu )?tenho )?({numero})(?: bocas?{funcionamento})?", texto)
    if not explicita:
        return None
    valor = explicita[1]
    return _NUMEROS_POR_EXTENSO[valor] if valor in _NUMEROS_POR_EXTENSO else int(valor)


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


def apoia(alternativas: Iterable[str], falas: list[Fala], *, literal: bool = False) -> bool:
    """Alguma fala dela cita o item? Basta uma das alternativas (sinônimos), com todas as palavras dela."""
    alternativas = list(alternativas)
    if literal:
        return any(_posicao(alternativas, norm_text(texto), literal=True) is not None for f in falas
                   for texto in (f.texto, f.pergunta))
    return any(_frase_presente(a, f.alcance) for f in falas for a in alternativas)


def ultima_referencia(alternativas: Iterable[str], falas: list[Fala], *, literal: bool = False) -> Optional[Fala]:
    alternativas = tuple(alternativas)
    return next((f for f in reversed(falas) if apoia(alternativas, [f], literal=literal)), None)


def declaracoes_literais(texto: str) -> list[Fala]:
    """Orações atuais da pessoa, excluindo citações, perguntas e hipóteses.

    Este recorte é deliberadamente mais estrito que a evidência de uma tool:
    serve para atualizar estados explícitos antes de qualquer avanço do turno,
    sem converter uma receita colada ou uma fala de terceiro em cadastro.
    """
    sem_citacoes = re.sub(r"```[\s\S]*?```|`[^`]*`|\"[^\"]*\"|'[^']*'|“[^”]*”|«[^»]*»", " ", texto)
    saida = []
    for sentenca in re.findall(r"[^.!?;\n]+[.!?;\n]?", sem_citacoes):
        if (sentenca.lstrip().startswith(">") or sentenca.rstrip().endswith("?")
                or re.search(r"\b(?:por exemplo|como exemplo|hipoteticamente|suponha|supondo)\b", norm_text(sentenca))):
            continue
        normal = norm_text(sentenca)
        # Preserve a contradição inteira para estado_citado devolvê-la como
        # incerta, em vez de transformar "tenho e não tenho" em um não firme.
        contraditoria = re.search(r"\b(?:tenho|sei|domino) e nao (?:tenho|sei|domino)\b", normal)
        separador = (r"," if contraditoria else
                     r",|\b(?:e|mas|porem|so que)\s+(?=(?:eu\s+)?(?:nao\s+|nunca\s+)?(?:tenho|possuo|uso|sei|fiz|faco|domino|consigo|estou|comprei|ganhei|adquiri|acabou|faltou)\b)")
        for trecho in re.split(separador, normal):
            trecho = trecho.strip(" .,;\n")
            if not trecho or re.search(r"\b(?:se eu|se voce|caso|hipoteticamente|suponha|supondo|por exemplo|como exemplo|ela disse|ele disse)\b", trecho):
                continue
            if (re.search(r"\b(?:quando|vontade|intencao|pretendo|gostaria|tivesse|teria)\b", trecho)
                    or (re.search(r"\bse\b", trecho) and not re.search(r"\bnao sei se\b", trecho))):
                continue
            primeira_pessoa = re.match(
                r"^(?:(?:agora|ainda|eu|corrigindo|na verdade)\s+)*(?:"
                r"(?:(?:nao|nunca|jamais)\s+)?(?:tenho|possuo|uso|sei|fiz|faco|domino|consigo|comprei|ganhei|adquiri)\b|"
                r"estou (?:aprendendo|treinando)\b|nao tenho certeza\b|preciso conferir\b|vou conferir\b|"
                r"(?:meu|minha)\s+)", trecho)
            recurso = re.match(r"^(?:(?:agora|ainda)\s+)*(?:(?:acabou|faltou)\s+)?(?:(?:meu|minha|o|a)\s+)?(?:gas|botijao|energia|luz)\b", trecho)
            if primeira_pessoa or recurso:
                saida.extend(extrair([], trecho))
    return saida


_ESTADOS = re.compile(
    r"(?P<incerto>\b(?:talvez|acho|nao sei se|nao tenho certeza|preciso conferir|vou conferir)\b)|"
    r"(?P<parcial>\b(?:aprend\w*|trein\w*|parcial\w*|mais ou menos|sei pouco)\b)|"
    r"(?P<nao>\b(?:nao|nunca|jamais)\s+(?:ainda\s+)?(?:tenho|possuo|uso|tem|sei|fiz|faco|domino|consigo|funciona)\b|\bnao\b(?!\s+(?:so|apenas)))|"
    r"(?P<sim>\b(?:tenho|possuo|uso|tem|sei|domino|faco|consigo|ja fiz|comprei|ganhei|adquiri|sim)\b)"
)
_NOVA_CLAUSULA = re.compile(
    r"(?:,\s*|\b(?:e|mas|porem)\s+)(?=(?:eu\s+)?(?:nao\s+|nunca\s+)?"
    r"(?:tenho|possuo|uso|tem|sei|fiz|faco|domino|consigo|estou|aprendo)\b)"
)


def _singular(palavra: str) -> str:
    return palavra[:-1] if len(palavra) > 3 and palavra.endswith("s") else palavra


def _posicao(termos: Iterable[str], texto: str, *, literal: bool = False) -> Optional[tuple[int, int]]:
    palavras = list(re.finditer(r"[a-z0-9]+", texto))
    normalizar = _singular if literal else _radical
    uteis = [(m, normalizar(m[0])) for m in palavras if m[0] not in _LIGACAO]
    for termo in sorted(termos, key=lambda t: len(_palavras(t)), reverse=True):
        alvo = [normalizar(p) for p in _palavras(termo) if p not in _LIGACAO]
        if not alvo:
            continue
        for i in reversed(range(len(uteis) - len(alvo) + 1)):
            if [r for _, r in uteis[i:i + len(alvo)]] == alvo:
                return uteis[i][0].start(), uteis[i + len(alvo) - 1][0].end()
    return None


def _estado_da_clausula(texto: str, posicao: tuple[int, int]) -> Optional[str]:
    antes, depois = texto[:posicao[0]], texto[posicao[1]:].strip(" ,")
    estados = [m.lastgroup for m in _ESTADOS.finditer(antes)]
    if "incerto" in estados:
        return None
    # Sujeito antes do estado: "molho branco estou aprendendo", "forno não funciona".
    depois = re.sub(r"^(?:(?:eu|mas|ainda|agora|ele|ela|esta|estou|e)\s+)+", "", depois)
    if re.match(r"^(?:talvez|acho|nao sei se|nao tenho certeza|preciso conferir)", depois):
        return None
    if re.match(r"^(?:nao (?:esta )?funciona\w*|quebrad\w*|pifad\w*|nunca fiz|nao sei|nao tenho|nao domino|nao consigo|nao(?:\s*$|,))", depois):
        return "nao"
    if re.match(r"^(?:aprend\w*|trein\w*|parcial\w*|mais ou menos|sei pouco)", depois):
        return "parcial"
    if re.search(r"\b(?:nao|nunca|jamais)\s+(?:tenho|possuo|uso|tem|sei(?: fazer)?|fiz|faco|domino|consigo)\s+(?:(?:um|uma|o|a|nenhum|nenhuma)\s+)?$", antes):
        return "nao"
    if re.match(r"^(?:tenho|possuo|sim|funciona\w*|sei|domino|ja fiz)\b", depois):
        return "sim"
    if not estados:
        return "sim" if re.search(r"\b(?:meu|minha)\s*$", antes) else None
    if "incerto" in estados or len(set(estados)) > 1:
        return None
    return estados[-1]


def estado_citado(alternativas: Iterable[str], fala: Fala, *, pergunta_unica: bool = True,
                  literal: bool = False) -> Optional[str]:
    """Estado explícito do item nesta fala; não interpreta ironia ou referências indiretas.

    Orações novas não herdam a negação de outra lista. Perguntas e afirmações
    conflitantes ficam pendentes. A interpretação de uma resposta curta exige
    que a pergunta seja sobre um único item.
    """
    termos = tuple(alternativas)
    estados = []
    citou = False
    for sentenca in re.findall(r"[^.!?;\n]+[.!?;\n]?", fala.texto):
        pergunta = sentenca.rstrip().endswith("?")
        normal = norm_text(sentenca)
        if (_posicao(termos, normal, literal=literal) is not None and
                re.search(r"\b(?:(?:tenho|sei|domino) e nao (?:tenho|sei|domino)|nao (?:tenho|sei|domino) e (?:tenho|sei|domino))\b", normal)):
            citou = True
            estados.append(None)
            continue
        for clausula in _NOVA_CLAUSULA.split(normal):
            posicao = _posicao(termos, clausula, literal=literal)
            if posicao is not None:
                citou = True
                estados.append(None if pergunta else _estado_da_clausula(clausula, posicao))
    if citou:
        if re.search(r"\b(?:agora|corrigindo|na verdade|atualizando)\b", norm_text(fala.texto)):
            return estados[-1]
        return estados[0] if estados and estados[0] is not None and len(set(estados)) == 1 else None
    if not fala.pergunta or not pergunta_unica:
        return None
    texto = norm_text(fala.texto).strip("., ")
    if texto in {"nao", "nao tenho", "nao sei", "nao sei fazer", "nunca", "nunca fiz", "ainda nao"}:
        return "nao"
    if texto in {"sim", "tenho", "tenho sim", "sei", "sei sim", "sei fazer", "funciona", "funciona sim"}:
        return "sim"
    if re.fullmatch(r"(?:ainda )?(?:estou )?(?:aprendendo|treinando|mais ou menos)", texto):
        return "parcial"
    if re.fullmatch(r"\d+(?:[.,]\d+)?(?: bocas?)?", texto):
        return "sim" if next(iter(_numeros(texto)), 0) > 0 else "nao"
    return None


def disponibilidade_citada(alternativas: Iterable[str], fala: Fala) -> Optional[str]:
    """Disponibilidade literal de gás/energia, sem inferir um recurso pelo outro."""
    encontrados = []
    for sentenca in re.findall(r"[^.!?;\n]+[.!?;\n]?", fala.texto):
        texto = norm_text(sentenca)
        posicao = _posicao(alternativas, texto)
        if posicao is None:
            continue
        if sentenca.rstrip().endswith("?"):
            encontrados.append(None)
            continue
        antes, depois = texto[:posicao[0]], texto[posicao[1]:].strip(" ,")
        depois = re.sub(r"^(?:(?:mas|ainda|agora|ele|ela|esta|e|aqui|daqui|de botijao|botijao|encanado)\s+)+", "", depois)
        if re.match(r"^(?:talvez|acho|nao tenho certeza|preciso conferir)", depois) or re.search(r"\b(?:talvez|nao sei se|nao tenho certeza)\b", antes):
            estado = None
        elif (re.match(r"^(?:acabou|faltou|indisponivel|insuficiente|sem\b|nao (?:tenho|tem|ha|funciona))", depois)
              or re.search(r"\b(?:sem|acabou (?:o|a)?|nao (?:tenho|tem|ha))\s*$", antes)):
            estado = "nao"
        elif re.match(r"^(?:caindo|cai\b|caiu|instavel|oscila\w*|intermitente|acabando|risco\b|nao (?:e |esta )?estavel)", depois):
            estado = "parcial"
        elif re.match(r"^(?:estavel|suficiente|garantid\w*|normal|ok|cheio|botijao|encanado)\b", depois):
            estado = "sim"
        else:
            estado = None
        encontrados.append(estado)
    if encontrados:
        return encontrados[-1]
    if fala.pergunta:
        pergunta = norm_text(fala.pergunta)
        if re.search(r"\b(?:gas|botijao|encanado)\b", pergunta) and re.search(r"\b(?:energia|luz|eletrica)\b", pergunta):
            return None
        resposta = norm_text(fala.texto).strip("., ")
        if resposta in {"nao", "nao tenho", "acabou", "sem gas", "sem energia", "sem luz", "indisponivel"}:
            return "nao"
        if resposta in {"esta caindo", "cai as vezes", "instavel", "oscilando", "esta acabando", "acabando", "risco de acabar"}:
            return "parcial"
        if resposta in {"sim", "estavel", "normal", "ok", "cheio", "garantido"}:
            return "sim"
    return None


def condicoes_do_lote(texto: str) -> dict[str, tuple[str, str]]:
    """Declarações explícitas, positivas ou negativas, sobre este preparo."""
    regras = {
        "gas": (r"\b(?:gas|botijao)\b", r"\b(?:suficientes?|garant\w*|reserva)\b"),
        "energia": (r"\b(?:energia|luz)\b", r"\b(?:suficientes?|garant\w*|estavel)\b"),
        "espaco_geladeira": (r"\b(?:geladeira|freezer)\b", r"\b(?:cabem?|espaco|guard\w*)\b"),
    }
    confirmacoes: dict[str, tuple[str, str]] = {}
    for frase in re.findall(r"[^.!?;\n]+[.!?;\n]?", texto):
        literal = frase.strip().rstrip(";")
        if not literal or literal.endswith("?") or literal.startswith(">") or re.search(r"""["“”«»`]""", literal):
            continue
        normal = norm_text(literal)
        if (not re.search(r"\b(?:lote|receita|prato|preparo|ingredientes|marmitas)\b", normal)
                or re.search(r"\b(?:talvez|se|acho|poderia|queria|gostaria|mas|porem|exceto|nao sei|nao tenho certeza)\b", normal)):
            continue
        if not re.match(
                r"^(?:(?:sim|agora|eu|aqui)[, ]+)*(?:nao\s+)?"
                r"(?:(?:tenho|temos|tem|ha|garanto|consigo|cabem?|comporta)\b|"
                r"(?:o|a|este|esse|os|as)\s+(?:gas|energia|lote|ingredientes|porcoes)\b.*?"
                r"\b(?:cabem?|est[ao]o? garantid\w*)\b)", normal):
            continue
        estado = "nao" if re.search(r"\bnao\b", normal) else "sim"
        for chave, (recurso, condicao) in regras.items():
            if re.search(recurso, normal) and re.search(condicao, normal):
                confirmacoes[chave] = (estado, literal)
    return confirmacoes


def apoia_numero(valor: float, falas: list[Fala], *, minutos: bool = False) -> bool:
    """O número aparece na fala dela? Em minutos, ``2 horas`` também sustenta 120."""
    alvos = {float(valor)}
    if minutos and valor:
        alvos.add(float(valor) / 60)
    return any(alvo in f.numeros for f in falas for alvo in alvos)


def contem_frase(frases: Iterable[str], falas: list[Fala]) -> bool:
    """Alguma fala dela tem a frase inteira, palavra por palavra ('zona leste')? É mais exigente que ``apoia``, que só pede as palavras no
    mesmo texto: 'Rio Grande do Sul, zona norte' não sustenta 'zona sul'."""
    alvos = [f" {' '.join(_palavras(f))} " for f in frases if _palavras(f)]
    return any(a in f" {' '.join(_palavras(fala.texto))} " for fala in falas for a in alvos)


def cita_sigla(sigla: str, falas: list[Fala]) -> bool:
    """A sigla (SP) escrita em maiúsculas e sozinha: 'pa' e 'ma' são palavras, 'PA' e 'MA' são estados."""
    padrao = re.compile(rf"(?<![A-Za-zÀ-ÿ]){re.escape(sigla)}(?![A-Za-zÀ-ÿ])")
    return any(padrao.search(f.texto) for f in falas)


_TEMPO = re.compile(r"\b(\d+(?:[.,]\d+)?|um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez)\s*(horas?|h|minutos?|min)\b")
_NUMERO_TEMPO = r"(?:\d+(?:[.,]\d+)?|um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez)"
_DURACAO_DECLARADA = re.compile(
    rf"(?:(?P<meia>meia hora)|(?P<horas>{_NUMERO_TEMPO})\s*(?:horas?|h)"
    rf"(?:\s*(?:e\s+)?(?:(?P<metade>meia)|(?P<resto>{_NUMERO_TEMPO})\s*(?:minutos?|min)))?|"
    rf"(?P<minutos>{_NUMERO_TEMPO})\s*(?:minutos?|min))")


def _minutos_declarados(texto: str) -> Optional[float]:
    medida = _DURACAO_DECLARADA.fullmatch(texto.strip())
    if not medida:
        return None

    def numero(valor: Optional[str]) -> float:
        if not valor:
            return 0
        return float(_NUMEROS_POR_EXTENSO[valor]) if valor in _NUMEROS_POR_EXTENSO else float(valor.replace(",", "."))

    total = (30 if medida["meia"] else numero(medida["horas"]) * 60
             + (30 if medida["metade"] else numero(medida["resto"])) + numero(medida["minutos"]))
    return total if total > 0 else None


def _pergunta_de_disponibilidade(texto: str) -> bool:
    """Exige uma pergunta real sobre o tempo da pessoa, não a duração da receita."""
    for trecho in re.findall(r"[^.!?;\n]+\?", _texto(texto)):
        pergunta = norm_text(trecho)
        if (re.search(r"\b(?:quanto tempo|quantos minutos|quantas horas)\b", pergunta)
                and re.search(r"\b(?:voce|sua|seu)\b", pergunta)
                and re.search(r"\b(?:consegue|pode|tem|disponivel|disponibilidade)\b", pergunta)
                and re.search(r"\b(?:dedicar|cozinhar|cozinhada|preparo|preparar)\b", pergunta)):
            return True
    return False


def tempo_disponivel_declarado(fala: Fala) -> Optional[float]:
    """Minutos disponíveis ditos agora, sem inferir capacidade a partir da receita.

    Uma duração isolada só responde à pergunta de disponibilidade imediatamente
    anterior. Citações, hipóteses, negações e limites inferiores não viram um
    limite operacional salvo. O chamador faz apenas o merge desse campo.
    """
    texto = _texto(fala.texto)
    sem_citacoes = re.sub(r"```[\s\S]*?```|`[^`]*`|\"[^\"]*\"|'[^']*'|“[^”]*”|«[^»]*»", " ", texto)
    sem_citacoes = re.sub(r"(?m)^\s*>.*$", " ", sem_citacoes)
    # Um sinal negativo não pode desaparecer na normalização de texto.
    if re.search(r"(?<!\w)-\s*\d|[<>]", sem_citacoes):
        return None
    candidatos = []
    # Preserve decimais com ponto ao separar frases; perguntas não são fatos.
    for sentenca in re.findall(r"(?:(?<=\d)\.(?=\d)|[^.!?;\n])+[.!?;\n]?", sem_citacoes):
        if sentenca.rstrip().endswith("?"):
            continue
        normal = norm_text(sentenca).strip(" .")
        if re.search(r"\b(?:nao|nunca|jamais|talvez|acho|depende|se|caso|teria|tivesse|pretendo|gostaria|suponha|supondo|hipoteticamente|por exemplo|como exemplo|no minimo|pelo menos|mais de|menos de|entre|mas|porem|exceto|so que)\b", normal):
            continue
        for trecho in re.split(r",\s*(?!\d)|\be\s+(?=(?:eu\s+)?(?:tenho|posso|consigo|disponho)\b)", normal):
            literal = re.fullmatch(
                r"(?:(?:eu|agora|corrigindo|na verdade)\s+)*(?:tenho|disponho de|posso dedicar|consigo dedicar)\s+"
                r"(?:(?:ate|no maximo|apenas|somente|so)\s+)?(.+?)\s+"
                r"(?:(?:livres?|disponiveis?)\s+)?(?:por cozinhada|a cada cozinhada|para cada cozinhada|"
                r"para (?:cozinhar|esta cozinhada|essa cozinhada|o preparo|este preparo))", trecho.strip())
            if literal:
                minutos = _minutos_declarados(literal[1])
                if minutos is not None:
                    candidatos.append(minutos)
    if candidatos:
        return candidatos[0] if len(set(candidatos)) == 1 else None

    # Resposta numérica curta: o contexto vem exclusivamente da pergunta do
    # histórico real associado à Fala, nunca do painel ou de outra conversa.
    if "?" in sem_citacoes or not _pergunta_de_disponibilidade(fala.pergunta or fala.resposta_a):
        return None
    resposta = norm_text(sem_citacoes).strip(" .")
    resposta = re.sub(r"^(?:(?:eu\s+)?tenho\s+)?(?:(?:ate|no maximo|apenas|somente|so)\s+)?", "", resposta)
    return _minutos_declarados(resposta)


def tempo_disponivel_faixa_declarada(fala: Fala) -> Optional[dict[str, Any]]:
    """Preserva limites estritos; 'mais de 2 h' não declara um máximo de 120 min."""
    texto = _texto(fala.texto)
    texto = re.sub(r"```[\s\S]*?```|`[^`]*`|\"[^\"]*\"|'[^']*'|“[^”]*”|«[^»]*»", " ", texto)
    texto = re.sub(r"(?m)^\s*>\s*(?=[A-Za-zÀ-ÿ]).*$", " ", texto)
    if re.search(r"(?<!\w)-\s*\d", texto):
        return None

    def faixa(expressao: str) -> Optional[dict[str, Any]]:
        medida = re.fullmatch(r"(menos de|mais de)\s+(.+)", expressao.strip())
        minutos = _minutos_declarados(medida[2]) if medida else None
        return {"operador": "menor_que" if medida[1] == "menos de" else "maior_que", "minutos": minutos} if minutos else None

    encontrados = []
    for sentenca in re.findall(r"(?:(?<=\d)\.(?=\d)|[^.!?;\n])+[.!?;\n]?", texto):
        if sentenca.rstrip().endswith("?"):
            continue
        normal = norm_text(sentenca).strip(" .")
        if re.search(r"\b(?:nao|nunca|jamais|talvez|acho|depende|se|caso|teria|tivesse|pretendo|gostaria|suponha|supondo|hipoteticamente|por exemplo|como exemplo|mas|porem|exceto|so que)\b", normal):
            continue
        literal = re.fullmatch(
            r"(?:(?:eu|agora|corrigindo|na verdade)\s+)*(?:tenho|disponho de|posso dedicar|consigo dedicar)\s+"
            r"((?:menos de|mais de)\s+.+?)\s+(?:por cozinhada|a cada cozinhada|para cada cozinhada|"
            r"para (?:cozinhar|esta cozinhada|essa cozinhada|o preparo|este preparo))", normal)
        if literal and (valor := faixa(literal[1])):
            encontrados.append(valor)
    if encontrados:
        return encontrados[0] if all(v == encontrados[0] for v in encontrados) else None
    if "?" in texto or not _pergunta_de_disponibilidade(fala.pergunta or fala.resposta_a):
        return None
    # Símbolos isolados são úteis ao digitar o próprio rótulo do botão. Não
    # aceite uma linha citada ('> Tenho ...') como uma declaração da pessoa.
    resposta = texto.strip().rstrip(".")
    resposta = re.sub(r"^<\s*(?=\d)", "menos de ", resposta)
    resposta = re.sub(r"^>\s*(?=\d)", "mais de ", resposta)
    return faixa(norm_text(resposta))


PERGUNTA_TEMPO_DA_RECEITA = "Quanto tempo costuma levar esse preparo completo, incluindo preparo e cozimento?"


def tempo_da_receita_declarado(fala: Fala) -> Optional[float]:
    """Interpreta uma estimativa curta quando a pergunta era sobre a ficha."""
    if (PERGUNTA_TEMPO_DA_RECEITA not in fala.resposta_a
            or _pergunta_de_disponibilidade(fala.resposta_a)
            or re.search(r"""[?`"'“”«»<>]|(?<!\w)-\s*\d""", fala.texto)):
        return None
    resposta = norm_text(fala.texto).strip(" .")
    resposta = re.sub(r"^(?:leva |demora )?(?:cerca de |mais ou menos |aproximadamente )?", "", resposta)
    return _minutos_declarados(resposta)


def unidade_do_numero(valor: float, falas: list[Fala]) -> Optional[str]:
    """``"h"`` ou ``"min"`` quando ela disse o número com a unidade ('2 horas', 'duas horas', '90 minutos'); ``None`` se não deu para saber."""
    achadas: set[str] = set()
    for f in falas:
        for numero, unidade in _TEMPO.findall(norm_text(f.texto)):
            n = float(_NUMEROS_POR_EXTENSO[numero]) if numero in _NUMEROS_POR_EXTENSO else float(numero.replace(",", "."))
            if n == float(valor):
                achadas.add("h" if unidade.startswith("h") else "min")
    return achadas.pop() if len(achadas) == 1 else None


def cobertura(texto: str, falas: list[Fala]) -> float:
    """Fração das palavras de conteúdo de ``texto`` que aparecem nas falas dela (para observações livres)."""
    radicais = {_radical(p) for p in _palavras(texto) if len(p) >= 4 and p not in _IGNORAR_EM_OBSERVACAO}
    if not radicais:
        return 1.0
    todas = set().union(*(f.alcance for f in falas)) if falas else set()
    return len(radicais & todas) / len(radicais)
