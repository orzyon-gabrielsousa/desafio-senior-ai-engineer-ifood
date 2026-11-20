"""Perfil do negócio da Dona Maria: onde a loja fica (país, estado, cidade e zona), que tipo de negócio é e que tipo de alimentação ela faz.

É a base das sugestões da consultora: pratos que vendem bem para aquele lugar (o Analytics prioriza pela zona e pela cidade da loja) e para aquele tipo de
negócio. Os **pratos** em si (o que a cozinha dela faz, com quais equipamentos, utensílios e habilidades) moram em Minha cozinha (``cozinha.py``), não aqui.

Dois caminhos gravam o mesmo registro (``config`` do SQLite): a Dona Maria, pela tela Meu negócio, e a consultora, pela conversa (``negocio_salvar``, que só
grava o que ela disse, como a trava de evidência do perfil da cozinha). As opções fechadas usam o catálogo abaixo. A cidade e o objetivo são texto livre.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .units import norm_text

PAIS = "Brasil"

TIPOS: dict[str, str] = {
    "restaurante": "Restaurante",
    "restaurante_hotel": "Restaurante de hotel",
    "marmitaria": "Marmitaria",
    "lanchonete": "Lanchonete",
    "confeitaria": "Confeitaria",
    "pizzaria": "Pizzaria",
    "padaria": "Padaria",
    "outro": "Outro",
}

# Tipo de alimentação que ela faz -> (como a tela mostra, categoria dos pratos na base: cozinha-e-equipamentos/pratos.md e Analytics).
# É a ponte entre o negócio e Minha cozinha: os pratos dessas categorias abrem primeiro na lista de pratos da cozinha.
ALIMENTACAO: dict[str, tuple[str, str]] = {
    "caseira": ("Comida caseira e prato do dia", "Prato do dia"),
    "marmita": ("Marmitas", "Marmita"),
    "pizza": ("Pizzas", "Pizza"),
    "massas": ("Massas", "Massas"),
    "lanches": ("Lanches", "Lanche"),
    "salgados": ("Salgados", "Salgado"),
    "doces": ("Doces e sobremesas", "Sobremesa"),
    "peixes": ("Peixes e frutos do mar", "Peixes"),
    "japonesa": ("Comida japonesa", "Japonesa"),
    "saudavel": ("Saudável e saladas", "Saudável"),
}

# Como ela diria cada tipo e cada alimentação (a trava de evidência procura essas palavras nas falas dela).
TERMOS_TIPO: dict[str, tuple[str, ...]] = {
    "restaurante": ("restaurante",),
    "restaurante_hotel": ("restaurante de hotel", "restaurante do hotel", "hotel"),
    "marmitaria": ("marmitaria", "marmita", "marmitex"),
    "lanchonete": ("lanchonete", "lanche"),
    "confeitaria": ("confeitaria", "doceria", "bolos", "doces"),
    "pizzaria": ("pizzaria", "pizza"),
    "padaria": ("padaria", "panificadora"),
}
TERMOS_ALIMENTACAO: dict[str, tuple[str, ...]] = {
    "caseira": ("comida caseira", "caseira", "prato feito", "prato do dia", "pf", "comida de casa", "almoco"),
    "marmita": ("marmita", "marmitas", "marmitex"),
    "pizza": ("pizza", "pizzas"),
    "massas": ("massa", "massas", "macarrao", "lasanha", "espaguete"),
    "lanches": ("lanche", "lanches", "hamburguer", "sanduiche", "x salada"),
    "salgados": ("salgado", "salgados", "coxinha", "pastel", "pao de queijo", "empada"),
    "doces": ("doce", "doces", "bolo", "bolos", "sobremesa", "sobremesas", "confeitaria", "pudim", "brigadeiro", "acai"),
    "peixes": ("peixe", "peixes", "frutos do mar", "moqueca", "camarao", "pescado"),
    "japonesa": ("japonesa", "sushi", "sashimi", "poke"),
    "saudavel": ("saudavel", "fit", "salada", "saladas", "vegetariana", "vegana", "light"),
}

_CATEGORIA_POR_TERMO = {termo: categoria for categoria, termos in TERMOS_ALIMENTACAO.items() for termo in termos}
_MENCAO_ALIMENTACAO = re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(_CATEGORIA_POR_TERMO, key=len, reverse=True))) + r")\b")

# UF -> (nome, região). A região sai do estado: ninguém precisa informar duas vezes.
ESTADOS: dict[str, tuple[str, str]] = {
    "AC": ("Acre", "Norte"), "AL": ("Alagoas", "Nordeste"), "AP": ("Amapá", "Norte"), "AM": ("Amazonas", "Norte"),
    "BA": ("Bahia", "Nordeste"), "CE": ("Ceará", "Nordeste"), "DF": ("Distrito Federal", "Centro-Oeste"),
    "ES": ("Espírito Santo", "Sudeste"), "GO": ("Goiás", "Centro-Oeste"), "MA": ("Maranhão", "Nordeste"),
    "MT": ("Mato Grosso", "Centro-Oeste"), "MS": ("Mato Grosso do Sul", "Centro-Oeste"), "MG": ("Minas Gerais", "Sudeste"),
    "PA": ("Pará", "Norte"), "PB": ("Paraíba", "Nordeste"), "PR": ("Paraná", "Sul"), "PE": ("Pernambuco", "Nordeste"),
    "PI": ("Piauí", "Nordeste"), "RJ": ("Rio de Janeiro", "Sudeste"), "RN": ("Rio Grande do Norte", "Nordeste"),
    "RS": ("Rio Grande do Sul", "Sul"), "RO": ("Rondônia", "Norte"), "RR": ("Roraima", "Norte"), "SC": ("Santa Catarina", "Sul"),
    "SP": ("São Paulo", "Sudeste"), "SE": ("Sergipe", "Nordeste"), "TO": ("Tocantins", "Norte"),
}

ZONAS: dict[str, str] = {
    "centro": "Centro",
    "norte": "Zona Norte",
    "sul": "Zona Sul",
    "leste": "Zona Leste",
    "oeste": "Zona Oeste",
}

CAMPOS = ("tipo", "alimentacao", "estado", "cidade", "zona", "objetivo")


def opcoes() -> dict[str, list[dict[str, str]]]:
    """Catálogo para a tela montar os campos de seleção."""
    return {
        "tipos": [{"id": k, "label": v} for k, v in TIPOS.items()],
        "alimentacao": [{"id": k, "label": v[0]} for k, v in ALIMENTACAO.items()],
        "paises": [{"id": PAIS, "label": PAIS}],
        "estados": [{"id": uf, "label": nome, "regiao": regiao} for uf, (nome, regiao) in sorted(ESTADOS.items(), key=lambda kv: norm_text(kv[1][0]))],
        "zonas": [{"id": k, "label": v} for k, v in ZONAS.items()],
    }


def _texto(valor: Any, limite: int) -> str:
    return " ".join(str(valor or "").split())[:limite]


def normalizar(dados: Any) -> dict[str, Any]:
    """Valida o que veio da tela (ou da conversa) e devolve só os campos conhecidos. Nenhum campo é obrigatório: ela pode preencher aos poucos.
    Levanta ``ValueError`` com mensagem para a pessoa."""
    if not isinstance(dados, dict):
        raise ValueError("o perfil do negócio precisa ser um objeto")
    tipo = str(dados.get("tipo") or "").strip()
    if tipo and tipo not in TIPOS:
        raise ValueError("escolha o tipo do negócio na lista")
    bruto = dados.get("alimentacao") or []
    if not isinstance(bruto, list):
        raise ValueError("'alimentacao' precisa ser uma lista")
    alimentacao: list[str] = []
    for item in bruto:
        chave = str(item).strip()
        if chave not in ALIMENTACAO:
            raise ValueError(f"'{chave}' não está na lista de tipos de alimentação")
        if chave not in alimentacao:
            alimentacao.append(chave)
    pais = str(dados.get("pais") or PAIS).strip()
    if pais != PAIS:
        raise ValueError(f"por enquanto só o {PAIS}: o Analytics ainda não tem dados de outros países")
    estado = str(dados.get("estado") or "").strip().upper()
    if estado and estado not in ESTADOS:
        raise ValueError("escolha o estado na lista")
    zona = str(dados.get("zona") or "").strip()
    if zona and zona not in ZONAS:
        raise ValueError("escolha a zona na lista")
    return {"tipo": tipo, "alimentacao": alimentacao, "pais": PAIS, "estado": estado, "cidade": _texto(dados.get("cidade"), 60),
            "zona": zona, "objetivo": _texto(dados.get("objetivo"), 240)}


# ----------------------------------------------------------------------------- do que ela diz para o catálogo
def _chave(valor: Any) -> str:
    return norm_text(str(valor or "")).replace(" ", "")


def resolver_tipo(texto: Any) -> Optional[str]:
    """'Restaurante de hotel', 'restaurante_hotel' e 'restaurante do hotel' são o mesmo tipo; ``None`` se não for nenhum da lista."""
    alvo = _chave(texto)
    for chave, rotulo in TIPOS.items():
        if alvo in (_chave(chave), _chave(rotulo)):
            return chave
    for chave, termos in TERMOS_TIPO.items():
        if alvo in {_chave(t) for t in termos}:
            return chave
    return None


def resolver_alimentacao(texto: Any) -> Optional[str]:
    alvo = _chave(texto)
    for chave, (rotulo, _) in ALIMENTACAO.items():
        if alvo in (_chave(chave), _chave(rotulo)):
            return chave
    for chave, termos in TERMOS_ALIMENTACAO.items():
        if alvo in {_chave(t) for t in termos}:
            return chave
    return None


def categorias_mencionadas(texto: Any) -> list[str]:
    """IDs do catálogo citados como opções, na ordem em que aparecem no texto."""
    if not isinstance(texto, str):
        return []
    normal = norm_text(texto)
    categorias: list[str] = []
    ultimo_fim = 0
    for mencao in _MENCAO_ALIMENTACAO.finditer(normal):
        termo = mencao[0]
        anterior = normal[ultimo_fim:mencao.start()].strip()
        trecho = re.split(r"[,.;:/]|\b(?:e|ou)\b", anterior)[-1].strip()
        if re.search(r"\b(?:nao|sem|em vez de|no lugar de|exceto)(?: (?:quero|pretendo|vou|vendo|vender|servir|fazer))*$", trecho):
            continue
        # Uma pizza de camarão ou com massa integral continua sendo pizza.
        if categorias and (re.match(r"^(?:(?:caseir[ao]s?|saudave(?:l|is)|fit|light) )*(?:de|da|do|com|rechead[ao]s? com)\b", trecho)
                           or not anterior and termo in {"caseira", "doce", "doces", "saudavel", "fit", "light"}):
            continue
        categoria = _CATEGORIA_POR_TERMO[termo]
        if categoria not in categorias:
            categorias.append(categoria)
        ultimo_fim = mencao.end()
    return categorias


def resolver_zona(texto: Any) -> Optional[str]:
    alvo = norm_text(str(texto or ""))
    for tirar in ("zona ", " da cidade", "zona"):
        alvo = alvo.replace(tirar, " ")
    alvo = alvo.strip()
    return alvo if alvo in ZONAS else None


def resolver_estado(texto: Any) -> Optional[str]:
    """A sigla ou o nome do estado ('sp', 'São Paulo', 'sao paulo zona leste' → SP). O nome mais longo ganha; ambíguo ou desconhecido é ``None``."""
    bruto = str(texto or "").strip()
    if bruto.upper() in ESTADOS:
        return bruto.upper()
    palavras = f" {norm_text(bruto)} "
    achados = [(len(norm_text(nome)), uf) for uf, (nome, _) in ESTADOS.items() if f" {norm_text(nome)} " in palavras]
    return max(achados)[1] if achados else None


def categorias_da_loja(negocio: Optional[dict[str, Any]]) -> list[str]:
    """As categorias de pratos (da base) que o tipo de alimentação dela cobre, na ordem em que ela as marcou."""
    return [ALIMENTACAO[k][1] for k in (negocio or {}).get("alimentacao") or [] if k in ALIMENTACAO]


# ----------------------------------------------------------------------------- onde a loja fica
def localizacao(negocio: Optional[dict[str, Any]]) -> dict[str, str]:
    """Onde a loja fica, do país à zona. O que não foi informado vem vazio (e o Analytics usa só o que há)."""
    n = negocio or {}
    uf = str(n.get("estado") or "").upper()
    nome, regiao = ESTADOS.get(uf, ("", ""))
    return {"pais": PAIS, "regiao": regiao, "estado": uf, "estado_nome": nome,
            "cidade": str(n.get("cidade") or ""), "zona": ZONAS.get(str(n.get("zona") or ""), "")}


def descrever_local(negocio: Optional[dict[str, Any]]) -> str:
    local = localizacao(negocio)
    if not local["estado"]:
        return ""
    partes = [p for p in (local["zona"], f"{local['cidade']}/{local['estado']}" if local["cidade"] else local["estado_nome"]) if p]
    return ", ".join(partes) + f" (região {local['regiao']})"


def _alimentacao(negocio: dict[str, Any]) -> list[str]:
    return [ALIMENTACAO[k][0] for k in negocio.get("alimentacao") or [] if k in ALIMENTACAO]


def descrever(negocio: Optional[dict[str, Any]]) -> str:
    """Frase curta para a consultora usar nas sugestões."""
    n = negocio or {}
    if not any(n.get(campo) for campo in ("tipo", "alimentacao", "estado", "cidade", "objetivo")):
        return "A Dona Maria ainda não preencheu o perfil do negócio (menu: Meu negócio) nem contou na conversa."
    partes = [TIPOS.get(n.get("tipo", ""), "tipo de negócio não informado")]
    comida = _alimentacao(n)
    partes.append("faz: " + "; ".join(comida) if comida else "tipo de alimentação não informado")
    local = descrever_local(n)
    partes.append("local: " + local if local else "local da loja não informado (Meu negócio)")
    if n.get("objetivo"):
        partes.append("objetivo informado: " + n["objetivo"])
    return ". ".join(partes) + "."


def resumo_para_ela(negocio: Optional[dict[str, Any]]) -> str:
    """O que a consultora repete para a Dona Maria depois de salvar: só o que está no banco, sem inventar."""
    n = negocio or {}
    partes = []
    if n.get("tipo") in TIPOS:
        partes.append("É um negócio do tipo " + TIPOS[n["tipo"]].lower())
    comida = _alimentacao(n)
    if comida:
        partes.append("faz " + ", ".join(c.lower() for c in comida))
    local = descrever_local(n)
    if local:
        partes.append("fica em " + local)
    return "; ".join(partes) + "." if partes else "Ainda não tenho nada do negócio salvo."


# ----------------------------------------------------------------------------- o que ainda falta perguntar
def pendencias(negocio: Optional[dict[str, Any]]) -> list[dict[str, str]]:
    """O que a consultora ainda precisa saber do negócio (a zona é pedida junto com o lugar, mas não trava: nem toda cidade tem)."""
    n = negocio or {}
    saida: list[dict[str, str]] = []
    if not n.get("tipo"):
        saida.append({"grupo": "negocio", "campo": "tipo", "titulo": "Tipo de negócio",
                      "pergunta": "É um " + ", ".join(v.lower() for k, v in TIPOS.items() if k != "outro") + " ou outro tipo de negócio?"})
    if not n.get("alimentacao"):
        saida.append({"grupo": "negocio", "campo": "alimentacao", "titulo": "Tipo de alimentação que você faz",
                      "pergunta": "Que tipo de comida você faz (pode ser mais de um): " + ", ".join(v[0].lower() for v in ALIMENTACAO.values()) + "?"})
    if not (n.get("estado") and n.get("cidade")):
        saida.append({"grupo": "negocio", "campo": "local", "titulo": "Onde fica a loja",
                      "pergunta": "Em que cidade e estado fica o seu negócio? A zona da cidade é opcional."})
    return saida


def blocos_de_perguntas(pendencias_: list[dict[str, str]]) -> list[tuple[str, str]]:
    """(título, pergunta) de cada coisa que falta do negócio."""
    return [(p["titulo"], p["pergunta"]) for p in pendencias_]
