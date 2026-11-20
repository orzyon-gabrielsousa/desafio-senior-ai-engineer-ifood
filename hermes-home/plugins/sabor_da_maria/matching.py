"""Casamento aproximado entre nomes de ingredientes de receitas e itens da despensa.

Receitas da web escrevem "queijo ralado", "cheiro-verde", "1 cebola média picada";
a planilha tem "Queijo parmesão ralado", "Salsinha (cheiro-verde)", "Cebola". O
casamento combina três sinais: sinônimos de cozinha brasileira, sobreposição de
tokens (Jaccard) e similaridade de caracteres (``difflib``). O resultado traz o
score e um flag ``confiavel`` para o agente confirmar com a Dona Maria quando
houver dúvida, em vez de assumir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .units import norm_text

_STOPWORDS = {
    "de", "da", "do", "das", "dos", "em", "e", "com", "tipo", "a", "o", "para", "por", "um", "uma",
    "fresco", "fresca", "picado", "picada", "picadinho", "picadinha", "cortado", "cortada", "fatiado",
    "fatiada", "moido", "moida", "a", "gosto", "quanto", "baste", "q", "b", "sem", "pele", "ossos", "osso",
    "cozido", "cozida", "cru", "crua", "grande", "pequeno", "pequena", "medio", "media", "maduro", "madura",
    "integral", "liquido", "liquida", "po", "extra", "virgem", "1", "2", "3", "cheia", "rasa",
}
_SINGULAR = {"ovos": "ovo", "tomates": "tomate", "cebolas": "cebola", "batatas": "batata", "alcaparras": "alcaparra",
             "amendoas": "amendoa", "dentes": "dente", "folhas": "folha", "queijos": "queijo", "feijoes": "feijao"}

# frase normalizada da receita -> frase que descreve o item da despensa
SINONIMOS: dict[str, str] = {
    "cheiro verde": "salsinha cheiro verde", "salsa": "salsinha", "salsinha": "salsinha cheiro verde",
    "fuba": "polenta fuba", "polenta": "polenta fuba", "farinha de milho": "polenta fuba",
    "curcuma": "acafrao em po curcuma", "acafrao": "acafrao em po curcuma", "acafrao da terra": "acafrao em po curcuma",
    "patinho": "carne moida patinho", "carne moida": "carne moida patinho",
    "acem": "carne de panela acem", "carne de panela": "carne de panela acem", "musculo": "carne de panela acem",
    "alcatra": "miolo de alcatra", "bife": "miolo de alcatra", "carne": "carne de panela acem",
    "mucarela": "queijo mussarela", "musarela": "queijo mussarela", "mozarela": "queijo mussarela",
    "mozzarella": "queijo mussarela", "mussarela": "queijo mussarela", "queijo": "queijo mussarela",
    "parmesao": "queijo parmesao ralado", "queijo ralado": "queijo parmesao ralado", "queijo parmesao": "queijo parmesao ralado",
    "leite em po": "leite ninho em po", "ninho": "leite ninho em po", "leite ninho": "leite ninho em po",
    "chantili": "chantilly", "creme chantilly": "chantilly", "chantilly": "chantilly",
    "oleo": "oleo de soja", "oleo vegetal": "oleo de soja", "oleo de soja": "oleo de soja",
    "azeite": "azeite de oliva extra virgem", "azeite de oliva": "azeite de oliva extra virgem",
    "balsamico": "aceto balsamico", "vinagre balsamico": "aceto balsamico", "aceto": "aceto balsamico",
    "amendoa": "amendoa fatiada", "amendoas laminadas": "amendoa fatiada", "amendoa laminada": "amendoa fatiada",
    "caldo de carne": "caldo de carne tempero", "tablete de caldo": "caldo de carne tempero", "caldo": "caldo de carne tempero",
    "trigo": "farinha de trigo", "farinha": "farinha de trigo", "farinha de mandioca": "farinha de mandioca", "farofa": "farinha de mandioca",
    "batata inglesa": "batata", "batata": "batata",
    "arroz": "arroz branco tipo 1", "arroz branco": "arroz branco tipo 1",
    "ovo": "ovos", "ovos": "ovos", "clara": "ovos", "gema": "ovos",
    "macarrao": "macarrao espaguete", "espaguete": "macarrao espaguete", "massa": "macarrao espaguete", "spaghetti": "macarrao espaguete",
    "frango": "peito de frango", "file de frango": "peito de frango", "peito": "peito de frango", "frango desfiado": "peito de frango",
    "canela": "canela em po", "adocante": "adocante liquido",
    "cobertura": "cobertura de chocolate", "calda de chocolate": "cobertura de chocolate", "chocolate": "cobertura de chocolate",
    "leite": "leite integral", "manteiga": "manteiga", "couve manteiga": "couve", "couve": "couve",
    "alcaparra": "alcaparras", "bacon": "bacon", "sal": "sal", "acucar": "acucar", "cebola": "cebola", "alho": "alho",
    "tomate": "tomate", "feijao": "feijao", "feijao carioca": "feijao carioquinha", "feijao preto": "feijao preto",
    "leite condensado": "", "creme de leite": "", "requeijao": "",  # existem na vida real, não na despensa
}


def tokens(text: str) -> set[str]:
    out: set[str] = set()
    for tok in norm_text(text).replace("(", " ").replace(")", " ").replace(",", " ").split():
        tok = _SINGULAR.get(tok, tok)
        if tok in _STOPWORDS or len(tok) < 2:
            continue
        out.add(tok)
    return out


def canonical_query(text: str) -> str:
    """Aplica sinônimos à frase inteira ou à maior sub-frase conhecida."""
    n = norm_text(text).replace("(", " ").replace(")", " ")
    n = " ".join(_SINGULAR.get(w, w) for w in n.split())
    if n in SINONIMOS:
        return SINONIMOS[n] or n
    words = n.split()
    for size in range(min(4, len(words)), 0, -1):
        for start in range(0, len(words) - size + 1):
            phrase = " ".join(words[start:start + size])
            if phrase in SINONIMOS:
                mapped = SINONIMOS[phrase]
                return mapped if mapped else n
    return n


@dataclass(frozen=True)
class PreparoCaseiro:
    padrao: str
    materia_prima: str
    rendimento: float
    minutos: int
    equipamentos: tuple[str, ...]
    tecnicas: tuple[str, ...]
    preparo: str


# Rendimentos locais de planejamento, não medidas da publicação. Só preparos
# simples de um ingrediente com água; temperos adicionais pertencem à ficha.
PREPAROS_CASEIROS = (
    PreparoCaseiro(r"(?:molho|pure|passata) de tomate", "Tomate", 0.8, 20,
                  ("fogão",), (),
                  "Pique e cozinhe os tomates com água potável apenas se necessário; amasse e reduza até obter o molho."),
    PreparoCaseiro(r"pure de batata", "Batata", 0.85, 25,
                  ("fogão",), (),
                  "Descasque, cozinhe e amasse a batata; use um pouco da água do cozimento para um purê simples, sem leite ou manteiga adicionais."),
    PreparoCaseiro(r"arroz(?: branco)? cozido", "Arroz branco tipo 1", 2.5, 20,
                  ("fogão",), ("arroz e feijão",),
                  "Cozinhe o arroz cru em água potável até o ponto; o peso cozido inclui a água absorvida."),
    PreparoCaseiro(r"feijao(?: carioca| carioquinha)? cozido", "Feijão carioquinha", 2.0, 40,
                  ("fogão", "panela de pressão"), ("arroz e feijão",),
                  "Cozinhe o feijão em água potável na panela de pressão até ficar macio; considere o peso escorrido."),
    PreparoCaseiro(r"feijao preto cozido", "Feijão preto", 2.0, 40,
                  ("fogão", "panela de pressão"), ("arroz e feijão",),
                  "Cozinhe o feijão preto em água potável na panela de pressão até ficar macio; considere o peso escorrido."),
    PreparoCaseiro(r"(?:peito de )?frango (?:(?:cozido e |cozido )?desfiado|cozido)", "Peito de frango", 0.75, 25,
                  ("fogão",), ("refogar",),
                  "Cozinhe o peito de frango completamente em água potável e desfie se a receita pedir; nunca use o frango cru como produto pronto."),
)


def preparo_caseiro_entre(ingrediente: str, candidato: str) -> PreparoCaseiro | None:
    """Transformação direcional, separada do matching: matéria-prima não é produto pronto."""
    nome = re.sub(r"\s+(?:a gosto|quanto baste)$", "", norm_text(ingrediente))
    nome = re.sub(r"^\d+(?:[.,]\d+)?\s*(?:kg|g|ml|l)\s+(?:de\s+)?", "", nome)
    nome = " ".join(_SINGULAR.get(w, w) for w in nome.split() if w not in {"caseiro", "caseira"})
    for preparo in PREPAROS_CASEIROS:
        if (re.fullmatch(preparo.padrao, nome)
                and tokens(candidato) == tokens(preparo.materia_prima)
                and not re.search(r"\b(?:cozid[oa]|desfiad[oa]|pront[oa]|industrializad[oa])\b", norm_text(candidato))):
            return preparo
    return None


def _corte_frango(texto: str) -> str | None:
    nome = norm_text(texto)
    if re.search(r"\b(?:coxinhas? da asa|drumettes?)\b", nome):
        return "coxinha_da_asa"
    if not re.search(r"\bfrango\b", nome):
        return None
    for corte, padrao in (("peito", r"peitos?|files?"), ("sobrecoxa", r"sobrecoxas?"),
                          ("coxa", r"coxas?"), ("asa", r"asas?|tulipas?"), ("inteiro", r"inteiros?")):
        if re.search(r"\b(?:" + padrao + r")\b", nome):
            return corte
    return None


def cortes_incompativeis(ingrediente: str, candidato: str) -> bool:
    corte = _corte_frango(ingrediente)
    return corte is not None and corte != _corte_frango(candidato)


def formas_incompativeis(ingrediente: str, candidato: str) -> bool:
    a, b = tokens(ingrediente), tokens(candidato)
    for principal, preparos in (("tomate", {"molho", "extrato", "passata", "pure", "pelado", "seco"}),
                               ("batata", {"palha", "chips", "congelada", "pure"})):
        if principal in a and principal in b:
            return a & preparos != b & preparos
    return False


def score(query: str, candidate: str) -> float:
    if cortes_incompativeis(query, candidate) or formas_incompativeis(query, candidate):
        return 0.0
    q_can = canonical_query(query)
    q_tok = tokens(q_can) | tokens(query)
    c_tok = tokens(candidate)
    if not q_tok or not c_tok:
        return 0.0
    jaccard = len(q_tok & c_tok) / len(q_tok | c_tok)
    ratio = SequenceMatcher(None, norm_text(q_can), norm_text(candidate)).ratio()
    value = 0.55 * jaccard + 0.45 * ratio
    if tokens(q_can) and (tokens(q_can) <= c_tok or c_tok <= tokens(q_can)):
        value += 0.2
    if norm_text(q_can) == norm_text(candidate):
        value = 1.0
    return min(1.0, round(value, 3))


def best_matches(query: str, candidates: list[str], *, limit: int = 3, minimum: float = 0.3) -> list[dict]:
    scored = [(score(query, c), c) for c in candidates]
    scored = [s for s in scored if s[0] >= minimum]
    scored.sort(key=lambda s: s[0], reverse=True)
    return [{"nome": name, "score": value, "confiavel": value >= 0.6} for value, name in scored[:limit]]
