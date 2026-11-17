"""Composição e leitura das análises de preço propostas pela consultora."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Iterable

from . import costing, taxas
from .falas import Fala
from .store import Store
from .units import norm_text


_PEDIDO_DE_ACEITE = re.compile(
    r"\b(?:(?:aceita|aprova|confirma) (?:cozinhar|montar|preparar|fazer|seguir com|"
    r"(?:o|a|esse|essa|este|esta) (?:receita|versao|prato)|que quer (?:cozinhar|montar|preparar|fazer|seguir com))|"
    r"quer (?:cozinhar|montar|preparar|fazer|seguir com))\b"
)
_ACEITE = re.compile(
    r"^(?:sim|fechado|combinado|de acordo|(?:eu )?(?:aceito|aceitei|confirmo|aprovo)|"
    r"(?:pode|vamos|quero) (?:seguir|cozinhar|montar|preparar|fazer))\b"
)
_RECUSA = re.compile(
    r"^nao\b|\b(?:desisti|nao (?:disse que )?(?:aceito|confirmo|aprovo|quero|pode seguir|vamos seguir))\b"
)
_CONDICAO_OU_REVISAO = re.compile(
    r"\b(?:talvez|so se|desde que|contanto que|antes de aprovar|depois de revisar|"
    r"se (?:eu |voce )?(?:tiver|achar|for|ficar|encontrar|confirmar|conseguir|der|houver)|"
    r"mas (?:troque|mude|substitua|ajuste|retire|reduza|aumente))\b"
)
_CONFIRMACAO_DE_PERFIL = re.compile(r"^(?:sim )?(?:(?:eu )?confirmo(?: que)? )?(?:eu )?(?:tenho|possuo|uso|sei|domino)\b")
_ACEITE_PARCIAL = re.compile(r"\b(?:so|somente|apenas) (?:a ficha|a lista|os ingredientes|as quantidades|os equipamentos)\b")
_ACEITE_DE_PRECO = re.compile(r"^(?:sim )?(?:eu )?(?:aceito|aprovo|confirmo) (?:o preco|o valor|a sugestao de preco)\b")
_PRATO_NUMERADO = re.compile(r"(?:\bprato\s+(?:escolhido\s+)?#?\s*|(?<!\w)#\s*)(\d+)\b", re.I)


def _palavras(texto: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", norm_text(texto)))


def _ids_na_fala(texto: str) -> set[int]:
    return {int(numero) for numero in _PRATO_NUMERADO.findall(texto)}


def _menciona_prato(texto: str, nome: str, prato_id: int | None) -> bool:
    ids = _ids_na_fala(texto)
    if ids and prato_id is not None:
        return ids == {prato_id}
    nome_limpo = _palavras(nome)
    return bool(nome_limpo and f" {nome_limpo} " in f" {_palavras(texto)} ")


def aceite_da_receita(prato_id: int | None, nome: str, falas: Iterable[Fala]) -> Fala | None:
    """Lê o aceite na última fala da pessoa, ligado a um único prato por nome ou número."""
    falas = list(falas)
    if not falas:
        return None
    fala = falas[-1]
    texto = _palavras(fala.texto)
    if (not _ACEITE.search(texto) or _RECUSA.search(texto) or _CONDICAO_OU_REVISAO.search(texto)
            or _CONFIRMACAO_DE_PERFIL.search(texto) or _ACEITE_PARCIAL.search(texto) or _ACEITE_DE_PRECO.search(texto)
            or "?" in fala.texto):
        return None
    ids = _ids_na_fala(fala.texto)
    if _menciona_prato(fala.texto, nome, prato_id):
        return fala
    if ids:
        return None
    resposta_a = fala.resposta_a or fala.pergunta
    trechos = [t for t in re.split(r"(?<=[?!;])\s*|(?<=[.])\s+|\n+", resposta_a) if t.strip()]
    pedidos = [i for i, trecho in enumerate(trechos) if _PEDIDO_DE_ACEITE.search(_palavras(trecho))]
    if not pedidos or any("?" in t for t in trechos[pedidos[-1] + 1:]):
        return None
    if all(_menciona_prato(trechos[i], nome, prato_id) for i in pedidos):
        return fala
    if len(pedidos) == 1:
        i = pedidos[0]
        if i > 0 and not _ids_na_fala(trechos[i]) and _menciona_prato(trechos[i - 1], nome, prato_id):
            return fala
    return None


def confirmou_prato(aceite: Any, nome: str, falas: Iterable[Fala], *, prato_id: int | None = None) -> bool:
    """Mantém o argumento de citação antigo; o aceite vem do turno real, lido pelo servidor."""
    return aceite_da_receita(prato_id, nome, falas) is not None


def custo_por_porcao(prato: dict[str, Any]) -> float:
    """Usa o CMV do lote para evitar arredondamento duplo nas contas por porção."""
    return prato["cmv_total"] / prato["porcoes"]


def cenarios_da_consultora(args: dict) -> tuple[list[float], list[str], int, str]:
    margens = args.get("margens")
    if not isinstance(margens, list) or len(margens) not in (2, 3):
        raise ValueError("escolha 2 ou 3 margens para o prato")
    if any(type(m) not in (int, float) or not 0 < m < 1 for m in margens):
        raise ValueError("use margens em fração entre 0 e 1 (por exemplo, 0.2 para 20%)")
    if margens != sorted(set(margens)):
        raise ValueError("informe margens distintas da menor para a maior")

    nomes = args.get("nomes")
    if not isinstance(nomes, list) or len(nomes) != len(margens):
        raise ValueError("informe um nome para cada margem, na mesma ordem")
    if any(not isinstance(nome, str) or not 1 <= len(nome.strip()) <= 50 for nome in nomes):
        raise ValueError("use nomes de cenário com até 50 caracteres")
    nomes = [nome.strip() for nome in nomes]
    if len({nome.casefold() for nome in nomes}) != len(nomes):
        raise ValueError("use nomes distintos para os cenários")

    escolhido = args.get("cenario_recomendado")
    if type(escolhido) is not int or not 1 <= escolhido <= len(margens):
        raise ValueError("cenario_recomendado deve indicar a posição (começando em 1)")
    recomendacao = args.get("recomendacao")
    if not isinstance(recomendacao, str) or not 1 <= len(recomendacao.strip()) <= 280:
        raise ValueError("escreva uma justificativa de até 280 caracteres para o cenário recomendado")
    return margens, nomes, escolhido, recomendacao.strip()


def valor_da_meta(args: dict, chave: str) -> float | None:
    valor = args.get(chave)
    if valor is None:
        return None
    if type(valor) not in (int, float) or not math.isfinite(valor) or valor < 0 or round(valor, 2) != valor:
        raise ValueError(f"{chave} deve ser um valor em R$ maior ou igual a zero, em centavos inteiros")
    return valor


def porcoes_para_meta(cmv: float, preco: float, meta: float, *, perfil: dict[str, Any] | None = None) -> int | None:
    if meta == 0:
        return 0
    contribuicao = costing.avaliar_preco(preco, cmv, perfil=perfil)["lucro"]
    if contribuicao <= 0:
        return None
    return math.ceil(round(meta / contribuicao, 9))


def ficha_atual(store: Store, prato: dict[str, Any]) -> tuple[float, dict[str, Any]] | str:
    """Projeção de custo atual da própria ficha, sem mexer no preço escolhido ou nas reservas."""
    from .tools import _resolver_ingredientes

    receita = store.obter_receita(prato["receita_id"]) if prato.get("receita_id") else None
    ingredientes = prato["dados"].get("ingredientes") or ((receita["dados"].get("ingredientes") or []) if receita else [])
    porcoes = prato["porcoes"] or (receita["porcoes"] if receita else None)
    if not ingredientes or not porcoes or not math.isfinite(porcoes) or porcoes <= 0:
        return "Falta uma ficha com ingredientes, quantidades e rendimento."
    # As reservas da produção deste mesmo prato fazem parte do seu custo. Para os outros pratos, mantêm-se indisponíveis.
    despensa = Store(store.db_path, store.xlsx_path, ignorar_prato_id=prato["id"]) if prato["status"] == "aceito" else store
    try:
        resultado = _resolver_ingredientes(despensa, ingredientes)
    except ValueError as exc:
        return str(exc)
    if resultado["pendencias"]:
        return resultado["pendencias"][0]
    if len(resultado["itens"]) != len(ingredientes) or not 0 < resultado["cmv_total"] < float("inf"):
        return "Faltam dados para calcular o custo completo do lote."
    return porcoes, resultado


def cotacao(prato: dict[str, Any], porcoes: float, resultado: dict[str, Any], perfil: dict[str, Any]) -> str:
    """Identifica a ficha, os valores e a composição exibidos ao escolher um preço."""
    ingredientes = [(i["ingrediente"], i["quantidade_receita"], i.get("item_id"), i.get("usa_da_despensa_base"),
                     i.get("custo_uso_total")) for i in resultado["itens"]]
    dados = (prato["id"], prato["receita_id"], porcoes, resultado["cmv_total"], resultado["compras"], ingredientes, perfil)
    return hashlib.sha256(json.dumps(dados, sort_keys=True).encode()).hexdigest()[:12]


def cenarios_atuais(cmv: float, perfil: dict[str, Any], dados: dict[str, Any]) -> dict[str, Any]:
    """Reaplica margens e metas escolhidas aos custos e descontos de agora."""
    cenarios_antigos = dados.get("cenarios") or []
    margens = [c["margem_alvo"] for c in cenarios_antigos] or costing.MARGENS_PADRAO
    nomes = [c["nome"] for c in cenarios_antigos] or costing.NOMES_PADRAO
    passo = dados.get("arredondar_para") or 0.50
    proposta = costing.cenarios_preco(cmv, margens=margens, nomes=nomes, arredondar_para=passo, perfil=perfil)
    if len({c["preco_sugerido"] for c in proposta["cenarios"]}) != len(margens):
        proposta = costing.cenarios_preco(cmv, margens=margens, nomes=nomes, arredondar_para=0.01, perfil=perfil)
    lucro_alvo, custos_fixos = dados.get("lucro_alvo"), dados.get("custos_fixos")
    if lucro_alvo is not None or custos_fixos is not None:
        meta = (lucro_alvo or 0) + (custos_fixos or 0)
        for cenario in proposta["cenarios"]:
            cenario["porcoes_para_meta"] = porcoes_para_meta(cmv, cenario["preco_sugerido"], meta, perfil=perfil)
    return {**proposta, "arredondar_para": passo, "cenario_recomendado": dados.get("cenario_recomendado"),
            "recomendacao": dados.get("recomendacao") or "", "lucro_alvo": lucro_alvo, "custos_fixos": custos_fixos}


def consultar(store: Store) -> dict[str, Any]:
    perfil = store.perfil_precificacao()
    salvas = {linha["prato_id"]: linha for linha in store.analises_preco()}
    analises: list[dict[str, Any]] = []
    previas: list[dict[str, Any]] = []
    pendentes: list[dict[str, Any]] = []
    for prato in store.pratos():
        if prato["status"] not in {"selecionado", "cmv_calculado", "aceito"}:
            continue
        ficha = ficha_atual(store, prato)
        comum = {"prato_id": prato["id"], "prato": prato["nome"], "preco_escolhido": prato["preco_venda"]}
        if isinstance(ficha, str):
            pendentes.append({**comum, "motivo": ficha})
            continue
        porcoes, resultado = ficha
        salva = salvas.get(prato["id"])
        cmv_total = resultado["cmv_total"]
        cmv = cmv_total / porcoes
        escolhido = prato["preco_venda"]
        status = "registrado" if prato["status"] == "aceito" else "proposta" if salva else "previa"
        item = {**comum, **cenarios_atuais(cmv, perfil, salva["dados"] if salva else {}),
                "status": status, "porcoes": porcoes, "cmv_total": cmv_total, "cotacao": cotacao(prato, porcoes, resultado, perfil),
                "cmv_estimado": bool(resultado["compras"] or resultado["estimativas"]),
                "resultado_escolhido": costing.avaliar_preco(escolhido, cmv, perfil=perfil) if escolhido is not None else None,
                "atualizado_em": prato["atualizado_em"]}
        (previas if status == "previa" else analises).append(item)
    return {"analises": analises, "previas": previas, "pendentes": pendentes, "perfil_precificacao": perfil,
            "planos": taxas.PLANOS, "referencias": taxas.REFERENCIAS}
