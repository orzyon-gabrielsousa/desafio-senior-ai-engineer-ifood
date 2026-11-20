"""Tools do plugin ``sabor_da_maria`` expostas ao Hermes Agent.

Cada handler recebe ``args: dict`` e devolve uma string JSON (contrato do
``ctx.register_tool``). Erros nunca estouram: voltam como ``{"success": false,
"error": ...}`` para o modelo decidir o que fazer.

As tools cobrem as quatro etapas do desafio:

* 2.1/2.3  despensa_listar, despensa_buscar, despensa_atualizar, receita_conferir_ingredientes
* 2.2      perfil_cozinha_consultar, perfil_cozinha_salvar (elicitação com checklist; a tela Minha cozinha grava no mesmo perfil)
* negócio  negocio_salvar (onde a loja fica, tipo de negócio e de alimentação, contados na conversa; a tela Meu negócio grava no mesmo registro)
* 2.4      prato_calcular_cmv, preco_sugerir, cardapio_registrar, cardapio_remover, cardapio_consultar
* carrinho  carrinho_consultar, carrinho_adicionar, carrinho_adicionar_prato, carrinho_remover, carrinho_montar_da_receita, salvos_salvar
            (saldo = orçamento − carrinho; a tela da Dona Maria mexe no mesmo carrinho pela API)

Travas no backend (não dependem do que o modelo diz): ``prato_calcular_cmv`` e
``cardapio_registrar`` reavaliam o prato contra o estado atual (perfil salvo, despensa
disponível, orçamento restante) e recusam com ``bloqueado: true`` + ``bloqueios`` enquanto
houver requisito sem resposta ou impedimento conhecido. ``cardapio_registrar`` ainda recusa
se CMV/compras mudaram desde a apresentação, e só ele reserva estoque e compromete orçamento.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import math
import os
import re
from datetime import datetime
from typing import Any, Callable, Iterable, Optional
from uuid import NAMESPACE_URL, uuid5

from . import analytics, compras, costing, cozinha, escolhas, falas, fonte, montagem, pesquisa, precificacao, taxas, vendas, vendas_falas
from . import negocio as negocio_mod
from .matching import cortes_incompativeis, formas_incompativeis, preparo_caseiro_entre, score as score_ingrediente, tokens
from .store import OrcamentoEstourado, Store
from .units import format_base, materia_prima_do_preparo, norm_text, parse_pantry_unit, to_base

logger = logging.getLogger(__name__)
TOOLSET = "sabor_da_maria"


# ----------------------------------------------------------------------------- infra
def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _tool(fn: Callable[..., dict]) -> Callable[..., str]:
    # O Hermes só entrega session_id, task_id etc. ao handler que os declara na assinatura (com functools.wraps a
    # assinatura de `wrapper` é a de `fn`): a tool pede o que precisa e o resto é descartado aqui.
    contexto = {nome for nome in inspect.signature(fn).parameters if nome != "args"}

    @functools.wraps(fn)
    def wrapper(args: Optional[dict] = None, **kwargs: Any) -> str:
        try:
            return _json(fn(dict(args or {}), **{k: v for k, v in kwargs.items() if k in contexto}))
        except (ValueError, KeyError) as exc:  # pedido que não dá para atender: o modelo lê o motivo e corrige, sem traceback no log
            motivo = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc  # str(KeyError) vem entre aspas
            logger.warning("tool %s recusou o pedido: %s", fn.__name__, motivo, exc_info=logger.isEnabledFor(logging.DEBUG))
            return _json({"success": False, "error": f"{type(exc).__name__}: {motivo}"})
        except Exception as exc:  # noqa: BLE001 - o modelo precisa ver o erro, não um traceback
            logger.exception("tool %s falhou", fn.__name__)
            return _json({"success": False, "error": f"{type(exc).__name__}: {exc}"})
    return wrapper


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace("r$", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def _nivel(value: Any) -> Optional[str]:
    """Interpreta respostas livres como sim / nao / parcial."""
    return cozinha.nivel(value)


# ----------------------------------------------------------------------------- perfil (2.2)
CHECKLIST: list[tuple[str, str, str]] = [
    ("equipamentos", "fogao_bocas", "Seu fogão tem quantas bocas e todas funcionam?"),
    ("equipamentos", "forno", "Você tem forno funcionando (do fogão ou elétrico)?"),
    ("equipamentos", "panela_pressao", "Tem panela de pressão?"),
    ("equipamentos", "air_fryer", "Tem air fryer?"),
    ("equipamentos", "liquidificador", "Tem liquidificador?"),
    ("restricoes", "espaco_geladeira", "Quanto espaço sobra na geladeira para ingredientes e marmitas prontas?"),
    ("restricoes", "tempo_por_cozinhada_min", "Quanto tempo você consegue dedicar a cada cozinhada?"),
    ("restricoes", "gas", "Seu gás é de botijão ou encanado, e dá conta de cozinhar várias vezes na semana?"),
    ("restricoes", "energia", "A energia elétrica aí é estável para usar air fryer, liquidificador e geladeira sem susto?"),
]

def _equip_status(equipamentos: dict[str, Any], key: str) -> Optional[str]:
    key = cozinha.chave_equipamento(key)
    if key in cozinha.POR_ID:
        achado = cozinha.estado_equipamento(equipamentos, key)  # o id, uma variante ou um nome antigo do mesmo item
        if achado is not None:
            return achado
    return _nivel(equipamentos.get(key))  # Uma panela comum não confirma uma panela de pressão.


def _tecnica_status(tecnicas: dict[str, Any], nome: str) -> Optional[str]:
    if (chave := cozinha.chave_tecnica(nome)) in tecnicas:
        return _nivel(tecnicas[chave])
    alvo = norm_text(nome)
    alvo_tok = tokens(nome)
    for k, v in tecnicas.items():
        kn = norm_text(k.replace("_", " "))
        kt = tokens(kn)
        if kn == alvo or kn in alvo or alvo in kn:
            return _nivel(v)
        if kt and alvo_tok and len(kt & alvo_tok) / len(kt | alvo_tok) >= 0.5:
            return _nivel(v)
    habilidade = cozinha.habilidade_id(nome)  # a habilidade que ela marcou em Minha cozinha ('molho branco' → molhos)
    return cozinha.estado_habilidade(tecnicas, habilidade) if habilidade else None


def _pendencias_perfil(perfil: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for grupo, chave, pergunta in CHECKLIST:
        valores = perfil.get(grupo) or {}
        if chave == "tempo_por_cozinhada_min" and cozinha.faixa_tempo(valores.get("tempo_por_cozinhada_faixa")):
            continue
        if _equip_status(valores, chave) is None if grupo == "equipamentos" else valores.get(chave) in (None, ""):
            out.append({"grupo": grupo, "chave": chave, "pergunta": pergunta})
    if not perfil.get("tecnicas"):
        out.append({"grupo": "tecnicas", "chave": "tecnicas",
                    "pergunta": "Quais técnicas você domina bem (ex.: massa fresca, molho béchamel, ponto da carne, fritura, assados)?"})
    return out


@_tool
def perfil_cozinha_consultar(args: dict) -> dict:
    store = Store()
    perfil = store.perfil()
    pend = _pendencias_perfil(perfil)
    return {
        "success": True,
        "perfil": perfil,
        "minha_cozinha": cozinha.texto_do_perfil(perfil),
        "pendencias": pend,
        "completo": not pend,
        "perguntas_prontas": cozinha.perguntas_da_cozinha(pend),
        "orientacao": ("O cadastro geral está parcial. Conduza a receita e pergunte só os equipamentos, técnicas e condições relevantes ao preparo. "
                       "O cadastro completo é opcional e não impede pesquisar. Se ela pedir o formulário no chat, use o marcador indicado pelo painel. "
                       "O que ela já enviou no formulário ou marcou em Minha cozinha não se pergunta de novo. "
                       "Salve SÓ o que ela responder, com perfil_cozinha_salvar."
                       if pend else "Cozinha completa para os itens essenciais: não pergunte de novo o que está salvo. Pergunte só técnicas ou "
                                    "equipamentos específicos da receita, e salve a resposta."),
    }


# Palavras com que ela costuma falar de cada restrição (o nome da chave quase nunca é o que ela diz).
_NEEDLES_RESTRICOES: dict[str, tuple[str, ...]] = {
    "espaco_geladeira": ("geladeira", "refrigerador", "freezer"),
    "tempo_por_cozinhada_min": ("hora", "horas", "minuto", "minutos", "tempo", "cozinhada"),
    "gas": ("gas", "botijao", "encanado"),
    "energia": ("energia", "luz", "eletrica", "tomada"),
}


def _termos(grupo: str, chave: str) -> tuple[str, ...]:
    """Como ela diria isso: sinônimos do equipamento ou da restrição; para técnica, o próprio nome."""
    if grupo == "equipamentos" and chave in cozinha.POR_ID:
        return cozinha.termos_de(chave)
    if grupo == "tecnicas" and (tecnica := cozinha.chave_tecnica(chave)) in cozinha.POR_ID:
        return (chave.replace("_", " "), *cozinha.termos_de(tecnica))
    if grupo == "restricoes" and chave in _NEEDLES_RESTRICOES:
        return _NEEDLES_RESTRICOES[chave]
    return (chave.replace("_", " "),)


def _estado_declarado(grupo: str, chave: str, fala: falas.Fala) -> Optional[str]:
    if grupo == "restricoes" and chave in {"gas", "energia"}:
        return falas.disponibilidade_citada(_termos(grupo, chave), fala)
    if grupo == "equipamentos" and norm_text(fala.texto).strip("., ") in {"nao sei", "ainda nao sei"}:
        return None
    grupos = {cozinha.HABILIDADE} if grupo == "tecnicas" else {cozinha.EQUIPAMENTO, cozinha.UTENSILIO}
    itens = cozinha.ids_por_termos(fala.pergunta.split("(", 1)[0], grupos)
    return falas.estado_citado(_termos(grupo, chave), fala, pergunta_unica=len(itens) <= 1,
                               literal=grupo == "equipamentos")


def _valor_declarado(grupo: str, chave: str, valor: Any, falas_dela: list[falas.Fala]) -> tuple[Any, bool]:
    """Canoniza só o campo pedido quando a fala define inequivocamente o estado."""
    fala = falas.ultima_referencia(_termos(grupo, chave), falas_dela, literal=grupo == "equipamentos")
    if fala is None:
        return valor, False
    if grupo in {"equipamentos", "tecnicas"}:
        estado = _estado_declarado(grupo, chave, fala)
        if estado is None or estado == cozinha.nivel(valor):
            return valor, False
        novo = estado if grupo == "tecnicas" else {"sim": True, "nao": False, "parcial": "parcial"}[estado]
        return novo, True
    if grupo == "restricoes" and chave in {"gas", "energia"}:
        estado = _estado_declarado(grupo, chave, fala)
        if estado in {"nao", "parcial"}:
            novo = ({"gas": "sem gás", "energia": "sem energia"}[chave] if estado == "nao" else
                    {"gas": "risco de acabar", "energia": "cai às vezes"}[chave])
            return novo, cozinha.rotina_canonica(chave, valor) != novo
    return valor, False


def _incerteza_declarada(grupo: str, chave: str, falas_dela: list[falas.Fala]) -> bool:
    if grupo not in {"equipamentos", "tecnicas"} and not (grupo == "restricoes" and chave in {"gas", "energia"}):
        return False
    fala = falas.ultima_referencia(_termos(grupo, chave), falas_dela, literal=grupo == "equipamentos")
    if fala is None or _estado_declarado(grupo, chave, fala) is not None:
        return False
    texto = norm_text(fala.texto).strip("., ")
    ambigua = (grupo == "restricoes" and chave in {"gas", "energia"} and texto in {"nao", "nao tenho"}
               and bool(re.search(r"\b(?:gas|botijao|encanado)\b", norm_text(fala.pergunta)))
               and bool(re.search(r"\b(?:energia|luz|eletrica)\b", norm_text(fala.pergunta))))
    return ambigua or texto in {"nao sei", "ainda nao sei", "talvez"} or bool(re.search(
        r"\b(?:talvez|acho|nao sei|nao tenho certeza|preciso conferir|vou conferir|tenho e nao tenho)\b", texto))


def _estados_da_resposta_curta(fala: falas.Fala, tecnicas_da_ficha: set[str],
                              equipamentos_da_ficha: set[str]) -> dict[str, dict[str, Any]]:
    """Salva um sim/não só para o único item realmente perguntado no chat."""
    if re.search(r"[?`\"'“”«»<>]", fala.texto):
        return {}
    texto = norm_text(fala.texto).strip(" .!")
    if texto not in {"sim", "tenho", "tenho sim", "funciona", "funciona sim", "sei", "sei sim", "sei fazer",
                     "nao", "nao tenho", "nao sei", "nao sei fazer", "nunca fiz", "ainda nao",
                     "estou aprendendo", "aprendendo", "mais ou menos"}:
        return {}
    pergunta = falas.pergunta_unica(fala.pergunta)
    if not pergunta or re.search(r"\bnao\b", norm_text(pergunta)):
        return {}
    normal = norm_text(pergunta)
    if normal.startswith(("para este lote voce conta com ", "para este lote voce sabe ")):
        return {}  # O convite agrupado pede a declaração de cada condição.
    if re.search(r"\b(?:sabe|domina)\b", normal):
        grupo, grupos = "tecnicas", {cozinha.HABILIDADE}
    elif re.search(r"\b(?:tem|possui|usa)\b", normal):
        if texto not in {"sim", "tenho", "tenho sim", "funciona", "funciona sim", "nao", "nao tenho", "ainda nao", "mais ou menos"}:
            return {}
        grupo, grupos = "equipamentos", {cozinha.EQUIPAMENTO, cozinha.UTENSILIO}
    else:
        return {}
    if grupo == "tecnicas":
        pedida = re.fullmatch(r"voce sabe (.+) neste preparo", normal)
        alvo = cozinha.chave_tecnica(pedida[1]) if pedida else ""
        chave_da_ficha = alvo if alvo in tecnicas_da_ficha else ""
    else:
        pedida = re.fullmatch(r"voce tem (.+?)(?: funcionando)? (?:para este preparo|para esta receita)", normal)
        alvo = cozinha.chave_equipamento(pedida[1]) if pedida else ""
        chave_da_ficha = alvo if alvo in equipamentos_da_ficha else ""
    itens = {chave_da_ficha} if chave_da_ficha else cozinha.ids_por_termos(pergunta.split("(", 1)[0], grupos)
    if grupo == "equipamentos" and not chave_da_ficha:
        mencao = falas.extrair([], pergunta)
        itens |= {nome for nome in equipamentos_da_ficha if falas.apoia(_termos(grupo, nome), mencao, literal=True)}
    if len(itens) != 1:
        return {}
    chave, = itens
    if chave == "fogao" and re.search(r"\bquantas\b", normal):
        return {}  # Um sim não informa a capacidade do fogão.
    resposta = falas.extrair([{"role": "assistant", "content": pergunta}], fala.texto)[-1]
    estado = (falas.estado_citado(_termos(grupo, chave), resposta) if chave == chave_da_ficha else
              _estado_declarado(grupo, chave, resposta))
    if estado is None:
        return {}
    valor = estado if grupo == "tecnicas" else {"sim": True, "nao": False, "parcial": "parcial"}[estado]
    return {grupo: {chave: valor}}


def atualizar_declaracoes_do_turno(store: Store, fala: falas.Fala, *, tecnicas_da_ficha: tuple[str, ...] = (),
                                    equipamentos_da_ficha: tuple[str, ...] = ()) -> dict[str, dict[str, Any]]:
    """Salva estados literais atuais antes de avaliar/avançar uma ficha.

    Respostas curtas só valem junto da pergunta real de um único item.
    Não preenche outros campos e não depende da LLM chamar perfil_cozinha_salvar.
    Custos e condições não citadas continuam nas tools próprias.
    """
    atuais = falas.declaracoes_literais(fala.texto)
    condicoes_do_lote = falas.condicoes_do_lote(fala.texto)
    tempo_disponivel = falas.tempo_disponivel_declarado(fala)
    faixa_tempo = falas.tempo_disponivel_faixa_declarada(fala)
    bocas = falas.bocas_fogao_declaradas(fala)
    if bocas not in (0, *cozinha.BOCAS):
        bocas = None
    chaves_da_ficha = {cozinha.chave_tecnica(nome) for nome in tecnicas_da_ficha}
    equip_da_ficha = {cozinha.chave_equipamento(nome) for nome in equipamentos_da_ficha}
    estados_curtos = _estados_da_resposta_curta(fala, chaves_da_ficha, equip_da_ficha)
    if not atuais and tempo_disponivel is None and faixa_tempo is None and bocas is None and not estados_curtos:
        return {}
    perfil = store.perfil()
    candidatos = {
        "equipamentos": {i.id for i in cozinha.CATALOGO if i.grupo in {cozinha.EQUIPAMENTO, cozinha.UTENSILIO}}
                        | {cozinha.chave_equipamento(k) for k in perfil.get("equipamentos", {})} | equip_da_ficha,
        "tecnicas": {i.id for i in cozinha.CATALOGO if i.grupo == cozinha.HABILIDADE} | set(perfil.get("tecnicas", {})) | chaves_da_ficha,
        "restricoes": {"gas", "energia"},
    }
    # Responder "sei montar pizza" cobre a técnica da ficha sem afirmar
    # domínio de uma habilidade diferente, como massa fermentada.
    nomes_especificos: dict[str, list[str]] = {}
    catalogo = [item.id for item in cozinha.itens(cozinha.HABILIDADE)]
    for chave in chaves_da_ficha - cozinha.POR_ID.keys():
        nome = chave.replace("_", " ")
        mencao = falas.extrair([], nome)
        for habilidade in catalogo:
            if falas.apoia(_termos("tecnicas", habilidade), mencao):
                nomes_especificos.setdefault(habilidade, []).append(nome)
    mudancas: dict[str, dict[str, Any]] = {}
    declarados: dict[str, dict[str, Any]] = {}
    for grupo, valores in estados_curtos.items():
        for chave, valor in valores.items():
            anterior = perfil.get(grupo, {}).get(chave)
            if grupo == "equipamentos" and valor is True and cozinha.nivel(anterior) == "sim":
                valor = anterior  # Não substitua uma contagem já conhecida por True.
            declarados.setdefault(grupo, {})[chave] = valor
            if valor != anterior:
                mudancas.setdefault(grupo, {})[chave] = valor
    if bocas is not None:
        declarados.setdefault("equipamentos", {})["fogao"] = bocas
        anterior = perfil.get("equipamentos", {}).get("fogao")
        if bocas != anterior or isinstance(anterior, bool):
            mudancas.setdefault("equipamentos", {})["fogao"] = bocas
    if tempo_disponivel is not None:
        declarados.setdefault("restricoes", {})["tempo_por_cozinhada_min"] = tempo_disponivel
        if tempo_disponivel != perfil.get("restricoes", {}).get("tempo_por_cozinhada_min"):
            mudancas.setdefault("restricoes", {})["tempo_por_cozinhada_min"] = tempo_disponivel
        if perfil.get("restricoes", {}).get("tempo_por_cozinhada_faixa") is not None:
            declarados["restricoes"]["tempo_por_cozinhada_faixa"] = None
            mudancas.setdefault("restricoes", {})["tempo_por_cozinhada_faixa"] = None
    elif faixa_tempo is not None:
        declarados["restricoes"] = {"tempo_por_cozinhada_min": None, "tempo_por_cozinhada_faixa": faixa_tempo}
        if any(perfil.get("restricoes", {}).get(k) != v for k, v in declarados["restricoes"].items()):
            mudancas["restricoes"] = dict(declarados["restricoes"])
    for grupo, chaves in candidatos.items():
        for chave in chaves:
            if grupo == "equipamentos" and chave == "fogao" and bocas is not None:
                continue  # A quantidade declarada não vira apenas um equipamento presente.
            citadas = [f for f in atuais if falas.apoia(_termos(grupo, chave), [f], literal=grupo == "equipamentos")]
            # Uma menção ao forno na frase de habilidade não invalida a
            # declaração de posse feita numa frase anterior do mesmo lote.
            if grupo == "equipamentos":
                citadas = [f for f in citadas if re.search(r"\b(?:tenho|possuo|uso|comprei|ganhei|adquiri|meu|minha)\b", norm_text(f.texto))]
            elif grupo == "tecnicas":
                citadas = [f for f in citadas if re.search(r"\b(?:sei|fiz|faco|domino|consigo|aprend\w*|trein\w*)\b", norm_text(f.texto))]
                if nomes := nomes_especificos.get(chave):
                    citadas = [f for f in citadas if not any(falas.estado_citado((nome,), f) is not None for nome in nomes)]
            if not citadas:
                continue
            atual = citadas[-1]
            texto = norm_text(atual.texto)
            if grupo == "restricoes" and chave in condicoes_do_lote:
                if norm_text(condicoes_do_lote[chave][1]) in texto or texto in norm_text(condicoes_do_lote[chave][1]):
                    continue  # Suficiência para um lote não é rotina da cozinha.
            estado = _estado_declarado(grupo, chave, atual)
            anterior = perfil.get(grupo, {}).get(chave)
            if estado is None:
                if not _incerteza_declarada(grupo, chave, [atual]):
                    continue
                novo = None
            elif grupo == "equipamentos":
                novo = {"sim": True, "nao": False, "parcial": "parcial"}[estado]
                if estado == "sim" and cozinha.nivel(anterior) == "sim":
                    novo = anterior  # Preserva contagens já cadastradas e permite retomar uma resposta repetida.
            elif grupo == "tecnicas":
                novo = estado
            elif estado in {"nao", "parcial"}:
                novo, _ = _valor_declarado(grupo, chave, anterior, [atual])
            elif chave == "energia":
                novo = "estável"
            else:
                # A existência de gás não informa o tipo. Preserve o tipo já
                # conhecido; um recurso antes indisponível precisa ser citado.
                tipo = re.search(r"\b(botijao|encanado)\b", texto)
                if not tipo:
                    continue
                novo = cozinha.rotina_canonica(chave, tipo[1])
            declarados.setdefault(grupo, {})[chave] = novo
            if novo != anterior:
                mudancas.setdefault(grupo, {})[chave] = novo
    if mudancas:
        store.perfil_salvar(mudancas)
    # A repetição de uma negativa ainda é evidência atual do impedimento,
    # mesmo quando nenhuma gravação adicional no perfil foi necessária.
    return declarados


def _sem_apoio(grupo: str, chave: str, valor: Any, falas_dela: list[falas.Fala]) -> Optional[str]:
    """Motivo para recusar o item, ou ``None`` quando alguma fala dela o sustenta."""
    nome = chave.replace("_", " ")
    termos = _termos(grupo, chave)
    ultima = falas.ultima_referencia(termos, falas_dela, literal=grupo == "equipamentos")
    if ultima is None:
        return f"ela não falou de {nome}"
    if grupo == "restricoes" and chave == "tempo_por_cozinhada_min" and falas.tempo_disponivel_faixa_declarada(ultima):
        return "ela informou uma faixa de tempo, não um limite exato em minutos; preserve a faixa declarada"
    evidencia = falas_dela
    if grupo in {"equipamentos", "tecnicas"}:
        estado = _estado_declarado(grupo, chave, ultima)
        if estado is None:
            return f"confirme se ela tem/domina {nome}; a resposta mais recente não define um sim, não ou aprendendo para este item"
        if cozinha.nivel(valor) != estado:
            return f"o valor de {nome} contradiz a resposta mais recente ({estado}); não foi salvo"
        evidencia = [ultima]
    elif grupo == "restricoes" and chave in {"gas", "energia"} and _incerteza_declarada(grupo, chave, falas_dela):
        return f"confirme a disponibilidade de {nome}; a resposta mais recente deixou essa condição incerta"
    elif grupo == "restricoes" and chave in {"gas", "energia"} and _estado_declarado(grupo, chave, ultima) is None:
        if not falas.apoia(termos, falas.extrair([{"role": "user", "content": ultima.texto}])):
            return f"confirme {nome} separadamente; a resposta curta não identifica essa condição"
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if not (grupo == "equipamentos" and valor == 0) and not falas.apoia_numero(float(valor), evidencia, minutos=chave.endswith("_min")):
            return f"o número {valor:g} de {nome} não aparece no que ela disse"
    return None


MAX_OBSERVACAO = 160


def _tempo_em_minutos(chave: str, valor: Any, falas_dela: Optional[list[falas.Fala]]) -> tuple[Any, Optional[str]]:
    """O campo é em minutos e o 8B manda o número da fala dela ('2 horas' vira 2, e a conferência de falas aceita o 2). Se ela disse
    horas, converte (2 → 120); se disse minutos, vale; se não dá para saber, devolve o motivo para o modelo corrigir."""
    if chave != "tempo_por_cozinhada_min" or isinstance(valor, bool) or not isinstance(valor, (int, float)) or not 0 < valor < 15:
        return valor, None
    unidade = falas.unidade_do_numero(valor, falas_dela) if falas_dela else None
    if unidade == "h":
        return round(valor * 60), None
    if unidade == "min":
        return valor, None
    return valor, f"{valor:g} parece estar em horas: o campo é em MINUTOS (2 horas = 120, 90 minutos = 90)"


def _conferir_falas() -> bool:
    """Liga a trava de evidência (padrão). ``SABOR_DA_MARIA_CONFERIR_FALAS=0`` desliga, só para depuração."""
    return os.environ.get("SABOR_DA_MARIA_CONFERIR_FALAS", "1").strip().lower() not in {"0", "false", "off", "nao", "não"}


@_tool
def perfil_cozinha_salvar(args: dict, session_id: Optional[str] = None) -> dict:
    """Grava só o que ela disse. Cada item é conferido com as falas dela (mensagens e respostas do clarify);
    o que ela não falou volta em ``recusados`` e NÃO entra no perfil, nem que o modelo ache óbvio."""
    store = Store()
    pedido: dict[str, dict[str, Any]] = {}
    for grupo in ("equipamentos", "tecnicas", "restricoes"):
        valor = args.get(grupo)
        if isinstance(valor, list):
            valor = {str(k): True for k in valor}
        if isinstance(valor, dict):
            pedido[grupo] = {(cozinha.chave_equipamento(str(k)) if grupo == "equipamentos" else norm_text(str(k)).replace(" ", "_")): v
                             for k, v in valor.items()}
    observacoes = args.get("observacoes")
    observacoes = observacoes if isinstance(observacoes, list) else ([observacoes] if observacoes else [])

    verificar = _conferir_falas()
    falas_dela = falas.da_sessao(session_id) if verificar else []
    sem_fala = "ainda não tenho a fala dela nesta conversa; peça para ela contar de novo"
    aceitos: dict[str, Any] = {}
    recusados: list[dict[str, Any]] = []
    correcoes: list[dict[str, Any]] = []
    invalidacoes: dict[str, dict[str, Any]] = {}
    anterior = store.perfil()
    for grupo, itens in pedido.items():
        for chave, valor in list(itens.items()):
            informado = valor
            if verificar and falas_dela:
                valor, corrigido = _valor_declarado(grupo, chave, valor, falas_dela)
                if corrigido:
                    correcoes.append({"grupo": grupo, "chave": chave, "informado": informado, "salvo": valor,
                                      "motivo": "ajustado à resposta explícita mais recente da Dona Maria"})
            valor, motivo = _tempo_em_minutos(chave, valor, falas_dela if verificar else falas.da_sessao(session_id))
            motivo = motivo or (None if not verificar else (sem_fala if falas_dela is None else _sem_apoio(grupo, chave, valor, falas_dela)))
            if motivo:
                recusados.append({"grupo": grupo, "chave": chave, "valor": valor, "motivo": motivo})
                if verificar and falas_dela and _incerteza_declarada(grupo, chave, falas_dela) and anterior.get(grupo, {}).get(chave) is not None:
                    invalidacoes.setdefault(grupo, {})[chave] = None
            else:
                aceitos.setdefault(grupo, {})[chave] = cozinha.rotina_canonica(chave, valor) if grupo == "restricoes" else valor
    for obs in observacoes:
        texto = str(obs).strip()
        if not texto:
            continue
        if len(texto) > MAX_OBSERVACAO:  # o 8B despeja a mensagem inteira aqui em vez de separar nos campos certos
            recusados.append({"grupo": "observacoes", "chave": texto[:60], "motivo": f"observação longa demais (máx. {MAX_OBSERVACAO} caracteres): "
                              "salve cada informação no campo certo (equipamentos, tecnicas, restricoes)"})
            continue
        if verificar and (falas_dela is None or falas.cobertura(texto, falas_dela) < 0.6):
            recusados.append({"grupo": "observacoes", "chave": texto[:60], "motivo": sem_fala if falas_dela is None else "não encontrei isso no que ela disse"})
        else:
            aceitos.setdefault("observacoes", []).append(texto)

    atualizacoes = {grupo: {**aceitos.get(grupo, {}), **invalidacoes.get(grupo, {})}
                   for grupo in ("equipamentos", "tecnicas", "restricoes") if aceitos.get(grupo) or invalidacoes.get(grupo)}
    if aceitos.get("observacoes"):
        atualizacoes["observacoes"] = aceitos["observacoes"]
    tempo_salvo = (atualizacoes.get("restricoes") or {}).get("tempo_por_cozinhada_min")
    if (type(tempo_salvo) in (int, float) and math.isfinite(tempo_salvo) and tempo_salvo > 0
            and (anterior.get("restricoes") or {}).get("tempo_por_cozinhada_faixa") is not None):
        atualizacoes["restricoes"]["tempo_por_cozinhada_faixa"] = None
    perfil = store.perfil_salvar(atualizacoes) if atualizacoes else store.perfil()
    pend = _pendencias_perfil(perfil)
    resposta: dict[str, Any] = {"success": not recusados or bool(aceitos), "perfil": perfil, "pendencias": pend,
                                "completo": not pend, "salvo": aceitos}
    if correcoes:
        resposta["correcoes"] = correcoes
    if invalidacoes:
        resposta["marcados_pendentes"] = invalidacoes
    if recusados:
        so_formato = all("longa demais" in str(r.get("motivo")) or "MINUTOS" in str(r.get("motivo")) for r in recusados)
        resposta.update({
            "bloqueado": not aceitos,
            "recusados": recusados,
            "aviso": ("Os itens em 'recusados' NÃO foram salvos: corrija o que o motivo diz e salve de novo (o que ela falou continua valendo)."
                      if so_formato else
                      "Os itens em 'recusados' NÃO foram salvos: a Dona Maria não disse isso. Não preencha por ela. "
                      "Pergunte (um item de cada vez, com as palavras do item) e salve só o que ela responder."),
        })
    if aceitos:  # o resumo vai pronto: o 8B, sem ele, "confirma" ao usuário técnicas e restrições que ela nunca disse
        faltam = [t for g, t in (("equipamentos", "equipamentos e utensílios"), ("tecnicas", "técnicas que ela domina"), ("restricoes", "rotina da cozinha"))
                  if any(p["grupo"] == g for p in pend)]
        resposta["diga_a_dona_maria"] = ("Salvei na sua cozinha. " + cozinha.texto_do_perfil(perfil, para_ela=True)
                                        + " Se algo estiver errado, pode me corrigir aqui no chat."
                                        + (f" Ainda falta saber: {', '.join(faltam)}." if faltam else " A cozinha está completa."))
        resposta["regra"] = ("Ao contar para ela o que foi salvo, use só o que está em diga_a_dona_maria. Não acrescente técnicas, equipamentos, números "
                             "nem lugares que ela não disse: os exemplos das perguntas não são respostas dela.")
    pend_negocio = negocio_mod.pendencias(store.negocio())
    if pend or pend_negocio:
        if pend:
            resposta["perguntas_prontas"] = cozinha.perguntas_da_cozinha(pend)
        resposta["lembrete"] = ("Antes de perguntar o que falta, releia o que ela acabou de dizer: se ela já falou de técnicas, equipamentos ou rotina, "
                                "salve agora (cada coisa no seu campo; técnicas em 'tecnicas': nome da técnica → 'sim', 'nao' ou 'aprendendo') "
                                "e só pergunte o que sobrar."
                                + (" O negócio também está incompleto (" + ", ".join(p["titulo"].lower() for p in pend_negocio)
                                   + "): se ela já falou disso, salve com negocio_salvar." if pend_negocio else ""))
    return resposta


# ----------------------------------------------------------------------------- negócio: onde a loja fica, tipo de negócio e de alimentação
def _zona_apoiada(zona: str, falas_dela: list[falas.Fala]) -> bool:
    """'zona leste', 'leste da cidade' ou, para a pergunta sobre a zona, só 'leste'. A palavra solta não vale ('Rio Grande do Sul' não é zona sul)."""
    frases = ["centro", "zona central"] if zona == "centro" else [f"zona {zona}", f"{zona} da cidade", f"lado {zona}"]
    if falas.contem_frase(frases, falas_dela):
        return True
    return any(len(f.texto.split()) <= 3 and "zona" in norm_text(f.pergunta) and zona in norm_text(f.texto).split() for f in falas_dela)


def _sem_apoio_negocio(campo: str, valor: Any, falas_dela: list[falas.Fala]) -> Optional[str]:
    """Motivo para recusar o item do negócio, ou ``None`` quando alguma fala dela o sustenta."""
    if campo == "estado":
        nome = negocio_mod.ESTADOS[valor][0]
        return None if (falas.apoia([norm_text(nome)], falas_dela) or falas.cita_sigla(valor, falas_dela)) else f"ela não falou do estado {nome}"
    if campo == "cidade":
        return None if falas.apoia([valor], falas_dela) else f"ela não falou da cidade {valor}"
    if campo == "objetivo":
        return None if any(norm_text(valor) in norm_text(f.texto) for f in falas_dela) else "o objetivo precisa reproduzir o que ela contou, sem inventar metas"
    if campo == "zona":
        return None if _zona_apoiada(valor, falas_dela) else f"ela não falou da {negocio_mod.ZONAS[valor].lower()}"
    if campo == "tipo":
        return None if valor == "outro" or falas.apoia(negocio_mod.TERMOS_TIPO[valor], falas_dela) else f"ela não falou que é {negocio_mod.TIPOS[valor].lower()}"
    comida = negocio_mod.ALIMENTACAO[valor][0].lower()
    return None if falas.apoia(negocio_mod.TERMOS_ALIMENTACAO[valor], falas_dela) else f"ela não falou de {comida}"


@_tool
def negocio_salvar(args: dict, session_id: Optional[str] = None) -> dict:
    """Grava só o que ela disse do negócio. Cada item é conferido com as falas dela (como no perfil da cozinha); o que ela não falou
    volta em ``recusados`` e NÃO entra. Faz merge com o que já está salvo e a tela Meu negócio mostra o mesmo registro."""
    store = Store()
    pedido: dict[str, Any] = {}
    recusados: list[dict[str, Any]] = []
    for campo, resolver, rotulo in (("tipo", negocio_mod.resolver_tipo, "tipo de negócio"), ("zona", negocio_mod.resolver_zona, "zona"),
                                    ("estado", negocio_mod.resolver_estado, "estado")):
        bruto = args.get(campo)
        if bruto in (None, ""):
            continue
        valor = resolver(bruto)
        if valor is None:
            recusados.append({"grupo": campo, "chave": campo, "valor": bruto, "motivo": f"'{bruto}' não é um(a) {rotulo} da lista"})
        else:
            pedido[campo] = valor
    cidade = " ".join(str(args.get("cidade") or "").split())[:60]
    if cidade:
        pedido["cidade"] = cidade
    objetivo = " ".join(str(args.get("objetivo") or "").split())[:240]
    if objetivo:
        pedido["objetivo"] = objetivo
    comidas = args.get("alimentacao")
    comidas = [comidas] if isinstance(comidas, str) else (comidas if isinstance(comidas, list) else [])
    for bruto in comidas:
        valor = negocio_mod.resolver_alimentacao(bruto)
        if valor is None:
            recusados.append({"grupo": "alimentacao", "chave": "alimentacao", "valor": bruto, "motivo": f"'{bruto}' não é um tipo de alimentação da lista"})
        else:
            pedido.setdefault("alimentacao", []).append(valor)

    verificar = _conferir_falas()
    falas_dela = falas.da_sessao(session_id) if verificar else []
    sem_fala = "ainda não tenho a fala dela nesta conversa; peça para ela contar de novo"
    aceitos: dict[str, Any] = {}
    for campo, valor in pedido.items():
        for item in (valor if campo == "alimentacao" else [valor]):
            motivo = None if not verificar else (sem_fala if falas_dela is None else _sem_apoio_negocio(campo, item, falas_dela))
            if motivo:
                recusados.append({"grupo": campo, "chave": campo, "valor": item, "motivo": motivo})
            elif campo == "alimentacao":
                aceitos.setdefault("alimentacao", []).append(item)
            else:
                aceitos[campo] = item

    negocio = store.negocio_atualizar(aceitos) if aceitos else store.negocio()
    pend = negocio_mod.pendencias(negocio)
    resposta: dict[str, Any] = {"success": not recusados or bool(aceitos), "negocio": negocio, "salvo": aceitos, "pendencias": pend, "completo": not pend}
    if recusados:
        resposta.update({
            "bloqueado": not aceitos,
            "recusados": recusados,
            "aviso": ("Os itens em 'recusados' NÃO foram salvos: a Dona Maria não disse isso (ou não é uma opção da lista). Não preencha por ela. "
                      "Pergunte e salve só o que ela responder."),
        })
    if aceitos:  # o resumo vai pronto: o 8B, sem ele, "confirma" ao usuário coisas que ela nunca disse
        resposta["diga_a_dona_maria"] = ("Salvei o seu negócio. " + negocio_mod.resumo_para_ela(negocio) + " Se algo estiver errado, pode me corrigir aqui no chat."
                                        + (f" Ainda falta saber: {', '.join(p['titulo'].lower() for p in pend)}." if pend else " O negócio está completo."))
        resposta["regra"] = ("Ao contar para ela o que foi salvo, use só o que está em diga_a_dona_maria. Não acrescente técnicas, equipamentos, números "
                             "nem lugares que ela não disse: os exemplos das perguntas não são respostas dela.")
        from . import jornada

        proxima = jornada.resumo(store)
        if proxima["passo"] in ("negocio", "estoque", "ajuda"):
            resposta["diga_a_dona_maria"] = ("Salvei o que você contou. " + proxima["pergunta"]["texto"]
                                             if proxima["etapa"] == "negocio" else jornada.resposta_curta(store, proxima))
            montagem.registrar_resposta(session_id, [resposta["diga_a_dona_maria"]], substituir=True, prioridade=40)
    if pend:
        resposta["perguntas_prontas"] = cozinha.perguntas_iniciais(pend, [])
    return resposta


# ----------------------------------------------------------------------------- despensa (2.3)
@_tool
def despensa_listar(args: dict) -> dict:
    if args.get("recarregar") not in (None, False):
        return {"success": False, "bloqueado": True,
                "error": "A consulta da despensa não pode reimportar a planilha nem descartar ajustes de estoque.",
                "proximo_passo": "Consulte novamente sem recarregar. Reinicializar dados é uma operação administrativa separada, com pedido específico da pessoa."}
    store = Store()
    carga = store.ensure_loaded()
    # Vários termos separados por vírgula ou "e" ("frango, batata") valem como "um ou outro": o modelo pequeno manda a lista inteira num campo só.
    filtros = [t for t in (norm_text(x) for x in re.split(r"[,;/]|\be\b", str(args.get("filtro") or ""))) if t]
    itens = []
    total_pago = 0.0
    for item in store.ingredientes():
        total_pago += item.get("preco_total") or 0
        if filtros and not any(f in item["nome_norm"] for f in filtros):
            continue
        entrada = {
            "ingrediente": item["nome"],
            "estoque": item["estoque_legivel"],
            "disponivel": item["disponivel_legivel"],
            "unidade_base": item["unidade_base"],
            "custo_unitario": round(item["custo_unitario_base"], 4) if item.get("custo_unitario_base") is not None else None,
            "custo_unitario_legivel": item["custo_unitario_legivel"],
            "preco_pago": item.get("preco_total"),
            "unidade_na_planilha": item.get("unidade_original"),
            "observacao": item.get("observacao"),
        }
        if item.get("reservado_base"):
            entrada["reservado_para_pratos_aceitos"] = item["reservado_legivel"]
        itens.append(entrada)
    store.registrar_consulta_despensa()
    return {
        "success": True,
        "planilha": carga.get("origem"),
        "total_itens": len(itens),
        "valor_total_pago": round(total_pago, 2),
        "orcamento": store.orcamento(),
        "itens": itens,
        "nota": ("custo_unitario = preço total pago ÷ quantidade comprada, já convertido para kg, L ou un. "
                 "disponivel = estoque − quantidades separadas para os lotes aceitos, inclusive os que aguardam preço."),
    }


@_tool
def despensa_buscar(args: dict) -> dict:
    store = Store()
    nomes = args.get("ingredientes") or ([args["ingrediente"]] if args.get("ingrediente") else [])
    if isinstance(nomes, str):
        nomes = [nomes]
    resultados = []
    for nome in nomes:
        candidatos = store.buscar(str(nome), limit=3)
        resultados.append({
            "procurado": nome,
            "candidatos": candidatos,
            "encontrado": bool(candidatos and candidatos[0]["confiavel"]),
            "nota": None if candidatos else "nada parecido na despensa: provavelmente precisa comprar",
        })
    if resultados:
        store.registrar_consulta_despensa()
    return {"success": True, "resultados": resultados}


@_tool
def despensa_atualizar(args: dict, session_id: Optional[str] = None) -> dict:
    store = Store()
    nome = str(args.get("ingrediente") or "").strip()
    if not nome:
        raise ValueError("informe 'ingrediente'")
    if "compra" in args or args.get("novo"):
        raise ValueError("use despensa_registrar_compras para dar entrada de uma compra e atualizar o saldo")
    conteudo = args.get("conteudo_por_unidade")
    if not isinstance(conteudo, dict):
        raise ValueError("informe o conteúdo de uma unidade da embalagem")
    quantidade = _num(conteudo.get("quantidade"))
    unidade = str(conteudo.get("unidade") or "").strip()
    if quantidade is None or not math.isfinite(quantidade) or quantidade <= 0 or not unidade:
        raise ValueError("informe a medida positiva de uma embalagem em g, kg, ml ou L")
    falas_dela = falas.da_sessao(session_id) or []
    if not any(falas.apoia([nome], [fala]) and compras.medida_citada(fala.texto, quantidade, unidade)
               for fala in falas_dela[-1:]):
        raise ValueError("pergunte qual é o conteúdo de uma embalagem; a medida precisa vir da Dona Maria")
    item = store.atualizar_conteudo(nome, quantidade, unidade)
    return {"success": True, "ingrediente": {k: item.get(k) for k in ("nome", "estoque_legivel", "unidade_base", "custo_unitario_legivel", "custo_unitario_base", "conteudo_un_base", "observacao")},
            "alteracoes": [f"conteúdo de 1 unidade registrado: {item.get('observacao')}"]}


@_tool
def despensa_registrar_compras(args: dict, session_id: Optional[str] = None) -> dict:
    falas_dela = falas.da_sessao(session_id) or []
    receita_id = args.get("receita_id")
    fala = falas_dela[-1] if falas_dela else None
    if not fala or not (compras.pediu_registro(fala.texto)
                        or (receita_id is not None and compras.confirmou_proposta(fala.texto, fala.resposta_a))):
        raise ValueError("peça à Dona Maria para confirmar a entrada das compras realizadas na despensa")
    itens = args.get("compras")
    store = Store()
    if receita_id is not None:
        if itens is not None:
            raise ValueError("informe só receita_id para lançar os complementos conferidos da receita")
        receita = store.obter_receita(int(receita_id))
        if receita is None or receita["dados"].get("sessao_da_conferencia") != session_id:
            raise ValueError("use o receita_id de uma conferência feita nesta conversa")
        itens = compras.da_conferencia(receita["dados"]["resultado"])
        if not itens:
            raise ValueError("a conferência não tem compras complementares para lançar")
        if not all(compras.item_confirmado(fala.resposta_a, item) for item in itens):
            raise ValueError("mostre 'compras_para_confirmar' da conferência e peça confirmação dos itens, quantidades e valores")
        chave = f"chat-receita-compra:{receita['id']}"
    else:
        if not isinstance(itens, list) or not itens:
            raise ValueError("informe o receita_id da proposta exibida ou as compras que ela informou com nome, quantidade, unidade e preço")
        for item in itens:
            if not isinstance(item, dict) or not isinstance(item.get("nome"), str) or not item["nome"].strip():
                raise ValueError("informe o nome de cada ingrediente comprado")
            if set(item) - {"nome", "quantidade", "unidade", "preco_total", "compra_id", "carrinho_id"}:
                raise ValueError("informe apenas os dados da compra; use despensa_atualizar para o conteúdo da embalagem")
            qtd, preco = item.get("quantidade"), item.get("preco_total")
            if (type(qtd) not in (int, float) or not math.isfinite(qtd) or qtd <= 0
                    or type(preco) not in (int, float) or not math.isfinite(preco) or preco < 0
                    or not isinstance(item.get("unidade"), str) or not item["unidade"].strip()):
                raise ValueError("informe quantidade positiva, unidade e preço total de cada compra")
            if not compras.item_confirmado(fala.texto, item):
                raise ValueError(f"confirme com a Dona Maria o nome, a quantidade e o preço da compra de {item['nome']}")
        chave = compras.chave_manual(str(session_id), len(falas_dela), fala.texto, fala.resposta_a)
    try:
        resultado = store.registrar_compras(itens, chave=chave, receita_id=int(receita_id) if receita_id is not None else None)
    except OrcamentoEstourado as exc:
        return _recusa_saldo(exc, store)
    linhas = compras.mostrar(itens)
    acao = "Essas compras já foram registradas" if resultado["ja_registrado"] else "Compras registradas na despensa"
    texto = f"{acao} ({costing.brl(resultado['total'])}). Saldo atual: {costing.brl(resultado['orcamento']['saldo'])}."
    return {"success": True, **resultado, "compras_registradas": linhas, "diga_a_dona_maria": texto}


# ----------------------------------------------------------------------------- resolução de ingredientes
# A pendência diz o problema, em palavras que a Dona Maria pode ler: o mesmo texto aparece na ficha, no cartão do chat
# e no aviso de "não aprovado". O que o modelo deve fazer para resolver vai em ``como_resolver`` e nos próximos passos.
_COMO_COTAR = ("pesquise o preço de mercado (web_search) ou pergunte a ela; depois reconfira este ingrediente com "
               "preco_compra_estimado, quantidade_compra e unidade_compra")
_COMO_MEDIR_EMBALAGEM = "pergunte a ela o peso ou volume de 1 unidade e registre com despensa_atualizar(conteudo_por_unidade)"
# O ingrediente repetido não vira linha da ficha: a orientação ao modelo sai nos próximos passos, a partir desta marca.
_REPETIDO = "aparece em mais de uma linha da receita, e as quantidades precisam ser somadas"


def _orientar(linha: dict[str, Any], como: str) -> None:
    linha["como_resolver"] = "; ".join(filter(None, [linha.get("como_resolver"), como]))


def _numero_legivel(valor: Any) -> str:
    """600.0 vira 600: a ficha é lida por ela, não é saída de programa."""
    return f"{valor:g}" if type(valor) in (int, float) else str(valor)


_CAMPOS_DE_COMPRA = ("preco_compra_estimado", "quantidade_compra", "unidade_compra")
# Ela pediu para mudar a receita neste turno: aí vale o que o modelo enviar, conferido contra a fonte como adaptação.
_PEDIU_MUDANCA = re.compile(r"\b(?:adapt\w*|troq\w*|troc\w*|substitu\w*|alternativ\w*|tir[ea]\w*|retir\w*|acrescent\w*|"
                            r"adicion\w*|dobr\w*|metade|reduz\w*|aument\w*)\b")


def _levar_cotacoes(da_fonte: list[dict[str, Any]], *listas: Any) -> None:
    """A ficha volta a ser a da fonte e o preço de compra já cotado continua nela.

    O modelo pequeno reenvia só o ingrediente que acabou de cotar. Trocar a ficha por essa lista apagava os outros
    ingredientes, e copiar a fonte de novo sem levar a cotação apagava o preço: de um jeito ou de outro a ficha não
    saía do lugar. As listas vêm da mais antiga para a mais nova, e a cotação mais recente de um ingrediente vence.
    """
    for lista in listas:
        for cotado in lista if isinstance(lista, list) else []:
            if not isinstance(cotado, dict) or not all(_compra_info(cotado)):
                continue
            nome = norm_text(str(cotado.get("nome") or ""))
            alvos = [i for i in da_fonte if norm_text(str(i.get("nome") or "")) == nome]
            if not alvos:
                # "frango inteiro" cotado para "frango inteiro, cortado em pedaços": só vale se apontar para uma linha só.
                partes = tokens(nome)
                alvos = [i for i in da_fonte for linha in [tokens(str(i.get("nome") or ""))]
                         if partes and linha and (partes <= linha or linha <= partes)]
            if len(alvos) == 1:
                alvos[0].update({campo: cotado[campo] for campo in _CAMPOS_DE_COMPRA})


def _compra_info(ing: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    return _num(ing.get("preco_compra_estimado")), _num(ing.get("quantidade_compra")), (str(ing["unidade_compra"]) if ing.get("unidade_compra") else None)


def _resolver_ingredientes(store: Store, ingredientes: list[Any]) -> dict[str, Any]:
    linhas: list[dict[str, Any]] = []
    pendencias: list[str] = []
    estimativas: list[str] = []
    compras: list[dict[str, Any]] = []
    ingredientes_contados: set[tuple[str, str]] = set()
    consumo_no_lote: dict[int, float] = {}
    despensa = store.ingredientes()
    cmv_total = custo_despensa_total = custo_compra_total = compras_total = 0.0
    for ing in ingredientes:
        if isinstance(ing, str):
            ing = {"nome": ing}
        if not isinstance(ing, dict):
            pendencias.append(f"ingrediente inválido: {ing!r}")
            continue
        nome = str(ing.get("nome") or "").strip()
        if not nome:
            continue
        qtd = _num(ing.get("quantidade"), 0.0) or 0.0
        unidade = str(ing.get("unidade") or "un")
        linha: dict[str, Any] = {"ingrediente": nome, "quantidade_receita": f"{qtd:g} {unidade}".strip(), "qtd_num": qtd, "unidade_receita": unidade,
                 "opcional": bool(ing.get("opcional"))}
        explicito = "item_despensa" in ing
        alvo = ing.get("item_despensa")
        item = None
        preparo_caseiro = None
        if explicito and alvo:
            item = store.obter(str(alvo)) or store.melhor_item(str(alvo))
            if item is None:
                # Um vínculo antigo pode apontar para um ingrediente que nunca
                # esteve no estoque. Confira pelo nome usado na receita.
                explicito, alvo = False, None
                item = store.melhor_item(nome)
            elif not preparo_caseiro_entre(nome, item["nome"]) and (cortes_incompativeis(nome, item["nome"]) or formas_incompativeis(nome, item["nome"])):
                divergencia = "outro corte" if cortes_incompativeis(nome, item["nome"]) else "outra forma de preparo"
                linha["pendencia"] = f"{nome}: {item['nome']} é {divergencia}, diferente do que a receita pede"
                _orientar(linha, "confirme com ela uma adaptação ou use o ingrediente da receita")
                pendencias.append(linha["pendencia"])
                linhas.append(linha)
                continue
            elif not preparo_caseiro_entre(nome, item["nome"]) and score_ingrediente(nome, item["nome"]) < 0.6:
                linha["pendencia"] = f"{nome}: {item['nome']} não corresponde ao ingrediente da receita"
                _orientar(linha, "confira o item correto da despensa ou proponha uma adaptação explícita")
                pendencias.append(linha["pendencia"])
                linhas.append(linha)
                continue
        else:
            # Se uma compra já foi registrada com o mesmo nome, use o estoque e o preço pagos.
            item = store.obter(nome) if explicito else store.melhor_item(nome)
        if item is not None:
            preparo_caseiro = preparo_caseiro_entre(nome, item["nome"])
        compra_escolhida = explicito and not alvo and all(_compra_info(ing))
        if not (explicito and alvo) and not compra_escolhida:
            candidatos = [(i, preparo_caseiro_entre(nome, i["nome"])) for i in despensa]
            materias = [(i, p) for i, p in candidatos if p]
            if materias:
                prontos = [i for i, p in candidatos if not p and score_ingrediente(nome, i["nome"]) >= 0.6
                           and i.get("disponivel_base", 0) - consumo_no_lote.get(i["id"], 0) > 1e-9]
                if prontos:
                    item = max(prontos, key=lambda i: score_ingrediente(nome, i["nome"]))
                    preparo_caseiro = None
                elif item is None or preparo_caseiro or item.get("disponivel_base", 0) <= 1e-9:
                    item, preparo_caseiro = max(materias, key=lambda par: par[0].get("disponivel_base", 0)
                                                - consumo_no_lote.get(par[0]["id"], 0))
        composto = re.search(r"\bsal\b.*(?:,|/|\be\b).*\bpimenta\b|\bpimenta\b.*(?:,|/|\be\b).*\bsal\b", norm_text(nome))
        if composto and (item is None or norm_text(item["nome"]) != norm_text(nome).removesuffix(" a gosto")):
            linha["pendencia"] = "sal e pimenta são ingredientes distintos e precisam de medidas separadas"
            _orientar(linha, "separe as medidas de uso de cada um, sem contar ambos como estoque de Sal")
            pendencias.append(f"{nome}: {linha['pendencia']}")
            linhas.append(linha)
            continue
        identidade = (norm_text(item["nome"] if item is not None else nome), preparo_caseiro.padrao if preparo_caseiro else "")
        if identidade in ingredientes_contados:
            pendencias.append(f"{nome}: {_REPETIDO}")
            continue
        ingredientes_contados.add(identidade)
        agua_de_preparo = re.fullmatch(r"agua(?: (?:potavel|filtrada|morna|quente|fria|gelada|fervente))*", norm_text(nome))
        if not agua_de_preparo and (item is None or item.get("disponivel_base", 0) <= 1e-9):
            linha["estoque_insuficiente"] = True
            linha["motivo_falta"] = "não cadastrado na despensa" if item is None else "sem estoque disponível"
            linha["item_despensa"] = item["nome"] if item else None
            linha["sem_preco_falta"] = item is None and not all(_compra_info(ing))
        if not math.isfinite(qtd) or qtd <= 0:
            linha["pendencia"] = "a receita não traz a quantidade"
            _orientar(linha, "proponha uma medida em gramas ou mililitros para o lote, mesmo quando a receita diz 'a gosto'; informe a base da estimativa")
            pendencias.append(f"{nome}: {linha['pendencia']}")
            linhas.append(linha)
            continue
        memoria: list[str] = []
        if item is None and agua_de_preparo and not all(_compra_info(ing)):
            conv = to_base(qtd, unidade, nome, "L")
            linha.update({"item_despensa": None, "recurso_de_preparo": "água potável", "conversao": conv.note,
                          "estimativa": conv.estimate, "compra": None, "custo_uso_despensa": 0.0,
                          "custo_uso_compra": 0.0, "custo_uso_total": 0.0,
                          "memoria_de_calculo": "Água potável de preparo: utilidade da cozinha, sem compra de água engarrafada nem novo débito no orçamento de ingredientes."})
            if conv.qty_base is None:
                linha["pendencia"] = conv.note
                pendencias.append(f"{nome}: {conv.note}")
            else:
                linha.update({"necessario_base": conv.qty_base, "unidade_base": "L", "falta": "nada"})
            linhas.append(linha)
            continue
        if item is not None:
            base = item["unidade_base"]
            if preparo_caseiro:
                conv = materia_prima_do_preparo(qtd, unidade, nome, item["nome"], base, preparo_caseiro.rendimento,
                                               conteudo_un_base=item.get("conteudo_un_base"))
                linha["preparo_caseiro"] = {
                    "ingrediente": nome, "materia_prima": item["nome"], "rendimento": preparo_caseiro.rendimento,
                    "tempo_estimado_min": preparo_caseiro.minutos, "equipamentos": list(preparo_caseiro.equipamentos),
                    "tecnicas": list(preparo_caseiro.tecnicas), "modo_preparo": preparo_caseiro.preparo,
                    "origem": "estimativa_local", "grupo": f"{item['id']}:{preparo_caseiro.padrao}",
                }
            else:
                conv = to_base(qtd, unidade, item["nome"], base, conteudo_un_base=item.get("conteudo_un_base"))
            linha.update({"item_despensa": item["nome"], "conversao": conv.note, "estimativa": conv.estimate})
            if conv.estimate:
                estimativas.append(f"{nome}: {conv.note}")
            if conv.qty_base is None:
                linha["pendencia"] = conv.note
                if "peso/volume" in conv.note:
                    _orientar(linha, _COMO_MEDIR_EMBALAGEM)
                pendencias.append(f"{nome}: {conv.note}")
                linhas.append(linha)
                continue
            necessario = conv.qty_base
            estoque = float(item.get("estoque_base") or 0)
            reservado = float(item.get("reservado_base") or 0)
            tem = max(0.0, estoque - reservado - consumo_no_lote.get(item["id"], 0.0))
            usa = min(necessario, tem)
            consumo_no_lote[item["id"]] = consumo_no_lote.get(item["id"], 0.0) + usa
            falta = max(0.0, necessario - tem)
            if falta > 1e-9:
                linha.update(estoque_insuficiente=True, motivo_falta="estoque disponível insuficiente",
                             quantidade_faltante_base=round(falta, 6))
            custo_unit = item.get("custo_unitario_base")
            linha.update({
                "item_id": item["id"],
                "necessario": format_base(necessario, base), "necessario_base": round(necessario, 4), "unidade_base": base,
                "tem_na_despensa": format_base(tem, base), "usa_da_despensa": format_base(usa, base), "usa_da_despensa_base": round(usa, 6),
                "falta": format_base(falta, base) if falta > 1e-9 else "nada",
            })
            if reservado > 1e-9:
                linha["estoque_total"] = format_base(estoque, base)
                linha["reservado_por_outros_pratos"] = format_base(reservado, base)
            custo_despensa = (usa * custo_unit) if custo_unit is not None else 0.0
            if custo_unit is None:
                pendencias.append(f"{nome}: item sem preço na planilha")
            elif usa > 0:
                memoria.append(f"{format_base(usa, base)} × {costing.brl(custo_unit)}/{base} = {costing.brl(custo_despensa)}")
            custo_compra_uso = 0.0
            compra = None
            if falta > 1e-9:
                # Uma cotação do alimento pronto não é o preço da matéria-prima.
                preco_c, qtd_c, un_c = (None, None, None) if preparo_caseiro else _compra_info(ing)
                if preco_c and qtd_c and un_c:
                    try:
                        spec = parse_pantry_unit(un_c)
                    except ValueError:
                        linha["pendencia"] = f"a compra foi informada em {un_c!r}, que não diz o tamanho da embalagem"
                        _orientar(linha, "informe unidade_compra como kg, g, L, ml ou un com o conteúdo da embalagem")
                        pendencias.append(f"{nome}: {linha['pendencia']}")
                        linhas.append(linha)
                        continue
                    if spec.base != base:
                        pendencias.append(f"{nome}: compra informada em {spec.base}, mas o item é controlado em {base}")
                    else:
                        cu = preco_c / (qtd_c * spec.factor)
                        custo_compra_uso = falta * cu
                        compra = {"ingrediente": item["nome"], "quantidade": qtd_c, "unidade": un_c, "preco_estimado": round(preco_c, 2),
                                  "cobre_a_falta": qtd_c * spec.factor >= falta - 1e-9}
                        if not compra["cobre_a_falta"]:
                            pendencias.append(f"{nome}: a compra cotada não cobre a falta de {format_base(falta, base)}")
                        memoria.append(f"falta {format_base(falta, base)} × {costing.brl(cu)}/{base} (compra) = {costing.brl(custo_compra_uso)}")
                elif custo_unit is not None:
                    custo_compra_uso = falta * custo_unit
                    compra = {"ingrediente": item["nome"], "quantidade": round(falta, 4), "unidade": base, "preco_estimado": round(custo_compra_uso, 2),
                              "estimativa": "mesmo preço unitário da planilha; confirme preço e tamanho da embalagem"}
                    estimativas.append(f"{nome}: falta {format_base(falta, base)}, estimado ao preço da planilha ({costing.brl(custo_unit)}/{base})")
                    memoria.append(f"falta {format_base(falta, base)} × {costing.brl(custo_unit)}/{base} (estimado) = {costing.brl(custo_compra_uso)}")
                else:
                    pendencias.append(f"{nome}: falta {format_base(falta, base)} e ainda não há preço de compra")
                    linha["falta_preco"] = True
                    _orientar(linha, _COMO_COTAR)
            custo_item = custo_despensa + custo_compra_uso
            linha.update({"custo_uso_despensa": round(custo_despensa, 2), "custo_uso_compra": round(custo_compra_uso, 2),
                          "custo_uso_total": round(custo_item, 2), "compra": compra, "memoria_de_calculo": "; ".join(memoria)})
            cmv_total += custo_item
            custo_despensa_total += custo_despensa
            custo_compra_total += custo_compra_uso
            if compra:
                compras.append(compra)
                compras_total += compra["preco_estimado"]
        else:
            linha.update({"item_despensa": None, "tem_na_despensa": "não"})
            preco_c, qtd_c, un_c = _compra_info(ing)
            if preco_c and qtd_c and un_c:
                try:
                    spec = parse_pantry_unit(un_c)
                except ValueError:
                    linha["pendencia"] = f"a compra foi informada em {un_c!r}, que não diz o tamanho da embalagem"
                    _orientar(linha, "informe unidade_compra como kg, g, L, ml ou un com o conteúdo da embalagem")
                    pendencias.append(f"{nome}: {linha['pendencia']}")
                    linhas.append(linha)
                    continue
                conv = to_base(qtd, unidade, nome, spec.base)
                linha.update({"conversao": conv.note, "estimativa": conv.estimate})
                if conv.estimate:
                    estimativas.append(f"{nome}: {conv.note}")
                if conv.qty_base is None:
                    linha["pendencia"] = conv.note
                    if "peso/volume" in conv.note:
                        _orientar(linha, _COMO_MEDIR_EMBALAGEM)
                    pendencias.append(f"{nome}: {conv.note}")
                else:
                    cu = preco_c / (qtd_c * spec.factor)
                    custo = conv.qty_base * cu
                    compra = {"ingrediente": nome, "quantidade": qtd_c, "unidade": un_c, "preco_estimado": round(preco_c, 2),
                              "cobre_a_falta": qtd_c * spec.factor >= conv.qty_base - 1e-9}
                    if not compra["cobre_a_falta"]:
                        pendencias.append(f"{nome}: a compra cotada não cobre os {format_base(conv.qty_base, spec.base)} necessários")
                    memoria.append(f"{format_base(conv.qty_base, spec.base)} × {costing.brl(cu)}/{spec.base} (compra de {qtd_c:g} {un_c} por {costing.brl(preco_c)}) = {costing.brl(custo)}")
                    linha["quantidade_faltante_base"] = round(conv.qty_base, 6)
                    linha.update({"necessario": format_base(conv.qty_base, spec.base), "necessario_base": round(conv.qty_base, 4), "unidade_base": spec.base,
                                  "falta": format_base(conv.qty_base, spec.base), "custo_uso_despensa": 0.0, "custo_uso_compra": round(custo, 2),
                                  "custo_uso_total": round(custo, 2), "compra": compra, "memoria_de_calculo": "; ".join(memoria)})
                    cmv_total += custo
                    custo_compra_total += custo
                    compras.append(compra)
                    compras_total += preco_c
            else:
                linha["pendencia"] = "não está na despensa e ainda não tem preço de compra"
                linha["falta_preco"] = True
                _orientar(linha, _COMO_COTAR)
                pendencias.append(f"{nome}: {linha['pendencia']}")
        linhas.append(linha)
    agrupadas: list[dict[str, Any]] = []
    for compra in compras:
        anterior = next((c for c in agrupadas if c["ingrediente"] == compra["ingrediente"]
                         and c["unidade"] == compra["unidade"] and c.get("estimativa") and compra.get("estimativa")), None)
        if anterior:
            anterior["quantidade"] = round(anterior["quantidade"] + compra["quantidade"], 4)
            anterior["preco_estimado"] = round(anterior["preco_estimado"] + compra["preco_estimado"], 2)
        else:
            agrupadas.append(dict(compra))
    return {
        "itens": linhas,
        "cmv_total": round(cmv_total, 2),
        "custo_vindo_da_despensa": round(custo_despensa_total, 2),
        "custo_vindo_de_compras": round(custo_compra_total, 2),
        "compras": agrupadas,
        "compras_total_estimado": round(compras_total, 2),
        "pendencias": pendencias,
        "estimativas": estimativas,
        "preparos_caseiros": [l["preparo_caseiro"] for l in linhas if l.get("preparo_caseiro")],
        "faltantes": [{"ingrediente": l["ingrediente"], "item_despensa": l.get("item_despensa"),
                       "quantidade_receita": l["quantidade_receita"] if l["qtd_num"] > 0 else "medida a confirmar",
                       "quantidade_faltante_base": l.get("quantidade_faltante_base"),
                       "unidade_base": l.get("unidade_base"), "falta": l.get("falta"),
                       "sem_preco": bool(l.get("falta_preco") or l.get("sem_preco_falta")), "motivo": l["motivo_falta"]}
                      for l in linhas if l.get("estoque_insuficiente")],
    }


def _com_preparos_caseiros(requisitos: dict[str, Any], resultado: dict[str, Any]) -> dict[str, Any]:
    """Acrescenta o trabalho local sem alterar tempo, ingredientes ou preparo publicados."""
    preparos = {p["grupo"]: p for p in resultado.get("preparos_caseiros") or []}
    if not preparos:
        return requisitos
    req = dict(requisitos)
    for campo, chave in (("equipamentos_necessarios", "equipamentos"), ("tecnicas_necessarias", "tecnicas")):
        req[campo] = list(dict.fromkeys(list(req.get(campo) or []) + [v for p in preparos.values() for v in p[chave]]))
    extra = sum(p["tempo_estimado_min"] for p in preparos.values())
    req["tempo_receita_min"] = req.get("tempo_preparo_min")
    req["tempo_preparos_caseiros_min"] = extra
    if req.get("tempo_preparo_min") is not None:
        req["tempo_preparo_min"] += extra
    return req


def _texto_preparos_caseiros(resultado: dict[str, Any]) -> str:
    linhas = [f"{l['ingrediente']}: {l['conversao']} {l['preparo_caseiro']['modo_preparo']}"
              for l in resultado.get("itens") or [] if l.get("preparo_caseiro")]
    return ("Preparos caseiros locais, não publicados na fonte: " + " ".join(linhas)) if linhas else ""


def _orcamento_resumo(store: Store, compras_total: float) -> dict[str, Any]:
    orc = store.orcamento()
    # Em operações deste prato, o Store já exclui suas próprias linhas.
    # Os outros pratos e os itens avulsos continuam disputando o mesmo saldo.
    restante = orc["saldo"] if store.ignorar_prato_id is not None else orc["restante"]
    return {
        "inicial": orc["inicial"], "ja_comprometido": orc["comprometido"], "restante": restante,
        "gasto_confirmado": orc["gasto_confirmado"], "reservado_cardapio": orc["reservado_cardapio"],
        "no_carrinho": orc["carrinho"], "saldo_atual": orc["saldo"],
        "compras_desta_receita": round(compras_total, 2), "cabe_no_orcamento": compras_total <= restante + 1e-9,
        "sobra_apos_compras": round(restante - compras_total, 2),
    }


def _checar_equipamentos(perfil: dict[str, Any], necessarios: list[str]) -> dict[str, list[str]]:
    equip = perfil.get("equipamentos") or {}
    ok, faltando, nao_informado = [], [], []
    for nome in necessarios:
        alternativas = re.split(r"\s+ou\s+", str(nome), flags=re.IGNORECASE)
        estados = [(item, _equip_status(equip, item)) for item in alternativas]
        disponivel = next((item for item, status in estados if status == "sim"), None)
        if disponivel is not None:
            # A operação recebe o aparelho efetivamente disponível para conferir
            # gás/energia. "Forno ou air fryer" não exige os dois equipamentos.
            ok.append(disponivel)
        elif all(status == "nao" for _, status in estados):
            faltando.append(str(nome))
        else:
            nao_informado.append(str(nome))
    return {"ok": ok, "faltando": faltando, "nao_informado": nao_informado}


def _checar_tecnicas(perfil: dict[str, Any], necessarias: list[str]) -> dict[str, list[str]]:
    tec = perfil.get("tecnicas") or {}
    ok, faltando, parcial, nao_informado = [], [], [], []
    for nome in necessarias:
        status = _tecnica_status(tec, str(nome))
        {"sim": ok, "nao": faltando, "parcial": parcial}.get(status, nao_informado).append(str(nome))
    return {"ok": ok, "faltando": faltando, "parcial": parcial, "nao_informado": nao_informado}


def _assinatura_operacional(perfil: dict[str, Any], ingredientes: list[Any], nome: str,
                           porcoes: Any, requisitos: dict[str, Any]) -> str:
    conteudo = {"perfil": perfil, "ingredientes": ingredientes, "nome": nome,
                "porcoes": _num(porcoes), "requisitos": {k: v for k, v in requisitos.items()
                                                       if k not in {"tempo_minimo_de_forno", "metodo_adaptado"}}}
    return hashlib.sha256(json.dumps(conteudo, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


_CONFIRMACOES_DO_LOTE = {"gas": "gas_a_confirmar", "energia": "energia_a_confirmar",
                        "espaco_geladeira": "refrigeracao_a_confirmar"}


def _assinaturas_operacionais(perfil: dict[str, Any], ingredientes: list[Any], nome: str, porcoes: Any,
                              requisitos: dict[str, Any], preparo: Any) -> dict[str, str]:
    # Cada resposta pertence à versão e à respectiva condição da cozinha.
    # Conferir outro equipamento ou cotar um ingrediente não invalida a evidência.
    restricoes = perfil.get("restricoes") or {}
    medidas = _medidas_da_versao(ingredientes)
    versao = {**requisitos, "preparo": norm_text(str(preparo or ""))}
    return {
        **{chave: _assinatura_operacional({chave: restricoes.get(chave)}, medidas, nome, porcoes, versao)
           for chave in _CONFIRMACOES_DO_LOTE},
        "legada": _assinatura_operacional(perfil, ingredientes, nome, porcoes, requisitos),
    }


def _confirmacoes_vigentes(dados: dict[str, Any], assinaturas: dict[str, str]) -> dict[str, dict[str, str]]:
    return {c["chave"]: c for c in dados.get("confirmacoes_operacionais") or []
            if isinstance(c, dict) and c.get("chave") in _CONFIRMACOES_DO_LOTE
            and c.get("assinatura") in (assinaturas[c["chave"]], assinaturas["legada"])}


def _assinatura_falas(falas_dela: list[falas.Fala]) -> str:
    return hashlib.sha256(json.dumps([(f.texto, f.resposta_a) for f in falas_dela], ensure_ascii=False).encode()).hexdigest()


def _confirmar_operacao(pedido: Any, assinaturas: dict[str, str], session_id: Optional[str]) -> list[dict[str, str]]:
    """Evidência específica e recente, válida só para esta versão e perfil da cozinha."""
    if pedido is None:
        return []
    if not isinstance(pedido, dict):
        raise ValueError("confirmacoes_operacionais deve conter a fala literal para gas, energia ou espaco_geladeira")
    ultimas = falas.da_sessao(session_id) or []
    declaradas = falas.condicoes_do_lote(ultimas[-1].texto) if ultimas else {}
    confirmacoes = []
    for chave, evidencia in pedido.items():
        estado, original = declaradas.get(chave, ("", ""))
        trecho = norm_text(str(evidencia or "")).strip(". ")
        if estado != "sim" or len(trecho) < 12 or trecho not in norm_text(original):
            raise ValueError(f"{chave}: falta uma confirmação específica da Dona Maria sobre esta condição para o lote; um aceite genérico do prato não basta")
        confirmacoes.append({"chave": chave, "evidencia": original, "assinatura": assinaturas[chave]})
    return confirmacoes


def _receber_confirmacoes_operacionais(pedido: Any, assinaturas: dict[str, str],
                                      session_id: Optional[str]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Argumentos sem evidência não liberam condições nem descartam a ficha.

    A validação estrita continua por condição. Assim, uma confirmação legítima
    de gás pode ser preservada sem aceitar energia ou espaço inventados pelo
    modelo na mesma chamada.
    """
    if pedido is None:
        return [], []
    if not isinstance(pedido, dict):
        return [], [{"chave": "confirmacoes_operacionais", "motivo": "As condições deste lote ainda precisam ser confirmadas por você."}]
    perguntas = {
        "gas": "Ainda preciso confirmar se há gás suficiente para este lote.",
        "energia": "Ainda preciso confirmar se há energia suficiente para este preparo.",
        "espaco_geladeira": "Ainda preciso confirmar se este lote e seus ingredientes cabem na geladeira.",
    }
    aceitas: list[dict[str, str]] = []
    recusadas: list[dict[str, str]] = []
    for chave, evidencia in pedido.items():
        try:
            aceitas.extend(_confirmar_operacao({chave: evidencia}, assinaturas, session_id))
        except ValueError:
            recusadas.append({"chave": chave, "motivo": perguntas.get(chave, "Essa condição não foi confirmada por você.")})
    return aceitas, recusadas


def _restricoes_relevantes(perfil: dict[str, Any], tempo_preparo_min: Optional[float],
                          equip: dict[str, list[str]], ingredientes: list[Any],
                          confirmadas: Optional[set[str]] = None, *,
                          indisponiveis_no_lote: Optional[set[str]] = None,
                          tempo_minimo_de_forno: Optional[float] = None) -> dict[str, list[dict[str, Any]]]:
    """Condições da operação entram na mesma trava que os ingredientes e a cozinha.

    Instabilidade só importa para os aparelhos usados por esta ficha; fogão/forno sem
    fonte informada pedem confirmação quando o gás falta, sem presumir que são elétricos.
    Uma resposta genérica de aceite do prato não confirma nenhuma destas condições.
    """
    impedimentos: list[dict[str, Any]] = []
    confirmacoes: list[dict[str, Any]] = []
    restr = perfil.get("restricoes") or {}
    limite = _num(restr.get("tempo_por_cozinhada_min"))
    faixa = cozinha.faixa_tempo(restr.get("tempo_por_cozinhada_faixa"))
    exato = limite is not None and math.isfinite(limite) and limite > 0
    if not exato and not faixa:
        confirmacoes.append(_bloqueio("tempo_disponivel_pendente", "Quanto tempo você consegue dedicar a esta cozinhada?"))
    limite_estrito = faixa["minutos"] if not exato and faixa and faixa["operador"] == "menor_que" else None
    if tempo_minimo_de_forno and ((exato and tempo_minimo_de_forno > limite)
                                  or (limite_estrito is not None and tempo_minimo_de_forno >= limite_estrito)):
        disponibilidade = f"{limite:g} min" if exato else f"menos de {limite_estrito:g} min"
        # Este aviso é lido por ela (cartão do chat, painel lateral e resposta pronta): diz o problema, falando com ela.
        # O que fazer com ele o modelo recebe nos próximos passos e no painel do turno.
        impedimentos.append(_bloqueio("tempo_insuficiente",
            f"A receita precisa de pelo menos {tempo_minimo_de_forno:g} min de forno e você disse ter {disponibilidade} por cozinhada."))
    elif not tempo_preparo_min or not math.isfinite(tempo_preparo_min) or tempo_preparo_min < (tempo_minimo_de_forno or 0):
        detalhe = (f"O preparo exige pelo menos {tempo_minimo_de_forno:g} min de forno. Falta estimar o tempo total desta versão."
                   if tempo_minimo_de_forno else
                   "Falta o tempo total de preparo desta versão.")
        confirmacoes.append(_bloqueio("tempo_da_receita_pendente", detalhe))
    elif exato and tempo_preparo_min > limite:
        impedimentos.append(_bloqueio("tempo_insuficiente", f"A receita leva cerca de {tempo_preparo_min:g} min e você disse ter {limite:g} min por cozinhada."))
    elif not exato and faixa:
        marco = faixa["minutos"]
        if faixa["operador"] == "menor_que" and tempo_preparo_min >= marco:
            impedimentos.append(_bloqueio("tempo_insuficiente", f"A receita leva cerca de {tempo_preparo_min:g} min e você disse ter menos de {marco:g} min por cozinhada."))
        elif faixa["operador"] != "maior_que" or tempo_preparo_min > marco:
            confirmacoes.append(_bloqueio("tempo_disponivel_exato_pendente",
                f"Esta receita leva cerca de {tempo_preparo_min:g} min. A faixa informada ainda não confirma se cabe. "
                "Quantos minutos você consegue dedicar a esta cozinhada?"))

    usados = {str(n) for valores in equip.values() for n in valores}
    ids = {cozinha.chave_equipamento(n) for n in usados}
    if "fogao" in ids:
        equipamentos = perfil.get("equipamentos") or {}
        bocas = [v for chave, v in equipamentos.items() if cozinha.chave_equipamento(chave) == "fogao"]
        if not any(type(v) in (int, float) and v > 0 or re.search(r"\b[1-9]\d*\s*bocas?\b", str(v)) for v in bocas):
            confirmacoes.append(_bloqueio("fogao_capacidade_pendente", "Para organizar este preparo, quantas bocas do seu fogão funcionam?"))
    eletricos = ids & {"air_fryer", "micro_ondas", "liquidificador", "batedeira", "processador", "geladeira", "freezer"}
    termicos = ids & {"fogao", "forno", "chapa"}
    # A fonte pode ter sido informada no nome necessário ou na chave/valor salvo.
    descricao = " ".join(usados) + " " + " ".join(
        f"{chave} {valor}" for chave, valor in (perfil.get("equipamentos") or {}).items()
        if cozinha.chave_equipamento(chave) in termicos)
    eletrico_explicito = "eletric" in norm_text(descricao)
    gas_explicito = bool(re.search(r"\ba gas\b", norm_text(descricao)))
    if termicos and eletrico_explicito:
        eletricos |= termicos
    usa_gas = bool(termicos and (gas_explicito or not eletrico_explicito))
    nomes = " ".join(str(i.get("nome", "")) if isinstance(i, dict) else str(i) for i in ingredientes)
    pereciveis = bool(re.search(r"\b(frango|carne|peixe|salmao|atum|camarao|tilapia|leite|queijo|mussarela|manteiga|requeijao|creme)\b", norm_text(nomes)))
    precisa_frio = bool(ids & {"geladeira", "freezer"}) or pereciveis
    if precisa_frio:
        eletricos.add("refrigeração dos ingredientes")

    def disponibilidade(chave: str) -> str:
        valor = restr.get(chave)
        if valor is False:
            return "nao"
        texto = norm_text(str(valor or ""))
        if not texto:
            return "pendente"
        if texto in {"nao", "nenhum", "nenhuma", "sem", "indisponivel", "0"} or any(
                termo in texto for termo in ("sem gas", "sem energia", "sem luz", "nao tenho", "acabou", "nao funciona", "insuficiente")):
            return "nao"
        if re.search(r"\bnao\b", texto) or any(termo in texto for termo in ("as vezes", "cai", "instavel", "interrup", "racion", "falta", "risco", "oscila", "acabando")):
            return "parcial"
        if valor is True or texto in {"sim", "true", "ok"} or any(
                termo in texto for termo in ("estavel", "suficiente", "garantid", "normal", "botijao", "encanado")):
            return "sim"
        return "pendente"

    if eletricos:
        energia = disponibilidade("energia")
        itens = [a.replace("_", " ") for a in sorted(eletricos)]
        aparelhos = itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]
        if "energia" in (indisponiveis_no_lote or ()):
            impedimentos.append(_bloqueio("energia_indisponivel", f"A receita usa {aparelhos}, e você informou que não consegue garantir energia para este lote."))
        elif energia == "nao":
            impedimentos.append(_bloqueio("energia_indisponivel", f"A receita usa {aparelhos}, e você disse que não tem energia disponível."))
        elif energia in {"parcial", "pendente"}:
            confirmacoes.append(_bloqueio("energia_a_confirmar", f"A receita usa {aparelhos}, e a energia não está garantida para este preparo."))
    if usa_gas:
        gas = disponibilidade("gas")
        if "gas" in (indisponiveis_no_lote or ()):
            impedimentos.append(_bloqueio("gas_indisponivel", "Você informou que não consegue garantir gás para o fogão ou o forno deste lote."))
        elif gas == "nao" and gas_explicito:
            impedimentos.append(_bloqueio("gas_indisponivel", "A receita usa equipamento a gás e você disse que o gás não está disponível."))
        elif gas in {"nao", "parcial", "pendente"}:
            confirmacoes.append(_bloqueio("gas_a_confirmar", "O gás não está garantido para o fogão ou o forno desta receita."))

    espaco = norm_text(str(restr.get("espaco_geladeira") or ""))
    if precisa_frio:
        if "espaco_geladeira" in (indisponiveis_no_lote or ()):
            impedimentos.append(_bloqueio("refrigeracao_insuficiente", "Você informou que os ingredientes ou as porções deste lote não cabem na geladeira."))
        elif not espaco:
            confirmacoes.append(_bloqueio("refrigeracao_a_confirmar", "Há espaço na geladeira para os ingredientes e as porções deste lote?"))
        elif restr.get("espaco_geladeira") is False or espaco in {"nao", "nenhum", "nenhuma", "zero", "0", "sem espaco", "lotada", "nenhum espaco"}:
            impedimentos.append(_bloqueio("refrigeracao_insuficiente", "A receita precisa refrigerar ingredientes ou o lote pronto, e você disse que não há espaço na geladeira."))
        elif espaco in {"pouco", "pequeno", "pequena", "limitado", "limitada", "apertado", "apertada"}:
            confirmacoes.append(_bloqueio("refrigeracao_a_confirmar", "Você disse que há pouco espaço na geladeira, e este lote precisa de refrigeração."))
    liberadas = {_CONFIRMACOES_DO_LOTE[chave] for chave in (confirmadas or set()) if chave in _CONFIRMACOES_DO_LOTE}
    return {"impedimentos": impedimentos,
            "confirmacoes": [c for c in confirmacoes if c["tipo"] not in liberadas]}


# ----------------------------------------------------------------------------- veredito e travas
def _bloqueio(tipo: str, detalhe: str, **extra: Any) -> dict[str, Any]:
    return {"tipo": tipo, "detalhe": detalhe, **extra}


def _avaliar(res: dict[str, Any], equip: dict[str, list[str]], tec: dict[str, list[str]], orc: dict[str, Any],
             pend_perfil: list[dict[str, str]], operacao: Optional[dict[str, list[dict[str, Any]]]] = None) -> dict[str, Any]:
    """Veredito único, usado pela conferência (2.3), pelo CMV e pelo registro no cardápio (2.4).

    * ``inviavel_por_enquanto``: impedimento conhecido (equipamento ou técnica que ela disse não ter,
      compras que não cabem no orçamento).
    * ``precisa_confirmar``: falta resposta relevante (equipamento/técnica não informados,
      pendência de ingrediente).
    * ``viavel``: nada pendente. Só este veredito libera ``prato_calcular_cmv`` e ``cardapio_registrar``.
    """
    operacao = operacao or {}
    impedimentos: list[dict[str, Any]] = list(operacao.get("impedimentos") or [])
    for e in equip["faltando"]:
        impedimentos.append(_bloqueio("equipamento_faltando", f"{e}: você disse que não tem"))
    for t in tec["faltando"]:
        impedimentos.append(_bloqueio("tecnica_faltando", f"{t}: você disse que não domina"))
    if not orc["cabe_no_orcamento"]:
        impedimentos.append(_bloqueio("orcamento", f"as compras deste prato ({costing.brl(orc['compras_desta_receita'])}) "
                                                   f"não cabem nos {costing.brl(orc['restante'])} restantes do orçamento"))
    confirmacoes: list[dict[str, Any]] = list(operacao.get("confirmacoes") or [])
    ingredientes_pendentes = [_bloqueio("ingrediente_pendente", p) for p in res["pendencias"]]
    # Defina as medidas antes de confirmar fornecimento e espaço para o lote.
    # Assim uma medida nova não reabre gás/energia/geladeira a cada ingrediente.
    posicao = next((idx for idx, c in enumerate(confirmacoes) if c["tipo"] in {
        "energia_a_confirmar", "gas_a_confirmar", "refrigeracao_a_confirmar"}), len(confirmacoes))
    confirmacoes[posicao:posicao] = ingredientes_pendentes
    for e in equip["nao_informado"]:
        confirmacoes.append(_bloqueio("equipamento_nao_informado", f"{e}: você ainda não disse se tem"))
    for t in tec["nao_informado"]:
        confirmacoes.append(_bloqueio("tecnica_nao_informada", f"{t}: você ainda não disse se domina"))
    for t in tec["parcial"]:
        confirmacoes.append(_bloqueio("tecnica_parcial", f"{t}: você disse que domina só em parte"))
    # O checklist geral informa o cadastro, mas não é uma trava da receita.
    # Equipamentos/técnicas da ficha e operação acima são as pendências reais.
    if impedimentos:
        veredito = "inviavel_por_enquanto"
    elif confirmacoes:
        veredito = "precisa_confirmar"
    else:
        veredito = "viavel"
    proximos: list[str] = []
    if impedimentos:
        proximos.append("explique o impedimento e proponha saída: substituir equipamento/técnica, reduzir porções ou outra receita")
    if operacao.get("impedimentos") or operacao.get("confirmacoes"):
        proximos.append("resolva os bloqueios operacionais: confira duração e fornecimento/armazenamento para este lote; adapte a ficha ou salve somente a condição que Dona Maria confirmar. Um aceite genérico não libera o prato")
        if operacao.get("confirmacoes"):
            proximos.append("se ela confirmar especificamente gás/energia/espaço suficiente para este lote, reconfira a mesma versão usando confirmacoes_operacionais (gas, energia ou espaco_geladeira → fala literal dela). A evidência vale só para esta ficha e perfil; não altere pouco espaço para médio")
    if equip["nao_informado"] or tec["nao_informado"] or tec["parcial"]:
        proximos.append("confirmar com ela os equipamentos/técnicas marcados como nao_informado ou parcial e salvar com perfil_cozinha_salvar")
    if res["pendencias"]:
        proximos.append("resolver as pendências de ingredientes (preço de compra, peso de embalagem, quantidade)")
        proximos.extend(f"{i['ingrediente']}: {i['como_resolver']}" for i in res["itens"] if i.get("como_resolver"))
        repetidos = [p.removesuffix(": " + _REPETIDO) for p in res["pendencias"] if p.endswith(": " + _REPETIDO)]
        if repetidos:
            proximos.append("some numa única linha da ficha as quantidades de: " + "; ".join(dict.fromkeys(repetidos)))
    return {"veredito": veredito, "bloqueios": impedimentos + confirmacoes, "proximos_passos": proximos}


def _somar_cozinha(perfil: dict[str, Any], nome: str | None, ingredientes: list[Any], equip: dict[str, list[str]],
                   tec: dict[str, list[str]]) -> dict[str, Any]:
    """Soma ao que o modelo leu na receita o que a base de conhecimento exige do prato e dos ingredientes
    (prompts/base-de-conhecimento/cozinha-e-equipamentos): o modelo esquece de listar o forno da pizza, a ficha não.
    Muda ``equip`` e ``tec`` no lugar, sem repetir o que o modelo já listou, e devolve a conferência (com as frases prontas)."""
    nomes = [str((i.get("nome") if isinstance(i, dict) else i) or "") for i in ingredientes]
    c = cozinha.conferir(nome, nomes, perfil)
    ja_conhecidos = {cozinha.chave_equipamento(n) for lista in equip.values() for n in lista}
    for item_id in c["equipamentos_usados"]:
        if item_id not in ja_conhecidos:
            equip["ok"].append(cozinha.POR_ID[item_id].rotulo)
            ja_conhecidos.add(item_id)
    ja_equip = {cozinha.chave_equipamento(n) for lista in equip.values() for n in lista}
    for chave, destino in (("falta", "faltando"), ("confirmar", "nao_informado")):
        for f in c[chave]:
            if not ja_equip & set(f["ids"]):
                equip[destino].append(f["precisa"])
                ja_equip |= set(f["ids"])
    ja_tec = {cozinha.habilidade_id(n) for lista in tec.values() for n in lista} - {None}
    for chave, destino in (("faltam", "faltando"), ("aprendendo", "parcial"), ("confirmar", "nao_informado")):
        for hid in c["habilidades"][chave]:
            if hid not in ja_tec:
                tec[destino].append(cozinha.POR_ID[hid].rotulo)
                ja_tec.add(hid)
    return c


def _preparo_adaptado(dados: dict[str, Any]) -> str:
    preparo = str(dados.get("preparo_da_adaptacao") or dados.get("modo_preparo") or "").strip()
    original = str((dados.get("fonte_verificada") or {}).get("modo_preparo") or "")
    return preparo if fonte.normalizar_adaptacao(dados.get("adaptacao")) and norm_text(preparo) != norm_text(original) else ""


def _requisitos(receita: dict[str, Any]) -> dict[str, Any]:
    dados = receita.get("dados") or {}
    original = dados.get("fonte_verificada") or {}
    preparo = str(original.get("modo_preparo") or dados.get("modo_preparo") or "")
    equipamentos, tecnicas = fonte.requisitos(original)
    informados = [str(x) for x in dados.get("equipamentos_necessarios") or []]
    tecnicas_informadas = [str(x) for x in dados.get("tecnicas_necessarias") or []]
    tempo = _num(dados.get("tempo_preparo_min"))
    metodo_adaptado = False
    if adaptado := _preparo_adaptado(dados):
        novos_equip, novas_tecnicas = fonte.requisitos({"modo_preparo": adaptado})
        novos_ids = {cozinha.chave_equipamento(e) for e in novos_equip}
        termicos = {"forno", "fogao", "frigideira", "panela_pressao", "air_fryer", "micro_ondas"}
        if novos_ids & termicos:
            # Fichas antigas copiam requisitos inferidos da fonte para as
            # listas salvas. Retire os que ficaram obsoletos nesse método.
            originais_ids = {cozinha.chave_equipamento(e) for e in equipamentos}
            obsoletos = originais_ids - novos_ids
            informados = [e for e in informados if cozinha.chave_equipamento(e) not in obsoletos]
            tecnicas_obsoletas = set(tecnicas) - set(novas_tecnicas)
            tecnicas_informadas = [t for t in tecnicas_informadas if norm_text(t) not in tecnicas_obsoletas]
            equipamentos, tecnicas, preparo = novos_equip, novas_tecnicas, adaptado
            metodo_adaptado = bool(original and (novos_ids & termicos) != (originais_ids & termicos))
    elif tempo is None:
        tempo = _num(original.get("tempo_preparo_min"))
    requisitos = {
        "equipamentos_necessarios": list(dict.fromkeys(informados + equipamentos)),
        "tecnicas_necessarias": list(dict.fromkeys(tecnicas_informadas + tecnicas)),
        "tempo_preparo_min": tempo,
    }
    if minimo := fonte.tempo_minimo_de_forno(preparo):
        requisitos["tempo_minimo_de_forno"] = minimo
    if metodo_adaptado:
        requisitos["metodo_adaptado"] = True
    return requisitos


def _avaliar_fonte(aval: dict[str, Any], conferencia: dict[str, Any]) -> None:
    if conferencia.get("bloqueios"):
        aval["bloqueios"] = conferencia["bloqueios"] + aval["bloqueios"]
        if aval["veredito"] == "viavel":
            aval["veredito"] = "precisa_confirmar"
        aval["proximos_passos"].insert(0, "complete todos os ingredientes/medidas da fonte ou informe no campo adaptacao uma versão adaptada explícita "
                                          "(toda mudança é apresentada como versão distinta para aceite posterior); não calcule CMV de uma ficha parcial")
        if any(b.get("tipo") == "preparo_adaptacao_pendente" for b in conferencia["bloqueios"]):
            aval["proximos_passos"].insert(0, "a versão adaptada muda ingredientes: informe um modo_preparo próprio, diferente do original, antes de pedir aceite")


def _conferir_preparo_adaptado(conferencia: dict[str, Any], preparo: Any, original: Any = "", *,
                              omitidos: Iterable[str] = ()) -> None:
    alterou_ingredientes = any(not d.startswith(("rendimento da fonte", "tempo da fonte")) for d in conferencia.get("diferencas", []))
    conferencia["exige_preparo_adaptado"] = bool(conferencia.get("adaptacao") and alterou_ingredientes)
    if conferencia["exige_preparo_adaptado"] and (not str(preparo or "").strip() or norm_text(str(preparo)) == norm_text(str(original))):
        conferencia["bloqueios"].append(_bloqueio("preparo_adaptacao_pendente", "A versão adaptada muda ingredientes e ainda não tem um modo de preparo próprio."))
        return
    if not omitidos:
        return
    instrucoes = re.split(r"[.!?;\n]", str(preparo or ""))
    usos = [trecho for linha in instrucoes if (trecho := norm_text(linha))
            and re.search(r"\b(?:misture|junte|adicione|incorpore|acrescente|coloque|tempere|polvilhe|finalize|distribua|use)\b", trecho)
            and not re.search(r"\b(?:sem|omita|dispense|retire|exclua|nao)\b", trecho)]
    for nome in omitidos:
        chave = norm_text(montagem.nome_da_medida(nome))
        if chave and any(re.search(rf"\b{re.escape(chave)}\b", trecho) for trecho in usos):
            conferencia["bloqueios"].append(_bloqueio("preparo_adaptacao_pendente",
                f"O preparo ainda pede {nome}, que ficou de fora desta versão. A ficha precisa do preparo ajustado."))
            return


def _conferir_rendimento(aval: dict[str, Any], porcoes: float | None) -> None:
    if porcoes is not None and math.isfinite(porcoes) and porcoes > 0:
        return
    if aval["veredito"] == "viavel":
        aval["veredito"] = "precisa_confirmar"
    aval["bloqueios"].append(_bloqueio("rendimento_pendente", "Falta confirmar quantas porções este lote rende."))


def _avaliar_prato(store: Store, ingredientes: list[Any], receita: dict[str, Any]) -> dict[str, Any]:
    """Reavalia um prato contra o estado ATUAL: perfil salvo, despensa disponível (estoque menos reservas
    de outros pratos aceitos) e orçamento restante. É o que torna a trava independente do que o modelo diz."""
    req = _requisitos(receita)
    res = _resolver_ingredientes(store, ingredientes)
    req = _com_preparos_caseiros(req, res)
    perfil = store.perfil()
    equip = _checar_equipamentos(perfil, req["equipamentos_necessarios"])
    tec = _checar_tecnicas(perfil, req["tecnicas_necessarias"])
    nome_de_referencia = None if req.get("metodo_adaptado") else str(receita.get("nome") or "")
    da_cozinha = _somar_cozinha(perfil, nome_de_referencia, ingredientes, equip, tec)
    orc = _orcamento_resumo(store, res["compras_total_estimado"])
    dados_receita = receita.get("dados") or {}
    preparo = dados_receita.get("modo_preparo") or (dados_receita.get("fonte_verificada") or {}).get("modo_preparo")
    assinaturas = _assinaturas_operacionais(perfil, ingredientes, str(receita.get("nome") or ""), receita.get("porcoes"), req, preparo)
    condicoes = _confirmacoes_vigentes(dados_receita, assinaturas)
    negativas = {chave for chave, c in condicoes.items() if c.get("estado") == "nao"}
    confirmadas = set(condicoes) - negativas
    operacao = _restricoes_relevantes(perfil, req["tempo_preparo_min"], equip, ingredientes, confirmadas,
                                     indisponiveis_no_lote=negativas,
                                     tempo_minimo_de_forno=req.get("tempo_minimo_de_forno"))
    aval = _avaliar(res, equip, tec, orc, _pendencias_perfil(perfil), operacao)
    if "porcoes" in receita:
        _conferir_rendimento(aval, receita["porcoes"])
    if dados_receita.get("fonte_verificada"):
        conferencia_fonte = fonte.conferir(dados_receita["fonte_verificada"], ingredientes, receita.get("porcoes"),
                                           str(dados_receita.get("adaptacao") or ""), req.get("tempo_receita_min", req["tempo_preparo_min"]))
        _conferir_preparo_adaptado(conferencia_fonte, dados_receita.get("preparo_da_adaptacao"),
                                  dados_receita["fonte_verificada"].get("modo_preparo"),
                                  omitidos=dados_receita.get("ingredientes_omitidos") or ())
        _avaliar_fonte(aval, conferencia_fonte)
    aval.update({"resultado": res, "equipamentos": equip, "tecnicas": tec, "orcamento": orc, "requisitos": req, "cozinha": da_cozinha,
                 "avisos_operacionais": [b["detalhe"] for valores in operacao.values() for b in valores]})
    return aval


def registrar_condicoes_do_lote(store: Store, prato: dict[str, Any], receita: dict[str, Any],
                                aval: dict[str, Any], declaradas: dict[str, tuple[str, str]]) -> None:
    """Grava a resposta literal na ficha em foco antes de perguntar o próximo requisito."""
    dados = receita["dados"]
    ingredientes = dados.get("ingredientes") or []
    preparo = dados.get("modo_preparo") or (dados.get("fonte_verificada") or {}).get("modo_preparo")
    perfil = store.perfil()
    assinaturas = _assinaturas_operacionais(perfil, ingredientes, receita["nome"], receita.get("porcoes"),
                                             aval["requisitos"], preparo)
    vigentes = _confirmacoes_vigentes(dados, assinaturas)
    pendencias = {b["tipo"] for b in aval["bloqueios"]}
    registros = [{"chave": chave, "evidencia": evidencia, "estado": estado, "assinatura": assinaturas[chave]}
                 for chave, (estado, evidencia) in declaradas.items()
                 if estado == "nao" or chave in vigentes or _CONFIRMACOES_DO_LOTE[chave] in pendencias]
    if registros:
        store.atualizar_pendencia_rascunho(prato["id"], receita["id"], confirmacoes_operacionais=registros)


def _recusa(motivo: str, aval: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Resposta padrão de trava: nada foi gravado; o modelo recebe o que falta e o que fazer."""
    frases = (aval.get("cozinha") or {}).get("mensagens") or []
    return {"success": False, "bloqueado": True, "error": motivo, "veredito": aval.get("veredito"),
            "bloqueios": aval.get("bloqueios", []), "proximos_passos": aval.get("proximos_passos", []),
            **({"diga_a_dona_maria": " ".join(frases)} if frases else {}), **extra}


def _aviso_prato_nao_aprovado(store: Store, receita: dict[str, Any] | str, aval: dict[str, Any]) -> dict[str, Any]:
    from .alternativas import alternativa_no_estoque

    nome = receita if isinstance(receita, str) else receita["nome"]
    bloqueios = aval.get("bloqueios") or []
    motivo = bloqueios[0]["detalhe"].rstrip(".?") if bloqueios else "a ficha ainda está incompleta"
    aviso = f"O prato {nome} ainda não foi aprovado. Motivo: {motivo}."
    if bloqueios and bloqueios[0]["tipo"] == "ingrediente_pendente" and (aval.get("resultado") or {}).get("faltantes"):
        return {"alternativa_no_estoque": None, "diga_a_dona_maria": aviso +
                " O ingrediente continua na ficha. Vamos conferir as medidas e o custo do que falta."}
    alternativa = alternativa_no_estoque(Store() if store.ignorar_prato_id else store, receita, _avaliar_prato)
    cozinha_aviso = (aval.get("cozinha") or {}).get("mensagens") or []
    if cozinha_aviso:
        aviso += " " + cozinha_aviso[0]
    if alternativa:
        aviso += (f" {alternativa['nome']} cabe no estoque e na cozinha para {alternativa['porcoes']:g} porções, "
                  f"sem compras. Quer trocar «{nome}» por «{alternativa['nome']}»?")
    elif bloqueios and bloqueios[0]["tipo"] in {
            "tempo_da_receita_pendente", "tempo_insuficiente", "energia_indisponivel", "energia_a_confirmar",
            "gas_indisponivel", "gas_a_confirmar", "refrigeracao_insuficiente", "refrigeracao_a_confirmar"}:
        aviso += " A ficha pode continuar como rascunho. Precisamos resolver essa condição do preparo antes de aceitar ou reservar ingredientes."
    elif any(b["tipo"] in {"ingrediente_pendente", "orcamento"} for b in bloqueios):
        aviso += " Não encontrei um prato semelhante com estoque confirmado. Não dá para montar e registrar esta versão com os ingredientes disponíveis."
    else:
        aviso += " Não encontrei um prato semelhante que eu pudesse confirmar com a despensa e a cozinha atuais."
    return {"alternativa_no_estoque": alternativa, "diga_a_dona_maria": aviso}


def registrar_troca_confirmada(store: Store, texto: str, resposta_anterior: str) -> Optional[str]:
    from .alternativas import alternativa_no_estoque

    troca = escolhas.troca_confirmada(texto, resposta_anterior)
    if troca is None:
        return None
    antigo, novo = troca
    original = next((p for p in store.pratos_em_andamento() if norm_text(p["nome"]) == norm_text(antigo)), None)
    if original and original["status"] == "aceito":
        return "O prato já está aprovado no cardápio. Peça para revisar esse registro antes de trocar."
    receita_id = original.get("receita_id") if original else next(
        (r["id"] for r in reversed(store.receitas()) if norm_text(r["nome"]) == norm_text(antigo)), None)
    receita = store.obter_receita(receita_id) if receita_id else None
    alternativa = alternativa_no_estoque(store, receita or antigo, _avaliar_prato)
    if alternativa is None or norm_text(alternativa["nome"]) != norm_text(novo):
        return f"A troca não foi registrada: {novo} não está confirmado com o estoque disponível agora. Informe o que mudou."
    pratos = store.selecionar_pratos([alternativa], substituir_prato_id=original["id"] if original else None)
    return (f"A troca foi confirmada e {pratos[0]['nome']} está em Minha cozinha > Pratos, como rascunho #{pratos[0]['id']}. "
            "Informe apenas que a escolha foi guardada e ofereça continuar a ficha e o preço.")


def _reservas_de(res: dict[str, Any], legivel: bool = False) -> list[dict[str, Any]]:
    """Linhas da receita que saem da despensa: viram reserva no aceite."""
    out: list[dict[str, Any]] = []
    for linha in res["itens"]:
        if linha.get("item_id") is None or (linha.get("usa_da_despensa_base") or 0) <= 1e-9:
            continue
        if legivel:
            out.append({"ingrediente": linha["item_despensa"], "quantidade": linha["usa_da_despensa"]})
        else:
            out.append({"ingrediente_id": linha["item_id"], "ingrediente": linha["item_despensa"],
                        "quantidade_base": linha["usa_da_despensa_base"], "unidade_base": linha["unidade_base"]})
    return out


# ----------------------------------------------------------------------------- 2.3 conferência
def _fichas_em_preparo_da_sessao(store: Store, session_id: Optional[str]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not session_id:
        return []
    fichas = []
    for prato in store.pratos_em_andamento():
        if prato["status"] not in {"selecionado", "cmv_calculado"} or not prato.get("receita_id"):
            continue
        receita = store.obter_receita(prato["receita_id"])
        if receita and receita["status"] == "conferida" and receita["dados"].get("sessao_da_conferencia") == session_id:
            fichas.append((prato, receita))
    return fichas


@_tool
def pratos_selecionar(args: dict, session_id: Optional[str] = None) -> dict:
    if pesquisa.somente_sugestoes(session_id or ""):
        return {"success": False, "error": "Apresente as opções e aguarde a escolha da Dona Maria antes de guardar uma ficha."}
    falas_dela = falas.da_sessao(session_id) or []
    fala = falas_dela[-1] if falas_dela else None
    opcoes = escolhas.guardar_escolhas(fala.texto, escolhas.lista_da_escolha(falas_dela)) if fala else []
    if not opcoes:
        pedido = escolhas.pedido_de_escolha(fala.texto) if fala else None
        if not pedido:
            raise ValueError("aguarde a Dona Maria escolher os pratos que quer guardar")
        nomes = args.get("nomes")
        if not isinstance(nomes, list) or not nomes or not all(
            isinstance(nome, str) and norm_text(nome) and f" {norm_text(nome)} " in f" {pedido} " for nome in nomes
        ):
            raise ValueError("peça os nomes ou números dos pratos mostrados, sem trocar as opções")
        opcoes = [{"nome": nome} for nome in nomes]
    store = Store()
    pratos = store.selecionar_pratos(opcoes)
    from . import jornada

    jornada.continuar_escolhas(store, [p["id"] for p in pratos])
    if pratos:
        pesquisa.conduzir_escolhido(session_id or "", pratos[0])
    return {"success": True, "pratos_escolhidos": pratos,
            "diga_a_dona_maria": "Salvei suas escolhas como rascunhos. Vou conferir as receitas e conduzir com você o que faltar para cada prato.",
            "proximo_passo": "Retome a primeira ficha escolhida agora: leia sua fonte real e confira ingredientes, rendimento e requisitos. Não encerre mandando abrir um painel."}


def _medidas_da_versao(itens: list[Any]) -> list[Any]:
    return [(i.get("nome"), i.get("quantidade"), i.get("unidade")) if isinstance(i, dict) else i for i in itens]


def _tempo_da_conferencia(args: dict, fonte_verificada: dict, anteriores: dict, *,
                          mesma_versao: bool, ficha_original: bool,
                          minimo: float = 0) -> tuple[float | None, str | None, str | None]:
    publicado = _num(fonte_verificada.get("tempo_preparo_min"))
    if publicado and publicado > 0 and math.isfinite(publicado) and not _preparo_adaptado({**args, "fonte_verificada": fonte_verificada}):
        return publicado, "fonte_publicada", None
    informado = _num(args.get("tempo_preparo_min"))
    base = args.get("tempo_preparo_base")
    base = base.strip() if isinstance(base, str) else ""
    origem_anterior = anteriores.get("tempo_preparo_origem")
    tempo_anterior = _num(anteriores.get("tempo_preparo_min"))
    anterior_valido = bool(tempo_anterior and math.isfinite(tempo_anterior) and tempo_anterior > 0 and tempo_anterior >= minimo)
    estimativa_valida = bool(informado and math.isfinite(informado) and informado > 0 and informado >= minimo and base)
    if (mesma_versao and origem_anterior in {"estimativa_declarada", "estimativa_dos_passos"}
            and anterior_valido and (origem_anterior == "estimativa_declarada" or not estimativa_valida)):
        return tempo_anterior, origem_anterior, anteriores.get("tempo_preparo_base")
    if estimativa_valida:
        return informado, "estimativa_dos_passos", base
    return (None if ficha_original else informado), None, None


@_tool
def receita_conferir_ingredientes(args: dict, session_id: Optional[str] = None, *, receita_esperada: int | None = None,
                                  montagem_com_estoque: bool = False) -> dict:
    if pesquisa.somente_sugestoes(session_id or ""):
        return {"success": False, "error": "Apresente as opções e aguarde a escolha da Dona Maria antes de montar uma ficha."}
    store = Store()
    prato_id = args.get("prato_id")
    cotacao = pesquisa.alvo_da_cotacao(session_id or "")
    if cotacao:
        if prato_id is not None and int(prato_id) != cotacao[0]:
            raise ValueError(f"use a ficha #{cotacao[0]} para essa cotação")
        prato_id = cotacao[0]
    falas_dela = falas.da_sessao(session_id) or []
    id_da_fala = escolhas.id_mencionado(falas_dela[-1].texto) if falas_dela else None
    if id_da_fala is not None:
        if prato_id is not None and int(prato_id) != id_da_fala:
            raise ValueError(f"a Dona Maria pediu a conferência do prato #{id_da_fala}")
        prato_id = id_da_fala
    nome = str(args.get("nome") or args.get("prato") or "").strip()
    if falas_dela and id_da_fala is None:
        opcoes = escolhas.escolhidas(falas_dela[-1].texto, escolhas.lista_da_escolha(falas_dela))
        if len(opcoes) == 1:
            escolhido = store.selecionar_pratos(opcoes)[0]
            if prato_id is not None and int(prato_id) != escolhido["id"]:
                raise ValueError(f"a Dona Maria escolheu o prato #{escolhido['id']}, {escolhido['nome']}")
            prato_id = escolhido["id"]
    fonte_url = args.get("fonte_url")
    alvo_conduzido = pesquisa.alvo_escolhido(session_id or "")
    if alvo_conduzido:
        if alvo_conduzido.get("adaptacao_delegada"):
            receita_esperada = alvo_conduzido["receita_id"]
        alvo_id = int(alvo_conduzido["id"])
        if prato_id is not None and int(prato_id) != alvo_id:
            raise ValueError(f"esta conferência conduz o prato #{alvo_id}; não troque pelo número de outra ficha")
        fonte_alvo = alvo_conduzido.get("fonte_url")
        if alvo_conduzido.get("revisao_fonte") or alvo_conduzido.get("adaptacao_delegada"):
            alvo_salvo = store.obter_prato(alvo_id)
            if (not alvo_salvo or alvo_salvo["status"] != "selecionado"
                    or alvo_salvo.get("receita_id") != alvo_conduzido.get("receita_id")):
                raise ValueError("a ficha mudou desde o início desta retomada; confira a versão atual antes de revisar")
            # A retomada já foi vinculada à fonte salva. Uma URL redigitada
            # pela LLM não substitui a página que a pessoa pediu para conferir.
            fonte_url = fonte_alvo
        elif fonte_url and fonte_alvo and fonte_url != fonte_alvo:
            raise ValueError(f"use a fonte do prato escolhido #{alvo_id}: {fonte_alvo}")
        if prato_id is None:
            alvo_salvo = store.obter_prato(alvo_id)
            if not alvo_salvo or alvo_salvo["status"] != "selecionado":
                raise ValueError("o rascunho em montagem mudou; consulte a escolha atual antes de conferir")
            prato_id = alvo_id
            fonte_url = fonte_url or fonte_alvo
    criar_rascunho = False
    montagem_web = pesquisa.montando(session_id or "")
    if not montagem_web and prato_id is None and (not nome or not fonte_url):
        candidatas = [(p, r) for p, r in _fichas_em_preparo_da_sessao(store, session_id)
                      if r["dados"].get("fonte_verificada")
                      and (not nome or norm_text(nome) in {norm_text(p["nome"]), norm_text(r["nome"])})
                      and (not fonte_url or r.get("fonte_url") == fonte_url)]
        if len(candidatas) == 1:
            escolhido, previa = candidatas[0]
            prato_id, nome, fonte_url = escolhido["id"], escolhido["nome"], previa.get("fonte_url")
        elif len(candidatas) > 1:
            return _recusa("há mais de um rascunho web nesta sessão; confirme qual versão deve ser revisada", {
                "bloqueios": [_bloqueio("ficha_ambigua", "mais de um rascunho corresponde à revisão")],
                "proximos_passos": ["confirmar qual rascunho ela quer continuar e usar o prato_id correspondente"]},
                opcoes=[{"prato_id": p["id"], "receita_id": r["id"], "nome": p["nome"]} for p, r in candidatas])
    if montagem_web:
        fonte_lida = pesquisa.fonte_da_montagem(session_id or "")
        if not fonte_lida:
            raise ValueError("abra uma página direta com ingredientes e preparo para conferir o prato")
        if prato_id is None:
            candidatas = [(p, r) for p, r in _fichas_em_preparo_da_sessao(store, session_id)
                          if r.get("fonte_url") == fonte_lida[1] and norm_text(r["nome"]) == norm_text(fonte_lida[0])]
            if len(candidatas) == 1:
                prato_id = candidatas[0][0]["id"]
            elif len(candidatas) > 1:
                return _recusa("há mais de um rascunho desta fonte na sessão; informe o prato que a Dona Maria quer revisar", {
                    "bloqueios": [_bloqueio("ficha_ambigua", "mais de um prato corresponde à fonte lida")],
                    "proximos_passos": ["confirmar qual dos rascunhos deve receber a revisão"]},
                    opcoes=[{"prato_id": p["id"], "receita_id": r["id"], "nome": p["nome"]} for p, r in candidatas])
        if prato_id is not None and id_da_fala != prato_id:
            em_preparo = store.obter_prato(int(prato_id))
            previa = store.obter_receita(em_preparo["receita_id"]) if em_preparo and em_preparo.get("receita_id") else None
            if not (previa and previa.get("fonte_url") == fonte_lida[1]
                    and previa["dados"].get("sessao_da_conferencia") == session_id
                    and previa["dados"].get("origem_montagem") == "fonte_web"):
                raise ValueError("use a nova ficha lida para montar o rascunho; os pratos escolhidos são fichas separadas")
        nome_lido, url_lida = fonte_lida
        if norm_text(nome) not in {"", "receita sem nome", "prato sem nome", norm_text(nome_lido)} or fonte_url not in (None, "", url_lida):
            raise ValueError(f"use a ficha lida de {nome_lido} e a fonte {url_lida}")
        nome, fonte_url = fonte_lida
        criar_rascunho = prato_id is None
    elif prato_id is None and falas_dela and montagem.pediu_montagem(falas_dela[-1].texto):
        criar_rascunho = True
    prato_salvo = None
    if prato_id is not None:
        prato_salvo = store.obter_prato(int(prato_id))
        if prato_salvo is None:
            raise KeyError(f"prato_id {prato_id} não encontrado")
        if norm_text(nome) not in {"", "receita sem nome", "prato sem nome", norm_text(prato_salvo["nome"])}:
            raise ValueError(f"use o nome do prato escolhido {prato_salvo['nome']!r} ou deixe que o sistema o recupere pelo número")
        nome = prato_salvo["nome"]
    if not nome or norm_text(nome) in {"receita sem nome", "prato sem nome"}:
        raise ValueError("informe o nome da receita ou o número do prato escolhido para guardar a ficha certa")
    if prato_salvo is None and not criar_rascunho:
        # O vínculo implícito pelo nome também atualiza um rascunho existente.
        correspondente = next((p for p in reversed(store.pratos_em_andamento())
                                if p["status"] == "selecionado" and norm_text(p["nome"]) == norm_text(nome)), None)
        if correspondente:
            prato_id = correspondente["id"]
            prato_salvo = store.obter_prato(prato_id)
    ficha_salva = store.obter_receita(prato_salvo["receita_id"]) if prato_salvo and prato_salvo.get("receita_id") else None
    if receita_esperada is not None and (not ficha_salva or ficha_salva["id"] != receita_esperada or prato_salvo["status"] != "selecionado"):
        raise ValueError("a ficha mudou enquanto preparávamos a adaptação")
    anteriores = (ficha_salva or {}).get("dados") or {}
    montagem_com_estoque = bool(montagem_com_estoque or anteriores.get("montagem_com_estoque")
                               or (alvo_conduzido or {}).get("adaptacao_delegada") or montagem_web
                               or (alvo_conduzido and not ficha_salva))
    if cotacao:
        dados_cotacao = args.get("ingredientes")
        if not ficha_salva or prato_salvo["status"] != "selecionado":
            raise ValueError("a cotação requer a ficha atual do prato em rascunho")
        if not isinstance(dados_cotacao, list) or len(dados_cotacao) != 1 or not isinstance(dados_cotacao[0], dict):
            raise ValueError("envie apenas o ingrediente cotado, com preço e tamanho da embalagem")
        preco, quantidade, unidade = _compra_info(dados_cotacao[0])
        if not preco or not quantidade or not unidade or not all(math.isfinite(v) and v > 0 for v in (preco, quantidade)):
            raise ValueError("informe o preço e o tamanho da embalagem que encontrou na cotação")
        ingredientes_cotados = [dict(i) if isinstance(i, dict) else {"nome": i} for i in anteriores["ingredientes"]]
        destino = [i for i in ingredientes_cotados if isinstance(i, dict) and norm_text(str(i.get("nome"))) == norm_text(cotacao[1])]
        nome_cotado = norm_text(str(dados_cotacao[0].get("nome") or ""))
        if len(destino) != 1 or nome_cotado not in {norm_text(cotacao[1]), norm_text(montagem.nome_da_medida(cotacao[1]))}:
            raise ValueError(f"envie a cotação de {cotacao[1]} na ficha atual")
        destino[0].update(dict(zip(_CAMPOS_DE_COMPRA, (preco, quantidade, unidade))))
        args = {**anteriores, "ingredientes": ingredientes_cotados, "porcoes": ficha_salva["porcoes"]}
        fonte_url = ficha_salva.get("fonte_url")
    notas_anteriores = anteriores.get("ingredientes_faltantes") or []
    if anteriores.get("ingredientes") and (receita_esperada is not None or not notas_anteriores):
        notas_anteriores = Store.anotacoes_faltantes(_resolver_ingredientes(store, anteriores["ingredientes"]),
                                                    anteriores["ingredientes"], notas_anteriores, anteriores.get("adaptacao"))
    fonte_salva = anteriores.get("fonte_verificada")
    url_salva = (ficha_salva or {}).get("fonte_url") or (fonte_salva or {}).get("url")
    if prato_salvo:
        fonte_url = fonte_url or url_salva or (prato_salvo["dados"].get("selecao") or {}).get("fonte_url")
    ingredientes = args.get("ingredientes") or []
    porcoes = _num(args.get("porcoes"))
    fonte_verificada = fonte_salva if cotacao else pesquisa.ficha_lida(session_id or "", fonte_url)
    if fonte_salva and url_salva != fonte_url:
        fala_atual = falas_dela[-1].texto if falas_dela else ""
        normal = norm_text(fala_atual)
        autorizou_troca = (not fala_atual.rstrip().endswith("?") and not re.search(r"\b(?:nao|talvez|se)\b", normal)
                           and (re.search(r"\b(?:troque|mude|substitua)\b[^.!?]{0,40}\b(?:fonte|link|receita)\b", normal)
                                or (isinstance(fonte_url, str) and fonte_url in fala_atual
                                    and re.search(r"\b(?:use|prefiro|escolho|quero|troque|mude|substitua)\b", normal))))
        if not fonte_verificada or not autorizou_troca:
            raise ValueError(f"use a fonte verificada deste prato ({url_salva}); só troque com pedido da Dona Maria e após ler a nova receita")
    if fonte_verificada is None and url_salva == fonte_url:
        fonte_verificada = fonte_salva
    pedido_adaptacao = bool(falas_dela and montagem.pediu_adaptacao(falas_dela[-1].texto))
    adaptacao = fonte.normalizar_adaptacao(args.get("adaptacao"))
    adaptacao_anterior = fonte.normalizar_adaptacao(anteriores.get("adaptacao"))
    if (adaptacao_anterior and ficha_salva and fonte_url == url_salva
            and "adaptacao" not in args and not pedido_adaptacao):
        cotados = bool(ingredientes and isinstance(ingredientes, list)
                       and all(isinstance(i, dict) and all(_compra_info(i)) for i in ingredientes))
        mesmos_itens = _medidas_da_versao(ingredientes) == _medidas_da_versao(anteriores.get("ingredientes") or [])
        if not ingredientes or cotados or mesmos_itens:
            if not ingredientes or cotados:
                enviados = ingredientes if cotados else []
                ingredientes = [dict(i) if isinstance(i, dict) else i for i in anteriores["ingredientes"]]
                _levar_cotacoes(ingredientes, enviados)
            args = {**anteriores, "porcoes": ficha_salva["porcoes"], **args, "ingredientes": ingredientes}
            porcoes = _num(args.get("porcoes"))
            adaptacao = adaptacao_anterior
    if not adaptacao and pedido_adaptacao and ingredientes:
        adaptacao = "Adaptação local solicitada: " + falas_dela[-1].texto
    selecao_integral = bool(not adaptacao and alvo_conduzido and prato_id == alvo_conduzido["id"]
                           and not alvo_conduzido.get("adaptacao_delegada") and fonte_url == alvo_conduzido.get("fonte_url")
                           and prato_salvo and not prato_salvo.get("receita_id") and (
                               not ingredientes and not pedido_adaptacao
                               or falas_dela and escolhas.escolha_integral(falas_dela[-1].texto, escolhas.lista_da_escolha(falas_dela))))
    primeira_delegada = bool(montagem_web and not adaptacao and falas_dela
                            and (not prato_salvo or not prato_salvo.get("receita_id"))
                            and pesquisa.montagem_integral(falas_dela[-1].texto))
    revisar_original = bool(not adaptacao and alvo_conduzido and alvo_conduzido.get("revisao_fonte")
                            and prato_salvo and prato_salvo["status"] == "selecionado"
                            and prato_id == alvo_conduzido["id"]
                            and prato_salvo.get("receita_id") == alvo_conduzido.get("receita_id")
                            and fonte_url == alvo_conduzido.get("fonte_url"))
    if revisar_original:
        receita_anterior = store.obter_receita(prato_salvo["receita_id"])
        revisar_original = bool(receita_anterior and not fonte.normalizar_adaptacao(receita_anterior["dados"].get("adaptacao"))
                                and receita_anterior.get("fonte_url") == fonte_url
                                and receita_anterior["dados"].get("fonte_verificada"))
    # Cotação de um complemento numa ficha copiada da fonte: a receita não mudou, só ganhou um preço de compra.
    cotacao_da_original = bool(
        fonte_verificada and not adaptacao and ficha_salva and prato_salvo["status"] == "selecionado"
        and ficha_salva["dados"].get("ficha_original_da_fonte") and ficha_salva.get("fonte_url") == fonte_url
        and isinstance(ingredientes, list) and any(isinstance(i, dict) and all(_compra_info(i)) for i in ingredientes)
        and not (falas_dela and _PEDIU_MUDANCA.search(norm_text(falas_dela[-1].texto))))
    reconferencia_original = bool(fonte_verificada and not adaptacao and ficha_salva and prato_salvo["status"] == "selecionado"
                                   and anteriores.get("ficha_original_da_fonte") and ficha_salva.get("fonte_url") == fonte_url
                                   and not ingredientes)
    ficha_original = bool(anteriores.get("ficha_original_da_fonte") if cotacao else
                          fonte_verificada and (selecao_integral or primeira_delegada or revisar_original
                                                or cotacao_da_original or reconferencia_original))
    if ficha_original and not cotacao:
        # Ela escolheu a receita publicada, sem pedir alterações. Copiar fatos
        # já lidos não depende de a LLM redigitar medidas e acompanhamentos.
        enviados = ingredientes if cotacao_da_original else None
        adaptacao = ""
        ingredientes = fonte.ingredientes_publicados(fonte_verificada)
        _levar_cotacoes(ingredientes, ((ficha_salva or {}).get("dados") or {}).get("ingredientes"), enviados)
        if url_salva == fonte_url:
            fonte.levar_medidas_confirmadas(ingredientes, anteriores)
        porcoes = _num(fonte_verificada.get("porcoes")) or _num((ficha_salva or {}).get("porcoes"))
        equipamentos, tecnicas = fonte.requisitos(fonte_verificada)
        args = {**args, "modo_preparo": fonte_verificada.get("modo_preparo"),
                "equipamentos_necessarios": equipamentos, "tecnicas_necessarias": tecnicas}
    omitidos = args.get("ingredientes_omitidos") or []
    if not isinstance(omitidos, list) or any(not isinstance(n, str) or not n.strip() for n in omitidos):
        raise ValueError("ingredientes_omitidos precisa ser uma lista de nomes")
    if omitidos and not adaptacao:
        raise ValueError("descreva a adaptação para omitir ingredientes")
    if not isinstance(ingredientes, list) or not ingredientes:
        raise ValueError("informe 'ingredientes' como lista de {nome, quantidade, unidade}")
    if pedido_adaptacao:
        pedidos = montagem.omissoes_pedidas(falas_dela[-1].texto, [*(anteriores.get("ingredientes") or []), *ingredientes])
        omitidos = list(dict.fromkeys([*omitidos, *pedidos]))
    if omitidos:
        nomes_omitidos = {norm_text(montagem.nome_da_medida(n)) for n in omitidos}
        ingredientes = [i for i in ingredientes if norm_text(montagem.nome_da_medida(
            str(i.get("nome") if isinstance(i, dict) else i))) not in nomes_omitidos]
        if not ingredientes:
            raise ValueError("a adaptação precisa ter ingredientes realmente usados")
    if fonte_verificada:
        ingredientes = fonte.nomes_dos_ingredientes(fonte_verificada, ingredientes)
        args = {**args, "fonte_verificada": fonte_verificada}
        if porcoes is None and fonte_verificada.get("porcoes"):
            porcoes = _num(fonte_verificada["porcoes"])
    estimadas = {}
    if montagem_com_estoque and not cotacao:
        # Uma quantidade decidida pela usuária prevalece sobre a estimativa da consultora.
        ingredientes = [dict(i) if isinstance(i, dict) else i for i in ingredientes]
        medidas_firmes = anteriores.get("medidas_de_uso_confirmadas") or {}
        ultima_fala = falas_dela[-1:]
        for item in ingredientes:
            if not isinstance(item, dict) or item.get("nome") not in medidas_firmes:
                continue
            confirmada = medidas_firmes[item["nome"]]
            quantidade_proposta = _num(item.get("quantidade"))
            unidade_proposta = str(item.get("unidade") or "")
            se_altera_a_medida = bool(quantidade_proposta and unidade_proposta and ultima_fala
                                       and falas.apoia([montagem.nome_da_medida(item["nome"])], ultima_fala)
                                       and compras.medida_citada(ultima_fala[0].texto, quantidade_proposta, unidade_proposta))
            if not se_altera_a_medida:
                item.update(quantidade=confirmada["quantidade"], unidade=confirmada["unidade"])
        ingredientes, estimadas = montagem.estimar_medidas_do_lote(
            {"nome": nome, "porcoes": porcoes,
             "dados": {**args, "ingredientes": ingredientes, "fonte_verificada": fonte_verificada}})
        for item in ingredientes:
            if not isinstance(item, dict) or not item.get("nome"):
                continue
            medida = _num(item.get("quantidade"))
            base = item.get("base_estimativa_de_uso")
            if medida is not None and math.isfinite(medida) and medida > 0 and isinstance(base, str) and base.strip():
                estimadas[item["nome"]] = {"quantidade": medida, "unidade": item.get("unidade"), "base": base.strip()}
    preparo_publicado = (fonte_verificada or {}).get("modo_preparo") or ""
    preparo = str(args.get("modo_preparo") or preparo_publicado).strip()
    preparo_anterior = str(anteriores.get("modo_preparo") or preparo_publicado)
    mesma_versao = bool(ficha_salva and ficha_salva.get("fonte_url") == fonte_url
                       and norm_text(preparo) == norm_text(preparo_anterior)
                       and adaptacao == fonte.normalizar_adaptacao(anteriores.get("adaptacao"))
                       and porcoes == ficha_salva.get("porcoes")
                       and _medidas_da_versao(ingredientes) == _medidas_da_versao(anteriores.get("ingredientes") or []))
    args = {**args, "adaptacao": adaptacao}
    requisitos = _requisitos({"dados": args})
    tempo, tempo_origem, tempo_base = _tempo_da_conferencia(args, fonte_verificada or {}, anteriores,
                                                           mesma_versao=mesma_versao, ficha_original=ficha_original,
                                                           minimo=requisitos.get("tempo_minimo_de_forno", 0))
    requisitos["tempo_preparo_min"] = tempo
    args.update(requisitos)
    fidelidade = fonte.conferir(fonte_verificada, ingredientes, porcoes, adaptacao, args.get("tempo_preparo_min")) if fonte_verificada else None
    if fidelidade:
        _conferir_preparo_adaptado(fidelidade, args.get("modo_preparo"), fonte_verificada.get("modo_preparo"), omitidos=omitidos)
    res = _resolver_ingredientes(store, ingredientes)
    requisitos = _com_preparos_caseiros(requisitos, res)
    perfil = store.perfil()
    equip = _checar_equipamentos(perfil, requisitos["equipamentos_necessarios"])
    tec = _checar_tecnicas(perfil, requisitos["tecnicas_necessarias"])
    da_cozinha = _somar_cozinha(perfil, None if requisitos.get("metodo_adaptado") else nome, ingredientes, equip, tec)
    assinaturas = _assinaturas_operacionais(perfil, ingredientes, nome, porcoes, requisitos, preparo)
    minuto_minimo = requisitos.get("tempo_minimo_de_forno")
    operacao_baseline = _restricoes_relevantes(perfil, requisitos["tempo_preparo_min"], equip, ingredientes,
                                             tempo_minimo_de_forno=minuto_minimo)
    tipos_pendentes = {b["tipo"] for b in operacao_baseline["confirmacoes"]}
    chaves_pendentes = {chave for chave, tipo in _CONFIRMACOES_DO_LOTE.items() if tipo in tipos_pendentes}
    pedido_operacional = None if cotacao else args.get("confirmacoes_operacionais")
    # O perfil já responde condições normais. Campos extras que o modelo enviar
    # não devem reabrir cadastro nem criar evidência de uma confirmação inexistente.
    if not chaves_pendentes:
        pedido_operacional = None
    elif isinstance(pedido_operacional, dict):
        pedido_operacional = {chave: evidencia for chave, evidencia in pedido_operacional.items() if chave in chaves_pendentes}
    confirmacoes_operacionais, confirmacoes_recusadas = _receber_confirmacoes_operacionais(pedido_operacional, assinaturas, session_id)
    condicoes = _confirmacoes_vigentes(anteriores, assinaturas) if mesma_versao else {}
    condicoes.update({c["chave"]: c for c in confirmacoes_operacionais})
    confirmacoes_operacionais = list(condicoes.values())
    negativas = {chave for chave, c in condicoes.items() if c.get("estado") == "nao"}
    operacao = _restricoes_relevantes(perfil, requisitos["tempo_preparo_min"], equip, ingredientes,
                                     set(condicoes) - negativas, indisponiveis_no_lote=negativas, tempo_minimo_de_forno=minuto_minimo)
    avisos = [b["detalhe"] for valores in operacao.values() for b in valores]
    orc = _orcamento_resumo(store, res["compras_total_estimado"])
    pend_perfil = _pendencias_perfil(perfil)
    ja_tem = [l["ingrediente"] for l in res["itens"] if l.get("item_despensa") and l.get("falta") == "nada"]
    parcial = [l["ingrediente"] for l in res["itens"] if l.get("item_despensa") and l.get("falta") not in (None, "nada")]
    nao_tem = [l["ingrediente"] for l in res["itens"] if not l.get("item_despensa") and not l.get("recurso_de_preparo")]
    aval = _avaliar(res, equip, tec, orc, pend_perfil, operacao)
    _conferir_rendimento(aval, porcoes)
    if fidelidade:
        _avaliar_fonte(aval, fidelidade)
    veredito = aval["veredito"]
    dados = {**(anteriores if cotacao else {}),
             "ingredientes": ingredientes, "resultado": res, "equipamentos": equip, "tecnicas": tec, "veredito": veredito,
             "bloqueios": aval["bloqueios"], "prato_id": prato_id,
             "ingredientes_faltantes": Store.anotacoes_faltantes(res, ingredientes, notas_anteriores, adaptacao),
             "ingredientes_omitidos": omitidos,
             "equipamentos_necessarios": args.get("equipamentos_necessarios"), "tecnicas_necessarias": args.get("tecnicas_necessarias"),
             "tempo_preparo_min": args.get("tempo_preparo_min"), "sessao_da_conferencia": session_id,
             "confirmacoes_operacionais": confirmacoes_operacionais,
             "confirmacoes_recusadas": confirmacoes_recusadas,
             "fonte_verificada": fonte_verificada, "fidelidade_fonte": fidelidade,
             "ficha_original_da_fonte": ficha_original,
             "adaptacao": adaptacao or None,
             "adaptacao_proposta_no_contexto": (anteriores.get("adaptacao_proposta_no_contexto") if cotacao else
                                               _assinatura_falas(falas_dela) if adaptacao and fonte_verificada else None),
             "versao": f"{nome} (adaptação)" if adaptacao else nome,
             "preparo_da_adaptacao": str(args.get("modo_preparo") or "").strip() if adaptacao else None,
             "modo_preparo": preparo}
    if montagem_com_estoque:
        dados["montagem_com_estoque"] = True
    if url_salva == fonte_url:
        medidas_confirmadas = fonte.medidas_de_uso_vigentes(ingredientes, anteriores, "medidas_de_uso_confirmadas")
        if medidas_confirmadas:
            dados["medidas_de_uso_confirmadas"] = medidas_confirmadas
        estimadas = {**fonte.medidas_de_uso_vigentes(ingredientes, anteriores, "medidas_de_uso_estimadas"), **estimadas}
    estimadas = {nome_item: medida for nome_item, medida in estimadas.items()
                 if nome_item not in dados.get("medidas_de_uso_confirmadas", {})}
    if estimadas:
        dados["medidas_de_uso_estimadas"] = estimadas
    elif cotacao:
        dados.pop("medidas_de_uso_estimadas", None)
    if tempo_origem:
        dados["tempo_preparo_origem"] = tempo_origem
    if tempo_base:
        dados["tempo_preparo_base"] = tempo_base
    if mesma_versao and anteriores.get("perguntas_esgotadas"):
        dados["perguntas_esgotadas"] = anteriores["perguntas_esgotadas"]
    if criar_rascunho or montagem_web or fonte_verificada:
        dados["origem_montagem"] = "fonte_web" if montagem_web or fonte_verificada else "rascunho_solicitado"
    receita_id = store.salvar_receita(nome, fonte_url, porcoes, dados, "conferida",
                                      prato_id=int(prato_id) if prato_id is not None else None, criar_rascunho=criar_rascunho,
                                      receita_esperada=ficha_salva["id"] if cotacao else receita_esperada)
    # A Store também vincula uma ficha pelo nome de uma escolha existente,
    # mesmo sem prato_id no argumento. Use sempre o vínculo realmente salvo.
    receita_salva = store.obter_receita(receita_id)
    prato_id = receita_salva["dados"].get("prato_id")
    dados["prato_id"] = prato_id
    dados["ingredientes_faltantes"] = receita_salva["dados"].get("ingredientes_faltantes") or []
    aviso = (_aviso_prato_nao_aprovado(store, {"id": receita_id, "nome": nome, "porcoes": porcoes, "dados": dados},
                                      {**aval, "cozinha": da_cozinha})
             if veredito != "viavel" else {})
    compras_previstas = ("A lista de compras ainda tem itens a conferir." if res["pendencias"] else
                        f"Compras complementares previstas: {costing.brl(res['compras_total_estimado'])}.")
    texto_conferencia = f"Ficha de {nome} conferida. {compras_previstas} Saldo atual para compras: {costing.brl(orc['saldo_atual'])}."
    if texto_caseiro := _texto_preparos_caseiros(res):
        texto_conferencia += " " + texto_caseiro
    if texto_medidas := montagem.texto_medidas_estimadas(dados):
        texto_conferencia += " " + texto_medidas
    if dados["ingredientes_faltantes"]:
        texto_conferencia += " " + " ".join(a["observacao"] for a in dados["ingredientes_faltantes"]) + " A ficha fica salva com essas observações, sem compra automática."
    if ficha_original:
        texto_conferencia = "Montei a ficha com todos os ingredientes e as medidas publicadas na fonte escolhida. " + texto_conferencia
    if adaptacao and fidelidade:
        texto_conferencia = (f"Versão {nome} (adaptação): {adaptacao}. Diferenças da fonte: " +
                             "; ".join(fidelidade["diferencas"]) + ". " + texto_conferencia)
    if not res["pendencias"] and not (fidelidade or {}).get("bloqueios"):
        texto_conferencia += f" Custo dos ingredientes usados no lote: {costing.brl(res['cmv_total'])}."
    cotadas = [c for c in compras.da_conferencia(res) if cotacao and norm_text(c["nome"]) == norm_text(cotacao[1])]
    texto_cotacao = ("Cotação anotada na ficha: " + compras.mostrar(cotadas)[0] + ". É uma estimativa de compra.") if cotadas else ""
    if texto_cotacao:
        texto_conferencia += " " + texto_cotacao
    proximos = list(aval["proximos_passos"])
    if da_cozinha["mensagens"]:
        proximos.insert(0, "conte a ela, com as palavras de 'avisos_da_cozinha', o que a cozinha dela não faz ou ainda não confirmou")
    sem_preco = [str(i["ingrediente"]) for i in res["itens"] if i.get("falta_preco")]
    if veredito == "viavel":
        ref = f"prato #{prato_id}, {nome}," if prato_id is not None else nome
        proximos.append(f"mostre a conferência e pergunte: 'Você aceita preparar {ref} nessa versão?' Só avance após o aceite dela")
        proximo_passo = f"Mostre a ficha e peça o aceite desta versão. Após o aceite, chame prato_calcular_cmv(receita_id={receita_id})."
    else:
        proximo_passo = (f"Resolva somente as pendências indicadas e reconfira receita_conferir_ingredientes(prato_id={prato_id}) "
                         "com a ficha completa; não chame CMV enquanto houver bloqueios.")
        if sem_preco:
            proximo_passo += (" Mantenha os faltantes na ficha. Falta preço de compra de: " + "; ".join(sem_preco[:4]) +
                              ". Pesquise o preço de mercado (web_search) ou pergunte a ela e, ao reconferir, informe "
                              "preco_compra_estimado, quantidade_compra e unidade_compra nesses ingredientes.")
    proposta_equivalente = None
    if montagem_web and veredito == "inviavel_por_enquanto":
        proposta_equivalente = montagem.propor_alternativa(store, nome, session_id or "")
    if proposta_equivalente:
        proximos = ["Explique o que faltou na receita original e apresente a proposta_equivalente já montada, com o rendimento e a confirmação da versão."]
        aviso = {"alternativa_no_estoque": None, "diga_a_dona_maria": ""}
        proximo_passo = "Apresente a proposta_equivalente já salva e peça o aceite dessa versão; aguarde a resposta antes de CMV e preço."
    if fonte_verificada and fidelidade and not proposta_equivalente:
        from . import jornada

        pergunta_faltante = (jornada.pergunta_pendente({"id": prato_id, "nome": nome}, aval["bloqueios"], dados)
                             if prato_id is not None else None)
        linhas = [f"**{dados['versao']}** (prato #{prato_id}, rascunho).",
                  f"Fonte original: [{fonte_verificada['nome']}]({fonte_verificada['url']}).",
                  "Ingredientes do lote: " + "; ".join(
                      f"{_numero_legivel(i['quantidade'])} {i.get('unidade', '')} de {i.get('nome', '')}" if i.get("quantidade") is not None
                      else f"{i.get('nome', '')} (medida a confirmar)" for i in ingredientes if isinstance(i, dict)) + ".",
                  f"Rendimento proposto: {porcoes:g} porções." if porcoes else "Falta confirmar o rendimento do lote.",
                  ("Diferenças da fonte:\n" + "\n".join("- " + diferenca for diferenca in fidelidade["diferencas"]))
                  if fidelidade["diferencas"] else "Os ingredientes da fonte estão representados na ficha conferida."]
        if dados["ingredientes_faltantes"]:
            linhas.append("Observações salvas na ficha: " + " ".join(a["observacao"] for a in dados["ingredientes_faltantes"]))
        if texto_cotacao and veredito != "viavel":
            linhas.append(texto_cotacao)
        sem_medida = fonte.sem_medida(fidelidade)
        if sem_medida:
            # Sem "?" nem "antes de aprovar": a tela recolhe a ficha longa e deixa à vista só a pergunta final.
            confirmadas = dados.get("medidas_de_uso_confirmadas") or {}
            sem_quantidade = {norm_text(montagem.nome_da_medida(i["ingrediente"])) for i in res["itens"] if i.get("qtd_num") == 0}
            abertas = [nome for nome in sem_medida if norm_text(montagem.nome_da_medida(nome)) in sem_quantidade]
            if abertas:
                linhas.append("A receita não traz a quantidade de: " + "; ".join(abertas) + ". Vou propor essas medidas para você conferir.")
            if confirmadas:
                linhas.append("Medidas de uso locais confirmadas, não publicadas na fonte: " + "; ".join(
                    f"{m['quantidade']:g} {m['unidade']} de {montagem.nome_da_medida(nome)}" for nome, m in confirmadas.items()) + ".")
            if texto_medidas and veredito != "viavel":
                linhas.append(texto_medidas)
        if dados["modo_preparo"] and (not fidelidade.get("exige_preparo_adaptado") or dados["preparo_da_adaptacao"]):
            estimativa = "estimativa de " if tempo_origem and tempo_origem.startswith("estimativa_") else "cerca de "
            duracao = f" ({estimativa}{_numero_legivel(tempo)} min)" if tempo is not None else ""
            linhas.append(f"Preparo{duracao}: {dados['modo_preparo']}")
            if tempo_base:
                linhas.append(f"Base da estimativa de tempo: {tempo_base}")
        if texto_caseiro and veredito != "viavel":
            linhas.append(texto_caseiro)
        if veredito == "viavel":
            linhas.extend([texto_conferencia, f"Você gosta dessa versão e aceita preparar o prato #{prato_id} sem impedimentos?"])
        else:
            primeiro = aval["bloqueios"][0]["detalhe"] if aval["bloqueios"] else "Falta conferir um requisito desta versão."
            if pergunta_faltante and (pergunta_faltante.get("ingrediente_a_cotar") or pergunta_faltante.get("confirmacoes_agrupadas")):
                linhas.append(pergunta_faltante["texto"])
            else:
                linhas.append("Esta ficha ainda é um rascunho. " + (primeiro if primeiro.rstrip().endswith("?") else
                              "A próxima pendência é: " + primeiro.rstrip(". ") + ". Vamos resolver só esse ponto antes de aprovar?"))
        cotacao_pendente = pergunta_faltante and pergunta_faltante.get("ingrediente_a_cotar")
        prioridade = 20 if (veredito == "viavel" or cotacao_pendente or texto_cotacao
                            or (pergunta_faltante or {}).get("confirmacoes_agrupadas")) else 10
        montagem.registrar_resposta(session_id or "", linhas, substituir=True, prioridade=prioridade)
    pergunta_proxima = None
    humanos = {"tempo_disponivel_pendente", "tempo_da_receita_pendente", "rendimento_pendente", "fogao_capacidade_pendente", "energia_a_confirmar", "gas_a_confirmar",
               "refrigeracao_a_confirmar", "equipamento_nao_informado", "tecnica_nao_informada", "tecnica_parcial", "ingrediente_pendente"}
    if (prato_id is not None and not proposta_equivalente and not (fidelidade or {}).get("bloqueios")
            and aval["bloqueios"] and aval["bloqueios"][0]["tipo"] in humanos):
        from . import jornada

        primeira = aval["bloqueios"][0]
        pergunta_proxima = jornada.pergunta_pendente({"id": prato_id, "nome": nome}, aval["bloqueios"], dados)
        if pergunta_proxima is None and primeira["detalhe"].endswith("?"):
            if primeira["detalhe"] not in dados.get("perguntas_esgotadas", []):
                pergunta_proxima = {"texto": primeira["detalhe"], "opcoes": []}
        if pergunta_proxima and not (fonte_verificada and pergunta_proxima.get("ingrediente_a_cotar")) and (
                not res["pendencias"] or ficha_original or pergunta_proxima.get("ingrediente_a_cotar")):
            linhas = [f"Salvei o rascunho do prato #{prato_id}, **{nome}**. O prato ainda não foi aprovado e não reservei ingredientes."]
            if ficha_original:
                total = len(escolhas.escolhidas(falas_dela[-1].texto, escolhas.lista_da_escolha(falas_dela))) if falas_dela else 0
                if total > 1:
                    linhas.insert(0, f"Salvei os {total} pratos escolhidos separadamente. Vamos conferir uma ficha por vez.")
                linhas.append("Montei esta ficha com os ingredientes e as medidas publicadas na fonte escolhida.")
            if fonte_verificada:
                linhas.append(f"Fonte: [{fonte_verificada['nome']}]({fonte_verificada['url']}).")
            pendentes = [str(i.get("ingrediente")) for i in res["itens"] if i.get("pendencia")]
            if pendentes:
                linhas.append("Ingredientes ou medidas a conferir: " + ", ".join(pendentes[:3]) + ". Vamos resolver um ponto por vez.")
            if dados["ingredientes_faltantes"]:
                linhas.append("Observações salvas na ficha: " + " ".join(a["observacao"] for a in dados["ingredientes_faltantes"]))
            if texto_cotacao:
                linhas.append(texto_cotacao)
            linhas.append(pergunta_proxima["texto"])
            texto_conferencia = "\n\n".join(linhas)
            aviso = {"alternativa_no_estoque": None, "diga_a_dona_maria": ""}
            montagem.registrar_resposta(session_id or "", linhas, substituir=True, prioridade=20)
    if texto_cotacao and not fonte_verificada and veredito == "viavel":
        montagem.registrar_resposta(session_id or "", [texto_conferencia,
                                    f"Você gosta dessa versão e aceita preparar o prato #{prato_id} sem impedimentos?"],
                                    substituir=True, prioridade=20)
    if session_id and prato_id is not None and veredito == "viavel" and not (proposta_equivalente or cotacao):
        if avancou := _concluir_escolha_viavel(store, session_id, prato_id):
            proximo_passo = avancou
    return {
        "success": True,
        "receita_id": receita_id,
        "receita": nome,
        "prato_id": prato_id,
        "compras_para_confirmar": compras.mostrar(compras.da_conferencia(res)),
        "porcoes": porcoes,
        "veredito": veredito,
        "rascunho_salvo": prato_id is not None,
        "ingredientes_faltantes": dados["ingredientes_faltantes"],
        "ficha_original_da_fonte": ficha_original,
        "confirmacoes_recusadas": confirmacoes_recusadas,
        "pergunta_proxima": pergunta_proxima,
        "bloqueios": aval["bloqueios"],
        "tempo_preparo_min": requisitos["tempo_preparo_min"],
        "tempo_receita_min": tempo,
        "tempo_preparos_caseiros_min": requisitos.get("tempo_preparos_caseiros_min", 0),
        "preparos_caseiros": res["preparos_caseiros"],
        "tempo_preparo_origem": tempo_origem,
        "tempo_preparo_base": tempo_base,
        "fonte_verificada": fonte_verificada,
        "fidelidade_fonte": fidelidade,
        "versao": dados["versao"],
        "ingredientes": res["itens"],
        "resumo": {
            "ja_tem_tudo_que_precisa": ja_tem,
            "tem_mas_nao_o_suficiente": parcial,
            "nao_tem": nao_tem,
            "precisa_comprar": res["compras"],
            "compras_total_estimado": res["compras_total_estimado"],
            "orcamento": orc,
            "cmv_previa": None if res["pendencias"] or (fidelidade or {}).get("bloqueios") else {
                "total": res["cmv_total"], "por_porcao": costing.arredondar_moeda(costing.custo_por_porcao(res["cmv_total"], porcoes)) if porcoes else None},
        },
        "equipamentos": equip,
        "tecnicas": tec,
        "avisos_da_cozinha": da_cozinha["mensagens"],
        "avisos_operacionais": avisos,
        "perfil_pendente": pend_perfil,
        "pendencias": res["pendencias"],
        "estimativas": res["estimativas"],
        "proximos_passos": proximos,
        "proximo_passo": proximo_passo,
        "diga_a_dona_maria": texto_conferencia + (" " + aviso["diga_a_dona_maria"] if aviso and aviso["diga_a_dona_maria"] else ""),
        **({"proposta_equivalente": proposta_equivalente} if proposta_equivalente else {}),
        **({"alternativa_no_estoque": aviso["alternativa_no_estoque"]} if aviso else {}),
    }


# ----------------------------------------------------------------------------- 2.4 CMV e preço
@_tool
def prato_calcular_cmv(args: dict, session_id: Optional[str] = None) -> dict:
    store = Store()
    receita_id = args.get("receita_id")
    prato_id = args.get("prato_id")
    if receita_id is None and prato_id is None:
        candidatas = _fichas_em_preparo_da_sessao(store, session_id)
        if len(candidatas) == 1:
            prato_id, receita_id = candidatas[0][0]["id"], candidatas[0][1]["id"]
        elif len(candidatas) > 1:
            return _recusa("há mais de uma ficha conferida nesta sessão; confirme qual prato deve ser calculado", {
                "bloqueios": [_bloqueio("ficha_ambigua", "o custo não foi calculado porque há mais de um prato em preparação")],
                "proximos_passos": ["confirmar qual prato ela quer continuar e usar o receita_id correspondente"]},
                opcoes=[{"prato_id": p["id"], "receita_id": r["id"], "nome": p["nome"]} for p, r in candidatas])
    if receita_id is None and prato_id is not None:
        escolhido = store.obter_prato(int(prato_id))
        receita_id = escolhido.get("receita_id") if escolhido else None
    ingredientes = args.get("ingredientes")
    nome = str(args.get("nome") or "").strip()
    porcoes = _num(args.get("porcoes"))
    receita = store.obter_receita(int(receita_id)) if receita_id else None
    if receita_id and not receita:
        raise KeyError(f"receita_id {receita_id} não encontrada")
    vinculado = receita["dados"].get("prato_id") if receita else None
    if prato_id is not None or vinculado is not None:
        prato_id = int(prato_id if prato_id is not None else vinculado)
        escolhido = store.obter_prato(prato_id)
        if not receita or not escolhido or escolhido.get("receita_id") != int(receita_id):
            raise ValueError("use a conferência vinculada a este prato escolhido, ou revise a ficha com receita_conferir_ingredientes")
        if nome and norm_text(nome) != norm_text(escolhido["nome"]):
            raise ValueError(f"use o nome do prato escolhido {escolhido['nome']!r}")
        if ingredientes is not None and ingredientes != receita["dados"]["ingredientes"]:
            raise ValueError("a ficha deste prato deve ser revisada antes de recalcular o custo")
        if porcoes and receita["porcoes"] and porcoes != receita["porcoes"]:
            raise ValueError("redimensione os ingredientes e refaça a conferência antes de alterar as porções")
        nome = escolhido["nome"]
    if receita and not ingredientes:
        ingredientes = receita["dados"].get("ingredientes") or []
        nome = nome or receita["nome"]
        porcoes = porcoes or receita.get("porcoes")
    if not isinstance(ingredientes, list) or not ingredientes:
        raise ValueError("informe 'ingredientes' (lista) ou 'receita_id' de uma receita já conferida")
    if not porcoes or not math.isfinite(porcoes) or porcoes <= 0:
        raise ValueError("informe 'porcoes' (quantas porções a receita rende) para calcular o CMV por porção")
    if receita is None:
        return _recusa(
            "CMV só é calculado para receita conferida: chame receita_conferir_ingredientes (com equipamentos_necessarios e "
            "tecnicas_necessarias) e passe o receita_id devolvido",
            {"bloqueios": [_bloqueio("sem_receita_conferida", "o prato não está ligado a uma receita conferida; sem isso não dá para checar equipamentos e técnicas")],
             "proximos_passos": ["chamar receita_conferir_ingredientes e depois prato_calcular_cmv com receita_id"]})
    if prato_id is not None:
        store = Store(ignorar_prato_id=int(prato_id))
    # Trava: reavalia contra o estado atual. Só 'viavel' libera o CMV ("nunca pule a etapa 2 para chegar ao preço").
    aval = _avaliar_prato(store, ingredientes, receita)
    if aval["veredito"] != "viavel":
        return _recusa(
            f"CMV bloqueado: veredito {aval['veredito']} com {len(aval['bloqueios'])} pendência(s); resolva os bloqueios e chame de novo com o mesmo receita_id",
            aval, receita_id=int(receita_id), **_aviso_prato_nao_aprovado(store, receita, aval))
    res = aval["resultado"]
    cmv_total = res["cmv_total"]
    cmv_porcao = costing.arredondar_moeda(costing.custo_por_porcao(cmv_total, porcoes))
    orc = aval["orcamento"]
    memoria = [f"{l['ingrediente']}: {l['memoria_de_calculo']}" for l in res["itens"] if l.get("memoria_de_calculo")]
    memoria.append(f"CMV da receita = soma = {costing.brl(cmv_total)}")
    memoria.append(f"CMV por porção = {costing.brl(cmv_total)} ÷ {porcoes:g} porções = {costing.brl(cmv_porcao)}")
    dados = {"ingredientes": ingredientes, "resultado": res, "memoria_de_calculo": memoria,
             "ingredientes_faltantes": Store.anotacoes_faltantes(res, ingredientes, receita["dados"].get("ingredientes_faltantes") or [], receita["dados"].get("adaptacao")),
             "avaliacao": {k: aval[k] for k in ("veredito", "equipamentos", "tecnicas", "orcamento", "requisitos")}}
    prato_id = store.salvar_prato(nome or "Prato sem nome", int(receita_id), porcoes, cmv_total, cmv_porcao, dados, prato_id=prato_id)
    return {
        "success": True,
        "prato_id": prato_id,
        "prato": nome,
        "porcoes": porcoes,
        "cmv_total": cmv_total,
        "cmv_por_porcao": cmv_porcao,
        "custo_vindo_da_despensa": res["custo_vindo_da_despensa"],
        "custo_vindo_de_compras": res["custo_vindo_de_compras"],
        "compras_complementares": res["compras"],
        "compras_total_estimado": res["compras_total_estimado"],
        "diga_a_dona_maria": (f"Custo dos ingredientes usados no prato: {costing.brl(cmv_total)} para {porcoes:g} porções "
                             f"({costing.brl(cmv_porcao)} por porção). Na despensa: {costing.brl(res['custo_vindo_da_despensa'])} "
                             f"dos ingredientes usados. Compras complementares previstas: {costing.brl(res['compras_total_estimado'])}. "
                             f"Saldo atual para compras: {costing.brl(orc['saldo_atual'])}."),
        "orcamento": orc,
        "memoria_de_calculo": memoria,
        "formula": "CMV = Σ (quantidade usada × custo unitário); custo unitário = preço total pago ÷ quantidade comprada (abas Precos × Despensa)",
        "preco_minimo_por_porcao": costing.preco_minimo_centavo(costing.custo_por_porcao(cmv_total, porcoes), perfil=store.perfil_precificacao()),
        "pendencias": res["pendencias"],
        "estimativas": res["estimativas"],
        "itens": res["itens"],
        "veredito": aval["veredito"],
        "avisos_operacionais": aval["avisos_operacionais"],
        "reservas_previstas": _reservas_de(res, legivel=True),
        "proximo_passo": (f"Se a versão estiver aceita, chame preco_sugerir com prato_id={prato_id} para reservar o estoque, "
                          "montar o carrinho e salvar 2–3 cenários. Registre no cardápio quando ela escolher o preço."),
    }


def _pediu_para_usar_versao(prato: dict[str, Any], receita: dict[str, Any] | None) -> bool:
    selecao = prato["dados"].get("selecao") or {}
    dados_receita = (receita or {}).get("dados") or {}
    publicada = (dados_receita.get("fonte_verificada") or {}).get("url")
    resultado = dados_receita.get("resultado") or {}
    return bool(selecao.get("usar_quando_viavel") is True and publicada
                and selecao.get("fonte_url") == publicada == receita.get("fonte_url")
                and not fonte.normalizar_adaptacao(dados_receita.get("adaptacao"))
                and not dados_receita.get("medidas_de_uso_confirmadas")
                and not dados_receita.get("medidas_de_uso_estimadas")
                and not resultado.get("preparos_caseiros") and not resultado.get("compras")
                and not (prato["dados"].get("resultado") or {}).get("preparos_caseiros"))


@_tool
def preco_sugerir(args: dict, session_id: Optional[str] = None, *, usar_escolha: bool = False) -> dict:
    prato_id = args.get("prato_id")
    if not prato_id:
        return _recusa(
            "preço só é sugerido para prato com CMV calculado pela tool: chame prato_calcular_cmv e passe o prato_id",
            {"bloqueios": [_bloqueio("sem_cmv_calculado", "cmv_por_porcao avulso não é aceito: o CMV precisa vir de prato_calcular_cmv (conta de cabeça não vale)")],
             "proximos_passos": ["chamar prato_calcular_cmv (com receita_id) e depois preco_sugerir com prato_id"]})
    store = Store(ignorar_prato_id=int(prato_id))
    prato = store.obter_prato(int(prato_id))
    if not prato:
        raise KeyError(f"prato_id {prato_id} não encontrado")
    if prato["status"] == "removido":
        return _recusa("prato removido do cardápio; se ela quiser retomá-lo, calcule de novo com prato_calcular_cmv",
                       {"bloqueios": [_bloqueio("prato_removido", f"prato {prato_id} foi removido")], "proximos_passos": ["chamar prato_calcular_cmv de novo"]})
    if prato["status"] == "selecionado":
        return _recusa("o prato escolhido ainda não tem custo calculado", {
            "bloqueios": [_bloqueio("sem_cmv_calculado", "monte a ficha e calcule o custo antes de sugerir o preço")],
            "proximos_passos": ["usar receita_conferir_ingredientes com prato_id e depois prato_calcular_cmv"]})
    cmv = precificacao.custo_por_porcao(prato)
    ficha = precificacao.ficha_atual(store, prato)
    if not isinstance(ficha, str):
        porcoes_atual, resultado_atual = ficha
        cmv = costing.custo_por_porcao(resultado_atual["cmv_total"], porcoes_atual)
    avisos: list[str] = []
    informado = _num(args.get("cmv_por_porcao"))
    if informado is not None and abs(informado - costing.arredondar_moeda(cmv)) > 0.005:
        avisos.append(f"cmv_por_porcao informado ({costing.brl(informado)}) difere do calculado pela tool ({costing.brl(cmv)}); os cenários usam o calculado")
    perfil = store.perfil_precificacao()
    comissao, pagamento, _ = taxas.componentes(perfil)
    taxa = round(comissao + pagamento, 12)
    if _num(args.get("taxa_plataforma"), taxa) not in (taxa, taxa * 100):
        avisos.append("A avaliação usa a taxa fixa de 10% do enunciado; mantive essa regra.")
    passo = _num(args.get("arredondar_para"), 0.5)
    if not math.isfinite(passo) or passo < 0.01 or round(passo, 2) != passo:
        raise ValueError("arredondar_para deve ser um valor positivo em centavos inteiros")

    falas_dela = falas.da_sessao(session_id) or []
    aceite = args.get("aceite_prato")
    receita_do_aceite = store.obter_receita(prato["receita_id"]) if prato.get("receita_id") else None
    confirmado = (usar_escolha and _pediu_para_usar_versao(prato, receita_do_aceite)
                  and not (not isinstance(ficha, str) and resultado_atual.get("preparos_caseiros"))
                  or precificacao.confirmou_prato(aceite, prato["nome"], falas_dela, prato_id=int(prato_id)))
    if (receita_do_aceite and receita_do_aceite["dados"].get("adaptacao_proposta_no_contexto") == _assinatura_falas(falas_dela)):
        confirmado = False  # a nova versão ainda não foi apresentada quando esta fala foi enviada
    if aceite is not None and not confirmado:
        return _recusa(
            "para salvar a análise, falta a confirmação desta versão do prato",
            {"bloqueios": [_bloqueio("sem_aceite_do_prato", "faltou uma resposta afirmativa sobre este prato")],
             "proximos_passos": [f"pergunte 'Você aceita preparar o prato #{prato_id}, {prato['nome']}, nessa versão?'"]})
    if not confirmado:
        # Chamadas antigas ou simulações explícitas continuam disponíveis e não povoam o Analytics.
        margens_avulsas = args.get("margens")
        margens = None
        if isinstance(margens_avulsas, list) and margens_avulsas:
            margens = [m / 100.0 if m > 1 else m for m in (_num(x, 0.0) or 0.0 for x in margens_avulsas)]
        saida = costing.cenarios_preco(cmv, margens=margens, nomes=args.get("nomes"), arredondar_para=passo, perfil=perfil)
    else:
        if not math.isfinite(cmv) or cmv <= 0:
            raise ValueError("confira os custos dos ingredientes para obter um CMV por porção maior que zero")
        if any(k in args for k in ("margens", "nomes", "cenario_recomendado", "recomendacao")):
            margens, nomes, recomendado, recomendacao = precificacao.cenarios_da_consultora(args)
        else:
            margens, nomes = costing.MARGENS_PADRAO, costing.NOMES_PADRAO
            recomendado, recomendacao = 2, "O cenário intermediário equilibra preço de venda e sobra depois dos custos considerados."
        lucro_alvo = precificacao.valor_da_meta(args, "lucro_alvo")
        custos_fixos = precificacao.valor_da_meta(args, "custos_fixos")

        receita = store.obter_receita(prato["receita_id"]) if prato.get("receita_id") else None
        if receita is None:
            raise ValueError("o prato precisa de uma receita conferida")
        resultado_anterior = prato["dados"]["resultado"]
        aval = _avaliar_prato(store, prato["dados"]["ingredientes"], receita)
        if aval["veredito"] != "viavel":
            if aval["veredito"] == "inviavel_por_enquanto":
                store.liberar_compromisso_inviavel(int(prato_id), receita["id"])
            return _recusa("resolva as pendências da receita antes de montar os cenários", aval)
        resultado = aval["resultado"]
        if any(resultado[k] != resultado_anterior[k] for k in ("cmv_total", "compras_total_estimado")):
            return _recusa(
                "os custos mudaram desde que o CMV foi calculado",
                {"bloqueios": [_bloqueio("numeros_mudaram", "calcule o CMV novamente para salvar cenários atualizados")],
                 "proximos_passos": [f"chamar prato_calcular_cmv com receita_id={receita['id']}"]})

        saida = costing.cenarios_preco(cmv, margens=margens, nomes=nomes, arredondar_para=passo, perfil=perfil)
        if len({c["preco_sugerido"] for c in saida["cenarios"]}) != len(margens):
            saida = costing.cenarios_preco(cmv, margens=margens, nomes=nomes, arredondar_para=0.01, perfil=perfil)
        if len({c["preco_sugerido"] for c in saida["cenarios"]}) != len(margens):
            raise ValueError("escolha margens mais distantes para oferecer preços diferentes")
        if lucro_alvo is not None or custos_fixos is not None:
            meta = (lucro_alvo or 0) + (custos_fixos or 0)
            for c in saida["cenarios"]:
                c["porcoes_para_meta"] = precificacao.porcoes_para_meta(cmv, c["preco_sugerido"], meta, perfil=perfil)
        saida.update({
            "cmv_estimado": bool(resultado["compras"] or resultado["estimativas"]),
            "arredondar_para": passo,
            "cenario_recomendado": recomendado,
            "recomendacao": recomendacao,
            "lucro_alvo": lucro_alvo,
            "custos_fixos": custos_fixos,
        })
        try:
            store.salvar_analise_preco(int(prato_id), saida, receita_id_esperada=receita["id"],
                                      compras=resultado["compras"], da_despensa=_itens_da_despensa_para_carrinho(resultado),
                                      reservas=_reservas_de(resultado), resultado=resultado)
        except OrcamentoEstourado as exc:
            return _recusa_saldo(exc, Store())
        if prato["status"] == "cmv_calculado":
            saida["carrinho_atualizado"] = True
            saida["orcamento"] = Store().carrinho_resumo()
    simulacoes = args.get("precos_para_simular")
    if isinstance(simulacoes, list) and simulacoes:
        saida["simulacoes"] = costing.simular_precos(cmv, [p for p in (_num(x) for x in simulacoes) if p], perfil=perfil)
    saida.update({
        "success": True,
        "prato_id": int(prato_id),
        "prato": prato["nome"],
        "analise_salva": confirmado,
        "avisos": avisos,
        "orientacao": (f"Comece pelo mínimo de {costing.brl(saida['preco_minimo'])} por porção, que cobre os custos considerados. "
                       + "Explique o repasse 0,90 × preço e o lucro 0,90 × preço − CMV, com taxa fixa de 10% do enunciado. "
                       + "Apresente os valores calculados dos cenários. Quando houver volume para uma meta, diga que ele inclui os custos fixos informados "
                       "e depende de vender as porções. "
                       + ("Diga que a análise foi salva em Analytics. " if confirmado else "")
                       + ("Confirme que o lote foi gravado, os insumos saíram do estoque disponível e as despesas estão atualizadas. "
                          if saida.get("carrinho_atualizado") else "")
                       + "Pergunte qual preço ela quer adotar; só depois registre com cardapio_registrar."),
    })
    if confirmado and session_id:
        # A LLM pode refazer as margens após o hook inicial deste mesmo turno.
        # O chat deve apresentar a última análise efetivamente salva.
        _mostrar_analise(session_id, prato, saida)
    return saida


def _escolha_de_preco(store: Store, fala: falas.Fala) -> tuple[float | None, list[dict[str, Any]]]:
    preco = escolhas.preco_para_registrar(fala.texto, fala.resposta_a)
    if preco is None:
        return None, []
    return preco, [prato for prato in store.pratos_em_andamento() if escolhas.pediu_registro_do_prato(
        fala.texto, fala.resposta_a, prato["id"], prato["nome"])]


def _preco_curto_disponivel(store: Store, fala: falas.Fala, prato: dict[str, Any], preco: float) -> bool:
    if escolhas.preco_para_registrar(fala.texto) is not None:
        return True
    if prato["status"] == "aceito":
        return prato["preco_venda"] == preco  # repetição do mesmo turno ou da mesma confirmação
    return prato["status"] == "cmv_calculado" and any(a["prato_id"] == prato["id"] for a in store.analises_preco())


@_tool
def cardapio_registrar(args: dict, session_id: Optional[str] = None) -> dict:
    prato_id = args.get("prato_id")
    preco = _num(args.get("preco_escolhido"))
    if not prato_id or preco is None:
        raise ValueError("informe 'prato_id' e 'preco_escolhido'")
    if not math.isfinite(preco) or preco <= 0 or round(preco, 2) != preco:
        raise ValueError("'preco_escolhido' precisa ser positivo, com até duas casas decimais")
    prato_id = int(prato_id)
    # A reavaliação ignora reservas/compras deste mesmo prato: repetir a chamada (retry, troca de preço) não duplica nada.
    store = Store(ignorar_prato_id=prato_id)
    prato = store.obter_prato(prato_id)
    if not prato:
        raise KeyError(f"prato_id {prato_id} não encontrado")
    if prato["status"] == "removido":
        return _recusa("prato foi removido do cardápio; se ela quiser retomá-lo, calcule de novo com prato_calcular_cmv",
                       {"bloqueios": [_bloqueio("prato_removido", f"prato {prato_id} foi removido")],
                        "proximos_passos": ["chamar prato_calcular_cmv de novo e apresentar os preços outra vez"]})
    receita = store.obter_receita(int(prato["receita_id"])) if prato.get("receita_id") else None
    if receita is None or prato["status"] == "selecionado":
        bloqueios = (receita or {}).get("dados", {}).get("bloqueios") or [
            _bloqueio("sem_ficha_final", "falta concluir a conferência e calcular o custo do prato")]
        pendencias = {"bloqueios": bloqueios, "proximos_passos": ["retomar a ficha do prato salvo e resolver as pendências"]}
        return _recusa("o prato escolhido ainda não está aprovado", pendencias,
                       **_aviso_prato_nao_aprovado(store, receita or prato["nome"], pendencias))
    falas_dela = (falas.da_sessao(session_id) or []) if session_id else []
    fala = falas_dela[-1] if falas_dela else None
    if session_id and "selecao" in prato["dados"]:
        escolhido, alvos = _escolha_de_preco(store, fala) if fala else (None, [])
        if (not fala or escolhido != preco or [p["id"] for p in alvos] != [prato_id]
                or not _preco_curto_disponivel(store, fala, prato, preco)):
            return _recusa("o preço de venda ainda não foi confirmado para este prato", {
                "bloqueios": [_bloqueio("sem_escolha_de_preco", "aguarde um valor único em resposta à pergunta sobre o registro deste prato")],
                "proximos_passos": [f"mostre os cenários e pergunte qual preço ela escolhe para registrar o prato #{prato_id}"]})
    ingredientes = prato["dados"].get("ingredientes") or []
    # Trava 1: o estado ATUAL (perfil, despensa disponível, orçamento) precisa estar 'viavel'. O que o modelo
    # escreveu na conversa não conta; só o que foi salvo com perfil_cozinha_salvar / despensa_atualizar.
    aval = _avaliar_prato(store, ingredientes, receita)
    if aval["veredito"] != "viavel":
        if aval["veredito"] == "inviavel_por_enquanto":
            store.liberar_compromisso_inviavel(prato_id, receita["id"])
        return _recusa(f"registro bloqueado: veredito {aval['veredito']}; o cardápio só aceita prato sem pendência, "
                       "mesmo que a conversa diga que está tudo certo", aval, **_aviso_prato_nao_aprovado(store, receita, aval))
    res = aval["resultado"]
    porcoes = float(prato.get("porcoes") or 0)
    cmv_porcao = costing.arredondar_moeda(costing.custo_por_porcao(res["cmv_total"], porcoes)) if porcoes else None
    # Trava 2: o preço foi escolhido em cima de um CMV e de uma lista de compras apresentados. Se mudaram
    # (outro prato reservou estoque, despensa ou orçamento alterados), a escolha anterior não vale.
    res_antes = prato["dados"].get("resultado") or {}
    cmv_antes = float(prato.get("cmv_porcao") or 0)
    compras_antes = float(res_antes.get("compras_total_estimado") or 0)
    perfil_precificacao = store.perfil_precificacao()
    referencia = re.search(r"\bCotação ([a-f0-9]{12})\b", fala.texto, re.IGNORECASE) if fala else None
    versao_atual = precificacao.cotacao(prato, porcoes, res, perfil_precificacao)
    custos_mudaram = res["cmv_total"] != prato["cmv_total"] or abs(compras_antes - float(res["compras_total_estimado"] or 0)) > 0.005
    if (referencia and referencia[1].lower() != versao_atual) or (custos_mudaram and not referencia):
        return _recusa(
            "os números mudaram desde que o CMV foi apresentado (outro prato reservou estoque, ou despensa/orçamento mudaram); "
            "o preço escolhido não vale para a versão nova",
            {"veredito": "precisa_confirmar",
             "bloqueios": [_bloqueio("numeros_mudaram", f"CMV/porção {costing.brl(cmv_antes)} → {costing.brl(float(cmv_porcao or 0))}; "
                                                        f"compras {costing.brl(compras_antes)} → {costing.brl(res['compras_total_estimado'])}")],
             "proximos_passos": ["apresente o CMV e os cenários atualizados no chat e peça a nova escolha do preço"]},
            antes={"cmv_por_porcao": cmv_antes, "compras_total": compras_antes},
            agora={"cmv_por_porcao": cmv_porcao, "compras_total": res["compras_total_estimado"]})
    ja_registrado = prato["status"] == "aceito"
    dados = {**prato["dados"], "resultado": res,
             "avaliacao": {k: aval[k] for k in ("veredito", "equipamentos", "tecnicas", "orcamento", "requisitos")},
             "registro": {"preco_escolhido": preco, "porcoes": porcoes, "cmv_por_porcao": cmv_porcao}}
    try:
        confirmado = store.aceitar_prato(prato_id, preco, res["compras"], _reservas_de(res),
                                         confirmar_compras=bool(args.get("confirmar_compras", True)), dados=dados,
                                         cmv_total=res["cmv_total"], cmv_porcao=cmv_porcao,
                                         receita_id_esperada=receita["id"])
    except OrcamentoEstourado as exc:
        return _recusa_saldo(exc, Store())
    # As compras da receita agora são compromisso do prato: saem do carrinho para o mesmo gasto não contar duas vezes.
    baixados = confirmado.get("linhas_do_carrinho_que_viraram_compras", 0)
    cmv_exato = precificacao.custo_por_porcao(confirmado)
    avaliacao = costing.avaliar_preco(preco, cmv_exato, perfil=perfil_precificacao)
    alertas: list[str] = []
    if not avaliacao["viavel"]:
        minimo = costing.preco_minimo_centavo(cmv_exato, perfil=perfil_precificacao)
        alertas.append(f"O preço escolhido ({costing.brl(preco)}) fica abaixo do mínimo ({costing.brl(minimo)}), "
                       f"com perda projetada de {costing.brl(-avaliacao['lucro'])} por porção.")
    final = Store()  # visão completa (sem ignorar nada) para orçamento e reservas
    return {
        "success": True,
        "ja_registrado": ja_registrado,
        "prato": {"id": confirmado["id"], "nome": confirmado["nome"], "porcoes": confirmado["porcoes"], "cmv_por_porcao": confirmado["cmv_porcao"],
                  "preco_venda": confirmado["preco_venda"], "status": confirmado["status"]},
        "resultado_por_porcao": avaliacao,
        "compras_registradas": res["compras"],
        "reservas_registradas": [{k: r[k] for k in ("ingrediente", "quantidade")} for r in final.reservas_do_prato(prato_id)],
        "orcamento": final.orcamento(),
        "linhas_do_carrinho_que_viraram_compras": baixados,
        "diga_a_dona_maria": _texto_saldo(final.carrinho_resumo()),
        "alertas": alertas,
        "proximo_passo": "Confirme o preço e o saldo. Se houver complementos, acompanhe a compra e registre somente quando ela confirmar os itens e valores pagos; se não, ofereça planejar o próximo prato com o estoque disponível.",
        "nota": ("Estoque reservado. Se ela fizer as compras complementares deste prato, registre os valores pagos com "
                 "despensa_registrar_compras e o compra_id correspondente." if res["compras"] else
                 "Estoque reservado. Este prato não exige compras complementares e o saldo de compras não mudou."),
    }


def _pergunta_para_registrar() -> str:
    return "Qual preço por porção você escolhe para registrar esse prato no cardápio? Pode responder só com o valor em reais."


def _mostrar_analise(session_id: str, prato: dict[str, Any], analise: dict[str, Any], *, recomendar: bool = False) -> str:
    opcoes = analise["cenarios"]
    indice = analise.get("cenario_recomendado")
    recomendado = opcoes[indice - 1] if type(indice) is int and 1 <= indice <= len(opcoes) else None
    identificacao = f"prato #{prato['id']}, **{prato['nome']}**"
    linhas = [f"Calculei os cenários do {identificacao}. Vamos escolher juntos o preço; a análise também ficou salva em Analytics."]
    store = Store()
    prato = store.obter_prato(prato["id"]) or prato
    ficha = precificacao.ficha_atual(store, prato)
    if not isinstance(ficha, str):
        porcoes, resultado = ficha
        itens = resultado["itens"]
        exemplo = next((store.obter(i["item_despensa"]) for i in itens if i.get("item_despensa")), None)
        explicacao = "O custo unitário vem do preço total pago ÷ quantidade comprada das abas Precos e Despensa."
        if exemplo and exemplo.get("comprado_base") and exemplo.get("custo_unitario_base") is not None:
            explicacao += (f" Ex.: {exemplo['nome']}: {costing.brl(exemplo['preco_total'])} ÷ "
                           f"{format_base(exemplo['comprado_base'], exemplo['unidade_base'])} = "
                           f"{costing.brl(exemplo['custo_unitario_base'])}/{exemplo['unidade_base']}.")
        linhas.extend([explicacao, "\n".join(
            f"- {i['ingrediente']}: {i['memoria_de_calculo']}" for i in itens if i.get("memoria_de_calculo"))])
        linhas.append(f"**CMV do lote: {costing.brl(resultado['cmv_total'])}** ÷ {porcoes:g} porções = "
                      f"**{costing.brl(analise['cmv_por_porcao'])} por porção**. "
                      f"Compras complementares previstas: **{costing.brl(resultado['compras_total_estimado'])}**. "
                      "O que já está na despensa entra no custo do prato.")
        if resultado.get("estimativas"):
            linhas.append("Estimativas: " + "; ".join(resultado["estimativas"]))
    if analise.get("carrinho_atualizado"):
        contas = analise.get("orcamento") or store.carrinho_resumo()
        linhas.insert(1, "Registrei o lote no histórico e retirei suas quantidades do estoque disponível. " + _texto_despesas(contas))
    linhas.append(f"A taxa do desafio é **10%**: você recebe **0,90 × preço**. "
                  f"Mínimo para cobrir os ingredientes: {costing.brl(analise['cmv_por_porcao'])} ÷ 0,90 = "
                  f"**{costing.brl(analise['preco_minimo'])}**, arredondado para cima ao centavo. "
                  "Os valores exibidos estão em centavos; as contas usam o CMV por porção antes do arredondamento.")
    linhas.append("\n".join(
        f"- **{c['nome']}: {costing.brl(c['preco_sugerido'])}**. "
        f"Recebe 0,90 × {costing.brl(c['preco_sugerido'])} ≈ {costing.brl(c['com_preco_sugerido']['recebe_liquido'])}; "
        f"menos CMV {costing.brl(analise['cmv_por_porcao'])} ≈ **lucro {costing.brl(c['com_preco_sugerido']['lucro'])} por porção**."
        for c in opcoes))
    if recomendar and recomendado:
        linhas.append(f"Sugiro **{costing.brl(recomendado['preco_sugerido'])} por porção**. "
                      + (analise.get("recomendacao") or "Você decide qual cenário adotar."))
    linhas.append(_pergunta_para_registrar())
    montagem.registrar_resposta(session_id, linhas, substituir=True, prioridade=30)
    return "\n\n".join(linha for linha in linhas if linha)


_PEDIU_RECOMENDACAO = re.compile(r"^(?:(?:escolha|decida|recomende|sugira) (?:o preco|um preco|por mim|pra mim|para mim)\b|"
                                 r"(?:faz|faca|escolhe|monta) (?:pra|para) mim\b|qual preco voce recomenda\b)")


def avancar_cardapio_no_turno(store: Store, session_id: str, falas_do_turno: list[falas.Fala], *,
                              prato_em_conferencia: int | None = None) -> Optional[str]:
    """Usa o aceite ou o preço da própria usuária para avançar a ficha já gravada."""
    if not falas_do_turno:
        return None
    fala = falas_do_turno[-1]
    pratos = store.pratos_em_andamento()
    preco, alvos = _escolha_de_preco(store, fala)
    if preco is not None:
        if len(alvos) != 1:
            return "Ela informou um preço para registro. Pergunte qual prato deve receber esse preço."
        prato = alvos[0]
        if not _preco_curto_disponivel(store, fala, prato, preco):
            return "Para registrar esse preço, confirme qual ficha aprovada do prato deve receber o valor."
        registro = json.loads(cardapio_registrar({"prato_id": prato["id"], "preco_escolhido": preco}, session_id=session_id))
        if registro["success"]:
            estimativa = registro["resultado_por_porcao"]
            linhas = [f"Registrei o prato #{prato['id']}, **{prato['nome']}**, a **{costing.brl(preco)} por porção**.",
                      f"Recebe 0,90 × {costing.brl(preco)} ≈ {costing.brl(estimativa['recebe_liquido'])}; "
                      f"menos o CMV, sobram **{costing.brl(estimativa['lucro'])} por porção**, com taxa de 10% do desafio.",
                      _texto_despesas(registro["orcamento"])]
            if registro["compras_registradas"]:
                linhas.append("Os complementos estão previstos, ainda não são uma compra paga. Quando comprar, me conte os itens e os valores para eu dar entrada na despensa.")
            from . import jornada

            proximos = [p for p in jornada.fichas_em_uso(store.pratos_em_andamento(), jornada.controle(store)) if p["status"] != "aceito"]
            if proximos:
                linhas.append(f"Vamos conferir agora o próximo prato escolhido: **{proximos[0]['nome']}**. Vou trazer só a próxima decisão dessa ficha.")
            else:
                linhas.append("Seu cardápio está pronto para vender. " + jornada.PERGUNTA_DIVULGACAO)
            if registro["alertas"]:
                linhas.extend(registro["alertas"])
            montagem.registrar_resposta(session_id, linhas, substituir=True, prioridade=40)
            return "Registro concluído no cardápio. Mostre exatamente este resultado:\n" + "\n".join(linhas)
        return "O registro ficou pendente. Explique este resultado: " + _motivo_pendente(registro)

    if _PEDIU_RECOMENDACAO.search(norm_text(fala.texto)):
        alvos = [p for p in pratos if escolhas.perguntou_preco_do_prato(fala.resposta_a, p["id"], p["nome"])]
        if len(alvos) == 1 and alvos[0]["status"] == "cmv_calculado":
            prato = alvos[0]
            analise = next((a for a in precificacao.consultar(store)["analises"] if a["prato_id"] == prato["id"]), None)
            if analise:
                return "Apresente os valores da análise salva para ela escolher.\n" + _mostrar_analise(session_id, prato, analise, recomendar=True)

    alvos = [p for p in pratos if p["status"] != "aceito" and p.get("receita_id")
             and precificacao.aceite_da_receita(p["id"], p["nome"], falas_do_turno)]
    if not alvos and prato_em_conferencia is not None:
        return _concluir_escolha_viavel(store, session_id, prato_em_conferencia)
    if len(alvos) != 1:
        return None
    return _preparar_cenarios_do_aceite(store, session_id, alvos[0])


def _concluir_escolha_viavel(store: Store, session_id: str, prato_id: int) -> Optional[str]:
    prato = store.obter_prato(prato_id)
    if not prato or prato["status"] not in ("selecionado", "cmv_calculado") or not prato.get("receita_id"):
        return None
    if prato["status"] == "cmv_calculado" and any(a["prato_id"] == prato_id for a in store.analises_preco()):
        return None
    receita = store.obter_receita(prato["receita_id"])
    if not _pediu_para_usar_versao(prato, receita):
        return None
    resultado = _resolver_ingredientes(store, receita["dados"].get("ingredientes") or [])
    if resultado["preparos_caseiros"] or resultado["compras"]:
        return None
    return _preparar_cenarios_do_aceite(store, session_id, prato, usar_escolha=True)


def _preparar_cenarios_do_aceite(store: Store, session_id: str, prato: dict[str, Any], *,
                               usar_escolha: bool = False) -> Optional[str]:
    if usar_escolha or prato["status"] == "selecionado":
        cmv = json.loads(prato_calcular_cmv({"prato_id": prato["id"]}))
        if not cmv["success"]:
            return None if usar_escolha else "Ela já confirmou a versão. Explique a pendência que falta resolver: " + _motivo_pendente(cmv)
    anterior = next((a for a in store.analises_preco() if a["prato_id"] == prato["id"]), None)
    opcoes: dict[str, Any] = {"prato_id": prato["id"]}
    if anterior:
        dados = anterior["dados"]
        opcoes.update(margens=[c["margem_alvo"] for c in dados["cenarios"]], nomes=[c["nome"] for c in dados["cenarios"]],
                      cenario_recomendado=dados["cenario_recomendado"], recomendacao=dados["recomendacao"],
                      lucro_alvo=dados.get("lucro_alvo"), custos_fixos=dados.get("custos_fixos"))
        if dados.get("arredondar_para") is not None:
            opcoes["arredondar_para"] = dados["arredondar_para"]
    resultado = json.loads(preco_sugerir(opcoes, session_id=session_id, usar_escolha=usar_escolha))
    if not resultado.get("analise_salva"):
        return "Ela já confirmou a versão. Explique a pendência que falta resolver: " + _motivo_pendente(resultado)
    return "Aceite já confirmado. A análise foi salva. Apresente estes valores e a pergunta de escolha:\n" + _mostrar_analise(session_id, prato, resultado)


def _motivo_pendente(resultado: dict[str, Any]) -> str:
    mensagem = resultado.get("diga_a_dona_maria")
    if mensagem:
        return str(mensagem)
    motivos = [b["detalhe"] for b in resultado.get("bloqueios", [])]
    return " ".join(motivos) if motivos else str(resultado.get("error") or "consulte o estado atual do prato")


@_tool
def cardapio_remover(args: dict) -> dict:
    store = Store()
    prato_id = args.get("prato_id")
    if not prato_id:
        raise ValueError("informe 'prato_id'")
    out = store.remover_prato(int(prato_id), motivo=str(args.get("motivo") or "").strip() or None)
    p = out["prato"]
    return {
        "success": True,
        "prato": {"id": p["id"], "nome": p["nome"], "status": p["status"], "status_anterior": (p["dados"].get("remocao") or {}).get("status_anterior")},
        "reservas_liberadas": out["reservas_liberadas"],
        "compras_canceladas": out["compras_canceladas"],
        "orcamento": store.orcamento(),
        "nota": "Compra real lançada com despensa_registrar_compras continua na despensa e no orçamento.",
    }


@_tool
def cardapio_consultar(args: dict) -> dict:
    store = Store()
    pratos = store.pratos()
    perfil_precificacao = store.perfil_precificacao()
    aceitos, em_analise, removidos = [], [], []
    for p in pratos:
        receita = store.obter_receita(p["receita_id"]) if p.get("receita_id") else None
        resumo = {"id": p["id"], "nome": p["nome"], "porcoes": p["porcoes"], "cmv_por_porcao": p["cmv_porcao"], "preco_venda": p["preco_venda"], "status": p["status"],
                  "ingredientes_faltantes": ((receita or {}).get("dados") or {}).get("ingredientes_faltantes") or p["dados"].get("ingredientes_faltantes") or []}
        if p["status"] == "aceito" and p["preco_venda"]:
            ficha = precificacao.ficha_atual(store, p)
            if not isinstance(ficha, str):
                porcoes, resultado = ficha
                avaliacao = costing.avaliar_preco(p["preco_venda"], costing.custo_por_porcao(resultado["cmv_total"], porcoes), perfil=perfil_precificacao)
                resumo["cmv_por_porcao"] = avaliacao["cmv"]
                resumo["lucro_por_porcao"] = avaliacao["lucro"]
                resumo["resultado_por_porcao"] = avaliacao
            resumo["reservas"] = [{k: r[k] for k in ("ingrediente", "quantidade")} for r in store.reservas_do_prato(p["id"])]
            aceitos.append(resumo)
        elif p["status"] == "removido":
            removidos.append(resumo)
        else:
            em_analise.append(resumo)
    perfil = store.perfil()
    reservado = [{"ingrediente": i["nome"], "estoque": i["estoque_legivel"], "reservado": i["reservado_legivel"], "disponivel": i["disponivel_legivel"]}
                 for i in store.ingredientes() if i.get("reservado_base")]
    return {
        "success": True,
        "cardapio": aceitos,
        "pratos_em_analise": em_analise,
        "pratos_removidos": removidos,
        "estoque_reservado": reservado,
        "receitas_conferidas": store.receitas(),
        "orcamento": store.orcamento(),
        "perfil_resumo": {"equipamentos": perfil.get("equipamentos"), "restricoes": perfil.get("restricoes"), "tecnicas": perfil.get("tecnicas")},
        "perfil_pendente": _pendencias_perfil(perfil),
    }


# ----------------------------------------------------------------------------- carrinho e saldo
def _texto_despesas(resumo: dict[str, Any]) -> str:
    return (f"Orçamento **{costing.brl(resumo['inicial'])}** − despesas **{costing.brl(resumo['despesas'])}** "
            f"= saldo **{costing.brl(resumo['saldo_gerencial'])}**. "
            f"Disponível para novas compras **{costing.brl(resumo['saldo'])}**.")


def _texto_saldo(resumo: dict[str, Any]) -> str:
    """Frase pronta para a consultora repetir: o que ela tem, o que já escolheu e o que sobra."""
    partes = [f"orçamento {costing.brl(resumo['inicial'])}", f"despesas {costing.brl(resumo['despesas'])}",
              f"saldo {costing.brl(resumo['saldo_gerencial'])}"]
    if resumo["gasto_confirmado"]:
        partes.append(f"já pago {costing.brl(resumo['gasto_confirmado'])}")
    if resumo["reservado_cardapio"]:
        partes.append(f"reservado para compras de pratos {costing.brl(resumo['reservado_cardapio'])}")
    partes.append(f"no carrinho {costing.brl(resumo['carrinho'])}")
    partes.append(f"disponível para novas compras {costing.brl(resumo['saldo'])}")
    return " · ".join(partes)


def _qtd_texto(quantidade: Any, unidade: Optional[str]) -> str:
    return f"{float(quantidade):g} {unidade or ''}".strip()


def _texto_prato(grupo: dict[str, Any]) -> str:
    """O prato aberto, pronto para a conversa: cada ingrediente com a quantidade e o valor, e o total do prato."""
    cabecalho = f"{grupo['prato']}: {costing.brl(grupo['valor_do_prato'])}"
    if abs(grupo["a_comprar"] - grupo["valor_do_prato"]) > 0.004:
        cabecalho += f" (a comprar {costing.brl(grupo['a_comprar'])})"
    linhas = [f"- {i['nome']} ({_qtd_texto(i['quantidade'], i['unidade'])}): {costing.brl(i['preco_total'])}"
              + (" — já tem na despensa" if i["ja_tem"] else "") for i in grupo["ingredientes"]]
    return "\n".join([cabecalho, *linhas])


def _legivel_base(quantidade: float, base: str) -> tuple[float, str]:
    """Quantidade na unidade-base da despensa (kg, L, un) para algo que se lê: 0,03 kg vira 30 g."""
    if base == "kg" and quantidade < 1:
        return round(quantidade * 1000, 1), "g"
    if base == "L" and quantidade < 1:
        return round(quantidade * 1000, 1), "ml"
    return round(quantidade, 3), base


def _salvos_resumo(store: Store) -> dict[str, Any]:
    s = store.salvos()
    return {"pratos": [{"nome": p["nome"], "valor": p["preco_total"], "ingredientes": len(p["ingredientes"])} for p in s["pratos"]],
            "ingredientes": [{"nome": i["nome"], "valor": i["preco_total"], "quantidade": i["quantidade"], "unidade": i["unidade"]}
                             for i in s["ingredientes"]]}


def _carrinho_resposta(store: Store, **extra: Any) -> dict[str, Any]:
    resumo = store.carrinho_resumo()
    avulsos = [{k: i[k] for k in ("id", "nome", "quantidade", "unidade", "preco_total", "estimado", "motivo")}
               for i in store.carrinho_itens() if not i["prato"]]
    return {"success": True, "carrinho": avulsos, "pratos": store.carrinho_pratos(), "resumo": resumo,
            "diga_a_dona_maria": _texto_saldo(resumo), **extra}


def _recusa_saldo(exc: OrcamentoEstourado, store: Store) -> dict[str, Any]:
    resumo = store.carrinho_resumo()
    return {"success": False, "bloqueado": True, "error": str(exc), "falta": exc.falta, "resumo": resumo,
            "diga_a_dona_maria": _texto_saldo(resumo),
            "proximos_passos": ["diga quanto falta e pergunte se ela prefere tirar um item do carrinho (carrinho_remover) ou trocar por um mais barato"]}


def _aviso_da_cozinha(store: Store, prato: Optional[str], ingredientes: list[str], *, lembrar_do_perfil: bool = False) -> dict[str, Any]:
    """Confere o que vai para o carrinho com a cozinha dela (o que ela tem, o que sabe fazer e as fichas da base) e devolve as frases
    prontas para a consultora repetir. É rascunho de compra, então só avisa: quem decide é ela. As travas de verdade ficam no caminho da
    receita ao cardápio (``_avaliar_prato``)."""
    perfil = store.perfil()
    c = cozinha.conferir(prato, ingredientes, perfil)
    pend = _pendencias_perfil(perfil)
    linhas = [("Atenção: " + m) if c["status"] in ("nao_da", "atencao") else m for m in c["mensagens"]]
    if pend and lembrar_do_perfil:
        linhas.append(f"Ainda não sei tudo da sua cozinha ({len(pend)} perguntas sem resposta): conte aqui ou marque em Minha cozinha antes de comprar.")
    return {"status": c["status"], "mensagens": c["mensagens"], "texto": "\n".join(linhas), "perfil_pendente": [p["pergunta"] for p in pend]}


def _e_prato(store: Store, nome: str) -> bool:
    """Nome de prato (um dos salvos ou da base de pratos mais vendidos): não entra no carrinho como item de preço fechado."""
    return store.salvo_por_nome("prato", nome) is not None or analytics.e_prato(nome)


@_tool
def carrinho_consultar(args: dict) -> dict:
    store = Store()
    negocio = store.negocio()
    return _carrinho_resposta(store, negocio=negocio, negocio_resumo=negocio_mod.descrever(negocio), salvos=_salvos_resumo(store))


@_tool
def carrinho_adicionar(args: dict) -> dict:
    store = Store()
    nome = str(args.get("nome") or "").strip()
    preco = _num(args.get("preco_total"))
    if not nome or preco is None:
        raise ValueError("informe 'nome' e 'preco_total' (R$ da linha inteira)")
    if _e_prato(store, nome):
        return {"success": False, "bloqueado": True, "error": f"{nome} é um prato: ele entra aberto, com os ingredientes e o valor de cada um, não como um item só",
                "proximos_passos": ["chame carrinho_adicionar_prato com 'prato' e 'ingredientes' (cada um com nome, quantidade, unidade e preco_total em R$)"]}
    try:
        item = store.carrinho_adicionar(nome, preco, _num(args.get("quantidade"), 1.0), args.get("unidade"),
                                        estimado=bool(args.get("preco_estimado")), origem="consultora", motivo=args.get("motivo"))
    except OrcamentoEstourado as exc:
        return _recusa_saldo(exc, store)
    aviso = _aviso_da_cozinha(store, args.get("motivo"), [nome])
    extra: dict[str, Any] = {}
    if aviso["texto"]:
        extra = {"alerta_da_cozinha": aviso, "diga_a_dona_maria": aviso["texto"] + "\n" + _texto_saldo(store.carrinho_resumo())}
    return _carrinho_resposta(store, adicionado={k: item[k] for k in ("id", "nome", "quantidade", "unidade", "preco_total", "estimado")}, **extra)


@_tool
def carrinho_adicionar_prato(args: dict) -> dict:
    store = Store()
    nome = str(args.get("prato") or args.get("nome") or "").strip()
    if not nome:
        raise ValueError("informe o nome do 'prato'")
    ingredientes = args.get("ingredientes")
    origem = "consultora"
    if not ingredientes:  # prato que ela já salvou: usa os ingredientes e os valores guardados
        salvo = store.salvo_por_nome("prato", nome)
        if salvo is None:
            return {"success": False, "bloqueado": True,
                    "error": f"informe os ingredientes de {nome}, cada um com quantidade e valor (preco_total em R$)",
                    "proximos_passos": ["liste os ingredientes do prato com o valor de cada um (despensa_buscar para o que ela já tem, pesquisa ou ela mesma para o que falta)"]}
        ingredientes, origem = salvo["ingredientes"], "salvo"
    try:
        grupo = store.carrinho_adicionar_prato(nome, ingredientes, estimado=bool(args.get("precos_estimados")), origem=origem)
    except OrcamentoEstourado as exc:
        return _recusa_saldo(exc, store)
    extra: dict[str, Any] = {}
    if args.get("salvar"):
        extra["salvo_em_meus_pratos"] = store.salvo_salvar("prato", grupo["prato"], ingredientes=ingredientes, origem="consultora")["nome"]
    resumo = store.carrinho_resumo()
    aviso = _aviso_da_cozinha(store, grupo["prato"], [i["nome"] for i in grupo["ingredientes"]], lembrar_do_perfil=True)
    texto = (aviso["texto"] + "\n" if aviso["texto"] else "") + _texto_prato(grupo) + "\n" + _texto_saldo(resumo)
    return _carrinho_resposta(store, prato=grupo, diga_a_dona_maria=texto, alerta_da_cozinha=aviso, **extra)


@_tool
def carrinho_remover(args: dict) -> dict:
    store = Store()
    if args.get("limpar"):
        return _carrinho_resposta(store, removidos=store.carrinho_limpar())
    if args.get("item_id") is None and not str(args.get("nome") or "").strip():
        raise ValueError("informe 'nome' (do ingrediente ou do prato) ou 'item_id', ou 'limpar': true")
    saiu = store.carrinho_remover(item_id=args.get("item_id"), nome=args.get("nome"))
    return _carrinho_resposta(store, removidos=[{k: i[k] for k in ("id", "nome", "prato", "preco_total")} for i in saiu])


@_tool
def carrinho_montar_da_receita(args: dict) -> dict:
    store = Store()
    receita_id = args.get("receita_id")
    if not receita_id:
        raise ValueError("informe 'receita_id' (devolvido por receita_conferir_ingredientes)")
    receita = store.obter_receita(int(receita_id))
    if not receita:
        raise KeyError(f"receita_id {receita_id} não encontrada")
    # Mesma trava do prato: o que ela não consegue cozinhar (equipamento, técnica, perfil) não vai para a lista de compras.
    aval = _avaliar_prato(store, receita["dados"].get("ingredientes") or [], receita)
    if aval["veredito"] != "viavel":
        return _recusa(f"carrinho bloqueado: veredito {aval['veredito']}; só entra no carrinho o que ela consegue cozinhar, "
                       "com equipamentos, técnicas, perfil e preços resolvidos", aval,
                       **_aviso_prato_nao_aprovado(store, receita, aval))
    res = aval["resultado"]
    da_despensa = _itens_da_despensa_para_carrinho(res)
    if not res["compras"] and not da_despensa:
        return _carrinho_resposta(store, receita=receita["nome"], nota="nada a comprar: ela já tem tudo na despensa")
    try:
        store.carrinho_montar_da_receita(int(receita["id"]), receita["nome"], res["compras"], da_despensa)
    except OrcamentoEstourado as exc:
        return _recusa_saldo(exc, store)
    grupo = next(g for g in store.carrinho_pratos() if g["prato"] == receita["nome"][:80].strip())
    resumo = store.carrinho_resumo()
    return _carrinho_resposta(store, receita=receita["nome"], prato=grupo, diga_a_dona_maria=_texto_prato(grupo) + "\n" + _texto_saldo(resumo))


def _itens_da_despensa_para_carrinho(res: dict[str, Any]) -> list[dict[str, Any]]:
    da_despensa = []
    for linha in res["itens"]:  # o que ela já tem entra no prato com o custo, sem gastar saldo
        if linha.get("item_id") is None or (linha.get("usa_da_despensa_base") or 0) <= 1e-9:
            continue
        qtd, un = _legivel_base(float(linha["usa_da_despensa_base"]), linha["unidade_base"])
        nome = linha["item_despensa"] if linha.get("preparo_caseiro") else linha["ingrediente"]
        existente = next((i for i in da_despensa if i["ingrediente"] == nome and i["unidade"] == un), None)
        if existente:
            existente["quantidade"] += qtd
            existente["valor"] = round(existente["valor"] + (linha.get("custo_uso_despensa") or 0.0), 2)
        else:
            da_despensa.append({"ingrediente": nome, "quantidade": qtd, "unidade": un, "valor": linha.get("custo_uso_despensa") or 0.0})
    return da_despensa


@_tool
def salvos_salvar(args: dict) -> dict:
    store = Store()
    tipo = str(args.get("tipo") or "").strip().lower()
    tipo = "prato" if tipo.startswith("prat") else "ingrediente" if tipo.startswith("ingr") else tipo
    if tipo not in ("prato", "ingrediente"):
        raise ValueError("'tipo' precisa ser 'prato' ou 'ingrediente'")
    nome = str(args.get("nome") or "").strip()
    if not nome:
        raise ValueError("informe o 'nome'")
    if tipo == "prato":
        if args.get("ingredientes"):
            salvo = store.salvo_salvar("prato", nome, ingredientes=args["ingredientes"], origem="consultora")
        else:  # sem lista: guarda o prato que já está no carrinho, com os ingredientes e valores de lá
            salvo = store.salvar_do_carrinho(prato=nome, origem="consultora")
    elif args.get("preco_total") is not None:
        salvo = store.salvo_salvar("ingrediente", nome, preco_total=_num(args.get("preco_total")), quantidade=_num(args.get("quantidade"), 1.0),
                                   unidade=args.get("unidade"), origem="consultora")
    else:
        salvo = store.salvar_do_carrinho(nome=nome, origem="consultora")
    detalhe = f"{len(salvo['ingredientes'])} ingredientes, " if tipo == "prato" else ""
    return {"success": True, "salvo": {"tipo": tipo, "nome": salvo["nome"], "valor": salvo["preco_total"], "ingredientes": salvo["ingredientes"]},
            "diga_a_dona_maria": f"Salvei o {tipo} {salvo['nome']} ({detalhe}{costing.brl(salvo['preco_total'])}) em Meus pratos e ingredientes.",
            "salvos": _salvos_resumo(store)}


# ----------------------------------------------------------------------------- vendas realizadas
def _recusa_venda(motivo: str) -> dict[str, Any]:
    return {"success": False, "bloqueado": True, "error": motivo,
            "diga_a_dona_maria": "A venda não foi registrada. " + motivo,
            "proximo_passo": "Peça somente o fato da venda que falta confirmar; não transforme uma previsão em venda realizada."}


@_tool
def vendas_registrar(args: dict, session_id: str = "", turn_id: str = "") -> dict:
    turno = montagem.turno_atual(session_id)
    if not session_id or not turno or (turn_id and turno != turn_id):
        return _recusa_venda("preciso da mensagem e do turno atuais para confirmar uma venda sem duplicá-la")
    falas_dela = falas.da_sessao(session_id) or []
    if not falas_dela:
        return _recusa_venda("informe o prato e quantas porções você já vendeu")
    prato_id = args.get("prato_id")
    if type(prato_id) is not int or prato_id <= 0:
        return _recusa_venda("informe qual prato do cardápio foi vendido")
    store = Store()
    prato = store.obter_prato(prato_id)
    if not prato:
        return _recusa_venda("o prato informado não está no seu cardápio")
    try:
        dados = vendas_falas.conferir(falas_dela[-1].texto, prato_id, prato["nome"], args.get("quantidade"),
                                      args.get("preco_unitario"), args.get("vendido_em"))
        dados["operacao"] = str(uuid5(NAMESPACE_URL, json.dumps(["sabor:venda", session_id, turno, prato_id])))
        resultado = vendas.registrar(store, dados)
    except ValueError as exc:
        return _recusa_venda(str(exc))
    venda = resultado["venda"]
    resumo = vendas.resumo(store)
    hoje = resumo["periodos"]["dia"]
    cabecalho = ("Essa venda já estava registrada; não contei novamente." if resultado["ja_registrada"] else
                 f"Registrei a venda de **{venda['quantidade']} porções de {venda['prato']}**.")
    if venda["status"] == "estornada":
        cabecalho = "Essa venda já existia e permanece estornada; não foi somada novamente."
    data_local = datetime.fromisoformat(venda["vendido_em"]).astimezone(vendas.LOCAL).strftime("%d/%m/%Y %H:%M")
    linhas = [cabecalho,
              f"Preço por porção: **{costing.brl(venda['preco_unitario'])}**. "
              f"Valor da venda: **{costing.brl(venda['faturamento_bruto'])}**. Data: {data_local} (horário de São Paulo).",
              f"Taxa de 10%: {costing.brl(venda['taxa_plataforma'])}; repasse de 90%: {costing.brl(venda['recebe_liquido'])}; "
              f"CMV estimado: {costing.brl(venda['cmv_total'])}; lucro estimado: **{costing.brl(venda['lucro_estimado'])}**.",
              f"Hoje estão informadas {hoje['quantidade']} porções vendidas, com faturamento de {costing.brl(hoje['faturamento_bruto'])}.",
              vendas.AVISO]
    montagem.registrar_resposta(session_id, linhas, substituir=True, prioridade=50)
    return {"success": True, **resultado, "vendas_resumo": resumo, "diga_a_dona_maria": "\n\n".join(linhas)}


@_tool
def vendas_consultar(args: dict) -> dict:
    store = Store()
    consulta = vendas.consultar(store, str(args.get("periodo") or "dia"))
    total = consulta["resumo"]
    descricao = (f"Vendas realizadas informadas no período: {total['quantidade']} porções; "
                 f"faturamento {costing.brl(total['faturamento_bruto'])}; recebe {costing.brl(total['recebe_liquido'])} "
                 f"após a taxa de 10%; lucro estimado {costing.brl(total['lucro_estimado'])}."
                 if total["registros"] else "Ainda não há vendas realizadas confirmadas neste período.")
    return {"success": True, **consulta, "vendas_resumo": vendas.resumo(store),
            "diga_a_dona_maria": descricao + " " + vendas.AVISO}


# ----------------------------------------------------------------------------- analytics
@_tool
def analytics_analises_consultar(args: dict) -> dict:
    return {"success": True, **precificacao.consultar(Store())}


def _contexto_mais_vendidos(r: dict[str, Any], local: str) -> str:
    """De onde vêm os números, para a frase que a consultora repete."""
    if not local:
        return "só o país: a loja ainda não tem localização em Meu negócio"
    if r["escopo"] == "prioridade":
        return f"prioridade para {local}"
    nivel = next((e for e in r["escopos_disponiveis"] if e["escopo"] == r["escopo"]), None)
    return f"{nivel['rotulo']}: {nivel['onde']}" if nivel else r["escopo"]


def _marca_da_cozinha(m: dict[str, Any]) -> str:
    c = m.get("cozinha") or {}
    if c.get("status") == "nao_da":
        return " [NÃO dá na cozinha dela: " + "; ".join(f["precisa"] for f in c["falta"]) + "]"
    return " [atenção: ela ainda está aprendendo ou não sabe uma habilidade do prato]" if c.get("status") == "atencao" else ""


@_tool
def analytics_consultar(args: dict) -> dict:
    store = Store()
    negocio = store.negocio()
    perfil_precificacao = store.perfil_precificacao()
    r = analytics.consultar(negocio, str(args.get("escopo") or "prioridade"), int(_num(args.get("limite"), 8) or 8),
                           args.get("categoria"))
    cozinha.anotar_ranking(r["ranking"], store.perfil(), perfil_precificacao=perfil_precificacao)
    ranking = []
    for m in r["ranking"]:
        item = {"posicao": m["posicao"], "prato": m["prato"], "categoria": m["categoria"], "preco_medio": m["preco_medio"],
                "faixa": m["faixa"], "ingredientes": m["ingredientes"]}
        if "onde" in m:
            item["prioridade"] = m["prioridade"]
            item["onde"] = [f"{o['escopo']} #{o['posicao']}" for o in m["onde"]]
        else:
            item["indice_de_vendas"] = m["indice"]
        c = m["cozinha"]
        item["cozinha_dela"] = c["status"]
        if c["status"] in ("nao_da", "atencao"):
            item["aviso_da_cozinha"] = c["mensagens"][0]
        if m.get("preco"):
            p = m["preco"]
            item["quanto_cobrar"] = {"custo_tipico_de_exemplo": p["cmv"], "cobrar_no_minimo_sem_prejuizo": p["preco_minimo"],
                                     **{f"para_sobrar_{round(x['margem'] * 100)}_por_cento": x["preco"] for x in p["cenarios"]},
                                     "lucro_por_prato_no_preco_do_mercado": p["mercado"]["lucro"]}
        ranking.append(item)
    local = negocio_mod.descrever_local(negocio)
    resposta: dict[str, Any] = {
        "success": True, "escopo": r["escopo"], "local_da_loja": local or "não informado (Meu negócio)",
        "escopos_com_dados": [f"{e['rotulo']}: {e['onde']}" for e in r["escopos_disponiveis"]], "ranking": ranking, "aviso": r["aviso"],
        "como_usar": ("Copie 'diga_a_dona_maria' (ele já avisa que é a base de exemplo) e pergunte qual prato ela prefere. Os primeiros são os da zona "
                      "e da cidade da loja. Cada prato traz 'cozinha_dela' (ok, confirmar, atencao ou nao_da, conferido com o que ela tem e sabe fazer): "
                      "não ofereça como opção um prato 'nao_da' sem dizer o motivo. 'quanto_cobrar' usa um custo TÍPICO DE EXEMPLO; para o custo real "
                      "dela use prato_calcular_cmv e preco_sugerir. Para o prato que ela escolher, detalhe os ingredientes e o valor de cada um "
                      "(carrinho_adicionar_prato)."),
    }
    if r.get("mensagem"):
        resposta["mensagem"] = r["mensagem"]
    if ranking:
        lista = "; ".join(
            f"{m['posicao']}) {m['prato']} (~{costing.brl(m['preco_medio'])}"
            + (f"; cobrar a partir de {costing.brl(m['preco']['preco_minimo'])}" if m.get("preco") else "") + ")" + _marca_da_cozinha(m)
            for m in r["ranking"][:5])
        resposta["diga_a_dona_maria"] = (f"Mais vendidos ({_contexto_mais_vendidos(r, local)}; base de exemplo, dados ilustrativos): "
                                         f"{lista}. \"Cobrar a partir de\" cobre o custo de exemplo após a taxa de 10% do desafio. "
                                         + "Com o custo real da receita o preço muda.")
    return resposta


# ----------------------------------------------------------------------------- schemas (formato OpenAI function)
_ING_SCHEMA = {
    "type": "array",
    "description": "Lista completa dos ingredientes efetivamente usados nesta versão, com medidas publicadas ou estimativas locais explícitas. Em adaptações, omita do lote o que não será usado e registre a ressalva em adaptacao.",
    "items": {
        "type": "object",
        "properties": {
            "nome": {"type": "string", "description": "Ingrediente efetivamente usado. Na adaptação use o nome real disponível, não o nome de um ingrediente ausente."},
            "ingrediente_original": {"type": "string", "description": "Opcional em adaptação: ingrediente da fonte substituído por este item. Ex.: nome='Óleo de soja', ingrediente_original='azeite'. Explique a troca em adaptacao e no preparo."},
            "quantidade": {"type": "number", "description": "Quantidade numérica. Para 'a gosto', estime (ex.: sal 5 g)."},
            "base_estimativa_de_uso": {"type": "string", "description": "Se esta quantidade foi estimada, explique em poucas palavras a base para o lote e o rendimento atuais."},
            "unidade": {"type": "string", "description": "Unidade da receita: g, kg, ml, L, un, colher de sopa, colher de chá, xícara, dente, fatia, folha, maço, pequena/média/grande"},
            "item_despensa": {"type": "string", "description": "Opcional: nome exato do item da despensa correspondente. Passe string vazia se o ingrediente certamente NÃO está na despensa."},
            "preco_compra_estimado": {"type": "number", "description": "Se precisar comprar: preço estimado (R$) da embalagem"},
            "quantidade_compra": {"type": "number", "description": "Se precisar comprar: quantidade da embalagem"},
            "unidade_compra": {"type": "string", "description": "Se precisar comprar: unidade da embalagem (kg, g, L, ml, un)"},
            "opcional": {"type": "boolean", "description": "true se a receita diz que é opcional"},
        },
        "required": ["nome", "quantidade", "unidade"],
    },
}

@_tool
def divulgacao_planejar(args: dict, session_id: Optional[str] = None) -> dict:
    from . import divulgacao, jornada

    padrao = pesquisa.iniciar_analise_divulgacao(session_id or "")
    store = Store()
    etapa = jornada.resumo(store)
    if etapa["etapa"] != "divulgacao" or etapa["controle"]["divulgacao"] != "interesse":
        mensagem = "Posso fazer a análise de divulgação depois que o cardápio estiver aprovado e você quiser começar."
        if padrao:
            montagem.registrar_resposta(session_id or "", [mensagem], substituir=True, prioridade=20)
        return {"success": False, "error": mensagem}
    if padrao:
        preferencias = divulgacao.parametros_da_conversa(falas.da_sessao(session_id or "") or [])
        resultado, novo = divulgacao.preparar(store, preferencias)
        mensagem = divulgacao.texto_preparado(resultado, novo)
        montagem.registrar_resposta(session_id or "", [mensagem], substituir=True, prioridade=20)
        return {"success": True, **resultado, "plano_novo": novo, "diga_a_dona_maria": mensagem}
    if not args and (plano := etapa.get("plano_divulgacao")):
        mensagem = divulgacao.texto_preparado(plano, False)
        montagem.registrar_resposta(session_id or "", [mensagem], substituir=True, prioridade=20)
        return {"success": True, **plano, "plano_novo": False, "diga_a_dona_maria": mensagem}
    resultado = divulgacao.planejar(store, args)
    if resultado.get("diga_a_dona_maria"):
        montagem.registrar_resposta(session_id or "", [resultado["diga_a_dona_maria"]], substituir=True, prioridade=20)
    return {"success": True, **resultado}


SCHEMAS: dict[str, dict[str, Any]] = {
    "despensa_listar": {
        "name": "despensa_listar",
        "description": "Lista a despensa da Dona Maria (planilha despensa_dona_maria.xlsx): estoque, unidade normalizada (kg/L/un), custo unitário (preço pago ÷ quantidade comprada), preço pago e o orçamento restante para compras. É a ÚNICA fonte para 'quanto custa', 'quanto tem' ou 'tem na despensa?' de qualquer ingrediente: nunca use web_search para isso. Use no início da conversa e sempre que precisar de custos.",
        "parameters": {"type": "object", "properties": {
            "filtro": {"type": "string", "description": "Opcional: trecho do nome para filtrar (ex.: 'queijo'); vários separados por vírgula (ex.: 'frango, batata') trazem os que casam com qualquer um"},
        }, "additionalProperties": False},
    },
    "despensa_buscar": {
        "name": "despensa_buscar",
        "description": "Busca por proximidade e sinônimos os candidatos reais da despensa, com estoque, custo e score de semelhança (não probabilidade de sucesso). confiavel=false não proíbe uma adaptação: avalie a função culinária, use o nome real do substituto na ficha e explique a troca em adaptacao. Derivados simples usam a matéria-prima, como tomate→molho; não invente estoque do produto pronto. Para estoque e custo use esta tool, não web_search.",
        "parameters": {"type": "object", "properties": {
            "ingredientes": {"type": "array", "items": {"type": "string"}, "description": "Nomes como aparecem na receita"},
        }, "required": ["ingredientes"]},
    },
    "despensa_atualizar": {
        "name": "despensa_atualizar",
        "description": "Registra apenas o conteúdo de uma embalagem de um item que já existe, quando ela disser a medida. Exemplo: um pote de cobertura de chocolate tem 1,01 kg. Para compras use despensa_registrar_compras.",
        "parameters": {"type": "object", "properties": {
            "ingrediente": {"type": "string", "description": "Nome do item como está na despensa"},
            "conteudo_por_unidade": {"type": "object", "properties": {"quantidade": {"type": "number"}, "unidade": {"type": "string", "description": "g, kg, ml ou L"}},
                                     "required": ["quantidade", "unidade"], "description": "Quanto tem em uma embalagem"},
        }, "required": ["ingrediente", "conteudo_por_unidade"]},
    },
    "despensa_registrar_compras": {
        "name": "despensa_registrar_compras",
        "description": ("Dá entrada de compras confirmadas na despensa e abate o orçamento em uma chamada. Cadastra ingredientes novos automaticamente. "
                        "Depois de mostrar compras_para_confirmar da conferência, use apenas receita_id quando ela pedir o lançamento no estoque. "
                        "Para compras avulsas que ela informou, use compras com os dados da fala atual. Não passe ingredientes já disponíveis da receita. "
                        "Use diga_a_dona_maria para informar a gravação e o saldo real."),
        "parameters": {"type": "object", "properties": {
            "receita_id": {"type": "integer", "description": "Id da conferência cuja lista de compras foi mostrada e aprovada"},
            "compras": {"type": "array", "items": {"type": "object", "properties": {
                "nome": {"type": "string"}, "quantidade": {"type": "number"}, "unidade": {"type": "string"},
                "preco_total": {"type": "number", "description": "Valor total pago pelo item, em reais"},
                "compra_id": {"type": "integer", "description": "Id de compromisso de compra existente, se for a mesma compra"},
                "carrinho_id": {"type": "integer", "description": "Id do item no carrinho, se for a mesma compra"},
            }, "required": ["nome", "quantidade", "unidade", "preco_total"], "additionalProperties": False}},
        }, "additionalProperties": False},
    },
    "perfil_cozinha_consultar": {
        "name": "perfil_cozinha_consultar",
        "description": "Mostra a cozinha da Dona Maria (equipamentos e utensílios, técnicas, restrições operacionais: o que ela contou na conversa e o que marcou na tela Minha cozinha, que é o mesmo registro), as perguntas ainda pendentes e o texto delas já pronto (perguntas_prontas). Use os dados salvos antes de aprovar um prato. Pergunte no chat somente equipamentos, técnicas e condições operacionais relevantes ao preparo escolhido, usando os bloqueios da conferência. O checklist completo é opcional; não exige responder itens irrelevantes para pesquisar ou aprovar uma receita viável.",
        "parameters": {"type": "object", "properties": {}},
    },
    "perfil_cozinha_salvar": {
        "name": "perfil_cozinha_salvar",
        "description": "Salva SÓ o que a Dona Maria disse sobre a cozinha dela. A tool confere cada item com as falas dela e recusa (campo 'recusados') o que ela não falou, mesmo que pareça óbvio: se ela não falou, não envie o item, pergunte. Nunca complete o perfil por conta própria. 'equipamentos': nome do equipamento → número (bocas do fogão) ou true/false. 'tecnicas': nome da técnica → 'sim', 'nao' ou 'aprendendo'. 'restricoes': espaco_geladeira, tempo_por_cozinhada_min (em minutos), gas, energia e outras. Faz merge com o que já existe. Técnicas que ela contou (\"sei refogar\", \"nunca fiz massa fresca\") vão em 'tecnicas', nunca em texto livre.",
        "parameters": {"type": "object", "properties": {
            "equipamentos": {"type": "object", "description": "só equipamentos que ela citou: nome → número ou true/false", "additionalProperties": True},
            "tecnicas": {"type": "object", "description": "só técnicas que ela citou: nome → 'sim', 'nao' ou 'aprendendo'", "additionalProperties": True},
            "restricoes": {"type": "object", "description": "só restrições que ela contou: espaco_geladeira, tempo_por_cozinhada_min, gas, energia...", "additionalProperties": True},
        }},
    },
    "negocio_salvar": {
        "name": "negocio_salvar",
        "description": "Salva SÓ o que a Dona Maria disse do negócio dela: onde a loja fica (estado, cidade e zona da cidade), o tipo de negócio e o tipo de alimentação que ela faz. A tool confere cada item com as falas dela e recusa (campo 'recusados') o que ela não falou: se ela não falou, não envie, pergunte. Faz merge com o que já está salvo; ela vê e corrige tudo na tela Meu negócio. Os pratos e a cozinha dela NÃO vão aqui (use perfil_cozinha_salvar).",
        "parameters": {"type": "object", "properties": {
            "estado": {"type": "string", "description": "o estado onde a loja fica: o nome (ou a sigla) que ela disse"},
            "cidade": {"type": "string", "description": "a cidade onde a loja fica, como ela disse"},
            "objetivo": {"type": "string", "maxLength": 240, "description": "objetivo opcional do negócio, com as palavras dela; não invente meta de vendas nem orçamento"},
            "zona": {"type": "string", "enum": list(negocio_mod.ZONAS), "description": "a zona da cidade, se ela disse"},
            "tipo": {"type": "string", "enum": list(negocio_mod.TIPOS), "description": "o tipo de negócio que ela disse"},
            "alimentacao": {"type": "array", "items": {"type": "string", "enum": list(negocio_mod.ALIMENTACAO)},
                            "description": "o tipo de alimentação que ela faz (pode ser mais de um), só o que ela disse"},
        }},
    },
    "pratos_selecionar": {
        "name": "pratos_selecionar",
        "description": "Guarda os pratos que ela acabou de escolher em Minha cozinha > Pratos, como rascunhos a montar. A escolha não requer preço, ficha, carrinho ou aprovação. Informe só os nomes que ela escolheu; se ela indicou números da última lista, a ferramenta resolve a lista mostrada.",
        "parameters": {"type": "object", "properties": {
            "nomes": {"type": "array", "items": {"type": "string"}, "description": "nomes escolhidos quando ela informou por nome"},
        }},
    },
    "receita_conferir_ingredientes": {
        "name": "receita_conferir_ingredientes",
        "description": "Salva e confere uma versão contra despensa, cozinha e orçamento. A fonte é referência: adaptações explícitas podem substituir, estimar quantidades e omitir ingredientes faltantes. Use o mesmo prato_id, lista completa do lote, adaptacao e modo_preparo próprio. Não exija igualdade com a web numa adaptação. Só para a original sem alterações envie apenas prato_id. Em reconferência de adaptação salva, apenas prato_id preserva a versão atual. Mostre ficha, ressalvas e diga_a_dona_maria; aceite final e preço continuam com ela.",
        "parameters": {"type": "object", "properties": {
            "prato_id": {"type": "integer", "description": "id do prato escolhido que esta conferência está montando, se houver"},
            "nome": {"type": "string", "description": "Nome da receita"},
            "fonte_url": {"type": "string", "description": "URL de onde a receita foi tirada"},
            "porcoes": {"type": "number", "description": "Quantas porções a receita rende"},
            "ingredientes": _ING_SCHEMA,
            "ingredientes_omitidos": {"type": "array", "items": {"type": "string"}, "description": "Nomes dos ingredientes que NÃO serão usados na adaptação. A ferramenta os exclui do lote, mesmo se copiados por engano na lista, preservando fonte e ressalvas. Ex.: ['Fermento biológico', 'Orégano a gosto']. Use [] se não houver omissões."},
            "equipamentos_necessarios": {"type": "array", "items": {"type": "string"}, "description": "ex.: ['forno', 'panela de pressão', 'liquidificador']"},
            "tecnicas_necessarias": {"type": "array", "items": {"type": "string"}, "description": "ex.: ['massa fresca', 'molho branco', 'ponto da carne']"},
            "tempo_preparo_min": {"type": "number", "description": "Tempo total desta versão em minutos, incluindo preparo e cozimento. Use o total publicado quando aplicável; para estimar a partir das etapas preencha tempo_preparo_base."},
            "tempo_preparo_base": {"type": "string", "description": "Base da estimativa do tempo total quando a fonte não traz o total para esta versão. Explique os tempos de preparo e de cozimento considerados."},
            "adaptacao": {"type": "string", "description": "Descrição da versão local: substituições por proximidade, ingredientes omitidos e estimativas. Se ela delegou usar o estoque, proponha e salve agora, sem exigir outra autorização para adaptar. Fonte original e faltas ficam preservadas como observações; apenas insumos usados entram no CMV. Mostre a versão antes do aceite final."},
            "modo_preparo": {"type": "string", "description": "Preparo completo da versão proposta. Se a adaptação usar outro método, descreva as etapas e os equipamentos efetivamente usados para que a ferramenta confira as condições dessa versão."},
            "confirmacoes_operacionais": {"type": "object", "description": "Só quando ela confirmar especificamente fornecimento ou espaço suficiente para este lote: chave gas, energia ou espaco_geladeira e valor com a fala literal. Não aceita sim genérico; confirmação fica vinculada a ingredientes, rendimento, requisitos e perfil atuais.",
                                          "properties": {chave: {"type": "string"} for chave in ("gas", "energia", "espaco_geladeira")}, "additionalProperties": False},
        }},
    },
    "prato_calcular_cmv": {
        "name": "prato_calcular_cmv",
        "description": "Etapa 2.4: calcula o CMV do prato (Σ quantidade usada × custo unitário, custo unitário = preço pago ÷ quantidade comprada, incluindo compras complementares), o CMV por porção e a memória de cálculo linha a linha. Exige uma receita conferida (receita_id) e reavalia perfil da cozinha, despensa disponível e orçamento: com qualquer pendência devolve bloqueado=true com 'bloqueios' e 'proximos_passos' em vez do CMV. Resolva-os (clarify + perfil_cozinha_salvar, despensa_atualizar) e chame de novo com o mesmo receita_id.",
        "parameters": {"type": "object", "properties": {
            "prato_id": {"type": "integer", "description": "id do prato escolhido vinculado à conferência, se houver"},
            "receita_id": {"type": "integer", "description": "id devolvido pela conferência; pode usar prato_id quando já houver ficha vinculada"},
            "nome": {"type": "string", "description": "Opcional: nome do prato (padrão: nome da receita)"},
            "porcoes": {"type": "number", "description": "Opcional: porções que a receita rende (padrão: porções da receita conferida)"},
            "ingredientes": _ING_SCHEMA,
        }},
    },
    "preco_sugerir": {
        "name": "preco_sugerir",
        "description": "Usa o CMV e a taxa fixa de 10% do enunciado para sugerir preços. Após o aceite explícito de uma versão viável, registra o lote, baixa o estoque disponível, contabiliza despesas e salva a análise. Mostre os valores registrados, incluindo o saldo gerencial e o disponível para compras. Explique o CMV, o repasse 0,90 × preço e o lucro 0,90 × preço − CMV. Pergunte qual preço ela quer adotar.",
        "parameters": {"type": "object", "properties": {
            "prato_id": {"type": "integer", "description": "id do prato devolvido por prato_calcular_cmv (obrigatório)"},
            "aceite_prato": {"type": "string", "description": "Opcional. O aceite é validado pelo que ela disse na conversa."},
            "margens": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}, "description": "Escolha 2–3 margens-alvo distintas, da menor para a maior, como frações do recebido depois da taxa, por exemplo [0.20, 0.30]."},
            "nomes": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "string", "maxLength": 50}, "description": "Um nome distinto para cada cenário, na ordem das margens."},
            "cenario_recomendado": {"type": "integer", "minimum": 1, "maximum": 3, "description": "Posição do cenário que você recomenda, começando em 1."},
            "recomendacao": {"type": "string", "maxLength": 280, "description": "Justifique a recomendação em uma frase com base no prato e no plano da loja, sem inventar dados ou calcular valores à mão."},
            "lucro_alvo": {"type": "number", "minimum": 0, "description": "Opcional: meta de lucro em R$, quando ela informar. Use o mesmo período dos custos fixos."},
            "custos_fixos": {"type": "number", "minimum": 0, "description": "Opcional: custos fixos confirmados em R$ no período da meta dela."},
            "precos_para_simular": {"type": "array", "items": {"type": "number"}, "description": "Opcional: preços que a Dona Maria quer testar, ex.: [25, 29.9]"},
            "arredondar_para": {"type": "number", "minimum": 0.01, "description": "Arredondamento para cima em R$ (padrão 0.50)."},
        }, "required": ["prato_id"]},
    },
    "cardapio_registrar": {
        "name": "cardapio_registrar",
        "description": "Registra no cardápio o preço escolhido pela Dona Maria e reaproveita o lote da montagem. Reavalia as condições da cozinha, a despensa, o CMV e o disponível para compras. Repetir a confirmação ou trocar o preço mantém o mesmo lote e contabiliza seus insumos uma vez. Chame só depois que ela escolher o preço explicitamente.",
        "parameters": {"type": "object", "properties": {
            "prato_id": {"type": "integer", "description": "id devolvido por prato_calcular_cmv"},
            "preco_escolhido": {"type": "number", "description": "Preço de venda escolhido pela Dona Maria"},
            "confirmar_compras": {"type": "boolean", "description": "false para registrar o prato sem comprometer o orçamento ainda"},
        }, "required": ["prato_id", "preco_escolhido"]},
    },
    "cardapio_remover": {
        "name": "cardapio_remover",
        "description": "Tira um prato do cardápio: libera o estoque reservado e devolve ao saldo os compromissos de compra do prato. Compras já pagas e registradas na despensa continuam contabilizadas.",
        "parameters": {"type": "object", "properties": {
            "prato_id": {"type": "integer", "description": "id do prato (ver cardapio_consultar)"},
            "motivo": {"type": "string", "description": "Opcional: por que saiu (ex.: 'ela preferiu o estrogonofe')"},
        }, "required": ["prato_id"]},
    },
    "cardapio_consultar": {
        "name": "cardapio_consultar",
        "description": "Situação atual: pratos aceitos no cardápio (com preço, lucro por porção e o estoque reservado para cada um), pratos em análise, pratos removidos, estoque reservado por ingrediente, receitas conferidas, orçamento restante e resumo do perfil da cozinha. Use para retomar uma conversa ou fechar a sessão.",
        "parameters": {"type": "object", "properties": {}},
    },
    "divulgacao_planejar": {
        "name": "divulgacao_planejar",
        "description": "Prepara e salva o plano local de divulgação após a pessoa delegar a etapa. "
                       "Na chamada guiada, o plano usa o cardápio aprovado, vendas recentes e valores explícitos na conversa e reúne ações e mensagens prontas. "
                       "Em outra consulta, informe apenas os valores que a pessoa pediu para simular. A ferramenta não adere a campanhas externas.",
        "parameters": {"type": "object", "properties": {
            "prato_id": {"type": "integer", "minimum": 1, "description": "Prato aprovado que ela indicou"},
            "investimento": {"type": "number", "minimum": 0, "description": "Valor de divulgação em reais no período informado"},
            "porcoes": {"type": "integer", "minimum": 1, "description": "Porções hipotéticas no mesmo período"},
            "periodo": {"type": "string", "enum": ["dia", "semana", "mes"], "description": "Período do cenário. A análise padrão usa uma semana."},
        }, "additionalProperties": False},
    },
    "carrinho_consultar": {
        "name": "carrinho_consultar",
        "description": "Mostra o carrinho de compras da Dona Maria (ingredientes avulsos e PRATOS abertos, cada um com os ingredientes e o valor de cada um), o orçamento, quanto já está no carrinho, o SALDO que sobra, o perfil do negócio dela e o que ela salvou em 'Meus pratos e ingredientes'. Use para sugerir pratos e ingredientes e para avisar o saldo. Depois de qualquer mudança no carrinho, copie a ela o campo 'diga_a_dona_maria'.",
        "parameters": {"type": "object", "properties": {}},
    },
    "carrinho_adicionar": {
        "name": "carrinho_adicionar",
        "description": "Põe UM INGREDIENTE avulso no carrinho quando ela pedir ou aceitar. Só para ingrediente: prato não entra assim (a tool recusa), use carrinho_adicionar_prato. Recusa (bloqueado=true) se o preço não couber no saldo. O preço é o da linha inteira, em R$, e vem dela ou de uma pesquisa; marque preco_estimado=true quando for estimativa.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string", "description": "Nome do ingrediente"},
            "preco_total": {"type": "number", "description": "Preço da linha inteira em R$ (quantidade × preço unitário)"},
            "quantidade": {"type": "number", "description": "Quantidade (padrão 1)"},
            "unidade": {"type": "string", "description": "kg, g, L, ml, un, maço..."},
            "preco_estimado": {"type": "boolean", "description": "true se o preço é estimativa e não o que ela informou"},
            "motivo": {"type": "string", "description": "Opcional: para qual prato"},
        }, "required": ["nome", "preco_total"]},
    },
    "carrinho_adicionar_prato": {
        "name": "carrinho_adicionar_prato",
        "description": "Põe um PRATO no carrinho, sempre aberto: todos os ingredientes, cada um com quantidade e valor em R$ (preco_total), mais o total do prato. Nunca ponha um prato como item único com preço fechado. Sem 'ingredientes', usa o prato que ela salvou com esse nome. Ingrediente que ela já tem na despensa: ja_tem=true (mostra o valor e não gasta saldo). Recusa (bloqueado=true) se o que ela vai comprar não couber no saldo. Confere o prato com a cozinha dela: se ela não tem o que o prato precisa, o aviso vem em alerta_da_cozinha e no começo de diga_a_dona_maria (copie-o). salvar=true também guarda o prato em 'Meus pratos'.",
        "parameters": {"type": "object", "properties": {
            "prato": {"type": "string", "description": "Nome do prato"},
            "ingredientes": {"type": "array", "description": "Ingredientes do prato, cada um com o valor", "items": {"type": "object", "properties": {
                "nome": {"type": "string"},
                "quantidade": {"type": "number"},
                "unidade": {"type": "string", "description": "kg, g, L, ml, un..."},
                "preco_total": {"type": "number", "description": "Valor deste ingrediente em R$ (quantidade × preço unitário)"},
                "ja_tem": {"type": "boolean", "description": "true se ela já tem na despensa"},
            }, "required": ["nome", "preco_total"]}},
            "precos_estimados": {"type": "boolean", "description": "true se os valores são estimativa e não o que ela informou"},
            "salvar": {"type": "boolean", "description": "true para guardar também em Meus pratos"},
        }, "required": ["prato"]},
    },
    "carrinho_remover": {
        "name": "carrinho_remover",
        "description": "Tira do carrinho um ingrediente ou um PRATO inteiro (pelo nome ou item_id) quando ela pedir, ou esvazia tudo com limpar=true. Devolve o carrinho e o saldo atualizados.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string", "description": "Nome do ingrediente ou do prato como está no carrinho"},
            "item_id": {"type": "integer", "description": "id do item (de carrinho_consultar)"},
            "limpar": {"type": "boolean", "description": "true para esvaziar o carrinho inteiro"},
        }},
    },
    "carrinho_montar_da_receita": {
        "name": "carrinho_montar_da_receita",
        "description": "Põe no carrinho o prato de uma receita já conferida (receita_id de receita_conferir_ingredientes), aberto: o que ela compra, com os preços da própria conferência, e o que já tem na despensa, com o custo e sem gastar saldo. Só funciona com receita 'viavel' (equipamentos, técnicas e perfil confirmados) e dentro do saldo; senão devolve bloqueado=true com os motivos. Repetir não duplica. Quando o prato for aceito em cardapio_registrar, os itens saem do carrinho e viram compras comprometidas.",
        "parameters": {"type": "object", "properties": {
            "receita_id": {"type": "integer", "description": "id devolvido por receita_conferir_ingredientes"},
        }, "required": ["receita_id"]},
    },
    "salvos_salvar": {
        "name": "salvos_salvar",
        "description": "Guarda em 'Meus pratos e ingredientes' um PRATO (com os ingredientes e o valor de cada um) ou um INGREDIENTE avulso, para ela usar de novo, quando ela pedir para salvar. Sem 'ingredientes' (prato) ou sem 'preco_total' (ingrediente), guarda o que já está no carrinho com esse nome. Mesmo nome atualiza.",
        "parameters": {"type": "object", "properties": {
            "tipo": {"type": "string", "enum": ["prato", "ingrediente"]},
            "nome": {"type": "string", "description": "Nome do prato ou do ingrediente"},
            "ingredientes": {"type": "array", "description": "Só prato: ingredientes com o valor de cada um", "items": {"type": "object", "properties": {
                "nome": {"type": "string"}, "quantidade": {"type": "number"}, "unidade": {"type": "string"}, "preco_total": {"type": "number"}, "ja_tem": {"type": "boolean"},
            }, "required": ["nome", "preco_total"]}},
            "preco_total": {"type": "number", "description": "Só ingrediente: preço da linha em R$"},
            "quantidade": {"type": "number", "description": "Só ingrediente: quantidade (padrão 1)"},
            "unidade": {"type": "string", "description": "Só ingrediente: kg, g, L, ml, un..."},
        }, "required": ["tipo", "nome"]},
    },
    "analytics_analises_consultar": {
        "name": "analytics_analises_consultar",
        "description": "Recupera as análises de preço que a consultora já salvou no Analytics para os pratos aceitos pela Dona Maria. Inclui a recomendação, os cenários calculados e o preço registrado, quando ela escolheu.",
        "parameters": {"type": "object", "properties": {}},
    },
    "vendas_registrar": {
        "name": "vendas_registrar",
        "description": "Registra somente uma VENDA JÁ REALIZADA que a pessoa informou explicitamente na última mensagem deste turno (ex.: vendi 3 porções do prato #2 a R$ 8,50 cada). Montar ou aprovar prato, intenção de vender, previsão, hipótese, exemplo e citação não são vendas. Informe o prato e a quantidade literalmente vendidos; o número do prato não é a quantidade. Sem preço informado usa o preço escolhido no cardápio; se houve outro preço, copie o valor dito por ela. Sem data informada registra hoje; outra data exige ISO completo com fuso dito pela pessoa. Repetir a chamada no mesmo turno/prato não duplica. Não baixa estoque nem gasta o orçamento de complementos.",
        "parameters": {"type": "object", "properties": {
            "prato_id": {"type": "integer", "description": "Prato aprovado que a pessoa identificou por nome ou número"},
            "quantidade": {"type": "integer", "minimum": 1, "description": "Porções efetivamente vendidas, não a ID nem rendimento do prato"},
            "preco_unitario": {"type": "number", "description": "Opcional: outro valor por porção informado explicitamente na mensagem da venda"},
            "vendido_em": {"type": "string", "description": "Opcional: copie exatamente o ISO com fuso informado pela pessoa; não invente data nem transforme ontem em hoje"},
        }, "required": ["prato_id", "quantidade"], "additionalProperties": False},
    },
    "vendas_consultar": {
        "name": "vendas_consultar",
        "description": "Consulta apenas vendas realizadas já informadas, com faturamento, taxa fixa de 10%, repasse, CMV e lucro estimado. Dia atual, semana de segunda a domingo ou mês calendário, no fuso de São Paulo. Não infere vendas de pratos, reservas ou sugestões.",
        "parameters": {"type": "object", "properties": {
            "periodo": {"type": "string", "enum": ["dia", "semana", "mes"], "description": "Padrão dia; todos são períodos correntes"},
        }, "additionalProperties": False},
    },
}

HANDLERS: dict[str, Callable[..., str]] = {
    "despensa_listar": despensa_listar,
    "despensa_buscar": despensa_buscar,
    "despensa_atualizar": despensa_atualizar,
    "despensa_registrar_compras": despensa_registrar_compras,
    "perfil_cozinha_consultar": perfil_cozinha_consultar,
    "perfil_cozinha_salvar": perfil_cozinha_salvar,
    "negocio_salvar": negocio_salvar,
    "pratos_selecionar": pratos_selecionar,
    "receita_conferir_ingredientes": receita_conferir_ingredientes,
    "prato_calcular_cmv": prato_calcular_cmv,
    "preco_sugerir": preco_sugerir,
    "cardapio_registrar": cardapio_registrar,
    "cardapio_remover": cardapio_remover,
    "cardapio_consultar": cardapio_consultar,
    "divulgacao_planejar": divulgacao_planejar,
    "carrinho_consultar": carrinho_consultar,
    "carrinho_adicionar": carrinho_adicionar,
    "carrinho_adicionar_prato": carrinho_adicionar_prato,
    "carrinho_remover": carrinho_remover,
    "carrinho_montar_da_receita": carrinho_montar_da_receita,
    "salvos_salvar": salvos_salvar,
    "analytics_analises_consultar": analytics_analises_consultar,
    "vendas_registrar": vendas_registrar,
    "vendas_consultar": vendas_consultar,
}
