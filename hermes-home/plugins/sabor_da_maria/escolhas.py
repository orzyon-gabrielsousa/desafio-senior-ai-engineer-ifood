"""Resolve escolhas explícitas da lista que a consultora acabou de mostrar."""

from __future__ import annotations

import re
from typing import Any

from .store import Store
from .units import norm_text

_OPCAO = re.compile(r"^\s*(?:#{1,6}\s+)?(?:✅\s*)?(?:\*\*)?(\d{1,2})[.)]\s+(.+?)\s*$", re.M)
_FONTE = re.compile(r"^\s*(?:[-*]\s+)?\**fonte\s*:\**\s*(.+)$", re.I | re.M)
_LINK = re.compile(r"https?://[^\s<>\])]+", re.I)
_PEDIDO = re.compile(r"^(?:por favor[, ]+)?(?:adicione|adiciona|salve|salva|escolho|escolhi|quero (?:salvar|guardar|adicionar|ficar com|escolher|usar))\s+(.+)$")
_DESTINO = re.compile(r"\b(?:ao|no|na|em meu|no meu) (?:estoque|despensa|carrinho|cardapio)\b")
_SELECAO_COM_RESSALVA = re.compile(r"\b(?:exceto|menos o|menos a|nao (?:quero|escolho|escolha|adicione|salve|guarde)|talvez|se der|se puder)\b")
_RETOMAR = re.compile(r"^quero continuar o prato escolhido #(\d+)(?:\b|$)", re.I)
_OFERTA_TROCA = re.compile(r"Quer trocar «([^»\n]+)» por «([^»\n]+)»\?", re.I)
_ID_MENCIONADO = re.compile(r"\b(?:prato(?:\s+escolhido)?|confirmo|aceito|aprovo)\s+#\s*(\d+)\b", re.I)
_VALOR_PRECO = r"(?:\d{1,3}(?:\.\d{3})+|\d+)(?:[,.]\d{1,2})?"
_PRECO = re.compile(rf"\bR\$\s*({_VALOR_PRECO})(?!\d|[.,]\d)", re.I)
_PRECO_CURTO = re.compile(
    rf"(?:sim[,\s]+)?(?:(?:escolho(?: vender)?(?: por)?|quero(?: vender por)?|prefiro|pode ser|vou vender por)\s+)?"
    rf"(?P<moeda>R\$\s*)?(?P<valor>{_VALOR_PRECO})(?P<reais>\s+rea(?:l|is))?"
    rf"(?P<porcao>\s+por por[cç][aã]o)?"
    rf"(?:[.!;,]\s*(?:pode (?:registrar|salvar|cadastrar)|registre|salve|cadastre)(?:\s+(?:no|ao) card[aá]pio)?)?[.!]?",
    re.I,
)
_REGISTRO = re.compile(r"^(?:(?:sim|por favor)[, ]+)*(?:registre|salve|cadastre|coloque|adicione|"
                      r"(?:pode|quero) (?:registrar|salvar|cadastrar|colocar|adicionar))\b[^.!?]*\b(?:no|ao) cardapio\b")
_OBJETIVO_REGISTRO = re.compile(r"\b(?:registrar|salvar|cadastrar|colocar|adicionar|incluir|registrado)\b[^.!?]{0,80}\b(?:no|ao) cardapio\b")
_PERGUNTA_PRECO = re.compile(
    r"\b(?:qual (?:o |dos )?(?:precos?|valor)(?: de venda| por porcao)?|por quanto) "
    r"(?:(?:voce|a senhora) )?(?:quer|deseja|prefere|escolhe|gostaria|vamos|devo)\b"
)
_PERGUNTA_PREFERENCIA = re.compile(r"^qual (?:(?:voce|a senhora) )?(?:prefere|escolhe)\b")
_ORIENTACAO_PRECO = re.compile(r"\bescolha (?:um |o )?preco\b")
_REGISTRO_NEGADO = re.compile(r"\bnao (?:\w+ ){0,2}(?:regist\w*|salv\w*|cadastr\w*|coloqu\w*|adicione|escolho|aceito)\b")
_CONDICAO = re.compile(r"\b(?:se (?:eu|voce)|caso|quando eu|so depois|ainda nao|nao tenho certeza|talvez|"
                       r"antes de registrar|quero revisar|quero conferir|estou copiando|copiei)\b")
_ESCOLHA_PRECO = re.compile(r"^(?:(?:sim|por favor)[, ]+)*(?:escolho|vou vender|quero vender|use o preco)\b")
_REFERENCIA_ANAFORICA = re.compile(r"^(?:(?:sim|por favor)[, ]+)*(?:registre|salve|cadastre|coloque|adicione|"
                                  r"(?:pode|quero) (?:registrar|salvar|cadastrar|colocar|adicionar))\s+"
                                  r"(?:(?:este|esse|o) prato\s+)?(?:no|ao) cardapio\b")
_QUANTIDADES = {"2": 2, "dois": 2, "duas": 2, "3": 3, "tres": 3}
_NUMERO_GRUPO = r"(?:2|3|dois|duas|tres)"
_TIPO_OPCAO = r"(?:pratos|receitas|opcoes)"
_ALVO_COLETIVO = re.compile(
    rf"(?:(?:os|as|esses|estes|essas|estas) (?P<com_artigo>{_NUMERO_GRUPO})(?: {_TIPO_OPCAO})?|"
    rf"(?P<com_tipo>{_NUMERO_GRUPO}) {_TIPO_OPCAO}|"
    rf"(?P<ambos>ambos|ambas)(?: {_TIPO_OPCAO})?|todos(?: os pratos)?|todas(?: as (?:receitas|opcoes))?)"
)
_GOSTOU_DO_GRUPO = rf"gostei d(?:esses|estes|essas|estas|os|as) {_NUMERO_GRUPO}(?: {_TIPO_OPCAO})?|gostei de (?:ambos|ambas|todos|todas)"
_PEDIDO_COLETIVO = re.compile(
    rf"^(?:sim[, .!]+)?(?:(?:{_GOSTOU_DO_GRUPO})[,; .!]+(?:e )?)?"
    rf"(?:eu )?(?:quero|vou querer|pode (?:guardar|salvar|adicionar)) (?P<alvo>.+)$"
)
_PERGUNTA_ESCOLHA = re.compile(r"\b(?:qual (?:receita|prato|opcao)|nome (?:exato )?(?:da receita|do prato)|numero (?:do prato|da opcao))\b")
_APONTAR = re.compile(r"^(?:essa|esse|esta|este|aquela|aquele)\b(?:\s+(?:que voce mostrou|apresentad[oa](?: acima)?|acima))?\s*[?.:,!-]*\s*")
_NUMERO_E_NOME = re.compile(r"^(?:(?:a |o )?(?:opcao|prato|receita)\s+)?(\d{1,2})(?:[.): -]+(.+))?$")
_FEEDBACK = re.compile(r"^(?:sim[, ]+)?gost(?:o|ei)(?:[,. ]+(?:e )?nao vejo (?:nenhum )?impedimento)?[.!]?$|^nao vejo (?:nenhum )?impedimento[.!]?$")
_GOSTEI_PARA_USAR = re.compile(r"^gostei (?:de|do|da|desse|dessa) (.+?)(?:[,.]\s*(?:e )?vamos (?:usa (?:lo|la)|usar))?$")
_INGREDIENTES_NA_OPCAO = re.compile(r"\b(?:despensa|ja tem|precisa comprar|precisa conferir|ingredientes confirmados|faltando)\b")


def id_mencionado(texto: str) -> int | None:
    ids = set(_ID_MENCIONADO.findall(texto))
    # Uma referência clara seguida de uma segunda opção não escolhe nenhuma delas.
    todos = set(re.findall(r"#\s*(\d+)\b", texto))
    return int(ids.pop()) if len(ids) == 1 and ids == todos else None


def _preco_decimal(valor: str) -> float | None:
    if "," in valor:
        valor = valor.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", valor):
        valor = valor.replace(".", "")
    preco = float(valor)
    return preco if 0 < preco < float("inf") else None


def _preco_curto(texto: str) -> float | None:
    valor = _PRECO_CURTO.fullmatch(texto.strip())
    if not valor:
        return None
    # Um inteiro sozinho pode se referir ao número de uma opção da lista.
    if not (valor["moeda"] or valor["reais"] or valor["porcao"] or re.search(r"[,.]", valor["valor"])):
        return None
    return _preco_decimal(valor["valor"])


def _pergunta_preco_para_registro(resposta: str) -> bool:
    antes, separador, _ = resposta.rpartition("?")
    trechos = _trechos_do_pedido(antes.rsplit("?", 1)[-1]) if separador else []
    if not trechos:
        return False
    pergunta_sobre_preco = _PERGUNTA_PRECO.search(trechos[-1]) or (
        len(trechos) > 1 and _PERGUNTA_PREFERENCIA.search(trechos[-1]) and _ORIENTACAO_PRECO.search(trechos[-2])
    )
    return bool(pergunta_sobre_preco and any(
        _OBJETIVO_REGISTRO.search(trecho) and not _REGISTRO_NEGADO.search(trecho)
        for trecho in _trechos_do_pedido(resposta)
    ))


def perguntou_preco_do_prato(resposta_anterior: str, prato_id: int, nome: str) -> bool:
    """Confirma que a última pergunta solicita preço para registrar o prato identificado."""
    if not _pergunta_preco_para_registro(resposta_anterior):
        return False
    ids = set(_ID_MENCIONADO.findall(resposta_anterior)) | set(re.findall(r"(?<!#)#(\d+)\b", resposta_anterior))
    if ids:
        return ids == {str(prato_id)}
    nome_normal = norm_text(nome)
    return bool(nome_normal and re.search(rf"\b{re.escape(nome_normal)}\b", norm_text(resposta_anterior)))


def preco_para_registrar(texto: str, resposta_anterior: str = "") -> float | None:
    normal = norm_text(texto)
    if any(c in texto for c in ('?', '"', '“', '”', '`', '>')) or _REGISTRO_NEGADO.search(normal) or _CONDICAO.search(normal):
        return None
    trechos = _trechos_do_pedido(texto)
    if trechos and (_REGISTRO.match(trechos[0]) or _ESCOLHA_PRECO.match(trechos[0])) and any(
        _REGISTRO.match(trecho) for trecho in trechos
    ):
        valores = _PRECO.findall(texto)
        return _preco_decimal(valores[0]) if len(valores) == 1 else None
    return _preco_curto(texto) if _pergunta_preco_para_registro(resposta_anterior) else None


def pediu_registro_do_prato(texto: str, resposta_anterior: str, prato_id: int, nome: str) -> bool:
    id_atual = id_mencionado(texto)
    if id_atual is not None:
        return id_atual == prato_id
    if re.search(r"#\s*\d+", texto):
        return False
    if f" {norm_text(nome)} " in f" {norm_text(texto).rstrip('. ,')} ":
        return True
    if _preco_curto(texto) is not None and perguntou_preco_do_prato(resposta_anterior, prato_id, nome):
        return True
    # Só herda a referência quando o turno anterior ofereceu o preço desse prato.
    return (id_mencionado(resposta_anterior) == prato_id and bool(_PRECO.search(resposta_anterior))
            and any(_REFERENCIA_ANAFORICA.match(t) for t in _trechos_do_pedido(texto)))


def _trechos_do_pedido(texto: str) -> list[str]:
    return [norm_text(linha) for linha in re.split(r"(?<=[.!])\s+|[;\n]", texto) if linha.strip()]


def opcoes_exibidas(resposta: str) -> list[dict[str, Any]]:
    """Usa apenas seções numeradas de receita que tenham uma fonte na própria seção."""
    secoes = list(_OPCAO.finditer(resposta))
    opcoes = []
    for indice, secao in enumerate(secoes):
        fim = secoes[indice + 1].start() if indice + 1 < len(secoes) else len(resposta)
        bloco = resposta[secao.end():fim]
        fonte = _FONTE.search(bloco)
        if not fonte or not _INGREDIENTES_NA_OPCAO.search(norm_text(bloco)):
            continue
        nome = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", secao[2]).strip(" *_`")
        url = _LINK.search(fonte[1])
        if nome:
            opcoes.append({"numero": int(secao[1]), "nome": nome, "fonte_url": url[0].rstrip(".,") if url else None})
    return opcoes if [o["numero"] for o in opcoes] == list(range(1, len(opcoes) + 1)) else []


def pedido_de_escolha(texto: str) -> str | None:
    """Diferencia a escolha de um prato de um comando dirigido ao carrinho, estoque ou cardápio."""
    normal = norm_text(texto).rstrip(" .!")
    if "?" in texto or _DESTINO.search(normal) or _SELECAO_COM_RESSALVA.search(normal):
        return None
    pedido = _PEDIDO.fullmatch(normal)
    if pedido and not pedido[1].startswith(("ao ", "no ", "na ", "a quantidade", "o preco")):
        return re.sub(r" (?:pra|para) mim$", "", pedido[1])
    coletivo = _PEDIDO_COLETIVO.fullmatch(normal)
    if coletivo:
        alvo = re.sub(r" (?:pra|para) mim$", "", coletivo["alvo"])
        # "Quero esses dois: 2. Nome B; 1. Nome A" continua sendo uma escolha
        # de duas opções, não um pedido para combinar as receitas. Os nomes e
        # a quantidade ainda serão conferidos contra a lista exibida.
        grupo = _ALVO_COLETIVO.match(alvo)
        if grupo and (grupo.end() == len(alvo) or re.match(r"[\s:;,.!-]+\S", alvo[grupo.end():])):
            return alvo
    if gostei := _GOSTEI_PARA_USAR.fullmatch(normal):
        return gostei[1]
    return None


def escolhidas(texto: str, resposta_anterior: str) -> list[dict[str, Any]]:
    pedido = pedido_de_escolha(texto)
    opcoes = opcoes_exibidas(resposta_anterior)
    if not pedido:
        normal = norm_text(texto).strip(" .!")
        if _FEEDBACK.fullmatch(normal):
            return opcoes if len(opcoes) == 1 else []
        alvo = _APONTAR.sub("", normal, count=1)
        numero = _NUMERO_E_NOME.fullmatch(alvo)
        if numero:
            return [o for o in opcoes if o["numero"] == int(numero[1]) and
                    (not numero[2] or norm_text(o["nome"]) == numero[2])]
        return [o for o in opcoes if norm_text(o["nome"]) == alvo]
    todas = _ALVO_COLETIVO.fullmatch(pedido)
    if pedido in ("prato", "receita", "opcao") and _GOSTEI_PARA_USAR.fullmatch(norm_text(texto).strip(" .!")):
        return opcoes if len(opcoes) == 1 else []
    if todas:
        numero = todas["com_artigo"] or todas["com_tipo"]
        if numero or todas["ambos"]:
            total = _QUANTIDADES[numero] if numero else 2
            return opcoes if len(opcoes) == total else []
        return opcoes if len(opcoes) > 1 else []
    grupo = _ALVO_COLETIVO.match(pedido)
    if grupo:
        referencias = pedido[grupo.end():].strip(" :;,.!-")
        quantidade = grupo["com_artigo"] or grupo["com_tipo"]
        total = _QUANTIDADES[quantidade] if quantidade else 2 if grupo["ambos"] else len(opcoes)
        nomes = [o for o in opcoes if re.search(rf"(?<!\w){re.escape(norm_text(o['nome']))}(?!\w)", referencias)]
        numeros = {int(n) for n in re.findall(r"(?<!\w)(\d{1,2})[.)](?:\s|$)", referencias)}
        if len(nomes) == total and (not numeros or numeros == {o["numero"] for o in nomes}):
            return nomes
        return []
    numeros = re.fullmatch(r"(?:(?:os |as |o |a )?(?:pratos?|receitas?|opcao|opcoes) )?([1-9](?:(?:,\s*| e )[1-9])*)", pedido)
    if numeros:
        ids = {int(n) for n in re.findall(r"\d", numeros[1])}
        escolha = [o for o in opcoes if o["numero"] in ids]
        return escolha if len(escolha) == len(ids) else []
    return [o for o in opcoes if f" {norm_text(o['nome'])} " in f" {pedido} "]


def escolha_integral(texto: str, resposta_anterior: str) -> bool:
    """Pura seleção da lista, sem pedir mudança na receita publicada.

    Este predicado é deliberadamente mais restrito que ``escolhidas``: uma
    escolha com ressalva ainda pode virar rascunho, mas não autoriza completar
    sua ficha automaticamente com todas as medidas originais da fonte.
    """
    if not isinstance(texto, str) or any(c in texto for c in ('?', '"', '“', '”', '`', '>', '<')):
        return False
    opcoes = escolhidas(texto, resposta_anterior)
    if not opcoes:
        return False
    restante = norm_text(texto)
    if restante.strip(" .!") == "gostei":
        return len(opcoes) == 1
    if (gostei := _GOSTEI_PARA_USAR.fullmatch(restante.strip(" .!"))) and not _PEDIDO_COLETIVO.fullmatch(restante.strip(" .!")):
        restante = "quero usar " + gostei[1]
    # Só o número do grupo na posição "quero os 2" significa contagem de
    # opções. Um número depois dos nomes pode ser rendimento ou outra medida.
    pedido = _PEDIDO.fullmatch(restante)
    coletivo = _PEDIDO_COLETIVO.fullmatch(restante) if not pedido else None
    if pedido or coletivo:
        inicio = pedido.start(1) if pedido else coletivo.start("alvo")
        corpo = pedido[1] if pedido else coletivo["alvo"]
        grupo = _ALVO_COLETIVO.match(corpo)
        if grupo:
            restante = restante[:inicio] + " " + restante[inicio + grupo.end():]
    nomes_encontrados: set[int] = set()
    for opcao in sorted(opcoes, key=lambda o: len(norm_text(o["nome"])), reverse=True):
        nome = re.escape(norm_text(opcao["nome"]))
        literal = rf"(?<!\w){nome}(?!\w)"
        numerado = rf"(?<!\w){opcao['numero']}[.)]?\s+{nome}(?!\w)"
        restante, com_numero = re.subn(numerado, " ", restante)
        restante, so_nome = re.subn(literal, " ", restante)
        if com_numero or so_nome:
            nomes_encontrados.add(opcao["numero"])
    numeros = re.findall(r"\d+(?:[.,/]\d+)*", restante)
    if nomes_encontrados:
        if nomes_encontrados != {o["numero"] for o in opcoes} or numeros:
            return False
    else:
        # Seleções só por números também precisam bater integralmente com a
        # lista; decimais, frações ou números repetidos não são referências.
        if (any(not n.isdigit() or int(n) not in {o["numero"] for o in opcoes} for n in numeros)
                or len(numeros) != len(set(numeros))):
            return False
    permitidas = {
        "eu", "sim", "pode", "vou", "querer", "quero", "escolho", "escolhi", "salve", "salva", "salvar", "guardar", "guarde",
        "adicione", "adiciona", "adicionar", "usar", "ficar", "com", "por", "favor", "os", "as", "o", "a",
        "esses", "estes", "essas", "estas", "prato", "pratos", "receita", "receitas", "opcao", "opcoes",
        "e", "para", "pra", "mim",
    }
    palavras = re.findall(r"[a-z]+", restante)
    return all(palavra in permitidas for palavra in palavras)


def guardar_escolhas(texto: str, resposta_anterior: str) -> list[dict[str, Any]]:
    """Distingue guardar a ideia de pedir para usar a versão publicada quando conferida."""
    opcoes = escolhidas(texto, resposta_anterior)
    normal = norm_text(texto).strip(" .!")
    usar = normal == "gostei" or normal.startswith("quero usar ") or bool(_GOSTEI_PARA_USAR.fullmatch(normal))
    if usar and escolha_integral(texto, resposta_anterior):
        return [{**opcao, "usar_quando_viavel": True} for opcao in opcoes]
    return opcoes


def lista_da_escolha(falas: list) -> str:
    """A lista pode estar antes de uma pergunta curta que pediu nome ou número."""
    if not falas:
        return ""
    ultima = falas[-1].resposta_a
    if opcoes_exibidas(ultima) or not (_PERGUNTA_ESCOLHA.search(norm_text(ultima)) or pedido_de_escolha(falas[-1].texto)):
        return ultima
    return next((fala.resposta_a for fala in reversed(falas[-4:-1]) if opcoes_exibidas(fala.resposta_a)), ultima)


def preparar_turno(store: Store, texto: str, resposta_anterior: str) -> str | None:
    retomar = _RETOMAR.match(texto.strip())
    if retomar:
        prato = store.obter_prato(int(retomar[1]))
        if prato is None or prato["status"] == "removido":
            return "Esse prato não está mais disponível. Peça para escolher um dos pratos salvos em Minha cozinha."
        if prato["status"] == "aceito":
            return f"Ela retomou o prato #{prato['id']}, {prato['nome']}, que já está no cardápio. Consulte o registro antes de responder."
        dados = prato["dados"]
        fonte = (dados.get("selecao") or {}).get("fonte_url")
        instrucao = (f"Ela retomou apenas o prato #{prato['id']}, {prato['nome']}. Use este prato_id nas ferramentas; "
                     "mantenha as quantidades da ficha separadas do estoque disponível.")
        if prato.get("receita_id"):
            instrucao += f" A conferência existente tem receita_id={prato['receita_id']}; avance a partir dela ou revise se ela informou novos dados."
        elif fonte:
            instrucao += f" Fonte mostrada na escolha: {fonte}. Abra a página antes de usar medidas; se a leitura falhar, diga isso e peça uma referência ou os dados que faltam."
        return instrucao
    opcoes = guardar_escolhas(texto, resposta_anterior)
    if not opcoes:
        lista = opcoes_exibidas(resposta_anterior)
        if len(lista) > 1 and _FEEDBACK.fullmatch(norm_text(texto).strip(" .!")):
            nomes = "; ".join(f"{p['numero']}. {p['nome']}" for p in lista)
            return f"Ela gostou das ideias, mas ainda há várias opções. Pergunte qual prefere entre {nomes}."
        return None
    salvos = store.selecionar_pratos(opcoes)
    from . import jornada

    jornada.continuar_escolhas(store, [p["id"] for p in salvos])
    itens = "; ".join(f"#{p['id']} {p['nome']}" for p in salvos)
    primeira = salvos[0]
    fonte = primeira.get("fonte_url")
    leitura = (f"Abra a fonte mostrada {fonte} para recuperar ingredientes, rendimento e preparo. " if fonte else
               "A primeira escolha ainda não tem uma URL de fonte verificada. Procure a página direta desse prato; não invente o link nem use os ingredientes de outra opção. ")
    return (f"Escolhas gravadas em Minha cozinha > Pratos: {itens}. Confirme os nomes e o status da seleção. "
            "São fichas separadas; não pergunte se quer combinar ou escolher apenas um, porque a pessoa já escolheu todos esses pratos. "
            f"Comece a montar a ficha do prato #{primeira['id']}, {primeira['nome']}, com os dados que já pode recuperar. " + leitura +
            "Use o rendimento da fonte se ela não pediu outro, confira os ingredientes e os requisitos e salve a ficha mesmo que haja pendências. "
            "Use a mesma receita. Enquanto faltar dado essencial, mantenha o rascunho. "
            "Quando ela já pediu para usar a versão original, prossiga no aceite só depois de a conferência confirmar a viabilidade. "
            "Pergunte somente o primeiro requisito relevante que realmente faltar após consultar a fonte e o cadastro; depois conclua a outra ficha.")


def troca_confirmada(texto: str, resposta_anterior: str) -> tuple[str, str] | None:
    ofertas = _OFERTA_TROCA.findall(resposta_anterior)
    if len(ofertas) != 1:
        return None
    anterior, novo = ofertas[0]
    aceite = norm_text(texto).strip(" ,.")
    if aceite in {"sim", "sim pode trocar", "pode trocar", "aceito a troca", "quero trocar", f"quero trocar por {norm_text(novo)}"}:
        return anterior, novo
    return None
