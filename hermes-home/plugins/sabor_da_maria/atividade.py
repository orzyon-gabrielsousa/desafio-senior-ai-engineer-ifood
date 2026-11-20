"""Atividade da consultora: o que cada ferramenta fez, em português, para a tela mostrar o passo a passo.

Pelo streaming, o Hermes avisa a tela que uma ferramenta começou e terminou (``hermes.tool.progress``), com o nome e o argumento
principal (a busca digitada, a página lida). Não manda o que a ferramenta **achou**. Este módulo completa isso sem tocar no core:

* o hook público ``post_tool_call`` recebe argumentos e resultado de **toda** ferramenta (as do plugin e as do Hermes, como ``web_search``
  e ``web_extract``), transforma numa frase curta ("8 resultados · receitasdeminuto.com, tuta.com.br") e guarda no SQLite, com o mesmo
  ``tool_call_id`` que a tela recebe no streaming;
* a tela pergunta ``GET /api/atividade?ids=...`` e junta a frase ao passo que já está na conversa.

Só repete o que a ferramenta devolveu: nada vem do modelo. Faltou dado ou veio formato novo, a frase fica genérica e o passo aparece mesmo assim.
O hook nunca derruba o turno.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from . import costing
from .store import Store

logger = logging.getLogger(__name__)

MAX_RESUMO = 230
MAX_ITEM = 170
MAX_ITENS = 6
PREFIXO_TRAVA = "Travado: "  # a tela destaca o passo cujo resumo começa assim (playground/src/lib/tools.ts usa o mesmo texto)

Descricao = tuple[str, str, list[str]]  # título (passado), resumo, linhas de detalhe

TITULOS = {
    "web_search": "Pesquisou na internet",
    "web_extract": "Leu a receita",
    "despensa_listar": "Abriu a despensa",
    "despensa_buscar": "Conferiu a despensa",
    "despensa_atualizar": "Atualizou a despensa",
    "despensa_registrar_compras": "Registrou as compras na despensa",
    "pratos_selecionar": "Guardou os pratos escolhidos",
    "perfil_cozinha_consultar": "Olhou a cozinha dela",
    "perfil_cozinha_salvar": "Guardou a cozinha dela",
    "negocio_salvar": "Guardou o negócio dela",
    "receita_conferir_ingredientes": "Comparou com a despensa e o orçamento",
    "prato_calcular_cmv": "Calculou o CMV",
    "preco_sugerir": "Montou os preços",
    "cardapio_registrar": "Registrou no cardápio",
    "cardapio_remover": "Tirou do cardápio",
    "cardapio_consultar": "Consultou o cardápio",
    "divulgacao_planejar": "Simulou a margem com divulgação",
    "carrinho_consultar": "Abriu o carrinho",
    "carrinho_adicionar": "Colocou no carrinho",
    "carrinho_adicionar_prato": "Abriu o prato no carrinho",
    "carrinho_remover": "Tirou do carrinho",
    "carrinho_montar_da_receita": "Montou o carrinho da receita",
    "salvos_salvar": "Salvou em Meus pratos",
    "analytics_analises_consultar": "Consultou as análises salvas",
    "vendas_registrar": "Registrou uma venda realizada",
    "vendas_consultar": "Consultou as vendas realizadas",
    "clarify": "Perguntou a ela",
    "memory": "Guardou na memória",
    "skill_view": "Abriu o playbook",
    "skills_list": "Listou os playbooks",
    "todo": "Anotou o próximo passo",
    "todo_list": "Anotou o próximo passo",
}


# ----------------------------------------------------------------------------- leitura tolerante
def _dados(resultado: Any) -> dict[str, Any]:
    if isinstance(resultado, dict):
        return resultado
    if isinstance(resultado, str):
        try:
            bruto = json.loads(resultado)
        except ValueError:
            return {}
        return bruto if isinstance(bruto, dict) else {}
    return {}


def _curto(texto: Any, limite: int) -> str:
    t = re.sub(r"\s+", " ", str(texto if texto is not None else "")).strip()
    return t if len(t) <= limite else t[: limite - 1].rstrip() + "…"


def _site(url: Any) -> str:
    host = urlsplit(str(url or "")).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _lista(itens: list[Any]) -> list[str]:
    return [_curto(i, MAX_ITEM) for i in itens if i][:MAX_ITENS]


def _linhas(texto: Any) -> list[str]:
    return [l.strip() for l in str(texto or "").splitlines() if l.strip()]


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _nomes(valor: Any) -> list[str]:
    if isinstance(valor, str):
        return [valor]
    achados: list[str] = []
    for v in valor if isinstance(valor, list) else []:
        if isinstance(v, str):
            achados.append(v)
        elif isinstance(v, dict):
            achados.append(str(v.get("nome") or v.get("ingrediente") or ""))
    return achados


def _saldo(resumo: Any) -> str:
    if not isinstance(resumo, dict) or "saldo" not in resumo:
        return ""
    return f"no carrinho {costing.brl(float(resumo.get('carrinho') or 0))} · saldo {costing.brl(float(resumo['saldo']))}"


def entrada(ferramenta: str, args: Any) -> str:
    """O que a ferramenta recebeu, em uma linha: a busca digitada, as páginas, os ingredientes."""
    a = args if isinstance(args, dict) else {}
    if ferramenta == "web_search":
        return _curto(a.get("query"), 140)
    if ferramenta == "web_extract":
        urls = a.get("urls") if isinstance(a.get("urls"), list) else [a.get("urls")]
        return ", ".join(dict.fromkeys(s for s in (_site(u) for u in urls) if s))
    for chave in ("ingredientes", "filtro", "nome", "prato", "receita", "ingrediente", "query"):
        partes = [p for p in _nomes(a.get(chave)) if p]
        if partes:
            return _curto(", ".join(partes), 100)
    return ""


# ----------------------------------------------------------------------------- uma frase por ferramenta
def _web_search(args: dict, r: dict) -> Descricao:
    achados = [a for a in ((r.get("data") or {}).get("web") or []) if isinstance(a, dict)]
    if not achados:
        return TITULOS["web_search"], "Nenhum resultado para essa busca", []
    sites = list(dict.fromkeys(s for s in (_site(a.get("url")) for a in achados) if s))
    mais = f" e mais {len(sites) - 3}" if len(sites) > 3 else ""
    resumo = f"{_plural(len(achados), 'resultado', 'resultados')} · {', '.join(sites[:3])}{mais}"
    return TITULOS["web_search"], resumo, _lista([f"{_curto(a.get('title'), 90)} — {_site(a.get('url'))}" for a in achados])


_SECAO_INGREDIENTES = re.compile(r"^##\s*Ingredientes[^\n]*\n(.*?)(?=^##\s|\Z)", re.M | re.S)
_TITULO = re.compile(r"^#\s+(.+)$", re.M)
_RENDIMENTO = re.compile(r"Rendimento:\s*([^|\n]+)")


def _web_extract(args: dict, r: dict) -> Descricao:
    paginas = [p for p in (r.get("results") or []) if isinstance(p, dict)]
    resumos: list[str] = []
    itens: list[str] = []
    com_ingredientes = False
    for p in paginas:
        site = _site(p.get("url"))
        conteudo = str(p.get("content") or "")
        if p.get("error") or not conteudo.strip():
            itens.append(f"{site}: não consegui ler ({_curto(p.get('error') or 'sem conteúdo', 80)})")
            continue
        achou = _TITULO.search(conteudo)
        nome = _curto(achou.group(1) if achou else str(p.get("title") or site).split(" - ")[0], 70)
        secao = _SECAO_INGREDIENTES.search(conteudo)
        ingredientes = [l[2:].strip() for l in (secao.group(1).splitlines() if secao else []) if l.startswith("- ")]
        if ingredientes:
            com_ingredientes = True
            rendimento = _RENDIMENTO.search(conteudo)
            extra = f", rende {_curto(rendimento.group(1), 24)}" if rendimento else ""
            resumos.append(f"{nome} ({_plural(len(ingredientes), 'ingrediente', 'ingredientes')}{extra})")
            itens.append(f"{nome} · {site}: " + ", ".join(ingredientes[:8]) + ("…" if len(ingredientes) > 8 else ""))
        else:
            resumos.append(f"{nome} (sem lista de ingredientes estruturada, li o texto)")
            itens.append(f"{nome} · {site}")
    if not resumos:
        titulo = "Tentou ler as páginas"
    elif len(paginas) == 1 and com_ingredientes:
        titulo = TITULOS["web_extract"]
    else:
        titulo = f"Leu {_plural(len(resumos), 'página', 'páginas')}"
    return titulo, "; ".join(resumos) or "Não consegui ler a página", _lista(itens)


def _despensa_listar(args: dict, r: dict) -> Descricao:
    itens = [i for i in (r.get("itens") or []) if isinstance(i, dict)]
    filtro = str(args.get("filtro") or "").strip()
    if filtro:
        resumo = f"{_plural(len(itens), 'item', 'itens')} com “{filtro}”"
    else:
        resumo = _plural(int(r.get("total_itens") or len(itens)), "item na despensa", "itens na despensa")
    orc = r.get("orcamento") or {}
    if "restante" in orc:
        resumo += f" · orçamento para complementos {costing.brl(float(orc['restante']))} de {costing.brl(float(orc.get('inicial') or 0))}"
    linhas = [f"{i.get('ingrediente')}: {i.get('disponivel') or i.get('estoque')} a {i.get('custo_unitario_legivel')}" for i in itens]
    return TITULOS["despensa_listar"], resumo, _lista(linhas)


def _despensa_buscar(args: dict, r: dict) -> Descricao:
    tem: list[str] = []
    falta: list[str] = []
    itens: list[str] = []
    for x in r.get("resultados") or []:
        nome = str(x.get("procurado") or "")
        achado = (x.get("candidatos") or [None])[0] if x.get("encontrado") else None
        if achado:
            tem.append(f"{nome} ({achado.get('disponivel_legivel')})")
            itens.append(f"{nome} → {achado.get('nome')}: {achado.get('disponivel_legivel')} disponível, {achado.get('custo_unitario_legivel')}")
        else:
            falta.append(nome)
            itens.append(f"{nome} → não tem na despensa (precisa comprar)")
    partes = [f"Tem: {', '.join(tem)}" if tem else "", f"Não tem: {', '.join(falta)}" if falta else ""]
    return TITULOS["despensa_buscar"], " · ".join(p for p in partes if p) or "Nada para conferir", _lista(itens)


def _despensa_atualizar(args: dict, r: dict) -> Descricao:
    alteracoes = r.get("alteracoes")
    if isinstance(alteracoes, list):
        alteracoes = "; ".join(str(a) for a in alteracoes)
    item = r.get("ingrediente")
    nome = item.get("nome") if isinstance(item, dict) else item
    return TITULOS["despensa_atualizar"], _curto(f"{nome or 'Item'}: {alteracoes or 'sem mudança'}", MAX_RESUMO), []


def _cozinha_consultar(args: dict, r: dict) -> Descricao:
    resumo = _curto(r.get("minha_cozinha") or "", 170)
    pend = r.get("pendencias") or []
    if pend and not r.get("completo"):
        resumo += f" · {_plural(len(pend), 'pergunta sem resposta', 'perguntas sem resposta')}"
    return TITULOS["perfil_cozinha_consultar"], resumo, []


def _chaves_salvas(salvo: Any) -> list[str]:
    if not isinstance(salvo, dict):
        return []
    achadas: list[str] = []
    for grupo, valor in salvo.items():
        achadas += [str(k) for k in valor] if isinstance(valor, dict) else [str(grupo)]
    return achadas


def _guardou(titulo: str, r: dict) -> Descricao:
    salvas = _chaves_salvas(r.get("salvo"))
    recusados = [str(x.get("chave") or x.get("grupo") or "") for x in (r.get("recusados") or []) if isinstance(x, dict)]
    partes = [f"Guardou: {', '.join(salvas)}" if salvas else "", f"Não guardou (ela não disse): {', '.join(recusados)}" if recusados else ""]
    resumo = " · ".join(p for p in partes if p) or "Nada novo para guardar"
    return titulo, _curto(resumo, MAX_RESUMO), []


def _conferir(args: dict, r: dict) -> Descricao:
    resumo = r.get("resumo") or {}
    orc = resumo.get("orcamento") or {}
    tem = len(resumo.get("ja_tem_tudo_que_precisa") or [])
    pouco = resumo.get("tem_mas_nao_o_suficiente") or []
    falta = len(resumo.get("nao_tem") or []) + len(pouco)
    compras = float(resumo.get("compras_total_estimado") or 0)
    partes = [f"tem {tem}", f"falta {falta}", f"comprar {costing.brl(compras)}"]
    if "cabe_no_orcamento" in orc:
        partes.append("cabe no orçamento" if orc["cabe_no_orcamento"] else "NÃO cabe no orçamento")
    if (resumo.get("cmv_previa") or {}).get("total"):
        partes.append(f"custo da receita {costing.brl(float(resumo['cmv_previa']['total']))}")
    linhas = [f"{i.get('ingrediente')}: {i.get('memoria_de_calculo') or ('falta ' + str(i.get('falta')))}" for i in r.get("ingredientes") or [] if isinstance(i, dict)]
    veredito = str(r.get("veredito") or "").replace("_", " ")
    nome = r.get("receita") if r.get("receita") and r.get("receita") != "Receita sem nome" else ""
    return TITULOS["receita_conferir_ingredientes"], _curto(f"{nome + ': ' if nome else ''}{', '.join(partes)}" + (f" · {veredito}" if veredito else ""), MAX_RESUMO), _lista(linhas)


def _cmv(args: dict, r: dict) -> Descricao:
    resumo = f"{r.get('prato') or 'Prato'}: CMV {costing.brl(float(r.get('cmv_total') or 0))}"
    if r.get("cmv_por_porcao") is not None:
        resumo += f" · {costing.brl(float(r['cmv_por_porcao']))} por porção"
    memoria = r.get("memoria_de_calculo")
    return TITULOS["prato_calcular_cmv"], resumo, _lista(memoria if isinstance(memoria, list) else _linhas(memoria))


def _preco(args: dict, r: dict) -> Descricao:
    cenarios = [c for c in (r.get("cenarios") or []) if isinstance(c, dict)]
    resumo = f"{r.get('prato') or 'Prato'}: preço mínimo {costing.brl(float(r.get('preco_minimo') or 0))}"
    linhas = []
    for c in cenarios:
        av = c.get("com_preco_sugerido") or {}
        linhas.append(f"{c.get('nome')}: {costing.brl(float(c.get('preco_sugerido') or 0))} → ela recebe {costing.brl(float(av.get('recebe_liquido') or 0))}, "
                      f"lucro {costing.brl(float(av.get('lucro') or 0))}")
    return TITULOS["preco_sugerir"], resumo, _lista(linhas)


def _registrar(args: dict, r: dict) -> Descricao:
    p = r.get("prato") or {}
    resumo = f"{p.get('nome') or 'Prato'} a {costing.brl(float(p.get('preco_venda') or 0))}" + (" (já estava registrado)" if r.get("ja_registrado") else "")
    return TITULOS["cardapio_registrar"], resumo, _lista(_linhas(r.get("diga_a_dona_maria")))


def _carrinho(titulo: str) -> Callable[[dict, dict], Descricao]:
    def descrever(args: dict, r: dict) -> Descricao:
        prato = r.get("prato")
        nome = prato.get("nome") if isinstance(prato, dict) else prato
        adicionado = r.get("adicionado")
        quem = nome or (adicionado.get("nome") if isinstance(adicionado, dict) else "") or ""
        resumo = " · ".join(p for p in (str(quem), _saldo(r.get("resumo"))) if p) or "Carrinho atualizado"
        return titulo, _curto(resumo, MAX_RESUMO), _lista(_linhas(r.get("diga_a_dona_maria")))
    return descrever


def _generico(ferramenta: str) -> Callable[[dict, dict], Descricao]:
    def descrever(args: dict, r: dict) -> Descricao:
        titulo = TITULOS.get(ferramenta) or ferramenta.replace("_", " ").capitalize()
        return titulo, _curto(r.get("diga_a_dona_maria") or r.get("mensagem") or "", MAX_RESUMO), []
    return descrever


def _analises_salvas(args: dict, r: dict) -> Descricao:
    pratos = [a.get("prato") for a in r.get("analises", []) if isinstance(a, dict) and isinstance(a.get("prato"), str)]
    return TITULOS["analytics_analises_consultar"], _plural(len(pratos), "análise salva", "análises salvas"), _lista(pratos)


def _venda_realizada(args: dict, r: dict) -> Descricao:
    venda = r.get("venda") or {}
    resumo = (f"{venda.get('quantidade', 0)} porções de {venda.get('prato', 'prato')} · "
              f"faturamento {costing.brl(venda.get('faturamento_bruto') or 0)}")
    return TITULOS["vendas_registrar"], resumo, _lista([r.get("diga_a_dona_maria")])


def _vendas_realizadas(args: dict, r: dict) -> Descricao:
    total = r.get("resumo") or {}
    return (TITULOS["vendas_consultar"],
            f"{total.get('quantidade', 0)} porções · faturamento {costing.brl(total.get('faturamento_bruto') or 0)}",
            _lista([r.get("diga_a_dona_maria")]))


DESCRITORES: dict[str, Callable[[dict, dict], Descricao]] = {
    "web_search": _web_search,
    "web_extract": _web_extract,
    "despensa_listar": _despensa_listar,
    "despensa_buscar": _despensa_buscar,
    "despensa_atualizar": _despensa_atualizar,
    "despensa_registrar_compras": lambda a, r: (TITULOS["despensa_registrar_compras"], str(r.get("diga_a_dona_maria") or ""),
                                                  _lista(r.get("compras_registradas") or [])),
    "pratos_selecionar": lambda a, r: (TITULOS["pratos_selecionar"], "Escolhas em Minha cozinha › Pratos",
                                        _lista([p["nome"] for p in r.get("pratos_escolhidos") or []])),
    "perfil_cozinha_consultar": _cozinha_consultar,
    "perfil_cozinha_salvar": lambda a, r: _guardou(TITULOS["perfil_cozinha_salvar"], r),
    "negocio_salvar": lambda a, r: _guardou(TITULOS["negocio_salvar"], r),
    "receita_conferir_ingredientes": _conferir,
    "prato_calcular_cmv": _cmv,
    "preco_sugerir": _preco,
    "cardapio_registrar": _registrar,
    "carrinho_consultar": _carrinho(TITULOS["carrinho_consultar"]),
    "carrinho_adicionar": _carrinho(TITULOS["carrinho_adicionar"]),
    "carrinho_adicionar_prato": _carrinho(TITULOS["carrinho_adicionar_prato"]),
    "carrinho_remover": _carrinho(TITULOS["carrinho_remover"]),
    "carrinho_montar_da_receita": _carrinho(TITULOS["carrinho_montar_da_receita"]),
    "analytics_analises_consultar": _analises_salvas,
    "vendas_registrar": _venda_realizada,
    "vendas_consultar": _vendas_realizadas,
}


def descrever(ferramenta: str, args: Any, resultado: Any) -> dict[str, Any]:
    """Título (no passado), resumo e linhas de detalhe do que a ferramenta fez, só com o que ela devolveu. Sempre devolve algo."""
    a = args if isinstance(args, dict) else {}
    r = _dados(resultado)
    titulo = TITULOS.get(ferramenta) or ferramenta.replace("_", " ").capitalize()
    if r.get("error") or r.get("success") is False:
        if ferramenta == "despensa_atualizar":
            titulo = "Não atualizou a despensa"
        elif ferramenta == "despensa_registrar_compras":
            titulo = "Compra não registrada"
    if r.get("bloqueado"):
        # Trava de aceite ou de saldo fazendo o trabalho dela: não é falha, e a tela mostra o motivo.
        bloqueios = r.get("bloqueios") if isinstance(r.get("bloqueios"), list) else []
        primeiro = bloqueios[0] if bloqueios and isinstance(bloqueios[0], dict) else {}
        motivo = r.get("error") or primeiro.get("detalhe") or "falta algo antes de seguir"
        extras = [str(b.get("detalhe")) for b in bloqueios if isinstance(b, dict) and b.get("detalhe")]
        return {"titulo": titulo, "ok": True, "resumo": _curto(f"{PREFIXO_TRAVA}{motivo}", MAX_RESUMO), "itens": _lista(extras),
                "entrada": entrada(ferramenta, a)}
    erro = r.get("error") or (r.get("success") is False and (r.get("mensagem") or "a ferramenta recusou o pedido"))
    if erro:
        return {"titulo": titulo, "ok": False, "resumo": _curto(erro, MAX_RESUMO), "itens": [], "entrada": entrada(ferramenta, a)}
    try:
        titulo, resumo, itens = DESCRITORES.get(ferramenta, _generico(ferramenta))(a, r)
    except Exception:  # noqa: BLE001 - formato novo de resultado: o passo aparece com o que dá
        logger.exception("atividade: não consegui descrever %s", ferramenta)
        resumo, itens = "", []
    return {"titulo": titulo, "ok": True, "resumo": _curto(resumo, MAX_RESUMO), "itens": itens, "entrada": entrada(ferramenta, a)}


# ----------------------------------------------------------------------------- hook
def apos_tool(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", tool_call_id: str = "",
              duration_ms: Optional[int] = None, **_: Any) -> None:
    """Hook ``post_tool_call``: guarda a frase do que a ferramenta fez. Sem ``tool_call_id`` a tela não teria como juntar; nunca derruba o turno."""
    if not tool_name or not tool_call_id or tool_name.startswith("_"):
        return
    try:
        d = descrever(tool_name, args, result)
        Store().atividade_registrar(tool_call_id, tool_name, d["titulo"], sessao=session_id or "", entrada=d["entrada"], resumo=d["resumo"],
                                    itens=d["itens"], ok=d["ok"], duracao_ms=int(duration_ms) if duration_ms is not None else None)
    except Exception:  # noqa: BLE001
        logger.exception("atividade: não consegui registrar %s", tool_name)
