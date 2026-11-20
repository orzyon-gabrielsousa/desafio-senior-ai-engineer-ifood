"""Adapta receitas da casa ao estoque; a descoberta inicial segue a pesquisa web."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import Counter, OrderedDict
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from . import costing, escolhas, falas as falas_mod, fonte
from .falas import Fala
from .matching import tokens
from .store import Store
from .units import norm_text, to_base

INSTRUCAO_ADAPTACAO = (
    "MONTAGEM FLEXÍVEL: a receita da internet é referência, não uma obrigação de ingredientes idênticos. "
    "Use despensa_listar/despensa_buscar e raciocine sobre proximidade culinária e função no preparo. "
    "Score é semelhança, não probabilidade de sucesso. Proponha substituições plausíveis com os itens reais; "
    "na ficha use nome do ingrediente efetivamente consumido e ingrediente_original para indicar a troca. "
    "Derivados simples podem ser preparados com a matéria-prima disponível, contando quantidades, rendimento, tempo e custo. "
    "Se ela delegou usar o estoque ou ignorar faltantes, execute a adaptação agora, sem pedir essa autorização outra vez. "
    "O que não puder usar fica fora dos ingredientes do lote e anotado nas observações, não como compra obrigatória. "
    "Envie a lista completa do lote e ingredientes_omitidos com os nomes que ficarão de fora, adaptacao explicando trocas/omissões, modo_preparo próprio e tempo_preparo_min "
    "com tempo_preparo_base. Proponha medidas numéricas locais para itens a gosto e deixe claras as estimativas. "
    "Salve com receita_conferir_ingredientes no mesmo prato_id e preserve a fonte como referência; "
    "não diga que salvou sem a ferramenta. Mostre diferenças e peça só o aceite final ou o requisito real da cozinha ainda desconhecido. "
    "Pizza sem fermento pode virar massa fina sem fermentação: explique a textura diferente e ajuste o preparo. "
    "Fermentação natural não é fermento instantâneo: leva dias e exige manejo e planejamento; não invente levain pronto no estoque. "
    "Não invente estoque, preço, compra, capacidade da cozinha ou aceite. CMV e reservas usam apenas os insumos reais do lote."
)

_WEB = "pesquise na internet receitas para este pedido "
_ADAPTAR = re.compile(r"^(?:(?:por favor|voce)[, ]+)?(?:"
                      r"(?:adapte|adapta|pode adaptar) (?:(?:essa|esta|a|esse|este|o) )?(?:receita|prato)|"
                      r"(?:proponha|pode (?:propor|fazer)) uma adaptacao (?:(?:dessa|desta|da|desse|deste|do) )?(?:receita|prato))\b")
_PEDIDO = re.compile(r"^(?:(?:agora|entao)[, ]+)?(?:monte|monta|prepare|crie|faca|quero montar)\b")
_MONTAR_PRATO = re.compile(r"^(?:(?:um |uma )?(?:prato|receita|marmita))(?: (?:para|pro|pra) (?:o |meu |o meu )?cardapio)?$")
_EQUIVALENTE = re.compile(r"^(?:um |uma )?(?:(?:prato|receita|marmita) )?(?:equivalente|similar|parecid[oa]|alternativ[oa])"
                          r"(?: (?:com|usando) (?:(?:esses|estes|os|que) )?(?:mesmos )?(?:ingredientes|o que (?:eu )?tenho|a despensa))?$")
# "...com os ingredientes que tenho / com o que tenho / da despensa": montar a partir do estoque, sem compra nova.
_DESPENSA = re.compile(r"\b(?:com (?:os |o )?ingredientes (?:que )?(?:eu )?tenho|com o que (?:eu )?tenho"
                       r"|usando (?:os ingredientes|o que) (?:que )?(?:eu )?tenho|(?:da|com a|usando a) despensa)\b")
# "...sem azeitona / sem a azeitona": exclusão de um ingrediente nesta montagem.
_SEM_INGREDIENTE = re.compile(r"\bsem (?:a |o |as |os )?([a-z][a-z ]{1,28}?)(?=\s+(?:e|com|usando|na|no|da|do)\b|[.,!?]|$)")
_NUMERO_MEDIDA = r"\d+(?:[.,]\d+)?"
_UNIDADE_MEDIDA = r"kg|g|gramas?|quilos?|ml|mililitros?|l|litros?"
_ASSINATURA_MEDIDA = re.compile(r"\bmedidas ([a-f0-9]{16})$")
_PROPORCOES_PIZZA = {"cebola": 0.2, "alho": 0.02, "molho de tomate": 0.3,
                     "sal": 0.01, "fatias de queijo": 0.3, "oregano": 0.002}
_RESPOSTAS: OrderedDict[str, tuple[str, list[str], bool, int]] = OrderedDict()
_LOCK = threading.Lock()


def iniciar_turno(sessao: str, turno: str) -> None:
    if not sessao or not turno:
        return
    with _LOCK:
        _RESPOSTAS[sessao] = (turno, [], False, 0)
        _RESPOSTAS.move_to_end(sessao)
        if len(_RESPOSTAS) > 32:
            _RESPOSTAS.popitem(last=False)


def turno_atual(sessao: str) -> str | None:
    """Identidade do turno ativo; não cria nem consome uma resposta."""
    with _LOCK:
        atual = _RESPOSTAS.get(sessao)
        return atual[0] if atual else None


def registrar_resposta(sessao: str, linhas: list[str], *, substituir: bool = False, prioridade: int = 0) -> None:
    """Preserva o avanço mais relevante do turno; empates ficam com o último."""
    with _LOCK:
        atual = _RESPOSTAS.get(sessao)
        if atual and prioridade >= atual[3]:
            _RESPOSTAS[sessao] = (atual[0], linhas, substituir, prioridade)


def resposta_preparada(sessao: str, turno: str, *, prioridade_minima: int = 20) -> str | None:
    """Lê o resultado conferido sem consumi-lo antes da finalização do Hermes.

    Pendências corrigíveis (prioridade 10) continuam com a LLM. A chave do turno
    impede reutilizar uma proposta antiga depois de outra mensagem da pessoa.
    """
    if not sessao or not turno:
        return None
    with _LOCK:
        atual = _RESPOSTAS.get(sessao)
        if not atual or atual[0] != turno or atual[3] < prioridade_minima:
            return None
        texto = "\n\n".join(linha for linha in atual[1] if linha.strip()).strip()
    return texto or None


def completar_resposta(response_text: str = "", session_id: str = "", turn_id: str = "", **_: Any) -> str | None:
    """Garante que fatos da ficha e valores do registro cheguem ao chat no mesmo turno."""
    with _LOCK:
        atual = _RESPOSTAS.get(session_id)
        if not atual or atual[0] != turn_id:
            return None
        _, linhas, substituir, _ = _RESPOSTAS.pop(session_id)
    if not linhas or not isinstance(response_text, str):
        return None
    if substituir:
        pronta = "\n\n".join(linhas)
        return pronta if pronta != response_text else None
    if all(norm_text(linha.split(":", 1)[0]) in norm_text(response_text) for linha in linhas[1:-1]):
        return None
    # A API envia o complemento no streaming quando preservamos o prefixo original.
    final = linhas[-1]
    if final.endswith("?") and re.sub(r"\W+", " ", norm_text(final)).strip() in re.sub(r"\W+", " ", norm_text(response_text)):
        linhas = linhas[:-1]
    return response_text + "\n\n" + "\n\n".join(linhas)


def pediu_adaptacao(texto: Any) -> bool:
    """Reconhece delegação de uma versão local, sem tratar negação ou hipótese como pedido."""
    if not isinstance(texto, str) or re.search(r"[?`\"“”]|^\s*>", texto):
        return False
    normal = norm_text(texto).strip(" .!")
    return bool(re.match(
        r"^(?:(?:por favor|agora|entao)[, ]+)?(?:"
        r"(?:adapte|adapta|pode adaptar|quero adaptar)\b|"
        r"(?:ignore|ignora|pule|omita)\b|"
        r"(?:troque|substitua|use|usar)\b.*\b(?:por|no lugar|em vez)\b|"
        r"(?:faca|monte|salve|quero (?:montar|salvar|fazer))\b.*\b(?:sem|com o que|estoque|despensa)\b)", normal))


def omissoes_pedidas(texto: Any, ingredientes: list[Any]) -> list[str]:
    """Vincula um 'sem X' explícito a um único ingrediente; não escolhe substitutos."""
    if not pediu_adaptacao(texto):
        return []
    nomes = {norm_text(nome_da_medida(str(i.get("nome") if isinstance(i, dict) else i))):
             str(i.get("nome") if isinstance(i, dict) else i) for i in ingredientes}
    omitidos = []
    for pedido in _SEM_INGREDIENTE.finditer(norm_text(texto)):
        alvo = tokens(re.sub(r"^(?:comprar|usar|adicionar|incluir)\s+", "", pedido[1]))
        candidatos = [nome for normal, nome in nomes.items() if alvo and alvo <= tokens(normal)]
        if len(candidatos) == 1 and candidatos[0] not in omitidos:
            omitidos.append(candidatos[0])
    return omitidos


def nome_da_medida(nome: str) -> str:
    nome = re.sub(r"\s+(?:a gosto|quanto baste)$", "", nome, flags=re.I).strip()
    nome = re.sub(r"^quantidade n[aã]o informada de\s+", "", nome, flags=re.I)
    # Fichas antigas podem trazer a medida dentro do nome, até em duas camadas.
    medida = (r"\d+(?:[.,/]\d+|\s+\d+/\d+)?\s*"
              r"(?:un(?:idades?)?|kg|g|ml|l|colher(?:es)?(?: de (?:sopa|ch[aá]))?|x[ií]caras?(?: de ch[aá])?)")
    return re.sub(rf"^(?:{medida}\s+(?:de\s+)?)+", "", nome, flags=re.I).strip() or nome


def _medidas_da_pizza(dados: dict[str, Any]) -> tuple[float, dict[str, dict[str, Any]]]:
    """Usa a farinha efetivamente prevista para este lote, mesmo numa adaptação."""
    original = dados.get("fonte_verificada") or {}
    if not re.search(r"\bpizza\b", norm_text(original.get("nome", ""))):
        return 0.0, {}
    itens = dados.get("ingredientes") or []
    farinhas = [i for i in itens if isinstance(i, dict) and
                norm_text(nome_da_medida(str(i.get("nome") or ""))) == "farinha de trigo"]
    if len(farinhas) != 1:
        return 0.0, {}
    farinha, = farinhas
    try:
        quantidade = float(str(farinha.get("quantidade")).replace(",", "."))
    except (ValueError, TypeError):
        return 0.0, {}
    conv = to_base(quantidade, str(farinha.get("unidade") or ""), "farinha de trigo", "kg")
    if conv.estimate or conv.qty_base is None or not math.isfinite(conv.qty_base) or conv.qty_base <= 0:
        return 0.0, {}
    gramas = conv.qty_base * 1000
    publicadas = {}
    for linha in original.get("ingredientes") or []:
        publicadas.setdefault(norm_text(nome_da_medida(str(linha))), []).append(str(linha))
    usos = Counter(norm_text(nome_da_medida(str(i.get("nome") or ""))) for i in itens if isinstance(i, dict))
    confirmadas = dados.get("medidas_de_uso_confirmadas") or {}
    estimativas = {}
    for item in itens:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("nome") or "")
        chave = norm_text(nome_da_medida(nome))
        if chave not in _PROPORCOES_PIZZA or usos[chave] != 1 or nome in confirmadas:
            continue
        linhas = publicadas.get(chave, [])
        if len(linhas) != 1 or re.search(r"[0-9½¼¾⅓⅔]", linhas[0]) or fonte._medida(linhas[0])[0] is not None:
            continue
        try:
            if float(str(item.get("quantidade") or 0).replace(",", ".")) != 0:
                continue
        except (ValueError, TypeError):
            continue
        proporcao = _PROPORCOES_PIZZA[chave]
        quantidade = round(gramas * proporcao, 2)
        if math.isfinite(quantidade) and quantidade > 0:
            percentual = f"{proporcao * 100:g}".replace(".", ",")
            estimativas[nome] = {"quantidade": quantidade, "unidade": "g",
                                 "base": f"proporção de teste de {percentual}% dos {gramas:g} g de farinha de trigo do lote"}
    return gramas, estimativas


def estimar_medidas_do_lote(receita: dict[str, Any]) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    """Completa as medidas sem base publicada quando há uma proporção local definida."""
    dados = receita.get("dados") or {}
    ingredientes = dados.get("ingredientes") or []
    _, estimativas = _medidas_da_pizza(dados)
    if not estimativas:
        return ingredientes, {}
    lote = []
    for item in ingredientes:
        estimativa = estimativas.get(item.get("nome")) if isinstance(item, dict) else None
        lote.append({**item, "quantidade": estimativa["quantidade"], "unidade": estimativa["unidade"]} if estimativa else item)
    return lote, estimativas


def texto_medidas_estimadas(dados: dict[str, Any]) -> str:
    estimativas = dados.get("medidas_de_uso_estimadas") or {}
    if not estimativas:
        return ""
    medidas = [f"{medida['quantidade']:g} {medida['unidade']} de {nome_da_medida(nome)} ({medida['base']})"
               for nome, medida in estimativas.items()]
    resumo = " e ".join(filter(None, [", ".join(medidas[:-1]), medidas[-1]]))
    return f"Para este lote, estimei {resumo}. Você pode ajustar essas medidas ao revisar a ficha."


def pergunta_da_medida(prato: dict[str, Any], pendencia: dict[str, Any], dados: dict[str, Any]) -> dict[str, Any] | None:
    """Uma medida de uso por vez; sugestões locais não alteram a receita publicada."""
    if pendencia.get("tipo") != "ingrediente_pendente" or not pendencia.get("detalhe", "").endswith(": a receita não traz a quantidade"):
        return None
    nome = pendencia["detalhe"].removesuffix(": a receita não traz a quantidade")
    itens = dados.get("ingredientes") or []
    alvos = [(idx, i) for idx, i in enumerate(itens) if isinstance(i, dict) and norm_text(str(i.get("nome"))) == norm_text(nome)]
    if len(alvos) != 1:
        return None
    indice, item = alvos[0]
    try:
        if float(str(item.get("quantidade", 0)).replace(",", ".")) > 0:
            return None
    except (ValueError, TypeError):
        pass
    rotulo = nome_da_medida(nome)
    farinha_gramas, estimativas = _medidas_da_pizza(dados)
    estimativa = estimativas.get(nome) if not dados.get("adaptacao") or norm_text(rotulo) == "sal" else None
    quantidade = estimativa["quantidade"] if estimativa else None
    original = dados.get("fonte_verificada") or {}
    identidade = {"prato_id": prato["id"], "fonte": original, "ingredientes": itens}
    cotacao = hashlib.sha256(json.dumps(identidade, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    pergunta = {"texto": f"Qual quantidade de {rotulo} você vai usar no lote inteiro? Informe um número e uma unidade, como 5 g.",
                "opcoes": [],
                "medida": {"indice": indice, "nome": nome, "rotulo": rotulo, "cotacao": cotacao, "quantidade": quantidade}}
    if quantidade is not None:
        resposta = f"Confirmo {quantidade:g} g de {rotulo} como medida de uso deste lote."
        pergunta.update(texto=(f"A fonte não informa a quantidade de {rotulo}. Para um lote piloto com {farinha_gramas:g} g de farinha, "
                               f"sugiro {quantidade:g} g de {rotulo}. É uma estimativa local para testar e ajustar, não uma medida publicada. "
                               f"Qual quantidade de {rotulo} você vai usar no lote inteiro?"),
                        opcoes=[{"rotulo": f"Usar {quantidade:g} g", "resposta": resposta,
                                 "mensagem": resposta + f" Continue o prato #{prato['id']}. Medidas {cotacao}."}])
    return pergunta


def tem_assinatura_da_medida(texto: str) -> bool:
    return bool(_ASSINATURA_MEDIDA.search(norm_text(texto).strip(" .!")))


def parece_resposta_de_medida(texto: str) -> bool:
    """Reconhece uma tentativa curta para a consultora esclarecer sem gravar."""
    if re.search(r"[?\n`\"'“”«»<>]", texto):
        return False
    literal = norm_text(texto).strip(" .!")
    return bool(re.fullmatch(
        rf"{_NUMERO_MEDIDA}|(?:{_NUMERO_MEDIDA}\s*)?(?:{_UNIDADE_MEDIDA})(?:\s*(?:/|\bpor\b)\s*(?:{_UNIDADE_MEDIDA}))?",
        literal))


def salvar_medida_do_turno(store: Store, prato: dict[str, Any], receita: dict[str, Any], fala: Fala,
                           pendencias: list[dict[str, Any]]) -> bool:
    """Confirmação contextual ou medida explícita; nunca transforma retomada em aceite."""
    if not pendencias:
        return False
    pergunta = pergunta_da_medida(prato, pendencias[0], receita["dados"])
    if not pergunta or re.search(r"[?`\"'“”«»<>]|(?<!\w)-\s*\d", fala.texto):
        return False
    medida = pergunta["medida"]
    texto = norm_text(fala.texto).strip(" .!")
    assinatura = _ASSINATURA_MEDIDA.search(texto)
    if assinatura:
        if assinatura[1] != medida["cotacao"]:
            return False
        texto = texto[:assinatura.start()].strip(" .!")
    texto = re.sub(rf"\s*\.?\s*(?:continue|vamos continuar) o prato {prato['id']}$", "", texto).strip(" .!")
    contextual = pergunta["texto"] in fala.resposta_a and fala.resposta_a.count("?") == 1
    if texto in {"sim", "sim confirmo", "confirmo", "pode usar", "pode usar essa medida"}:
        if not contextual or medida["quantidade"] is None:
            return False
        quantidade, unidade = medida["quantidade"], "g"
    else:
        numero = rf"({_NUMERO_MEDIDA})\s*({_UNIDADE_MEDIDA})"
        simples = re.fullmatch(numero, texto) if contextual else None
        explicita = re.fullmatch(r"(?:(?:eu )?(?:confirmo|vou usar|quero usar|use|usar) )?" + numero +
                                r" de " + re.escape(norm_text(medida["rotulo"])) +
                                r"(?: como medida de uso deste lote| para (?:este|esse) lote| (?:neste|nesse) lote)?", texto)
        valor = simples or explicita
        if not valor:
            return False
        quantidade = float(valor[1].replace(",", "."))
        unidade = valor[2]
    if not math.isfinite(quantidade) or quantidade <= 0:
        return False
    unidade = {"grama": "g", "gramas": "g", "quilo": "kg", "quilos": "kg", "mililitro": "ml", "mililitros": "ml",
               "litro": "L", "litros": "L", "l": "L"}.get(unidade, unidade)
    return store.atualizar_medida_rascunho(prato["id"], receita["id"], medida["indice"],
                                          receita["dados"]["ingredientes"], quantidade, unidade, fala.texto)


@dataclass(frozen=True)
class FichaDaCasa:
    nome: str
    referencias: tuple[str, ...]
    ingredientes: tuple[tuple[str, int, str], ...]
    equipamento: tuple[str, ...]
    tecnicas: tuple[str, ...]
    minutos: int
    preparo: str
    adaptacao: str


# Quantidades da receita da casa para um lote teste de 4 porções. A resolução de
# estoque e preço usa o nome exato do item comprado, com quantidades cruas.
FICHAS = (
    FichaDaCasa(
        "Arroz de forno com frango, tomate e mussarela", ("arroz de forno", "arroz gratinado"),
        (("Arroz branco tipo 1", 240, "g"), ("Peito de frango", 440, "g"), ("Tomate", 320, "g"),
         ("Cebola", 80, "g"), ("Queijo mussarela", 120, "g"), ("Óleo de soja", 15, "ml"), ("Sal", 4, "g")),
        ("fogão", "panela grande", "forno", "forma"), ("arroz e feijão", "assar"), 55,
        "Cozinhe o arroz. Asse o frango com o tomate, a cebola e o óleo até cozinhar por completo. Desfie o frango, "
        "misture com o arroz e o líquido do tomate, cubra com mussarela e leve ao forno para gratinar.",
        "Mantive arroz, frango e mussarela. Tomate e cebola substituem a seleta e deixam o arroz úmido sem requeijão.",
    ),
    FichaDaCasa(
        "Arroz de panela com frango e tomate", ("arroz de forno", "arroz gratinado", "arroz com frango", "galinhada"),
        (("Arroz branco tipo 1", 240, "g"), ("Peito de frango", 440, "g"), ("Tomate", 320, "g"),
         ("Cebola", 80, "g"), ("Óleo de soja", 15, "ml"), ("Sal", 4, "g")),
        ("fogão", "panela grande"), ("arroz e feijão", "refogar"), 40,
        "Doure o frango em cubos, cozinhe a cebola e o tomate na mesma panela e junte arroz, sal e água. "
        "Cozinhe tampado até o arroz ficar macio e o frango cozinhar por completo.",
        "O arroz, o frango e o tomate vão juntos à panela. O preparo dispensa forno, queijo, requeijão e seleta.",
    ),
    FichaDaCasa(
        "Espaguete com patinho e tomate fresco", ("bolonhesa", "macarrao com carne", "espaguete"),
        (("Macarrão espaguete", 320, "g"), ("Carne moída (patinho)", 400, "g"), ("Tomate", 500, "g"),
         ("Cebola", 80, "g"), ("Alho", 10, "g"), ("Óleo de soja", 15, "ml"), ("Sal", 5, "g")),
        ("fogão", "panela grande", "frigideira"), ("refogar", "molho"), 45,
        "Cozinhe o espaguete. Doure a carne, cozinhe com cebola, alho e tomate picado até engrossar e misture com a massa.",
        "Usei o espaguete e o patinho da despensa. O molho leva tomate fresco.",
    ),
    FichaDaCasa(
        "Peito de frango assado com batata", ("frango com batata", "frango com batatas", "frango assado com batata", "frango assado com batatas", "coxinha da asa", "coxinhas da asa"),
        (("Peito de frango", 480, "g"), ("Batata", 600, "g"), ("Cebola", 100, "g"), ("Alho", 10, "g"),
         ("Óleo de soja", 20, "ml"), ("Sal", 4, "g")),
        ("forno", "forma"), ("assar",), 50,
        "Asse as batatas em pedaços com cebola, alho e óleo. Junte o peito em porções e termine de assar até o frango cozinhar por completo.",
        "Usei peito de frango no lugar da coxinha da asa e mantenho a batata.",
    ),
    FichaDaCasa(
        "Pizza de mussarela", ("pizza de mussarela", "pizza mussarela", "pizza de queijo", "pizza"),
        (("Farinha de trigo", 300, "g"), ("Tomate", 200, "g"), ("Cebola", 40, "g"), ("Alho", 5, "g"),
         ("Queijo mussarela", 150, "g"), ("Óleo de soja", 15, "ml"), ("Sal", 4, "g")),
        ("forno", "forma"), ("assar", "molho"), 40,
        "Misture a farinha com água morna, sal e óleo até formar uma massa lisa e deixe descansar. Cozinhe o tomate picado "
        "com cebola e alho até virar um molho. Abra a massa numa forma, espalhe o molho, cubra com a mussarela e asse em "
        "forno quente até dourar a borda.",
        "Massa rápida feita com a farinha da despensa e molho de tomate fresco; sem azeitona e sem orégano, que não estão na despensa.",
    ),
)


def _pedido(texto: str) -> tuple[bool, str] | None:
    normal = norm_text(texto).strip()
    web = normal.startswith(_WEB)
    if web:
        normal = normal.removeprefix(_WEB).strip()
    if _ADAPTAR.match(normal):
        return web, "um prato equivalente"
    comando = _PEDIDO.match(normal)
    if not comando or "?" in texto:
        return None
    return web, normal[comando.end():].strip(" ,.!")


def pediu_montagem(texto: str) -> bool:
    pedido = _pedido(texto)
    if not pedido:
        return False
    corpo = pedido[1]
    return bool(_MONTAR_PRATO.fullmatch(corpo) or _EQUIVALENTE.fullmatch(corpo)
                or _DESPENSA.search(corpo) or _ingrediente_excluido(corpo))


def _ingrediente_excluido(pedido: str) -> str | None:
    """O ingrediente que ela pediu para tirar nesta montagem ("sem azeitona")."""
    encontrado = _SEM_INGREDIENTE.search(pedido)
    return encontrado[1].strip() if encontrado else None


def _sem_ingrediente(ficha: FichaDaCasa, exclusao: str) -> FichaDaCasa:
    alvo = tokens(exclusao)
    if not alvo:
        return ficha

    def removido(nome: str) -> bool:
        return bool(alvo & tokens(nome)) or norm_text(exclusao) in norm_text(nome)

    ingredientes = tuple(item for item in ficha.ingredientes if not removido(item[0]))
    if len(ingredientes) == len(ficha.ingredientes):
        return ficha
    return replace(ficha, ingredientes=ingredientes,
                   adaptacao=f"{ficha.adaptacao} Retirei {exclusao}, a pedido dela.")


def _relevancia(ficha: FichaDaCasa, falas: list[Fala]) -> int:
    # A menção na última resposta (por exemplo, a ficha que acabou de falhar)
    # tem prioridade sobre alternativas que só apareceram mais atrás na lista.
    textos = [falas[-1].texto, falas[-1].resposta_a, escolhas.lista_da_escolha(falas)]
    textos.extend(fala.resposta_a for fala in reversed(falas[-4:-1]))
    for peso, texto in enumerate(textos):
        normal = norm_text(texto)
        if any(re.search(rf"\b{re.escape(referencia)}\b", normal) for referencia in ficha.referencias):
            return len(textos) - peso
    return 0


def _rascunho(ficha: FichaDaCasa, sessao: str, porcoes: int) -> dict[str, Any]:
    ingredientes = [{"nome": nome, "item_despensa": nome, "quantidade": quantidade * porcoes / 4, "unidade": unidade}
                    for nome, quantidade, unidade in ficha.ingredientes]
    return {"nome": ficha.nome, "porcoes": porcoes, "dados": {
        "origem_montagem": "receita_da_despensa", "ingredientes": ingredientes, "sessao_da_conferencia": sessao,
        "modo_preparo": ficha.preparo, "equipamentos_necessarios": list(ficha.equipamento),
        "tecnicas_necessarias": list(ficha.tecnicas), "tempo_preparo_min": ficha.minutos,
    }}


def _receita_de_referencia(store: Store, sessao: str, nome: str = "", falas: list[Fala] | None = None) -> dict[str, Any] | None:
    """Retoma a ficha lida desta conversa, preservando a original como rascunho."""
    mencionado = escolhas.id_mencionado(falas[-1].texto) if falas else None
    for resumo in reversed(store.receitas()):
        receita = store.obter_receita(resumo["id"])
        dados = receita["dados"]
        if not dados.get("fonte_verificada") or dados.get("sessao_da_conferencia") != sessao:
            continue
        if nome and norm_text(receita["nome"]) != norm_text(nome):
            continue
        if mencionado is not None and dados.get("prato_id") != mencionado:
            continue
        return receita
    return None


def _vincular_origem(rascunho: dict[str, Any], ficha: FichaDaCasa, referencia: dict[str, Any] | None) -> None:
    if referencia is None:
        return
    from .tools import _assinatura_falas, _conferir_preparo_adaptado

    dados = rascunho["dados"]
    original = deepcopy(referencia["dados"]["fonte_verificada"])
    adaptacao = f"Versão da despensa de {original.get('nome') or referencia['nome']}: {ficha.nome}, com as quantidades e o preparo próprios apresentados."
    fidelidade = fonte.conferir(original, dados["ingredientes"], rascunho["porcoes"], adaptacao, ficha.minutos)
    _conferir_preparo_adaptado(fidelidade, ficha.preparo, original.get("modo_preparo"))
    dados.update({"fonte_verificada": original, "fidelidade_fonte": fidelidade, "adaptacao": adaptacao,
                  "proposta_anterior_id": referencia["dados"].get("prato_id"),
                  "preparo_da_adaptacao": ficha.preparo, "versao": f"{ficha.nome} (adaptação)",
                  "adaptacao_proposta_no_contexto": _assinatura_falas(falas_mod.da_sessao(dados["sessao_da_conferencia"]) or [])})
    rascunho["fonte_url"] = referencia.get("fonte_url") or original.get("url")


def _conferir(store: Store, ficha: FichaDaCasa, session_id: str,
              referencia: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    from .tools import _avaliar_prato

    rascunho = _rascunho(ficha, session_id, 4)
    _vincular_origem(rascunho, ficha, referencia)
    conf = _avaliar_prato(store, rascunho["dados"]["ingredientes"], rascunho)
    if conf["veredito"] == "inviavel_por_enquanto" or conf["avisos_operacionais"]:
        return None
    res = conf["resultado"]
    if res["compras"]:
        # Uma mesa de teste menor é mais útil que sugerir uma compra nova.
        rascunho = _rascunho(ficha, session_id, 2)
        _vincular_origem(rascunho, ficha, referencia)
        conf = _avaliar_prato(store, rascunho["dados"]["ingredientes"], rascunho)
        res = conf["resultado"]
    if res["compras"] or res["pendencias"] or res["estimativas"] or any(linha.get("falta") != "nada" for linha in res["itens"]):
        return None
    return rascunho, conf


def _contexto(store: Store, ficha: FichaDaCasa, rascunho: dict[str, Any], conf: dict[str, Any], equivalente: bool) -> str:
    res = conf["resultado"]
    dados = {**rascunho["dados"], "resultado": res, "equipamentos": conf["equipamentos"], "tecnicas": conf["tecnicas"],
             "veredito": conf["veredito"], "bloqueios": conf["bloqueios"],
             "adaptacao": rascunho["dados"].get("adaptacao") or (ficha.adaptacao if equivalente else None)}
    receita_id = store.salvar_receita(ficha.nome, rascunho.get("fonte_url"), rascunho["porcoes"], dados, "conferida", criar_rascunho=True)
    prato_id = store.obter_receita(receita_id)["dados"]["prato_id"]
    if dados.get("proposta_anterior_id"):
        from . import jornada

        jornada.adiar_proposta(store, dados["proposta_anterior_id"], prato_id)
    ingredientes = "; ".join(f"{i['necessario']} de {i['item_despensa']}" for i in res["itens"])
    abertura = "Montei uma versão para você testar" if equivalente else "Montei uma opção para o seu cardápio"
    linhas = [f"{abertura}: **{ficha.nome}** (prato #{prato_id}, rendimento inicial de {rascunho['porcoes']} porções)."]
    original = dados.get("fonte_verificada")
    if original:
        linhas.append(f"Adaptação local de [{original.get('nome') or 'receita lida'}]({rascunho.get('fonte_url')}). As quantidades e o preparo desta versão são próprios da proposta local.")
        linhas.append(dados["adaptacao"])
        diferencas = dados["fidelidade_fonte"].get("diferencas") or []
        if diferencas:
            linhas.append("Diferenças da fonte:\n" + "\n".join("- " + diferenca for diferenca in diferencas))
    else:
        linhas.append("Adaptação local da despensa: as quantidades e o preparo desta versão não foram lidos de uma receita na internet.")
        if equivalente:
            linhas.append(ficha.adaptacao)
    linhas.extend((f"Ingredientes do lote: {ingredientes}.", f"Preparo (cerca de {ficha.minutos} min): {ficha.preparo}",
                   f"Custo dos ingredientes usados: {costing.brl(res['cmv_total'])} por lote. Compras complementares: R$ 0,00.",
                   "A ficha ficou em Minha cozinha › Pratos, como rascunho."))
    if conf["veredito"] == "viavel":
        linhas.append(f"Você gosta dessa versão e aceita preparar o prato #{prato_id}, {ficha.nome}, sem impedimentos?")
    else:
        primeiro = conf["bloqueios"][0]["detalhe"].rstrip(". ")
        linhas.append(f"Antes de aprovar a ficha, só preciso confirmar: {primeiro}" + ("" if primeiro.endswith("?") else "."))
    registrar_resposta(dados["sessao_da_conferencia"], linhas, substituir=bool(original),
                      prioridade=20 if conf["veredito"] == "viavel" else 10)
    return (f"Rascunho com nome, ingredientes e rendimento já gravado (prato_id={prato_id}, receita_id={receita_id}). "
            "Apresente o texto abaixo em português diretamente à Dona Maria, sem chamar ferramentas neste turno. "
            "A ficha atual ainda não comprometeu dinheiro nem estoque, e outros rascunhos continuam disponíveis.\n"
            + "\n".join(linhas))


def _escolher_proposta(store: Store, candidatas: list[tuple[FichaDaCasa, int]], session_id: str, *, equivalente: bool,
                       referencia: dict[str, Any] | None = None) -> str | None:
    propostas = []
    for ficha, relevancia in candidatas:
        if not relevancia:
            continue
        result = _conferir(store, ficha, session_id, referencia)
        if result:
            rascunho, conf = result
            propostas.append((int(conf["veredito"] == "viavel"), relevancia, ficha, rascunho, conf))
    propostas.sort(key=lambda proposta: proposta[:2], reverse=True)
    for _, _, ficha, rascunho, conf in propostas:
        try:
            return _contexto(store, ficha, rascunho, conf, equivalente)
        except ValueError:
            continue  # Uma ficha homônima já em andamento não deve ser sobrescrita.
    return None


def propor_alternativa(store: Store, nome: str, session_id: str, *,
                      referencia_salva: dict[str, Any] | None = None, sem_ingrediente: str | None = None) -> str | None:
    """Após uma conferência bloqueada, prepara só uma variante da mesma família de receita."""
    referencia = Fala("monte um prato equivalente", resposta_a=nome)
    candidatas = [(ficha, _relevancia(ficha, [referencia])) for ficha in FICHAS if norm_text(ficha.nome) != norm_text(nome)]
    if not any(peso for _, peso in candidatas):
        # Uma fonte de frango com acompanhamento indisponível pode inspirar
        # outra composição de frango já conferida, apresentada como adaptação.
        # A semelhança dá só uma candidata: estoque e cozinha são reavaliados.
        familias = {p for p in ("frango", "arroz", "batata", "macarrao", "espaguete") if re.search(rf"\b{p}\b", norm_text(nome))}
        candidatas = [(ficha, len(familias & set(norm_text(ficha.nome).split()))) for ficha, _ in candidatas]
    if sem_ingrediente:
        candidatas = [(ficha, peso) for ficha, peso in candidatas
                      if not any(norm_text(nome_da_medida(ing)) == sem_ingrediente for ing, _, _ in ficha.ingredientes)]
    referencia_da_fonte = referencia_salva if (referencia_salva or {}).get("dados", {}).get("fonte_verificada") else None
    return _escolher_proposta(store, candidatas, session_id, equivalente=True,
                             referencia=referencia_da_fonte or _receita_de_referencia(store, session_id, nome))


def propor_turno(store: Store, falas: list[Fala], session_id: str, *, permitir_adaptacao_local: bool = False) -> str | None:
    if not falas or not pediu_montagem(falas[-1].texto):
        return None
    _, pedido = _pedido(falas[-1].texto)
    equivalente = bool(_EQUIVALENTE.fullmatch(pedido))
    exclusao = _ingrediente_excluido(pedido)
    da_despensa = bool(_DESPENSA.search(pedido)) or exclusao is not None
    local = equivalente or da_despensa
    # A descoberta inicial sempre começa na web (§2.1), com ou sem o globo.
    # As fichas locais servem para adaptação pedida, pedido da despensa ou contingência declarada.
    if not local and not permitir_adaptacao_local:
        return None
    referencia = _receita_de_referencia(store, session_id, falas=falas) if equivalente else None
    contexto = falas
    if referencia:
        contexto = [*falas, Fala(falas[-1].texto, resposta_a=referencia["nome"])]
    if local:
        candidatas = [(ficha, _relevancia(ficha, contexto)) for ficha in FICHAS]
        maior = max(peso for _, peso in candidatas)
        if maior:
            candidatas = [(ficha, peso) for ficha, peso in candidatas if peso == maior]
        if exclusao:
            candidatas = [(_sem_ingrediente(ficha, exclusao), peso) for ficha, peso in candidatas]
    else:
        candidatas = [(ficha, 1) for ficha in FICHAS]
    proposta = _escolher_proposta(store, candidatas, session_id, equivalente=local, referencia=referencia)
    if proposta:
        return proposta
    if equivalente:
        return ("Ela pediu uma adaptação da receita anterior. Recupere os ingredientes na conversa e consulte despensa_buscar. "
                "Monte uma única versão sem compras, com nome descritivo, medidas e rendimento inicial. "
                "Confira a nova ficha com receita_conferir_ingredientes, usando o nome novo. Se não houver uma troca compatível, "
                "aponte o ingrediente ou preparo específico e ofereça a alternativa mais próxima que você conseguir verificar.")
    if da_despensa:
        return ("Ela pediu para montar um prato a partir da despensa" + (f", sem {exclusao}" if exclusao else "") + ". "
                "Consulte despensa_buscar e monte uma única versão sem compras, com nome descritivo, medidas e rendimento inicial. "
                "Confira a nova ficha com receita_conferir_ingredientes. Se faltar algum ingrediente, proponha a troca mais próxima "
                "que você conseguir verificar, sem inventar fonte.")
    return None
