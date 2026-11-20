"""A pesquisa de receitas (item 2.1 do desafio) na ordem certa, para o modelo pequeno não pular etapa.

O Hermes já traz as ferramentas (``web_search`` com SearXNG e ``web_extract`` com o provider ``receitas-local`` deste plugin). O que o Qwen3-8B
faz sozinho, visto em teste real: uma busca e a resposta em cima dos trechos dos resultados, sem ler a página nem conferir a despensa; ou
"vou pesquisar na web, um momento" e o fim do turno, sem chamar nada; ou, perguntado de forma aberta ("o que eu consigo fazer?"), nem busca.
Dois empurrões, sem tocar no core:

* ``roteiro_do_turno``: quando a mensagem dela pede receita ou ideia de prato, o painel do turno traz o roteiro em ordem, e o turno fica marcado
  (``marcar_turno``, por sessão);
* ``depois_da_ferramenta`` (hook ``transform_tool_result``): nesse turno, o resultado da despensa, da busca na internet e da leitura da página
  leva o próximo passo escrito, no mesmo campo ``proximo_passo`` que as tools do plugin usam. Quem conduz é a ferramenta que acabou de
  responder, que é o que um modelo pequeno mais respeita.

Quem decide o que cozinhar continua sendo ela: o roteiro termina sempre na pergunta "você gosta de cozinhar isso? vê algum impedimento?".
"""

from __future__ import annotations

import json
import logging
import re
import threading
from html import unescape
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit

from . import negocio
from .units import norm_text

logger = logging.getLogger(__name__)

MAX_SESSOES = 16
MAX_JA_MOSTRADAS = 24

# Pede receita ou ideia do que cozinhar. "despensa" e "cardápio" sozinhos não entram: "quanto paguei?" e "mostra o cardápio" não são pesquisa.
PEDE_RECEITA = re.compile(
    r"\bmontar (?:o |um |meu )?cardapio|\bo que (?:eu )?(?:posso|consigo|da pra|vale a pena) (?:fazer|cozinhar)|"
    r"\bgosto de (?:fazer|cozinhar)|\bideias? de (?:prato|receita|comida|marmita)|\bmais (?:vendidos?|pedidos?)\b|\bvendem? bem\b|"
    r"\b(?:pesquis\w*|busc\w*|procur\w*|sug\w*|quer[oa]|encontr\w*|mostr\w*|mont[ae]|montar)\b.{0,85}\b(?:receitas?|sugestoes de pratos?)\b|"
    r"\bcomo (?:fazer|preparar|cozinhar)\b")
_BUSCA_DE_PRECO = re.compile(r"^(?:preco|quanto custa|cotacao|valor)\b")
_MONTAR_UM = re.compile(r"^(?:pesquise na internet receitas para este pedido )?(?:agora[, ]+)?"
                        r"(?:(?:pesquise e |vamos )?)(?:monte|monta|montar|crie|prepare|faca|quero montar) (?:mais )?(?:um|uma|1) (?:prato|receita|marmita)\b")
_MONTAGEM_NEGADA = re.compile(r"\bnao (?:monte|monta|crie|prepare|faca|quero montar)\b")
_PESQUISA_NEGADA = re.compile(r"^(?:pesquise na internet receitas para este pedido )?nao (?:pesquis\w*|busc\w*|procur\w*|quer[oa])\b")
_COLETANEA = re.compile(r"\b\d+\s+(?:ideias|receitas|pratos|opcoes|sugestoes)\b")

ROTEIRO = (
    "PESQUISA DE RECEITAS (ela pediu receita ou ideia de prato): siga esta ordem, uma ferramenta por vez, sem pular etapa e SEM anunciar o que vai fazer "
    "('vou pesquisar...'): chame a ferramenta. "
    "(1) despensa_listar: o que ela tem em quantidade. "
    "(2) web_search na web pública em português. Comece pelo pedido específico do turno ou pelo que ela quer começar a vender "
    "no objetivo salvo e pelos tipos de alimentação que selecionou. Use a despensa e a cozinha como filtros dentro desse tema. "
    "Para mais vendidos, use recortes de pedidos publicados. Coloque sugestões de outros tipos depois da prioridade. "
    "(3) web_extract nas 2 melhores páginas (as que trazem a receita completa), as duas na mesma chamada. "
    "(4) despensa_buscar com os ingredientes das receitas lidas, numa chamada só. "
    "(5) responda em TEXTO, sem usar o carrinho. Use títulos numerados '### 1. Nome do prato', uma linha 'Fonte: [título](URL)' em cada opção, "
    "e liste todos os ingredientes e o que precisa comprar ou conferir. Só diga 'tem tudo' quando a despensa confirmar todos os ingredientes e as quantidades. "
    "Mantenha o corte da fonte. Peito de frango e coxinha da asa exigem quantidades e preparo diferentes. Termine com uma única pergunta: "
    "'Qual opção você quer escolher? Pode escolher mais de uma ou pedir outras sugestões.' Aguarde a escolha antes de salvar ou montar fichas. "
    "O carrinho, a conferência dos ingredientes e o preço entram depois da escolha: "
    "não dê preço de ingrediente que falta (nem 'estimativa'), porque o preço vem depois, pesquisado ou dito por ela. "
    "Só afirme o que as ferramentas devolveram (receita que você não leu na página não se descreve) e não cite nomes de ferramentas para ela."
)

PASSO_APOS_DESPENSA = (
    "Próximo passo: chame web_search AGORA, sem avisar antes. Pesquise receitas reais em português na web pública a partir do "
    "pedido atual ou do objetivo e tipo de alimentação salvos. Acrescente ingredientes e métodos que façam sentido para esse foco."
)

PASSO_APOS_BUSCA = (
    "Agora leia as 2 melhores páginas com web_extract(urls=[...]), as duas na mesma chamada e de preferência as que trazem a receita completa "
    "(ingredientes e modo de preparo). Depois confira os ingredientes com despensa_buscar e só então apresente as candidatas em texto, sem usar o carrinho, "
    "e aguarde ela escolher uma opção antes de montar a ficha. Não descreva receita só pelo trecho do resultado da busca."
)

BUSCA_SEM_RESULTADO = ("Nenhuma página foi encontrada. Tente outra consulta sobre o mesmo tipo de receita. "
                       "Se não conseguir ler uma receita real, informe a limitação em vez de inventar fonte ou dizer que leu.")

PASSO_APOS_LEITURA = (
    "Agora chame despensa_buscar com os ingredientes das receitas lidas (uma chamada, todos de uma vez) e responda em TEXTO, sem usar o carrinho: para cada "
    "candidata, nome, fonte (link), o que ela já tem e o que falta comprar (sem preço: o preço vem depois, pesquisado ou dito por ela). "
    "Termine perguntando apenas qual opção ela quer escolher. Fale só do que está na página e do que a despensa confirmou."
)

PASSO_APOS_CONFERENCIA = (
    "Agora apresente as candidatas, sem usar o carrinho. Use '### 1. Nome do prato', 'Fonte: [título](URL)' e numere as opções na ordem. "
    "Já tem (nomes exatos dos itens confirmados); Precisa conferir ou comprar (nomes, sem valores em R$). Inclua todos os ingredientes lidos. "
    "Se o corte da receita não aparecer no estoque, mostre a falta e ofereça uma adaptação separada para ela decidir. "
    "Mostre os custos unitários do estoque confirmado, sem estimar o custo total da receita ou o preço de complementos. "
    "Termine com uma pergunta: 'Qual opção você quer escolher? Pode escolher mais de uma ou pedir outras sugestões.' "
    "Aguarde essa escolha; não salve rascunho, não monte ficha nem pergunte condições da cozinha ainda. "
    "Se ela pediu opções diferentes, busque outro prato em vez de apresentar duas páginas da mesma receita. "
    "Deixe as cotações para depois da escolha. Confira a versão final e peça o aceite antes de calcular preço."
)

ROTEIRO_MONTAR = (
    "MONTAR UM PRATO PARA O CARDÁPIO: ela delegou uma ficha. A pesquisa real na internet é parte do desafio, mesmo sem o globo ativado. "
    "Chame despensa_listar e depois web_search na web pública, priorizando o prato pedido ou o objetivo e tipo de alimentação salvos. "
    "Selecione ingredientes disponíveis e métodos da cozinha dentro dessa prioridade. "
    "Leia a página direta do prato com ingredientes, rendimento, preparo e tempo total. Um artigo com várias ideias é só uma referência para encontrar a página direta. "
    "Escolha uma receita, confira a disponibilidade e chame receita_conferir_ingredientes com o nome, a URL, as quantidades usadas, o rendimento, "
    "tempo_preparo_min e os requisitos do preparo. Prefira o tempo total publicado. Se faltar, use a resposta dela sobre a duração da receita ou estime a partir do preparo e do tamanho do lote, explicando a base. "
    "Após duas tentativas sobre a mesma pendência, resolva com os dados disponíveis ou confira uma alternativa que caiba no tempo e na cozinha confirmados. O aceite exige uma versão viável. "
    "Mostre a ficha salva, a fonte e os dados da conferência. Pergunte: Você gosta de cozinhar isso? Vê algum impedimento? "
    "Se algum item faltar, apresente uma adaptação viável e a diferença antes de pedir o aceite. "
    "Se precisar assumir um rendimento, proponha um lote de teste e diga quantas porções ele rende. Cadastro no cardápio e compras exigem confirmações próprias."
)

_PESQUISANDO: "OrderedDict[str, str]" = OrderedDict()
_FONTES_MONTAGEM: dict[str, tuple[str, str]] = {}
_FICHAS_LIDAS: dict[str, dict[str, dict[str, Any]]] = {}
_FALHAS_MONTAGEM: dict[str, int] = {}
_PROXIMA_FERRAMENTA: dict[str, str] = {}
_COTACOES: dict[str, tuple[int, str]] = {}
_ALVO_ESCOLHIDO: dict[str, dict[str, Any]] = {}
_CORRECOES_ESCOLHIDO: dict[str, int] = {}
_FALHAS_LEITURA: dict[str, int] = {}
_SUGESTOES_LIDAS: dict[str, list[dict[str, Any]]] = {}
# Receitas que a conversa já mostrou como opção. Uma busca nova ("não gostei, procure outra") só vale com receitas diferentes.
_JA_MOSTRADAS: dict[str, list[dict[str, str]]] = {}
_FOCOS: dict[str, "FocoDaBusca"] = {}
_LOCK = threading.Lock()


@dataclass(frozen=True)
class FocoDaBusca:
    assunto: str
    categorias: tuple[str, ...]
    explicito: bool

    def instrucao(self) -> str:
        origem = "Prioridade desta busca" if self.explicito else "Tipos de alimentação do cadastro"
        return f"{origem}: {self.assunto}. Use isso na consulta; aproveite os ingredientes e a cozinha dentro desse foco."

    def combina(self, titulo: Any, ingredientes: list[Any] | None = None) -> bool:
        if self.categorias:
            return bool(set(negocio.categorias_mencionadas(titulo)) & set(self.categorias))
        # Objetivos de texto livre podem citar um prato fora do catálogo.
        termos = set(re.findall(r"\b\w+\b", norm_text(str(titulo or ""))))
        termos.update(re.findall(r"\b\w+\b", norm_text(" ".join(str(i) for i in ingredientes or []))))
        eixo = next(iter(re.findall(r"\b\w{3,}\b", norm_text(self.assunto))), "")
        return bool(eixo and eixo in termos)


_CATEGORIAS_AMPLAS = {"caseira", "marmita", "saudavel"}
_OBJETIVO_DE_CARDAPIO = re.compile(
    r"\b(?:come[cç]ar\s+(?:vendendo|a\s+vender|com)|vend[eo]r?|oferecer|servir|preparar|fazer|[ée]\s+vender)\s+(.+)", re.I)
_PEDIDO_DE_CARDAPIO = re.compile(
    r"\b(?:receitas?|ideias?|sugest[oõ]es(?:\s+de\s+pratos?)?)\s+(?:simples\s+)?(?:de|para)\s+"
    r"(?!pratos?\b|receitas?\b|comida\b|o\s+meu\b|meu\b|minha\b|mim\b|este\b|esse\b|o\s+delivery\b|acordo\b|vender\b|come[cç]ar\b)(.+)", re.I)
_DOMINIO_NA_CONSULTA = re.compile(r"\b(?:site|inurl):\s*[^\s]+", re.I)
_PEDIDO_POR_CATEGORIA = re.compile(r"\b(?:pesquis\w*|sug\w*|busc\w*|procur\w*|mostr\w*)\b")


def _assunto(texto: str) -> str:
    trecho = re.split(r"[.!?;\n]|\b(?:usando|aproveitando|depois|mais tarde|para vender|com o que)\b", texto, maxsplit=1, flags=re.I)[0]
    trecho = re.sub(r"^(?:um|uma|uns|umas)\s+", "", trecho.strip(), flags=re.I)
    trecho = re.sub(r"\s+(?:e|ou)$", "", trecho.strip(" ,:"), flags=re.I)
    return re.sub(r"\s+", " ", trecho)[:100]


def foco_da_busca(perfil: dict[str, Any], mensagem: Any) -> FocoDaBusca | None:
    """Pedido específico, objetivo de lançamento e tipos cadastrados, nessa ordem."""
    atual = str(mensagem or "")
    atual = re.sub(r"^pesquise na internet receitas para este pedido\s*[:.\-]?\s*", "", atual, flags=re.I)
    objetivo = str(perfil.get("objetivo") or "")
    for texto, padrao in ((atual, _PEDIDO_DE_CARDAPIO), (objetivo, _OBJETIVO_DE_CARDAPIO)):
        for frase in re.split(r"[.!?;\n]+|,\s*(?=(?:quero|pretendo|prefiro|n[aã]o)\b)", texto, flags=re.I):
            citado = padrao.search(frase)
            if citado and re.search(r"\b(?:nao|nem|sem|evite)\b", norm_text(frase[:citado.start()])):
                continue
            assunto = _assunto(citado[1]) if citado else ""
            if norm_text(assunto) in {"mais", "melhor", "bem", "comida", "mais comida", "para mais pessoas"}:
                assunto = ""
            categorias = negocio.categorias_mencionadas(assunto or frase)
            if assunto or categorias:
                if not assunto:
                    assunto = ", ".join(negocio.ALIMENTACAO[k][0].lower() for k in categorias)
                elif categorias and not re.search(r"\b(?:e|ou)\b", norm_text(assunto)):
                    categorias = categorias[:1]
                return FocoDaBusca(assunto, tuple(categorias), True)
    cadastradas = tuple(k for k in perfil.get("alimentacao") or [] if k in negocio.ALIMENTACAO)
    if cadastradas:
        return FocoDaBusca(", ".join(negocio.ALIMENTACAO[k][0].lower() for k in cadastradas), cadastradas, False)
    return None


def ajustar_consulta(tool_name: str = "", args: dict[str, Any] | None = None,
                     session_id: str = "", **_: Any) -> dict[str, Any] | None:
    """Mantém a consulta livre de domínios sugeridos pelo modelo e ancorada no tema do turno."""
    if tool_name != "web_search" or not isinstance(args, dict) or not isinstance(args.get("query"), str):
        return None
    with _LOCK:
        modo = _PESQUISANDO.get(session_id)
        foco = _FOCOS.get(session_id)
        cotacao = _COTACOES.get(session_id)
    consulta = args["query"]
    if cotacao:
        from .montagem import nome_da_medida
        from .store import Store

        perfil = Store().negocio()
        local = " ".join(lugar for lugar in (perfil.get("cidade"), perfil.get("estado")) if lugar)
        consulta = f"preço de {nome_da_medida(cotacao[1])} embalagem {local}".strip()
        return {"action": "modify", "args": {**args, "query": consulta}}
    if modo not in ("sugerir", "montar", "pesquisar") or _BUSCA_DE_PRECO.search(norm_text(consulta)):
        return None
    aberta = " ".join(_DOMINIO_NA_CONSULTA.sub("", consulta).split())
    if foco and not foco.combina(aberta):
        if foco.explicito or negocio.categorias_mencionadas(aberta) or not set(foco.categorias) & _CATEGORIAS_AMPLAS:
            aberta = f"receita de {foco.assunto}"
        else:
            suplemento = negocio.ALIMENTACAO[foco.categorias[0]][0].lower()
            aberta = f"{aberta} {suplemento}".strip()
    return {"action": "modify", "args": {"query": aberta or "receita com ingredientes disponíveis"}} if aberta != consulta else None


def _chave_url(url: Any) -> str:
    """A mesma página com ou sem ``www.``, protocolo ou barra final."""
    if not isinstance(url, str) or not url.strip():
        return ""
    partes = urlsplit(url.strip())
    return (partes.netloc.lower().removeprefix("www.") + partes.path.rstrip("/") + (f"?{partes.query}" if partes.query else ""))


def _repetida(vistas: list[dict[str, str]], nome: Any, url: Any) -> bool:
    chave, nome_normal = _chave_url(url), norm_text(str(nome or ""))
    return any((chave and vista["url"] == chave) or (nome_normal and vista["nome_normal"] == nome_normal) for vista in vistas)


def _aviso_das_vistas(vistas: list[dict[str, str]]) -> str:
    if not vistas:
        return ""
    nomes = "; ".join(dict.fromkeys(vista["nome"] for vista in vistas))
    return (f" Ela já viu nesta conversa e quer opções diferentes de: {nomes}. Busque OUTRO prato, com outro preparo: "
            "não repita esses nomes na consulta nem abra de novo as mesmas páginas.")


def pediu_montar_um(mensagem: Any) -> bool:
    if not isinstance(mensagem, str):
        return False
    texto = norm_text(mensagem)
    return bool(_MONTAR_UM.search(texto) and not _MONTAGEM_NEGADA.search(texto) and "equivalente" not in texto)


def montagem_integral(mensagem: Any) -> bool:
    """Só delegações puras autorizam copiar a receita publicada integralmente.

    Uma frase parecida com um pedido de montagem pode conter alterações de
    ingredientes, rendimento ou preparo. A lista fechada não apaga essas escolhas.
    """
    if not isinstance(mensagem, str) or "?" in mensagem:
        return False
    texto = norm_text(mensagem).strip(" .!?")
    canonica = norm_text("Quero sua ajuda. Pesquise uma receita real para o meu negócio com o estoque disponível "
                         "e monte uma proposta, sem aprovar por mim.").strip(" .!?")
    simples = re.fullmatch(r"(?:monte|monta|quero montar) (?:um prato|uma receita)(?: para (?:mim|(?:o )?meu cardapio))?", texto)
    return bool(texto == canonica or simples)


def pede_receita(mensagem: Any) -> bool:
    if not isinstance(mensagem, str):
        return False
    texto = norm_text(mensagem)
    return not _PESQUISA_NEGADA.match(texto) and bool(
        pediu_montar_um(mensagem) or PEDE_RECEITA.search(texto)
        or _PEDIDO_POR_CATEGORIA.search(texto) and negocio.categorias_mencionadas(mensagem))


def roteiro_do_turno(mensagem: Any) -> Optional[str]:
    """O roteiro da pesquisa, quando a mensagem dela pede receita ou ideia; senão nada (o painel não vira lista de ordens)."""
    if pediu_montar_um(mensagem):
        return ROTEIRO_MONTAR
    return ROTEIRO if pede_receita(mensagem) else None


def marcar_turno(sessao: str, mensagem: Any, *, conduzida: bool = False, somente_sugestoes: bool = False,
                 ja_mostradas: Optional[list[dict[str, Any]]] = None, foco: FocoDaBusca | None = None,
                 cotacao: tuple[int, str] | None = None, cotacao_informada: bool = False,
                 planejar_divulgacao: bool = False) -> None:
    """Guarda se o turno desta sessão é de pesquisa de receitas, para as ferramentas do turno saberem (o hook delas não vê a mensagem).

    ``ja_mostradas`` são as opções que a conversa já apresentou (nome e fonte). Num turno só de sugestões elas saem da busca e
    da leitura: pedir outra busca e receber a mesma receita deixaria a pessoa andando em círculo.
    """
    if not sessao:
        return
    vistas: list[dict[str, str]] = []
    for opcao in (ja_mostradas or []) if somente_sugestoes else []:
        nome = str(opcao.get("nome") or "").strip() if isinstance(opcao, dict) else ""
        if not nome:
            continue
        vista = {"nome": nome, "nome_normal": norm_text(nome), "url": _chave_url(opcao.get("fonte_url"))}
        if vista not in vistas:
            vistas.append(vista)
    with _LOCK:
        _FONTES_MONTAGEM.pop(sessao, None)
        _FALHAS_MONTAGEM.pop(sessao, None)
        _PROXIMA_FERRAMENTA.pop(sessao, None)
        _COTACOES.pop(sessao, None)
        _ALVO_ESCOLHIDO.pop(sessao, None)
        _CORRECOES_ESCOLHIDO.pop(sessao, None)
        _FALHAS_LEITURA.pop(sessao, None)
        _SUGESTOES_LIDAS.pop(sessao, None)
        _JA_MOSTRADAS.pop(sessao, None)
        _FOCOS.pop(sessao, None)
        if vistas:
            _JA_MOSTRADAS[sessao] = vistas[-MAX_JA_MOSTRADAS:]
        _PESQUISANDO[sessao] = ("" if cotacao or planejar_divulgacao else "sugerir" if somente_sugestoes else
                               "montar" if pediu_montar_um(mensagem) else "pesquisar" if pede_receita(mensagem) else "")
        if foco and _PESQUISANDO[sessao]:
            _FOCOS[sessao] = foco
        if conduzida and _PESQUISANDO[sessao]:
            _PROXIMA_FERRAMENTA[sessao] = "despensa_listar"
        if cotacao:
            _COTACOES[sessao] = cotacao
            _PROXIMA_FERRAMENTA[sessao] = "receita_conferir_ingredientes" if cotacao_informada else "web_search"
        if planejar_divulgacao:
            _PROXIMA_FERRAMENTA[sessao] = "divulgacao_planejar"
        _PESQUISANDO.move_to_end(sessao)
        while len(_PESQUISANDO) > MAX_SESSOES:
            antiga, _ = _PESQUISANDO.popitem(last=False)
            _FONTES_MONTAGEM.pop(antiga, None)
            _FALHAS_MONTAGEM.pop(antiga, None)
            _FICHAS_LIDAS.pop(antiga, None)
            _PROXIMA_FERRAMENTA.pop(antiga, None)
            _COTACOES.pop(antiga, None)
            _ALVO_ESCOLHIDO.pop(antiga, None)
            _CORRECOES_ESCOLHIDO.pop(antiga, None)
            _FALHAS_LEITURA.pop(antiga, None)
            _SUGESTOES_LIDAS.pop(antiga, None)
            _JA_MOSTRADAS.pop(antiga, None)
            _FOCOS.pop(antiga, None)


def conduzir_escolhido(sessao: str, prato: dict[str, Any], *, receita: dict[str, Any] | None = None,
                      revisar_fonte: bool = False) -> bool:
    """A pessoa já escolheu: execute só a leitura e a montagem desse rascunho.

    Nenhuma ferramenta de aceite, preço, compras ou reservas é forçada. O alvo
    não é uma autorização para substituir a opção por outra receita.
    """
    if not sessao or not prato or prato.get("status") != "selecionado":
        return False
    snapshot = None
    if prato.get("receita_id"):
        from .fonte import normalizar_adaptacao

        if not revisar_fonte or not receita or receita.get("id") != prato["receita_id"]:
            return False
        dados = receita.get("dados") or {}
        fidelidade = dados.get("fidelidade_fonte") or {}
        snapshot = dados.get("fonte_verificada")
        bloqueios = (dados.get("bloqueios") or []) + (fidelidade.get("bloqueios") or [])
        url = prato.get("fonte_url") or (prato.get("dados", {}).get("selecao") or {}).get("fonte_url")
        if (not isinstance(snapshot, dict) or not snapshot.get("ingredientes") or not snapshot.get("modo_preparo")
                or not url or snapshot.get("url") != url or receita.get("fonte_url") != url
                or any(normalizar_adaptacao(v) for v in (dados.get("adaptacao"), fidelidade.get("adaptacao")))
                or dados.get("preparo_da_adaptacao")
                or not any(b.get("tipo") in ("fonte_incompleta", "fonte_divergente") for b in bloqueios)):
            return False
    with _LOCK:
        _PESQUISANDO[sessao] = "selecionado"
        _ALVO_ESCOLHIDO[sessao] = {"id": prato["id"], "nome": prato["nome"], "fonte_url": prato.get("fonte_url")}
        if snapshot:
            _ALVO_ESCOLHIDO[sessao].update({"revisao_fonte": True, "receita_id": receita["id"], "fonte_url": snapshot["url"]})
            _FICHAS_LIDAS.setdefault(sessao, {})[snapshot["url"]] = deepcopy(snapshot)
            _PROXIMA_FERRAMENTA[sessao] = "receita_conferir_ingredientes"
        else:
            url = prato.get("fonte_url")
            lida = (_FICHAS_LIDAS.get(sessao) or {}).get(url or "")
            fonte_pronta = bool(lida and lida.get("url") == url and lida.get("ingredientes") and lida.get("modo_preparo"))
            _PROXIMA_FERRAMENTA[sessao] = ("receita_conferir_ingredientes" if fonte_pronta else
                                          "web_extract" if url else "web_search")
    return True


def conduzir_adaptacao(sessao: str, prato: dict[str, Any], receita: dict[str, Any]) -> bool:
    """Garante consulta e gravação delegadas; composição e preparo continuam com a LLM."""
    if not sessao or prato.get("status") != "selecionado" or receita.get("id") != prato.get("receita_id"):
        return False
    with _LOCK:
        _PESQUISANDO[sessao] = "selecionado"
        _ALVO_ESCOLHIDO[sessao] = {"id": prato["id"], "nome": prato["nome"], "receita_id": receita["id"],
                                  "fonte_url": receita.get("fonte_url"), "adaptacao_delegada": True}
        snapshot = receita.get("dados", {}).get("fonte_verificada")
        if snapshot and snapshot.get("url"):
            _FICHAS_LIDAS.setdefault(sessao, {})[snapshot["url"]] = deepcopy(snapshot)
        _PROXIMA_FERRAMENTA[sessao] = "despensa_listar"
    return True


def ferramenta_da_conducao(sessao: str) -> str | None:
    """Etapa delegada de leitura/conferência; nunca aponta ferramentas de decisão."""
    with _LOCK:
        return _PROXIMA_FERRAMENTA.get(sessao)


def iniciar_analise_divulgacao(sessao: str) -> bool:
    """Consome uma única chamada de análise padrão, sem repetir nesta rodada."""
    with _LOCK:
        if _PROXIMA_FERRAMENTA.get(sessao) != "divulgacao_planejar":
            return False
        _PROXIMA_FERRAMENTA.pop(sessao)
        return True


def alvo_da_cotacao(sessao: str) -> tuple[int, str] | None:
    with _LOCK:
        return _COTACOES.get(sessao)


def alvo_escolhido(sessao: str) -> dict[str, Any] | None:
    """Cópia do rascunho escolhido para este turno; nunca infere outro alvo."""
    with _LOCK:
        alvo = _ALVO_ESCOLHIDO.get(sessao)
        return dict(alvo) if alvo else None


def pesquisando(sessao: str) -> bool:
    with _LOCK:
        return bool(_PESQUISANDO.get(sessao))


def somente_sugestoes(sessao: str) -> bool:
    """A escolha da receita continua pendente neste turno; leituras não a decidem."""
    with _LOCK:
        return _PESQUISANDO.get(sessao) == "sugerir"


def montando(sessao: str) -> bool:
    with _LOCK:
        return _PESQUISANDO.get(sessao) == "montar"


def fonte_da_montagem(sessao: str) -> tuple[str, str] | None:
    with _LOCK:
        return _FONTES_MONTAGEM.get(sessao)


def ficha_lida(sessao: str, url: str | None) -> dict[str, Any] | None:
    with _LOCK:
        return (_FICHAS_LIDAS.get(sessao) or {}).get(url or "")


def esquecer_tudo() -> None:
    with _LOCK:
        _PESQUISANDO.clear()
        _FONTES_MONTAGEM.clear()
        _FALHAS_MONTAGEM.clear()
        _FICHAS_LIDAS.clear()
        _PROXIMA_FERRAMENTA.clear()
        _COTACOES.clear()
        _ALVO_ESCOLHIDO.clear()
        _CORRECOES_ESCOLHIDO.clear()
        _FALHAS_LEITURA.clear()
        _SUGESTOES_LIDAS.clear()
        _JA_MOSTRADAS.clear()
        _FOCOS.clear()


def _receita_direta(paginas: list[Any]) -> tuple[str, str] | None:
    from .fonte import contrato_pagina

    for pagina in paginas:
        if not isinstance(pagina, dict):
            continue
        contrato = contrato_pagina(pagina)
        if not contrato:
            continue
        nome = str(contrato.get("nome") or "").strip()
        url = contrato.get("url")
        metadata = pagina.get("metadata")
        if not (nome and isinstance(url, str) and url.startswith(("https://", "http://"))
                and contrato.get("ingredientes") and str(contrato.get("modo_preparo") or "").strip()):
            continue
        if isinstance(metadata, dict) and metadata.get("recipe_schema") is False and metadata.get("recipe_textual") is not True:
            continue
        if not _COLETANEA.search(norm_text(nome)) and not _COLETANEA.search(norm_text(pagina.get("title") or "")):
            return nome, url
    return None


def _montagem_local(sessao: str) -> str:
    # Uma coletânea não encerra a pesquisa: procure a página direta antes de
    # recorrer a uma adaptação local, identificada como tal para a usuária.
    from . import falas, montagem
    from .store import Store

    with _LOCK:
        falhas = _FALHAS_MONTAGEM.get(sessao, 0) + 1
        _FALHAS_MONTAGEM[sessao] = falhas
    if falhas == 1:
        return ("Ainda não foi possível ler uma receita real completa. Refine agora a consulta com web_search e abra com web_extract "
                "uma página direta da receita com ingredientes e preparo. Não use a coletânea como fonte de uma ficha.")
    proposta = montagem.propor_turno(Store(), falas.da_sessao(sessao) or [], sessao, permitir_adaptacao_local=True)
    marcar_turno(sessao, None)
    if proposta:
        return ("Não foi possível ler uma receita real completa após refinar a pesquisa. Explique essa limitação e apresente "
                "a proposta local abaixo, já conferida no banco, como adaptação da despensa. Ela não é uma receita web verificada.\n" + proposta)
    return ("Não foi possível ler uma receita real completa após refinar a pesquisa. Informe essa limitação sem inventar fonte. "
            "Consulte despensa_buscar e proponha uma adaptação local identificada como tal, com nome, medidas, rendimento e requisitos; "
            "confira com receita_conferir_ingredientes antes de pedir feedback e aceite. Se faltar algum dado, pergunte só por ele.")


def _busca_de_receita(args: Any) -> bool:
    consulta = args.get("query") if isinstance(args, dict) else None
    return isinstance(consulta, str) and "receita" in norm_text(consulta)


def _apresentar_sugestoes(sessao: str, dados: dict[str, Any]) -> str | None:
    """Só receitas verificadas neste turno podem virar opções selecionáveis."""
    from . import montagem
    from .fonte import ingredientes_publicados

    with _LOCK:
        contratos = deepcopy(_SUGESTOES_LIDAS.get(sessao) or [])
        outra_busca = bool(_JA_MOSTRADAS.get(sessao))
        foco = _FOCOS.get(sessao)
    if not contratos:
        return None
    def texto(valor: Any) -> str:
        return " ".join(re.sub(r"<[^>]+>", " ", unescape(str(valor))).split())
    linhas = [("Procurei opções diferentes das que você já viu. " if outra_busca else "")
              + "Estas são as receitas que consegui ler e verificar. Você escolhe antes de montar a ficha."]
    separa_alternativas = bool(foco and not (set(foco.categorias) & _CATEGORIAS_AMPLAS))
    if separa_alternativas:
        contratos.sort(key=lambda c: not foco.combina(c["nome"], c.get("ingredientes")))
    rotulou_alternativas = False
    for numero, contrato in enumerate(contratos[:3], 1):
        nome, url = texto(contrato["nome"]), contrato["url"]
        if separa_alternativas and not rotulou_alternativas and not foco.combina(nome, contrato.get("ingredientes")):
            linhas.append("**Ideias secundárias para considerar depois.**")
            rotulou_alternativas = True
        ingredientes = [texto(i) for i in contrato["ingredientes"]]
        amostra = "; ".join(ingredientes[:6])
        if len(ingredientes) > 6:
            amostra += f"; e mais {len(ingredientes) - 6} itens da receita"
        bloco = [f"### {numero}. {nome}", f"Fonte: [{nome}]({url})", "Ingredientes publicados: " + amostra + "."]
        duracao = contrato.get("tempo_preparo_min")
        if type(duracao) in (int, float) and duracao > 0:
            bloco.append(f"Tempo informado na fonte: {duracao:g} min.")
        else:
            bloco.append("Tempo total a conferir pelas etapas do preparo.")
        nomes = {norm_text(i.get("nome") or "") for i in ingredientes_publicados(contrato)}
        custos = []
        for resultado in dados.get("resultados") or []:
            if not isinstance(resultado, dict) or not resultado.get("encontrado"):
                continue
            exatos = [c for c in resultado.get("candidatos") or [] if isinstance(c, dict) and c.get("confiavel")
                      and norm_text(c.get("nome") or "") in nomes
                      and norm_text(c.get("nome") or "") == norm_text(resultado.get("procurado") or "")]
            if len(exatos) == 1 and exatos[0].get("custo_unitario_legivel"):
                custos.append(f"{texto(exatos[0]['nome'])}: {texto(exatos[0]['custo_unitario_legivel'])}")
        if custos:
            bloco.append("Referências de custo da despensa, por unidade: " + "; ".join(dict.fromkeys(custos)) + ".")
        bloco.append("Precisa conferir: as medidas, a disponibilidade para este lote e o custo completo, depois da sua escolha.")
        linhas.append("\n".join(bloco))
    linhas.append("Qual opção você quer escolher? Pode escolher mais de uma ou pedir outras sugestões.")
    montagem.registrar_resposta(sessao, linhas, substituir=True, prioridade=20)
    return "\n\n".join(linhas)


def _falha_de_leitura(sessao: str, dados: dict[str, Any], alvo: dict[str, Any] | None, *, repetidas: bool = False) -> str:
    """Uma segunda tentativa é o limite; uma escolha com URL não muda de fonte."""
    with _LOCK:
        tentativas = _FALHAS_LEITURA.get(sessao, 0) + 1
        _FALHAS_LEITURA[sessao] = tentativas
        outra_busca = bool(_JA_MOSTRADAS.get(sessao))
        foco = _FOCOS.get(sessao) if not alvo else None
        if tentativas < 2:
            _PROXIMA_FERRAMENTA[sessao] = "web_extract" if alvo and alvo.get("fonte_url") else "web_search"
        else:
            _PROXIMA_FERRAMENTA.pop(sessao, None)
    if tentativas < 2:
        passo = (f"A leitura não trouxe uma receita completa verificável. Tente web_extract uma última vez SOMENTE na fonte "
                 f"já escolhida {alvo['fonte_url']}; não busque nem substitua o prato."
                 if alvo and alvo.get("fonte_url") else
                 "Só apareceram receitas que ela já viu nesta conversa. Refine a busca uma última vez por OUTRO prato, numa página direta "
                 "com ingredientes e preparo. Não reapresente as receitas anteriores nem monte uma ficha."
                 if repetidas else
                 "A leitura não trouxe uma receita completa verificável. Refine a busca uma última vez por uma página direta com ingredientes e preparo. "
                 "Não apresente páginas descartadas como opções nem monte uma ficha.")
        if foco:
            passo += " " + foco.instrucao()
    else:
        from . import montagem

        texto = (f"Não consegui ler a receita completa de **{alvo['nome']}** na fonte escolhida. "
                 "A opção continua pendente; não substituí a receita nem montei uma ficha com dados incompletos. "
                 "Você pode enviar outro link com a receita completa?" if alvo else
                 "Não encontrei outra receita completa, diferente das que você já viu, depois de duas tentativas. "
                 "Não montei fichas nem fiz compras. Você quer tentar outra busca ou enviar um link de receita completa?"
                 if outra_busca else
                 "Não consegui ler uma receita real completa após duas tentativas. Não montei fichas nem fiz compras. "
                 "Você quer tentar outra busca ou enviar um link de receita completa?")
        if foco and foco.explicito:
            texto = (f"Busquei receitas de **{foco.assunto}** em duas consultas. Ainda não consegui verificar uma ficha completa para esse foco. "
                     "Quer refinar a busca dentro dessa prioridade ou me enviar uma receita para conferir?")
        montagem.registrar_resposta(sessao, [texto], substituir=True, prioridade=20)
        passo = "Encerre este turno e explique a limitação: " + texto + " Não faça outra busca ou leitura neste turno."
    return json.dumps({**dados, "proximo_passo": passo}, ensure_ascii=False)


def pergunta_do_preco(ingrediente: str) -> str:
    from .montagem import nome_da_medida

    return f"Quanto custa {nome_da_medida(ingrediente)} e qual é a quantidade da embalagem?"


def _passo_apos_preco(sessao: str, *, sem_resultados: bool = False) -> Optional[str]:
    """Cotado o ingrediente, o passo é reconferir a ficha que espera esse preço.

    Sem isso o modelo ficava por conta própria depois da busca: tentou gravar a embalagem na despensa (de um item que ela
    nem comprou) e repetiu para ela a recusa da ferramenta. Sem ficha esperando preço, a busca fica como está.
    """
    from . import jornada
    from .store import Store, default_db_path

    if not default_db_path().exists():  # abrir o Store cria o banco: sem consultoria em andamento não há ficha a retomar
        return None
    store = Store()
    cotacao = alvo_da_cotacao(sessao)
    prato = store.obter_prato(cotacao[0]) if cotacao else jornada.ficha_para_retomar(store)
    receita = store.obter_receita(prato["receita_id"]) if prato and prato.get("receita_id") else None
    if cotacao and prato and receita:
        pergunta = pergunta_do_preco(cotacao[1])
        instrucao = "Se a busca não trouxe preço e quantidade verificáveis, pergunte apenas: " + pergunta
        if sem_resultados:
            from . import montagem

            montagem.registrar_resposta(sessao, [f"Mantive {cotacao[1]} na ficha do prato #{prato['id']}, **{prato['nome']}**.",
                                                 "A busca não trouxe uma cotação que eu pudesse usar.", pergunta],
                                         substituir=True, prioridade=20)
            return instrucao
        return (f"Use o preço e o tamanho da embalagem que a busca mostrar para reconferir o prato #{prato['id']}. "
                f"Chame receita_conferir_ingredientes(prato_id={prato['id']}) enviando apenas {cotacao[1]} em ingredientes "
                "com preco_compra_estimado, quantidade_compra e unidade_compra. Mostre a estimativa sem executar compra ou aceite. "
                + instrucao)
    itens = [str(i.get("ingrediente")) for i in (((receita or {}).get("dados") or {}).get("resultado") or {}).get("itens") or []
             if isinstance(i, dict) and i.get("falta_preco")]
    if not itens:
        return None
    # Na ficha copiada da fonte basta enviar o ingrediente cotado: os outros são mantidos pela própria conferência.
    quais = ("só com o ingrediente que você cotou (nome como está na ficha) e seus preco_compra_estimado, quantidade_compra e "
             "unidade_compra; os outros ingredientes da ficha são mantidos" if receita["dados"].get("ficha_original_da_fonte") else
             "com todos os ingredientes da ficha e, no que você cotou, preco_compra_estimado, quantidade_compra e unidade_compra")
    return (f"Com o preço e o tamanho da embalagem que a busca mostrou, reconfira a ficha do prato #{prato['id']}, {prato['nome']}: chame "
            f"receita_conferir_ingredientes(prato_id={prato['id']}) {quais}. Ainda sem preço: {'; '.join(itens[:4])}. "
            "Se a busca não deixou claro o preço ou a embalagem, pergunte a ela quanto paga e em que quantidade compra, numa pergunta só. "
            "Nada foi comprado: não registre compra nem altere a despensa.")


def _passo(ferramenta: str, args: Any, dados: dict[str, Any], sessao: str) -> Optional[str]:
    em_montagem = montando(sessao)
    if ferramenta == "web_search" and alvo_da_cotacao(sessao):
        return _passo_apos_preco(sessao, sem_resultados=not (dados.get("data") or {}).get("web"))
    pagina_util = ferramenta == "web_extract" and _receita_direta(dados.get("results") or []) is not None
    if (dados.get("error") or dados.get("success") is False) and not pagina_util:
        if em_montagem and ferramenta in ("web_search", "web_extract"):
            return _montagem_local(sessao)
        return None
    if ferramenta == "web_search" and isinstance(args, dict) and _BUSCA_DE_PRECO.search(norm_text(str(args.get("query") or ""))):
        return _passo_apos_preco(sessao)
    em_pesquisa = pesquisando(sessao)
    with _LOCK:
        alvo = _ALVO_ESCOLHIDO.get(sessao)
        vistas = list(_JA_MOSTRADAS.get(sessao) or [])
        foco = _FOCOS.get(sessao)
    if alvo and alvo.get("adaptacao_delegada"):
        from .montagem import INSTRUCAO_ADAPTACAO

        if ferramenta == "receita_conferir_ingredientes" and dados.get("success"):
            return ("Apresente a versão local já salva, as trocas, as omissões e as medidas estimadas. "
                    "Reúna as condições reais da cozinha que o resultado ainda pede. Se a ficha estiver viável, peça o aceite da versão.")
        return (f"A adaptação do prato #{alvo['id']}, {alvo['nome']}, já foi delegada. "
                "Agora chame receita_conferir_ingredientes com a lista completa dos insumos usados e o preparo próprio. "
                "Não peça autorização novamente nem calcule valores de cabeça. " + INSTRUCAO_ADAPTACAO)
    if alvo and ferramenta == "receita_conferir_ingredientes" and any(
            b.get("tipo") in ("fonte_incompleta", "fonte_divergente") for b in dados.get("bloqueios") or []):
        return (f"A ficha do prato #{alvo['id']} ainda omitiu ou alterou dados da página que você acabou de ler. "
                "Corrija você mesma os argumentos e reconfira o MESMO prato, sem perguntar à pessoa por ingredientes já publicados. "
                "Inclua todas as linhas da fonte, inclusive acompanhamentos. Em nome use só o ingrediente; quantidade e unidade ficam nos próprios campos. "
                "Não invente confirmacoes_operacionais nem altere rendimento ou medidas sem uma adaptação explícita.")
    if alvo and ferramenta == "web_search":
        return (f"A pessoa escolheu o prato #{alvo['id']}, {alvo['nome']}. Leia agora a página direta desse prato com web_extract. "
                "Não volte à lista de opções nem substitua o prato. Se a fonte não puder ser verificada, mantenha o rascunho e explique essa pendência.")
    if alvo and ferramenta == "web_extract" and pagina_util:
        return (f"Monte AGORA a ficha separada do prato #{alvo['id']}, {alvo['nome']}, com receita_conferir_ingredientes(prato_id={alvo['id']}). "
                "Use o nome e a fonte desse rascunho, os ingredientes, quantidades e rendimento da página lida. "
                "Cozinha ainda não respondida não impede guardar rascunho: deixe a ficha pendente e pergunte só o primeiro requisito relevante. "
                "Não envie confirmacoes_operacionais genéricas ou inventadas. Não aprove, não registre preço e não compre por ela.")
    if somente_sugestoes(sessao) and ferramenta == "web_extract" and pagina_util:
        return PASSO_APOS_LEITURA
    if ferramenta == "despensa_listar" and em_pesquisa:
        return PASSO_APOS_DESPENSA + (" " + foco.instrucao() if foco else "") + _aviso_das_vistas(vistas)
    if ferramenta == "despensa_buscar" and em_pesquisa:
        if em_montagem:
            return ("Confira um único prato com receita_conferir_ingredientes. Use a página direta e as quantidades da ficha. "
                    "Mostre os itens usados, o rendimento e as pendências que a conferência devolveu.")
        return PASSO_APOS_CONFERENCIA
    if ferramenta == "web_search" and (em_pesquisa or _busca_de_receita(args)):
        consulta = args.get("query", "") if isinstance(args, dict) else ""
        if _BUSCA_DE_PRECO.search(norm_text(consulta)):
            return None
        if em_montagem:
            if not (dados.get("data") or {}).get("web"):
                return _montagem_local(sessao)
            return ("Abra com web_extract a página direta da receita mais compatível com a despensa. "
                    "Priorize um link de receita que tenha ingredientes e preparo completos. Pule listas e páginas de categoria.")
        return PASSO_APOS_BUSCA if (dados.get("data") or {}).get("web") else BUSCA_SEM_RESULTADO
    if ferramenta == "web_extract" and em_montagem:
        fonte = _receita_direta(dados.get("results") or [])
        if fonte is None:
            return _montagem_local(sessao)
        with _LOCK:
            _FONTES_MONTAGEM[sessao] = fonte
        return (f"Use a ficha de {fonte[0]} ({fonte[1]}). Confira com receita_conferir_ingredientes usando as quantidades, o rendimento e o tempo total publicado, quando houver. "
                "A ficha será vinculada a um rascunho. Mostre o que ficou confirmado e pergunte: Você gosta de cozinhar isso? Vê algum impedimento? "
                "Só depois peça o aceite dessa versão.")
    if ferramenta == "web_extract" and any("## Ingredientes" in str(p.get("content") or "") for p in dados.get("results") or [] if isinstance(p, dict)):
        return PASSO_APOS_LEITURA
    return None


def _conferencia_para_conversa(dados: dict[str, Any]) -> dict[str, Any]:
    """Mantém o que orienta a próxima decisão. O SQLite guarda a conferência completa."""
    campos_item = ("ingrediente", "quantidade_receita", "item_despensa", "usa_da_despensa", "falta", "pendencia",
                   "como_resolver", "falta_preco", "compra", "custo_uso_total")
    compacta = {k: v for k, v in dados.items() if k not in {"perfil_pendente", "fidelidade_fonte", "avisos_operacionais"}}
    compacta["ingredientes"] = [{k: item[k] for k in campos_item if k in item} for item in dados["ingredientes"]]
    fonte = dados.get("fonte_verificada")
    if isinstance(fonte, dict):
        compacta["fonte_verificada"] = {k: v for k, v in fonte.items() if k in {"nome", "url", "porcoes", "tempo_preparo_min"}}
    if (fidelidade := dados.get("fidelidade_fonte")) and fidelidade.get("diferencas"):
        compacta["fidelidade_fonte"] = {"diferencas": fidelidade["diferencas"]}
    return compacta


def depois_da_ferramenta(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **_: Any) -> Optional[str]:
    """Hook ``transform_tool_result``: devolve o resultado com ``proximo_passo``, ou ``None`` para deixar como está. Nunca derruba o turno."""
    if tool_name not in ("despensa_listar", "despensa_buscar", "web_search", "web_extract", "receita_conferir_ingredientes") or not isinstance(result, str):
        return None
    try:
        try:
            dados = json.loads(result)
        except ValueError:  # resposta em texto puro (erro de rede, por exemplo): não é o formato que este hook conhece
            if not (somente_sugestoes(session_id) or alvo_escolhido(session_id) or alvo_da_cotacao(session_id)) or tool_name not in ("web_extract", "web_search"):
                return None
            dados = {"success": False, "error": "Não foi possível obter uma receita verificável.", "results": []}
        with _LOCK:
            modo = _PESQUISANDO.get(session_id)
            alvo = dict(_ALVO_ESCOLHIDO[session_id]) if session_id in _ALVO_ESCOLHIDO else None
            foco = _FOCOS.get(session_id) if modo in ("sugerir", "pesquisar", "montar") else None
            # Só a busca de sugestões descarta o que ela já viu; a ficha de uma opção escolhida usa a própria fonte.
            vistas = list(_JA_MOSTRADAS.get(session_id) or []) if modo == "sugerir" else []
        filtro_prioridade = bool(foco and foco.explicito and not (set(foco.categorias) & _CATEGORIAS_AMPLAS))
        filtrar = modo == "sugerir" or bool(alvo) or filtro_prioridade
        repetidas = False
        if isinstance(dados, dict) and filtrar and tool_name == "web_extract":
            validas = [p for p in dados.get("results") or [] if isinstance(p, dict) and _receita_direta([p])
                       and (not alvo or not alvo.get("fonte_url") or p.get("url") == alvo["fonte_url"])]
            novas = [p for p in validas if not _repetida(vistas, *_receita_direta([p]))]
            repetidas = len(novas) < len(validas)
            if filtro_prioridade:
                from .fonte import contrato_pagina

                relativas = [p for p in novas if (contrato := contrato_pagina(p))
                             and foco.combina(contrato["nome"], contrato.get("ingredientes"))]
                # Uma ficha delegada deve estar no tema. Na lista de opções, só
                # acrescente ideias de outros tipos depois de encontrar o foco.
                novas = (relativas + [p for p in novas if p not in relativas]
                         if relativas and modo == "sugerir" else relativas)
            dados = {**dados, "results": novas}
            if not novas:
                return _falha_de_leitura(session_id, dados, alvo, repetidas=repetidas)
        achados = dados["data"].get("web") if isinstance(dados, dict) and isinstance(dados.get("data"), dict) else None
        if vistas and tool_name == "web_search" and isinstance(achados, list):
            ineditos = [r for r in achados if not (isinstance(r, dict) and _repetida(vistas, None, r.get("url")))]
            repetidas = len(ineditos) < len(achados)
            dados = {**dados, "data": {**dados["data"], "web": ineditos}}
            achados = ineditos
        if foco and tool_name == "web_search" and isinstance(achados, list) and achados:
            ordenados = sorted(achados, key=lambda r: not isinstance(r, dict) or not foco.combina(r.get("title")))
            dados = {**dados, "data": {**dados["data"], "web": ordenados}}
        if (isinstance(dados, dict) and filtrar and tool_name == "web_search"
                and ((dados.get("error") or dados.get("success") is False) or not (dados.get("data") or {}).get("web"))):
            return _falha_de_leitura(session_id, dados, alvo, repetidas=repetidas)
        if isinstance(dados, dict):
            with _LOCK:
                etapa = _PROXIMA_FERRAMENTA.get(session_id)
                if etapa == tool_name:
                    pagina_util = tool_name == "web_extract" and _receita_direta(dados.get("results") or []) is not None
                    adaptando = (_ALVO_ESCOLHIDO.get(session_id) or {}).get("adaptacao_delegada")
                    if adaptando and tool_name == "receita_conferir_ingredientes" and dados.get("success") and dados.get("receita_id"):
                        _ALVO_ESCOLHIDO[session_id]["receita_id"] = dados["receita_id"]
                    corrigivel = bool(tool_name == "receita_conferir_ingredientes" and session_id in _ALVO_ESCOLHIDO and (
                        (adaptando and dados.get("success") is False)
                        or any(b.get("tipo") in ("fonte_incompleta", "fonte_divergente", "preparo_adaptacao_pendente")
                               for b in dados.get("bloqueios") or [])))
                    if corrigivel and _CORRECOES_ESCOLHIDO.get(session_id, 0) < 1:
                        _CORRECOES_ESCOLHIDO[session_id] = 1
                    elif ((dados.get("error") or dados.get("success") is False) and not pagina_util) or tool_name == "receita_conferir_ingredientes":
                        _PROXIMA_FERRAMENTA.pop(session_id, None)
                    elif tool_name == "despensa_listar":
                        _PROXIMA_FERRAMENTA[session_id] = "receita_conferir_ingredientes" if adaptando else "web_search"
                    elif tool_name == "web_search":
                        if session_id not in _COTACOES and (dados.get("data") or {}).get("web"):
                            _PROXIMA_FERRAMENTA[session_id] = "web_extract"
                        else:
                            _PROXIMA_FERRAMENTA.pop(session_id, None)
                    elif tool_name == "web_extract":
                        _PROXIMA_FERRAMENTA[session_id] = (("despensa_buscar" if _PESQUISANDO.get(session_id) == "sugerir" else
                                                          "receita_conferir_ingredientes") if _receita_direta(dados.get("results") or []) else "web_search")
                    elif tool_name == "despensa_buscar" and _PESQUISANDO.get(session_id) == "sugerir":
                        _PROXIMA_FERRAMENTA.pop(session_id, None)
        if tool_name == "web_extract" and isinstance(dados, dict) and session_id:
            from .fonte import contrato_pagina
            for pagina in dados.get("results") or []:
                contrato = contrato_pagina(pagina) if isinstance(pagina, dict) and _receita_direta([pagina]) else None
                if contrato and contrato.get("url"):
                    with _LOCK:
                        fichas = _FICHAS_LIDAS.setdefault(session_id, {})
                        fichas[contrato["url"]] = contrato
                        if len(fichas) > 8:
                            fichas.pop(next(iter(fichas)))
                        if len(_FICHAS_LIDAS) > MAX_SESSOES:
                            _FICHAS_LIDAS.pop(next(iter(_FICHAS_LIDAS)))
                        if modo == "sugerir":
                            atuais = _SUGESTOES_LIDAS.setdefault(session_id, [])
                            if len(atuais) < 3 and not any(c["url"] == contrato["url"] for c in atuais):
                                atuais.append(deepcopy(contrato))
        passo = _passo(tool_name, args, dados, session_id) if isinstance(dados, dict) else None
        if modo == "sugerir" and tool_name == "despensa_buscar" and isinstance(dados, dict) and dados.get("success") is not False:
            apresentacao = _apresentar_sugestoes(session_id, dados)
            if apresentacao:
                passo = "Apresente somente estas opções verificadas e aguarde a escolha, sem montar uma ficha:\n" + apresentacao
        if modo == "sugerir" and tool_name == "web_extract" and isinstance(dados, dict) and dados.get("results"):
            fontes = [_receita_direta([p]) for p in dados["results"]]
            passo = ("Somente estas receitas foram lidas e verificadas: " + "; ".join(f"{nome} ({url})" for nome, url in fontes) + ". "
                     "Não apresente como candidata nenhuma página que apareceu só na busca ou foi descartada nesta leitura. " + (passo or PASSO_APOS_LEITURA))
        if passo:
            dados = {**dados, "proximo_passo": passo}
        if (tool_name == "receita_conferir_ingredientes" and len(result) > 8000 and isinstance(dados, dict)
                and dados.get("receita_id") and isinstance(dados.get("ingredientes"), list)):
            return json.dumps(_conferencia_para_conversa(dados), ensure_ascii=False, separators=(",", ":"))
        return json.dumps(dados, ensure_ascii=False) if passo else None
    except Exception:  # noqa: BLE001 - sem o empurrão o modelo ainda tem o resultado original
        logger.exception("pesquisa: não consegui acrescentar o próximo passo a %s", tool_name)
        return None
