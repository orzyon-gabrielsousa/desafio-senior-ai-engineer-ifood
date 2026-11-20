"""Persistência em SQLite dos dados do Sabor da Maria.

Banco embutido (stdlib ``sqlite3``), sem serviço externo: cabe no mesmo volume
do Hermes e sobrevive entre sessões. Tabelas:

* ``ingredientes``  — despensa normalizada (estoque, custo unitário em R$/kg, R$/L ou R$/un)
* ``perfil``        — equipamentos, utensílios, técnicas e restrições operacionais da cozinha: o que a consultora
                      elicita na conversa e o que ela marca na tela Minha cozinha (o mesmo registro, ver cozinha.py)
* ``receitas``      — candidatas conferidas (ingredientes, origem, pendências)
* ``pratos``        — escolhas em preparação, CMV calculado e, quando publicado, preço escolhido
* ``analises_preco`` — última análise da consultora por prato escolhido pela Dona Maria
* ``vendas``        — vendas realizadas informadas, com preço/CMV históricos e estorno reversível
* ``compras``       — compras complementares planejadas/confirmadas/canceladas (orçamento)
* ``historico_pratos`` e ``historico_prato_insumos`` — lote aceito, quantidades e custos apropriados
* ``reservas``      — estoque da despensa reservado por prato aceito (disponível = estoque − reservas)
* ``carrinho``      — itens que ela separou para comprar (a consultora mexe a pedido dela); prato = ingredientes com valor;
                      o que já está na despensa aparece com valor mas não gasta orçamento
* ``salvos``        — pratos (com ingredientes e valores) e ingredientes avulsos que ela guardou para usar de novo
* ``config``        — orçamento inicial, origem da planilha e perfil do negócio (onde a loja fica, tipo de negócio e tipo de alimentação)
* ``atividade``     — o que cada ferramenta da consultora fez (pesquisa na internet, leitura de receita, despensa, preços...), em
                      uma frase em português por chamada: a tela mostra o passo a passo com isso (ver atividade.py)

Reservas e compromissos de um prato são liberados por ``cardapio_remover``. Compras realizadas
entram na despensa e no orçamento, preservando o vínculo histórico com o lote quando conhecido. Um ``Store(ignorar_prato_id=X)`` desconta do cálculo as reservas e compras do próprio
prato X, para que repetir o registro de X (retry, troca de preço) não conte duas vezes.

O carrinho é rascunho de compra: ``saldo = restante − carrinho``. Itens ligados a uma receita saem do
carrinho quando o preço é registrado (viram compras comprometidas), para o mesmo gasto não contar duas vezes.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

from . import cozinha as cozinha_mod
from . import negocio as negocio_mod
from .matching import best_matches
from .units import format_base, norm_text, parse_pantry_unit
from .xlsx_reader import read_table

_LOCK = threading.RLock()

# Um CMV calculado durante a conferência é uma prévia. A análise salva
# com o lote identifica o aceite que separa a disponibilidade no estoque.
_LOTE_ATIVO = """p.receita_id IS h.receita_id AND (p.status = 'aceito' OR (
    p.status = 'cmv_calculado' AND EXISTS (SELECT 1 FROM analises_preco a WHERE a.prato_id = p.id)))"""
_CAMPOS_INSUMO = ("ingrediente_id", "ingrediente", "quantidade", "quantidade_base", "unidade_base", "custo_uso", "origem", "derivado")


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
CREATE TABLE IF NOT EXISTS analises_preco (
    nome_norm TEXT PRIMARY KEY,
    prato_id INTEGER NOT NULL REFERENCES pratos(id),
    dados TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vendas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operacao TEXT NOT NULL UNIQUE,
    prato_id INTEGER NOT NULL REFERENCES pratos(id),
    prato TEXT NOT NULL,
    quantidade INTEGER NOT NULL CHECK (quantidade > 0),
    preco_unitario REAL NOT NULL CHECK (preco_unitario > 0),
    cmv_unitario REAL NOT NULL CHECK (cmv_unitario >= 0),
    vendido_em TEXT NOT NULL,
    registrado_em TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmada' CHECK (status IN ('confirmada', 'estornada')),
    estornado_em TEXT
);
CREATE INDEX IF NOT EXISTS idx_vendas_periodo ON vendas(vendido_em);
CREATE TABLE IF NOT EXISTS compras (
    id INTEGER PRIMARY KEY,
    prato_id INTEGER,
    historico_prato_id INTEGER,
    receita_origem_id INTEGER,
    ingrediente TEXT NOT NULL,
    quantidade REAL,
    unidade TEXT,
    preco_estimado REAL NOT NULL,
    status TEXT NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS historico_pratos (
    id INTEGER PRIMARY KEY,
    prato_id INTEGER NOT NULL,
    receita_id INTEGER,
    registrado_em TEXT,
    dados TEXT NOT NULL,
    despesa_despensa REAL NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_historico_pratos_receita
    ON historico_pratos(prato_id, COALESCE(receita_id, -1));
CREATE TABLE IF NOT EXISTS historico_prato_insumos (
    lote_id INTEGER NOT NULL REFERENCES historico_pratos(id),
    ordem INTEGER NOT NULL,
    ingrediente_id INTEGER REFERENCES ingredientes(id),
    ingrediente TEXT NOT NULL,
    quantidade TEXT,
    quantidade_base REAL,
    unidade_base TEXT,
    custo_uso REAL,
    origem TEXT NOT NULL,
    derivado TEXT,
    PRIMARY KEY(lote_id, ordem)
);
CREATE TABLE IF NOT EXISTS lotes_compras (
    chave TEXT PRIMARY KEY,
    itens TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS atividade (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chamada TEXT NOT NULL UNIQUE,
    sessao TEXT,
    ferramenta TEXT NOT NULL,
    entrada TEXT NOT NULL DEFAULT '',
    titulo TEXT NOT NULL,
    resumo TEXT NOT NULL DEFAULT '',
    itens TEXT,
    ok INTEGER NOT NULL DEFAULT 1,
    duracao_ms INTEGER,
    criado_em TEXT NOT NULL
);
"""

MAX_ATIVIDADE = 400  # linhas guardadas do passo a passo da consultora; as mais antigas saem


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


def _fonte_de_receita(valor: Any) -> Optional[str]:
    if not isinstance(valor, str):
        return None
    try:
        url = urlsplit(valor)
    except ValueError:
        return None
    return valor.strip() if url.scheme in ("http", "https") and url.hostname else None


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


def _compra_real(compra: Any) -> dict[str, Any]:
    if not isinstance(compra, dict):
        raise ValueError("cada compra precisa ter nome, quantidade, unidade e preço total")
    nome = _nome_limpo(compra.get("nome"), "ingrediente")
    quantidade = _numero(compra.get("quantidade"), f"quantidade de {nome}")
    spec = parse_pantry_unit(str(compra.get("unidade") or ""))
    quantidade_base = quantidade * spec.factor
    if not math.isfinite(quantidade_base) or quantidade_base <= 0:
        raise ValueError(f"a quantidade de {nome} precisa ser maior que zero e finita")
    preco = _numero(compra.get("preco_total"), f"preço total de {nome}")
    if preco < 0:
        raise ValueError(f"o preço total de {nome} não pode ser negativo")
    conteudo = compra.get("conteudo_por_unidade")
    conteudo_base = None
    if conteudo is not None:
        if not isinstance(conteudo, dict):
            raise ValueError("informe o conteúdo de 1 unidade com quantidade e unidade")
        medida = parse_pantry_unit(str(conteudo.get("unidade") or ""))
        qtd = _numero(conteudo.get("quantidade"), f"conteúdo de 1 unidade de {nome}") * medida.factor
        if medida.base == "un" or not math.isfinite(qtd) or qtd <= 0:
            raise ValueError("informe uma quantidade positiva em g, kg, ml ou L para o conteúdo de 1 unidade")
        conteudo_base = (qtd, medida.base)
    compra_id, carrinho_id = compra.get("compra_id"), compra.get("carrinho_id")
    for chave, id_ in (("compra_id", compra_id), ("carrinho_id", carrinho_id)):
        if id_ is not None and (type(id_) is not int or id_ <= 0):
            raise ValueError(f"informe um {chave} válido")
    if compra_id is not None and carrinho_id is not None:
        raise ValueError("informe compra_id ou carrinho_id, conforme a origem da compra")
    return {"nome": nome, "nome_norm": norm_text(nome), "quantidade": quantidade, "unidade": spec.original,
            "quantidade_base": quantidade_base, "unidade_base": spec.base, "preco_total": round(preco, 2),
            "conteudo_por_unidade": conteudo_base, "compra_id": compra_id, "carrinho_id": carrinho_id}


def _mesma_quantidade(origem: sqlite3.Row, compra: dict[str, Any]) -> bool:
    try:
        spec = parse_pantry_unit(str(origem["unidade"] or ""))
        quantidade = float(origem["quantidade"]) * spec.factor
    except (TypeError, ValueError):
        return False
    return spec.base == compra["unidade_base"] and math.isclose(quantidade, compra["quantidade_base"], abs_tol=1e-9)


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
        # Reservas, compras e carrinho deste prato não contam na reavaliação dele.
        self.ignorar_prato_id = int(ignorar_prato_id) if ignorar_prato_id is not None else None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrar(conn)

    @staticmethod
    def _migrar(conn: sqlite3.Connection) -> None:
        """Completa as colunas do carrinho e preserva as fichas antigas no histórico."""
        carrinho = {r[1] for r in conn.execute("PRAGMA table_info(carrinho)")}
        compras = {r[1] for r in conn.execute("PRAGMA table_info(compras)")}
        historico = {r[1] for r in conn.execute("PRAGMA table_info(historico_pratos)")}
        migrou_lotes = conn.execute("SELECT 1 FROM config WHERE chave = 'historico_lotes_v2'").fetchone()
        if {"prato", "ja_tem"} <= carrinho and {"historico_prato_id", "receita_origem_id"} <= compras and migrou_lotes:
            return
        conn.execute("BEGIN IMMEDIATE")
        carrinho = {r[1] for r in conn.execute("PRAGMA table_info(carrinho)")}
        compras = {r[1] for r in conn.execute("PRAGMA table_info(compras)")}
        if "prato" not in carrinho:
            conn.execute("ALTER TABLE carrinho ADD COLUMN prato TEXT")
        if "ja_tem" not in carrinho:
            conn.execute("ALTER TABLE carrinho ADD COLUMN ja_tem INTEGER NOT NULL DEFAULT 0")
        if "receita_origem_id" not in compras:
            conn.execute("ALTER TABLE compras ADD COLUMN receita_origem_id INTEGER")
        if "historico_prato_id" not in compras:
            conn.execute("ALTER TABLE compras ADD COLUMN historico_prato_id INTEGER")
        if "despesa_despensa" not in historico:
            historico = {r[1] for r in conn.execute("PRAGMA table_info(historico_pratos)")}
            if "despesa_despensa" not in historico:
                conn.execute("ALTER TABLE historico_pratos ADD COLUMN despesa_despensa REAL NOT NULL DEFAULT 0")
        if not conn.execute("SELECT 1 FROM config WHERE chave = 'historico_lotes_v2'").fetchone() or "historico_prato_id" not in compras:
            Store._migrar_historico(conn)
            conn.execute("INSERT OR IGNORE INTO config(chave, valor) VALUES ('historico_lotes_v2', '1')")

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

    def perfil_precificacao(self) -> dict[str, Any]:
        from .taxas import normalizar

        with self.connect() as conn:
            bruto = self._config_get(conn, "perfil_precificacao")
        return normalizar(json.loads(bruto) if bruto else {})

    def salvar_perfil_precificacao(self, dados: dict[str, Any]) -> dict[str, Any]:
        from .taxas import normalizar

        perfil = normalizar(dados)
        with _LOCK, self.connect() as conn:
            self._config_set(conn, "perfil_precificacao", json.dumps(perfil, ensure_ascii=False))
        return perfil

    def _reservas_map(self, conn: sqlite3.Connection, *, considerar_todos: bool = False) -> dict[int, float]:
        """ingrediente_id -> quantidade reservada por pratos aceitos (menos o prato ignorado)."""
        ignorado = None if considerar_todos else self.ignorar_prato_id
        rows = conn.execute(
            """SELECT r.ingrediente_id AS iid, SUM(r.quantidade_base) AS q FROM reservas r
               JOIN pratos p ON p.id = r.prato_id
               WHERE p.status IN ('cmv_calculado', 'aceito') AND (? IS NULL OR r.prato_id IS NOT ?)
               GROUP BY r.ingrediente_id""",
            (ignorado, ignorado),
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

    def registrar_compra(self, nome: str, quantidade: float, unidade: str, preco_total: float, *,
                         novo: bool = False, conteudo_por_unidade: Optional[dict[str, Any]] = None,
                         compra_id: Optional[int] = None, carrinho_id: Optional[int] = None) -> dict[str, Any]:
        """Entrada de estoque com preço: custo unitário vira média ponderada do que foi pago."""
        lote = self.registrar_compras([{"nome": nome, "quantidade": quantidade, "unidade": unidade, "preco_total": preco_total,
                                      "conteudo_por_unidade": conteudo_por_unidade, "compra_id": compra_id, "carrinho_id": carrinho_id}],
                                     cadastrar=novo)
        return lote["ingredientes"][0]

    def registrar_compras(self, compras: list[dict[str, Any]], *, chave: Optional[str] = None,
                          cadastrar: bool = True, receita_id: Optional[int] = None) -> dict[str, Any]:
        """Lança as compras no estoque e no orçamento na mesma transação. Uma chave repetida não relança o lote."""
        self.ensure_loaded()
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._registrar_compras(conn, compras, chave=chave, cadastrar=cadastrar, receita_id=receita_id)

    def _registrar_compras(self, conn: sqlite3.Connection, compras: list[dict[str, Any]], *,
                           chave: Optional[str] = None, cadastrar: bool = True,
                           receita_id: Optional[int] = None) -> dict[str, Any]:
        if not isinstance(compras, list) or not 1 <= len(compras) <= MAX_INGREDIENTES:
            raise ValueError(f"informe de 1 a {MAX_INGREDIENTES} compras")
        linhas = [_compra_real(c) for c in compras]
        if len({linha["nome_norm"] for linha in linhas}) != len(linhas):
            raise ValueError("informe uma linha por ingrediente comprado")
        campos = ("nome_norm", "quantidade_base", "unidade_base", "preco_total", "conteudo_por_unidade", "compra_id", "carrinho_id")
        assinatura = json.dumps({"receita_id": receita_id,
                                 "itens": [[linha[k] for k in campos] for linha in sorted(linhas, key=lambda l: l["nome_norm"])]},
                                ensure_ascii=False)
        total = round(sum(linha["preco_total"] for linha in linhas), 2)
        registrado = conn.execute("SELECT itens FROM lotes_compras WHERE chave = ?", (chave,)).fetchone() if chave else None
        if registrado is not None and registrado["itens"] != assinatura:
            raise ValueError("essa confirmação já registrou outras compras. Confira a despensa antes de tentar novamente")
        if registrado is None:
            saldo = self._ler_orcamento(conn)["saldo"]
            baixadas = [self._baixar_compromisso(conn, linha, receita_id) for linha in linhas]
            liberado = sum(valor for valor, _, _ in baixadas)
            disponivel = round(saldo + liberado, 2)
            if total > disponivel + 1e-9:
                raise OrcamentoEstourado(total - disponivel, disponivel)
            for linha, (_, compra_atualizada, origem) in zip(linhas, baixadas):
                self._entrada_no_estoque(conn, linha, cadastrar)
                if not compra_atualizada:
                    historico = conn.execute("SELECT id FROM historico_pratos WHERE receita_id = ? ORDER BY id DESC LIMIT 1",
                                            (origem,)).fetchone() if origem is not None else None
                    conn.execute(
                        """INSERT INTO compras(historico_prato_id, receita_origem_id, ingrediente, quantidade, unidade,
                           preco_estimado, status, criado_em) VALUES(?,?,?,?,?,?,'confirmada',?)""",
                        (historico["id"] if historico else None, origem, linha["nome"], linha["quantidade"],
                         linha["unidade"], linha["preco_total"], _now()),
                    )
            if chave:
                conn.execute("INSERT INTO lotes_compras(chave, itens) VALUES(?, ?)", (chave, assinatura))
        reservas = self._reservas_map(conn)
        ingredientes = []
        for linha in linhas:
            item = self._ingrediente_exato(conn, linha["nome_norm"])
            if item is not None:
                ingredientes.append(_ingredient_dict(item, reservas.get(item["id"], 0.0)))
        return {"ingredientes": ingredientes, "orcamento": self._ler_orcamento(conn), "total": total,
                "ja_registrado": registrado is not None}

    @staticmethod
    def _ingrediente_exato(conn: sqlite3.Connection, nome_norm: str) -> Optional[sqlite3.Row]:
        return conn.execute("SELECT * FROM ingredientes WHERE nome_norm = ? ORDER BY id LIMIT 1", (nome_norm,)).fetchone()

    def _entrada_no_estoque(self, conn: sqlite3.Connection, linha: dict[str, Any], cadastrar: bool) -> None:
        item = self._ingrediente_exato(conn, linha["nome_norm"])
        nome, qtd_base, preco = linha["nome"], linha["quantidade_base"], linha["preco_total"]
        if item is None:
            if not cadastrar:
                raise KeyError(f"Ingrediente {nome!r} não está na despensa; passe novo=true para cadastrar")
            cur = conn.execute(
                """INSERT INTO ingredientes(nome, nome_norm, unidade_original, unidade_base, estoque_base, comprado_base, preco_total,
                   custo_unitario_base, origem, observacao, atualizado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (nome, linha["nome_norm"], linha["unidade"], linha["unidade_base"], qtd_base, qtd_base, preco,
                 preco / qtd_base, "compra", "cadastrado após compra complementar", _now()),
            )
            item_id = cur.lastrowid
        else:
            if item["unidade_base"] != linha["unidade_base"]:
                raise ValueError(f"{nome!r} é controlado em {item['unidade_base']}; informe a compra nessa base")
            item_id = item["id"]
            comprado = (item["comprado_base"] or 0) + qtd_base
            pago = (item["preco_total"] or 0) + preco
            conn.execute(
                """UPDATE ingredientes SET estoque_base = estoque_base + ?, comprado_base = ?, preco_total = ?,
                   custo_unitario_base = ?, atualizado_em = ? WHERE id = ?""",
                (qtd_base, comprado, pago, pago / comprado, _now(), item_id),
            )
        conteudo = linha["conteudo_por_unidade"]
        if conteudo is not None:
            quantidade, unidade = conteudo
            conn.execute(
                """UPDATE ingredientes SET conteudo_un_base = ?, conteudo_un_unidade = ?, observacao = ?, atualizado_em = ?
                   WHERE id = ?""",
                (quantidade, unidade, f"1 un = {format_base(quantidade, unidade)} (informado pela Dona Maria)", _now(), item_id),
            )

    @staticmethod
    def _baixar_compromisso(conn: sqlite3.Connection, linha: dict[str, Any],
                           receita_id: Optional[int]) -> tuple[float, bool, Optional[int]]:
        """Devolve o valor liberado, se a compra já existia e a receita de origem da linha do carrinho."""
        ativas = conn.execute(
            """SELECT c.*, p.receita_id FROM compras c JOIN pratos p ON c.prato_id = p.id
               WHERE p.status = 'aceito' AND c.status IN ('planejada', 'confirmada')"""
        ).fetchall()
        candidatas = [c for c in ativas if norm_text(c["ingrediente"]) == linha["nome_norm"]]
        carrinho = conn.execute("SELECT * FROM carrinho WHERE ja_tem = 0").fetchall()
        itens = [c for c in carrinho if norm_text(c["nome"]) == linha["nome_norm"]]
        compra_id, carrinho_id = linha["compra_id"], linha["carrinho_id"]
        if receita_id is not None and compra_id is None and carrinho_id is None:
            da_receita = [c for c in candidatas if c["receita_id"] == receita_id and _mesma_quantidade(c, linha)]
            do_carrinho = [c for c in itens if c["receita_id"] == receita_id and _mesma_quantidade(c, linha)]
            if len(da_receita) + len(do_carrinho) == 1:
                compra_id = da_receita[0]["id"] if da_receita else None
                carrinho_id = do_carrinho[0]["id"] if do_carrinho else None
        if compra_id is not None:
            compra = next((c for c in candidatas if c["id"] == compra_id), None)
            if compra is None or not _mesma_quantidade(compra, linha):
                raise ValueError(f"compra_id {compra_id} não corresponde ao ingrediente, à quantidade ou à unidade da compra")
            conn.execute(
                """UPDATE compras SET prato_id = NULL, ingrediente = ?, quantidade = ?, unidade = ?, preco_estimado = ?,
                   status = 'confirmada', criado_em = ? WHERE id = ?""",
                (linha["nome"], linha["quantidade"], linha["unidade"], linha["preco_total"], _now(), compra_id),
            )
            return (float(compra["preco_estimado"]) if compra["status"] == "confirmada" else 0.0), True, None
        if carrinho_id is not None:
            item = next((c for c in itens if c["id"] == carrinho_id), None)
            if item is None or not _mesma_quantidade(item, linha):
                raise ValueError(f"carrinho_id {carrinho_id} não corresponde ao ingrediente, à quantidade ou à unidade da compra")
            conn.execute("DELETE FROM carrinho WHERE id = ?", (carrinho_id,))
            return float(item["preco_total"]), False, item["receita_id"]
        if candidatas or itens:
            ids = [f"compra_id={c['id']}" for c in candidatas] + [f"carrinho_id={c['id']}" for c in itens]
            raise ValueError(f"{linha['nome']} já tem uma linha pendente ({', '.join(ids)}). Identifique qual foi comprada")
        return 0.0, False, None

    # ------------------------------------------------------------------ perfil
    def perfil(self) -> dict[str, Any]:
        with self.connect() as conn:
            return self._ler_perfil(conn)

    @staticmethod
    def _ler_perfil(conn: sqlite3.Connection) -> dict[str, Any]:
        rows = conn.execute("SELECT chave, valor FROM perfil").fetchall()
        out = {"equipamentos": {}, "tecnicas": {}, "restricoes": {}, "observacoes": []}
        for r in rows:
            out[r["chave"]] = json.loads(r["valor"])
        return out

    def perfil_salvar(self, partes: dict[str, Any]) -> dict[str, Any]:
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            atual = self._ler_perfil(conn)
            for chave in ("equipamentos", "tecnicas", "restricoes"):
                novo = partes.get(chave)
                if isinstance(novo, dict):
                    atual[chave] = {**atual.get(chave, {}), **{norm_text(k).replace(" ", "_"): v for k, v in novo.items()}}
            obs = partes.get("observacoes")
            if obs:
                lista = atual.get("observacoes") or []
                for item in (obs if isinstance(obs, list) else [obs]):
                    texto = str(item).strip()
                    if texto and texto not in lista:
                        lista.append(texto)
                atual["observacoes"] = lista
            self._gravar_perfil(conn, atual)
            return atual

    @staticmethod
    def _gravar_perfil(conn: sqlite3.Connection, atual: dict[str, Any]) -> None:
        for chave in ("equipamentos", "tecnicas", "restricoes", "observacoes"):
            conn.execute("INSERT INTO perfil(chave, valor, atualizado_em) VALUES(?,?,?) ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, atualizado_em = excluded.atualizado_em",
                         (chave, json.dumps(atual.get(chave, {} if chave != "observacoes" else []), ensure_ascii=False), _now()))

    # ------------------------------------------------------------------ minha cozinha
    def cozinha(self, perfil: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """A cozinha dela como a tela mostra: o catálogo (equipamentos, utensílios, habilidades, rotina) com o que está salvo,
        o quanto já respondeu e o que a conversa gravou fora do catálogo. É o MESMO perfil que a consultora lê e grava."""
        perfil = self.perfil() if perfil is None else perfil
        return {**cozinha_mod.da_perfil(perfil), "resumo": cozinha_mod.resumo(perfil), "outros": cozinha_mod.outros(perfil)}

    def cozinha_salvar(self, dados: Any) -> dict[str, Any]:
        """Grava o que ela marcou em Minha cozinha (validado contra o catálogo). Só muda o que vier; o resto do perfil fica."""
        limpo = cozinha_mod.normalizar(dados)
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            perfil = cozinha_mod.aplicar(self._ler_perfil(conn), limpo)
            self._gravar_perfil(conn, perfil)
        return self.cozinha(perfil)

    # ------------------------------------------------------------------ receitas e pratos
    def selecionar_pratos(self, pratos: list[dict[str, Any]], *, substituir_prato_id: Optional[int] = None) -> list[dict[str, Any]]:
        """Guarda escolhas para desenvolver depois, independentemente de receita, preço, estoque e carrinho."""
        if not isinstance(pratos, list) or not pratos:
            raise ValueError("informe os pratos escolhidos")
        if substituir_prato_id is not None and (type(substituir_prato_id) is not int or substituir_prato_id <= 0 or len(pratos) != 1):
            raise ValueError("informe o prato original e uma única alternativa para substituir")
        ids: list[int] = []
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            original = None
            if substituir_prato_id is not None:
                original = conn.execute("SELECT * FROM pratos WHERE id = ?", (substituir_prato_id,)).fetchone()
                if original is None or original["status"] not in ("selecionado", "cmv_calculado", "removido"):
                    raise ValueError("só é possível substituir um prato ainda em preparação")
            for escolha in pratos:
                if not isinstance(escolha, dict) or not isinstance(escolha.get("nome"), str):
                    raise ValueError("informe o nome de cada prato escolhido")
                nome = _nome_limpo(escolha["nome"], "prato")
                if original is not None and norm_text(nome) == norm_text(original["nome"]):
                    raise ValueError("escolha um prato diferente do original")
                fonte = _fonte_de_receita(escolha.get("fonte_url"))
                if escolha.get("fonte_url") is not None and fonte is None:
                    raise ValueError("informe um link HTTP ou HTTPS para a fonte da receita")
                receita_id = escolha.get("receita_id")
                receita = None
                if receita_id is not None:
                    receita = conn.execute("SELECT id, porcoes FROM receitas WHERE id = ?", (receita_id,)).fetchone()
                    if receita is None:
                        raise ValueError(f"receita {receita_id} não encontrada")
                existente = next((p for p in conn.execute("SELECT * FROM pratos WHERE status <> 'removido' ORDER BY id DESC")
                                  if norm_text(p["nome"]) == norm_text(nome) or (receita is not None and p["receita_id"] == receita["id"])), None)
                if existente is None:
                    selecao = {"fonte_url": fonte}
                    if fonte and escolha.get("usar_quando_viavel") is True:
                        selecao["usar_quando_viavel"] = True
                    dados = {"selecao": selecao}
                    agora = _now()
                    cur = conn.execute(
                        """INSERT INTO pratos(nome, receita_id, porcoes, status, dados, criado_em, atualizado_em)
                           VALUES(?, ?, ?, 'selecionado', ?, ?, ?)""",
                        (nome, receita["id"] if receita else None, receita["porcoes"] if receita else None,
                         json.dumps(dados, ensure_ascii=False), agora, agora),
                    )
                    ids.append(int(cur.lastrowid))
                    continue
                if existente["status"] == "selecionado":
                    dados = json.loads(existente["dados"])
                    selecao = dados.setdefault("selecao", {})
                    if fonte and not selecao.get("fonte_url"):
                        selecao["fonte_url"] = fonte
                    if fonte and fonte == selecao.get("fonte_url") and escolha.get("usar_quando_viavel") is True:
                        vinculada = conn.execute("SELECT fonte_url FROM receitas WHERE id = ?", (existente["receita_id"],)).fetchone()
                        if vinculada is None or vinculada["fonte_url"] == fonte:
                            selecao["usar_quando_viavel"] = True
                    novo_id = existente["receita_id"] if existente["receita_id"] is not None or receita is None else receita["id"]
                    d_json = json.dumps(dados, ensure_ascii=False)
                    if d_json != existente["dados"] or novo_id != existente["receita_id"]:
                        conn.execute("UPDATE pratos SET receita_id = ?, dados = ?, atualizado_em = ? WHERE id = ?",
                                     (novo_id, d_json, _now(), existente["id"]))
                if existente["id"] not in ids:
                    ids.append(existente["id"])
            if original is not None:
                self._substituir_rascunho(conn, original, ids[0])
            por_id = {row["id"]: row for row in self._pratos_com_receita(conn) if row["id"] in ids}
            return [self._resumo_prato(por_id[prato_id]) for prato_id in ids]

    def _substituir_rascunho(self, conn: sqlite3.Connection, original: sqlite3.Row, alternativa_id: int) -> None:
        if original["id"] == alternativa_id:
            raise ValueError("escolha um prato diferente do original")
        dados = json.loads(original["dados"])
        if original["status"] == "removido":
            if (dados.get("remocao") or {}).get("substituido_por") == alternativa_id:
                return
            raise ValueError("o prato original já saiu da preparação")
        agora = _now()
        dados["remocao"] = {"motivo": "substituído por uma alternativa", "em": agora,
                            "status_anterior": original["status"], "substituido_por": alternativa_id}
        self._liberar_itens_do_rascunho(conn, original["id"], original["receita_id"])
        conn.execute("UPDATE pratos SET status = 'removido', dados = ?, atualizado_em = ? WHERE id = ?",
                     (json.dumps(dados, ensure_ascii=False), agora, original["id"]))
        conn.execute("DELETE FROM analises_preco WHERE prato_id = ?", (original["id"],))

    @staticmethod
    def _pratos_com_receita(conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute(
            """SELECT p.*, r.dados AS receita_dados, r.fonte_url, r.porcoes AS receita_porcoes
               FROM pratos p LEFT JOIN receitas r ON r.id = p.receita_id
               WHERE p.status IN ('selecionado', 'cmv_calculado', 'aceito') ORDER BY p.id""",
        ).fetchall()

    @staticmethod
    def anotacoes_faltantes(resultado: dict[str, Any], ingredientes: list[Any],
                           anteriores: list[dict[str, Any]], adaptacao: Any = None) -> list[dict[str, Any]]:
        """Mantém a falta histórica separada dos ingredientes efetivamente usados."""
        from .montagem import nome_da_medida

        atuais = {norm_text(nome_da_medida(a["ingrediente"])): dict(a) for a in resultado.get("faltantes") or []}
        nomes = {norm_text(nome_da_medida(str(i.get("nome") if isinstance(i, dict) else i))) for i in ingredientes}
        anotacoes = {}
        for anterior in anteriores:
            chave = norm_text(nome_da_medida(anterior["ingrediente"]))
            anotacoes.setdefault(chave, dict(anterior))
        for chave, nota in anotacoes.items():
            if chave not in atuais:
                rotulo = nome_da_medida(nota["ingrediente"])
                if chave in nomes:
                    nota.update(estado="resolvido", observacao=f"{rotulo}: falta de estoque resolvida nesta conferência.")
                elif adaptacao:
                    nota.update(estado="omitido_na_adaptacao", observacao=f"{rotulo}: faltava na despensa e não será usado nesta adaptação local.")
        for chave, atual in atuais.items():
            nota = {**anotacoes.get(chave, {}), **atual, "estado": "faltando"}
            nota.setdefault("registrado_em", _now())
            quantidade = atual.get("falta") or atual["quantidade_receita"]
            nota["observacao"] = f"{atual['ingrediente']}: {atual['motivo']} ({quantidade})."
            if atual.get("sem_preco"):
                nota["observacao"] += " Custo do complemento ainda não conhecido."
            anotacoes[chave] = nota
        return list(anotacoes.values())

    @staticmethod
    def _resumo_prato(row: sqlite3.Row) -> dict[str, Any]:
        dados = json.loads(row["dados"])
        receita = json.loads(row["receita_dados"]) if row["receita_dados"] else {}
        resultado = dados.get("resultado") or receita.get("resultado") or {}
        fonte = _fonte_de_receita(row["fonte_url"]) or _fonte_de_receita((dados.get("selecao") or {}).get("fonte_url"))
        custos = resultado if not resultado.get("pendencias") else {}
        campos = ("id", "nome", "status", "receita_id", "cmv_total", "cmv_porcao", "preco_venda")
        return {**{campo: row[campo] for campo in campos}, "fonte_url": fonte, "porcoes": row["porcoes"] or row["receita_porcoes"],
                "veredito": (dados.get("avaliacao") or {}).get("veredito") or receita.get("veredito"),
                "bloqueios": receita.get("bloqueios") if row["status"] == "selecionado" and receita.get("bloqueios") else [],
                "ingredientes_faltantes": receita.get("ingredientes_faltantes") or dados.get("ingredientes_faltantes") or [],
                "compras_estimadas": custos.get("compras_total_estimado"), "custo_da_despensa": custos.get("custo_vindo_da_despensa")}

    def pratos_em_andamento(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [self._resumo_prato(row) for row in self._pratos_com_receita(conn)]

    def salvar_receita(self, nome: str, fonte_url: Optional[str], porcoes: Optional[float], dados: dict[str, Any], status: str,
                      *, prato_id: Optional[int] = None, criar_rascunho: bool = False,
                      receita_esperada: Optional[int] = None) -> int:
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if criar_rascunho and prato_id is None:
                prato = next((p for p in conn.execute("SELECT * FROM pratos WHERE status <> 'removido' ORDER BY id DESC")
                              if norm_text(p["nome"]) == norm_text(nome)), None)
                if prato is not None and prato["status"] != "selecionado":
                    raise ValueError("já existe outra ficha para este prato; use o rascunho existente")
                if prato is not None and prato["receita_id"] is not None:
                    anterior = conn.execute("SELECT fonte_url, porcoes, dados FROM receitas WHERE id = ?", (prato["receita_id"],)).fetchone()
                    dados_anteriores = json.loads(anterior["dados"]) if anterior else {}
                    if (anterior and dados.get("origem_montagem")
                            and dados_anteriores.get("origem_montagem") == dados["origem_montagem"]
                            and anterior["fonte_url"] == fonte_url and anterior["porcoes"] == porcoes
                            and dados_anteriores.get("ingredientes") == dados.get("ingredientes")):
                        return int(prato["receita_id"])
                    raise ValueError("já existe outra ficha para este prato; use o rascunho existente")
                if prato is None:
                    agora = _now()
                    criado = conn.execute("""INSERT INTO pratos(nome, status, dados, criado_em, atualizado_em)
                                            VALUES(?, 'selecionado', ?, ?, ?)""",
                                          (_nome_limpo(nome, "prato"), json.dumps({"selecao": {"fonte_url": fonte_url}}), agora, agora))
                    prato = conn.execute("SELECT * FROM pratos WHERE id = ?", (criado.lastrowid,)).fetchone()
            else:
                prato = self._prato_para_preparar(conn, nome, prato_id=prato_id)
            if receita_esperada is not None and (prato is None or prato["status"] != "selecionado" or prato["receita_id"] != receita_esperada):
                raise ValueError("a ficha mudou durante a cotação; consulte o rascunho atual")
            notas_anteriores = []
            if prato is not None:
                dados = {**dados, "prato_id": prato["id"]}
                if prato["receita_id"]:
                    previa = conn.execute("SELECT dados FROM receitas WHERE id = ?", (prato["receita_id"],)).fetchone()
                    notas_anteriores = (json.loads(previa["dados"]).get("ingredientes_faltantes") or []) if previa else []
            if "faltantes" in (dados.get("resultado") or {}):
                dados = {**dados, "ingredientes_faltantes": self.anotacoes_faltantes(
                    dados["resultado"], dados.get("ingredientes") or [],
                    dados.get("ingredientes_faltantes") or notas_anteriores, dados.get("adaptacao"))}
            cur = conn.execute("INSERT INTO receitas(nome, fonte_url, porcoes, dados, status, criado_em) VALUES(?,?,?,?,?,?)",
                               (nome, fonte_url, porcoes, json.dumps(dados, ensure_ascii=False), status, _now()))
            receita_id = int(cur.lastrowid)
            if prato is not None:
                self._liberar_itens_do_rascunho(conn, prato["id"], prato["receita_id"])
                selecao = json.loads(prato["dados"]).get("selecao") or {}
                if selecao.get("usar_quando_viavel") and fonte_url != selecao.get("fonte_url"):
                    selecao.pop("usar_quando_viavel")
                conn.execute("""UPDATE pratos SET receita_id = ?, porcoes = ?, cmv_total = NULL, cmv_porcao = NULL,
                              preco_venda = NULL, status = 'selecionado', dados = ?, atualizado_em = ? WHERE id = ?""",
                             (receita_id, porcoes, json.dumps({"selecao": selecao}, ensure_ascii=False),
                              _now(), prato["id"]))
                conn.execute("DELETE FROM analises_preco WHERE prato_id = ?", (prato["id"],))
            return receita_id

    @staticmethod
    def _prato_para_preparar(conn: sqlite3.Connection, nome: str, *, prato_id: Optional[int] = None,
                            receita_id: Optional[int] = None) -> Optional[sqlite3.Row]:
        if prato_id is not None:
            prato = conn.execute("SELECT * FROM pratos WHERE id = ? AND status IN ('selecionado', 'cmv_calculado')", (prato_id,)).fetchone()
            if prato is None:
                raise ValueError("escolha um prato em preparação")
            if receita_id is not None and prato["receita_id"] not in (None, receita_id):
                raise ValueError("use a conferência mais recente do prato")
            return prato
        if receita_id is not None:
            prato = conn.execute("SELECT * FROM pratos WHERE receita_id = ? AND status = 'selecionado' ORDER BY id DESC LIMIT 1", (receita_id,)).fetchone()
            if prato is not None:
                return prato
        return next((p for p in conn.execute("SELECT * FROM pratos WHERE status = 'selecionado' ORDER BY id DESC")
                     if norm_text(p["nome"]) == norm_text(nome) and (receita_id is None or p["receita_id"] in (None, receita_id))), None)

    def obter_receita(self, receita_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM receitas WHERE id = ?", (receita_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["dados"] = json.loads(d["dados"])
        from .fonte import corrigir_secoes_da_ficha

        d["dados"] = corrigir_secoes_da_ficha(d["dados"], d["porcoes"])
        return d

    def atualizar_medida_rascunho(self, prato_id: int, receita_id: int, indice: int,
                                  ingredientes_esperados: list[Any], quantidade: float, unidade: str, evidencia: str) -> bool:
        """Altera só uma medida pendente da versão respondida, sem aceitar ou reservar."""
        if (type(quantidade) not in (int, float) or not math.isfinite(quantidade) or quantidade <= 0
                or unidade not in {"g", "kg", "ml", "L"} or not evidencia.strip()):
            return False
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT r.dados FROM pratos p JOIN receitas r ON r.id = p.receita_id
                                  WHERE p.id = ? AND r.id = ? AND p.status = 'selecionado'""", (prato_id, receita_id)).fetchone()
            if row is None:
                return False
            dados = json.loads(row["dados"])
            ingredientes = dados.get("ingredientes") or []
            if ingredientes != ingredientes_esperados or type(indice) is not int or not 0 <= indice < len(ingredientes):
                return False
            item = ingredientes[indice]
            if not isinstance(item, dict):
                return False
            try:
                if float(str(item.get("quantidade", 0)).replace(",", ".")) > 0:
                    return False
            except (TypeError, ValueError):
                pass
            nome = str(item.get("nome") or "")
            from . import fonte
            from .montagem import nome_da_medida

            original = dados.get("fonte_verificada")
            if original:
                linhas = [l for l in original.get("ingredientes") or []
                          if norm_text(nome_da_medida(str(l))) == norm_text(nome_da_medida(nome))]
                if (len(linhas) != 1 or fonte._medida(str(linhas[0]))[0] is not None
                        or re.search(r"[0-9½¼¾⅓⅔]", str(linhas[0]))):
                    return False
            item.update(quantidade=quantidade, unidade=unidade)
            dados.setdefault("medidas_de_uso_confirmadas", {})[nome] = {
                "quantidade": quantidade, "unidade": unidade, "evidencia": evidencia,
                "origem": "medida_de_uso_local"}
            conn.execute("UPDATE receitas SET dados = ? WHERE id = ?", (json.dumps(dados, ensure_ascii=False), receita_id))
            return True

    def atualizar_faltantes_rascunho(self, prato_id: int, receita_id: int,
                                    ingredientes_esperados: list[Any], resultado: dict[str, Any]) -> bool:
        """Anota faltas na versão conferida, sem mudar a receita, o aceite ou compromissos."""
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT r.dados FROM pratos p JOIN receitas r ON r.id = p.receita_id
                                  WHERE p.id = ? AND r.id = ? AND p.status = 'selecionado'""", (prato_id, receita_id)).fetchone()
            if row is None:
                return False
            dados = json.loads(row["dados"])
            if dados.get("ingredientes") != ingredientes_esperados:
                return False
            notas = self.anotacoes_faltantes(resultado, ingredientes_esperados,
                                            dados.get("ingredientes_faltantes") or [], dados.get("adaptacao"))
            if notas == (dados.get("ingredientes_faltantes") or []):
                return False
            dados["ingredientes_faltantes"] = notas
            conn.execute("UPDATE receitas SET dados = ? WHERE id = ?", (json.dumps(dados, ensure_ascii=False), receita_id))
            return True

    def atualizar_pendencia_rascunho(self, prato_id: int, receita_id: int, *,
                                    tempo: Optional[float] = None, porcoes: Optional[float] = None,
                                    pergunta_esgotada: Optional[str] = None,
                                    confirmacoes_operacionais: Optional[list[dict[str, str]]] = None) -> bool:
        """Registra evidência de montagem apenas na versão que recebeu a resposta."""
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT r.dados, r.porcoes FROM pratos p JOIN receitas r ON r.id = p.receita_id
                                  WHERE p.id = ? AND r.id = ? AND p.status = 'selecionado'""", (prato_id, receita_id)).fetchone()
            if row is None:
                return False
            dados = json.loads(row["dados"])
            if porcoes is not None:
                if row["porcoes"] or not math.isfinite(porcoes) or porcoes <= 0:
                    return False
                conn.execute("UPDATE receitas SET porcoes = ? WHERE id = ?", (porcoes, receita_id))
                return True
            if tempo is not None:
                if dados.get("tempo_preparo_min") or (dados.get("fonte_verificada") or {}).get("tempo_preparo_min"):
                    return False
                if not math.isfinite(tempo) or tempo <= 0:
                    return False
                dados.update(tempo_preparo_min=tempo, tempo_preparo_origem="estimativa_declarada")
            elif pergunta_esgotada:
                esgotadas = dados.setdefault("perguntas_esgotadas", [])
                if pergunta_esgotada in esgotadas:
                    return False
                esgotadas.append(pergunta_esgotada)
            elif confirmacoes_operacionais:
                anteriores = dados.get("confirmacoes_operacionais") or []
                por_condicao = {c["chave"]: c for c in anteriores if isinstance(c, dict) and "chave" in c}
                por_condicao.update({c["chave"]: c for c in confirmacoes_operacionais})
                atualizadas = list(por_condicao.values())
                if atualizadas == anteriores:
                    return False
                dados["confirmacoes_operacionais"] = atualizadas
            else:
                return False
            conn.execute("UPDATE receitas SET dados = ? WHERE id = ?", (json.dumps(dados, ensure_ascii=False), receita_id))
            return True

    def receitas(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id, nome, fonte_url, porcoes, status, criado_em FROM receitas ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def salvar_prato(self, nome: str, receita_id: Optional[int], porcoes: Optional[float], cmv_total: float,
                     cmv_porcao: Optional[float], dados: dict[str, Any], *, prato_id: Optional[int] = None) -> int:
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if prato_id is None and receita_id is not None:
                receita = conn.execute("SELECT dados FROM receitas WHERE id = ?", (receita_id,)).fetchone()
                if receita is not None:
                    prato_id = json.loads(receita["dados"]).get("prato_id")
            prato = self._prato_para_preparar(conn, nome, prato_id=prato_id, receita_id=receita_id)
            if prato is not None:
                dados_atuais = json.loads(prato["dados"])
                dados = {**dados, "selecao": dados_atuais.get("selecao") or {}}
                d_json = json.dumps(dados, ensure_ascii=False)
                novos = (receita_id, porcoes, cmv_total, cmv_porcao)
                atuais = tuple(prato[campo] for campo in ("receita_id", "porcoes", "cmv_total", "cmv_porcao"))
                if atuais != novos or dados_atuais != dados or prato["status"] != "cmv_calculado":
                    self._liberar_itens_do_rascunho(conn, prato["id"], prato["receita_id"])
                    conn.execute("""UPDATE pratos SET receita_id = ?, porcoes = ?, cmv_total = ?, cmv_porcao = ?, dados = ?,
                                  status = 'cmv_calculado', atualizado_em = ? WHERE id = ?""", (*novos, d_json, _now(), prato["id"]))
                    conn.execute("DELETE FROM analises_preco WHERE prato_id = ?", (prato["id"],))
                return prato["id"]
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

    def salvar_analise_preco(
        self, prato_id: int, dados: dict[str, Any], *, receita_id_esperada: int | None = None,
        compras: list[dict[str, Any]] | None = None, da_despensa: list[dict[str, Any]] | None = None,
        reservas: list[dict[str, Any]] | None = None, resultado: dict[str, Any] | None = None,
    ) -> None:
        """Grava a análise e, no aceite do rascunho, separa o lote na mesma transação."""
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prato = conn.execute("SELECT * FROM pratos WHERE id = ?", (prato_id,)).fetchone()
            if not prato or prato["status"] not in ("cmv_calculado", "aceito"):
                raise ValueError("o CMV do prato precisa estar calculado para salvar a análise")
            if receita_id_esperada is not None and prato["receita_id"] != receita_id_esperada:
                raise ValueError("a versão da receita mudou, reconfira o lote antes de reservar")
            if receita_id_esperada is not None and prato["status"] == "cmv_calculado":
                self._trocar_reservas(conn, prato_id, reservas or [])
                self._montar_carrinho_receita(conn, receita_id_esperada, prato["nome"], compras or [], da_despensa or [],
                                             origem="receita_aceita")
                self._registrar_historico_prato(conn, prato, registrado_em=_now(), resultado=resultado)
            conn.execute(
                """INSERT INTO analises_preco(nome_norm, prato_id, dados, atualizado_em) VALUES(?,?,?,?)
                   ON CONFLICT(nome_norm) DO UPDATE SET prato_id = excluded.prato_id,
                       dados = excluded.dados, atualizado_em = excluded.atualizado_em""",
                (norm_text(prato["nome"]), prato_id, json.dumps(dados, ensure_ascii=False), _now()),
            )

    def analises_preco(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT a.prato_id, a.dados, p.nome, p.status, p.porcoes, p.cmv_total, p.preco_venda,
                          max(a.atualizado_em, p.atualizado_em) AS atualizado_em
                   FROM analises_preco a JOIN pratos p ON p.id = a.prato_id
                   WHERE p.status IN ('cmv_calculado', 'aceito')
                   ORDER BY atualizado_em DESC, p.id DESC"""
            ).fetchall()
        return [{**dict(row), "dados": json.loads(row["dados"])} for row in rows]

    def aceitar_prato(self, prato_id: int, preco_venda: float, compras: list[dict[str, Any]],
                      reservas: Optional[list[dict[str, Any]]] = None, *, confirmar_compras: bool = True,
                      dados: Optional[dict[str, Any]] = None, cmv_total: float | None = None, cmv_porcao: float | None = None,
                      receita_id_esperada: int | None = None) -> dict[str, Any]:
        """Aceite numa transação só: status + preço, compras comprometidas e reservas de estoque.

        Idempotente por ``prato_id``: compras e reservas anteriores do mesmo prato são substituídas,
        então repetir a chamada (retry, troca de preço) nunca duplica orçamento nem reserva.
        """
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prato = conn.execute("SELECT status, receita_id FROM pratos WHERE id = ?", (prato_id,)).fetchone()
            if not prato:
                raise KeyError(f"Prato {prato_id} não encontrado")
            if prato["status"] not in ("cmv_calculado", "aceito"):
                raise ValueError("calcule o CMV do prato antes de registrar no cardápio")
            if receita_id_esperada is not None and prato["receita_id"] != receita_id_esperada:
                raise ValueError("a versão da receita mudou, confira os preços da nova versão")
            baixados = conn.execute("DELETE FROM carrinho WHERE receita_id = ?", (prato["receita_id"],)).rowcount
            if confirmar_compras:
                total = round(sum(float(c.get("preco_estimado") or 0) for c in compras), 2)
                saldo = self._ler_orcamento(conn, prato_id)["saldo"]
                if total > 0 and total > saldo + 1e-9:
                    raise OrcamentoEstourado(total - saldo, saldo)
            self._trocar_reservas(conn, prato_id, reservas or [])
            if dados is not None:
                conn.execute("""UPDATE pratos SET preco_venda = ?, status = 'aceito', dados = ?, atualizado_em = ?,
                                cmv_total = COALESCE(?, cmv_total), cmv_porcao = COALESCE(?, cmv_porcao) WHERE id = ?""",
                             (float(preco_venda), json.dumps(dados, ensure_ascii=False), _now(), cmv_total, cmv_porcao, prato_id))
            else:
                conn.execute("UPDATE pratos SET preco_venda = ?, status = 'aceito', atualizado_em = ? WHERE id = ?", (float(preco_venda), _now(), prato_id))
            prato_atual = conn.execute("SELECT * FROM pratos WHERE id = ?", (prato_id,)).fetchone()
            historico_id = self._registrar_historico_prato(conn, prato_atual, registrado_em=prato_atual["atualizado_em"])
            self._atualizar_compras_do_lote(conn, prato_id, historico_id, compras, confirmar_compras)
        return {**(self.obter_prato(prato_id) or {}), "linhas_do_carrinho_que_viraram_compras": baixados}

    @staticmethod
    def _atualizar_compras_do_lote(conn: sqlite3.Connection, prato_id: int, historico_id: int,
                                   compras: list[dict[str, Any]], confirmar_compras: bool) -> None:
        """Mantém as mesmas linhas em reenvios e preserva compromissos substituídos."""
        status = "confirmada" if confirmar_compras else "planejada"
        campos = ("ingrediente", "quantidade", "unidade", "preco_estimado")
        atuais = conn.execute("SELECT * FROM compras WHERE prato_id = ? AND status IN ('confirmada', 'planejada') ORDER BY id",
                              (prato_id,)).fetchall()
        novas = [(str(c.get("ingrediente")), c.get("quantidade"), c.get("unidade"), float(c.get("preco_estimado") or 0))
                 for c in compras]
        if ([tuple(r[campo] for campo in campos) for r in atuais] == novas
                and all(r["historico_prato_id"] == historico_id for r in atuais)):
            if any(r["status"] != status for r in atuais):
                conn.execute("UPDATE compras SET status = ? WHERE prato_id = ? AND status IN ('confirmada', 'planejada')",
                             (status, prato_id))
            return
        conn.execute("UPDATE compras SET status = 'cancelada' WHERE prato_id = ? AND status IN ('confirmada', 'planejada')",
                     (prato_id,))
        conn.executemany(
            """INSERT INTO compras(prato_id, historico_prato_id, ingrediente, quantidade, unidade, preco_estimado, status, criado_em)
               VALUES(?,?,?,?,?,?,?,?)""",
            [(prato_id, historico_id, *c, status, _now()) for c in novas],
        )

    def _trocar_reservas(self, conn: sqlite3.Connection, prato_id: int, reservas: list[dict[str, Any]]) -> None:
        """Confere o estoque dentro da transação também quando outra ficha reservou o mesmo item."""
        conn.execute("DELETE FROM reservas WHERE prato_id = ?", (prato_id,))
        por_ingrediente: dict[int, float] = {}
        for item in reservas:
            qtd = float(item.get("quantidade_base") or 0)
            if qtd <= 1e-9 or item.get("ingrediente_id") is None:
                continue
            ingrediente_id = int(item["ingrediente_id"])
            por_ingrediente[ingrediente_id] = por_ingrediente.get(ingrediente_id, 0.0) + qtd
        if not por_ingrediente:
            return
        ocupadas = self._reservas_map(conn, considerar_todos=True)
        for ingrediente_id, qtd in por_ingrediente.items():
            ingrediente = conn.execute("SELECT nome, estoque_base FROM ingredientes WHERE id = ?", (ingrediente_id,)).fetchone()
            if ingrediente is None:
                raise ValueError("um ingrediente saiu da despensa, confira novamente o lote")
            livre = float(ingrediente["estoque_base"]) - ocupadas.get(ingrediente_id, 0.0)
            if not math.isfinite(qtd) or qtd > livre + 1e-6:
                raise ValueError(f"o estoque disponível de {ingrediente['nome']} mudou, confira novamente o lote")
            conn.execute("INSERT INTO reservas(prato_id, ingrediente_id, quantidade_base, criado_em) VALUES(?,?,?,?)",
                         (prato_id, ingrediente_id, qtd, _now()))

    @staticmethod
    def _liberar_itens_do_rascunho(conn: sqlite3.Connection, prato_id: int, receita_id: int | None) -> None:
        conn.execute("DELETE FROM reservas WHERE prato_id = ?", (prato_id,))
        conn.execute("DELETE FROM carrinho WHERE receita_id = ? AND origem = 'receita_aceita'", (receita_id,))

    def _reabrir_preparo(self, conn: sqlite3.Connection, prato: sqlite3.Row, *, retirar_intencao: bool = False) -> None:
        self._liberar_itens_do_rascunho(conn, prato["id"], prato["receita_id"])
        conn.execute("DELETE FROM analises_preco WHERE prato_id = ?", (prato["id"],))
        dados = json.loads(prato["dados"])
        if retirar_intencao:
            (dados.get("selecao") or {}).pop("usar_quando_viavel", None)
        conn.execute("""UPDATE pratos SET status = 'selecionado', cmv_total = NULL, cmv_porcao = NULL,
                        dados = ?, atualizado_em = ? WHERE id = ?""",
                     (json.dumps(dados, ensure_ascii=False), _now(), prato["id"]))

    def liberar_compromisso_inviavel(self, prato_id: int, receita_id: int) -> bool:
        """Libera o lote ainda em preparação para uma versão que se tornou inviável."""
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prato = conn.execute(
                """SELECT p.* FROM pratos p WHERE p.id = ? AND p.receita_id = ? AND p.status = 'cmv_calculado' AND (
                       EXISTS (SELECT 1 FROM analises_preco WHERE prato_id = p.id)
                    OR EXISTS (SELECT 1 FROM reservas WHERE prato_id = p.id)
                    OR EXISTS (SELECT 1 FROM carrinho WHERE receita_id = p.receita_id AND origem = 'receita_aceita'))""",
                (prato_id, receita_id)).fetchone()
            if prato is None:
                return False
            self._reabrir_preparo(conn, prato)
            return True

    def remover_prato(self, prato_id: int, motivo: Optional[str] = None) -> dict[str, Any]:
        """Tira o prato do cardápio: libera as reservas e cancela as compras comprometidas no orçamento.

        Compra real já lançada no estoque (``registrar_compra``) não é desfeita: o estoque e o custo ficam.
        """
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prato = conn.execute("SELECT * FROM pratos WHERE id = ?", (prato_id,)).fetchone()
            if not prato:
                raise KeyError(f"Prato {prato_id} não encontrado")
            reservas = conn.execute(
                """SELECT i.nome AS ingrediente, i.unidade_base, r.quantidade_base FROM reservas r
                   JOIN ingredientes i ON i.id = r.ingrediente_id WHERE r.prato_id = ? ORDER BY r.id""", (prato_id,)).fetchall()
            compras = conn.execute("SELECT * FROM compras WHERE prato_id = ? AND status IN ('confirmada', 'planejada') ORDER BY id", (prato_id,)).fetchall()
            conn.execute("UPDATE compras SET status = 'cancelada' WHERE prato_id = ? AND status IN ('confirmada', 'planejada')", (prato_id,))
            self._liberar_itens_do_rascunho(conn, prato_id, prato["receita_id"])
            dados = {**json.loads(prato["dados"]), "remocao": {"motivo": motivo, "em": _now(), "status_anterior": prato["status"]}}
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
                   WHERE p.status IN ('cmv_calculado', 'aceito') ORDER BY r.prato_id, r.id""").fetchall()
        return [{"prato_id": r["prato_id"], "prato": r["prato"], "ingrediente": r["ingrediente"],
                 "quantidade": format_base(r["quantidade_base"], r["unidade_base"])} for r in rows]

    # ------------------------------------------------------------------ histórico de compras
    @staticmethod
    def _migrar_historico(conn: sqlite3.Connection) -> None:
        pratos = conn.execute(
            """SELECT * FROM pratos p WHERE p.status IN ('aceito', 'removido') OR (
               p.status = 'cmv_calculado' AND EXISTS (SELECT 1 FROM analises_preco WHERE prato_id = p.id))"""
        ).fetchall()
        for prato in pratos:
            if prato["status"] == "removido" and (json.loads(prato["dados"]).get("remocao") or {}).get("status_anterior") != "aceito":
                continue
            historico_id = Store._registrar_historico_prato(conn, prato, registrado_em=None)
            conn.execute("UPDATE compras SET historico_prato_id = ? WHERE prato_id = ? AND historico_prato_id IS NULL",
                         (historico_id, prato["id"]))
        for lote in conn.execute("SELECT id, dados FROM historico_pratos").fetchall():
            dados = json.loads(lote["dados"])
            insumos = dados.pop("insumos", None)
            if insumos is not None:
                Store._gravar_insumos_lote(conn, lote["id"], insumos)
                conn.execute("UPDATE historico_pratos SET dados = ? WHERE id = ?",
                             (json.dumps(dados, ensure_ascii=False), lote["id"]))

    @staticmethod
    def _insumos_do_lote(conn: sqlite3.Connection, prato_id: int, resultado: dict[str, Any]) -> list[dict[str, Any]]:
        insumos = []
        for item in resultado.get("itens") or []:
            if not isinstance(item, dict):
                continue
            if item.get("item_despensa") and (item.get("usa_da_despensa_base") or 0) > 0:
                preparo = item.get("preparo_caseiro") or {}
                insumos.append({"ingrediente": item["item_despensa"], "ingrediente_id": item.get("item_id"),
                                "quantidade": item.get("usa_da_despensa"), "quantidade_base": item["usa_da_despensa_base"],
                                "unidade_base": item.get("unidade_base"),
                                "custo_uso": item.get("custo_uso_despensa"), "origem": "despensa",
                                **({"derivado": preparo["ingrediente"]} if preparo.get("ingrediente") else {})})
            compra = item.get("compra") or {}
            if compra.get("ingrediente"):
                insumos.append({"ingrediente": compra["ingrediente"], "quantidade": item.get("falta"),
                                "quantidade_base": item.get("quantidade_faltante_base"), "unidade_base": item.get("unidade_base"),
                                "custo_uso": item.get("custo_uso_compra"), "origem": "complemento"})
            if item.get("recurso_de_preparo"):
                insumos.append({"ingrediente": item["recurso_de_preparo"], "quantidade": item.get("quantidade_receita"),
                                "quantidade_base": item.get("necessario_base"), "unidade_base": item.get("unidade_base"),
                                "custo_uso": item.get("custo_uso_total"), "origem": "preparo"})
        if not insumos:
            reservas = conn.execute(
                """SELECT i.id, i.nome, i.unidade_base, i.custo_unitario_base, r.quantidade_base FROM reservas r
                   JOIN ingredientes i ON i.id = r.ingrediente_id WHERE r.prato_id = ? ORDER BY r.id""",
                (prato_id,)).fetchall()
            insumos = [{"ingrediente": row["nome"], "ingrediente_id": row["id"],
                        "quantidade": format_base(row["quantidade_base"], row["unidade_base"]),
                        "quantidade_base": row["quantidade_base"], "unidade_base": row["unidade_base"],
                        "custo_uso": row["quantidade_base"] * row["custo_unitario_base"] if row["custo_unitario_base"] is not None else None,
                        "origem": "despensa"} for row in reservas]
        return insumos

    @staticmethod
    def _gravar_insumos_lote(conn: sqlite3.Connection, lote_id: int, insumos: list[dict[str, Any]]) -> None:
        conn.execute("DELETE FROM historico_prato_insumos WHERE lote_id = ?", (lote_id,))
        conn.executemany(
            """INSERT INTO historico_prato_insumos(lote_id, ordem, ingrediente_id, ingrediente, quantidade,
               quantidade_base, unidade_base, custo_uso, origem, derivado) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [(lote_id, ordem, *(item.get(campo) for campo in _CAMPOS_INSUMO)) for ordem, item in enumerate(insumos)],
        )

    @staticmethod
    def _despesa_despensa_do_lote(conn: sqlite3.Connection, insumos: list[dict[str, Any]], custo_despensa: float) -> float:
        """Apropria apenas a parcela do custo médio que ainda não entrou como compra paga."""
        valores_pagos = {}
        for compra in conn.execute("SELECT ingrediente, preco_estimado FROM compras WHERE prato_id IS NULL AND status = 'confirmada'"):
            nome = norm_text(compra["ingrediente"])
            valores_pagos[nome] = valores_pagos.get(nome, 0.0) + compra["preco_estimado"]
        duplicado = 0.0
        for linha in insumos:
            pago = valores_pagos.get(norm_text(linha["ingrediente"]), 0.0)
            if linha["origem"] != "despensa" or not pago:
                continue
            item = conn.execute("SELECT comprado_base, preco_total FROM ingredientes WHERE id = ?", (linha.get("ingrediente_id"),)).fetchone()
            if item and item["comprado_base"] and item["preco_total"]:
                duplicado += linha["quantidade_base"] * min(item["preco_total"], pago) / item["comprado_base"]
        return round(max(0.0, custo_despensa - duplicado), 2)

    @staticmethod
    def _registrar_historico_prato(conn: sqlite3.Connection, prato: sqlite3.Row, *, registrado_em: str | None,
                                  resultado: dict[str, Any] | None = None) -> int:
        """Tira uma fotografia de cada lote aceito e mantém a mesma data na escolha de preço."""
        registro = conn.execute("SELECT * FROM historico_pratos WHERE prato_id = ? AND receita_id IS ?",
                                (prato["id"], prato["receita_id"])).fetchone()
        if registro is not None and prato["status"] == "removido":
            return int(registro["id"])
        dados = json.loads(prato["dados"])
        receita = conn.execute("SELECT dados FROM receitas WHERE id = ?", (prato["receita_id"],)).fetchone()
        dados_receita = json.loads(receita["dados"]) if receita else {}
        resultado = resultado or dados.get("resultado") or dados_receita.get("resultado") or {}
        insumos = Store._insumos_do_lote(conn, prato["id"], resultado)
        if not insumos and registro:
            insumos = json.loads(registro["dados"]).get("insumos") or []
        custo_despensa = resultado.get("custo_vindo_da_despensa")
        if custo_despensa is None:
            custo_despensa = round(sum((i.get("custo_uso") or 0) for i in insumos if i["origem"] == "despensa"), 2)
        snapshot = {"prato": prato["nome"], "porcoes": prato["porcoes"], "cmv_total": prato["cmv_total"], "custo_despensa": custo_despensa}
        bruto = json.dumps(snapshot, ensure_ascii=False)
        if registro:
            historico_id = int(registro["id"])
            if registro["dados"] == bruto:
                gravados = conn.execute(
                    f"SELECT {', '.join(_CAMPOS_INSUMO)} FROM historico_prato_insumos WHERE lote_id = ? ORDER BY ordem",
                    (historico_id,),
                ).fetchall()
                if [tuple(item) for item in gravados] == [tuple(item.get(campo) for campo in _CAMPOS_INSUMO) for item in insumos]:
                    return historico_id
            despesa = Store._despesa_despensa_do_lote(conn, insumos, custo_despensa)
            conn.execute("UPDATE historico_pratos SET dados = ?, despesa_despensa = ? WHERE id = ?", (bruto, despesa, historico_id))
        else:
            despesa = Store._despesa_despensa_do_lote(conn, insumos, custo_despensa)
            cursor = conn.execute(
                "INSERT INTO historico_pratos(prato_id, receita_id, registrado_em, dados, despesa_despensa) VALUES(?,?,?,?,?)",
                (prato["id"], prato["receita_id"], registrado_em, bruto, despesa))
            historico_id = int(cursor.lastrowid)
        Store._gravar_insumos_lote(conn, historico_id, insumos)
        conn.execute(
            "UPDATE compras SET historico_prato_id = ? WHERE receita_origem_id = ? AND historico_prato_id IS NULL",
            (historico_id, prato["receita_id"]))
        return historico_id

    def historico_compras(self) -> dict[str, Any]:
        """Lista compras e lotes confirmados, inclusive quando a ficha sai do cardápio."""
        with self.connect() as conn:
            conn.execute("BEGIN")
            lotes = conn.execute(
                f"""SELECT h.*, CASE WHEN {_LOTE_ATIVO} AND p.status = 'aceito' THEN 'no_cardapio'
                          WHEN {_LOTE_ATIVO} THEN 'preco_pendente' ELSE 'encerrado' END AS status
                   FROM historico_pratos h LEFT JOIN pratos p ON p.id = h.prato_id ORDER BY h.id DESC""").fetchall()
            insumos = conn.execute(
                """SELECT lote_id, ingrediente, quantidade, quantidade_base, unidade_base, custo_uso, origem, derivado
                   FROM historico_prato_insumos ORDER BY lote_id, ordem""").fetchall()
            compras = conn.execute(
                """SELECT * FROM compras WHERE historico_prato_id IS NOT NULL
                   OR (prato_id IS NULL AND status = 'confirmada') ORDER BY id DESC""").fetchall()
        por_lote: dict[int, list[dict[str, Any]]] = {}
        insumos_por_lote: dict[int, list[dict[str, Any]]] = {}
        for linha in insumos:
            insumo = dict(linha)
            lote_id = insumo.pop("lote_id")
            if insumo["derivado"] is None:
                del insumo["derivado"]
            insumos_por_lote.setdefault(lote_id, []).append(insumo)
        avulsas = []
        for row in compras:
            registro = {k: row[k] for k in ("id", "ingrediente", "quantidade", "unidade")}
            registro.update(valor=row["preco_estimado"], registrado_em=row["criado_em"],
                            status=("paga" if row["prato_id"] is None else "comprometida")
                            if row["status"] == "confirmada" else row["status"])
            if row["historico_prato_id"] is None:
                avulsas.append(registro)
            else:
                por_lote.setdefault(row["historico_prato_id"], []).append(registro)
        return {"lotes": [{**json.loads(row["dados"]), "id": row["id"], "prato_id": row["prato_id"],
                            "receita_id": row["receita_id"], "registrado_em": row["registrado_em"],
                            "ativo": row["status"] != "encerrado", "status": row["status"],
                            "insumos": insumos_por_lote.get(row["id"], []), "complementos": por_lote.get(row["id"], [])}
                           for row in lotes],
                "avulsas": avulsas}

    # ------------------------------------------------------------------ orçamento
    def orcamento(self) -> dict[str, Any]:
        self.ensure_loaded()
        with self.connect() as conn:
            conn.execute("BEGIN")
            return self._ler_orcamento(conn, self.ignorar_prato_id)

    def _ler_orcamento(self, conn: sqlite3.Connection, ignorar_prato_id: Optional[int] = None) -> dict[str, Any]:
        inicial = float(self._config_get(conn, "orcamento_inicial") or default_budget())
        rows = conn.execute(
            """SELECT c.id, c.prato_id, c.ingrediente, c.quantidade, c.unidade, c.preco_estimado, c.status,
                      c.criado_em, p.nome AS prato
               FROM compras c LEFT JOIN pratos p ON p.id = c.prato_id
               WHERE (? IS NULL OR c.prato_id IS NOT ?) ORDER BY c.id""",
            (ignorar_prato_id, ignorar_prato_id)).fetchall()
        carrinho = self._carrinho_total(conn, ignorar_prato_id)
        confirmadas = [dict(r) for r in rows if r["status"] == "confirmada"]
        planejadas = [dict(r) for r in rows if r["status"] == "planejada"]
        gasto_confirmado = round(sum(r["preco_estimado"] for r in confirmadas if r["prato_id"] is None), 2)
        reservado_cardapio = round(sum(r["preco_estimado"] for r in confirmadas if r["prato_id"] is not None), 2)
        comprometido = round(gasto_confirmado + reservado_cardapio, 2)
        despesa_despensa = round(conn.execute(
            f"""SELECT COALESCE(SUM(h.despesa_despensa), 0) AS total FROM historico_pratos h
                 JOIN pratos p ON p.id = h.prato_id WHERE {_LOTE_ATIVO} AND p.id IS NOT ?""",
            (ignorar_prato_id,),
        ).fetchone()["total"], 2)
        compromissos_em_preparo = conn.execute(
            f"""SELECT COALESCE(SUM(c.preco_total), 0) AS total FROM carrinho c
                 WHERE c.ja_tem = 0 AND c.origem = 'receita_aceita' AND EXISTS (
                   SELECT 1 FROM historico_pratos h JOIN pratos p ON p.id = h.prato_id
                   WHERE {_LOTE_ATIVO} AND p.status = 'cmv_calculado' AND p.id IS NOT ?
                     AND h.receita_id = c.receita_id)""", (ignorar_prato_id,),
        ).fetchone()["total"]
        despesa_complementos = round(comprometido + compromissos_em_preparo, 2)
        despesas = round(despesa_despensa + despesa_complementos, 2)
        return {
            "inicial": inicial,
            "comprometido": comprometido,
            "gasto_confirmado": gasto_confirmado,
            "reservado_cardapio": reservado_cardapio,
            "restante": round(inicial - comprometido, 2),
            "carrinho": carrinho,
            "saldo": round(inicial - comprometido - carrinho, 2),
            "despesa_despensa": despesa_despensa,
            "despesa_complementos": despesa_complementos,
            "despesas": despesas,
            "saldo_gerencial": round(inicial - despesas, 2),
            "compras_confirmadas": confirmadas,
            "compras_planejadas": planejadas,
        }

    # ------------------------------------------------------------------ carrinho
    def _carrinho_total(self, conn: sqlite3.Connection, ignorar_prato_id: int | None = None) -> float:
        # Só o que ela vai comprar gasta saldo: o ingrediente que já tem na despensa aparece com valor, mas não conta.
        return round(float(conn.execute(
            """SELECT COALESCE(SUM(preco_total), 0) FROM carrinho
               WHERE ja_tem = 0 AND (receita_id IS NULL OR receita_id IS NOT (SELECT receita_id FROM pratos WHERE id = ?))""",
            (ignorar_prato_id,)).fetchone()[0]), 2)

    def carrinho_itens(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM carrinho ORDER BY id").fetchall()
        return [_item_carrinho(r) for r in rows]

    def carrinho_resumo(self, orcamento: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Quanto ela tem (orçamento), quanto já escolheu (carrinho) e o saldo que sobra."""
        orc = self.orcamento() if orcamento is None else orcamento
        chaves = ("inicial", "comprometido", "gasto_confirmado", "reservado_cardapio", "carrinho", "saldo",
                  "despesa_despensa", "despesa_complementos", "despesas", "saldo_gerencial")
        return {**{chave: orc[chave] for chave in chaves}, "estourou": orc["saldo"] < -0.005}

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
        self.ensure_loaded()
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            saldo = self._ler_orcamento(conn)["saldo"]
            if custo > saldo + 1e-9:
                raise OrcamentoEstourado(custo - saldo, saldo)
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
        self.ensure_loaded()
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            saldo = self._ler_orcamento(conn)["saldo"]
            if a_comprar > saldo + 1e-9:
                raise OrcamentoEstourado(a_comprar - saldo, saldo)
            for l in linhas:
                self._gravar_linha(conn, l["nome"], l["preco_total"], l["quantidade"], l["unidade"], estimado=estimado, origem=origem,
                                   receita_id=receita_id, motivo=None, prato=nome_prato, ja_tem=l["ja_tem"])
        return next(g for g in self.carrinho_pratos() if norm_text(g["prato"]) == norm_text(nome_prato))

    def carrinho_atualizar_quantidade(self, item_id: int, quantidade: float) -> dict[str, Any]:
        """Muda a quantidade mantendo o preço por unidade (o total da linha acompanha)."""
        qtd = _numero(quantidade, "quantidade")
        if qtd <= 0:
            raise ValueError("a quantidade precisa ser maior que zero; para tirar o item, remova-o")
        self.ensure_loaded()
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM carrinho WHERE id = ?", (int(item_id),)).fetchone()
            if row is None:
                raise KeyError(f"o item {item_id} não está no carrinho")
            if row["origem"] == "receita_aceita":
                raise ValueError("revise a ficha do prato para alterar a quantidade reservada para o lote")
            novo_total = round(row["preco_total"] / row["quantidade"] * qtd, 2)
            aumento = round(novo_total - row["preco_total"], 2)
            saldo = self._ler_orcamento(conn)["saldo"]
            if not row["ja_tem"] and aumento > saldo + 1e-9:
                raise OrcamentoEstourado(aumento - saldo, saldo)
            conn.execute("UPDATE carrinho SET quantidade = ?, preco_total = ? WHERE id = ?", (qtd, novo_total, row["id"]))
            row = conn.execute("SELECT * FROM carrinho WHERE id = ?", (row["id"],)).fetchone()
        return _item_carrinho(row)

    def carrinho_remover(self, item_id: Optional[int] = None, nome: Optional[str] = None) -> list[dict[str, Any]]:
        """Tira do carrinho pelo id ou pelo nome: ingrediente, ou o prato inteiro quando o nome é o do prato.
        Nome parecido só vale se der um único item (senão pede para ela dizer qual). Devolve o que saiu."""
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
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
            lotes = {r["receita_id"] for r in alvo if r["origem"] == "receita_aceita"}
            if any(r["origem"] == "receita_aceita" and r["receita_id"] in lotes and r["id"] not in ids for r in linhas):
                raise ValueError("retire o prato inteiro do carrinho para liberar a reserva deste lote")
            conn.execute(f"DELETE FROM carrinho WHERE id IN ({','.join('?' * len(ids))})", ids)
            self._reabrir_pratos_fora_do_carrinho(conn, alvo)
        return [_item_carrinho(r) for r in alvo]

    def carrinho_remover_prato(self, prato: str) -> list[dict[str, Any]]:
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            alvo = [r for r in conn.execute("SELECT * FROM carrinho ORDER BY id").fetchall() if r["prato"] and norm_text(r["prato"]) == norm_text(prato)]
            if not alvo:
                raise KeyError(f"o prato {prato!r} não está no carrinho")
            conn.execute(f"DELETE FROM carrinho WHERE id IN ({','.join('?' * len(alvo))})", [r["id"] for r in alvo])
            self._reabrir_pratos_fora_do_carrinho(conn, alvo)
        return [_item_carrinho(r) for r in alvo]

    def carrinho_limpar(self) -> int:
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            auto = conn.execute("SELECT receita_id, origem FROM carrinho WHERE origem = 'receita_aceita'").fetchall()
            removidos = conn.execute("DELETE FROM carrinho").rowcount
            self._reabrir_pratos_fora_do_carrinho(conn, auto)
            return removidos

    def _reabrir_pratos_fora_do_carrinho(self, conn: sqlite3.Connection, linhas: list[sqlite3.Row]) -> None:
        receitas = {r["receita_id"] for r in linhas if r["receita_id"] is not None and r["origem"] == "receita_aceita"}
        for receita_id in sorted(receitas):
            prato = conn.execute(
                """SELECT p.* FROM pratos p WHERE p.receita_id = ? AND p.status = 'cmv_calculado'
                    AND NOT EXISTS (SELECT 1 FROM carrinho WHERE receita_id = p.receita_id)""",
                (receita_id,)).fetchone()
            if prato is not None:
                self._reabrir_preparo(conn, prato, retirar_intencao=True)

    def carrinho_montar_da_receita(self, receita_id: int, receita_nome: str, compras: list[dict[str, Any]],
                                   da_despensa: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
        """Troca as linhas desta receita pelo prato completo: o que ela compra (os preços da conferência) e o que já tem na
        despensa (com o custo, sem gastar saldo). Repetir não duplica; respeita o saldo."""
        self.ensure_loaded()
        receita_id = int(receita_id)
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ha_lote = conn.execute(
                f"""SELECT 1 FROM historico_pratos h JOIN pratos p ON p.id = h.prato_id
                     WHERE h.receita_id = ? AND p.status = 'cmv_calculado' AND {_LOTE_ATIVO}""",
                (receita_id,),
            ).fetchone()
            if not ha_lote:
                self._montar_carrinho_receita(conn, receita_id, receita_nome, compras, da_despensa or [], origem="receita")
            rows = conn.execute("SELECT * FROM carrinho WHERE receita_id = ? ORDER BY id", (receita_id,)).fetchall()
        return [_item_carrinho(row) for row in rows]

    def _montar_carrinho_receita(self, conn: sqlite3.Connection, receita_id: int, receita_nome: str,
                                compras: list[dict[str, Any]], da_despensa: list[dict[str, Any]], *, origem: str) -> None:
        a_comprar = [(str(c.get("ingrediente") or "").strip(), c.get("quantidade"), c.get("unidade"), round(float(c.get("preco_estimado") or 0), 2))
                     for c in compras]
        a_comprar = [item for item in a_comprar if item[0] and item[3] > 0]
        da_casa = [(str(c.get("ingrediente") or "").strip(), c.get("quantidade"), c.get("unidade"), round(float(c.get("valor") or 0), 2))
                   for c in da_despensa]
        da_casa = [item for item in da_casa if item[0]]
        prato = receita_nome.strip()[:80]
        novas = [(nome[:80], float(qtd) if qtd else 1.0, str(un)[:20] if un else None,
                  valor, 0 if ja_tem else 1, origem, prato, ja_tem)
                 for ja_tem, itens in ((1, da_casa), (0, a_comprar)) for nome, qtd, un, valor in itens]
        campos = "nome, quantidade, unidade, preco_total, estimado, origem, prato, ja_tem"
        atuais = [tuple(row) for row in conn.execute(f"SELECT {campos} FROM carrinho WHERE receita_id = ? ORDER BY id", (receita_id,))]
        if atuais == novas:
            return
        conn.execute("DELETE FROM carrinho WHERE receita_id = ?", (receita_id,))
        novo = round(sum(item[3] for item in a_comprar), 2)
        saldo = self._ler_orcamento(conn)["saldo"]
        if novo > 0 and novo > saldo + 1e-9:
            raise OrcamentoEstourado(novo - saldo, saldo)
        conn.executemany(
            f"INSERT INTO carrinho({campos}, receita_id, criado_em) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(*linha, receita_id, _now()) for linha in novas],
        )

    def carrinho_baixar_receita(self, receita_id: int) -> int:
        """Retira as linhas da receita e libera o lote se a preparação estiver em aberto."""
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            alvo = conn.execute("SELECT receita_id, origem FROM carrinho WHERE receita_id = ?", (int(receita_id),)).fetchall()
            removidos = conn.execute("DELETE FROM carrinho WHERE receita_id = ?", (int(receita_id),)).rowcount
            self._reabrir_pratos_fora_do_carrinho(conn, alvo)
            return removidos

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
        """O perfil do negócio (onde a loja fica, tipo de negócio e tipo de alimentação), ou ``None`` se nada foi salvo.
        O que um perfil antigo guardava e saiu (o que vende, para quem, observações) não aparece mais."""
        with self.connect() as conn:
            return self._ler_negocio(conn)

    def _ler_negocio(self, conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
        bruto = self._config_get(conn, "negocio")
        if not bruto:
            return None
        dados = json.loads(bruto)
        return {**{campo: ([] if campo == "alimentacao" else "") for campo in negocio_mod.CAMPOS}, "pais": negocio_mod.PAIS,
                **{k: dados[k] for k in (*negocio_mod.CAMPOS, "atualizado_em") if k in dados}}

    def negocio_salvar(self, dados: Any) -> dict[str, Any]:
        """O formulário inteiro da tela Meu negócio: o que vier substitui o que estava salvo."""
        limpo = {**negocio_mod.normalizar(dados), "atualizado_em": _now()}
        with _LOCK, self.connect() as conn:
            self._config_set(conn, "negocio", json.dumps(limpo, ensure_ascii=False))
        return limpo

    def negocio_atualizar(self, partes: dict[str, Any], *, somar_alimentacao: bool = True) -> dict[str, Any]:
        """Muda só os campos enviados. Na conversa, os tipos de alimentação se somam conforme ela vai contando."""
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            atual = self._ler_negocio(conn) or {}
            junto = {**atual, **partes}
            if somar_alimentacao and "alimentacao" in partes:
                junto["alimentacao"] = list(dict.fromkeys([*(atual.get("alimentacao") or []), *partes["alimentacao"]]))
            limpo = {**negocio_mod.normalizar(junto), "atualizado_em": _now()}
            self._config_set(conn, "negocio", json.dumps(limpo, ensure_ascii=False))
            return limpo

    # ------------------------------------------------------------------ atividade da consultora (passo a passo da conversa)
    def registrar_consulta_despensa(self) -> None:
        with _LOCK, self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO config(chave, valor) VALUES ('despensa_consultada', '1')")

    def despensa_consultada(self) -> bool:
        with self.connect() as conn:
            if self._config_get(conn, "despensa_consultada"):
                return True
            return conn.execute("SELECT 1 FROM atividade WHERE ferramenta IN ('despensa_listar', 'despensa_buscar') AND ok = 1 LIMIT 1").fetchone() is not None

    def atividade_registrar(self, chamada: str, ferramenta: str, titulo: str, *, sessao: str = "", entrada: str = "", resumo: str = "",
                            itens: Optional[list[str]] = None, ok: bool = True, duracao_ms: Optional[int] = None) -> None:
        """Guarda o que uma ferramenta fez (uma linha por chamada do Hermes, a mesma ``chamada`` que a tela recebe no streaming)."""
        with _LOCK, self.connect() as conn:
            conn.execute(
                """INSERT INTO atividade(chamada, sessao, ferramenta, entrada, titulo, resumo, itens, ok, duracao_ms, criado_em)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(chamada) DO UPDATE SET ferramenta = excluded.ferramenta, entrada = excluded.entrada, titulo = excluded.titulo,
                       resumo = excluded.resumo, itens = excluded.itens, ok = excluded.ok, duracao_ms = excluded.duracao_ms""",
                (chamada, sessao or None, ferramenta, entrada, titulo, resumo, json.dumps(itens or [], ensure_ascii=False),
                 1 if ok else 0, duracao_ms, _now()))
            conn.execute("DELETE FROM atividade WHERE id <= (SELECT MAX(id) FROM atividade) - ?", (MAX_ATIVIDADE,))

    @staticmethod
    def _atividade_linha(r: sqlite3.Row) -> dict[str, Any]:
        return {"chamada": r["chamada"], "ferramenta": r["ferramenta"], "entrada": r["entrada"], "titulo": r["titulo"], "resumo": r["resumo"],
                "itens": json.loads(r["itens"] or "[]"), "ok": bool(r["ok"]), "duracao_ms": r["duracao_ms"], "criado_em": r["criado_em"]}

    def atividade_das_chamadas(self, chamadas: list[str]) -> dict[str, dict[str, Any]]:
        """O que as chamadas pedidas fizeram, por ``chamada`` (a que ainda não terminou não aparece)."""
        chamadas = [c for c in dict.fromkeys(chamadas) if c][:60]
        if not chamadas:
            return {}
        with self.connect() as conn:
            linhas = conn.execute(f"SELECT * FROM atividade WHERE chamada IN ({','.join('?' * len(chamadas))})", chamadas).fetchall()
        return {r["chamada"]: self._atividade_linha(r) for r in linhas}

    def atividade_recente(self, limite: int = 30) -> list[dict[str, Any]]:
        """As últimas ações da consultora, da mais nova para a mais antiga."""
        with self.connect() as conn:
            linhas = conn.execute("SELECT * FROM atividade ORDER BY id DESC LIMIT ?", (max(1, min(int(limite), 100)),)).fetchall()
        return [self._atividade_linha(r) for r in linhas]
