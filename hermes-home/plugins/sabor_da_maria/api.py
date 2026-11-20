"""API HTTP mínima da tela: carrinho, pratos, itens salvos, perfil do negócio, Minha cozinha, Analytics e o passo a passo da consultora.

Serviço pequeno, só stdlib, que usa o MESMO ``Store`` e o mesmo leitor de Analytics das tools do plugin:
nenhuma regra é duplicada e a tela e a consultora enxergam os mesmos dados no mesmo SQLite. Quem o expõe
ao navegador é o nginx do playground (``/api/``); a porta não é publicada no host além do loopback.

    python -m sabor_da_maria.api      # porta em SABOR_DA_MARIA_API_PORT (padrão 8700)

Rotas (JSON; erros voltam como ``{"erro": "..."}``):

    GET    /api/saude                        verifica a abertura e a carga inicial do SQLite
    GET    /api/conversas                    cópia das conversas do playground em disco
    PUT    /api/conversas/<id>               guarda mensagens e o identificador de sessão do Hermes
    DELETE /api/conversas/<id>               remove a conversa da tela e do índice de busca
    GET    /api/memoria                      contagem de conversas, arquivos e vetores em disco
    GET    /api/estado                       orçamento + carrinho + salvos + perfil do negócio + a cozinha dela + avisos do
                                             carrinho (o que a cozinha dela não faz) + opções de seleção
    GET    /api/analytics?escopo=&limite=&categoria=    pratos mais vendidos, priorizados pelo lugar da loja, cada um com
                                             o que a cozinha dela faz e quanto cobrar para lucrar depois da taxa
    GET    /api/analytics/analises          {analises: [...]} propostas salvas pela consultora após o aceite da receita final,
    GET    /api/vendas/resumo               vendas realizadas: hoje, semana de segunda a domingo e mês, no fuso de São Paulo
    GET    /api/vendas?periodo=dia|semana|mes    histórico de vendas e totais do período (lista limitada, totais completos)
    POST   /api/vendas                      {prato_id,quantidade,preco_unitario?,vendido_em?:ISO com fuso,operacao:UUID}
    POST   /api/vendas/<id>/estorno          cancela a contagem sem apagar a venda nem alterar estoque/orçamento
    POST   /api/vendas/<id>/restaurar        reverte o estorno
    PUT    /api/analytics/taxas             compatibilidade: devolve regra fixa de 10% do enunciado
                                             com cenários, recomendação e, quando registrado, o preço escolhido
    GET    /api/precos?cmv=&preco=           quanto cobrar para um custo (CMV) dado, e o que sobra no preço do mercado
    POST   /api/carrinho/itens               {nome, preco_total, quantidade?, unidade?}   ingrediente avulso
    PATCH  /api/carrinho/itens/<id>          {quantidade}
    DELETE /api/carrinho/itens/<id>
    POST   /api/carrinho/pratos              {prato, ingredientes: [{nome, quantidade?, unidade?, preco_total, ja_tem?}]}
    DELETE /api/carrinho/pratos/<prato>      tira o prato inteiro
    DELETE /api/carrinho                     esvazia
    POST   /api/salvos                       {tipo: prato|ingrediente, nome, ingredientes? | preco_total, quantidade?, unidade?}
    POST   /api/salvos/<id>/carrinho         põe o item salvo no carrinho
    DELETE /api/salvos/<id>
    PUT    /api/negocio                      {tipo?, alimentacao?: [id], estado?, cidade?, zona?}   (onde a loja fica, tipo de negócio e de alimentação)
    PATCH  /api/negocio                      atualiza só os campos enviados pelo formulário do chat
    PUT    /api/cozinha                      {equipamentos?: {id: true|false|null}, habilidades?: {id: sim|aprendendo|nao|null},
                                              rotina?: {chave: valor|null}, remover?: [{grupo, chave}]}   (ids do catálogo)
    GET    /api/cozinha/pratos               cada prato da base com o que precisa, o que ela tem e as habilidades dela
    GET    /api/atividade?ids=a,b,c          o que cada chamada de ferramenta da consultora fez (o chat junta com os passos que recebe
                                             no streaming); sem ids, as últimas ações (?limite=30)

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

from . import analytics, costing, historico, precificacao, vendas
from . import cozinha as cozinha_mod
from . import negocio as negocio_mod
from . import jornada
from .store import OrcamentoEstourado, Store
from .tools import _pendencias_perfil

logger = logging.getLogger("sabor_da_maria.api")

MAX_CORPO = 65_536


def estado(store: Optional[Store] = None) -> dict[str, Any]:
    """Tudo o que a tela precisa numa chamada só."""
    store = store or Store()
    perfil = store.perfil()
    negocio = store.negocio()
    pendencias_cozinha = _pendencias_perfil(perfil)
    orcamento = store.orcamento()
    carrinho = store.carrinho_itens()
    analises_salvas = {analise["prato_id"] for analise in store.analises_preco()}
    pratos_atuais = jornada.pratos_atuais(store)
    return {
        "geracao_dados": historico.geracao(store.db_path),
        "jornada": jornada.resumo(store, pratos=pratos_atuais),
        "vendas_resumo": vendas.resumo(store),
        "orcamento": store.carrinho_resumo(orcamento=orcamento),
        "perfil_precificacao": store.perfil_precificacao(),
        "compras_pagas": [{"ingrediente": compra["ingrediente"], "quantidade": compra["quantidade"],
                           "unidade": compra["unidade"], "preco_total": compra["preco_estimado"]}
                          for compra in orcamento["compras_confirmadas"] if compra["prato_id"] is None],
        "historico_compras": store.historico_compras(),
        "pratos_escolhidos": [{**prato, "analise_preco_salva": prato["id"] in analises_salvas}
                              for prato in pratos_atuais],
        "despensa_consultada": store.despensa_consultada(),
        "carrinho": carrinho,
        "alertas": cozinha_mod.alertas_do_carrinho(carrinho, perfil),
        "salvos": store.salvos(),
        "negocio": negocio,
        "cozinha": store.cozinha(perfil),
        "cadastro": {"negocio_pendente": bool(negocio_mod.pendencias(negocio)),
                     "cozinha_pendente": bool(pendencias_cozinha),
                     "cozinha_campos": ["fogao" if p["chave"] == "fogao_bocas" else p["chave"] for p in pendencias_cozinha]},
        "opcoes": {**negocio_mod.opcoes(), "cozinha": cozinha_mod.opcoes()},
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


def _saude() -> tuple[int, dict[str, Any]]:
    try:
        Store().ensure_loaded()
    except Exception:  # noqa: BLE001 - a prontidão depende do banco e da carga inicial
        logger.exception("não consegui preparar o banco da consultoria")
        return 503, {"erro": "banco de dados indisponível"}
    return 200, {"status": "ok"}


def _conversas(metodo: str, rota: list[str], corpo: Any, store: Store) -> tuple[int, dict[str, Any]]:
    banco = historico.Historico(historico.diretorio(store.db_path))
    geracao = historico.geracao(store.db_path)
    if len(rota) == 1 and metodo == "GET":
        return 200, {"geracao_dados": geracao, "conversas": banco.recentes()}
    if len(rota) != 2 or metodo not in ("PUT", "DELETE"):
        return 405, {"erro": "método não permitido"}
    id_conversa = historico.identificador(rota[1])
    dados = _objeto(corpo)
    if "geracao_dados" not in dados or dados["geracao_dados"] != geracao:
        return 409, {"erro": "a geração dos dados mudou", "geracao_dados": geracao}
    if metodo == "PUT":
        try:
            return 200, {"conversa": banco.salvar(id_conversa, dados.get("conversa"))}
        except historico.ConversaExcluida:
            return 410, {"erro": "a conversa foi excluída"}
    sessao = banco.excluir(id_conversa)
    if sessao:
        from . import memoria

        memoria.excluir_sessao(sessao)
    return 200, {"ok": True}


def _segmentos(caminho: str) -> list[str]:
    return [unquote(p) for p in caminho.split("/") if p]


def tratar(metodo: str, caminho: str, corpo: Any = None) -> tuple[int, dict[str, Any]]:
    """Roteia uma requisição. Fica fora da classe HTTP para dar para testar sem abrir porta."""
    url = urlsplit(caminho)
    partes = _segmentos(url.path)
    consulta = parse_qs(url.query)
    if partes[:1] != ["api"]:
        return 404, {"erro": "rota desconhecida"}
    rota = partes[1:]
    if rota == ["saude"] and metodo == "GET":
        return _saude()
    try:
        store = Store()
        if rota[:1] == ["conversas"]:
            return _conversas(metodo, rota, corpo, store)
        if rota == ["memoria"] and metodo == "GET":
            from . import memoria

            banco = historico.Historico(historico.diretorio(store.db_path))
            return 200, {"conversas_salvas": banco.total(), **memoria.estatisticas()}
        if rota == ["estado"] and metodo == "GET":
            return 200, estado(store)
        if rota == ["jornada"] and metodo == "POST":
            jornada.responder(store, _objeto(corpo).get("acao"))
            return 200, estado(store)
        if rota == ["vendas", "resumo"] and metodo == "GET":
            return 200, vendas.resumo(store)
        if rota == ["vendas"]:
            if metodo == "GET":
                return 200, vendas.consultar(store, consulta.get("periodo", ["dia"])[0])
            if metodo == "POST":
                resultado = vendas.registrar(store, _objeto(corpo))
                return 200 if resultado["ja_registrada"] else 201, {**resultado, "estado": estado(store)}
        if len(rota) == 3 and rota[0] == "vendas" and metodo == "POST":
            if rota[2] == "estorno":
                return 200, {**vendas.estornar(store, _id(rota[1])), "estado": estado(store)}
            if rota[2] == "restaurar":
                return 200, {**vendas.restaurar(store, _id(rota[1])), "estado": estado(store)}
        if rota == ["despensa", "conferir"] and metodo == "POST":
            itens = [{"nome": item["nome"], "disponivel": item["disponivel_legivel"], "preco_pago": item["preco_total"]}
                     for item in store.ingredientes()]
            store.registrar_consulta_despensa()
            return 200, {"itens": itens, "estado": estado(store)}
        if rota == ["analytics", "analises"] and metodo == "GET":
            return 200, precificacao.consultar(store)
        if rota == ["analytics", "taxas"] and metodo == "PUT":
            store.salvar_perfil_precificacao(_objeto(corpo))
            return 200, precificacao.consultar(store)
        if rota == ["analytics"] and metodo == "GET":
            limite = int(_numero(consulta.get("limite", ["10"])[0], "limite"))
            r = analytics.consultar(store.negocio(), consulta.get("escopo", ["prioridade"])[0], limite,
                                    consulta.get("categoria", [None])[0])
            cozinha_mod.anotar_ranking(r["ranking"], store.perfil(), perfil_precificacao=store.perfil_precificacao())
            return 200, r
        if rota == ["precos"] and metodo == "GET":
            preco = consulta.get("preco", [""])[0]
            return 200, costing.quanto_cobrar(_numero(consulta.get("cmv", [""])[0], "custo do prato"),
                                              _numero(preco, "preço") if preco else None, perfil=store.perfil_precificacao())
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
        if rota == ["negocio"] and metodo == "PATCH":
            store.negocio_atualizar(_objeto(corpo), somar_alimentacao=False)
            return 200, estado(store)
        if rota == ["cozinha", "pratos"] and metodo == "GET":
            return 200, cozinha_mod.pratos_da_cozinha(store.perfil(), negocio_mod.categorias_da_loja(store.negocio()))
        if rota == ["cozinha"] and metodo == "PUT":
            store.cozinha_salvar(_objeto(corpo))
            return 200, estado(store)
        if rota == ["atividade"] and metodo == "GET":
            ids = [i.strip() for i in consulta.get("ids", [""])[0].split(",") if i.strip()]
            if ids:
                return 200, {"atividade": store.atividade_das_chamadas(ids)}
            return 200, {"recentes": store.atividade_recente(int(_numero(consulta.get("limite", ["30"])[0], "limite")))}
        if rota and rota[0] in {"saude", "estado", "jornada", "vendas", "despensa", "analytics", "precos", "carrinho", "salvos", "negocio", "cozinha", "atividade", "memoria"}:
            return 405, {"erro": "método não permitido"}
        return 404, {"erro": "rota desconhecida"}
    except OrcamentoEstourado as exc:
        return 409, {"erro": str(exc), "falta": exc.falta, "estado": estado()}
    except vendas.ConflitoOperacao as exc:
        return 409, {"erro": str(exc)}
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
        caminho = _segmentos(urlsplit(self.path).path)
        historico_local = caminho[:2] in (["api", "conversas"], ["api", "memoria"])
        if historico_local:
            host = self.headers.get("Host", "").lower()
            origem = self.headers.get("Origin")
            if host.split(":", 1)[0] not in ("localhost", "127.0.0.1") or (origem and origem != f"http://{host}"):
                self._responder(403, {"erro": "acesse o histórico pelo playground local"})
                return
        try:
            tamanho = int(self.headers.get("Content-Length") or 0)
            if tamanho < 0:
                raise ValueError()
        except ValueError:
            self._responder(400, {"erro": "tamanho da requisição inválido"})
            return
        if tamanho > (historico.MAX_CORPO if caminho[:2] == ["api", "conversas"] else MAX_CORPO):
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
    except Exception:  # noqa: BLE001 - /api/saude devolve 503 e tenta novamente
        logger.exception("não consegui preparar o banco da consultoria na inicialização")
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
