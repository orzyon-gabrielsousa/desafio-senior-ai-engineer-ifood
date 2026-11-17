"""Cópia persistente das conversas que o playground exibe, separada do índice do agente."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

MAX_CORPO = 2 * 1024 * 1024
IDENTIFICADOR = re.compile(r"[\w:.-]{1,160}", re.ASCII)
SCHEMA = """
CREATE TABLE IF NOT EXISTS conversas (
    id TEXT PRIMARY KEY,
    atualizado REAL NOT NULL,
    dados TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversas_atualizado ON conversas(atualizado DESC);
CREATE TABLE IF NOT EXISTS conversas_excluidas (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS exclusoes (session_id TEXT PRIMARY KEY);
"""


class ConversaExcluida(Exception):
    pass


def diretorio(db_despensa: Path) -> Path:
    raiz = os.environ.get("SABOR_DA_MARIA_INTERNALS")
    return Path(raiz) if raiz else db_despensa.parent / "database-internals"


def geracao(db_despensa: Path) -> str | None:
    try:
        return db_despensa.with_name(".geracao-dados").read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


def identificador(valor: Any) -> str:
    if not isinstance(valor, str) or not IDENTIFICADOR.fullmatch(valor):
        raise ValueError("identificador de conversa inválido")
    return valor


def normalizar(id_conversa: str, conversa: Any) -> dict[str, Any]:
    if not isinstance(conversa, dict) or conversa.get("id") != id_conversa:
        raise ValueError("identificador diferente do da conversa")
    titulo, atualizado, mensagens = (conversa.get(k) for k in ("title", "updatedAt", "messages"))
    if not isinstance(titulo, str) or not titulo.strip() or len(titulo) > 160:
        raise ValueError("título de conversa inválido")
    if type(atualizado) not in (int, float) or not 0 <= atualizado <= 10**15:
        raise ValueError("data de atualização inválida")
    if not isinstance(mensagens, list) or len(mensagens) > 10_000:
        raise ValueError("lista de mensagens inválida")
    sessao = conversa.get("sessionId")
    if sessao is not None:
        identificador(sessao)
    ids = set()
    for mensagem in mensagens:
        if (not isinstance(mensagem, dict) or mensagem.get("role") not in ("user", "assistant")
                or not isinstance(mensagem.get("content"), str) or not isinstance(mensagem.get("tools"), list)):
            raise ValueError("mensagem de conversa inválida")
        mensagem_id = identificador(mensagem.get("id"))
        if mensagem_id in ids:
            raise ValueError("mensagem duplicada na conversa")
        ids.add(mensagem_id)
    return {"id": id_conversa, "title": titulo, "sessionId": sessao, "updatedAt": atualizado, "messages": mensagens}


def _data(mensagem: dict[str, Any]) -> int | float:
    valor = mensagem.get("at")
    return valor if type(valor) in (int, float) and 0 <= valor <= 10**15 else math.inf


def _mesclar(anterior: dict[str, Any], nova: dict[str, Any]) -> dict[str, Any]:
    nova_mais_recente = nova["updatedAt"] >= anterior["updatedAt"]
    mensagens = {m["id"]: m for m in anterior["messages"]}
    for mensagem in nova["messages"]:
        original = mensagens.get(mensagem["id"])
        # Um snapshot da resposta em andamento não pode desfazer uma resposta já concluída.
        if (original is None or (original.get("pending") and not mensagem.get("pending"))
                or (nova_mais_recente and not (mensagem.get("pending") and not original.get("pending")))):
            mensagens[mensagem["id"]] = mensagem
    ordenadas = list(mensagens.values())
    if all(_data(m) != math.inf for m in ordenadas):
        ordenadas.sort(key=_data)
    recente, antiga = (nova, anterior) if nova_mais_recente else (anterior, nova)
    return {**recente, "sessionId": recente["sessionId"] or antiga["sessionId"], "messages": ordenadas}


class Historico:
    def __init__(self, raiz: Path):
        self.path = raiz / "conversas.sqlite3"

    @contextmanager
    def _abrir(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600))
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.executescript(SCHEMA)
            with conn:
                yield conn

    def recentes(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._abrir() as conn:
            return [json.loads(dados) for dados, in conn.execute("SELECT dados FROM conversas ORDER BY atualizado DESC, id LIMIT 30")]

    def total(self) -> int:
        if not self.path.exists():
            return 0
        with self._abrir() as conn:
            return conn.execute("SELECT COUNT(*) FROM conversas").fetchone()[0]

    def salvar(self, id_conversa: str, conversa: Any) -> dict[str, Any]:
        nova = normalizar(id_conversa, conversa)
        with self._abrir() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM conversas_excluidas WHERE id = ?", (id_conversa,)).fetchone():
                raise ConversaExcluida()
            linha = conn.execute("SELECT dados FROM conversas WHERE id = ?", (id_conversa,)).fetchone()
            if linha:
                nova = _mesclar(json.loads(linha[0]), nova)
            dados = json.dumps(nova, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(dados.encode("utf-8")) > MAX_CORPO:
                raise ValueError("a conversa excede o limite de armazenamento")
            conn.execute("""INSERT INTO conversas(id, atualizado, dados) VALUES (?, ?, ?)
                         ON CONFLICT (id) DO UPDATE SET atualizado = excluded.atualizado, dados = excluded.dados""",
                         (id_conversa, nova["updatedAt"], dados))
        return nova

    def excluir(self, id_conversa: str) -> str | None:
        with self._abrir() as conn:
            conn.execute("BEGIN IMMEDIATE")
            linha = conn.execute("SELECT dados FROM conversas WHERE id = ?", (id_conversa,)).fetchone()
            sessao = json.loads(linha[0])["sessionId"] if linha else None
            conn.execute("INSERT OR IGNORE INTO conversas_excluidas(id) VALUES (?)", (id_conversa,))
            conn.execute("DELETE FROM conversas WHERE id = ?", (id_conversa,))
            if sessao:
                conn.execute("INSERT OR IGNORE INTO exclusoes(session_id) VALUES (?)", (sessao,))
        return sessao
