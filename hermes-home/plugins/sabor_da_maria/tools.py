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
import inspect
import json
import logging
import math
import os
import re
from typing import Any, Callable, Optional

from . import analytics, compras, costing, cozinha, escolhas, falas, precificacao, taxas
from . import negocio as negocio_mod
from .matching import cortes_incompativeis, tokens
from .store import OrcamentoEstourado, Store
from .units import format_base, norm_text, parse_pantry_unit, to_base

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

def _equip_key(nome: str) -> str:
    """O id do catálogo (cozinha.py) que o nome cita: 'fogão de 4 bocas' → fogao, 'assadeira' → forma. Fora do catálogo, o próprio nome."""
    return cozinha.id_por_termos(nome, (cozinha.EQUIPAMENTO, cozinha.UTENSILIO)) or norm_text(nome).replace(" ", "_")


def _equip_status(equipamentos: dict[str, Any], key: str) -> Optional[str]:
    if key in cozinha.POR_ID:
        achado = cozinha.estado_equipamento(equipamentos, key)  # o id, uma variante ou um nome antigo do mesmo item
        if achado is not None:
            return achado
    if key in equipamentos:
        return _nivel(equipamentos[key])
    for k, v in equipamentos.items():
        if k.startswith(key) or key.startswith(k):
            return _nivel(v)
    return None


def _tecnica_status(tecnicas: dict[str, Any], nome: str) -> Optional[str]:
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
        if _equip_status(valores, chave) is None if grupo == "equipamentos" else chave not in valores:
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
        "orientacao": ("A cozinha dela ainda não está completa. Siga o modo de cadastro do painel. Se indicar formulário no chat, use o marcador "
                       "indicado. Nas outras interfaces, faça as perguntas de 'perguntas_prontas' em texto ou com a tool clarify. "
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
    if grupo == "tecnicas" and cozinha.habilidade_id(chave.replace("_", " ")):
        return (chave.replace("_", " "), *cozinha.termos_de(cozinha.habilidade_id(chave.replace("_", " ")) or ""))
    if grupo == "restricoes" and chave in _NEEDLES_RESTRICOES:
        return _NEEDLES_RESTRICOES[chave]
    return (chave.replace("_", " "),)


def _sem_apoio(grupo: str, chave: str, valor: Any, falas_dela: list[falas.Fala]) -> Optional[str]:
    """Motivo para recusar o item, ou ``None`` quando alguma fala dela o sustenta."""
    nome = chave.replace("_", " ")
    if not falas.apoia(_termos(grupo, chave), falas_dela):
        return f"ela não falou de {nome}"
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if not falas.apoia_numero(float(valor), falas_dela, minutos=chave.endswith("_min")):
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
            pedido[grupo] = {(_equip_key(str(k)) if grupo == "equipamentos" else norm_text(str(k)).replace(" ", "_")): v
                             for k, v in valor.items()}
    observacoes = args.get("observacoes")
    observacoes = observacoes if isinstance(observacoes, list) else ([observacoes] if observacoes else [])

    verificar = _conferir_falas()
    falas_dela = falas.da_sessao(session_id) if verificar else []
    sem_fala = "ainda não tenho a fala dela nesta conversa; peça para ela contar de novo"
    aceitos: dict[str, Any] = {}
    recusados: list[dict[str, Any]] = []
    for grupo, itens in pedido.items():
        for chave, valor in list(itens.items()):
            valor, motivo = _tempo_em_minutos(chave, valor, falas_dela if verificar else falas.da_sessao(session_id))
            motivo = motivo or (None if not verificar else (sem_fala if falas_dela is None else _sem_apoio(grupo, chave, valor, falas_dela)))
            if motivo:
                recusados.append({"grupo": grupo, "chave": chave, "valor": valor, "motivo": motivo})
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

    perfil = store.perfil_salvar(aceitos) if aceitos else store.perfil()
    pend = _pendencias_perfil(perfil)
    resposta: dict[str, Any] = {"success": not recusados or bool(aceitos), "perfil": perfil, "pendencias": pend,
                                "completo": not pend, "salvo": aceitos}
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
                                        + " Você vê e corrige tudo em Minha cozinha (menu)."
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
        resposta["diga_a_dona_maria"] = ("Salvei o seu negócio. " + negocio_mod.resumo_para_ela(negocio) + " Você vê e corrige tudo em Meu negócio (menu)."
                                        + (f" Ainda falta saber: {', '.join(p['titulo'].lower() for p in pend)}." if pend else " O negócio está completo."))
        resposta["regra"] = ("Ao contar para ela o que foi salvo, use só o que está em diga_a_dona_maria. Não acrescente técnicas, equipamentos, números "
                             "nem lugares que ela não disse: os exemplos das perguntas não são respostas dela.")
    if pend:
        resposta["perguntas_prontas"] = cozinha.perguntas_iniciais(pend, [])
    return resposta


# ----------------------------------------------------------------------------- despensa (2.3)
@_tool
def despensa_listar(args: dict) -> dict:
    store = Store()
    carga = store.ensure_loaded(force=bool(args.get("recarregar")))
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
                 "disponivel = estoque − o que os pratos já aceitos no cardápio reservaram."),
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
def _compra_info(ing: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    return _num(ing.get("preco_compra_estimado")), _num(ing.get("quantidade_compra")), (str(ing["unidade_compra"]) if ing.get("unidade_compra") else None)


def _resolver_ingredientes(store: Store, ingredientes: list[Any]) -> dict[str, Any]:
    linhas: list[dict[str, Any]] = []
    pendencias: list[str] = []
    estimativas: list[str] = []
    compras: list[dict[str, Any]] = []
    ingredientes_contados: set[str] = set()
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
        if explicito and alvo:
            item = store.obter(str(alvo)) or store.melhor_item(str(alvo))
            if item is None:
                pendencias.append(f"{nome}: '{alvo}' não existe na despensa; use despensa_buscar")
            elif cortes_incompativeis(nome, item["nome"]):
                linha["pendencia"] = f"{nome}: {item['nome']} é outro corte. Confirme uma adaptação ou informe o corte da receita."
                pendencias.append(linha["pendencia"])
                linhas.append(linha)
                continue
        else:
            # Se uma compra já foi registrada com o mesmo nome, use o estoque e o preço pagos.
            item = store.obter(nome) if explicito else store.melhor_item(nome)
        identidade = norm_text(item["nome"] if item is not None else nome)
        if identidade in ingredientes_contados:
            pendencias.append(f"{nome}: some as quantidades do mesmo ingrediente em uma única linha da receita")
            continue
        ingredientes_contados.add(identidade)
        if not math.isfinite(qtd) or qtd <= 0:
            linha["pendencia"] = "quantidade não informada; estime em g/ml mesmo quando a receita diz 'a gosto'"
            pendencias.append(f"{nome}: {linha['pendencia']}")
            linhas.append(linha)
            continue
        memoria: list[str] = []
        if item is not None:
            base = item["unidade_base"]
            conv = to_base(qtd, unidade, item["nome"], base, conteudo_un_base=item.get("conteudo_un_base"))
            linha.update({"item_despensa": item["nome"], "conversao": conv.note, "estimativa": conv.estimate})
            if conv.estimate:
                estimativas.append(f"{nome}: {conv.note}")
            if conv.qty_base is None:
                linha["pendencia"] = conv.note
                pendencias.append(f"{nome}: {conv.note}")
                linhas.append(linha)
                continue
            necessario = conv.qty_base
            estoque = float(item.get("estoque_base") or 0)
            reservado = float(item.get("reservado_base") or 0)
            tem = max(0.0, estoque - reservado)  # disponível = estoque − reservas de outros pratos já aceitos
            usa = min(necessario, tem)
            falta = max(0.0, necessario - tem)
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
                preco_c, qtd_c, un_c = _compra_info(ing)
                if preco_c and qtd_c and un_c:
                    spec = parse_pantry_unit(un_c)
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
                    pendencias.append(f"{nome}: falta {format_base(falta, base)} e não há preço; informe preco_compra_estimado + quantidade_compra + unidade_compra")
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
                spec = parse_pantry_unit(un_c)
                conv = to_base(qtd, unidade, nome, spec.base)
                linha.update({"conversao": conv.note, "estimativa": conv.estimate})
                if conv.estimate:
                    estimativas.append(f"{nome}: {conv.note}")
                if conv.qty_base is None:
                    linha["pendencia"] = conv.note
                    pendencias.append(f"{nome}: {conv.note}")
                else:
                    cu = preco_c / (qtd_c * spec.factor)
                    custo = conv.qty_base * cu
                    compra = {"ingrediente": nome, "quantidade": qtd_c, "unidade": un_c, "preco_estimado": round(preco_c, 2),
                              "cobre_a_falta": qtd_c * spec.factor >= conv.qty_base - 1e-9}
                    if not compra["cobre_a_falta"]:
                        pendencias.append(f"{nome}: a compra cotada não cobre os {format_base(conv.qty_base, spec.base)} necessários")
                    memoria.append(f"{format_base(conv.qty_base, spec.base)} × {costing.brl(cu)}/{spec.base} (compra de {qtd_c:g} {un_c} por {costing.brl(preco_c)}) = {costing.brl(custo)}")
                    linha.update({"necessario": format_base(conv.qty_base, spec.base), "necessario_base": round(conv.qty_base, 4), "unidade_base": spec.base,
                                  "falta": format_base(conv.qty_base, spec.base), "custo_uso_despensa": 0.0, "custo_uso_compra": round(custo, 2),
                                  "custo_uso_total": round(custo, 2), "compra": compra, "memoria_de_calculo": "; ".join(memoria)})
                    cmv_total += custo
                    custo_compra_total += custo
                    compras.append(compra)
                    compras_total += preco_c
            else:
                linha["pendencia"] = ("não está na despensa: informe preco_compra_estimado, quantidade_compra e unidade_compra "
                                      "(pesquise o preço ou pergunte à Dona Maria)")
                pendencias.append(f"{nome}: {linha['pendencia']}")
        linhas.append(linha)
    return {
        "itens": linhas,
        "cmv_total": round(cmv_total, 2),
        "custo_vindo_da_despensa": round(custo_despensa_total, 2),
        "custo_vindo_de_compras": round(custo_compra_total, 2),
        "compras": compras,
        "compras_total_estimado": round(compras_total, 2),
        "pendencias": pendencias,
        "estimativas": estimativas,
    }


def _orcamento_resumo(store: Store, compras_total: float) -> dict[str, Any]:
    orc = store.orcamento()
    restante = orc["restante"]
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
        status = _equip_status(equip, _equip_key(str(nome)))
        (ok if status == "sim" else faltando if status == "nao" else nao_informado).append(str(nome))
    return {"ok": ok, "faltando": faltando, "nao_informado": nao_informado}


def _checar_tecnicas(perfil: dict[str, Any], necessarias: list[str]) -> dict[str, list[str]]:
    tec = perfil.get("tecnicas") or {}
    ok, faltando, parcial, nao_informado = [], [], [], []
    for nome in necessarias:
        status = _tecnica_status(tec, str(nome))
        {"sim": ok, "nao": faltando, "parcial": parcial}.get(status, nao_informado).append(str(nome))
    return {"ok": ok, "faltando": faltando, "parcial": parcial, "nao_informado": nao_informado}


def _restricoes_relevantes(perfil: dict[str, Any], tempo_preparo_min: Optional[float]) -> list[str]:
    avisos = []
    restr = perfil.get("restricoes") or {}
    limite = _num(restr.get("tempo_por_cozinhada_min"))
    if tempo_preparo_min and limite and tempo_preparo_min > limite:
        avisos.append(f"a receita leva ~{tempo_preparo_min:g} min e Dona Maria disse ter {limite:g} min por cozinhada")
    return avisos


# ----------------------------------------------------------------------------- veredito e travas
def _bloqueio(tipo: str, detalhe: str, **extra: Any) -> dict[str, Any]:
    return {"tipo": tipo, "detalhe": detalhe, **extra}


def _avaliar(res: dict[str, Any], equip: dict[str, list[str]], tec: dict[str, list[str]], orc: dict[str, Any],
             pend_perfil: list[dict[str, str]]) -> dict[str, Any]:
    """Veredito único, usado pela conferência (2.3), pelo CMV e pelo registro no cardápio (2.4).

    * ``inviavel_por_enquanto``: impedimento conhecido (equipamento ou técnica que ela disse não ter,
      compras que não cabem no orçamento).
    * ``precisa_confirmar``: falta resposta (checklist do perfil, equipamento/técnica não informados,
      pendência de ingrediente).
    * ``viavel``: nada pendente. Só este veredito libera ``prato_calcular_cmv`` e ``cardapio_registrar``.
    """
    impedimentos: list[dict[str, Any]] = []
    for e in equip["faltando"]:
        impedimentos.append(_bloqueio("equipamento_faltando", f"{e}: Dona Maria disse que não tem"))
    for t in tec["faltando"]:
        impedimentos.append(_bloqueio("tecnica_faltando", f"{t}: Dona Maria disse que não domina"))
    if not orc["cabe_no_orcamento"]:
        impedimentos.append(_bloqueio("orcamento", f"as compras deste prato ({costing.brl(orc['compras_desta_receita'])}) "
                                                   f"não cabem nos {costing.brl(orc['restante'])} restantes do orçamento"))
    confirmacoes: list[dict[str, Any]] = []
    for p in res["pendencias"]:
        confirmacoes.append(_bloqueio("ingrediente_pendente", p))
    for e in equip["nao_informado"]:
        confirmacoes.append(_bloqueio("equipamento_nao_informado", f"{e}: ela ainda não disse se tem"))
    for t in tec["nao_informado"]:
        confirmacoes.append(_bloqueio("tecnica_nao_informada", f"{t}: ela ainda não disse se domina"))
    for t in tec["parcial"]:
        confirmacoes.append(_bloqueio("tecnica_parcial", f"{t}: ela disse que domina só em parte; confirme se dá conta nesta receita"))
    for p in pend_perfil:
        confirmacoes.append(_bloqueio("perfil_pendente", p["pergunta"], chave=p["chave"]))
    if impedimentos:
        veredito = "inviavel_por_enquanto"
    elif confirmacoes:
        veredito = "precisa_confirmar"
    else:
        veredito = "viavel"
    proximos: list[str] = []
    if impedimentos:
        proximos.append("explique o impedimento e proponha saída: substituir equipamento/técnica, reduzir porções ou outra receita")
    if pend_perfil:
        proximos.append("perguntar à Dona Maria os itens de perfil pendentes (clarify) e salvar com perfil_cozinha_salvar")
    if equip["nao_informado"] or tec["nao_informado"] or tec["parcial"]:
        proximos.append("confirmar com ela os equipamentos/técnicas marcados como nao_informado ou parcial e salvar com perfil_cozinha_salvar")
    if res["pendencias"]:
        proximos.append("resolver as pendências de ingredientes (preço de compra, peso de embalagem, quantidade)")
    return {"veredito": veredito, "bloqueios": impedimentos + confirmacoes, "proximos_passos": proximos}


def _somar_cozinha(perfil: dict[str, Any], nome: str, ingredientes: list[Any], equip: dict[str, list[str]],
                   tec: dict[str, list[str]]) -> dict[str, Any]:
    """Soma ao que o modelo leu na receita o que a base de conhecimento exige do prato e dos ingredientes
    (prompts/base-de-conhecimento/cozinha-e-equipamentos): o modelo esquece de listar o forno da pizza, a ficha não.
    Muda ``equip`` e ``tec`` no lugar, sem repetir o que o modelo já listou, e devolve a conferência (com as frases prontas)."""
    nomes = [str((i.get("nome") if isinstance(i, dict) else i) or "") for i in ingredientes]
    c = cozinha.conferir(nome, nomes, perfil)
    ja_equip = {_equip_key(n) for lista in equip.values() for n in lista}
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


def _requisitos(receita: dict[str, Any]) -> dict[str, Any]:
    dados = receita.get("dados") or {}
    return {
        "equipamentos_necessarios": [str(x) for x in (dados.get("equipamentos_necessarios") or [])],
        "tecnicas_necessarias": [str(x) for x in (dados.get("tecnicas_necessarias") or [])],
        "tempo_preparo_min": _num(dados.get("tempo_preparo_min")),
    }


def _avaliar_prato(store: Store, ingredientes: list[Any], receita: dict[str, Any]) -> dict[str, Any]:
    """Reavalia um prato contra o estado ATUAL: perfil salvo, despensa disponível (estoque menos reservas
    de outros pratos aceitos) e orçamento restante. É o que torna a trava independente do que o modelo diz."""
    req = _requisitos(receita)
    res = _resolver_ingredientes(store, ingredientes)
    perfil = store.perfil()
    equip = _checar_equipamentos(perfil, req["equipamentos_necessarios"])
    tec = _checar_tecnicas(perfil, req["tecnicas_necessarias"])
    da_cozinha = _somar_cozinha(perfil, str(receita.get("nome") or ""), ingredientes, equip, tec)
    orc = _orcamento_resumo(store, res["compras_total_estimado"])
    aval = _avaliar(res, equip, tec, orc, _pendencias_perfil(perfil))
    aval.update({"resultado": res, "equipamentos": equip, "tecnicas": tec, "orcamento": orc, "requisitos": req, "cozinha": da_cozinha,
                 "avisos_operacionais": _restricoes_relevantes(perfil, req["tempo_preparo_min"])})
    return aval


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
    alternativa = alternativa_no_estoque(Store() if store.ignorar_prato_id else store, receita, _avaliar_prato)
    aviso = f"O prato {nome} ainda não foi aprovado. Motivo: {motivo}."
    cozinha_aviso = (aval.get("cozinha") or {}).get("mensagens") or []
    if cozinha_aviso:
        aviso += " " + cozinha_aviso[0]
    if alternativa:
        aviso += (f" {alternativa['nome']} cabe no estoque e na cozinha para {alternativa['porcoes']:g} porções, "
                  f"sem compras. Quer trocar «{nome}» por «{alternativa['nome']}»?")
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
@_tool
def pratos_selecionar(args: dict, session_id: Optional[str] = None) -> dict:
    falas_dela = falas.da_sessao(session_id) or []
    fala = falas_dela[-1] if falas_dela else None
    opcoes = escolhas.escolhidas(fala.texto, escolhas.lista_da_escolha(falas_dela)) if fala else []
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
    return {"success": True, "pratos_escolhidos": pratos,
            "diga_a_dona_maria": "As escolhas estão em Minha cozinha › Pratos. Abra Continuar no chat para montar a ficha de cada prato."}


@_tool
def receita_conferir_ingredientes(args: dict, session_id: Optional[str] = None) -> dict:
    store = Store()
    prato_id = args.get("prato_id")
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
    if prato_id is not None:
        escolhido = store.obter_prato(int(prato_id))
        if escolhido is None:
            raise KeyError(f"prato_id {prato_id} não encontrado")
        if norm_text(nome) not in {"", "receita sem nome", "prato sem nome", norm_text(escolhido["nome"])}:
            raise ValueError(f"use o nome do prato escolhido {escolhido['nome']!r} ou deixe que o sistema o recupere pelo número")
        nome = escolhido["nome"]
        fonte_url = fonte_url or (escolhido["dados"].get("selecao") or {}).get("fonte_url")
    if not nome or norm_text(nome) in {"receita sem nome", "prato sem nome"}:
        raise ValueError("informe o nome da receita ou o número do prato escolhido para guardar a ficha certa")
    ingredientes = args.get("ingredientes") or []
    if not isinstance(ingredientes, list) or not ingredientes:
        raise ValueError("informe 'ingredientes' como lista de {nome, quantidade, unidade}")
    porcoes = _num(args.get("porcoes"))
    res = _resolver_ingredientes(store, ingredientes)
    perfil = store.perfil()
    equip = _checar_equipamentos(perfil, list(args.get("equipamentos_necessarios") or []))
    tec = _checar_tecnicas(perfil, list(args.get("tecnicas_necessarias") or []))
    da_cozinha = _somar_cozinha(perfil, nome, ingredientes, equip, tec)
    avisos = _restricoes_relevantes(perfil, _num(args.get("tempo_preparo_min")))
    orc = _orcamento_resumo(store, res["compras_total_estimado"])
    pend_perfil = _pendencias_perfil(perfil)
    ja_tem = [l["ingrediente"] for l in res["itens"] if l.get("item_despensa") and l.get("falta") == "nada"]
    parcial = [l["ingrediente"] for l in res["itens"] if l.get("item_despensa") and l.get("falta") not in (None, "nada")]
    nao_tem = [l["ingrediente"] for l in res["itens"] if not l.get("item_despensa")]
    aval = _avaliar(res, equip, tec, orc, pend_perfil)
    veredito = aval["veredito"]
    dados = {"ingredientes": ingredientes, "resultado": res, "equipamentos": equip, "tecnicas": tec, "veredito": veredito,
             "bloqueios": aval["bloqueios"], "prato_id": prato_id,
             "equipamentos_necessarios": args.get("equipamentos_necessarios"), "tecnicas_necessarias": args.get("tecnicas_necessarias"),
             "tempo_preparo_min": args.get("tempo_preparo_min"), "sessao_da_conferencia": session_id}
    receita_id = store.salvar_receita(nome, fonte_url, porcoes, dados, "conferida",
                                      prato_id=int(prato_id) if prato_id is not None else None)
    aviso = (_aviso_prato_nao_aprovado(store, {"id": receita_id, "nome": nome, "porcoes": porcoes, "dados": dados},
                                      {**aval, "cozinha": da_cozinha})
             if veredito != "viavel" else {})
    texto_conferencia = (f"Ficha de {nome} conferida. Compras complementares previstas: {costing.brl(res['compras_total_estimado'])}. "
                         f"Saldo atual para compras: {costing.brl(orc['saldo_atual'])}.")
    if not res["pendencias"]:
        texto_conferencia += f" Custo dos ingredientes usados no lote: {costing.brl(res['cmv_total'])}."
    proximos = list(aval["proximos_passos"])
    if da_cozinha["mensagens"]:
        proximos.insert(0, "conte a ela, com as palavras de 'avisos_da_cozinha', o que a cozinha dela não faz ou ainda não confirmou")
    if veredito == "viavel":
        ref = f"prato #{prato_id}, {nome}," if prato_id is not None else nome
        proximos.append(f"mostre a conferência e pergunte: 'Você aceita preparar {ref} nessa versão?' Só avance após o aceite dela")
    else:
        proximos.append(f"resolvidos os bloqueios, chamar prato_calcular_cmv com receita_id={receita_id} (ele reavalia sozinho; não precisa conferir de novo)")
    return {
        "success": True,
        "receita_id": receita_id,
        "receita": nome,
        "prato_id": prato_id,
        "compras_para_confirmar": compras.mostrar(compras.da_conferencia(res)),
        "porcoes": porcoes,
        "veredito": veredito,
        "bloqueios": aval["bloqueios"],
        "ingredientes": res["itens"],
        "resumo": {
            "ja_tem_tudo_que_precisa": ja_tem,
            "tem_mas_nao_o_suficiente": parcial,
            "nao_tem": nao_tem,
            "precisa_comprar": res["compras"],
            "compras_total_estimado": res["compras_total_estimado"],
            "orcamento": orc,
            "cmv_previa": None if res["pendencias"] else {
                "total": res["cmv_total"], "por_porcao": round(res["cmv_total"] / porcoes, 2) if porcoes else None},
        },
        "equipamentos": equip,
        "tecnicas": tec,
        "avisos_da_cozinha": da_cozinha["mensagens"],
        "avisos_operacionais": avisos,
        "perfil_pendente": pend_perfil,
        "pendencias": res["pendencias"],
        "estimativas": res["estimativas"],
        "proximos_passos": proximos,
        "diga_a_dona_maria": texto_conferencia + (" " + aviso["diga_a_dona_maria"] if aviso else ""),
        **({"alternativa_no_estoque": aviso["alternativa_no_estoque"]} if aviso else {}),
    }


# ----------------------------------------------------------------------------- 2.4 CMV e preço
@_tool
def prato_calcular_cmv(args: dict) -> dict:
    store = Store()
    receita_id = args.get("receita_id")
    prato_id = args.get("prato_id")
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
    # Trava: reavalia contra o estado atual. Só 'viavel' libera o CMV ("nunca pule a etapa 2 para chegar ao preço").
    aval = _avaliar_prato(store, ingredientes, receita)
    if aval["veredito"] != "viavel":
        return _recusa(
            f"CMV bloqueado: veredito {aval['veredito']} com {len(aval['bloqueios'])} pendência(s); resolva os bloqueios e chame de novo com o mesmo receita_id",
            aval, receita_id=int(receita_id), **_aviso_prato_nao_aprovado(store, receita, aval))
    res = aval["resultado"]
    cmv_total = res["cmv_total"]
    cmv_porcao = round(cmv_total / porcoes, 2)
    orc = aval["orcamento"]
    memoria = [f"{l['ingrediente']}: {l['memoria_de_calculo']}" for l in res["itens"] if l.get("memoria_de_calculo")]
    memoria.append(f"CMV da receita = soma = {costing.brl(cmv_total)}")
    memoria.append(f"CMV por porção = {costing.brl(cmv_total)} ÷ {porcoes:g} porções = {costing.brl(cmv_porcao)}")
    dados = {"ingredientes": ingredientes, "resultado": res, "memoria_de_calculo": memoria,
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
        "preco_minimo_por_porcao": costing.preco_minimo_centavo(cmv_total / porcoes, perfil=store.perfil_precificacao()),
        "pendencias": res["pendencias"],
        "estimativas": res["estimativas"],
        "itens": res["itens"],
        "veredito": aval["veredito"],
        "avisos_operacionais": aval["avisos_operacionais"],
        "reservas_previstas": _reservas_de(res, legivel=True),
        "proximo_passo": (f"chame preco_sugerir com prato_id={prato_id}, aceite_prato e as opções da consultora para salvar 2–3 cenários; só cardapio_registrar "
                          "(com o preço que ela escolher) reserva o estoque e compromete o orçamento"),
    }


@_tool
def preco_sugerir(args: dict, session_id: Optional[str] = None) -> dict:
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
        cmv = resultado_atual["cmv_total"] / porcoes_atual
    avisos: list[str] = []
    informado = _num(args.get("cmv_por_porcao"))
    if informado is not None and abs(informado - round(cmv, 2)) > 0.005:
        avisos.append(f"cmv_por_porcao informado ({costing.brl(informado)}) difere do calculado pela tool ({costing.brl(cmv)}); os cenários usam o calculado")
    perfil = store.perfil_precificacao()
    comissao, pagamento, _ = taxas.componentes(perfil)
    taxa = round(comissao + pagamento, 12)
    if _num(args.get("taxa_plataforma"), taxa) not in (taxa, taxa * 100):
        avisos.append("Usei as taxas configuradas em Analytics.")
    if perfil["imposto"] is None:
        avisos.append("Impostos pendentes no Analytics. Os valores são provisórios.")
    passo = _num(args.get("arredondar_para"), 0.5)
    if not math.isfinite(passo) or passo < 0.01 or round(passo, 2) != passo:
        raise ValueError("arredondar_para deve ser um valor positivo em centavos inteiros")

    falas_dela = falas.da_sessao(session_id) or []
    aceite = args.get("aceite_prato")
    confirmado = precificacao.confirmou_prato(aceite, prato["nome"], falas_dela, prato_id=int(prato_id))
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
        store.salvar_analise_preco(int(prato_id), saida)
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
                       + ("Diga que a projeção ainda depende da alíquota de impostos. " if perfil["imposto"] is None else "")
                       + "Apresente os valores calculados dos cenários. Quando houver volume para uma meta, diga que ele inclui os custos fixos informados "
                       "e depende de vender as porções. "
                       + ("Diga que a análise foi salva em Analytics. " if confirmado else "")
                       + "Pergunte qual preço ela quer adotar; só depois registre com cardapio_registrar."),
    })
    return saida


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
        if not fala or escolhas.preco_para_registrar(fala.texto) != preco or not escolhas.pediu_registro_do_prato(
            fala.texto, fala.resposta_a, prato_id, prato["nome"]
        ):
            return _recusa("o preço de venda ainda não foi confirmado para este prato", {
                "bloqueios": [_bloqueio("sem_escolha_de_preco", "aguarde a escolha do preço e o pedido de registro no cardápio")],
                "proximos_passos": [f"mostre os cenários e pergunte qual preço ela escolhe para registrar o prato #{prato_id}"]})
    ingredientes = prato["dados"].get("ingredientes") or []
    # Trava 1: o estado ATUAL (perfil, despensa disponível, orçamento) precisa estar 'viavel'. O que o modelo
    # escreveu na conversa não conta; só o que foi salvo com perfil_cozinha_salvar / despensa_atualizar.
    aval = _avaliar_prato(store, ingredientes, receita)
    if aval["veredito"] != "viavel":
        return _recusa(f"registro bloqueado: veredito {aval['veredito']}; o cardápio só aceita prato sem pendência, "
                       "mesmo que a conversa diga que está tudo certo", aval, **_aviso_prato_nao_aprovado(store, receita, aval))
    res = aval["resultado"]
    porcoes = float(prato.get("porcoes") or 0)
    cmv_porcao = round(res["cmv_total"] / porcoes, 2) if porcoes else None
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
             "proximos_passos": ["peça que ela revise e escolha o preço atualizado em Analytics, ou apresente novamente o CMV e os cenários no chat"]},
            antes={"cmv_por_porcao": cmv_antes, "compras_total": compras_antes},
            agora={"cmv_por_porcao": cmv_porcao, "compras_total": res["compras_total_estimado"]})
    ja_registrado = prato["status"] == "aceito"
    dados = {**prato["dados"], "resultado": res,
             "avaliacao": {k: aval[k] for k in ("veredito", "equipamentos", "tecnicas", "orcamento", "requisitos")},
             "registro": {"preco_escolhido": preco, "porcoes": porcoes, "cmv_por_porcao": cmv_porcao}}
    confirmado = store.aceitar_prato(prato_id, preco, res["compras"], _reservas_de(res),
                                     confirmar_compras=bool(args.get("confirmar_compras", True)), dados=dados,
                                     cmv_total=res["cmv_total"], cmv_porcao=cmv_porcao)
    # As compras da receita agora são compromisso do prato: saem do carrinho para o mesmo gasto não contar duas vezes.
    baixados = store.carrinho_baixar_receita(int(receita["id"]))
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
        "nota": ("Estoque reservado. Se ela fizer as compras complementares deste prato, registre os valores pagos com "
                 "despensa_registrar_compras e o compra_id correspondente." if res["compras"] else
                 "Estoque reservado. Este prato não exige compras complementares e o saldo de compras não mudou."),
    }


def avancar_cardapio_no_turno(store: Store, session_id: str, falas_do_turno: list[falas.Fala]) -> Optional[str]:
    """Usa o aceite ou o preço da própria usuária para avançar a ficha já gravada."""
    if not falas_do_turno:
        return None
    fala = falas_do_turno[-1]
    pratos = store.pratos_em_andamento()
    preco = escolhas.preco_para_registrar(fala.texto)
    if preco is not None:
        alvos = [p for p in pratos if escolhas.pediu_registro_do_prato(fala.texto, fala.resposta_a, p["id"], p["nome"])]
        if len(alvos) != 1:
            return "Ela informou um preço para registro. Pergunte qual prato deve receber esse preço."
        prato = alvos[0]
        registro = json.loads(cardapio_registrar({"prato_id": prato["id"], "preco_escolhido": preco}, session_id=session_id))
        if registro["success"]:
            return (f"Registro concluído no cardápio. Confirme o prato #{prato['id']}, {prato['nome']}, por {costing.brl(preco)} por porção. "
                    f"{registro['diga_a_dona_maria']}. O estoque do prato foi reservado pelo registro.")
        return "O registro ficou pendente. Explique este resultado: " + _motivo_pendente(registro)

    alvos = [p for p in pratos if p["status"] != "aceito" and p.get("receita_id")
             and precificacao.aceite_da_receita(p["id"], p["nome"], falas_do_turno)]
    if len(alvos) != 1:
        return None
    prato = alvos[0]
    if prato["status"] == "selecionado":
        cmv = json.loads(prato_calcular_cmv({"prato_id": prato["id"]}))
        if not cmv["success"]:
            return "Ela já confirmou a versão. Explique a pendência que falta resolver: " + _motivo_pendente(cmv)
    anterior = next((a for a in store.analises_preco() if a["prato_id"] == prato["id"]), None)
    opcoes: dict[str, Any] = {"prato_id": prato["id"]}
    if anterior:
        dados = anterior["dados"]
        opcoes.update(margens=[c["margem_alvo"] for c in dados["cenarios"]], nomes=[c["nome"] for c in dados["cenarios"]],
                      cenario_recomendado=dados["cenario_recomendado"], recomendacao=dados["recomendacao"],
                      lucro_alvo=dados.get("lucro_alvo"), custos_fixos=dados.get("custos_fixos"))
        if dados.get("arredondar_para") is not None:
            opcoes["arredondar_para"] = dados["arredondar_para"]
    resultado = json.loads(preco_sugerir(opcoes, session_id=session_id))
    if not resultado.get("analise_salva"):
        return "Ela já confirmou a versão. Explique a pendência que falta resolver: " + _motivo_pendente(resultado)
    precos = "; ".join(f"{c['nome']} {costing.brl(c['preco_sugerido'])} (sobra {costing.brl(c['com_preco_sugerido']['lucro'])})"
                       for c in resultado["cenarios"])
    return (f"Aceite já confirmado. A análise do prato #{prato['id']}, {prato['nome']}, está salva em Analytics. "
            f"CMV por porção {costing.brl(resultado['cmv_por_porcao'])}, preço mínimo {costing.brl(resultado['preco_minimo'])}. "
            f"Cenários por porção após os descontos considerados e o custo dos ingredientes: {precos}. "
            + ("A projeção ainda depende da alíquota de impostos configurada em Analytics. " if resultado["taxa_impostos"] is None else "")
            + f"Saldo atual para compras {costing.brl(store.carrinho_resumo()['saldo'])}. "
            "Apresente esses dados e pergunte só qual preço ela escolhe para registrar no cardápio.")


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
        resumo = {"id": p["id"], "nome": p["nome"], "porcoes": p["porcoes"], "cmv_por_porcao": p["cmv_porcao"], "preco_venda": p["preco_venda"], "status": p["status"]}
        if p["status"] == "aceito" and p["preco_venda"]:
            ficha = precificacao.ficha_atual(store, p)
            if not isinstance(ficha, str):
                porcoes, resultado = ficha
                avaliacao = costing.avaliar_preco(p["preco_venda"], resultado["cmv_total"] / porcoes, perfil=perfil_precificacao)
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
def _texto_saldo(resumo: dict[str, Any]) -> str:
    """Frase pronta para a consultora repetir: o que ela tem, o que já escolheu e o que sobra."""
    partes = [f"orçamento {costing.brl(resumo['inicial'])}"]
    if resumo["gasto_confirmado"]:
        partes.append(f"já pago {costing.brl(resumo['gasto_confirmado'])}")
    if resumo["reservado_cardapio"]:
        partes.append(f"reservado para compras de pratos {costing.brl(resumo['reservado_cardapio'])}")
    partes.append(f"no carrinho {costing.brl(resumo['carrinho'])}")
    partes.append(f"saldo {costing.brl(resumo['saldo'])}")
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
    da_despensa = []
    for linha in res["itens"]:  # o que ela já tem entra no prato com o custo, sem gastar saldo
        if linha.get("item_id") is None or (linha.get("usa_da_despensa_base") or 0) <= 1e-9:
            continue
        qtd, un = _legivel_base(float(linha["usa_da_despensa_base"]), linha["unidade_base"])
        da_despensa.append({"ingrediente": linha["ingrediente"], "quantidade": qtd, "unidade": un, "valor": linha.get("custo_uso_despensa") or 0.0})
    if not res["compras"] and not da_despensa:
        return _carrinho_resposta(store, receita=receita["nome"], nota="nada a comprar: ela já tem tudo na despensa")
    try:
        store.carrinho_montar_da_receita(int(receita["id"]), receita["nome"], res["compras"], da_despensa)
    except OrcamentoEstourado as exc:
        return _recusa_saldo(exc, store)
    grupo = next(g for g in store.carrinho_pratos() if g["prato"] == receita["nome"][:80].strip())
    resumo = store.carrinho_resumo()
    return _carrinho_resposta(store, receita=receita["nome"], prato=grupo, diga_a_dona_maria=_texto_prato(grupo) + "\n" + _texto_saldo(resumo))


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
                                         f"{lista}. \"Cobrar a partir de\" cobre o custo de exemplo após os descontos configurados em Analytics. "
                                         + ("A projeção ainda depende da alíquota de impostos. " if perfil_precificacao["imposto"] is None else "")
                                         + "Com o custo real da receita o preço muda.")
    return resposta


# ----------------------------------------------------------------------------- schemas (formato OpenAI function)
_ING_SCHEMA = {
    "type": "array",
    "description": "Ingredientes da receita com quantidades. Use as medidas da receita (g, kg, ml, L, un, colher de sopa, colher de chá, xícara, dente, fatia, folha, maço...).",
    "items": {
        "type": "object",
        "properties": {
            "nome": {"type": "string", "description": "Nome como está na receita (ex.: 'queijo ralado', 'cebola média')"},
            "quantidade": {"type": "number", "description": "Quantidade numérica. Para 'a gosto', estime (ex.: sal 5 g)."},
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

SCHEMAS: dict[str, dict[str, Any]] = {
    "despensa_listar": {
        "name": "despensa_listar",
        "description": "Lista a despensa da Dona Maria (planilha despensa_dona_maria.xlsx): estoque, unidade normalizada (kg/L/un), custo unitário (preço pago ÷ quantidade comprada), preço pago e o orçamento restante para compras. É a ÚNICA fonte para 'quanto custa', 'quanto tem' ou 'tem na despensa?' de qualquer ingrediente: nunca use web_search para isso. Use no início da conversa e sempre que precisar de custos.",
        "parameters": {"type": "object", "properties": {
            "filtro": {"type": "string", "description": "Opcional: trecho do nome para filtrar (ex.: 'queijo'); vários separados por vírgula (ex.: 'frango, batata') trazem os que casam com qualquer um"},
            "recarregar": {"type": "boolean", "description": "Opcional: true recarrega a planilha do zero (descarta ajustes feitos na despensa)"},
        }},
    },
    "despensa_buscar": {
        "name": "despensa_buscar",
        "description": "Responde 'a Dona Maria tem X?' e 'quanto custa X na despensa?' para um ou mais ingredientes, com casamento aproximado e sinônimos (cheiro-verde→salsinha, fubá→polenta, queijo ralado→parmesão...). Devolve estoque e custo unitário de cada candidato com score; confirme com ela quando 'confiavel' for false. Para custo ou estoque de ingrediente use esta tool, não web_search (a internet não conhece a despensa dela).",
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
        "description": "Mostra a cozinha da Dona Maria (equipamentos e utensílios, técnicas, restrições operacionais: o que ela contou na conversa e o que marcou na tela Minha cozinha, que é o mesmo registro), as perguntas ainda pendentes e o texto delas já pronto (perguntas_prontas). Chame antes de aprovar qualquer prato: pergunte o que estiver pendente copiando perguntas_prontas em texto (ou com clarify, se você tiver essa tool).",
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
        "description": "Confere quantidades da receita contra despensa, cozinha e orçamento. Passe prato_id ou o nome da receita. Use diga_a_dona_maria para o custo e o saldo, que são distintos. Confira apenas as quantidades que a receita usa. Quando houver impedimento, apresente o motivo e aguarde confirmação antes de trocar.",
        "parameters": {"type": "object", "properties": {
            "prato_id": {"type": "integer", "description": "id do prato escolhido que esta conferência está montando, se houver"},
            "nome": {"type": "string", "description": "Nome da receita"},
            "fonte_url": {"type": "string", "description": "URL de onde a receita foi tirada"},
            "porcoes": {"type": "number", "description": "Quantas porções a receita rende"},
            "ingredientes": _ING_SCHEMA,
            "equipamentos_necessarios": {"type": "array", "items": {"type": "string"}, "description": "ex.: ['forno', 'panela de pressão', 'liquidificador']"},
            "tecnicas_necessarias": {"type": "array", "items": {"type": "string"}, "description": "ex.: ['massa fresca', 'molho branco', 'ponto da carne']"},
            "tempo_preparo_min": {"type": "number", "description": "Tempo total de preparo em minutos, se a receita informar"},
        }, "required": ["ingredientes"]},
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
        "description": "Usa o CMV e as taxas configuradas em Analytics para sugerir preços. A ferramenta lê o aceite direto da conversa e salva a análise no Analytics depois da confirmação. Se o painel já trouxer a análise salva, use os cenários atualizados do painel. Quando impostos estiverem pendentes, apresente a projeção como provisória. Pergunte qual preço ela quer adotar para registrar o prato.",
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
        "description": "Registra a decisão da Dona Maria: prato aceito com o preço escolhido por ela. Reavalia o prato contra o estado atual e RECUSA (bloqueado=true, com 'bloqueios') se faltar requisito de equipamento/técnica/perfil, se houver pendência de ingrediente, se as compras não couberem no orçamento ou se CMV/compras mudaram desde a apresentação. Quando aceita, numa operação só: reserva na despensa o que o prato usa e compromete as compras no orçamento (abate dos R$ restantes). Repetir para o mesmo prato não duplica nada. Chame só depois que ela escolher o preço explicitamente.",
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
    "carrinho_consultar": carrinho_consultar,
    "carrinho_adicionar": carrinho_adicionar,
    "carrinho_adicionar_prato": carrinho_adicionar_prato,
    "carrinho_remover": carrinho_remover,
    "carrinho_montar_da_receita": carrinho_montar_da_receita,
    "salvos_salvar": salvos_salvar,
    "analytics_analises_consultar": analytics_analises_consultar,
}
