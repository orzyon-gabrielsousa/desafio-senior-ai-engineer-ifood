"""Perfil do negócio da Dona Maria: que tipo de comércio é, o que ela vende e onde fica a loja.

É a base das sugestões da consultora: pratos que vendem bem para aquele tipo de negócio e, no Analytics,
os pratos mais vendidos priorizados pela zona e pela cidade da loja. A Dona Maria edita pela tela; a
consultora só lê. As opções são fechadas (a tela mostra seleção, não texto livre), então o que chega ao
banco sempre bate com o catálogo abaixo. Só a cidade é texto livre.
"""

from __future__ import annotations

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

VENDE: dict[str, str] = {
    "prato_do_dia": "Arroz, feijão e prato do dia",
    "carnes": "Carnes e grelhados",
    "marmitas": "Marmitas",
    "bolos": "Bolos e doces",
    "pizzas": "Pizzas",
    "lanches": "Lanches e salgados",
    "bebidas": "Sucos e bebidas",
    "saladas": "Saladas e pratos leves",
}

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


def opcoes() -> dict[str, list[dict[str, str]]]:
    """Catálogo para a tela montar os campos de seleção."""
    return {
        "tipos": [{"id": k, "label": v} for k, v in TIPOS.items()],
        "vende": [{"id": k, "label": v} for k, v in VENDE.items()],
        "estados": [{"id": uf, "label": nome, "regiao": regiao} for uf, (nome, regiao) in sorted(ESTADOS.items(), key=lambda kv: norm_text(kv[1][0]))],
        "zonas": [{"id": k, "label": v} for k, v in ZONAS.items()],
    }


def _texto(valor: Any, limite: int) -> str:
    return " ".join(str(valor or "").split())[:limite]


def normalizar(dados: Any) -> dict[str, Any]:
    """Valida o que veio da tela e devolve só os campos conhecidos. Levanta ``ValueError`` com mensagem para a pessoa."""
    if not isinstance(dados, dict):
        raise ValueError("o perfil do negócio precisa ser um objeto")
    tipo = str(dados.get("tipo") or "").strip()
    if tipo not in TIPOS:
        raise ValueError("escolha o tipo do negócio na lista")
    bruto = dados.get("vende") or []
    if not isinstance(bruto, list):
        raise ValueError("'vende' precisa ser uma lista")
    vende: list[str] = []
    for item in bruto:
        chave = str(item).strip()
        if chave not in VENDE:
            raise ValueError(f"'{chave}' não está na lista do que o negócio vende")
        if chave not in vende:
            vende.append(chave)
    estado = str(dados.get("estado") or "").strip().upper()
    if estado and estado not in ESTADOS:
        raise ValueError("escolha o estado na lista")
    zona = str(dados.get("zona") or "").strip()
    if zona and zona not in ZONAS:
        raise ValueError("escolha a zona na lista")
    return {
        "tipo": tipo,
        "vende": vende,
        "publico": _texto(dados.get("publico"), 80),
        "observacoes": _texto(dados.get("observacoes"), 400),
        "estado": estado,
        "cidade": _texto(dados.get("cidade"), 60),
        "zona": zona,
    }


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


def descrever(negocio: Optional[dict[str, Any]]) -> str:
    """Frase curta para a consultora usar nas sugestões."""
    if not negocio:
        return "A Dona Maria ainda não preencheu o perfil do negócio (menu: Meu negócio)."
    partes = [TIPOS.get(negocio.get("tipo", ""), "Negócio")]
    vende = [VENDE[k] for k in negocio.get("vende") or [] if k in VENDE]
    if vende:
        partes.append("vende: " + "; ".join(vende))
    if negocio.get("publico"):
        partes.append("público: " + negocio["publico"])
    local = descrever_local(negocio)
    partes.append("local: " + local if local else "local da loja não informado (Meu negócio)")
    if negocio.get("observacoes"):
        partes.append("obs.: " + negocio["observacoes"])
    return ". ".join(partes) + "."
