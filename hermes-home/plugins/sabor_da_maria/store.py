"""Persistência em SQLite dos dados do Sabor da Maria.

Banco embutido (stdlib ``sqlite3``), sem serviço externo: cabe no mesmo volume
do Hermes e sobrevive entre sessões. Tabelas:

* ``ingredientes``  — despensa normalizada (estoque, custo unitário em R$/kg, R$/L ou R$/un)
* ``perfil``        — equipamentos, utensílios, técnicas e restrições operacionais da cozinha: o que a consultora
                      elicita na conversa e o que ela marca na tela Minha cozinha (o mesmo registro, ver cozinha.py)
* ``receitas``      — candidatas conferidas (ingredientes, origem, pendências)
* ``pratos``        — escolhas em preparação, CMV calculado e, quando aceito, preço escolhido
* ``analises_preco`` — última análise da consultora por prato escolhido pela Dona Maria
* ``compras``       — compras complementares planejadas/confirmadas/canceladas (orçamento)
* ``reservas``      — estoque da despensa reservado por prato aceito (disponível = estoque − reservas)
* ``carrinho``      — itens que ela separou para comprar (a consultora mexe a pedido dela); prato = ingredientes com valor;
                      o que já está na despensa aparece com valor mas não gasta orçamento
* ``salvos``        — pratos (com ingredientes e valores) e ingredientes avulsos que ela guardou para usar de novo
* ``config``        — orçamento inicial, origem da planilha e perfil do negócio (onde a loja fica, tipo de negócio e tipo de alimentação)
* ``atividade``     — o que cada ferramenta da consultora fez (pesquisa na internet, leitura de receita, despensa, preços...), em
                      uma frase em português por chamada: a tela mostra o passo a passo com isso (ver atividade.py)

Reservas e compromissos de um prato são liberados por ``cardapio_remover``. Compras realizadas
entram na despensa e no orçamento, sem vínculo com prato. Um ``Store(ignorar_prato_id=X)`` desconta do cálculo as reservas e compras do próprio
prato X, para que repetir o registro de X (retry, troca de preço) não conte duas vezes.

O carrinho é rascunho de compra: ``saldo = restante − carrinho``. Itens ligados a uma receita saem do
carrinho quando o prato dela é aceito (viram compras comprometidas), para o mesmo gasto não contar duas vezes.
"""

from __future__ import annotations

import json
import math
import os
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
            liberado = sum(valor for valor, _ in baixadas)
            disponivel = round(saldo + liberado, 2)
            if total > disponivel + 1e-9:
                raise OrcamentoEstourado(total - disponivel, disponivel)
            for linha, (_, compra_atualizada) in zip(linhas, baixadas):
                self._entrada_no_estoque(conn, linha, cadastrar)
                if not compra_atualizada:
                    conn.execute(
                        """INSERT INTO compras(ingrediente, quantidade, unidade, preco_estimado, status, criado_em)
                           VALUES(?,?,?,?,'confirmada',?)""",
                        (linha["nome"], linha["quantidade"], linha["unidade"], linha["preco_total"], _now()),
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
                           receita_id: Optional[int]) -> tuple[float, bool]:
        """Libera o custo já reservado e indica se converteu uma compra já lançada no orçamento."""
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
            return (float(compra["preco_estimado"]) if compra["status"] == "confirmada" else 0.0), True
        if carrinho_id is not None:
            item = next((c for c in itens if c["id"] == carrinho_id), None)
            if item is None or not _mesma_quantidade(item, linha):
                raise ValueError(f"carrinho_id {carrinho_id} não corresponde ao ingrediente, à quantidade ou à unidade da compra")
            conn.execute("DELETE FROM carrinho WHERE id = ?", (carrinho_id,))
            return float(item["preco_total"]), False
        if candidatas or itens:
            ids = [f"compra_id={c['id']}" for c in candidatas] + [f"carrinho_id={c['id']}" for c in itens]
            raise ValueError(f"{linha['nome']} já tem uma linha pendente ({', '.join(ids)}). Identifique qual foi comprada")
        return 0.0, False

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
                    dados = {"selecao": {"fonte_url": fonte}}
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

    @staticmethod
    def _substituir_rascunho(conn: sqlite3.Connection, original: sqlite3.Row, alternativa_id: int) -> None:
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
                "compras_estimadas": custos.get("compras_total_estimado"), "custo_da_despensa": custos.get("custo_vindo_da_despensa")}

    def pratos_em_andamento(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [self._resumo_prato(row) for row in self._pratos_com_receita(conn)]

    def salvar_receita(self, nome: str, fonte_url: Optional[str], porcoes: Optional[float], dados: dict[str, Any], status: str,
                      *, prato_id: Optional[int] = None) -> int:
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prato = self._prato_para_preparar(conn, nome, prato_id=prato_id)
            if prato is not None:
                dados = {**dados, "prato_id": prato["id"]}
            cur = conn.execute("INSERT INTO receitas(nome, fonte_url, porcoes, dados, status, criado_em) VALUES(?,?,?,?,?,?)",
                               (nome, fonte_url, porcoes, json.dumps(dados, ensure_ascii=False), status, _now()))
            receita_id = int(cur.lastrowid)
            if prato is not None:
                conn.execute("""UPDATE pratos SET receita_id = ?, porcoes = ?, cmv_total = NULL, cmv_porcao = NULL,
                              preco_venda = NULL, status = 'selecionado', dados = ?, atualizado_em = ? WHERE id = ?""",
                             (receita_id, porcoes, json.dumps({"selecao": json.loads(prato["dados"]).get("selecao") or {}}, ensure_ascii=False),
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
        return d

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

    def salvar_analise_preco(self, prato_id: int, dados: dict[str, Any]) -> None:
        """Salva a versão mais recente da proposta para o mesmo prato."""
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prato = conn.execute("SELECT nome, status FROM pratos WHERE id = ?", (prato_id,)).fetchone()
            if not prato or prato["status"] not in ("cmv_calculado", "aceito"):
                raise ValueError("o CMV do prato precisa estar calculado para salvar a análise")
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
                      dados: Optional[dict[str, Any]] = None, cmv_total: float | None = None, cmv_porcao: float | None = None) -> dict[str, Any]:
        """Aceite numa transação só: status + preço, compras comprometidas e reservas de estoque.

        Idempotente por ``prato_id``: compras e reservas anteriores do mesmo prato são substituídas,
        então repetir a chamada (retry, troca de preço) nunca duplica orçamento nem reserva.
        """
        with _LOCK, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prato = conn.execute("SELECT status FROM pratos WHERE id = ?", (prato_id,)).fetchone()
            if not prato:
                raise KeyError(f"Prato {prato_id} não encontrado")
            if prato["status"] not in ("cmv_calculado", "aceito"):
                raise ValueError("calcule o CMV do prato antes de registrar no cardápio")
            if dados is not None:
                conn.execute("""UPDATE pratos SET preco_venda = ?, status = 'aceito', dados = ?, atualizado_em = ?,
                                cmv_total = COALESCE(?, cmv_total), cmv_porcao = COALESCE(?, cmv_porcao) WHERE id = ?""",
                             (float(preco_venda), json.dumps(dados, ensure_ascii=False), _now(), cmv_total, cmv_porcao, prato_id))
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
            return self._ler_orcamento(conn, self.ignorar_prato_id)

    def _ler_orcamento(self, conn: sqlite3.Connection, ignorar_prato_id: Optional[int] = None) -> dict[str, Any]:
        inicial = float(self._config_get(conn, "orcamento_inicial") or default_budget())
        rows = conn.execute(
            """SELECT c.*, p.nome AS prato FROM compras c LEFT JOIN pratos p ON p.id = c.prato_id
               WHERE (? IS NULL OR c.prato_id IS NOT ?) ORDER BY c.id""",
            (ignorar_prato_id, ignorar_prato_id)).fetchall()
        carrinho = self._carrinho_total(conn)
        confirmadas = [dict(r) for r in rows if r["status"] == "confirmada"]
        planejadas = [dict(r) for r in rows if r["status"] == "planejada"]
        gasto_confirmado = round(sum(r["preco_estimado"] for r in confirmadas if r["prato_id"] is None), 2)
        reservado_cardapio = round(sum(r["preco_estimado"] for r in confirmadas if r["prato_id"] is not None), 2)
        comprometido = round(gasto_confirmado + reservado_cardapio, 2)
        return {
            "inicial": inicial,
            "comprometido": comprometido,
            "gasto_confirmado": gasto_confirmado,
            "reservado_cardapio": reservado_cardapio,
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

    def carrinho_resumo(self, orcamento: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Quanto ela tem (orçamento), quanto já escolheu (carrinho) e o saldo que sobra."""
        orc = self.orcamento() if orcamento is None else orcamento
        chaves = ("inicial", "comprometido", "gasto_confirmado", "reservado_cardapio", "carrinho", "saldo")
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
            return conn.execute("SELECT 1 FROM atividade WHERE ferramenta = 'despensa_listar' AND ok = 1 LIMIT 1").fetchone() is not None

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
