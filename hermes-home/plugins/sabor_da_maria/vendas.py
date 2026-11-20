"""Vendas realizadas informadas pela pessoa, separadas do planejamento do cardápio.

O registro preserva preço e custo conhecidos na data da informação. Não representa
produção, pagamento de compra nem baixa física de estoque: esses fatos não são
inferidos de uma venda, de uma proposta ou de uma reserva de ingredientes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from .store import Store

FUSO = "America/Sao_Paulo"
LOCAL = ZoneInfo(FUSO)
CENTAVO = Decimal("0.01")
PERIODOS = ("dia", "semana", "mes")
AVISO = ("Vendas realizadas informadas por você. O lucro é estimado com o CMV salvo no registro "
         "e a taxa de 10% do desafio. Registrar ou estornar uma venda não altera o orçamento "
         "de complementos nem faz uma baixa automática no estoque.")


class ConflitoOperacao(ValueError):
    """A mesma chave de envio já foi usada com outros dados."""


def _decimal(valor: Any, rotulo: str) -> Decimal:
    if isinstance(valor, bool) or valor is None or valor == "":
        raise ValueError(f"{rotulo} inválido")
    try:
        numero = Decimal(str(valor).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{rotulo} inválido") from None
    if not numero.is_finite():
        raise ValueError(f"{rotulo} inválido")
    return numero


def _inteiro(valor: Any, rotulo: str) -> int:
    numero = _decimal(valor, rotulo)
    if numero <= 0 or numero != numero.to_integral_value() or numero > 2**53 - 1:
        raise ValueError(f"{rotulo} precisa ser um inteiro positivo")
    return int(numero)


def _moeda(valor: Decimal) -> float:
    return float(valor.quantize(CENTAVO, rounding=ROUND_HALF_UP))


def _instante(valor: str | datetime, rotulo: str) -> datetime:
    if isinstance(valor, datetime):
        instante = valor
    elif isinstance(valor, str):
        try:
            instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{rotulo} inválido; informe uma data ISO com fuso horário") from None
    else:
        raise ValueError(f"{rotulo} inválido; informe uma data ISO com fuso horário")
    if instante.tzinfo is None or instante.utcoffset() is None:
        raise ValueError(f"{rotulo} precisa incluir o fuso horário")
    return instante.astimezone(timezone.utc)


def _agora(agora: datetime | None) -> datetime:
    return _instante(agora or datetime.now(timezone.utc), "agora")


def _iso(instante: datetime) -> str:
    return instante.astimezone(timezone.utc).isoformat(timespec="seconds")


def _totais(linhas: list[dict[str, Any]]) -> dict[str, Any]:
    ativas = [linha for linha in linhas if linha["status"] == "confirmada"]
    bruto = sum((Decimal(str(l["preco_unitario"])) * l["quantidade"] for l in ativas), Decimal(0))
    custo = sum((Decimal(str(l["cmv_unitario"])) * l["quantidade"] for l in ativas), Decimal(0))
    return {"quantidade": sum(l["quantidade"] for l in ativas), "registros": len(ativas),
            "faturamento_bruto": _moeda(bruto), "taxa_plataforma": _moeda(bruto * Decimal("0.10")),
            "recebe_liquido": _moeda(bruto * Decimal("0.90")), "cmv_total": _moeda(custo),
            "lucro_estimado": _moeda(bruto * Decimal("0.90") - custo)}


def _venda(linha: Any) -> dict[str, Any]:
    venda = dict(linha)
    venda.update({k: v for k, v in _totais([{**venda, "status": "confirmada"}]).items()
                  if k not in ("quantidade", "registros")})
    return venda


def _limites(periodo: str, agora: datetime) -> tuple[datetime, datetime]:
    if periodo not in PERIODOS:
        raise ValueError("período inválido; use dia, semana ou mes")
    dia = agora.astimezone(LOCAL).replace(hour=0, minute=0, second=0, microsecond=0)
    if periodo == "dia":
        return dia, dia + timedelta(days=1)
    if periodo == "semana":
        inicio = dia - timedelta(days=dia.weekday())
        return inicio, inicio + timedelta(days=7)
    inicio = dia.replace(day=1)
    fim = inicio.replace(year=inicio.year + 1, month=1) if inicio.month == 12 else inicio.replace(month=inicio.month + 1)
    return inicio, fim


def registrar(store: Store, dados: dict[str, Any], *, agora: datetime | None = None) -> dict[str, Any]:
    if not isinstance(dados, dict):
        raise ValueError("envie os dados da venda em um objeto")
    try:
        operacao = str(UUID(str(dados.get("operacao") or "")))
    except (ValueError, AttributeError):
        raise ValueError("informe a chave UUID da operação para evitar vendas duplicadas") from None
    prato_id = _inteiro(dados.get("prato_id"), "prato")
    quantidade = _inteiro(dados.get("quantidade"), "quantidade de porções")
    informado = dados.get("preco_unitario") is not None
    preco = _decimal(dados["preco_unitario"], "preço por porção") if informado else None
    if preco is not None and (preco <= 0 or preco > Decimal("1e12") or preco != preco.quantize(CENTAVO, rounding=ROUND_HALF_UP)):
        raise ValueError("preço por porção precisa ser positivo, com até duas casas decimais")
    data_informada = dados.get("vendido_em") is not None
    vendido = _instante(dados["vendido_em"], "data da venda") if data_informada else None
    atual = _agora(agora)
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existente = conn.execute("SELECT * FROM vendas WHERE operacao = ?", (operacao,)).fetchone()
        if existente:
            if (existente["prato_id"] != prato_id or existente["quantidade"] != quantidade or
                    (informado and Decimal(str(existente["preco_unitario"])) != preco) or
                    (data_informada and existente["vendido_em"] != _iso(vendido))):
                raise ConflitoOperacao("essa operação já foi registrada com outros dados; confira a venda antes de tentar de novo")
            return {"venda": _venda(existente), "ja_registrada": True}
        vendido = vendido or atual
        if vendido > atual:
            raise ValueError("uma venda realizada não pode ter data futura")
        prato = conn.execute("SELECT * FROM pratos WHERE id = ?", (prato_id,)).fetchone()
        if not prato or prato["status"] != "aceito" or prato["preco_venda"] is None:
            raise ValueError("registre o prato aprovado no cardápio e escolha o preço antes de informar a venda")
        preco = preco if preco is not None else _decimal(prato["preco_venda"], "preço registrado no cardápio")
        if preco <= 0:
            raise ValueError("o prato precisa de um preço de venda positivo")
        if prato["cmv_total"] is not None and prato["porcoes"]:
            custo = _decimal(prato["cmv_total"], "CMV") / _decimal(prato["porcoes"], "rendimento")
        else:
            custo = _decimal(prato["cmv_porcao"], "CMV por porção")
        if custo < 0 or custo * quantidade > Decimal("1e12") or preco * quantidade > Decimal("1e12"):
            raise ValueError("os valores da venda excedem o limite permitido")
        cursor = conn.execute("""INSERT INTO vendas(operacao,prato_id,prato,quantidade,preco_unitario,cmv_unitario,
                                  vendido_em,registrado_em,status) VALUES(?,?,?,?,?,?,?,?,'confirmada')""",
                              (operacao, prato_id, prato["nome"], quantidade, float(preco), float(custo), _iso(vendido), _iso(atual)))
        venda = conn.execute("SELECT * FROM vendas WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return {"venda": _venda(venda), "ja_registrada": False}


def _mudar_status(store: Store, venda_id: int, status: str, agora: datetime | None) -> dict[str, Any]:
    venda_id = _inteiro(venda_id, "venda")
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        venda = conn.execute("SELECT * FROM vendas WHERE id = ?", (venda_id,)).fetchone()
        if not venda:
            raise KeyError("venda não encontrada")
        repetida = venda["status"] == status
        if not repetida:
            conn.execute("UPDATE vendas SET status = ?, estornado_em = ? WHERE id = ?",
                         (status, _iso(_agora(agora)) if status == "estornada" else None, venda_id))
            venda = conn.execute("SELECT * FROM vendas WHERE id = ?", (venda_id,)).fetchone()
        return {"venda": _venda(venda), "ja_estornada" if status == "estornada" else "ja_restaurada": repetida}


def estornar(store: Store, venda_id: int, *, agora: datetime | None = None) -> dict[str, Any]:
    return _mudar_status(store, venda_id, "estornada", agora)


def restaurar(store: Store, venda_id: int, *, agora: datetime | None = None) -> dict[str, Any]:
    return _mudar_status(store, venda_id, "confirmada", agora)


def resumo(store: Store, *, agora: datetime | None = None) -> dict[str, Any]:
    atual = _agora(agora)
    periodos = {}
    with store.connect() as conn:
        conn.execute("BEGIN")  # Os três períodos são lidos na mesma fotografia do banco.
        possui = bool(conn.execute("SELECT 1 FROM vendas WHERE status='confirmada' LIMIT 1").fetchone())
        for periodo in PERIODOS:
            inicio, fim = _limites(periodo, atual)
            linhas = [dict(r) for r in conn.execute("SELECT quantidade,preco_unitario,cmv_unitario,status FROM vendas WHERE vendido_em >= ? AND vendido_em < ?",
                                                  (_iso(inicio), _iso(fim)))]
            periodos[periodo] = {"inicio": inicio.isoformat(), "fim": fim.isoformat(), **_totais(linhas)}
    return {"fuso": FUSO, "referencia": _iso(atual), "possui_registros": possui, "periodos": periodos}


def consultar(store: Store, periodo: str = "dia", *, agora: datetime | None = None, limite: int = 200) -> dict[str, Any]:
    atual = _agora(agora)
    inicio, fim = _limites(periodo, atual)
    if type(limite) is not int or not 1 <= limite <= 1000:
        raise ValueError("limite de vendas inválido")
    with store.connect() as conn:
        todas = [dict(r) for r in conn.execute("SELECT * FROM vendas WHERE vendido_em >= ? AND vendido_em < ? ORDER BY vendido_em DESC,id DESC",
                                             (_iso(inicio), _iso(fim)))]
    return {"periodo": periodo, "inicio": inicio.isoformat(), "fim": fim.isoformat(), "fuso": FUSO,
            "resumo": {"inicio": inicio.isoformat(), "fim": fim.isoformat(), **_totais(todas)},
            "vendas": [_venda(linha) for linha in todas[:limite]], "limite": limite,
            "total_registros": len(todas), "truncado": len(todas) > limite, "aviso": AVISO}
