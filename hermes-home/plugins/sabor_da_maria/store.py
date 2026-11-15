"""Persistência em SQLite dos dados do Sabor da Maria.

Banco embutido (stdlib ``sqlite3``), sem serviço externo: cabe no mesmo volume
do Hermes e sobrevive entre sessões. Tabelas:

* ``ingredientes``  — despensa normalizada (estoque, custo unitário em R$/kg, R$/L ou R$/un)
* ``perfil``        — equipamentos, técnicas e restrições operacionais elicitadas
* ``receitas``      — candidatas conferidas (ingredientes, origem, pendências)
* ``pratos``        — CMV calculado e, quando aceito, preço escolhido
* ``compras``       — compras complementares planejadas/confirmadas/canceladas (orçamento)
* ``reservas``      — estoque da despensa reservado por prato aceito (disponível = estoque − reservas)
* ``carrinho``      — itens que ela separou para comprar (a consultora mexe a pedido dela); prato = ingredientes com valor;
                      o que já está na despensa aparece com valor mas não gasta orçamento
* ``salvos``        — pratos (com ingredientes e valores) e ingredientes avulsos que ela guardou para usar de novo
* ``config``        — orçamento inicial, origem da planilha e perfil do negócio

Reservas e compras confirmadas só existem para pratos ``aceito``; ``cardapio_remover`` as
libera. Um ``Store(ignorar_prato_id=X)`` desconta do cálculo as reservas e compras do próprio
prato X, para que repetir o registro de X (retry, troca de preço) não conte duas vezes.

O carrinho é rascunho de compra: ``saldo = restante − carrinho``. Itens ligados a uma receita saem do
carrinho quando o prato dela é aceito (viram compras comprometidas), para o mesmo gasto não contar duas vezes.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from . import negocio as negocio_mod
from .matching import best_matches
from .units import format_base, norm_text, parse_pantry_unit
from .xlsx_reader import read_table

_LOCK = threading.RLock()


class OrcamentoEstourado(ValueError):
    """O item (ou a nova quantidade) não cabe no saldo do orçamento."""

    def __init__(self, falta: float, saldo: float):
        self.falta = round(falta, 2)
        self.saldo = round(saldo, 2)
        super().__init__(f"não cabe no saldo: faltam R$ {self.falta:.2f} (saldo atual R$ {self.saldo:.2f})".replace(".", ","))

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredientes (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE,
    nome_norm TEXT NOT NULL,
    unidade_original TEXT,
    unidade_base TEXT NOT NULL,
    estoque_base REAL NOT NULL DEFAULT 0,
    comprado_base REAL,
    preco_total REAL,
    custo_unitario_base REAL,
    conteudo_un_base REAL,
    conteudo_un_unidade TEXT,
    origem TEXT NOT NULL DEFAULT 'planilha',
    observacao TEXT,
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS perfil (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receitas (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    fonte_url TEXT,
    porcoes REAL,
    dados TEXT NOT NULL,
    status TEXT NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pratos (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    receita_id INTEGER,
    porcoes REAL,
    cmv_total REAL,
    cmv_porcao REAL,
    preco_venda REAL,
    status TEXT NOT NULL,
    dados TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS compras (
    id INTEGER PRIMARY KEY,
    prato_id INTEGER,
    ingrediente TEXT NOT NULL,
    quantidade REAL,
    unidade TEXT,
    preco_estimado REAL NOT NULL,
    status TEXT NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservas (
    id INTEGER PRIMARY KEY,
    prato_id INTEGER NOT NULL,
    ingrediente_id INTEGER NOT NULL,
    quantidade_base REAL NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reservas_prato ON reservas(prato_id);
CREATE INDEX IF NOT EXISTS idx_reservas_ingrediente ON reservas(ingrediente_id);
CREATE TABLE IF NOT EXISTS carrinho (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    quantidade REAL NOT NULL DEFAULT 1,
    unidade TEXT,
    preco_total REAL NOT NULL,
    estimado INTEGER NOT NULL DEFAULT 0,
    origem TEXT NOT NULL DEFAULT 'manual',
    receita_id INTEGER,
    motivo TEXT,
    prato TEXT,
    ja_tem INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_carrinho_receita ON carrinho(receita_id);
CREATE TABLE IF NOT EXISTS salvos (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    nome TEXT NOT NULL,
    nome_norm TEXT NOT NULL,
    quantidade REAL,
    unidade TEXT,
    preco_total REAL,
    ingredientes TEXT,
    origem TEXT NOT NULL DEFAULT 'tela',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    UNIQUE (tipo, nome_norm)
);
CREATE TABLE IF NOT EXISTS config (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser()


def default_db_path() -> Path:
    return Path(os.environ.get("SABOR_DA_MARIA_DB") or hermes_home() / "sabor_da_maria" / "sabor_da_maria.db")


def default_xlsx_path() -> Path:
    return Path(os.environ.get("SABOR_DA_MARIA_XLSX") or hermes_home() / "sabor_da_maria" / "entrada" / "despensa_dona_maria.xlsx")


def default_budget() -> float:
    return float(os.environ.get("SABOR_DA_MARIA_ORCAMENTO", "80").replace(",", "."))


def _col(row: dict[str, Any], prefix: str) -> Any:
    """Coluna cujo nome começa com ``prefix`` (tolera acentos/sufixos como '(R$)')."""
    for key, value in row.items():
        if norm_text(key).startswith(norm_text(prefix)):
            return value
    raise KeyError(f"Coluna começando com {prefix!r} não encontrada em {list(row)}")


def _item_carrinho(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["estimado"] = bool(d["estimado"])
    d["ja_tem"] = bool(d.get("ja_tem"))
    d["preco_total"] = round(float(d["preco_total"]), 2)
    return d


def _nome_limpo(valor: Any, rotulo: str = "item") -> str:
    nome = " ".join(str(valor or "").split())
    if not nome:
        raise ValueError(f"informe o nome do {rotulo}")
    if len(nome) > 80:
        raise ValueError(f"o nome do {rotulo} passa de 80 caracteres")
    return nome


def _unidade_limpa(valor: Any) -> Optional[str]:
    return " ".join(str(valor).split())[:20] if valor else None


def _numero(valor: Any, rotulo: str) -> float:
    """Número vindo da tela ou de uma tool: aceita 12, 12.5 e "12,50"; recusa texto, vazio e booleano."""
    if isinstance(valor, bool) or valor is None or valor == "":
        raise ValueError(f"{rotulo} inválido")
    try:
        numero = float(str(valor).strip().replace(",", ".")) if isinstance(valor, str) else float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{rotulo} inválido") from None
    if numero != numero or numero in (float("inf"), float("-inf")):
        raise ValueError(f"{rotulo} inválido")
    return numero


MAX_INGREDIENTES = 40


def _linhas_do_prato(ingredientes: Any) -> list[dict[str, Any]]:
    """Valida os ingredientes de um prato. Cada um precisa de nome e de valor (R$): um prato sem o valor de cada
    ingrediente não entra, nem no carrinho nem nos salvos. ``ja_tem`` (já está na despensa) pode custar 0."""
    if not isinstance(ingredientes, list) or not ingredientes:
        raise ValueError("informe os ingredientes do prato, cada um com quantidade e valor (preco_total em R$)")
    if len(ingredientes) > MAX_INGREDIENTES:
        raise ValueError(f"no máximo {MAX_INGREDIENTES} ingredientes por prato")
    linhas = []
    for i in ingredientes:
        if not isinstance(i, dict):
            raise ValueError("cada ingrediente precisa ser um objeto {nome, quantidade, unidade, preco_total}")
        nome = _nome_limpo(i.get("nome"), "ingrediente")
        ja_tem = bool(i.get("ja_tem"))
        if i.get("preco_total") in (None, ""):
            raise ValueError(f"informe o valor de {nome} (preco_total em R$)")
        preco = round(_numero(i.get("preco_total"), f"valor de {nome}"), 2)
        if preco < 0 or (preco == 0 and not ja_tem):
            raise ValueError(f"informe o valor de {nome} (maior que zero)")
        qtd = 1.0 if i.get("quantidade") in (None, "") else _numero(i.get("quantidade"), f"quantidade de {nome}")
        if qtd <= 0:
            raise ValueError(f"a quantidade de {nome} precisa ser maior que zero")
        linhas.append({"nome": nome, "quantidade": qtd, "unidade": _unidade_limpa(i.get("unidade")), "preco_total": preco, "ja_tem": ja_tem})
    return linhas


def _salvo(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d.pop("nome_norm", None)
    d["ingredientes"] = json.loads(d["ingredientes"]) if d.get("ingredientes") else []
    if d.get("preco_total") is not None:
        d["preco_total"] = round(float(d["preco_total"]), 2)
    return d


def _ingredient_dict(row: sqlite3.Row, reservado: float = 0.0) -> dict[str, Any]:
    d = dict(row)
    base = d["unidade_base"]
    reservado = float(reservado or 0.0)
    disponivel = max(0.0, float(d["estoque_base"]) - reservado)
    d["reservado_base"] = round(reservado, 6)
    d["disponivel_base"] = round(disponivel, 6)
    d["estoque_legivel"] = format_base(d["estoque_base"], base)
    d["reservado_legivel"] = format_base(reservado, base)
    d["disponivel_legivel"] = format_base(disponivel, base)
    if d.get("custo_unitario_base") is not None:
        d["custo_unitario_legivel"] = f"R$ {d['custo_unitario_base']:.2f}/{d['unidade_base']}".replace(".", ",")
    else:
        d["custo_unitario_legivel"] = "sem preço registrado"
    return d


class Store:
    def __init__(self, db_path: Optional[str | Path] = None, xlsx_path: Optional[str | Path] = None,
                 orcamento: Optional[float] = None, ignorar_prato_id: Optional[int] = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.xlsx_path = Path(xlsx_path) if xlsx_path else default_xlsx_path()
        self._orcamento = orcamento
        # Reservas e compras deste prato não contam em disponibilidade/orçamento (reavaliação do próprio prato).
        self.ignorar_prato_id = int(ignorar_prato_id) if ignorar_prato_id is not None else None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrar(conn)

    @staticmethod
    def _migrar(conn: sqlite3.Connection) -> None:
        """Bancos criados por versões anteriores ganham as colunas novas do carrinho (prato e ja_tem)."""
        colunas = {r[1] for r in conn.execute("PRAGMA table_info(carrinho)")}
        if "prato" not in colunas:
            conn.execute("ALTER TABLE carrinho ADD COLUMN prato TEXT")
        if "ja_tem" not in colunas:
            conn.execute("ALTER TABLE carrinho ADD COLUMN ja_tem INTEGER NOT NULL DEFAULT 0")

    # ------------------------------------------------------------------ infra
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Conexão curta por operação: commit no sucesso, rollback no erro, sempre fecha."""
        conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _config_get(self, conn: sqlite3.Connection, key: str) -> Optional[str]:
        row = conn.execute("SELECT valor FROM config WHERE chave = ?", (key,)).fetchone()
        return row["valor"] if row else None

    def _config_set(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute("INSERT INTO config(chave, valor) VALUES(?, ?) ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor", (key, value))

    def _reservas_map(self, conn: sqlite3.Connection) -> dict[int, float]:
        """ingrediente_id -> quantidade reservada por pratos aceitos (menos o prato ignorado)."""
        rows = conn.execute(
            """SELECT r.ingrediente_id AS iid, SUM(r.quantidade_base) AS q FROM reservas r
               JOIN pratos p ON p.id = r.prato_id
               WHERE p.status = 'aceito' AND (? IS NULL OR r.prato_id IS NOT ?)
               GROUP BY r.ingrediente_id""",
            (self.ignorar_prato_id, self.ignorar_prato_id),
        ).fetchall()
        return {int(r["iid"]): float(r["q"] or 0.0) for r in rows}

    # ------------------------------------------------------------------ carga
    def ensure_loaded(self, force: bool = False) -> dict[str, Any]:
        """Carrega a planilha na primeira vez (ou quando ``force``). Idempotente."""
        with _LOCK, self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM ingredientes").fetchone()[0]
            if count and not force:
                return {"carregado": False, "itens": count, "origem": self._config_get(conn, "planilha")}
            if not self.xlsx_path.exists():
                raise FileNotFoundError(f"Planilha não encontrada: {self.xlsx_path}")
            despensa = read_table(self.xlsx_path, "Despensa")
            precos = read_table(self.xlsx_path, "Precos")
            precos_por_nome = {norm_text(str(_col(r, "Ingrediente"))): r for r in precos}
            conn.execute("DELETE FROM ingredientes WHERE origem = 'planilha'")
            avisos: list[str] = []
            for row in despensa:
                nome = str(_col(row, "Ingrediente")).strip()
                spec = parse_pantry_unit(str(_col(row, "Unidade")))
                estoque_base = float(_col(row, "Quantidade em estoque") or 0) * spec.factor
                preco_row = precos_por_nome.get(norm_text(nome))
                comprado_base = preco_total = custo = None
                if preco_row is not None:
                    spec_p = parse_pantry_unit(str(_col(preco_row, "Unidade")))
                    comprado_base = float(_col(preco_row, "Quantidade comprada") or 0) * spec_p.factor
                    preco_total = float(_col(preco_row, "Preco total pago") or 0)
                    custo = (preco_total / comprado_base) if comprado_base else None
                else:
                    avisos.append(f"{nome}: sem linha na aba Precos")
                obs: list[str] = []
                if spec.is_package:
                    obs.append(f"embalagem '{spec.original}' normalizada para {spec.base}")
                if spec.base == "un" and "ovo" not in norm_text(nome):
                    obs.append("vendido por unidade sem peso/volume informado: pergunte o conteúdo da embalagem antes de usar em receita medida em g/ml")
                conn.execute(
                    """INSERT INTO ingredientes(nome, nome_norm, unidade_original, unidade_base, estoque_base, comprado_base,
                       preco_total, custo_unitario_base, origem, observacao, atualizado_em)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(nome) DO UPDATE SET nome_norm=excluded.nome_norm, unidade_original=excluded.unidade_original,
                       unidade_base=excluded.unidade_base, estoque_base=excluded.estoque_base, comprado_base=excluded.comprado_base,
                       preco_total=excluded.preco_total, custo_unitario_base=excluded.custo_unitario_base,
                       observacao=excluded.observacao, atualizado_em=excluded.atualizado_em""",
                    (nome, norm_text(nome), spec.original, spec.base, estoque_base, comprado_base, preco_total, custo,
                     "planilha", "; ".join(obs) or None, _now()),
                )
            self._config_set(conn, "planilha", str(self.xlsx_path))
            self._config_set(conn, "carregado_em", _now())
            if self._config_get(conn, "orcamento_inicial") is None:
                self._config_set(conn, "orcamento_inicial", str(self._orcamento if self._orcamento is not None else default_budget()))
            return {"carregado": True, "itens": len(despensa), "origem": str(self.xlsx_path), "avisos": avisos}

    # ------------------------------------------------------------------ despensa
    def ingredientes(self) -> list[dict[str, Any]]:
        self.ensure_loaded()
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM ingredientes ORDER BY id").fetchall()
            reservas = self._reservas_map(conn)
        return [_ingredient_dict(r, reservas.get(int(r["id"]), 0.0)) for r in rows]

    def obter(self, nome: str) -> Optional[dict[str, Any]]:
        self.ensure_loaded()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ingredientes WHERE nome = ? OR nome_norm = ?", (nome, norm_text(nome))).fetchone()
            reservas = self._reservas_map(conn) if row else {}
        return _ingredient_dict(row, reservas.get(int(row["id"]), 0.0)) if row else None

    def buscar(self, nome: str, limit: int = 3) -> list[dict[str, Any]]:
        itens = {i["nome"]: i for i in self.ingredientes()}
        exato = self.obter(nome)
        campos = ("estoque_legivel", "estoque_base", "reservado_legivel", "disponivel_legivel", "disponivel_base",
                  "unidade_base", "custo_unitario_base", "custo_unitario_legivel", "observacao")
        if exato:
            return [{"nome": exato["nome"], "score": 1.0, "confiavel": True, **{k: exato[k] for k in campos}}]
        out = []
        for m in best_matches(nome, list(itens), limit=limit):
            item = itens[m["nome"]]
            out.append({**m, **{k: item[k] for k in campos}})
        return out

    def melhor_item(self, nome: str) -> Optional[dict[str, Any]]:
        """Item da despensa quando o casamento é confiável; senão None."""
        matches = self.buscar(nome, limit=1)
        if matches and matches[0]["confiavel"]:
            return self.obter(matches[0]["nome"])
        return None

    def atualizar_conteudo(self, nome: str, quantidade: float, unidade: str) -> dict[str, Any]:
        item = self.obter(nome)
        if not item:
            raise KeyError(f"Ingrediente {nome!r} não está na despensa")
        spec = parse_pantry_unit(unidade)
        if spec.base == "un":
            raise ValueError("Informe o conteúdo em g, kg, ml ou L")
        with _LOCK, self.connect() as conn:
            conn.execute("UPDATE ingredientes SET conteudo_un_base = ?, conteudo_un_unidade = ?, observacao = ?, atualizado_em = ? WHERE id = ?",
                         (float(quantidade) * spec.factor, spec.base,
                          f"1 un = {format_base(float(quantidade) * spec.factor, spec.base)} (informado pela Dona Maria)", _now(), item["id"]))
        return self.obter(item["nome"]) or {}

    def registrar_compra(self, nome: str, quantidade: float, unidade: str, preco_total: float, *, novo: bool = False) -> dict[str, Any]:
        """Entrada de estoque com preço: custo unitário vira média ponderada do que foi pago."""
        spec = parse_pantry_unit(unidade)
        qtd_base = float(quantidade) * spec.factor
        item = self.obter(nome)
        with _LOCK, self.connect() as conn:
            if item is None:
                if not novo:
                    raise KeyError(f"Ingrediente {nome!r} não está na despensa; passe novo=true para cadastrar")
                conn.execute(
                    """INSERT INTO ingredientes(nome, nome_norm, unidade_original, unidade_base, estoque_base, comprado_base, preco_total,
                       custo_unitario_base, origem, observacao, atualizado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (nome.strip(), norm_text(nome), spec.original, spec.base, qtd_base, qtd_base, float(preco_total),
                     float(preco_total) / qtd_base if qtd_base else None, "compra", "cadastrado após compra complementar", _now()),
                )
            else:
                if item["unidade_base"] != spec.base:
                    raise ValueError(f"{nome!r} é controlado em {item['unidade_base']}; informe a compra nessa base")
                comprado = (item["comprado_base"] or 0) + qtd_base
                pago = (item["preco_total"] or 0) + float(preco_total)
                conn.execute(
                    "UPDATE ingredientes SET estoque_base = estoque_base + ?, comprado_base = ?, preco_total = ?, custo_unitario_base = ?, atualizado_em = ? WHERE id = ?",
                    (qtd_base, comprado, pago, (pago / comprado) if comprado else None, _now(), item["id"]),
                )
        return self.obter(nome.strip()) or {}

    # ------------------------------------------------------------------ perfil
    def perfil(self) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute("SELECT chave, valor FROM perfil").fetchall()
        out = {"equipamentos": {}, "tecnicas": {}, "restricoes": {}, "observacoes": []}
        for r in rows:
            out[r["chave"]] = json.loads(r["valor"])
        return out

    def perfil_salvar(self, partes: dict[str, Any]) -> dict[str, Any]:
        atual = self.perfil()
        for chave in ("equipamentos", "tecnicas", "restricoes"):
            novo = partes.get(chave)
            if isinstance(novo, dict):
                atual[chave] = {**atual.get(chave, {}), **{norm_text(k).replace(" ", "_"): v for k, v in novo.items()}}
        obs = partes.get("observacoes")
        if obs:
            lista = atual.get("observacoes") or []
            for item in (obs if isinstance(obs, list) else [obs]):
                if str(item).strip() and str(item).strip() not in lista:
                    lista.append(str(item).strip())
            atual["observacoes"] = lista
        with _LOCK, self.connect() as conn:
            for chave in ("equipamentos", "tecnicas", "restricoes", "observacoes"):
                conn.execute("INSERT INTO perfil(chave, valor, atualizado_em) VALUES(?,?,?) ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, atualizado_em = excluded.atualizado_em",
                             (chave, json.dumps(atual.get(chave, {} if chave != "observacoes" else []), ensure_ascii=False), _now()))
        return atual

    # ------------------------------------------------------------------ receitas e pratos
    def salvar_receita(self, nome: str, fonte_url: Optional[str], porcoes: Optional[float], dados: dict[str, Any], status: str) -> int:
        with _LOCK, self.connect() as conn:
            cur = conn.execute("INSERT INTO receitas(nome, fonte_url, porcoes, dados, status, criado_em) VALUES(?,?,?,?,?,?)",
                               (nome, fonte_url, porcoes, json.dumps(dados, ensure_ascii=False), status, _now()))
            return int(cur.lastrowid)

    def obter_receita(self, receita_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM receitas WHERE id = ?", (receita_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["dados"] = json.loads(d["dados"])
        return d

    def receitas(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id, nome, fonte_url, porcoes, status, criado_em FROM receitas ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def salvar_prato(self, nome: str, receita_id: Optional[int], porcoes: Optional[float], cmv_total: float,
                     cmv_porcao: Optional[float], dados: dict[str, Any]) -> int:
        with _LOCK, self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO pratos(nome, receita_id, porcoes, cmv_total, cmv_porcao, preco_venda, status, dados, criado_em, atualizado_em) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (nome, receita_id, porcoes, cmv_total, cmv_porcao, None, "cmv_calculado", json.dumps(dados, ensure_ascii=False), _now(), _now()))
            return int(cur.lastrowid)

    def obter_prato(self, prato_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM pratos WHERE id = ?", (prato_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["dados"] = json.loads(d["dados"])
        return d

    def pratos(self, status: Optional[str] = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if status:
                rows = conn.execute("SELECT * FROM pratos WHERE status = ? ORDER BY id", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM pratos ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["dados"] = json.loads(d["dados"])
            out.append(d)
        return out

    def aceitar_prato(self, prato_id: int, preco_venda: float, compras: list[dict[str, Any]],
                      reservas: Optional[list[dict[str, Any]]] = None, *, confirmar_compras: bool = True,
                      dados: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Aceite numa transação só: status + preço, compras comprometidas e reservas de estoque.

        Idempotente por ``prato_id``: compras e reservas anteriores do mesmo prato são substituídas,
        então repetir a chamada (retry, troca de preço) nunca duplica orçamento nem reserva.
        """
        prato = self.obter_prato(prato_id)
        if not prato:
            raise KeyError(f"Prato {prato_id} não encontrado")
        with _LOCK, self.connect() as conn:
            if dados is not None:
                conn.execute("UPDATE pratos SET preco_venda = ?, status = 'aceito', dados = ?, atualizado_em = ? WHERE id = ?",
                             (float(preco_venda), json.dumps(dados, ensure_ascii=False), _now(), prato_id))
            else:
                conn.execute("UPDATE pratos SET preco_venda = ?, status = 'aceito', atualizado_em = ? WHERE id = ?", (float(preco_venda), _now(), prato_id))
            conn.execute("DELETE FROM compras WHERE prato_id = ?", (prato_id,))
            for c in compras:
                conn.execute("INSERT INTO compras(prato_id, ingrediente, quantidade, unidade, preco_estimado, status, criado_em) VALUES(?,?,?,?,?,?,?)",
                             (prato_id, str(c.get("ingrediente")), c.get("quantidade"), c.get("unidade"), float(c.get("preco_estimado") or 0),
                              "confirmada" if confirmar_compras else "planejada", _now()))
            conn.execute("DELETE FROM reservas WHERE prato_id = ?", (prato_id,))
            for r in reservas or []:
                qtd = float(r.get("quantidade_base") or 0)
                if qtd <= 1e-9 or r.get("ingrediente_id") is None:
                    continue
                conn.execute("INSERT INTO reservas(prato_id, ingrediente_id, quantidade_base, criado_em) VALUES(?,?,?,?)",
                             (prato_id, int(r["ingrediente_id"]), qtd, _now()))
        return self.obter_prato(prato_id) or {}

    def remover_prato(self, prato_id: int, motivo: Optional[str] = None) -> dict[str, Any]:
        """Tira o prato do cardápio: libera as reservas e cancela as compras comprometidas no orçamento.

        Compra real já lançada no estoque (``registrar_compra``) não é desfeita: o estoque e o custo ficam.
        """
        prato = self.obter_prato(prato_id)
        if not prato:
            raise KeyError(f"Prato {prato_id} não encontrado")
        with _LOCK, self.connect() as conn:
            reservas = conn.execute(
                """SELECT i.nome AS ingrediente, i.unidade_base, r.quantidade_base FROM reservas r
                   JOIN ingredientes i ON i.id = r.ingrediente_id WHERE r.prato_id = ? ORDER BY r.id""", (prato_id,)).fetchall()
            compras = conn.execute("SELECT * FROM compras WHERE prato_id = ? AND status IN ('confirmada', 'planejada') ORDER BY id", (prato_id,)).fetchall()
            conn.execute("UPDATE compras SET status = 'cancelada' WHERE prato_id = ? AND status IN ('confirmada', 'planejada')", (prato_id,))
            conn.execute("DELETE FROM reservas WHERE prato_id = ?", (prato_id,))
            dados = {**prato["dados"], "remocao": {"motivo": motivo, "em": _now(), "status_anterior": prato["status"]}}
            conn.execute("UPDATE pratos SET status = 'removido', dados = ?, atualizado_em = ? WHERE id = ?",
                         (json.dumps(dados, ensure_ascii=False), _now(), prato_id))
        return {
            "prato": self.obter_prato(prato_id) or {},
            "reservas_liberadas": [{"ingrediente": r["ingrediente"], "quantidade": format_base(r["quantidade_base"], r["unidade_base"])} for r in reservas],
            "compras_canceladas": [{k: c[k] for k in ("ingrediente", "quantidade", "unidade", "preco_estimado")} for c in compras],
        }

    def reservas_do_prato(self, prato_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT i.nome AS ingrediente, i.unidade_base, r.quantidade_base FROM reservas r
                   JOIN ingredientes i ON i.id = r.ingrediente_id WHERE r.prato_id = ? ORDER BY r.id""", (prato_id,)).fetchall()
        return [{"ingrediente": r["ingrediente"], "quantidade": format_base(r["quantidade_base"], r["unidade_base"]),
                 "quantidade_base": r["quantidade_base"], "unidade_base": r["unidade_base"]} for r in rows]

    def reservas_ativas(self) -> list[dict[str, Any]]:
        """Reservas de todos os pratos aceitos, para o resumo do cardápio."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT r.prato_id, p.nome AS prato, i.nome AS ingrediente, i.unidade_base, r.quantidade_base
                   FROM reservas r JOIN pratos p ON p.id = r.prato_id JOIN ingredientes i ON i.id = r.ingrediente_id
                   WHERE p.status = 'aceito' ORDER BY r.prato_id, r.id""").fetchall()
        return [{"prato_id": r["prato_id"], "prato": r["prato"], "ingrediente": r["ingrediente"],
                 "quantidade": format_base(r["quantidade_base"], r["unidade_base"])} for r in rows]

    # ------------------------------------------------------------------ orçamento
    def orcamento(self) -> dict[str, Any]:
        self.ensure_loaded()
        with self.connect() as conn:
            inicial = float(self._config_get(conn, "orcamento_inicial") or default_budget())
            rows = conn.execute(
                """SELECT c.*, p.nome AS prato FROM compras c LEFT JOIN pratos p ON p.id = c.prato_id
                   WHERE (? IS NULL OR c.prato_id IS NOT ?) ORDER BY c.id""",
                (self.ignorar_prato_id, self.ignorar_prato_id)).fetchall()
            carrinho = self._carrinho_total(conn)
        confirmadas = [dict(r) for r in rows if r["status"] == "confirmada"]
        planejadas = [dict(r) for r in rows if r["status"] == "planejada"]
        comprometido = round(sum(r["preco_estimado"] for r in confirmadas), 2)
        return {
            "inicial": inicial,
            "comprometido": comprometido,
            "restante": round(inicial - comprometido, 2),
            "carrinho": carrinho,
            "saldo": round(inicial - comprometido - carrinho, 2),
            "compras_confirmadas": confirmadas,
            "compras_planejadas": planejadas,
        }

    # ------------------------------------------------------------------ carrinho
    def _carrinho_total(self, conn: sqlite3.Connection) -> float:
        # Só o que ela vai comprar gasta saldo: o ingrediente que já tem na despensa aparece com valor, mas não conta.
        return round(float(conn.execute("SELECT COALESCE(SUM(preco_total), 0) FROM carrinho WHERE ja_tem = 0").fetchone()[0]), 2)

    def carrinho_itens(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM carrinho ORDER BY id").fetchall()
        return [_item_carrinho(r) for r in rows]

    def carrinho_resumo(self) -> dict[str, Any]:
        """Quanto ela tem (orçamento), quanto já escolheu (carrinho) e o saldo que sobra."""
        orc = self.orcamento()
        return {"inicial": orc["inicial"], "comprometido": orc["comprometido"], "carrinho": orc["carrinho"],
                "saldo": orc["saldo"], "estourou": orc["saldo"] < -0.005}

    def carrinho_pratos(self) -> list[dict[str, Any]]:
        """O carrinho agrupado por prato: cada um com os ingredientes e o valor de cada um. Avulso fica de fora."""
        grupos: dict[str, dict[str, Any]] = {}
        for i in self.carrinho_itens():
            if not i.get("prato"):
                continue
            g = grupos.setdefault(norm_text(i["prato"]), {"prato": i["prato"], "valor_do_prato": 0.0, "a_comprar": 0.0, "ingredientes": []})
            g["valor_do_prato"] = round(g["valor_do_prato"] + i["preco_total"], 2)
            if not i["ja_tem"]:
                g["a_comprar"] = round(g["a_comprar"] + i["preco_total"], 2)
            g["ingredientes"].append({k: i[k] for k in ("id", "nome", "quantidade", "unidade", "preco_total", "ja_tem", "estimado")})
        return list(grupos.values())

    def _gravar_linha(self, conn: sqlite3.Connection, nome: str, preco: float, qtd: float, un: Optional[str], *, estimado: bool,
                      origem: str, receita_id: Optional[int], motivo: Optional[str], prato: Optional[str], ja_tem: bool) -> int:
        """Põe a linha no carrinho. O mesmo ingrediente (nome + unidade) no mesmo prato soma quantidade e valor."""
        linhas = conn.execute("SELECT * FROM carrinho WHERE receita_id IS ? ORDER BY id", (receita_id,)).fetchall()
        if prato:  # mantém a grafia do prato que já está no carrinho
            prato = next((r["prato"] for r in linhas if r["prato"] and norm_text(r["prato"]) == norm_text(prato)), prato)
        igual = next((r for r in linhas
                      if norm_text(r["nome"]) == norm_text(nome) and (r["unidade"] or "") == (un or "")
                      and norm_text(r["prato"] or "") == norm_text(prato or "") and bool(r["ja_tem"]) == bool(ja_tem)), None)
        if igual is not None:
            conn.execute("UPDATE carrinho SET quantidade = ?, preco_total = ?, estimado = ? WHERE id = ?",
                         (igual["quantidade"] + qtd, round(igual["preco_total"] + preco, 2), int(bool(igual["estimado"]) or estimado), igual["id"]))
            return int(igual["id"])
        cur = conn.execute(
            "INSERT INTO carrinho(nome, quantidade, unidade, preco_total, estimado, origem, receita_id, motivo, prato, ja_tem, criado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (nome, qtd, un, preco, int(bool(estimado)), origem, receita_id, (motivo or "").strip()[:120] or None, prato, int(bool(ja_tem)), _now()))
        return int(cur.lastrowid)

    def carrinho_adicionar(self, nome: str, preco_total: float, quantidade: Optional[float] = 1.0, unidade: Optional[str] = None, *,
                           estimado: bool = False, origem: str = "manual", receita_id: Optional[int] = None,
                           motivo: Optional[str] = None, prato: Optional[str] = None, ja_tem: bool = False) -> dict[str, Any]:
        """Põe o ingrediente no carrinho se couber no saldo (``ja_tem``: já está na despensa, tem valor mas não gasta saldo)."""
        nome = _nome_limpo(nome, "item")
        preco = round(_numero(preco_total, "preço"), 2)
        if preco < 0 or (preco == 0 and not ja_tem):
            raise ValueError("informe o preço do item (maior que zero)")
        qtd = 1.0 if quantidade is None else _numero(quantidade, "quantidade")
        if qtd <= 0:
            raise ValueError("a quantidade precisa ser maior que zero")
        un = _unidade_limpa(unidade)
        custo = 0.0 if ja_tem else preco
        with _LOCK:
            saldo = self.carrinho_resumo()["saldo"]
            if custo > saldo + 1e-9:
                raise OrcamentoEstourado(custo - saldo, saldo)
            with self.connect() as conn:
                item_id = self._gravar_linha(conn, nome, preco, qtd, un, estimado=estimado, origem=origem, receita_id=receita_id,
                                             motivo=motivo, prato=prato, ja_tem=ja_tem)
                row = conn.execute("SELECT * FROM carrinho WHERE id = ?", (item_id,)).fetchone()
        return _item_carrinho(row)

    def carrinho_adicionar_prato(self, prato: str, ingredientes: Any, *, estimado: bool = False, origem: str = "manual",
                                 receita_id: Optional[int] = None) -> dict[str, Any]:
        """Põe um prato inteiro: uma linha por ingrediente, cada uma com o seu valor. Só o que ela compra gasta saldo."""
        nome_prato = _nome_limpo(prato, "prato")
        linhas = _linhas_do_prato(ingredientes)
        a_comprar = round(sum(l["preco_total"] for l in linhas if not l["ja_tem"]), 2)
        with _LOCK:
            saldo = self.carrinho_resumo()["saldo"]
            if a_comprar > saldo + 1e-9:
                raise OrcamentoEstourado(a_comprar - saldo, saldo)
            with self.connect() as conn:
                for l in linhas:
                    self._gravar_linha(conn, l["nome"], l["preco_total"], l["quantidade"], l["unidade"], estimado=estimado, origem=origem,
                                       receita_id=receita_id, motivo=None, prato=nome_prato, ja_tem=l["ja_tem"])
        return next(g for g in self.carrinho_pratos() if norm_text(g["prato"]) == norm_text(nome_prato))

    def carrinho_atualizar_quantidade(self, item_id: int, quantidade: float) -> dict[str, Any]:
        """Muda a quantidade mantendo o preço por unidade (o total da linha acompanha)."""
        qtd = _numero(quantidade, "quantidade")
        if qtd <= 0:
            raise ValueError("a quantidade precisa ser maior que zero; para tirar o item, remova-o")
        with _LOCK:
            saldo = self.carrinho_resumo()["saldo"]
            with self.connect() as conn:
                row = conn.execute("SELECT * FROM carrinho WHERE id = ?", (int(item_id),)).fetchone()
                if row is None:
                    raise KeyError(f"o item {item_id} não está no carrinho")
                novo_total = round(row["preco_total"] / row["quantidade"] * qtd, 2)
                aumento = round(novo_total - row["preco_total"], 2)
                if not row["ja_tem"] and aumento > saldo + 1e-9:
                    raise OrcamentoEstourado(aumento - saldo, saldo)
                conn.execute("UPDATE carrinho SET quantidade = ?, preco_total = ? WHERE id = ?", (qtd, novo_total, row["id"]))
                row = conn.execute("SELECT * FROM carrinho WHERE id = ?", (row["id"],)).fetchone()
        return _item_carrinho(row)

    def carrinho_remover(self, item_id: Optional[int] = None, nome: Optional[str] = None) -> list[dict[str, Any]]:
        """Tira do carrinho pelo id ou pelo nome: ingrediente, ou o prato inteiro quando o nome é o do prato.
        Nome parecido só vale se der um único item (senão pede para ela dizer qual). Devolve o que saiu."""
        with _LOCK, self.connect() as conn:
            linhas = conn.execute("SELECT * FROM carrinho ORDER BY id").fetchall()
            if item_id is not None:
                alvo = [r for r in linhas if r["id"] == int(item_id)]
            else:
                chave = norm_text(nome or "")
                if not chave:
                    raise ValueError("informe o item (nome ou id)")
                alvo = [r for r in linhas if norm_text(r["nome"]) == chave]
                if not alvo:
                    alvo = [r for r in linhas if r["prato"] and norm_text(r["prato"]) == chave]
                if not alvo:
                    parecidos: dict[str, tuple[str, list[sqlite3.Row]]] = {}
                    for r in linhas:
                        for rotulo in (r["nome"], r["prato"]):
                            n = norm_text(rotulo or "")
                            if n and (chave in n or n in chave):
                                parecidos.setdefault(n, (rotulo, []))[1].append(r)
                    if len(parecidos) > 1:
                        raise ValueError("há mais de um item parecido no carrinho (" + ", ".join(v[0] for v in parecidos.values()) + "); diga qual")
                    alvo = next(iter(parecidos.values()))[1] if parecidos else []
            if not alvo:
                raise KeyError("esse item não está no carrinho")
            ids = sorted({r["id"] for r in alvo})
            conn.execute(f"DELETE FROM carrinho WHERE id IN ({','.join('?' * len(ids))})", ids)
        return [_item_carrinho(r) for r in alvo]

    def carrinho_remover_prato(self, prato: str) -> list[dict[str, Any]]:
        with _LOCK, self.connect() as conn:
            alvo = [r for r in conn.execute("SELECT * FROM carrinho ORDER BY id").fetchall() if r["prato"] and norm_text(r["prato"]) == norm_text(prato)]
            if not alvo:
                raise KeyError(f"o prato {prato!r} não está no carrinho")
            conn.execute(f"DELETE FROM carrinho WHERE id IN ({','.join('?' * len(alvo))})", [r["id"] for r in alvo])
        return [_item_carrinho(r) for r in alvo]

    def carrinho_limpar(self) -> int:
        with _LOCK, self.connect() as conn:
            return conn.execute("DELETE FROM carrinho").rowcount

    def carrinho_montar_da_receita(self, receita_id: int, receita_nome: str, compras: list[dict[str, Any]],
                                   da_despensa: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
        """Troca as linhas desta receita pelo prato completo: o que ela compra (os preços da conferência) e o que já tem na
        despensa (com o custo, sem gastar saldo). Repetir não duplica; respeita o saldo."""
        a_comprar = [(str(c.get("ingrediente") or "").strip(), c.get("quantidade"), c.get("unidade"), round(float(c.get("preco_estimado") or 0), 2))
                     for c in compras]
        a_comprar = [item for item in a_comprar if item[0] and item[3] > 0]
        da_casa = [(str(c.get("ingrediente") or "").strip(), c.get("quantidade"), c.get("unidade"), round(float(c.get("valor") or 0), 2))
                   for c in (da_despensa or [])]
        da_casa = [item for item in da_casa if item[0]]
        prato = receita_nome.strip()[:80]
        with _LOCK:
            saldo = self.carrinho_resumo()["saldo"]
            with self.connect() as conn:
                antigo = float(conn.execute("SELECT COALESCE(SUM(preco_total), 0) FROM carrinho WHERE receita_id = ? AND ja_tem = 0", (int(receita_id),)).fetchone()[0])
                novo = round(sum(item[3] for item in a_comprar), 2)
                disponivel = round(saldo + antigo, 2)
                if novo > disponivel + 1e-9:
                    raise OrcamentoEstourado(novo - disponivel, disponivel)
                conn.execute("DELETE FROM carrinho WHERE receita_id = ?", (int(receita_id),))
                for ja_tem, itens in ((1, da_casa), (0, a_comprar)):
                    for nome, qtd, un, valor in itens:
                        conn.execute(
                            "INSERT INTO carrinho(nome, quantidade, unidade, preco_total, estimado, origem, receita_id, motivo, prato, ja_tem, criado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (nome[:80], float(qtd) if qtd else 1.0, str(un)[:20] if un else None, valor, 0 if ja_tem else 1, "receita",
                             int(receita_id), None, prato, ja_tem, _now()))
                rows = conn.execute("SELECT * FROM carrinho WHERE receita_id = ? ORDER BY id", (int(receita_id),)).fetchall()
        return [_item_carrinho(r) for r in rows]

    def carrinho_baixar_receita(self, receita_id: int) -> int:
        """O prato desta receita foi aceito: as linhas viram compras comprometidas e saem do carrinho."""
        with _LOCK, self.connect() as conn:
            return conn.execute("DELETE FROM carrinho WHERE receita_id = ?", (int(receita_id),)).rowcount

    # ------------------------------------------------------------------ pratos e ingredientes salvos
    def salvos(self) -> dict[str, list[dict[str, Any]]]:
        """O que ela guardou para usar de novo: pratos (com ingredientes e valores) e ingredientes avulsos."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM salvos ORDER BY nome_norm").fetchall()
        itens = [_salvo(r) for r in rows]
        return {"pratos": [s for s in itens if s["tipo"] == "prato"], "ingredientes": [s for s in itens if s["tipo"] == "ingrediente"]}

    def salvo_obter(self, salvo_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM salvos WHERE id = ?", (int(salvo_id),)).fetchone()
        if row is None:
            raise KeyError(f"o item salvo {salvo_id} não existe")
        return _salvo(row)

    def salvo_por_nome(self, tipo: str, nome: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM salvos WHERE tipo = ? AND nome_norm = ?", (tipo, norm_text(nome or ""))).fetchone()
        return _salvo(row) if row else None

    def salvo_salvar(self, tipo: str, nome: str, *, preco_total: Any = None, quantidade: Any = None, unidade: Any = None,
                     ingredientes: Any = None, origem: str = "tela") -> dict[str, Any]:
        """Guarda um prato (com os ingredientes e o valor de cada um) ou um ingrediente avulso. Mesmo nome atualiza."""
        tipo = str(tipo or "").strip().lower()
        if tipo not in ("prato", "ingrediente"):
            raise ValueError("o tipo precisa ser 'prato' ou 'ingrediente'")
        nome = _nome_limpo(nome, tipo)
        if tipo == "prato":
            linhas = _linhas_do_prato(ingredientes)
            dados: tuple[Any, ...] = (None, None, round(sum(l["preco_total"] for l in linhas), 2), json.dumps(linhas, ensure_ascii=False))
        else:
            preco = round(_numero(preco_total, "preço"), 2)
            if preco <= 0:
                raise ValueError("informe o preço do ingrediente (maior que zero)")
            qtd = 1.0 if quantidade in (None, "") else _numero(quantidade, "quantidade")
            if qtd <= 0:
                raise ValueError("a quantidade precisa ser maior que zero")
            dados = (qtd, _unidade_limpa(unidade), preco, None)
        agora = _now()
        with _LOCK, self.connect() as conn:
            conn.execute(
                """INSERT INTO salvos(tipo, nome, nome_norm, quantidade, unidade, preco_total, ingredientes, origem, criado_em, atualizado_em)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(tipo, nome_norm) DO UPDATE SET nome = excluded.nome, quantidade = excluded.quantidade, unidade = excluded.unidade,
                     preco_total = excluded.preco_total, ingredientes = excluded.ingredientes, origem = excluded.origem, atualizado_em = excluded.atualizado_em""",
                (tipo, nome, norm_text(nome), *dados, origem, agora, agora))
            row = conn.execute("SELECT * FROM salvos WHERE tipo = ? AND nome_norm = ?", (tipo, norm_text(nome))).fetchone()
        return _salvo(row)

    def salvo_remover(self, salvo_id: int) -> dict[str, Any]:
        item = self.salvo_obter(salvo_id)
        with _LOCK, self.connect() as conn:
            conn.execute("DELETE FROM salvos WHERE id = ?", (int(salvo_id),))
        return item

    def salvar_do_carrinho(self, *, prato: Optional[str] = None, nome: Optional[str] = None, origem: str = "tela") -> dict[str, Any]:
        """Guarda o que já está no carrinho: o prato inteiro (com os ingredientes) ou um ingrediente avulso."""
        if prato:
            grupo = next((g for g in self.carrinho_pratos() if norm_text(g["prato"]) == norm_text(prato)), None)
            if grupo is None:
                raise KeyError(f"o prato {prato!r} não está no carrinho")
            ingredientes = [{"nome": i["nome"], "quantidade": i["quantidade"], "unidade": i["unidade"], "preco_total": i["preco_total"],
                             "ja_tem": i["ja_tem"]} for i in grupo["ingredientes"]]
            return self.salvo_salvar("prato", grupo["prato"], ingredientes=ingredientes, origem=origem)
        linha = next((i for i in self.carrinho_itens() if not i["prato"] and norm_text(i["nome"]) == norm_text(nome or "")), None)
        if linha is None:
            raise KeyError(f"o ingrediente {nome!r} não está avulso no carrinho")
        return self.salvo_salvar("ingrediente", linha["nome"], preco_total=linha["preco_total"], quantidade=linha["quantidade"],
                                 unidade=linha["unidade"], origem=origem)

    def carrinho_do_salvo(self, salvo_id: int) -> dict[str, Any]:
        """Põe no carrinho um prato ou ingrediente salvo (respeita o saldo)."""
        s = self.salvo_obter(salvo_id)
        if s["tipo"] == "ingrediente":
            return {"tipo": "ingrediente", "item": self.carrinho_adicionar(s["nome"], s["preco_total"], s["quantidade"], s["unidade"], origem="salvo")}
        return {"tipo": "prato", "prato": self.carrinho_adicionar_prato(s["nome"], s["ingredientes"], origem="salvo")}

    # ------------------------------------------------------------------ perfil do negócio
    def negocio(self) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            bruto = self._config_get(conn, "negocio")
        return json.loads(bruto) if bruto else None

    def negocio_salvar(self, dados: Any) -> dict[str, Any]:
        limpo = {**negocio_mod.normalizar(dados), "atualizado_em": _now()}
        with _LOCK, self.connect() as conn:
            self._config_set(conn, "negocio", json.dumps(limpo, ensure_ascii=False))
        return limpo
