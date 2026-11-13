"""Tools do plugin ``sabor_da_maria`` expostas ao Hermes Agent.

Cada handler recebe ``args: dict`` e devolve uma string JSON (contrato do
``ctx.register_tool``). Erros nunca estouram: voltam como ``{"success": false,
"error": ...}`` para o modelo decidir o que fazer.

As tools cobrem as quatro etapas do desafio:

* 2.1/2.3  despensa_listar, despensa_buscar, despensa_atualizar, receita_conferir_ingredientes
* 2.2      perfil_cozinha_consultar, perfil_cozinha_salvar (elicitação com checklist)
* 2.4      prato_calcular_cmv, preco_sugerir, cardapio_registrar, cardapio_consultar
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Any, Callable, Optional

from . import costing
from .matching import tokens
from .store import Store
from .units import format_base, norm_text, parse_pantry_unit, to_base

logger = logging.getLogger(__name__)
TOOLSET = "sabor_da_maria"


# ----------------------------------------------------------------------------- infra
def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _tool(fn: Callable[[dict], dict]) -> Callable[..., str]:
    @functools.wraps(fn)
    def wrapper(args: Optional[dict] = None, **kwargs: Any) -> str:
        try:
            return _json(fn(dict(args or {})))
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
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "sim" if value else "nao"
    if isinstance(value, (int, float)):
        return "sim" if value > 0 else "nao"
    text = norm_text(str(value))
    if text in {"nao", "n", "no", "false", "nenhum", "nenhuma", "nao tem", "quebrado", "quebrada", "nao funciona", "0", "nunca fiz", "nunca"}:
        return "nao"
    if any(p in text for p in ("mais ou menos", "aprend", "parcial", "pouco", "as vezes", "razoavel", "basico", "ja tentei")):
        return "parcial"
    return "sim"


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

_EQUIPAMENTOS: list[tuple[str, tuple[str, ...]]] = [
    ("fogao", ("fogao", "boca", "bocas", "cooktop")),
    ("forno", ("forno",)),
    ("panela_pressao", ("pressao",)),
    ("air_fryer", ("air fryer", "airfryer", "fritadeira eletrica", "fritadeira sem oleo", "fritadeira a ar")),
    ("liquidificador", ("liquidificador",)),
    ("batedeira", ("batedeira",)),
    ("micro_ondas", ("micro ondas", "microondas")),
    ("freezer", ("freezer", "congelador")),
    ("geladeira", ("geladeira",)),
    ("processador", ("processador", "mixer", "multiprocessador")),
    ("frigideira", ("frigideira",)),
    ("panela_grande", ("panela grande", "caldeirao", "panelao")),
    ("forma", ("forma", "assadeira", "travessa", "refratario", "tabuleiro")),
    ("escorredor", ("escorredor",)),
    ("ralador", ("ralador",)),
    ("balanca", ("balanca",)),
    ("espremedor", ("espremedor",)),
    ("peneira", ("peneira",)),
    ("rolo", ("rolo de massa", "rolo")),
]


def _equip_key(nome: str) -> str:
    n = norm_text(nome)
    for key, needles in _EQUIPAMENTOS:
        if any(x in n for x in needles):
            return key
    return n.replace(" ", "_")


def _equip_status(equipamentos: dict[str, Any], key: str) -> Optional[str]:
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
    return None


def _pendencias_perfil(perfil: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for grupo, chave, pergunta in CHECKLIST:
        valores = perfil.get(grupo) or {}
        if _equip_status(valores, chave) is None if grupo == "equipamentos" else chave not in valores:
            out.append({"grupo": grupo, "chave": chave, "pergunta": pergunta})
    if not perfil.get("tecnicas"):
        out.append({"grupo": "tecnicas", "chave": "tecnicas",
                    "pergunta": "Quais técnicas você domina bem (ex.: massa fresca, molho branco, ponto de carne, fritura, assados)?"})
    return out


@_tool
def perfil_cozinha_consultar(args: dict) -> dict:
    store = Store()
    perfil = store.perfil()
    pend = _pendencias_perfil(perfil)
    return {
        "success": True,
        "perfil": perfil,
        "pendencias": pend,
        "completo": not pend,
        "orientacao": ("Antes de aceitar qualquer prato, pergunte o que está em 'pendencias' usando a tool clarify "
                       "(até 5 perguntas por vez, com opções). Salve as respostas com perfil_cozinha_salvar."
                       if pend else "Perfil completo para os itens essenciais; pergunte técnicas específicas conforme a receita."),
    }


@_tool
def perfil_cozinha_salvar(args: dict) -> dict:
    store = Store()
    partes: dict[str, Any] = {}
    for chave in ("equipamentos", "tecnicas", "restricoes"):
        valor = args.get(chave)
        if isinstance(valor, dict):
            partes[chave] = {(_equip_key(k) if chave == "equipamentos" else k): v for k, v in valor.items()}
        elif isinstance(valor, list):
            partes[chave] = {(_equip_key(str(k)) if chave == "equipamentos" else str(k)): True for k in valor}
    if args.get("observacoes"):
        partes["observacoes"] = args["observacoes"]
    perfil = store.perfil_salvar(partes)
    pend = _pendencias_perfil(perfil)
    return {"success": True, "perfil": perfil, "pendencias": pend, "completo": not pend}


# ----------------------------------------------------------------------------- despensa (2.3)
@_tool
def despensa_listar(args: dict) -> dict:
    store = Store()
    carga = store.ensure_loaded(force=bool(args.get("recarregar")))
    filtro = norm_text(str(args.get("filtro") or ""))
    itens = []
    total_pago = 0.0
    for item in store.ingredientes():
        total_pago += item.get("preco_total") or 0
        if filtro and filtro not in item["nome_norm"]:
            continue
        itens.append({
            "ingrediente": item["nome"],
            "estoque": item["estoque_legivel"],
            "unidade_base": item["unidade_base"],
            "custo_unitario": round(item["custo_unitario_base"], 4) if item.get("custo_unitario_base") is not None else None,
            "custo_unitario_legivel": item["custo_unitario_legivel"],
            "preco_pago": item.get("preco_total"),
            "unidade_na_planilha": item.get("unidade_original"),
            "observacao": item.get("observacao"),
        })
    return {
        "success": True,
        "planilha": carga.get("origem"),
        "total_itens": len(itens),
        "valor_total_pago": round(total_pago, 2),
        "orcamento": store.orcamento(),
        "itens": itens,
        "nota": "custo_unitario = preço total pago ÷ quantidade comprada, já convertido para kg, L ou un.",
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
def despensa_atualizar(args: dict) -> dict:
    store = Store()
    nome = str(args.get("ingrediente") or "").strip()
    if not nome:
        raise ValueError("informe 'ingrediente'")
    alteracoes = []
    item = None
    conteudo = args.get("conteudo_por_unidade")
    if isinstance(conteudo, dict):
        item = store.atualizar_conteudo(nome, _num(conteudo.get("quantidade"), 0.0) or 0.0, str(conteudo.get("unidade") or "g"))
        alteracoes.append(f"conteúdo de 1 unidade registrado: {item.get('observacao')}")
    compra = args.get("compra")
    if isinstance(compra, dict):
        item = store.registrar_compra(nome, _num(compra.get("quantidade"), 0.0) or 0.0, str(compra.get("unidade") or "kg"),
                                      _num(compra.get("preco_total"), 0.0) or 0.0, novo=bool(args.get("novo")))
        alteracoes.append("compra lançada no estoque (custo unitário recalculado pela média do que foi pago)")
    if item is None:
        item = store.obter(nome)
        if item is None:
            raise KeyError(f"{nome!r} não está na despensa")
    return {"success": True, "ingrediente": {k: item.get(k) for k in ("nome", "estoque_legivel", "unidade_base", "custo_unitario_legivel", "custo_unitario_base", "conteudo_un_base", "observacao")},
            "alteracoes": alteracoes}


# ----------------------------------------------------------------------------- resolução de ingredientes
def _compra_info(ing: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    return _num(ing.get("preco_compra_estimado")), _num(ing.get("quantidade_compra")), (str(ing["unidade_compra"]) if ing.get("unidade_compra") else None)


def _resolver_ingredientes(store: Store, ingredientes: list[Any]) -> dict[str, Any]:
    linhas: list[dict[str, Any]] = []
    pendencias: list[str] = []
    estimativas: list[str] = []
    compras: list[dict[str, Any]] = []
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
        linha: dict[str, Any] = {"ingrediente": nome, "quantidade_receita": f"{qtd:g} {unidade}".strip(), "opcional": bool(ing.get("opcional"))}
        explicito = "item_despensa" in ing
        alvo = ing.get("item_despensa")
        item = None
        if explicito and alvo:
            item = store.obter(str(alvo)) or store.melhor_item(str(alvo))
            if item is None:
                pendencias.append(f"{nome}: '{alvo}' não existe na despensa; use despensa_buscar")
        elif not explicito:
            item = store.melhor_item(nome)
        if qtd <= 0:
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
            tem = float(item.get("estoque_base") or 0)
            usa = min(necessario, tem)
            falta = max(0.0, necessario - tem)
            custo_unit = item.get("custo_unitario_base")
            linha.update({
                "necessario": format_base(necessario, base), "necessario_base": round(necessario, 4), "unidade_base": base,
                "tem_na_despensa": format_base(tem, base), "usa_da_despensa": format_base(usa, base),
                "falta": format_base(falta, base) if falta > 1e-9 else "nada",
            })
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


# ----------------------------------------------------------------------------- 2.3 conferência
@_tool
def receita_conferir_ingredientes(args: dict) -> dict:
    store = Store()
    nome = str(args.get("nome") or "").strip() or "Receita sem nome"
    ingredientes = args.get("ingredientes") or []
    if not isinstance(ingredientes, list) or not ingredientes:
        raise ValueError("informe 'ingredientes' como lista de {nome, quantidade, unidade}")
    porcoes = _num(args.get("porcoes"))
    res = _resolver_ingredientes(store, ingredientes)
    perfil = store.perfil()
    equip = _checar_equipamentos(perfil, list(args.get("equipamentos_necessarios") or []))
    tec = _checar_tecnicas(perfil, list(args.get("tecnicas_necessarias") or []))
    avisos = _restricoes_relevantes(perfil, _num(args.get("tempo_preparo_min")))
    orc = _orcamento_resumo(store, res["compras_total_estimado"])
    pend_perfil = _pendencias_perfil(perfil)
    ja_tem = [l["ingrediente"] for l in res["itens"] if l.get("item_despensa") and l.get("falta") == "nada"]
    parcial = [l["ingrediente"] for l in res["itens"] if l.get("item_despensa") and l.get("falta") not in (None, "nada")]
    nao_tem = [l["ingrediente"] for l in res["itens"] if not l.get("item_despensa")]
    if equip["faltando"] or tec["faltando"] or not orc["cabe_no_orcamento"]:
        veredito = "inviavel_por_enquanto"
    elif res["pendencias"] or equip["nao_informado"] or tec["nao_informado"] or tec["parcial"] or pend_perfil:
        veredito = "precisa_confirmar"
    else:
        veredito = "viavel"
    dados = {"ingredientes": ingredientes, "resultado": res, "equipamentos": equip, "tecnicas": tec, "veredito": veredito,
             "equipamentos_necessarios": args.get("equipamentos_necessarios"), "tecnicas_necessarias": args.get("tecnicas_necessarias"),
             "tempo_preparo_min": args.get("tempo_preparo_min")}
    receita_id = store.salvar_receita(nome, args.get("fonte_url"), porcoes, dados, "conferida")
    proximos = []
    if pend_perfil:
        proximos.append("perguntar à Dona Maria os itens de perfil pendentes (clarify) e salvar com perfil_cozinha_salvar")
    if equip["nao_informado"] or tec["nao_informado"] or tec["parcial"]:
        proximos.append("confirmar com ela os equipamentos/técnicas marcados como nao_informado ou parcial antes de seguir")
    if res["pendencias"]:
        proximos.append("resolver as pendências de ingredientes (preço de compra, peso de embalagem, quantidade)")
    if veredito == "viavel":
        proximos.append("se ela gostar da receita, chamar prato_calcular_cmv com os mesmos ingredientes e porções")
    return {
        "success": True,
        "receita_id": receita_id,
        "receita": nome,
        "porcoes": porcoes,
        "veredito": veredito,
        "ingredientes": res["itens"],
        "resumo": {
            "ja_tem_tudo_que_precisa": ja_tem,
            "tem_mas_nao_o_suficiente": parcial,
            "nao_tem": nao_tem,
            "precisa_comprar": res["compras"],
            "compras_total_estimado": res["compras_total_estimado"],
            "orcamento": orc,
            "cmv_previa": {"total": res["cmv_total"], "por_porcao": round(res["cmv_total"] / porcoes, 2) if porcoes else None},
        },
        "equipamentos": equip,
        "tecnicas": tec,
        "avisos_operacionais": avisos,
        "perfil_pendente": pend_perfil,
        "pendencias": res["pendencias"],
        "estimativas": res["estimativas"],
        "proximos_passos": proximos,
    }


# ----------------------------------------------------------------------------- 2.4 CMV e preço
@_tool
def prato_calcular_cmv(args: dict) -> dict:
    store = Store()
    receita_id = args.get("receita_id")
    ingredientes = args.get("ingredientes")
    nome = str(args.get("nome") or "").strip()
    porcoes = _num(args.get("porcoes"))
    if receita_id and not ingredientes:
        receita = store.obter_receita(int(receita_id))
        if not receita:
            raise KeyError(f"receita_id {receita_id} não encontrada")
        ingredientes = receita["dados"].get("ingredientes") or []
        nome = nome or receita["nome"]
        porcoes = porcoes or receita.get("porcoes")
    if not isinstance(ingredientes, list) or not ingredientes:
        raise ValueError("informe 'ingredientes' (lista) ou 'receita_id' de uma receita já conferida")
    if not porcoes or porcoes <= 0:
        raise ValueError("informe 'porcoes' (quantas porções a receita rende) para calcular o CMV por porção")
    res = _resolver_ingredientes(store, ingredientes)
    cmv_total = res["cmv_total"]
    cmv_porcao = round(cmv_total / porcoes, 2)
    orc = _orcamento_resumo(store, res["compras_total_estimado"])
    memoria = [f"{l['ingrediente']}: {l['memoria_de_calculo']}" for l in res["itens"] if l.get("memoria_de_calculo")]
    memoria.append(f"CMV da receita = soma = {costing.brl(cmv_total)}")
    memoria.append(f"CMV por porção = {costing.brl(cmv_total)} ÷ {porcoes:g} porções = {costing.brl(cmv_porcao)}")
    dados = {"ingredientes": ingredientes, "resultado": res, "memoria_de_calculo": memoria}
    prato_id = store.salvar_prato(nome or "Prato sem nome", int(receita_id) if receita_id else None, porcoes, cmv_total, cmv_porcao, dados)
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
        "orcamento": orc,
        "memoria_de_calculo": memoria,
        "formula": "CMV = Σ (quantidade usada × custo unitário); custo unitário = preço total pago ÷ quantidade comprada (abas Precos × Despensa)",
        "preco_minimo_por_porcao": round(costing.preco_minimo(cmv_porcao), 2),
        "pendencias": res["pendencias"],
        "estimativas": res["estimativas"],
        "itens": res["itens"],
        "proximo_passo": f"chame preco_sugerir com cmv_por_porcao={cmv_porcao} e prato_id={prato_id} para montar 2–3 cenários",
    }


@_tool
def preco_sugerir(args: dict) -> dict:
    store = Store()
    cmv = _num(args.get("cmv_por_porcao"))
    prato_id = args.get("prato_id")
    prato = store.obter_prato(int(prato_id)) if prato_id else None
    if cmv is None and prato:
        cmv = prato.get("cmv_porcao")
    if cmv is None:
        raise ValueError("informe 'cmv_por_porcao' (ou 'prato_id' de um prato já calculado)")
    taxa = _num(args.get("taxa_plataforma"), costing.TAXA_PADRAO) or costing.TAXA_PADRAO
    if taxa > 1:
        taxa = taxa / 100.0
    margens = args.get("margens")
    if isinstance(margens, list) and margens:
        margens = [m / 100.0 if m > 1 else m for m in (_num(x, 0.0) or 0.0 for x in margens)]
    else:
        margens = None
    saida = costing.cenarios_preco(cmv, taxa, margens, args.get("nomes"), _num(args.get("arredondar_para"), 0.5) or 0.5)
    simulacoes = args.get("precos_para_simular")
    if isinstance(simulacoes, list) and simulacoes:
        saida["simulacoes"] = costing.simular_precos(cmv, [p for p in (_num(x) for x in simulacoes) if p], taxa)
    saida.update({
        "success": True,
        "prato_id": prato_id,
        "prato": prato["nome"] if prato else None,
        "orientacao": ("Explique a taxa de 10%, mostre o preço mínimo e os cenários com a conta aberta, e pergunte qual "
                       "preço a Dona Maria quer adotar. A decisão é dela; depois registre com cardapio_registrar."),
    })
    return saida


@_tool
def cardapio_registrar(args: dict) -> dict:
    store = Store()
    prato_id = args.get("prato_id")
    preco = _num(args.get("preco_escolhido"))
    if not prato_id or preco is None:
        raise ValueError("informe 'prato_id' e 'preco_escolhido'")
    prato = store.obter_prato(int(prato_id))
    if not prato:
        raise KeyError(f"prato_id {prato_id} não encontrado")
    compras = prato["dados"].get("resultado", {}).get("compras", [])
    confirmado = store.aceitar_prato(int(prato_id), preco, compras, confirmar_compras=args.get("confirmar_compras", True))
    avaliacao = costing.avaliar_preco(preco, float(prato["cmv_porcao"] or 0))
    return {
        "success": True,
        "prato": {"id": confirmado["id"], "nome": confirmado["nome"], "porcoes": confirmado["porcoes"], "cmv_por_porcao": confirmado["cmv_porcao"],
                  "preco_venda": confirmado["preco_venda"], "status": confirmado["status"]},
        "resultado_por_porcao": avaliacao,
        "compras_registradas": compras,
        "orcamento": store.orcamento(),
        "nota": "Lembre a Dona Maria de lançar as compras reais com despensa_atualizar(compra=...) quando ela voltar do mercado.",
    }


@_tool
def cardapio_consultar(args: dict) -> dict:
    store = Store()
    pratos = store.pratos()
    aceitos = []
    em_analise = []
    for p in pratos:
        resumo = {"id": p["id"], "nome": p["nome"], "porcoes": p["porcoes"], "cmv_por_porcao": p["cmv_porcao"], "preco_venda": p["preco_venda"], "status": p["status"]}
        if p["status"] == "aceito" and p["preco_venda"]:
            resumo["lucro_por_porcao"] = costing.avaliar_preco(p["preco_venda"], p["cmv_porcao"] or 0)["lucro"]
            aceitos.append(resumo)
        else:
            em_analise.append(resumo)
    perfil = store.perfil()
    return {
        "success": True,
        "cardapio": aceitos,
        "pratos_em_analise": em_analise,
        "receitas_conferidas": store.receitas(),
        "orcamento": store.orcamento(),
        "perfil_resumo": {"equipamentos": perfil.get("equipamentos"), "restricoes": perfil.get("restricoes"), "tecnicas": perfil.get("tecnicas")},
        "perfil_pendente": _pendencias_perfil(perfil),
    }


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
            "filtro": {"type": "string", "description": "Opcional: trecho do nome para filtrar (ex.: 'queijo')"},
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
        "description": "Ajusta um item da despensa: registra o conteúdo de 1 unidade (ex.: cobertura de chocolate = 1,01 kg) para itens vendidos por 'un', ou lança uma compra (quantidade, unidade, preço total) que entra no estoque e recalcula o custo unitário. Use 'novo': true para cadastrar ingrediente que não existia.",
        "parameters": {"type": "object", "properties": {
            "ingrediente": {"type": "string", "description": "Nome do item (como na despensa, ou novo)"},
            "conteudo_por_unidade": {"type": "object", "properties": {"quantidade": {"type": "number"}, "unidade": {"type": "string", "description": "g, kg, ml ou L"}}, "description": "Quanto tem em 1 unidade"},
            "compra": {"type": "object", "properties": {"quantidade": {"type": "number"}, "unidade": {"type": "string"}, "preco_total": {"type": "number"}}, "description": "Compra realizada a lançar no estoque"},
            "novo": {"type": "boolean", "description": "true para cadastrar ingrediente novo junto com a compra"},
        }, "required": ["ingrediente"]},
    },
    "perfil_cozinha_consultar": {
        "name": "perfil_cozinha_consultar",
        "description": "Mostra o que já se sabe da cozinha da Dona Maria (equipamentos, técnicas, restrições operacionais) e a lista de perguntas ainda pendentes do checklist. Chame antes de aprovar qualquer prato: o que estiver pendente deve ser perguntado com a tool clarify.",
        "parameters": {"type": "object", "properties": {}},
    },
    "perfil_cozinha_salvar": {
        "name": "perfil_cozinha_salvar",
        "description": "Salva respostas da Dona Maria sobre a cozinha dela. 'equipamentos' aceita chaves como fogao_bocas (número), forno, panela_pressao, air_fryer, liquidificador, batedeira, micro_ondas, freezer (true/false). 'tecnicas' é um dicionário técnica→'sim'/'nao'/'aprendendo'. 'restricoes' aceita espaco_geladeira, tempo_por_cozinhada_min, gas, energia e outras. Faz merge com o que já existe.",
        "parameters": {"type": "object", "properties": {
            "equipamentos": {"type": "object", "description": "ex.: {\"fogao_bocas\": 4, \"forno\": true, \"panela_pressao\": true, \"air_fryer\": false, \"liquidificador\": true}", "additionalProperties": True},
            "tecnicas": {"type": "object", "description": "ex.: {\"massa fresca\": \"nao\", \"molho branco\": \"sim\", \"ponto de carne\": \"aprendendo\"}", "additionalProperties": True},
            "restricoes": {"type": "object", "description": "ex.: {\"espaco_geladeira\": \"pouco\", \"tempo_por_cozinhada_min\": 120, \"gas\": \"botijão ok\", \"energia\": \"estável\"}", "additionalProperties": True},
            "observacoes": {"type": "string", "description": "Qualquer observação livre relevante"},
        }},
    },
    "receita_conferir_ingredientes": {
        "name": "receita_conferir_ingredientes",
        "description": "Etapa 2.3: compara os ingredientes de uma receita com a despensa e diz o que ela já tem (e quanto), o que falta comprar, custo estimado e se cabe no orçamento restante. Também confere equipamentos e técnicas necessários contra o perfil salvo e devolve um veredito (viavel / precisa_confirmar / inviavel_por_enquanto) com os próximos passos. Salva a receita como candidata.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string", "description": "Nome da receita"},
            "fonte_url": {"type": "string", "description": "URL de onde a receita foi tirada"},
            "porcoes": {"type": "number", "description": "Quantas porções a receita rende"},
            "ingredientes": _ING_SCHEMA,
            "equipamentos_necessarios": {"type": "array", "items": {"type": "string"}, "description": "ex.: ['forno', 'panela de pressão', 'liquidificador']"},
            "tecnicas_necessarias": {"type": "array", "items": {"type": "string"}, "description": "ex.: ['massa fresca', 'molho branco', 'ponto da carne']"},
            "tempo_preparo_min": {"type": "number", "description": "Tempo total de preparo em minutos, se a receita informar"},
        }, "required": ["nome", "ingredientes"]},
    },
    "prato_calcular_cmv": {
        "name": "prato_calcular_cmv",
        "description": "Etapa 2.4: calcula o CMV do prato (Σ quantidade usada × custo unitário, custo unitário = preço pago ÷ quantidade comprada, incluindo compras complementares), o CMV por porção e a memória de cálculo linha a linha. Use depois que a Dona Maria aceitar a receita (gostou + tem equipamentos + técnicas + ingredientes garantidos). Pode reaproveitar uma receita conferida via receita_id.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string", "description": "Nome do prato"},
            "porcoes": {"type": "number", "description": "Quantas porções a receita rende (obrigatório para CMV por porção)"},
            "ingredientes": _ING_SCHEMA,
            "receita_id": {"type": "integer", "description": "Opcional: id devolvido por receita_conferir_ingredientes para reaproveitar os ingredientes"},
        }, "required": ["nome", "porcoes"]},
    },
    "preco_sugerir": {
        "name": "preco_sugerir",
        "description": "Monta o preço de venda no delivery com taxa de 10% sobre a venda (Dona Maria recebe 0,90×P): preço mínimo (CMV÷0,90), 2–3 cenários de margem com a conta aberta e, se pedido, a simulação de preços que ela sugerir. Apresente os cenários e deixe ELA decidir.",
        "parameters": {"type": "object", "properties": {
            "cmv_por_porcao": {"type": "number", "description": "CMV por porção em R$ (de prato_calcular_cmv)"},
            "prato_id": {"type": "integer", "description": "Opcional: id do prato calculado"},
            "taxa_plataforma": {"type": "number", "description": "Taxa da plataforma (padrão 0.10)"},
            "margens": {"type": "array", "items": {"type": "number"}, "description": "Opcional: margens-alvo sobre o valor recebido, ex.: [0.3, 0.5, 0.65]"},
            "precos_para_simular": {"type": "array", "items": {"type": "number"}, "description": "Opcional: preços que a Dona Maria quer testar, ex.: [25, 29.9]"},
            "arredondar_para": {"type": "number", "description": "Arredondamento do preço sugerido (padrão 0.50)"},
        }},
    },
    "cardapio_registrar": {
        "name": "cardapio_registrar",
        "description": "Registra a decisão da Dona Maria: prato aceito com o preço escolhido por ela. Confirma as compras complementares no orçamento (abate dos R$ restantes). Chame só depois que ela escolher o preço explicitamente.",
        "parameters": {"type": "object", "properties": {
            "prato_id": {"type": "integer", "description": "id devolvido por prato_calcular_cmv"},
            "preco_escolhido": {"type": "number", "description": "Preço de venda escolhido pela Dona Maria"},
            "confirmar_compras": {"type": "boolean", "description": "false para registrar o prato sem comprometer o orçamento ainda"},
        }, "required": ["prato_id", "preco_escolhido"]},
    },
    "cardapio_consultar": {
        "name": "cardapio_consultar",
        "description": "Situação atual: pratos aceitos no cardápio (com preço e lucro por porção), pratos em análise, receitas conferidas, orçamento restante e resumo do perfil da cozinha. Use para retomar uma conversa ou fechar a sessão.",
        "parameters": {"type": "object", "properties": {}},
    },
}

HANDLERS: dict[str, Callable[..., str]] = {
    "despensa_listar": despensa_listar,
    "despensa_buscar": despensa_buscar,
    "despensa_atualizar": despensa_atualizar,
    "perfil_cozinha_consultar": perfil_cozinha_consultar,
    "perfil_cozinha_salvar": perfil_cozinha_salvar,
    "receita_conferir_ingredientes": receita_conferir_ingredientes,
    "prato_calcular_cmv": prato_calcular_cmv,
    "preco_sugerir": preco_sugerir,
    "cardapio_registrar": cardapio_registrar,
    "cardapio_consultar": cardapio_consultar,
}
