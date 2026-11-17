"""Leitor legado de rankings ilustrativos e catálogo de nomes usado pelo carrinho.

Os dados moram em Markdown na base de conhecimento do repositório,
``prompts/base-de-conhecimento/pratos-mais-vendidos/``: um arquivo por estado (com as cidades e zonas dentro),
um por região (``<região>/_regiao.md``) e um do país (``brasil.md``). Cada seção traz uma tabela
``# | Prato | Categoria | Preço médio | Faixa | Índice de vendas | Ingredientes típicos``. Veja o README da pasta.

Os pratos são **priorizados pelo lugar da loja**: o que vende na zona dela pesa mais que o que vende na
cidade, que pesa mais que o do estado, da região e do país (``PESOS``). Sem localização informada em
"Meu negócio", vale só o ranking do país.

O guia de evidências publicadas é separado e lido por ``guias.py``. Os rankings de exemplo
ficam fora do contexto da consultora e do Analytics atual.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import negocio as negocio_mod
from .units import norm_text

logger = logging.getLogger(__name__)

ESCOPOS = ("zona", "cidade", "estado", "regiao", "pais")  # do mais local ao mais amplo
# O lugar mais próximo pesa mais que todos os mais amplos somados (1,0 > 0,5 + 0,2 + 0,1 + 0,05; 0,5 > 0,2 + 0,1 + 0,05; ...):
# o prato que lidera a zona da loja ganha de um prato que só aparece bem na região ou no país.
PESOS = {"zona": 1.0, "cidade": 0.5, "estado": 0.2, "regiao": 0.1, "pais": 0.05}
ROTULOS = {"zona": "Zona", "cidade": "Cidade", "estado": "Estado", "regiao": "Região", "pais": "País"}
AVISO = "Base de exemplo criada para a demonstração do desafio: dados ilustrativos, não são vendas reais."

_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Prato:
    posicao: int
    nome: str
    categoria: str
    preco_medio: float
    preco_min: float
    preco_max: float
    indice: int
    ingredientes: str


@dataclass
class Secao:
    escopo: str
    pais: str = "Brasil"
    regiao: str = ""
    estado: str = ""
    cidade: str = ""
    zona: str = ""
    arquivo: str = ""
    fonte: str = ""
    leitura: str = ""
    pratos: list[Prato] = field(default_factory=list)


def raiz() -> Optional[Path]:
    """Pasta ``pratos-mais-vendidos``: variável de ambiente, o repositório (rodando no host) ou ``/prompts`` (containers)."""
    candidatos: list[Path] = []
    env = os.environ.get("SABOR_DA_MARIA_BASE_CONHECIMENTO")
    if env:
        candidatos.append(Path(env))
    candidatos += [p / "prompts" / "base-de-conhecimento" for p in Path(__file__).resolve().parents]
    candidatos.append(Path("/prompts/base-de-conhecimento"))
    for base in candidatos:
        pasta = base / "pratos-mais-vendidos"
        if pasta.is_dir():
            return pasta
    return None


def _numero(texto: str) -> float:
    achado = _NUMERO.search(texto)
    if not achado:
        raise ValueError(f"número esperado em {texto!r}")
    return float(achado.group().replace(",", "."))


def _faixa(texto: str) -> tuple[float, float]:
    numeros = [float(n.replace(",", ".")) for n in _NUMERO.findall(texto)]
    if len(numeros) != 2 or numeros[0] > numeros[1]:
        raise ValueError(f"faixa esperada como 'min–max', veio {texto!r}")
    return numeros[0], numeros[1]


def _linha(celulas: list[str]) -> Prato:
    if len(celulas) != 7:
        raise ValueError(f"a tabela precisa de 7 colunas, veio {len(celulas)}: {celulas}")
    pos, nome, categoria, medio, faixa, indice, ingredientes = celulas
    minimo, maximo = _faixa(faixa)
    indice_n = int(_numero(indice))
    if not 0 <= indice_n <= 100:
        raise ValueError(f"o índice de vendas vai de 0 a 100, veio {indice_n} em {nome!r}")
    return Prato(int(_numero(pos)), nome, categoria, _numero(medio), minimo, maximo, indice_n, ingredientes)


def _meta(texto: str) -> tuple[dict[str, str], str]:
    """Cabeçalho ``--- chave: valor ---`` do arquivo (sem biblioteca de YAML: só stdlib)."""
    if not texto.startswith("---"):
        return {}, texto
    fim = texto.find("\n---", 3)
    if fim < 0:
        return {}, texto
    meta = {}
    for linha in texto[3:fim].splitlines():
        if ":" in linha and not linha.lstrip().startswith("#"):
            chave, _, valor = linha.partition(":")
            meta[chave.strip()] = valor.split(" #")[0].strip()
    return meta, texto[fim + 4:]


def analisar(texto: str, arquivo: str = "") -> list[Secao]:
    """Lê um arquivo da base. Levanta ``ValueError`` (dizendo a linha) se a tabela estiver fora do formato."""
    meta, corpo = _meta(texto)
    base = dict(pais=meta.get("pais", "Brasil"), regiao=meta.get("regiao", ""), estado=meta.get("estado", ""), arquivo=arquivo, fonte=meta.get("fonte", ""))
    secoes: list[Secao] = []
    atual: Optional[Secao] = None
    cidade = ""
    for numero, bruta in enumerate(corpo.splitlines(), 1):
        linha = bruta.strip()
        if linha.startswith("### "):
            titulo = linha[4:].strip()
            atual = Secao("zona", cidade=cidade, zona=titulo, **base) if cidade else None
        elif linha.startswith("## "):
            titulo = linha[3:].strip()
            if titulo.lower().startswith("cidade:"):  # norm_text tiraria os dois-pontos: aqui compara o texto cru
                cidade = titulo.split(":", 1)[1].strip()
                atual = Secao("cidade", cidade=cidade, **base)
            else:
                cidade = ""
                escopo = {"estado": "estado", "regiao": "regiao", "pais": "pais"}.get(norm_text(titulo))
                atual = Secao(escopo, **base) if escopo else None
            if atual is not None:
                secoes.append(atual)
        if linha.startswith("### ") and atual is not None:
            secoes.append(atual)
        elif atual is not None and not linha.startswith(("#", "|")) and linha and not atual.pratos and not atual.leitura:
            atual.leitura = linha
        elif atual is not None and linha.startswith("|"):
            celulas = [c.strip() for c in linha.strip("|").split("|")]
            if not celulas or celulas[0].lstrip("-: ") == "" or celulas[0] == "#":
                continue  # separador e cabeçalho
            try:
                atual.pratos.append(_linha(celulas))
            except ValueError as exc:
                raise ValueError(f"{arquivo or 'arquivo'}, linha {numero}: {exc}") from None
    return [s for s in secoes if s.pratos]


_LOCK = threading.Lock()
_CACHE: dict[str, tuple[tuple[int, ...], list[Secao]]] = {}


def carregar(pasta: Optional[Path] = None) -> list[Secao]:
    """Todas as seções da base. Relê só quando algum arquivo muda (para editar a base sem reiniciar nada)."""
    pasta = pasta or raiz()
    if pasta is None:
        return []
    arquivos = sorted(p for p in pasta.rglob("*.md")
                      if p.name.lower() != "readme.md" and p.relative_to(pasta).parts[0] != "evidencias")
    assinatura = tuple(p.stat().st_mtime_ns for p in arquivos) + (len(arquivos),)
    with _LOCK:
        em_cache = _CACHE.get(str(pasta))
        if em_cache and em_cache[0] == assinatura:
            return em_cache[1]
    secoes: list[Secao] = []
    for p in arquivos:
        try:
            secoes += analisar(p.read_text(encoding="utf-8"), str(p.relative_to(pasta)))
        except ValueError as exc:  # um arquivo com defeito não derruba a consulta dos outros
            logger.warning("analytics: %s", exc)
    with _LOCK:
        _CACHE[str(pasta)] = (assinatura, secoes)
    return secoes


def aplicaveis(secoes: list[Secao], local: dict[str, str]) -> dict[str, Secao]:
    """Seções que valem para o lugar da loja, uma por escopo."""
    uf, cidade, zona, regiao = local["estado"], norm_text(local["cidade"]), norm_text(local["zona"]), norm_text(local["regiao"])
    achadas: dict[str, Secao] = {}
    for s in secoes:
        if s.escopo == "zona" and uf and s.estado == uf and norm_text(s.cidade) == cidade and norm_text(s.zona) == zona and zona:
            achadas["zona"] = s
        elif s.escopo == "cidade" and uf and s.estado == uf and norm_text(s.cidade) == cidade and cidade:
            achadas["cidade"] = s
        elif s.escopo == "estado" and uf and s.estado == uf:
            achadas["estado"] = s
        elif s.escopo == "regiao" and regiao and norm_text(s.regiao) == regiao:
            achadas["regiao"] = s
        elif s.escopo == "pais":
            achadas["pais"] = s
    return achadas


def _faixa_texto(p: Prato) -> str:
    return f"R$ {p.preco_min:g}–{p.preco_max:g}".replace(".", ",")


def _item(p: Prato, **extra: Any) -> dict[str, Any]:
    return {"prato": p.nome, "categoria": p.categoria, "preco_medio": p.preco_medio, "faixa": _faixa_texto(p),
            "preco_min": p.preco_min, "preco_max": p.preco_max, "ingredientes": p.ingredientes, **extra}


def _prioridade(aplic: dict[str, Secao], categoria: Optional[str]) -> list[dict[str, Any]]:
    total = sum(PESOS[e] for e in aplic) or 1.0
    mapa: dict[str, dict[str, Any]] = {}
    for escopo in ESCOPOS:
        secao = aplic.get(escopo)
        if secao is None:
            continue
        for p in secao.pratos:
            if categoria and norm_text(p.categoria) != norm_text(categoria):
                continue
            m = mapa.setdefault(norm_text(p.nome), {**_item(p), "pontos": 0.0, "onde": []})  # preço e ingredientes do escopo mais local
            m["pontos"] += PESOS[escopo] * p.indice / 100
            m["onde"].append({"escopo": escopo, "posicao": p.posicao, "indice": p.indice})
    ranking = sorted(mapa.values(), key=lambda m: (-m["pontos"], m["onde"][0]["posicao"], m["prato"]))
    for i, m in enumerate(ranking, 1):
        m["posicao"] = i
        m["prioridade"] = round(100 * m.pop("pontos") / total)
    return ranking


def consultar(negocio: Optional[dict[str, Any]] = None, escopo: str = "prioridade", limite: int = 10, categoria: Optional[str] = None,
              pasta: Optional[Path] = None) -> dict[str, Any]:
    """O ranking de pratos mais vendidos para a loja. ``escopo='prioridade'`` junta todos os escopos, o local pesando mais."""
    escopo = (escopo or "prioridade").strip().lower()
    escopo = {"região": "regiao", "país": "pais", "local": "prioridade"}.get(escopo, escopo)
    if escopo != "prioridade" and escopo not in ESCOPOS:
        raise ValueError("escopo precisa ser prioridade, zona, cidade, estado, regiao ou pais")
    limite = max(1, min(int(limite or 10), 30))
    local = negocio_mod.localizacao(negocio)
    secoes = carregar(pasta)
    aplic = aplicaveis(secoes, local)
    resultado: dict[str, Any] = {
        "local": local, "local_definido": bool(local["estado"]), "escopo": escopo,
        "escopos_disponiveis": [{"escopo": e, "rotulo": ROTULOS[e], "pratos": len(aplic[e].pratos), "onde": _onde(aplic[e])} for e in ESCOPOS if e in aplic],
        "aviso": AVISO, "fonte": "prompts/base-de-conhecimento/pratos-mais-vendidos",
    }
    if not secoes:
        return {**resultado, "ranking": [], "mensagem": "A base de pratos mais vendidos não foi encontrada."}
    if escopo == "prioridade":
        ranking = _prioridade(aplic, categoria)
    else:
        secao = aplic.get(escopo)
        ranking = [_item(p, posicao=p.posicao, indice=p.indice) for p in secao.pratos
                   if not categoria or norm_text(p.categoria) == norm_text(categoria)] if secao else []
        ranking.sort(key=lambda m: m["posicao"])
        resultado["leitura"] = secao.leitura if secao else ""
    resultado["ranking"] = ranking[:limite]
    if not local["estado"]:
        resultado["mensagem"] = "Sem a localização da loja só vale o ranking do país: informe estado, cidade e zona em Meu negócio para priorizar pelo local."
    elif escopo != "prioridade" and escopo not in aplic:
        resultado["mensagem"] = f"A base ainda não tem dados de {ROTULOS[escopo].lower()} para este local."
    return resultado


def _onde(secao: Secao) -> str:
    return {"zona": f"{secao.zona}, {secao.cidade}", "cidade": secao.cidade, "estado": secao.estado, "regiao": secao.regiao, "pais": secao.pais}[secao.escopo]


def nomes_de_pratos() -> set[str]:
    """Nomes dos pratos da base, com e sem o parêntese ('Coxinha de frango (porção com 6)' e 'Coxinha de frango')."""
    nomes: set[str] = set()
    for s in carregar():
        for p in s.pratos:
            nomes.add(norm_text(p.nome))
            nomes.add(norm_text(re.sub(r"\s*\([^)]*\)\s*$", "", p.nome)))
    return nomes


def e_prato(nome: str) -> bool:
    """O nome é exatamente o de um prato da base? Igualdade, não prefixo: 'arroz' é ingrediente, 'Arroz carreteiro' é prato."""
    n = norm_text(nome)
    return bool(n) and n in nomes_de_pratos()
