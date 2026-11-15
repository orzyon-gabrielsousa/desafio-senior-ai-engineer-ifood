"""API HTTP mínima da tela: carrinho, pratos, itens salvos, perfil do negócio e Analytics.

Serviço pequeno, só stdlib, que usa o MESMO ``Store`` e o mesmo leitor de Analytics das tools do plugin:
nenhuma regra é duplicada e a tela e a consultora enxergam os mesmos dados no mesmo SQLite. Quem o expõe
ao navegador é o nginx do playground (``/api/``); a porta não é publicada no host além do loopback.

    python -m sabor_da_maria.api      # porta em SABOR_DA_MARIA_API_PORT (padrão 8700)

Rotas (JSON; erros voltam como ``{"erro": "..."}``):

    GET    /api/saude
    GET    /api/estado                       orçamento + carrinho + salvos + perfil do negócio + opções de seleção
    GET    /api/analytics?escopo=&limite=&categoria=    pratos mais vendidos, priorizados pelo lugar da loja
    POST   /api/carrinho/itens               {nome, preco_total, quantidade?, unidade?}   ingrediente avulso
    PATCH  /api/carrinho/itens/<id>          {quantidade}
    DELETE /api/carrinho/itens/<id>
    POST   /api/carrinho/pratos              {prato, ingredientes: [{nome, quantidade?, unidade?, preco_total, ja_tem?}]}
    DELETE /api/carrinho/pratos/<prato>      tira o prato inteiro
    DELETE /api/carrinho                     esvazia
    POST   /api/salvos                       {tipo: prato|ingrediente, nome, ingredientes? | preco_total, quantidade?, unidade?}
    POST   /api/salvos/<id>/carrinho         põe o item salvo no carrinho
    DELETE /api/salvos/<id>
    PUT    /api/negocio                      {tipo, vende[], publico?, observacoes?, estado?, cidade?, zona?}

Item que não cabe no saldo volta 409 com o ``estado`` atual; entrada inválida, 400.
"""

from __future__ import annotations

import json
import logging
import math
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from . import analytics
from . import negocio as negocio_mod
from .store import OrcamentoEstourado, Store

logger = logging.getLogger("sabor_da_maria.api")

MAX_CORPO = 65_536


def estado(store: Optional[Store] = None) -> dict[str, Any]:
    """Tudo o que a tela precisa numa chamada só."""
    store = store or Store()
    return {
        "orcamento": store.carrinho_resumo(),
        "carrinho": store.carrinho_itens(),
        "salvos": store.salvos(),
        "negocio": store.negocio(),
        "opcoes": negocio_mod.opcoes(),
    }


def _objeto(corpo: Any) -> dict[str, Any]:
    if not isinstance(corpo, dict):
        raise ValueError("envie um objeto JSON")
    return corpo


def _numero(valor: Any, rotulo: str) -> float:
    if isinstance(valor, bool) or valor is None or valor == "":
        raise ValueError(f"{rotulo} inválido")
    try:
        numero = float(str(valor).replace(",", ".")) if isinstance(valor, str) else float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{rotulo} inválido") from None
    if not math.isfinite(numero):
        raise ValueError(f"{rotulo} inválido")
    return numero


def _id(texto: str) -> int:
    if not texto.isdigit():
        raise ValueError("id inválido")
    return int(texto)


def tratar(metodo: str, caminho: str, corpo: Any = None) -> tuple[int, dict[str, Any]]:
    """Roteia uma requisição. Fica fora da classe HTTP para dar para testar sem abrir porta."""
    url = urlsplit(caminho)
    partes = [unquote(p) for p in url.path.split("/") if p]
    consulta = parse_qs(url.query)
    if partes[:1] != ["api"]:
        return 404, {"erro": "rota desconhecida"}
    rota = partes[1:]
    try:
        if rota == ["saude"] and metodo == "GET":
            return 200, {"status": "ok"}
        store = Store()
        if rota == ["estado"] and metodo == "GET":
            return 200, estado(store)
        if rota == ["analytics"] and metodo == "GET":
            limite = int(_numero(consulta.get("limite", ["10"])[0], "limite"))
            return 200, analytics.consultar(store.negocio(), consulta.get("escopo", ["prioridade"])[0], limite, consulta.get("categoria", [None])[0])
        if rota == ["carrinho", "itens"] and metodo == "POST":
            dados = _objeto(corpo)
            store.carrinho_adicionar(
                dados.get("nome"), _numero(dados.get("preco_total"), "preço"),
                _numero(dados.get("quantidade", 1), "quantidade"), dados.get("unidade"), origem="manual")
            return 201, estado(store)
        if len(rota) == 3 and rota[:2] == ["carrinho", "itens"]:
            item_id = _id(rota[2])
            if metodo == "PATCH":
                store.carrinho_atualizar_quantidade(item_id, _numero(_objeto(corpo).get("quantidade"), "quantidade"))
                return 200, estado(store)
            if metodo == "DELETE":
                store.carrinho_remover(item_id=item_id)
                return 200, estado(store)
        if rota == ["carrinho", "pratos"] and metodo == "POST":
            dados = _objeto(corpo)
            store.carrinho_adicionar_prato(dados.get("prato"), dados.get("ingredientes"), origem="manual")
            return 201, estado(store)
        if len(rota) == 3 and rota[:2] == ["carrinho", "pratos"] and metodo == "DELETE":
            store.carrinho_remover_prato(rota[2])
            return 200, estado(store)
        if rota == ["carrinho"] and metodo == "DELETE":
            store.carrinho_limpar()
            return 200, estado(store)
        if rota == ["salvos"] and metodo == "POST":
            dados = _objeto(corpo)
            store.salvo_salvar(dados.get("tipo"), dados.get("nome"), preco_total=dados.get("preco_total"), quantidade=dados.get("quantidade"),
                               unidade=dados.get("unidade"), ingredientes=dados.get("ingredientes"), origem="tela")
            return 201, estado(store)
        if len(rota) == 3 and rota[0] == "salvos" and rota[2] == "carrinho" and metodo == "POST":
            store.carrinho_do_salvo(_id(rota[1]))
            return 201, estado(store)
        if len(rota) == 2 and rota[0] == "salvos" and metodo == "DELETE":
            store.salvo_remover(_id(rota[1]))
            return 200, estado(store)
        if rota == ["negocio"] and metodo == "PUT":
            store.negocio_salvar(_objeto(corpo))
            return 200, estado(store)
        if rota and rota[0] in {"saude", "estado", "analytics", "carrinho", "salvos", "negocio"}:
            return 405, {"erro": "método não permitido"}
        return 404, {"erro": "rota desconhecida"}
    except OrcamentoEstourado as exc:
        return 409, {"erro": str(exc), "falta": exc.falta, "estado": estado()}
    except ValueError as exc:
        return 400, {"erro": str(exc)}
    except KeyError as exc:
        return 404, {"erro": str(exc.args[0]) if exc.args else "não encontrado"}
    except Exception:  # noqa: BLE001 - a tela recebe uma mensagem, o detalhe fica no log
        logger.exception("falha em %s %s", metodo, caminho)
        return 500, {"erro": "erro interno"}


class _Handler(BaseHTTPRequestHandler):
    server_version = "SaborDaMaria/1.0"

    def _responder(self, status: int, dados: dict[str, Any]) -> None:
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _executar(self, metodo: str) -> None:
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho > MAX_CORPO:
            self._responder(413, {"erro": "corpo grande demais"})
            return
        corpo: Any = None
        if tamanho:
            try:
                corpo = json.loads(self.rfile.read(tamanho))
            except (ValueError, UnicodeDecodeError):
                self._responder(400, {"erro": "JSON inválido"})
                return
        status, dados = tratar(metodo, self.path, corpo)
        self._responder(status, dados)

    def do_GET(self) -> None:  # noqa: N802 - nomes do BaseHTTPRequestHandler
        self._executar("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._executar("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._executar("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._executar("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._executar("DELETE")

    def log_message(self, formato: str, *args: Any) -> None:
        logger.info("%s %s", self.address_string(), formato % args)


def criar_servidor(host: str = "127.0.0.1", porta: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, porta), _Handler)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("SABOR_DA_MARIA_API_HOST", "0.0.0.0")
    porta = int(os.environ.get("SABOR_DA_MARIA_API_PORT", "8700"))
    try:
        Store().ensure_loaded()  # cria o banco e carrega a planilha já na subida
    except Exception as exc:  # noqa: BLE001 - a API sobe mesmo assim; a tool da despensa mostra o erro
        logger.warning("não consegui carregar a planilha: %s", exc)
    servidor = criar_servidor(host, porta)
    logger.info("API do carrinho em http://%s:%s", host, porta)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
