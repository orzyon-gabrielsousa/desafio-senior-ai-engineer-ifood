"""Extração local de receitas da web, sem API externa.

O Hermes separa *busca* (``web.search_backend``) de *extração* (``web.extract_backend``).
A busca fica com o SearXNG (container local). A extração, que nos backends
oficiais depende de Firecrawl/Tavily/Exa (SaaS), fica aqui: ``urllib`` + ``html.parser``
da biblioteca padrão, com duas camadas:

1. **schema.org/Recipe em JSON-LD** — a maioria dos sites brasileiros de receita
   (TudoGostoso, Panelinha, Receitas Nestlé, Ana Maria Braga, ...) embute a receita
   estruturada para o Google. Quando existe, devolvemos ingredientes, rendimento,
   tempo e modo de preparo já limpos, o que é o que o agente precisa para
   comparar com a despensa.
2. **Texto legível da página** como fallback, priorizando ``<article>``/``<main>``.

Segurança: só http(s), recusa hosts privados/loopback (anti-SSRF, em cima do
filtro que o próprio Hermes já aplica), limite de 3 MB por página.
"""

from __future__ import annotations

import gzip
import ipaddress
import json
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROVIDER_NAME = "receitas-local"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
    "Accept-Encoding": "identity",
}
MAX_BYTES = 3_000_000
TIMEOUT_S = 20
MAX_CHARS = 20_000
_LD_RE = re.compile(r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_DUR_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
_CHARSET_RE = re.compile(rb"charset\s*=\s*[\"']?([A-Za-z0-9_\-]+)", re.I)


# ----------------------------------------------------------------------------- rede
def host_is_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return True
    return False


def _check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"esquema não permitido: {parsed.scheme or 'vazio'}")
    if not parsed.hostname:
        raise ValueError("URL sem host")
    if host_is_private(parsed.hostname):
        raise ValueError(f"host privado/loopback recusado: {parsed.hostname}")


def fetch(url: str, timeout: float = TIMEOUT_S) -> tuple[str, str]:
    """Baixa a página e devolve ``(url_final, html)``."""
    _check_url(url)
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        _check_url(final_url)
        data = response.read(MAX_BYTES)
        if "gzip" in (response.headers.get("Content-Encoding") or ""):
            try:
                data = gzip.decompress(data)
            except Exception:
                pass
        charset = response.headers.get_content_charset()
    if not charset:
        match = _CHARSET_RE.search(data[:4096])
        charset = match.group(1).decode("ascii", "ignore") if match else "utf-8"
    try:
        text = data.decode(charset, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    return final_url, text


# ----------------------------------------------------------------------------- JSON-LD
def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for key in ("@graph", "mainEntity", "itemListElement", "hasPart"):
            if key in node:
                yield from _walk(node[key])
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_recipe(node: dict[str, Any]) -> bool:
    kind = node.get("@type")
    if isinstance(kind, list):
        return "Recipe" in kind
    return kind == "Recipe"


def jsonld_recipes(html: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in _LD_RE.findall(html):
        block = block.strip()
        block = re.sub(r"^<!--\s*|\s*-->$", "", block)
        data = None
        for candidate in (block, block.replace("\n", " ").replace("\r", " ")):
            try:
                data = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        if data is None:
            continue
        for node in _walk(data):
            if _is_recipe(node):
                out.append(node)
    return out


def iso_minutes(value: Any) -> Optional[int]:
    if not isinstance(value, str):
        return None
    match = _DUR_RE.match(value.strip().upper())
    if not match:
        return None
    days, hours, minutes, seconds = (int(x) if x else 0 for x in match.groups())
    return days * 1440 + hours * 60 + minutes + (1 if seconds >= 30 else 0)


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("text") or "")
    if isinstance(value, list):
        return ", ".join(_text(v) for v in value if _text(v))
    return str(value or "")


def _instruction_lines(value: Any) -> list[str]:
    lines: list[str] = []
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9])", value) if p.strip()]
        lines.extend(parts)
    elif isinstance(value, dict):
        kind = value.get("@type")
        if kind == "HowToSection" or "itemListElement" in value:
            name = value.get("name")
            if name:
                lines.append(f"[{name}]")
            lines.extend(_instruction_lines(value.get("itemListElement")))
        else:
            text = value.get("text") or value.get("name")
            if text:
                lines.append(str(text).strip())
    elif isinstance(value, list):
        for item in value:
            lines.extend(_instruction_lines(item))
    return [re.sub(r"\s+", " ", l) for l in lines if l]


def recipe_markdown(recipe: dict[str, Any], url: str) -> str:
    nome = _text(recipe.get("name")) or "Receita"
    parts = [f"# {nome}", f"Fonte: {url}"]
    meta = []
    if recipe.get("recipeYield"):
        meta.append(f"Rendimento: {_text(recipe['recipeYield'])}")
    for key, label in (("prepTime", "Preparo"), ("cookTime", "Cozimento"), ("totalTime", "Tempo total")):
        minutes = iso_minutes(recipe.get(key))
        if minutes:
            meta.append(f"{label}: {minutes} min")
    for key, label in (("recipeCategory", "Categoria"), ("recipeCuisine", "Cozinha"), ("keywords", "Palavras-chave")):
        if recipe.get(key):
            meta.append(f"{label}: {_text(recipe[key])}")
    rating = recipe.get("aggregateRating")
    if isinstance(rating, dict) and rating.get("ratingValue"):
        meta.append(f"Avaliação: {rating.get('ratingValue')} ({rating.get('ratingCount') or rating.get('reviewCount') or '?'} votos)")
    if meta:
        parts.append(" | ".join(meta))
    if recipe.get("description"):
        parts.append(re.sub(r"\s+", " ", _text(recipe["description"])).strip())
    ingredientes = recipe.get("recipeIngredient") or recipe.get("ingredients") or []
    if isinstance(ingredientes, str):
        ingredientes = [ingredientes]
    if ingredientes:
        parts.append("## Ingredientes (schema.org/Recipe)")
        parts.extend(f"- {re.sub(r'\\s+', ' ', _text(i)).strip()}" for i in ingredientes if _text(i).strip())
    passos = _instruction_lines(recipe.get("recipeInstructions"))
    if passos:
        parts.append("## Modo de preparo")
        parts.extend(f"{i}. {p}" for i, p in enumerate(passos, 1))
    return "\n".join(parts)


# ----------------------------------------------------------------------------- texto
class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe",
            "button", "select", "option", "template", "figure"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "section", "article", "main",
             "ul", "ol", "table", "blockquote", "pre", "dd", "dt", "figcaption", "span"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._in_title = False
        self._in_article = 0
        self.title = ""
        self.all_parts: list[str] = []
        self.article_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        if tag in ("article", "main"):
            self._in_article += 1
        if tag in self.BLOCK and tag != "span":
            self._append("\n")
        if tag in ("h1", "h2", "h3"):
            self._append("\n## " if tag != "h1" else "\n# ")
        if tag == "li":
            self._append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        if tag in ("article", "main") and self._in_article:
            self._in_article -= 1
        if tag in self.BLOCK:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self._append(data)

    def _append(self, text: str) -> None:
        self.all_parts.append(text)
        if self._in_article:
            self.article_parts.append(text)


def _clean(text: str) -> str:
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if not line or line in ("-",):
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(line)
    return "\n".join(out).strip()


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    article = _clean("".join(parser.article_parts))
    everything = _clean("".join(parser.all_parts))
    text = article if len(article) >= 400 else everything
    title = re.sub(r"\s+", " ", parser.title).strip()
    if not title:
        match = re.search(r"property=[\"']og:title[\"'][^>]*content=[\"']([^\"']+)", html, re.I)
        title = match.group(1).strip() if match else ""
    return title, text[:MAX_CHARS]


# ----------------------------------------------------------------------------- página -> documento
def extract_page(url: str) -> dict[str, Any]:
    """Um documento no formato que o ``web_extract`` do Hermes espera."""
    try:
        final_url, html = fetch(url)
    except urllib.error.HTTPError as exc:
        return {"url": url, "title": "", "content": "", "error": f"HTTP {exc.code} from origin"}
    except Exception as exc:  # DNS, timeout, SSRF, TLS
        return {"url": url, "title": "", "content": "", "error": f"falha ao baixar: {exc}"}
    title, text = html_to_text(html)
    recipes = jsonld_recipes(html)
    sections: list[str] = []
    for recipe in recipes[:2]:
        sections.append(recipe_markdown(recipe, final_url))
        if not title:
            title = _text(recipe.get("name"))
    if recipes:
        sections.append("## Texto da página (resumo)")
        sections.append(text[:6000])
    else:
        sections.append(text)
    content = "\n\n".join(s for s in sections if s).strip()
    if not content:
        return {"url": final_url, "title": title, "content": "", "error": "página sem texto legível"}
    return {
        "url": final_url,
        "title": title,
        "content": content,
        "raw_content": content,
        "metadata": {"sourceURL": final_url, "title": title, "recipe_schema": bool(recipes), "extractor": PROVIDER_NAME},
    }


def extract_many(urls: list[str]) -> list[dict[str, Any]]:
    return [extract_page(u) for u in urls]


def make_provider():
    """Instancia o provider só quando o Hermes está importável (não no host, nos testes)."""
    from agent.web_search_provider import WebSearchProvider

    class ReceitasLocalProvider(WebSearchProvider):
        """Extração local: urllib + html.parser + schema.org/Recipe. Sem chave, sem SaaS."""

        name = PROVIDER_NAME
        display_name = "Extração local de receitas (stdlib, sem API)"

        def is_available(self) -> bool:
            return True

        def supports_search(self) -> bool:
            return False

        def supports_extract(self) -> bool:
            return True

        def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
            logger.info("%s: extraindo %d URL(s)", PROVIDER_NAME, len(urls))
            return extract_many(list(urls))

        def get_setup_schema(self) -> dict[str, Any]:
            return {"name": "Extração local de receitas", "badge": "free · local", "tag": "urllib + schema.org/Recipe, sem chave", "env_vars": []}

    return ReceitasLocalProvider()
