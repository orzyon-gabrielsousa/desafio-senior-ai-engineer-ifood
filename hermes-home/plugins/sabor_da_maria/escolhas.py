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
_RETOMAR = re.compile(r"^quero continuar o prato escolhido #(\d+)(?:\b|$)", re.I)
_OFERTA_TROCA = re.compile(r"Quer trocar «([^»\n]+)» por «([^»\n]+)»\?", re.I)
_ID_MENCIONADO = re.compile(r"\b(?:prato(?:\s+escolhido)?|confirmo|aceito|aprovo)\s+#\s*(\d+)\b", re.I)
_PRECO = re.compile(r"\bR\$\s*((?:\d{1,3}(?:\.\d{3})+|\d+)(?:[,.]\d{1,2})?)(?!\d|[.,]\d)", re.I)
_REGISTRO = re.compile(r"^(?:(?:sim|por favor)[, ]+)*(?:registre|salve|cadastre|coloque|adicione|"
                      r"(?:pode|quero) (?:registrar|salvar|cadastrar|colocar|adicionar))\b[^.!?]*\b(?:no|ao) cardapio\b")
_REGISTRO_NEGADO = re.compile(r"\bnao (?:\w+ ){0,2}(?:regist\w*|salv\w*|cadastr\w*|coloqu\w*|adicione|escolho|aceito)\b")
_CONDICAO = re.compile(r"\b(?:se (?:eu|voce)|caso|quando eu|so depois|ainda nao|nao tenho certeza|talvez|"
                       r"antes de registrar|quero revisar|quero conferir|estou copiando|copiei)\b")
_ESCOLHA_PRECO = re.compile(r"^(?:(?:sim|por favor)[, ]+)*(?:escolho|vou vender|quero vender|use o preco)\b")
_REFERENCIA_ANAFORICA = re.compile(r"^(?:(?:sim|por favor)[, ]+)*(?:registre|salve|cadastre|coloque|adicione|"
                                  r"(?:pode|quero) (?:registrar|salvar|cadastrar|colocar|adicionar))\s+"
                                  r"(?:(?:este|esse|o) prato\s+)?(?:no|ao) cardapio\b")
_QUANTIDADES = {"2": 2, "dois": 2, "duas": 2, "3": 3, "tres": 3}
_PERGUNTA_ESCOLHA = re.compile(r"\b(?:qual (?:receita|prato|opcao)|nome da receita|numero (?:do prato|da opcao))\b")
_APONTAR = re.compile(r"^(?:essa|esse|esta|este|aquela|aquele)\b(?:\s+(?:que voce mostrou|apresentad[oa](?: acima)?|acima))?\s*[?.:,!-]*\s*")
_NUMERO_E_NOME = re.compile(r"^(?:(?:a |o )?(?:opcao|prato|receita)\s+)?(\d{1,2})(?:[.): -]+(.+))?$")
_FEEDBACK = re.compile(r"^(?:sim[, ]+)?gosto(?:[,. ]+(?:e )?nao vejo (?:nenhum )?impedimento)?[.!]?$|^nao vejo (?:nenhum )?impedimento[.!]?$")


def id_mencionado(texto: str) -> int | None:
    ids = set(_ID_MENCIONADO.findall(texto))
    # Uma referência clara seguida de uma segunda opção não escolhe nenhuma delas.
    todos = set(re.findall(r"#\s*(\d+)\b", texto))
    return int(ids.pop()) if len(ids) == 1 and ids == todos else None


def preco_para_registrar(texto: str) -> float | None:
    normal = norm_text(texto)
    if any(c in texto for c in ('?', '"', '“', '”', '`', '>')) or _REGISTRO_NEGADO.search(normal) or _CONDICAO.search(normal):
        return None
    trechos = _trechos_do_pedido(texto)
    if not trechos or not (_REGISTRO.match(trechos[0]) or _ESCOLHA_PRECO.match(trechos[0])):
        return None
    if not any(_REGISTRO.match(trecho) for trecho in trechos):
        return None
    valores = _PRECO.findall(texto)
    if len(valores) != 1:
        return None
    valor = valores[0]
    if "," in valor:
        valor = valor.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", valor):
        valor = valor.replace(".", "")
    preco = float(valor)
    return preco if 0 < preco < float("inf") else None


def pediu_registro_do_prato(texto: str, resposta_anterior: str, prato_id: int, nome: str) -> bool:
    id_atual = id_mencionado(texto)
    if id_atual is not None:
        return id_atual == prato_id
    if re.search(r"#\s*\d+", texto):
        return False
    if f" {norm_text(nome)} " in f" {norm_text(texto).rstrip('. ,')} ":
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
        if not fonte or not re.search(r"\b(?:despensa|ja tem|precisa comprar|precisa conferir)\b", norm_text(bloco)):
            continue
        nome = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", secao[2]).strip(" *_`")
        url = _LINK.search(fonte[1])
        if nome:
            opcoes.append({"numero": int(secao[1]), "nome": nome, "fonte_url": url[0].rstrip(".,") if url else None})
    return opcoes if [o["numero"] for o in opcoes] == list(range(1, len(opcoes) + 1)) else []


def pedido_de_escolha(texto: str) -> str | None:
    """Diferencia a escolha de um prato de um comando dirigido ao carrinho, estoque ou cardápio."""
    normal = norm_text(texto).rstrip(" .")
    if "?" in texto or _DESTINO.search(normal):
        return None
    pedido = _PEDIDO.fullmatch(normal)
    if not pedido or pedido[1].startswith(("ao ", "no ", "na ", "a quantidade", "o preco")):
        return None
    return re.sub(r" (?:pra|para) mim$", "", pedido[1])


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
    todas = re.fullmatch(r"(?:(?:esses|estes|essas|estas|os|as) )?(2|3|dois|duas|tres)(?: pratos| receitas| opcoes)?", pedido)
    if todas:
        return opcoes if len(opcoes) == _QUANTIDADES[todas[1]] else []
    numeros = re.fullmatch(r"(?:(?:os |as |o |a )?(?:pratos?|receitas?|opcao|opcoes) )?([1-9](?:(?:,\s*| e )[1-9])*)", pedido)
    if numeros:
        ids = {int(n) for n in re.findall(r"\d", numeros[1])}
        escolha = [o for o in opcoes if o["numero"] in ids]
        return escolha if len(escolha) == len(ids) else []
    return [o for o in opcoes if f" {norm_text(o['nome'])} " in f" {pedido} "]


def lista_da_escolha(falas: list) -> str:
    """A lista pode estar antes de uma pergunta curta que pediu nome ou número."""
    if not falas:
        return ""
    ultima = falas[-1].resposta_a
    if opcoes_exibidas(ultima) or not _PERGUNTA_ESCOLHA.search(norm_text(ultima)):
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
    opcoes = escolhidas(texto, resposta_anterior)
    if not opcoes:
        lista = opcoes_exibidas(resposta_anterior)
        if len(lista) > 1 and _FEEDBACK.fullmatch(norm_text(texto).strip(" .!")):
            nomes = "; ".join(f"{p['numero']}. {p['nome']}" for p in lista)
            return f"Ela gostou das ideias, mas ainda há várias opções. Pergunte qual prefere entre {nomes}."
        return None
    salvos = store.selecionar_pratos(opcoes)
    itens = "; ".join(f"#{p['id']} {p['nome']}" for p in salvos)
    return (f"Escolhas gravadas em Minha cozinha > Pratos: {itens}. Confirme os nomes e o status da seleção. "
            "Neste turno, não troque os pratos, não calcule com outra receita e não lance compras ou reservas. "
            "Para continuar, peça só a próxima informação útil, como quantas porções preparar de cada prato.")


def troca_confirmada(texto: str, resposta_anterior: str) -> tuple[str, str] | None:
    ofertas = _OFERTA_TROCA.findall(resposta_anterior)
    if len(ofertas) != 1:
        return None
    anterior, novo = ofertas[0]
    aceite = norm_text(texto).strip(" ,.")
    if aceite in {"sim", "sim pode trocar", "pode trocar", "aceito a troca", "quero trocar", f"quero trocar por {norm_text(novo)}"}:
        return anterior, novo
    return None
