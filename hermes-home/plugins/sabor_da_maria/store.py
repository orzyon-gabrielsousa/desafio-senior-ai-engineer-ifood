"""Persistência em SQLite dos dados do Sabor da Maria.

Banco embutido (stdlib ``sqlite3``), sem serviço externo: cabe no mesmo volume
do Hermes e sobrevive entre sessões. Tabelas:

* ``ingredientes``  — despensa normalizada (estoque, custo unitário em R$/kg, R$/L ou R$/un)
* ``perfil``        — equipamentos, técnicas e restrições operacionais elicitadas
* ``receitas``      — candidatas conferidas (ingredientes, origem, pendências)
* ``pratos``        — CMV calculado e, quando aceito, preço escolhido
* ``compras``       — compras complementares planejadas/confirmadas (orçamento)
* ``config``        — orçamento inicial e origem da planilha
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

from .matching import best_matches
from .units import format_base, norm_text, parse_pantry_unit
from .xlsx_reader import read_table

_LOCK = threading.RLock()

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


def _ingredient_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["estoque_legivel"] = format_base(d["estoque_base"], d["unidade_base"])
    if d.get("custo_unitario_base") is not None:
        d["custo_unitario_legivel"] = f"R$ {d['custo_unitario_base']:.2f}/{d['unidade_base']}".replace(".", ",")
    else:
        d["custo_unitario_legivel"] = "sem preço registrado"
    return d


class Store:
    def __init__(self, db_path: Optional[str | Path] = None, xlsx_path: Optional[str | Path] = None,
                 orcamento: Optional[float] = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.xlsx_path = Path(xlsx_path) if xlsx_path else default_xlsx_path()
        self._orcamento = orcamento
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

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
        return [_ingredient_dict(r) for r in rows]

    def obter(self, nome: str) -> Optional[dict[str, Any]]:
        self.ensure_loaded()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM ingredientes WHERE nome = ? OR nome_norm = ?", (nome, norm_text(nome))).fetchone()
        return _ingredient_dict(row) if row else None

    def buscar(self, nome: str, limit: int = 3) -> list[dict[str, Any]]:
        itens = {i["nome"]: i for i in self.ingredientes()}
        exato = self.obter(nome)
        if exato:
            return [{"nome": exato["nome"], "score": 1.0, "confiavel": True, **{k: exato[k] for k in ("estoque_legivel", "estoque_base", "unidade_base", "custo_unitario_base", "custo_unitario_legivel", "observacao")}}]
        out = []
        for m in best_matches(nome, list(itens), limit=limit):
            item = itens[m["nome"]]
            out.append({**m, **{k: item[k] for k in ("estoque_legivel", "estoque_base", "unidade_base", "custo_unitario_base", "custo_unitario_legivel", "observacao")}})
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

    def aceitar_prato(self, prato_id: int, preco_venda: float, compras: list[dict[str, Any]], confirmar_compras: bool = True) -> dict[str, Any]:
        prato = self.obter_prato(prato_id)
        if not prato:
            raise KeyError(f"Prato {prato_id} não encontrado")
        with _LOCK, self.connect() as conn:
            conn.execute("UPDATE pratos SET preco_venda = ?, status = 'aceito', atualizado_em = ? WHERE id = ?", (float(preco_venda), _now(), prato_id))
            conn.execute("DELETE FROM compras WHERE prato_id = ?", (prato_id,))
            for c in compras:
                conn.execute("INSERT INTO compras(prato_id, ingrediente, quantidade, unidade, preco_estimado, status, criado_em) VALUES(?,?,?,?,?,?,?)",
                             (prato_id, str(c.get("ingrediente")), c.get("quantidade"), c.get("unidade"), float(c.get("preco_estimado") or 0),
                              "confirmada" if confirmar_compras else "planejada", _now()))
        return self.obter_prato(prato_id) or {}

    # ------------------------------------------------------------------ orçamento
    def orcamento(self) -> dict[str, Any]:
        self.ensure_loaded()
        with self.connect() as conn:
            inicial = float(self._config_get(conn, "orcamento_inicial") or default_budget())
            rows = conn.execute("SELECT c.*, p.nome AS prato FROM compras c LEFT JOIN pratos p ON p.id = c.prato_id ORDER BY c.id").fetchall()
        confirmadas = [dict(r) for r in rows if r["status"] == "confirmada"]
        planejadas = [dict(r) for r in rows if r["status"] == "planejada"]
        comprometido = round(sum(r["preco_estimado"] for r in confirmadas), 2)
        return {
            "inicial": inicial,
            "comprometido": comprometido,
            "restante": round(inicial - comprometido, 2),
            "compras_confirmadas": confirmadas,
            "compras_planejadas": planejadas,
        }
