"""Contrato da receita lida: preserva a fonte e detecta fichas parciais do modelo."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlsplit

from . import matching
from .units import norm_text, normalize_measure, strip_accents, to_base


_ITEM_LISTADO = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)(.+)$")
_ANUNCIO = {"continua apos patrocinio", "continua apos publicidade", "publicidade", "patrocinio"}
_APOS_PREPARO = {"pode congelar", "pode armazenar", "conservacao", "dicas", "notas", "compre o que voce precisa",
                "autor", "tempo de preparo", "tempo total", "serve", "rendimento", "receitas relacionadas", "veja tambem"}
_ROTAS_DE_LISTAGEM = {"receita", "receitas", "recipe", "recipes", "index", "home"}
_SECOES_DE_LISTAGEM = {"categoria", "categorias", "category", "categories", "tag", "tags", "busca", "search"}
_TITULO_DE_GRUPO = re.compile(r"^(?:(para (?:a |o )?))?(massa|recheio|cobertura|montagem|finalizacao|molho|calda)$")


def _titulo_de_grupo(linha: str) -> re.Match[str] | None:
    return _TITULO_DE_GRUPO.fullmatch(norm_text(linha).strip(" :."))


def _indices_de_titulos(linhas: list[str]) -> set[int]:
    grupos = [(i, titulo) for i, linha in enumerate(linhas) if (titulo := _titulo_de_grupo(linha))]
    reconhecidos = []
    for pos, (inicio, titulo) in enumerate(grupos):
        fim = grupos[pos + 1][0] if pos + 1 < len(grupos) else len(linhas)
        if any(_medida(linha)[0] is not None for linha in linhas[inicio + 1:fim]):
            reconhecidos.append((inicio, titulo))
    # Alguns schemas misturam títulos nus com ingredientes. Só os trate
    # como seções se o arranjo mostrar dois grupos distintos e medidos.
    if len({m[2] for _, m in reconhecidos}) >= 2:
        return {i for i, _ in reconhecidos}
    return {i for i, m in reconhecidos if m[1] or linhas[i].lstrip().startswith("#") or linhas[i].rstrip().endswith(":")}


def sem_titulos_de_ingredientes(linhas: list[str]) -> list[str]:
    """Preserva os ingredientes da publicação e descarta só os títulos identificáveis."""
    titulos = _indices_de_titulos(linhas)
    return [linha for i, linha in enumerate(linhas) if i not in titulos]


def _contrato_textual(pagina: dict[str, Any]) -> dict[str, Any] | None:
    """Uma receita direta sem JSON-LD ainda pode ter seções e listas claras.

    O título do documento identifica a receita, não os links relacionados que
    podem aparecer antes dos ingredientes. Só promovemos um par inequívoco de
    seções; listas de ideias, páginas incompletas e parágrafos ambíguos continuam
    como texto a conferir. Rendimento e duração não são deduzidos dos passos.
    """
    url = str(pagina.get("url") or "")
    titulo = " ".join(str(pagina.get("title") or "").split())
    endereco = urlsplit(url)
    caminho = [norm_text(unquote(parte)) for parte in endereco.path.strip("/").split("/")]
    pagina_final = caminho[-1].split(".", 1)[0]
    if (endereco.scheme not in {"https", "http"} or not endereco.hostname
            or not pagina_final or pagina_final in _ROTAS_DE_LISTAGEM
            or any(parte in _SECOES_DE_LISTAGEM for parte in caminho)
            or not titulo or not re.search(r"[A-Za-zÀ-ÿ]", titulo)
            or re.search(r"\b\d+\s+(?:receitas|ideias|pratos|opcoes)\b", norm_text(titulo))
            or norm_text(titulo).startswith(("tem mais ideias", "receitas para", "ideias de"))):
        return None
    linhas = str(pagina.get("content") or "").splitlines()

    def secao(linha: str) -> str:
        return norm_text(re.sub(r"^\s*#{1,6}\s*", "", linha)).strip(" :")

    ingredientes = [i for i, linha in enumerate(linhas) if secao(linha) in ("ingredientes", "ingredientes (schema.org/recipe)")]
    preparo = [i for i, linha in enumerate(linhas) if secao(linha) == "modo de preparo"]
    if len(ingredientes) != 1 or len(preparo) != 1 or ingredientes[0] >= preparo[0]:
        return None
    itens = []
    for linha in linhas[ingredientes[0] + 1:preparo[0]]:
        if not linha.strip() or secao(linha) in _ANUNCIO:
            continue
        item = _ITEM_LISTADO.match(linha)
        if not item:
            if not _titulo_de_grupo(linha):
                return None
            itens.append(linha.strip())
        else:
            itens.append(item[1].strip())
    itens = sem_titulos_de_ingredientes(itens)
    passos = []
    for linha in linhas[preparo[0] + 1:]:
        if not linha.strip() or secao(linha) in _ANUNCIO:
            continue
        passo = _ITEM_LISTADO.match(linha)
        if not passo:
            if secao(linha) in _APOS_PREPARO:
                break  # Conservação, loja e links seguintes não são passos da receita.
            return None  # Texto ambíguo pode ser uma continuação do preparo.
        passos.append(passo[1].strip())
    if not itens or not passos:
        return None
    # O rótulo e seu valor também podem vir em linhas separadas, como no
    # fallback real do Panelinha. "Até 6" / "Até 2h" não viram valores exatos.
    texto = "\n".join(linhas)
    rendimento = re.search(r"(?im)^\s*(?:rendimento|serve)\s*:?\s*\n?\s*([^\n]+)", texto)
    tempo = re.search(r"(?im)^\s*(?:tempo total|tempo de preparo)\s*:?\s*\n?\s*([^\n]+)", texto)
    numero = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(?:porcoes|pessoas)", norm_text(rendimento[1]).strip(" .") if rendimento else "")
    duracao = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(min|minutos?|h|horas?)", norm_text(tempo[1]).strip(" .") if tempo else "")
    return {"nome": titulo, "url": url, "ingredientes": itens,
            "porcoes": float(numero[1].replace(",", ".")) if numero else None,
            "tempo_preparo_min": float(duracao[1].replace(",", ".")) * (60 if duracao[2].startswith("h") else 1) if duracao else None,
            "rendimento_texto": rendimento[1].strip() if rendimento else None,
            "tempo_preparo_texto": tempo[1].strip() if tempo else None,
            "modo_preparo": "\n".join(passos)}


def texto_da_ficha(conteudo: Any) -> str:
    """Isola a primeira receita completa, ignorando o cabeçalho do transporte.

    O web_extract do Hermes pode envolver a ficha em ``# Receita / Fonte``.
    O título válido é o último H1 anterior aos ingredientes, não esse envelope.
    O próximo H1 encerra a ficha para não juntar receitas distintas da página.
    """
    texto = str(conteudo or "").split("\n## Texto da página", 1)[0].strip()
    ingredientes = re.search(r"^## Ingredientes[^\n]*$", texto, re.M)
    if not ingredientes:
        return texto.split("\n# ", 1)[0]
    titulos = [m for m in re.finditer(r"^# [^\n]+$", texto, re.M) if m.start() < ingredientes.start()]
    inicio = titulos[-1].start() if titulos else 0
    trecho = texto[inicio:]
    proxima = re.search(r"\n# ", trecho)
    return trecho[:proxima.start()] if proxima else trecho


def contrato_pagina(pagina: dict[str, Any]) -> dict[str, Any] | None:
    receitas = pagina.get("receitas")
    if isinstance(receitas, list) and receitas and isinstance(receitas[0], dict):
        receita = receitas[0]
        linhas = receita.get("ingredientes") or []
        linhas = [linhas] if isinstance(linhas, str) else linhas
        if isinstance(linhas, list) and all(isinstance(linha, str) for linha in linhas):
            return {**receita, "ingredientes": sem_titulos_de_ingredientes(linhas), "url": pagina.get("url")}
    texto = texto_da_ficha(pagina.get("content"))
    nome = re.search(r"^# (.+)$", texto, re.M)
    ingredientes = re.search(r"^## Ingredientes[^\n]*\n(.+?)(?=\n## |\Z)", texto, re.M | re.S)
    preparo = re.search(r"^## Modo de preparo[^\n]*\n(.+?)(?=\n## |\Z)", texto, re.M | re.S)
    if not (nome and ingredientes and preparo):
        return _contrato_textual(pagina)
    linhas = [re.sub(r"^\s*[-*]\s*", "", l).strip() for l in ingredientes[1].splitlines() if l.strip()]
    rendimento = re.search(r"Rendimento:\s*(\d+(?:[.,]\d+)?)", texto, re.I)
    tempo = re.search(r"Tempo total:\s*(\d+(?:[.,]\d+)?)\s*min", texto, re.I)
    return {"nome": nome[1].strip(), "url": pagina.get("url"), "ingredientes": sem_titulos_de_ingredientes(linhas),
            "porcoes": float(rendimento[1].replace(",", ".")) if rendimento else None,
            "tempo_preparo_min": float(tempo[1].replace(",", ".")) if tempo else None,
            "modo_preparo": preparo[1].strip()}


def requisitos(contrato: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Só requisitos evidentes do preparo, sem inventar equipamento para comida fria."""
    preparo = str(contrato.get("modo_preparo") or "")
    texto = norm_text(preparo)
    texto = re.sub(r"\bsem (?:usar |precisar de )?(?:o )?forno\b", " ", texto)
    equipamentos, tecnicas = [], []
    alternativa_forno_air = bool(re.search(r"\b(?:forno(?: convencional)? (?:ou|/) (?:na )?air ?fryer|air ?fryer (?:ou|/) (?:no )?forno)\b", texto))
    if alternativa_forno_air:
        equipamentos.append("forno ou air fryer")
    tem_air = bool(re.search(r"\bair ?fryer\b", texto))
    tem_micro = bool(re.search(r"\bmicro ?ondas\b", texto))
    forno_explicito = bool(re.search(r"\bforno\b", texto))
    assar = bool(re.search(r"\b(asse|assar|assado|gratine|gratinar)\b", texto))
    if not alternativa_forno_air and (forno_explicito or (assar and not tem_air and not tem_micro)):
        equipamentos.append("forno")
    for padrao, equipamento in (
            (r"\b(assadeira|refratario|forma)\b", "forma"),
            (r"\bair ?fryer\b", "air fryer"), (r"\bmicro ?ondas\b", "micro-ondas"), (r"\bliquidificador\b", "liquidificador"),
            (r"\bbatedeira\b", "batedeira"), (r"\bpanela de pressao\b", "panela de pressão"),
            (r"\bfrigideira\b", "frigideira"), (r"\b(fogao|fogo medio|fogo baixo|fogo alto)\b", "fogão"),
            (r"\b(geladeira|refrigere|refrigerar)\b", "geladeira"), (r"\bfreezer\b", "freezer")):
        if re.search(padrao, texto) and not (alternativa_forno_air and equipamento == "air fryer"):
            equipamentos.append(equipamento)
    # Refogar numa panela exige uma fonte de calor. Uma indicação explícita de
    # aparelho elétrico não deve ganhar um fogão adicional por inferência.
    for trecho in re.split(r"[.!?;\n]", preparo):
        trecho = norm_text(trecho)
        if (re.search(r"\b(refogue|refogar|refogado)\b", trecho)
                and not re.search(r"\b(micro ?ondas|air ?fryer|forno)\b", trecho)):
            if "fogão" not in equipamentos:
                equipamentos.append("fogão")
            break
    for padrao, tecnica in ((r"\b(asse|assar|assado|gratine|gratinar|forno)\b", "assar"),
                            (r"\b(refogue|refogar|refogado)\b", "refogar"),
                            (r"\b(grelhe|grelhar)\b", "grelhar"),
                            (r"\b(empane|empanar)\b", "empanar"),
                            (r"\b(bechamel|molho branco)\b", "molhos")):
        if re.search(padrao, texto):
            tecnicas.append(tecnica)
    return equipamentos, tecnicas


def tempo_minimo_de_forno(preparo: str) -> float | None:
    """Extrai só tempos de forno explícitos; soma retornos identificados como sequenciais."""
    acumulado = maior = 0.0
    etapas = re.split(r"(?<!\d)[.!?]|[.!?](?!\d)|[;\n]", strip_accents(preparo).lower())
    for etapa in etapas:
        # Preaquecimento, condicionais e métodos alternativos não estabelecem
        # por si só um tempo obrigatório de forno para o lote.
        if (not re.search(r"\b(?:ao|no|do) forno\b", etapa)
                or re.search(r"\b(?:se|ou|enquanto|nao|sem|esfriar|antes de|preaquecimento|pre\s*-?\s*aquecer)\b", etapa)):
            continue
        acao = re.search(
            r"\b(?:asse|assar|gratine|gratinar)\b|\b(?:leve|coloque|volte|retorne|devolva)\b.*?\b(?:ao|no) forno\b", etapa)
        if not acao:
            continue
        tempo = re.search(
            r"\b(?:por|durante)\s*(?P<adicional>mais\s+|\+\s*)?"
            r"(?P<quantidade>\d+(?:[.,]\d+)?)\s*(?P<unidade>min(?:utos?)?|h(?:oras?)?)\b", etapa[acao.end():])
        if not tempo:
            continue
        minutos = float(tempo["quantidade"].replace(",", "."))
        if tempo["unidade"].startswith("h"):
            minutos *= 60
        sequencial = tempo["adicional"] or re.search(r"\b(?:novamente|volte|retorne|devolva)\b", etapa)
        acumulado = acumulado + minutos if sequencial else minutos
        maior = max(maior, acumulado)
    return maior or None


def _medida(linha: str) -> tuple[float | None, str | None, str]:
    for fracao, numero in (("½", "0.5"), ("¼", "0.25"), ("¾", "0.75"), ("⅓", "0.3333333333333333"), ("⅔", "0.6666666666666666")):
        if linha.lstrip().startswith(fracao):
            linha = linha.lstrip().replace(fracao, numero + " ", 1)
            break
    # Frações Unicode no meio da medida ainda não têm interpretação segura.
    # Não deixe a normalização apagar o símbolo e transformar 1½ em 1.
    if re.search(r"[½¼¾⅓⅔]", linha):
        return None, None, norm_text(linha)
    texto = norm_text(linha)
    m = re.match(r"^(\d+(?:[.,]\d+)?(?:/\d+)?)\s*(\D.+)$", texto)
    if not m:
        return None, None, texto
    numero, resto = m.groups()
    partes = numero.replace(",", ".").split("/")
    divisor = float(partes[1]) if len(partes) == 2 else 1
    if divisor <= 0:
        return None, None, resto
    quantidade = float(partes[0]) / divisor
    if re.match(r"^peitos? de frango\b", resto):
        return quantidade, "peito", re.sub(r"^peitos\b", "peito", resto)
    medida = re.match(r"^(kg|g|gramas?|quilos?|ml|l|litros?|colher(?:es)?(?: (?:de )?(?:sopa|cha|sobremesa|cafe))?|"
                      r"xicaras?(?: (?:de )?cha)?|copos?(?: americanos?| de requeijao)?|dentes?|unidades?|fatias?)"
                      r"\b\s*(?:de\s+)?(.+)$", resto)
    if medida:
        unidade, nome = medida.groups()
        fracao_extra = re.match(r"^e\s+(\d+)\s*/\s*(\d+)\s*(?:de\s+)?(.+)$", nome)
        if fracao_extra:
            numerador, denominador, nome = fracao_extra.groups()
            if int(denominador) <= 0:
                return None, None, nome
            quantidade += int(numerador) / int(denominador)
        # Corrija apenas a leitura estruturada. A linha da publicação fica
        # intacta na fonte verificada e continua disponível na conferência.
        if unidade.startswith("copo") and nome == "a leite":
            nome = "leite"
        if nome == "fermenro em po":
            nome = "fermento em po"
        return quantidade, unidade, nome
    tamanho = re.search(r"\b(grandes?|medias?|pequenas?)\b", resto)
    return quantidade, tamanho[1] if tamanho else "un", resto


def nomes_dos_ingredientes(contrato: dict[str, Any], ingredientes: list[Any]) -> list[Any]:
    """Se a LLM copiou uma linha inteira como nome, separe só o nome conhecido.

    Não corrige nem inventa quantidades: a fidelidade continua comparando os
    campos numéricos enviados com a fonte. Nomes comerciais não são abreviados.
    """
    nomes = {}
    for linha in contrato.get("ingredientes") or []:
        qtd, unidade, nome = _medida(str(linha))
        if qtd is not None and unidade and nome:
            nomes[norm_text(str(linha))] = nome
    return [{**i, "nome": nomes.get(norm_text(str(i.get("nome") or "")), i.get("nome"))}
            if isinstance(i, dict) else i for i in ingredientes]


def _linhas_publicadas(contrato: dict[str, Any]) -> list[tuple[str, float | None, str | None, str]]:
    """Soma a mesma medida do mesmo insumo quando a fonte a repete em etapas diferentes."""
    linhas = []
    indices = {}
    for linha in contrato.get("ingredientes") or []:
        linha = str(linha)
        quantidade, unidade, nome = _medida(linha)
        if quantidade is None or not unidade:
            linhas.append((linha, quantidade, unidade, nome))
            continue
        chave = (nome, normalize_measure(unidade))
        if chave in indices:
            indice = indices[chave]
            anterior, soma, medida, _ = linhas[indice]
            linhas[indice] = (f"{anterior} + {linha}", soma + quantidade, medida, nome)
        else:
            indices[chave] = len(linhas)
            linhas.append((linha, quantidade, unidade, nome))
    return linhas


def ingredientes_publicados(contrato: dict[str, Any]) -> list[dict[str, Any]]:
    """Copia a ficha original, sem estimar medidas que a página não informa."""
    return [{"nome": nome, "quantidade": quantidade, "unidade": unidade}
            if quantidade is not None and unidade else {"nome": linha}
            for linha, quantidade, unidade, nome in _linhas_publicadas(contrato)]


def levar_medidas_confirmadas(ingredientes: list[dict[str, Any]], anteriores: dict[str, Any]) -> None:
    """Uma nova cópia da publicação não apaga medidas de uso já confirmadas."""
    medidas = anteriores.get("medidas_de_uso_confirmadas") or {}
    for item in ingredientes:
        nome = str(item.get("nome") or "")
        medida = medidas.get(nome)
        if medida and item.get("quantidade") is None:
            item.update(quantidade=medida["quantidade"], unidade=medida["unidade"])


def medidas_de_uso_vigentes(ingredientes: list[Any], anteriores: dict[str, Any], campo: str) -> dict[str, Any]:
    medidas = anteriores.get(campo) or {}
    return {str(i["nome"]): medidas[i["nome"]] for i in ingredientes if isinstance(i, dict) and i.get("nome") in medidas
            and i.get("quantidade") == medidas[i["nome"]]["quantidade"] and i.get("unidade") == medidas[i["nome"]]["unidade"]}


def corrigir_secoes_da_ficha(dados: dict[str, Any], porcoes: Any) -> dict[str, Any]:
    """Corrige a leitura de uma cópia original, preservando ingredientes que receberam medidas."""
    contrato = dados.get("fonte_verificada") or {}
    publicadas = contrato.get("ingredientes") or []
    if not dados.get("ficha_original_da_fonte") or dados.get("adaptacao") or not all(isinstance(l, str) for l in publicadas):
        return dados
    titulos = _indices_de_titulos(publicadas)
    if not titulos:
        return dados
    nomes = {norm_text(publicadas[i]) for i in titulos}
    ingredientes = []
    removidos = set()
    for item in dados.get("ingredientes") or []:
        if isinstance(item, dict) and set(item) == {"nome"} and (nome := norm_text(str(item["nome"]))) in nomes:
            removidos.add(nome)
        else:
            ingredientes.append(item)
    if removidos != nomes:
        return dados
    limpa = {**contrato, "ingredientes": [linha for i, linha in enumerate(publicadas) if i not in titulos]}
    fidelidade = conferir(limpa, ingredientes, porcoes, tempo_preparo_min=dados.get("tempo_preparo_min"))
    bloqueios = [b for b in dados.get("bloqueios") or [] if b.get("tipo") != "ingrediente_pendente"
                 or norm_text(b["detalhe"].split(":", 1)[0]) not in nomes]
    return {**dados, "fonte_verificada": limpa, "ingredientes": ingredientes, "bloqueios": bloqueios,
            "fidelidade_fonte": {**(dados.get("fidelidade_fonte") or {}), **fidelidade}}


def normalizar_adaptacao(valor: Any) -> str:
    """Ausência declarada não autoriza omissões na receita publicada.

    Avaliamos a declaração inteira, não a presença da palavra 'não': uma
    mudança afirmativa como 'não usar X; trocar por Y' continua sendo proposta.
    """
    if not isinstance(valor, str):
        return ""
    texto = " ".join(valor.split()).strip()
    normal = norm_text(texto).strip(" .!;:")
    ausente = re.fullmatch(
        r"(?:nao|nenhuma?|n/a|nao se aplica|nao aplicavel|"
        r"(?:nao ha|nao tem)(?: (?:nenhuma? )?(?:adaptacao|adaptacoes|alteracao|alteracoes|mudanca|mudancas))?|"
        r"sem (?:adaptacao|adaptacoes|alteracao|alteracoes|mudanca|mudancas)|"
        r"receita original|conforme (?:a )?(?:receita )?original)", normal)
    return "" if not normal or ausente else texto


# Linha da fonte sem quantidade ("sal a gosto"). O texto orienta o modelo; a ficha dela mostra só o ingrediente.
SEM_MEDIDA = "medida não estruturada; conferir e apresentar a medida de uso proposta"


def sem_medida(conferencia: dict[str, Any]) -> list[str]:
    """Linhas da fonte que não trazem quantidade, sem a orientação escrita para o modelo."""
    sufixo = ": " + SEM_MEDIDA
    return [e[:-len(sufixo)] for e in conferencia.get("estimativas") or [] if isinstance(e, str) and e.endswith(sufixo)]


def _mesmo_ingrediente(da_fonte: str, da_ficha: str) -> float:
    """Fonte e ficha falam da mesma receita: nome igual é o mesmo ingrediente.

    ``matching.score`` casa ingrediente com item da despensa e aplica os sinônimos dela antes de comparar ("massa" vira
    macarrão, "patinho" vira carne moída). Para "massa de tomate" contra "massa de tomate" isso dava nota abaixo do
    corte, e a ficha copiada da própria fonte aparecia como divergente dela.
    """
    return 1.0 if norm_text(da_fonte) == norm_text(da_ficha) else matching.score(da_fonte, da_ficha)


def conferir(contrato: dict[str, Any], ingredientes: list[Any], porcoes: Any,
             adaptacao: str = "", tempo_preparo_min: Any = None) -> dict[str, Any]:
    """Cada linha da fonte precisa estar representada; mudanças viram versão declarada."""
    adaptacao = normalizar_adaptacao(adaptacao)
    usados: set[int] = set()
    faltantes, divergencias, estimativas, referencias = [], [], [], []
    for linha, quantidade, unidade, nome in _linhas_publicadas(contrato):
        candidatos = [(_mesmo_ingrediente(nome, str((i.get("ingrediente_original") if adaptacao else None) or i.get("nome") or "")), idx, i)
                      for idx, i in enumerate(ingredientes) if isinstance(i, dict) and idx not in usados]
        melhor = max(candidatos, key=lambda c: c[0], default=None)
        if melhor is None or melhor[0] < 0.6:
            faltantes.append(str(linha))
            referencias.append(nome)
            continue
        _, idx, item = melhor
        usados.add(idx)
        if adaptacao and item.get("ingrediente_original") and norm_text(nome) != norm_text(str(item.get("nome") or "")):
            divergencias.append(f"{linha} → substituído por {item.get('nome')} nesta adaptação")
            referencias.append(nome)
        if quantidade is None:
            estimativas.append(f"{linha}: {SEM_MEDIDA}")
            if re.search(r"[0-9½¼¾⅓⅔]", str(linha)) or re.match(r"^(um|uma|meio|meia|dois|duas|tres|quatro)\b", norm_text(str(linha))):
                divergencias.append(f"{linha}: a medida da receita ainda não foi convertida; a quantidade proposta é uma adaptação")
            continue
        try:
            qtd = float(str(item.get("quantidade")).replace(",", "."))
            un = str(item.get("unidade") or "")
            comparada = False
            for base in ("kg", "L", "un"):
                original = to_base(quantidade, unidade or "un", nome, base)
                proposta = to_base(qtd, un, str(item.get("nome") or ""), base)
                if original.qty_base is not None and proposta.qty_base is not None:
                    comparada = True
                    if abs(original.qty_base - proposta.qty_base) > 1e-6:
                        divergencias.append(f"{linha} → proposto {qtd:g} {un} de {item.get('nome')}")
                        referencias.append(nome)
                    break
            if not comparada:
                divergencias.append(f"{linha}: não foi possível validar a conversão da medida proposta")
                referencias.append(nome)
        except (ValueError, TypeError, ZeroDivisionError):
            divergencias.append(f"{linha}: medida proposta sem validação")
            referencias.append(nome)
    extras = [str(i.get("nome")) for idx, i in enumerate(ingredientes) if isinstance(i, dict) and idx not in usados]
    if extras:
        divergencias.append("ingredientes acrescentados: " + ", ".join(extras))
        referencias.extend(extras)
    rendimento = contrato.get("porcoes")
    try:
        if rendimento is not None and porcoes is not None and float(rendimento) != float(porcoes):
            divergencias.append(f"rendimento da fonte {float(rendimento):g} porções → proposto {float(porcoes):g}")
            referencias.append("rendimento")
    except (ValueError, TypeError):
        divergencias.append("rendimento da fonte sem interpretação segura; confira o lote proposto")
        referencias.append("rendimento")
    try:
        tempo = contrato.get("tempo_preparo_min")
        if tempo is not None and tempo_preparo_min is not None and float(tempo) != float(tempo_preparo_min):
            divergencias.append(f"tempo da fonte {float(tempo):g} min → proposto {float(tempo_preparo_min):g} min")
            referencias.append("tempo")
    except (ValueError, TypeError):
        divergencias.append("tempo da fonte sem interpretação segura; confira a duração proposta")
        referencias.append("tempo")
    diferencas = [*(f"retirado da fonte: {linha}" for linha in faltantes), *divergencias]
    bloqueios = []
    if diferencas and not adaptacao.strip():
        bloqueios = [{"tipo": "fonte_incompleta" if faltantes else "fonte_divergente",
                      # Só o problema, em palavras para ela. Como corrigir vai nos próximos passos da conferência.
                      "detalhe": "A ficha ainda não bate com a receita lida: " + "; ".join(diferencas) + "."}]
    return {"bloqueios": bloqueios, "faltantes": faltantes, "diferencas": diferencas,
            "estimativas": estimativas, "adaptacao": adaptacao.strip() or None}
