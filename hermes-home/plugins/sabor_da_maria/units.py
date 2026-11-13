"""Unidades da despensa e conversão de medidas caseiras de receita.

Duas responsabilidades:

1. ``parse_pantry_unit``: interpreta a coluna ``Unidade`` da planilha. A planilha
   mistura unidades simples (``kg``, ``L``, ``un``) com embalagens compostas
   (``balde 2kg``, ``un 500g``, ``un 500ml``). Tudo é normalizado para três bases:
   ``kg``, ``L`` e ``un``. Assim ``1 un 500g`` de chantilly vira ``0,5 kg`` e o
   custo unitário passa a ser R$/kg, comparável com qualquer receita.

2. ``to_base``: converte a medida de uma receita (``2 colheres de sopa``,
   ``1 xícara``, ``3 dentes``) para a unidade base do item da despensa, usando
   tabelas de densidade e de peso por unidade. Toda conversão que depende de
   premissa volta marcada como ``estimativa`` com a premissa escrita, para o
   agente mostrar à Dona Maria em vez de esconder.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

BASE_UNITS = ("kg", "L", "un")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c))


def norm_text(text: str) -> str:
    text = strip_accents(text).lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9,./ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- planilha
_SIMPLE_UNITS: dict[str, tuple[str, float]] = {
    "kg": ("kg", 1.0), "quilo": ("kg", 1.0), "kilo": ("kg", 1.0),
    "g": ("kg", 0.001), "grama": ("kg", 0.001), "gr": ("kg", 0.001),
    "l": ("L", 1.0), "litro": ("L", 1.0), "lt": ("L", 1.0),
    "ml": ("L", 0.001), "mililitro": ("L", 0.001),
    "un": ("un", 1.0), "unidade": ("un", 1.0), "unid": ("un", 1.0), "u": ("un", 1.0), "pc": ("un", 1.0),
    "dz": ("un", 12.0), "duzia": ("un", 12.0),
}
_PACK_RE = re.compile(r"^(?P<pack>[a-z]+)?\s*(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|g|l|ml|lt)$")


@dataclass(frozen=True)
class UnitSpec:
    """Como uma unidade da planilha se traduz para a base: ``1 original = factor base``."""

    base: str
    factor: float
    original: str
    pack_label: str = ""

    @property
    def is_package(self) -> bool:
        return bool(self.pack_label) or self.factor not in (1.0, 0.001)


def parse_pantry_unit(label: str) -> UnitSpec:
    n = norm_text(label)
    if n in _SIMPLE_UNITS:
        base, factor = _SIMPLE_UNITS[n]
        return UnitSpec(base, factor, str(label))
    m = _PACK_RE.match(n)
    if m:
        qty = float(m.group("qty").replace(",", "."))
        base, factor = _SIMPLE_UNITS[m.group("unit")]
        return UnitSpec(base, qty * factor, str(label), m.group("pack") or "")
    raise ValueError(f"Unidade desconhecida na planilha: {label!r}")


# --------------------------------------------------------------------------- receitas
_PLURALS = {
    "colheres": "colher", "xicaras": "xicara", "dentes": "dente", "fatias": "fatia", "folhas": "folha",
    "unidades": "unidade", "gramas": "grama", "litros": "litro", "ovos": "ovo", "macos": "maco",
    "copos": "copo", "latas": "lata", "pitadas": "pitada", "bifes": "bife", "files": "file",
    "pedacos": "pedaco", "cabecas": "cabeca", "quilos": "quilo", "ramos": "ramo", "talos": "talo",
    "postas": "posta", "pacotes": "pacote", "caixas": "caixa", "pecas": "peca", "pequenas": "pequena",
    "medias": "media", "grandes": "grande", "pequenos": "pequena", "medios": "media", "cubos": "cubo",
    "pacotinhos": "pacotinho", "sache": "sache", "saches": "sache", "garrafas": "garrafa", "potes": "pote",
    "punhados": "punhado", "mililitros": "mililitro",
}
_VOLUME_ML: dict[str, float] = {
    "ml": 1, "mililitro": 1, "l": 1000, "litro": 1000, "lt": 1000,
    "colher de sopa": 15, "colher sopa": 15, "colher": 15, "cs": 15, "c sopa": 15, "col sopa": 15, "col de sopa": 15,
    "colher de cha": 5, "colher cha": 5, "cc": 5, "c cha": 5, "col cha": 5, "col de cha": 5,
    "colher de sobremesa": 10, "colher sobremesa": 10,
    "colher de cafe": 2.5, "colher cafe": 2.5,
    "xicara": 240, "xicara de cha": 240, "xicara cha": 240, "xic": 240, "xicara de cafe": 60,
    "copo": 200, "copo americano": 190, "copo de requeijao": 250,
}
_MASS_G: dict[str, float] = {
    "g": 1, "grama": 1, "gr": 1, "kg": 1000, "quilo": 1000, "kilo": 1000, "mg": 0.001,
    "pitada": 0.5, "punhado": 30,
}
_COUNT_UNITS = {
    "un", "unidade", "unid", "u", "pc", "peca", "dente", "fatia", "folha", "maco", "ramo", "talo", "cabeca",
    "ovo", "pedaco", "file", "bife", "posta", "pequena", "media", "grande", "cubo", "lata", "pacote", "caixa",
    "pacotinho", "sache", "garrafa", "pote", "tablete",
}

# Densidade aproximada (g/ml) por palavra-chave do item da despensa. Primeira
# correspondência vence, então as chaves mais específicas vêm antes.
_DENSIDADE_G_ML: list[tuple[str, float]] = [
    ("farinha de trigo", 0.55), ("farinha de mandioca", 0.60), ("fuba", 0.60), ("polenta", 0.60),
    ("leite ninho", 0.50), ("leite em po", 0.50), ("leite", 1.03),
    ("queijo parmesao", 0.40), ("parmesao", 0.40), ("ralado", 0.40), ("mussarela", 0.50), ("queijo", 0.45),
    ("acucar", 0.85), ("arroz", 0.85), ("feijao", 0.80), ("sal", 1.20), ("manteiga", 0.95),
    ("azeite", 0.92), ("oleo", 0.92), ("chantilly", 1.00), ("cobertura", 1.10), ("chocolate", 1.10),
    ("aceto", 1.00), ("vinagre", 1.00), ("adocante", 1.00), ("canela", 0.50), ("acafrao", 0.50),
    ("curcuma", 0.50), ("caldo", 0.60), ("amendoa", 0.35), ("alcaparra", 0.60), ("cebola", 0.60),
    ("tomate", 0.70), ("alho", 0.65), ("salsinha", 0.20), ("cheiro verde", 0.20), ("couve", 0.20),
    ("bacon", 0.60), ("carne", 0.90), ("frango", 0.90), ("batata", 0.70), ("macarrao", 0.60),
    ("espaguete", 0.60), ("agua", 1.00),
]

# Peso médio em gramas por unidade de contagem, por palavra-chave do item.
_PESO_UNIDADE_G: list[tuple[str, dict[str, float]]] = [
    ("cebola", {"un": 150, "pequena": 100, "media": 150, "grande": 220}),
    ("tomate", {"un": 120, "pequena": 90, "media": 120, "grande": 180}),
    ("batata", {"un": 150, "pequena": 100, "media": 150, "grande": 250}),
    ("alho", {"dente": 5, "un": 5, "cabeca": 40}),
    ("bacon", {"fatia": 15, "cubo": 10, "pedaco": 100}),
    ("mussarela", {"fatia": 20}),
    ("parmesao", {"fatia": 10}),
    ("couve", {"folha": 25, "maco": 300}),
    ("salsinha", {"maco": 60, "ramo": 5, "talo": 5, "punhado": 10}),
    ("cheiro verde", {"maco": 60, "ramo": 5, "talo": 5}),
    ("frango", {"file": 150, "un": 300, "peca": 300, "pedaco": 100}),
    ("alcatra", {"bife": 150, "un": 150, "pedaco": 100}),
    ("acem", {"pedaco": 100, "cubo": 25}),
    ("carne moida", {"pacote": 500}),
    ("caldo", {"tablete": 10, "cubo": 10, "sache": 10}),
    ("manteiga", {"pacote": 200, "tablete": 200}),
    ("macarrao", {"pacote": 500}),
    ("espaguete", {"pacote": 500}),
    ("amendoa", {"pacote": 100}),
]


@dataclass(frozen=True)
class Conversion:
    qty_base: Optional[float]
    base: str
    estimate: bool
    note: str


def _lookup(table: list[tuple[str, object]], item_key: str):
    key = norm_text(item_key)
    for needle, value in table:
        if needle in key:
            return needle, value
    return None, None


def densidade(item_key: str) -> tuple[float, bool]:
    """(g/ml, conhecida?). Sem correspondência usa 1,0 e sinaliza chute."""
    _, value = _lookup(_DENSIDADE_G_ML, item_key)
    return (float(value), True) if value is not None else (1.0, False)


def peso_unidade(item_key: str, count_unit: str) -> Optional[float]:
    _, table = _lookup(_PESO_UNIDADE_G, item_key)
    if not table:
        return None
    unit = count_unit if count_unit in table else {"unidade": "un", "unid": "un", "u": "un", "peca": "un"}.get(count_unit)
    return float(table[unit]) if unit in table else None


def normalize_measure(unit: str) -> str:
    """``"Colheres (sopa)"`` -> ``"colher de sopa"``; ``"g."`` -> ``"g"``."""
    n = norm_text(unit).replace("(", " ").replace(")", " ").replace(".", " ")
    n = re.sub(r"\s+", " ", n).strip()
    words = [_PLURALS.get(w, w) for w in n.split(" ") if w]
    n = " ".join(words)
    if n.startswith("colher ") and " de " not in n and n != "colher":
        n = n.replace("colher ", "colher de ", 1)
    if n.startswith("xicara ") and " de " not in n:
        n = n.replace("xicara ", "xicara de ", 1)
    return n


def to_base(quantity: float, unit: str, item_key: str, base: str, *, conteudo_un_base: Optional[float] = None) -> Conversion:
    """Converte ``quantity unit`` de uma receita para a unidade base do item.

    ``conteudo_un_base`` é quanto vale 1 unidade do item na base kg/L (ex.: a
    cobertura de chocolate é vendida por ``un``; se Dona Maria disser que o pote
    tem 1,01 kg, isso permite converter ``200 g`` em ``0,198 un``).
    """
    q = float(quantity)
    u = normalize_measure(unit or "un")
    if u == "":
        u = "un"
    if base == "kg":
        if u in _MASS_G:
            return Conversion(q * _MASS_G[u] / 1000.0, base, u in ("pitada", "punhado"),
                              f"{q:g} {u} = {q * _MASS_G[u]:g} g" + (" (pitada ≈ 0,5 g)" if u == "pitada" else ""))
        if u in _VOLUME_ML:
            dens, known = densidade(item_key)
            grams = q * _VOLUME_ML[u] * dens
            return Conversion(grams / 1000.0, base, True,
                              f"{q:g} {u} ≈ {q * _VOLUME_ML[u]:g} ml × densidade {dens:g} g/ml ≈ {grams:.0f} g"
                              + ("" if known else " (densidade não tabelada, usei 1,0 g/ml)"))
        if u in _COUNT_UNITS:
            peso = peso_unidade(item_key, u)
            if peso is None:
                return Conversion(None, base, True, f"não sei quanto pesa 1 {u} de {item_key}; informe em gramas")
            return Conversion(q * peso / 1000.0, base, True, f"{q:g} {u} ≈ {q * peso:g} g (≈ {peso:g} g por {u})")
        return Conversion(None, base, True, f"unidade '{unit}' não reconhecida; informe em g ou kg")
    if base == "L":
        if u in _VOLUME_ML:
            return Conversion(q * _VOLUME_ML[u] / 1000.0, base, u not in ("ml", "l", "litro", "mililitro", "lt"),
                              f"{q:g} {u} = {q * _VOLUME_ML[u]:g} ml")
        if u in _MASS_G:
            dens, known = densidade(item_key)
            ml = q * _MASS_G[u] / dens
            return Conversion(ml / 1000.0, base, True,
                              f"{q:g} {u} ≈ {ml:.0f} ml (densidade {dens:g} g/ml)" + ("" if known else ", chute"))
        return Conversion(None, base, True, f"unidade '{unit}' não reconhecida; informe em ml ou L")
    # base == "un"
    if u in _COUNT_UNITS:
        return Conversion(q, base, False, f"{q:g} {u}")
    if u in _MASS_G or u in _VOLUME_ML:
        amount_base = (q * _MASS_G[u] / 1000.0) if u in _MASS_G else (q * _VOLUME_ML[u] / 1000.0)
        if conteudo_un_base:
            return Conversion(amount_base / conteudo_un_base, base, True,
                              f"{q:g} {u} ÷ {conteudo_un_base:g} por unidade ≈ {amount_base / conteudo_un_base:.3f} un")
        if "ovo" in norm_text(item_key):
            return Conversion(q * _MASS_G.get(u, 0) / 50.0, base, True, f"{q:g} {u} ≈ {q * _MASS_G.get(u, 0) / 50.0:.1f} ovos (≈ 50 g cada)")
        return Conversion(None, base, True,
                          f"o item é contado por unidade e não sei o peso/volume de 1 unidade; "
                          f"registre com despensa_atualizar(conteudo_por_unidade)")
    return Conversion(None, base, True, f"unidade '{unit}' não reconhecida para item contado por unidade")


def format_base(qty: float, base: str) -> str:
    """Formato legível em pt-BR: ``0.5 kg`` -> ``500 g``; ``0.015 L`` -> ``15 ml``."""
    if qty is None:
        return "?"
    if base == "kg":
        return f"{qty * 1000:.0f} g" if qty < 1 else f"{qty:.3f}".rstrip("0").rstrip(".") + " kg"
    if base == "L":
        return f"{qty * 1000:.0f} ml" if qty < 1 else f"{qty:.3f}".rstrip("0").rstrip(".") + " L"
    return f"{qty:.2f}".rstrip("0").rstrip(".") + " un"
