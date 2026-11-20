"""Planeja divulgação usando o cardápio e as vendas informadas pela loja."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from typing import Any

from . import costing, jornada, precificacao, vendas
from .store import Store, _LOCK
from .units import strip_accents

_CENTAVO = Decimal("0.01")
_REPASSE = Decimal("0.90")
_DIAS = {"dia": 1, "semana": 7, "mes": 30}
_CENTRAL = "https://blog-parceiros.ifood.com.br/central-de-crescimento/"
_CHAVE_PLANO = "plano_divulgacao_v1"
_NUMERO = r"\d+(?:[.,]\d+)?"


def _volume_recente(store: Store, agora: datetime | None, dias: int) -> tuple[dict[int, int], datetime, datetime]:
    fim = vendas._agora(agora).astimezone(vendas.LOCAL).replace(hour=0, minute=0, second=0, microsecond=0)
    inicio = fim - timedelta(days=dias)
    with store.connect() as conn:
        linhas = conn.execute(
            """SELECT prato_id, SUM(quantidade) AS porcoes FROM vendas
               WHERE status = 'confirmada' AND vendido_em >= ? AND vendido_em < ? GROUP BY prato_id""",
            (vendas._iso(inicio), vendas._iso(fim)),
        ).fetchall()
    return {linha["prato_id"]: linha["porcoes"] for linha in linhas}, inicio, fim


def _cmvs_atuais(store: Store, prato_id: int | None) -> list[tuple[dict[str, Any], Decimal, Decimal]]:
    candidatos = []
    ativos = jornada.fichas_em_uso(jornada.pratos_atuais(store), jornada.controle(store))
    for resumo in ativos:
        if resumo["status"] != "aceito" or resumo.get("bloqueios") or not resumo.get("preco_venda"):
            continue
        if prato_id is not None and resumo["id"] != prato_id:
            continue
        prato = store.obter_prato(resumo["id"])
        if not prato or prato["status"] != "aceito":
            continue
        ficha = precificacao.ficha_atual(store, prato)
        if isinstance(ficha, str):
            continue
        porcoes, resultado = ficha
        preco = Decimal(str(prato["preco_venda"]))
        cmv = Decimal(str(resultado["cmv_total"])) / Decimal(str(porcoes))
        candidatos.append(({**prato, "porcoes": porcoes}, cmv, preco * _REPASSE - cmv))
    return candidatos


def _cenario(investimento: Decimal, porcoes: int, cmv: Decimal, margem: Decimal) -> dict[str, float]:
    rateio = investimento / porcoes
    return {
        "investimento": vendas._moeda(investimento),
        "rateio_por_porcao": vendas._moeda(rateio),
        "sobra_por_porcao": vendas._moeda(margem - rateio),
        "sobra_no_periodo": vendas._moeda(margem * porcoes - investimento),
        "preco_minimo_por_porcao": float(((cmv + rateio) / _REPASSE).quantize(_CENTAVO, rounding=ROUND_CEILING)),
    }


def _texto(plano: dict[str, Any]) -> str:
    prato, volume = plano["prato"], plano["volume"]
    porcoes = volume["porcoes"]
    cenario_base, *outros = plano["cenarios"]
    linhas = [f"Para **{prato['nome']}**, o preço de **{costing.brl(prato['preco_venda'])}** deixa "
              f"**{costing.brl(prato['lucro_por_porcao'])} por porção** após a taxa de 10% e o CMV."]
    if volume["origem"] == "historico":
        linhas.append(f"Usei como referência **{porcoes} porções registradas** nos últimos {volume['dias']} dias completos.")
    elif volume["origem"] == "informado":
        linhas.append(f"Usei as **{porcoes} porções previstas** para o período de {volume['dias']} dias.")
    else:
        linhas.append(f"Para comparar custos, simulei **um lote de {porcoes} porções em {volume['dias']} "
                      f"{'dia' if volume['dias'] == 1 else 'dias'}**. "
                      "É um volume hipotético, pois ainda faltam vendas registradas desse prato no período.")
    linhas.append(f"**Sem investimento**, a sobra estimada é de {costing.brl(cenario_base['sobra_no_periodo'])} "
                  "com esse volume.")
    for cenario in outros:
        detalhe = ("Simulação ilustrativa de" if plano["investimento_hipotetico"] else "Com o investimento informado de")
        linhas.append(f"**{detalhe} {costing.brl(cenario['investimento'])}** em divulgação, "
                      f"a sobra estimada fica em {costing.brl(cenario['sobra_por_porcao'])} por porção "
                      f"e {costing.brl(cenario['sobra_no_periodo'])} no total.")
        if cenario["sobra_no_periodo"] <= 0:
            linhas.append("Nesse volume, o investimento consome toda a margem do prato.")
    if volume["origem"] == "lote_hipotetico" and plano["investimento_hipotetico"]:
        linhas.append("Para este piloto, eu começaria sem investimento e acompanharia os pedidos registrados no Analytics.")
    if outros and plano["investimento_hipotetico"]:
        linhas.append("O investimento hipotético usa até 25% da margem calculada para esse volume.")
    if outros:
        linhas.append("As ofertas da loja ficam no Portal do Parceiro. Veja como acessá-las na "
                      f"[Central de Crescimento do iFood]({_CENTRAL}).")
    return "\n\n".join(linhas)


def planejar(store: Store, args: dict[str, Any], *, agora: datetime | None = None,
             preparar_campanha: bool = False) -> dict[str, Any]:
    """Compara possibilidades de investimento sem gravar anúncios, preços ou vendas."""
    periodo = args.get("periodo") if args.get("periodo") is not None else "semana"
    if not isinstance(periodo, str) or periodo not in _DIAS:
        raise ValueError("Escolha dia, semana ou mês para o período.")
    prato_id = vendas._inteiro(args["prato_id"], "prato") if args.get("prato_id") is not None else None
    porcoes_informadas = vendas._inteiro(args["porcoes"], "porções previstas") if args.get("porcoes") is not None else None
    investimento = None
    if args.get("investimento") is not None:
        investimento = vendas._decimal(args["investimento"], "investimento")
        if not 0 <= investimento <= 1_000_000 or investimento != investimento.quantize(_CENTAVO):
            raise ValueError("Informe o investimento em reais e centavos, de zero até R$ 1.000.000.")

    candidatos = _cmvs_atuais(store, prato_id)
    if not candidatos:
        raise ValueError("Para planejar divulgação, escolha um prato do cardápio com preço e ficha viável.")
    dias = _DIAS[periodo]
    historico, inicio, fim = _volume_recente(store, agora, dias)
    prato, cmv, margem = max(candidatos, key=lambda c: (historico.get(c[0]["id"], 0), c[2], -c[0]["id"]))
    porcoes_recentes = historico.get(prato["id"], 0)
    if porcoes_informadas is not None:
        volume = {"porcoes": porcoes_informadas, "origem": "informado", "dias": dias}
    elif porcoes_recentes > 0:
        volume = {"porcoes": porcoes_recentes, "origem": "historico", "dias": dias,
                  "inicio": inicio.isoformat(), "fim": fim.isoformat()}
    else:
        porcoes_lote = int(prato["porcoes"])
        if porcoes_lote < 1:
            raise ValueError("Informe quantas porções inteiras quer usar na simulação.")
        volume = {"porcoes": porcoes_lote, "origem": "lote_hipotetico", "dias": dias}

    porcoes = volume["porcoes"]
    hipotetico = investimento is None
    if hipotetico:
        investimento = (max(Decimal(0), margem) * porcoes / 4).quantize(_CENTAVO, rounding=ROUND_DOWN)
    cenarios = [_cenario(Decimal(0), porcoes, cmv, margem)]
    if investimento > 0:
        cenarios.append(_cenario(investimento, porcoes, cmv, margem))
    plano = {
        "prato": {"id": prato["id"], "nome": prato["nome"], "preco_venda": prato["preco_venda"],
                  "cmv_por_porcao": vendas._moeda(cmv), "lucro_por_porcao": vendas._moeda(margem)},
        "periodo": periodo, "volume": volume, "investimento_hipotetico": hipotetico, "cenarios": cenarios,
    }
    if preparar_campanha:
        plano["campanha"] = _campanha(store, plano, margem, investimento if not hipotetico else None, args)
    return {**plano, "diga_a_dona_maria": _texto(plano)}


def parametros_da_conversa(falas: list[Any]) -> dict[str, Any]:
    """Reaproveita valores explícitos da conversa, sem inferir gasto de um preço de venda."""
    parametros: dict[str, Any] = {}
    for fala in falas:
        texto = strip_accents(fala.texto).lower()
        if re.search(r"[?`'\"“”«»]|\b(?:talvez|por exemplo|hipoteticamente|nao quero|nao pretendo)\b", texto):
            continue
        margem = (re.search(rf"\bmargem(?: de lucro)?(?: minima| desejada)?(?: de| em)?\s+({_NUMERO})\s*%", texto)
                  or re.search(rf"\b({_NUMERO})\s*%\s+(?:de )?margem\b", texto))
        if margem:
            valor = Decimal(margem[1].replace(",", ".")) / 100
            if 0 <= valor < 1:
                parametros["margem_minima"] = float(valor)
        anterior = strip_accents(fala.resposta_a).lower()
        tema = r"\b(?:divulgacao|publicidade|campanha|anuncios?|anunciar|investimento|investir|simul\w*)\b"
        pergunta = "?" in anterior and "investimento" in anterior and "porcoes" in anterior
        if not (re.search(tema, texto) or pergunta):
            continue
        gasto = re.search(rf"\br\$\s*({_NUMERO})(?!\d|[.,]\d|\s*(?:a|por)\s+porcao\b)", texto)
        if re.search(r"\b(?:preco(?: de venda)?|cobrar|custou|cmv)\b", texto) and not re.search(tema, texto):
            gasto = None
        if gasto:
            valor = Decimal(gasto[1].replace(",", "."))
            if 0 <= valor <= 1_000_000 and valor == valor.quantize(_CENTAVO):
                parametros["investimento"] = float(valor)
        volume = re.search(r"(?<![\d.,])([1-9]\d{0,6})\s+porcoes\b", texto)
        if volume and not re.search(r"\b(?:vendi|vendemos|vendidas)\b", texto):
            parametros["porcoes"] = int(volume[1])
        for periodo, termos in (("semana", r"semana|7\s+dias"), ("mes", r"mes|30\s+dias"), ("dia", r"dia")):
            if re.search(rf"\b(?:por|na|no|em|para|durante|neste|nesta)\s+(?:(?:um|uma)\s+)?(?:{termos})\b", texto):
                parametros["periodo"] = periodo
                break
    return parametros


def _meta_da_margem(store: Store, prato: dict[str, Any], args: dict[str, Any]) -> tuple[Decimal, str]:
    minima = args.get("margem_minima")
    if minima is not None:
        valor = vendas._decimal(minima, "margem mínima")
        if not 0 <= valor < 1:
            raise ValueError("A margem mínima precisa estar entre zero e 100%.")
        return valor, "informada"
    analise = next((a for a in store.analises_preco() if a["prato_id"] == prato["id"]), None)
    if analise:
        for cenario in analise["dados"].get("cenarios", []):
            if cenario.get("preco_sugerido") == prato["preco_venda"] and cenario.get("margem_alvo") is not None:
                alvo = vendas._decimal(cenario["margem_alvo"], "margem do cenário aprovado")
                if 0 <= alvo < 1:
                    return alvo, "cenario_escolhido"
    return Decimal(0), "sem_meta"


def _campanha(store: Store, plano: dict[str, Any], margem: Decimal, investimento: Decimal | None,
              args: dict[str, Any]) -> dict[str, Any]:
    prato, volume = plano["prato"], plano["volume"]
    minima, origem_meta = _meta_da_margem(store, prato, args)
    repasse = Decimal(str(prato["preco_venda"])) * _REPASSE
    porcoes = volume["porcoes"]
    teto = max(Decimal(0), (margem - repasse * minima) * porcoes).quantize(_CENTAVO, rounding=ROUND_DOWN)
    previsto = min(investimento, teto) if investimento is not None else Decimal(0)
    if margem < repasse * minima:
        estrategia = "Medir a procura pelo cardápio atual e revisar a margem antes de considerar mídia paga."
    elif investimento is not None and investimento > teto:
        estrategia = "Usar os canais próprios e avaliar mídia paga até o limite compatível com a margem do lote."
    elif previsto > 0:
        estrategia = "Combinar a mensagem para canais próprios com um piloto de anúncios, conforme a oferta do Portal."
    else:
        estrategia = "Começar pelos canais próprios e usar as vendas registradas como referência para o próximo teste."
    preco = costing.brl(prato["preco_venda"])
    nome = prato["nome"]
    return {
        "objetivo": f"Dar visibilidade a {nome} no cardápio e acompanhar as porções vendidas.",
        "canal": "Cardápio e canais próprios" if previsto == 0 else "Canais próprios e piloto de Anúncios",
        "estrategia": estrategia,
        "titulo": f"{nome} no Sabor da Maria",
        "descricao": f"{nome} por {preco} a porção. Consulte a disponibilidade no cardápio do Sabor da Maria.",
        "legenda": f"Hoje tem {nome} no Sabor da Maria por {preco} a porção. Confira a disponibilidade no nosso cardápio!",
        "investimento": vendas._moeda(previsto),
        "lucro_previsto_por_porcao": vendas._moeda(margem - previsto / porcoes),
        "teto_para_margem": vendas._moeda(teto),
        "margem_minima": float(minima) if origem_meta != "sem_meta" else None,
        "origem_margem": origem_meta,
        "tarefas": [
            "Usar o título, o preço aprovado e a mensagem pronta nos canais escolhidos.",
            "Acompanhar porções vendidas e margem por porção no Analytics.",
            f"Comparar o resultado ao fim de {volume['dias']} {'dia' if volume['dias'] == 1 else 'dias'}.",
        ],
    }


def _identidade(pratos: list[dict[str, Any]]) -> list[list[Any]]:
    return [[p["id"], p.get("receita_id"), p.get("preco_venda")] for p in sorted(pratos, key=lambda p: p["id"])]


def plano_atual(store: Store, pratos: list[dict[str, Any]]) -> dict[str, Any] | None:
    with store.connect() as conn:
        bruto = store._config_get(conn, _CHAVE_PLANO)
    plano = json.loads(bruto) if bruto else None
    return plano if plano and plano.get("cardapio") == _identidade(pratos) else None


def preparar(store: Store, preferencias: dict[str, Any], *, agora: datetime | None = None) -> tuple[dict[str, Any], bool]:
    """Salva o plano local após o aceite. Repetir reabre a mesma campanha."""
    with _LOCK, store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        estado = jornada.resumo(store)
        if estado["etapa"] != "divulgacao" or estado["controle"]["divulgacao"] != "interesse":
            raise ValueError("Conclua o cardápio e confirme o interesse em divulgação para preparar o plano.")
        anterior = estado.get("plano_divulgacao")
        preferencias = {**(anterior or {}).get("preferencias", {}), **preferencias}
        if anterior and anterior.get("preferencias", {}) == preferencias:
            return anterior, False
        ativos = set(estado["pratos_ativos"])
        pratos = [p for p in store.pratos_em_andamento() if p["id"] in ativos]
        plano = planejar(store, preferencias, agora=agora, preparar_campanha=True)
        plano.update({"cardapio": _identidade(pratos), "preferencias": preferencias,
                      "criado_em": vendas._iso(vendas._agora(agora))})
        store._config_set(conn, _CHAVE_PLANO, json.dumps(plano, ensure_ascii=False))
    return plano, True


def texto_preparado(plano: dict[str, Any], novo: bool) -> str:
    campanha = plano["campanha"]
    nome = plano["prato"]["nome"]
    titulo = f"Salvei o plano de divulgação de {nome}" if novo else f"O plano de divulgação de {nome} já está salvo"
    retorno = f"**{titulo}**. A etapa 5 está concluída."
    if novo:
        retorno += (f"\n\n{campanha['estrategia']} O plano prevê **{costing.brl(campanha['investimento'])}** "
                    "de investimento em divulgação.\n\n"
                    f"**Mensagem pronta para usar**\n\n{campanha['legenda']}")
    return retorno + "\n\nVocê pode consultar os materiais e as ações no cartão de divulgação."
