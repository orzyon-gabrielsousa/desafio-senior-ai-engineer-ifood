#!/usr/bin/env python3
"""Arquiva os dados da demo com os serviços que escrevem no SQLite parados."""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime
from pathlib import Path


BANCOS = (
    "sabor_da_maria/sabor_da_maria.db", "state.db", "response_store.db",
    "runs_idempotency.db", "projects.db", "kanban.db", "cron/executions.db",
    "telemetry/shared_metrics/metrics.sqlite3",
)
HISTORICO = (
    "sessions", "runtime", "memories", "pending_messages", "terminal-sessions",
    "logs", "hook_outputs", "plans", ".hermes_history", "spawn-ledger.json", "cache/web",
    "sabor_da_maria/.geracao-dados",
)
INTERNALS = ("memoria", "kv", "conversas.sqlite3", "conversas.sqlite3-wal", "conversas.sqlite3-shm", "conversas.sqlite3-journal")
MEMORIAS_INICIAIS = {
    "MEMORY.md": "A despensa inicial vem da planilha despensa_dona_maria.xlsx. Consulte o estoque e os custos pelas ferramentas. A taxa do desafio é fixa em 10% sobre a venda, sem impostos ou encargos adicionais; o orçamento inicial de complementos é R$ 80,00.\n",
    "USER.md": "Dona Maria está montando o delivery Sabor da Maria e fala português do Brasil. Ajude a cadastrar o negócio e a cozinha pelo chat.\n",
}


def resetar(projeto: Path) -> Path:
    dados = projeto / "hermes-home"
    backups = projeto / ".sabor-backups"
    backups.mkdir(exist_ok=True, mode=0o700)
    copia = Path(tempfile.mkdtemp(prefix=datetime.now().strftime("reset-%Y%m%d-%H%M%S-"), dir=backups))
    movidos: list[tuple[Path, Path]] = []
    criados: list[Path] = []
    caminhos = (*HISTORICO, *(banco + sufixo for banco in BANCOS for sufixo in ("", "-wal", "-shm", "-journal")))
    memorias = dados / "memories"
    try:
        para_arquivar = [(dados / relativo, copia / relativo) for relativo in caminhos]
        para_arquivar += [(projeto / "database-internals" / relativo, copia / "database-internals" / relativo)
                          for relativo in INTERNALS]
        for origem, destino in para_arquivar:
            if origem.exists() or origem.is_symlink():
                destino.parent.mkdir(parents=True, exist_ok=True)
                origem.rename(destino)
                movidos.append((origem, destino))
        memorias.mkdir(mode=0o700)
        criados.append(memorias)
        for nome, conteudo in MEMORIAS_INICIAIS.items():
            arquivo = memorias / nome
            criados.append(arquivo)
            arquivo.write_text(conteudo, encoding="utf-8")
        pasta_app = dados / "sabor_da_maria"
        pasta_app.mkdir(exist_ok=True)
        geracao = pasta_app / ".geracao-dados"
        criados.append(geracao)
        geracao.write_text(uuid.uuid4().hex, encoding="utf-8")
    except OSError:
        for caminho in reversed(criados):
            if caminho.is_dir():
                caminho.rmdir()
            else:
                caminho.unlink(missing_ok=True)
        for origem, destino in reversed(movidos):
            destino.rename(origem)
        raise
    return copia


if __name__ == "__main__":
    caminho = resetar(Path(__file__).resolve().parents[1])
    print(f"Dados da demo e conversas do Hermes limpos. Cópia local: {caminho}")
