"""Minha cozinha: o que a Dona Maria tem, o que sabe fazer e se cada prato e cada ingrediente cabe nisso.

Três coisas moram aqui:

1. **O catálogo fechado** (equipamentos, utensílios e habilidades) que a tela mostra para ela marcar. A tela só
   envia ids do catálogo, então nada fora dele chega ao banco (mesma ideia do ``negocio.py``).
2. **A leitura do perfil salvo.** O que ela marca na tela vai para a MESMA tabela ``perfil`` que a consultora usa
   (``equipamentos``, ``tecnicas`` e ``restricoes``): por isso o que ela marca já vale para o CMV, o carrinho e o
   cardápio, e a consultora não pergunta de novo o que está lá. Na tela não há trava de evidência: a Dona Maria é
   quem está marcando; a trava de ``falas.py`` é só para o que o modelo quer gravar por conta própria.
3. **A conferência.** Lê as fichas de ``prompts/base-de-conhecimento/cozinha-e-equipamentos/`` (``pratos.md`` e
   ``ingredientes.md``: o que cada prato e cada ingrediente precisa) e diz, para a cozinha dela, se dá para fazer,
   se falta algo, se ela ainda está aprendendo ou se falta ela responder. As fichas são Markdown, editáveis sem
   código, no mesmo estilo da base de pratos mais vendidos.

A conferência **avisa**, não decide. No rascunho de compra (carrinho) o aviso aparece e ela escolhe; no caminho da
receita até o cardápio, o que a ficha exige entra no veredito das travas existentes (``tools._avaliar_prato``).
Os números de custo das fichas são de exemplo (custo típico dos ingredientes por porção), como a base de pratos.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from . import costing
from .units import norm_text

logger = logging.getLogger(__name__)

EQUIPAMENTO, UTENSILIO, HABILIDADE = "equipamento", "utensilio", "habilidade"
NOME_DA_BASE = "cozinha-e-equipamentos"
AVISO_CUSTO = "Custo típico de exemplo (ingredientes por porção), criado para a demonstração do desafio: use o seu custo real."


@dataclass(frozen=True)
class Item:
    id: str
    nome: str
    grupo: str
    termos: tuple[str, ...] = ()  # como ela diria (sinônimos); o nome também vale
    dica: str = ""
    frase: str = ""  # só habilidade: verbo no infinitivo, para as frases dos avisos ("assar no forno")
    curto: str = ""  # como o equipamento aparece dentro de uma frase, quando o nome da tela é longo ("forma", não "Forma, assadeira ou refratário")

    @property
    def rotulo(self) -> str:
        return self.curto or self.nome.lower()


# Os ids dos equipamentos e utensílios são as chaves que a consultora já gravava em ``perfil.equipamentos``
# (fogao, forno, panela_pressao, air_fryer, ...): o que ela contou antes continua valendo.
CATALOGO: tuple[Item, ...] = (
    # ---- equipamentos
    Item("fogao", "Fogão", EQUIPAMENTO, ("fogao a gas", "fogao eletrico", "cooktop", "boca", "bocas"), "a gás ou elétrico"),
    Item("forno", "Forno", EQUIPAMENTO, ("forno eletrico", "forno a gas", "forninho"), "do fogão ou elétrico"),
    Item("micro_ondas", "Micro-ondas", EQUIPAMENTO, ("microondas",)),
    Item("air_fryer", "Air fryer", EQUIPAMENTO, ("airfryer", "fritadeira eletrica", "fritadeira sem oleo", "fritadeira a ar", "fritadeira"), "fritadeira elétrica sem óleo"),
    Item("liquidificador", "Liquidificador", EQUIPAMENTO),
    Item("batedeira", "Batedeira", EQUIPAMENTO),
    Item("processador", "Processador ou mixer", EQUIPAMENTO, ("processador", "mixer", "multiprocessador"), curto="processador"),
    Item("geladeira", "Geladeira", EQUIPAMENTO, ("refrigerador",)),
    Item("freezer", "Freezer", EQUIPAMENTO, ("congelador",)),
    Item("chapa", "Chapa ou grill", EQUIPAMENTO, ("chapa", "grill", "grill eletrico", "sanduicheira"), "para lanches e grelhados", curto="chapa"),
    Item("churrasqueira", "Churrasqueira ou grelha", EQUIPAMENTO, ("churrasqueira", "grelha", "brasa", "carvao"), curto="churrasqueira"),
    # ---- panelas e utensílios
    Item("panela_pressao", "Panela de pressão", UTENSILIO, ("pressao",)),
    Item("panela_grande", "Panela grande", UTENSILIO, ("caldeirao", "panelao")),
    Item("panela_funda", "Panela funda para fritar", UTENSILIO, ("panela funda", "fritadeira de imersao", "frigideira funda", "wok"), "fritura por imersão", curto="panela funda"),
    Item("frigideira", "Frigideira", UTENSILIO, ("frigideira antiaderente",)),
    Item("forma", "Forma, assadeira ou refratário", UTENSILIO, ("forma", "assadeira", "travessa", "refratario", "tabuleiro"), curto="forma"),
    Item("faca", "Faca de cozinha", UTENSILIO, ("faca", "facas"), curto="faca"),
    Item("balanca", "Balança de cozinha", UTENSILIO, ("balanca",), "para pesar as porções e o custo", curto="balança"),
    Item("cuscuzeira", "Cuscuzeira", UTENSILIO),
    Item("esteira_sushi", "Esteira de sushi", UTENSILIO, ("esteira", "makisu")),
    # ---- habilidades
    Item("arroz_feijao", "Cozinhar arroz e feijão", HABILIDADE, ("arroz e feijao", "arroz", "feijao"), "arroz soltinho e feijão no ponto", "cozinhar arroz e feijão"),
    Item("refogar", "Refogar e temperar", HABILIDADE, ("refogar", "refogado", "temperar", "tempero"), "cebola, alho e temperos", "refogar e temperar"),
    Item("grelhar", "Grelhar carnes e frango", HABILIDADE, ("grelhar", "grelhado", "ponto da carne", "ponto de carne", "saltear"), "bife e frango no ponto certo", "grelhar carnes e frango"),
    Item("fritar", "Fritar por imersão", HABILIDADE, ("fritar", "fritura", "frito", "imersao"), "coxinha, pastel, batata", "fritar por imersão"),
    Item("empanar", "Empanar", HABILIDADE, ("empanar", "empanado", "milanesa"), "farinha, ovo e farinha de rosca", "empanar"),
    Item("modelar", "Modelar massas e salgados", HABILIDADE, ("modelar", "modelagem", "salgado"), "coxinha, pastel, pão de queijo", "modelar massas e salgados"),
    Item("massa_fermentada", "Massas com fermento (pizza e pães)", HABILIDADE, ("massa fermentada", "massa com fermento", "massa de pizza", "fermentacao", "pizza", "pao", "paes"), "sova, descanso e crescimento", "fazer massa com fermento (pizza e pão)"),
    Item("assar", "Assar no forno", HABILIDADE, ("assar", "assado", "gratinar", "gratinado"), "carnes, bolos e gratinados", "assar no forno"),
    Item("molhos", "Molhos (tomate, branco, cremes)", HABILIDADE, ("molho", "bechamel", "roux"), "molho de tomate, molho branco", "fazer molhos"),
    Item("doces", "Bolos, pudins e doces", HABILIDADE, ("bolo", "doce", "confeitaria", "sobremesa", "pudim", "calda", "caramelo"), "ponto de massa e de calda", "fazer bolos, pudins e doces"),
    Item("cozimento_longo", "Cozimentos longos", HABILIDADE, ("cozimento longo", "ensopado", "feijoada", "carne de panela", "cozido"), "feijoada, carne de panela, ensopados", "fazer cozimentos longos (feijoada, carne de panela)"),
    Item("peixes", "Peixes e frutos do mar", HABILIDADE, ("peixe", "frutos do mar", "camarao", "moqueca"), "limpar, temperar e dar o ponto", "preparar peixes e frutos do mar"),
    Item("sushi", "Sushi e peixe cru", HABILIDADE, ("sushi", "peixe cru", "sashimi"), "arroz de sushi, cortes e enrolar", "fazer sushi e preparar peixe cru"),
    Item("montagem", "Montar lanches, saladas e bowls", HABILIDADE, ("montar", "montagem", "lanche", "salada", "bowl"), "hambúrguer, salada, poke, açaí", "montar lanches, saladas e bowls"),
    Item("massas_regionais", "Cuscuz, tapioca e angu", HABILIDADE, ("cuscuz", "tapioca", "angu", "polenta", "pirao"), "massas de milho e mandioca", "fazer cuscuz, tapioca e angu"),
)
POR_ID: dict[str, Item] = {i.id: i for i in CATALOGO}
NIVEIS: dict[str, str] = {"sim": "Sei fazer", "aprendendo": "Estou aprendendo", "nao": "Não sei"}
BOCAS = (1, 2, 3, 4, 5, 6)

# Rotina da cozinha: as quatro restrições do checklist da consultora (chaves iguais às de ``perfil.restricoes``).
ROTINA: dict[str, dict[str, Any]] = {
    "espaco_geladeira": {"nome": "Espaço na geladeira", "opcoes": (("pouco", "Pouco"), ("médio", "Médio"), ("bastante", "Bastante"))},
    "tempo_por_cozinhada_min": {"nome": "Tempo por cozinhada", "opcoes": ((60, "Até 1 hora"), (120, "Até 2 horas"), (180, "Até 3 horas"), (240, "4 horas ou mais"))},
    "gas": {"nome": "Gás", "opcoes": (("botijão", "Botijão"), ("encanado", "Encanado"), ("falta às vezes", "Falta às vezes"))},
    "energia": {"nome": "Energia elétrica", "opcoes": (("estável", "Estável"), ("cai às vezes", "Cai às vezes"))},
}


def rotina_canonica(chave: str, valor: Any) -> Any:
    """O que a conversa grava sem acento ou com um complemento ('media', 'botijao ok') casa com a opção da tela ('médio', 'botijão'):
    as quatro primeiras letras bastam, porque as opções de cada pergunta começam diferente. O que não casa fica como veio."""
    opcoes = [v for v, _ in ROTINA.get(chave, {}).get("opcoes", ())]
    if isinstance(valor, bool) or valor in (None, "") or valor in opcoes:
        return valor
    texto = norm_text(str(valor))
    for v in opcoes:
        base = norm_text(str(v))
        if len(texto) >= 4 and texto.startswith(base[:4]) and not str(v).isdigit():
            return v
    return valor


def itens(grupo: str) -> list[Item]:
    return [i for i in CATALOGO if i.grupo == grupo]


def opcoes() -> dict[str, Any]:
    """Catálogo para a tela montar os campos."""
    def lista(grupo: str) -> list[dict[str, str]]:
        return [{"id": i.id, "label": i.nome, "dica": i.dica} for i in itens(grupo)]

    return {
        "equipamentos": lista(EQUIPAMENTO), "utensilios": lista(UTENSILIO), "habilidades": lista(HABILIDADE),
        "niveis": [{"id": k, "label": v} for k, v in NIVEIS.items()], "bocas": list(BOCAS),
        "rotina": [{"id": k, "label": r["nome"], "opcoes": [{"valor": v, "label": l} for v, l in r["opcoes"]]} for k, r in ROTINA.items()],
    }


# ----------------------------------------------------------------------------- como ela diria
def nivel(value: Any) -> Optional[str]:
    """Interpreta respostas livres como sim / nao / parcial (``None`` = ainda não respondeu)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "sim" if value else "nao"
    if isinstance(value, (int, float)):
        return "sim" if value > 0 else "nao"
    text = norm_text(str(value))
    if text in {"nao", "n", "no", "false", "nenhum", "nenhuma", "nao tem", "quebrado", "quebrada", "nao funciona", "0", "nunca fiz", "nunca"}:
        return "nao"
    if any(p in text for p in ("mais ou menos", "aprend", "parcial", "pouco", "as vezes", "razoavel", "basico", "ja tentei")):
        return "parcial"
    return "sim"


def _palavras(texto: str) -> tuple[str, ...]:
    """Palavras sem acento e sem plural simples: 'facas' e 'faca', 'bocas' e 'boca' são a mesma coisa."""
    return tuple(p[:-1] if len(p) > 3 and p.endswith("s") and not p.endswith("ss") else p for p in re.findall(r"[a-z0-9]+", norm_text(texto)))


def _contem(palavras: tuple[str, ...], termo: tuple[str, ...]) -> bool:
    n = len(termo)
    return n > 0 and any(palavras[i:i + n] == termo for i in range(len(palavras) - n + 1))


# Todos os termos de todos os itens, do mais específico (mais palavras) ao mais geral; no empate vale a ordem do catálogo.
_INDICE: list[tuple[tuple[str, ...], str]] = sorted(
    ((_palavras(t), i.id) for i in CATALOGO for t in (i.nome, *i.termos)), key=lambda par: -len(par[0]))
_EXATOS: dict[str, str] = {norm_text(t): i.id for i in reversed(CATALOGO) for t in (i.nome, *i.termos)}


def id_por_termos(texto: str, grupos: Optional[Iterable[str]] = None) -> Optional[str]:
    """Item do catálogo que o texto cita ('fogão de 4 bocas' → fogao; 'ponto da carne' → grelhar). O termo mais
    específico ganha; palavra inteira, não pedaço de palavra ('informação' não é 'forma')."""
    palavras = _palavras(texto)
    permitidos = set(grupos) if grupos else None
    for termo, item_id in _INDICE:
        if permitidos is not None and POR_ID[item_id].grupo not in permitidos:
            continue
        if _contem(palavras, termo):
            return item_id
    return None


def id_exato(texto: str, grupos: Optional[Iterable[str]] = None) -> Optional[str]:
    """Igualdade com o nome ou um sinônimo do item (é o que as fichas usam: quem escreve a ficha erra alto)."""
    item_id = _EXATOS.get(norm_text(texto))
    if item_id is None or (grupos is not None and POR_ID[item_id].grupo not in set(grupos)):
        return None
    return item_id


def habilidade_id(nome: str) -> Optional[str]:
    return id_por_termos(nome, (HABILIDADE,))


def termos_de(item_id: str) -> tuple[str, ...]:
    """Como ela diria o item (para a trava de evidência procurar nas falas dela)."""
    item = POR_ID[item_id]
    return (norm_text(item.nome), *(norm_text(t) for t in item.termos))


# ----------------------------------------------------------------------------- o que ela tem (perfil salvo)
# Equipamentos e utensílios moram no mesmo dicionário do perfil (``equipamentos``); habilidades em ``tecnicas``.
_GRUPOS_DO_DICT = {EQUIPAMENTO: (EQUIPAMENTO, UTENSILIO), UTENSILIO: (EQUIPAMENTO, UTENSILIO), HABILIDADE: (HABILIDADE,)}
GRUPOS_DO_PERFIL = ("equipamentos", "tecnicas", "restricoes", "observacoes")


def _item_da_chave(chave: str, grupos: Iterable[str]) -> Optional[str]:
    """O item do catálogo que uma chave do perfil quer dizer: o próprio id, uma variante ('forno_eletrico', 'fogao_bocas') ou
    um nome que ela ou a consultora usaram antes ('airfryer', 'ponto de carne')."""
    grupos = tuple(grupos)
    if chave in POR_ID and POR_ID[chave].grupo in grupos:
        return chave
    for item in CATALOGO:
        if item.grupo in grupos and chave.startswith(item.id + "_"):
            return item.id
    return id_por_termos(chave.replace("_", " "), grupos)


def _chaves_do(item_id: str, salvo: dict[str, Any]) -> list[str]:
    """Todas as chaves do perfil que são este item, a exata primeiro."""
    grupos = _GRUPOS_DO_DICT[POR_ID[item_id].grupo]
    return [k for k in sorted(salvo, key=lambda k: k != item_id) if _item_da_chave(k, grupos) == item_id]


def _valor_salvo(item_id: str, salvo: dict[str, Any]) -> Optional[Any]:
    """O valor salvo para o item (``None`` se nunca foi dito)."""
    chaves = _chaves_do(item_id, salvo)
    return salvo[chaves[0]] if chaves else None


def estado_equipamento(equipamentos: dict[str, Any], item_id: str) -> Optional[str]:
    """``sim``, ``nao``, ``parcial`` (funciona mais ou menos) ou ``None`` (ainda não disse)."""
    return nivel(_valor_salvo(item_id, equipamentos or {}))


def estado_habilidade(tecnicas: dict[str, Any], item_id: str) -> Optional[str]:
    return nivel(_valor_salvo(item_id, tecnicas or {}))


def da_perfil(perfil: dict[str, Any]) -> dict[str, Any]:
    """O perfil salvo como a tela precisa: só o catálogo, com valor limpo (True/False/bocas, sim/aprendendo/nao)."""
    equip, tec, rot = perfil.get("equipamentos") or {}, perfil.get("tecnicas") or {}, perfil.get("restricoes") or {}
    equipamentos: dict[str, Any] = {}
    for item in CATALOGO:
        if item.grupo == HABILIDADE:
            continue
        bruto = _valor_salvo(item.id, equip)
        estado = nivel(bruto)
        if estado is None:
            continue
        equipamentos[item.id] = False if estado == "nao" else (int(bruto) if item.id == "fogao" and isinstance(bruto, (int, float)) and not isinstance(bruto, bool) else True)
    habilidades: dict[str, str] = {}
    for item in itens(HABILIDADE):
        estado = nivel(_valor_salvo(item.id, tec))
        if estado is not None:
            habilidades[item.id] = {"sim": "sim", "parcial": "aprendendo", "nao": "nao"}[estado]
    rotina = {chave: rotina_canonica(chave, rot[chave]) for chave in ROTINA if rot.get(chave) not in (None, "")}
    return {"equipamentos": equipamentos, "habilidades": habilidades, "rotina": rotina}


def resumo(perfil: dict[str, Any]) -> dict[str, Any]:
    """Quanto da cozinha ela já respondeu e o que ficou de fora (para a tela e para a consultora)."""
    salvo = da_perfil(perfil)
    todos = [i for i in CATALOGO]
    respondidos = [i for i in todos if (i.id in salvo["habilidades"] if i.grupo == HABILIDADE else i.id in salvo["equipamentos"])]
    return {
        "total": len(todos), "respondidos": len(respondidos),
        "tem": [i.id for i in todos if i.grupo != HABILIDADE and salvo["equipamentos"].get(i.id)],
        "nao_tem": [i.id for i in todos if i.grupo != HABILIDADE and salvo["equipamentos"].get(i.id) is False],
        "sem_resposta": [i.id for i in todos if i not in respondidos],
        "sabe": [i for i, v in salvo["habilidades"].items() if v == "sim"],
        "aprendendo": [i for i, v in salvo["habilidades"].items() if v == "aprendendo"],
        "nao_sabe": [i for i, v in salvo["habilidades"].items() if v == "nao"],
    }


def _lista(ids: list[str], limite: int = 14) -> str:
    nomes = [POR_ID[i].rotulo for i in ids]
    return ", ".join(nomes[:limite]) + (f" e mais {len(nomes) - limite}" if len(nomes) > limite else "")


def _com_bocas(salvo: dict[str, Any], item_id: str) -> str:
    bocas = salvo["equipamentos"].get(item_id)
    if item_id == "fogao" and isinstance(bocas, int) and not isinstance(bocas, bool):
        return f"fogão ({bocas} bocas)"
    return POR_ID[item_id].rotulo


def texto_do_perfil(perfil: dict[str, Any], para_ela: bool = False) -> str:
    """Resumo curto da cozinha dela. Para a consultora (painel do turno, ``perfil_cozinha_consultar``) com marcadores em maiúsculas;
    ``para_ela``: a frase que a consultora repete para a Dona Maria depois de salvar (só o que está no banco, sem inventar)."""
    salvo, r = da_perfil(perfil), resumo(perfil)
    if not r["respondidos"] and not salvo["rotina"]:
        return "ainda não marcou nada em Minha cozinha (menu): pergunte o que falta, ou peça para ela preencher a tela."
    partes = []
    if r["tem"]:
        partes.append(("Você tem: " if para_ela else "TEM: ") + ", ".join(_com_bocas(salvo, i) for i in r["tem"]))
    if r["nao_tem"]:
        partes.append(("Não tem: " if para_ela else "NÃO TEM: ") + _lista(r["nao_tem"]))
    habilidades = ([("Sabe", r["sabe"]), ("está aprendendo", r["aprendendo"]), ("não sabe", r["nao_sabe"])] if para_ela
                   else [("SABE", r["sabe"]), ("está APRENDENDO", r["aprendendo"]), ("NÃO SABE", r["nao_sabe"])])
    partes += [f"{rot}: " + ", ".join(POR_ID[i].frase for i in ids) for rot, ids in habilidades if ids]
    if salvo["rotina"]:
        rotina = []
        for chave, valor in salvo["rotina"].items():
            legivel = dict(ROTINA[chave]["opcoes"]).get(valor, valor)
            rotina.append(f"{ROTINA[chave]['nome'].lower()} {str(legivel).lower()}")
        partes.append("rotina: " + ", ".join(rotina))
    extras = [o for o in outros(perfil) if o["grupo"] != "observacoes"]
    if extras:
        partes.append("também contou: " + ", ".join(f"{o['nome'].lower()} ({o['texto']})" for o in extras[:6]))
    if r["sem_resposta"] and not para_ela:
        partes.append(f"sem resposta: {len(r['sem_resposta'])} de {r['total']} itens")
    return "; ".join(partes) + "."


# ----------------------------------------------------------------------------- o que a tela envia
def _equipamentos_da_tela(bruto: Any) -> dict[str, Any]:
    if bruto in (None, ""):
        return {}
    if not isinstance(bruto, dict):
        raise ValueError("'equipamentos' precisa ser um objeto {id: true/false}")
    limpo: dict[str, Any] = {}
    for item_id, valor in bruto.items():
        item = POR_ID.get(str(item_id))
        if item is None or item.grupo == HABILIDADE:
            raise ValueError(f"'{item_id}' não está na lista de equipamentos e utensílios")
        if isinstance(valor, bool):
            limpo[item.id] = valor
        elif item.id == "fogao" and isinstance(valor, int) and valor in BOCAS:
            limpo[item.id] = valor
        else:
            raise ValueError(f"valor inválido para {item.rotulo}: use sim ou não" + (" (ou o número de bocas)" if item.id == "fogao" else ""))
    return limpo


def _habilidades_da_tela(bruto: Any) -> dict[str, Optional[str]]:
    if bruto in (None, ""):
        return {}
    if not isinstance(bruto, dict):
        raise ValueError("'habilidades' precisa ser um objeto {id: sim/aprendendo/nao}")
    limpo: dict[str, Optional[str]] = {}
    for item_id, valor in bruto.items():
        item = POR_ID.get(str(item_id))
        if item is None or item.grupo != HABILIDADE:
            raise ValueError(f"'{item_id}' não está na lista de habilidades")
        if valor in (None, ""):
            limpo[item.id] = None  # voltou a "não respondi"
        elif valor in NIVEIS:
            limpo[item.id] = valor
        else:
            raise ValueError(f"escolha 'sei fazer', 'estou aprendendo' ou 'não sei' para {item.rotulo}")
    return limpo


def _rotina_da_tela(bruto: Any) -> dict[str, Any]:
    if bruto in (None, ""):
        return {}
    if not isinstance(bruto, dict):
        raise ValueError("'rotina' precisa ser um objeto")
    limpo: dict[str, Any] = {}
    for chave, valor in bruto.items():
        if chave not in ROTINA:
            raise ValueError(f"'{chave}' não está na lista da rotina da cozinha")
        if valor in (None, ""):
            limpo[chave] = None
            continue
        validos = [v for v, _ in ROTINA[chave]["opcoes"]]
        if isinstance(valor, bool) or valor not in validos:
            raise ValueError(f"escolha uma das opções de {ROTINA[chave]['nome'].lower()}")
        limpo[chave] = valor
    return limpo


def _remover_da_tela(bruto: Any) -> list[dict[str, str]]:
    if bruto in (None, ""):
        return []
    if not isinstance(bruto, list):
        raise ValueError("'remover' precisa ser uma lista de {grupo, chave}")
    saida = []
    for r in bruto:
        if not isinstance(r, dict) or r.get("grupo") not in GRUPOS_DO_PERFIL or not str(r.get("chave") or "").strip():
            raise ValueError("para remover, informe o grupo (equipamentos, tecnicas, restricoes ou observacoes) e a chave")
        saida.append({"grupo": r["grupo"], "chave": str(r["chave"])[:200]})
    return saida


def normalizar(dados: Any) -> dict[str, Any]:
    """Valida o que veio da tela. Só o que vier é alterado; ``None`` volta o item a "não respondi" (só habilidade e rotina:
    equipamento é sim ou não). ``remover`` apaga o que a conversa gravou fora do catálogo. Levanta ``ValueError`` com
    mensagem para a pessoa."""
    if not isinstance(dados, dict):
        raise ValueError("a cozinha precisa ser um objeto")
    return {"equipamentos": _equipamentos_da_tela(dados.get("equipamentos")),
            "habilidades": _habilidades_da_tela(dados.get("habilidades")),
            "rotina": _rotina_da_tela(dados.get("rotina")),
            "remover": _remover_da_tela(dados.get("remover"))}


def aplicar(perfil: dict[str, Any], limpo: dict[str, Any]) -> dict[str, Any]:
    """O perfil depois de aplicar o que a tela enviou. O id do catálogo vira a chave de verdade: variantes antigas do mesmo item
    ('forno_eletrico') saem, para não sobrar duas respostas para a mesma coisa."""
    novo = {"equipamentos": dict(perfil.get("equipamentos") or {}), "tecnicas": dict(perfil.get("tecnicas") or {}),
            "restricoes": dict(perfil.get("restricoes") or {}), "observacoes": list(perfil.get("observacoes") or [])}
    for item_id, valor in limpo["equipamentos"].items():
        for chave in _chaves_do(item_id, novo["equipamentos"]):
            novo["equipamentos"].pop(chave)
        novo["equipamentos"][item_id] = valor
    for item_id, valor in limpo["habilidades"].items():
        for chave in _chaves_do(item_id, novo["tecnicas"]):
            novo["tecnicas"].pop(chave)
        if valor is not None:
            novo["tecnicas"][item_id] = valor
    for chave, valor in limpo["rotina"].items():
        if valor is None:
            novo["restricoes"].pop(chave, None)
        else:
            novo["restricoes"][chave] = valor
    for r in limpo.get("remover", []):
        if r["grupo"] == "observacoes":
            novo["observacoes"] = [o for o in novo["observacoes"] if o != r["chave"]]
        else:
            novo[r["grupo"]].pop(r["chave"], None)
    return novo


def outros(perfil: dict[str, Any]) -> list[dict[str, str]]:
    """O que a conversa gravou e o catálogo não tem ('massa fresca', 'panela de barro', observações): a tela mostra, para
    nada do que foi coletado ficar escondido, e deixa ela apagar o que estiver errado."""
    legivel = {"sim": "sei fazer", "parcial": "estou aprendendo", "nao": "não sei"}
    saida: list[dict[str, str]] = []
    for grupo, grupos_do_catalogo in (("equipamentos", _GRUPOS_DO_DICT[EQUIPAMENTO]), ("tecnicas", _GRUPOS_DO_DICT[HABILIDADE])):
        for chave, valor in (perfil.get(grupo) or {}).items():
            if _item_da_chave(chave, grupos_do_catalogo):
                continue
            estado = nivel(valor)
            if grupo == "equipamentos":
                texto = str(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) and estado == "sim" else {"sim": "tem", "parcial": "mais ou menos", "nao": "não tem"}.get(estado or "", str(valor))
            else:
                texto = legivel.get(estado or "", str(valor))
            saida.append({"grupo": grupo, "chave": chave, "nome": chave.replace("_", " ").capitalize(), "texto": texto[:80]})
    for chave, valor in (perfil.get("restricoes") or {}).items():
        if chave not in ROTINA:
            saida.append({"grupo": "restricoes", "chave": chave, "nome": chave.replace("_", " ").capitalize(),
                          "texto": ("sim" if valor is True else "não" if valor is False else str(valor))[:80]})
    for obs in perfil.get("observacoes") or []:
        saida.append({"grupo": "observacoes", "chave": str(obs), "nome": "Observação", "texto": str(obs)[:160]})
    return saida


def perguntas_da_cozinha(pendencias: list[dict[str, str]]) -> str:
    """As perguntas da elicitação do desafio (equipamentos e utensílios, técnicas, restrições), prontas para a consultora copiar
    na primeira resposta. Só entra o que ainda falta: o que ela marcou em Minha cozinha ou já contou não se pergunta de novo."""
    por_grupo = {g: [p["pergunta"] for p in pendencias if p["grupo"] == g] for g in ("equipamentos", "tecnicas", "restricoes")}
    blocos = [("Equipamentos e utensílios", por_grupo["equipamentos"]), ("Técnicas que você domina", por_grupo["tecnicas"]),
              ("Rotina da cozinha", por_grupo["restricoes"])]
    linhas = [f"{n}. **{titulo}:** {' '.join(perguntas)}" for n, (titulo, perguntas) in enumerate([b for b in blocos if b[1]], 1)]
    if not linhas:
        return ""
    return ("Antes de escolhermos um prato, preciso saber se a sua cozinha dá conta. Me conta:\n" + "\n".join(linhas)
            + "\nPode responder tudo de uma vez, do seu jeito. Se preferir, marque em **Minha cozinha** (menu): o que você contar aqui aparece lá.")


# ----------------------------------------------------------------------------- as fichas (prompts/base-de-conhecimento)
Grupo = tuple[str, ...]  # ids que servem (A ou B): basta um


@dataclass(frozen=True)
class FichaPrato:
    nome: str
    categoria: str
    precisa: tuple[Grupo, ...]
    habilidades: tuple[str, ...]
    custo_tipico: float
    observacao: str


@dataclass(frozen=True)
class RegraIngrediente:
    termos: tuple[tuple[str, ...], ...]
    nome: str
    precisa: tuple[Grupo, ...]
    para: str
    aviso: str


@dataclass(frozen=True)
class Base:
    pratos: dict[str, FichaPrato]
    regras: tuple[RegraIngrediente, ...]


def raiz() -> Optional[Path]:
    """Pasta ``cozinha-e-equipamentos``: variável de ambiente, o repositório (no host) ou ``/prompts`` (containers)."""
    candidatos: list[Path] = []
    env = os.environ.get("SABOR_DA_MARIA_BASE_CONHECIMENTO")
    if env:
        candidatos.append(Path(env))
    candidatos += [p / "prompts" / "base-de-conhecimento" for p in Path(__file__).resolve().parents]
    candidatos.append(Path("/prompts/base-de-conhecimento"))
    for base in candidatos:
        pasta = base / NOME_DA_BASE
        if pasta.is_dir():
            return pasta
    return None


def _tabela(texto: str) -> Iterable[tuple[int, list[str]]]:
    for numero, bruta in enumerate(texto.splitlines(), 1):
        linha = bruta.strip()
        if not linha.startswith("|"):
            continue
        celulas = [c.strip() for c in linha.strip("|").split("|")]
        if not celulas or celulas[0].lstrip("-: ") == "":
            continue  # separador
        yield numero, celulas


def _grupos(texto: str, onde: str) -> tuple[Grupo, ...]:
    """'forno, liquidificador ou processador' → ((forno,), (liquidificador, processador)). Nome desconhecido é erro, com a linha."""
    saida: list[Grupo] = []
    for pedaco in (p.strip() for p in texto.split(",")):
        if not pedaco or pedaco in ("-", "—"):
            continue
        ids = []
        for nome in re.split(r"\s+ou\s+", pedaco):
            item_id = id_exato(nome.strip(), (EQUIPAMENTO, UTENSILIO))
            if item_id is None:
                raise ValueError(f"{onde}: '{nome.strip()}' não é um equipamento nem um utensílio do catálogo")
            ids.append(item_id)
        saida.append(tuple(ids))
    return tuple(saida)


def _custo(texto: str, onde: str) -> float:
    achado = re.search(r"\d+(?:[.,]\d+)?", texto)
    if not achado or float(achado.group().replace(",", ".")) <= 0:
        raise ValueError(f"{onde}: custo típico precisa ser um número maior que zero, veio {texto!r}")
    return float(achado.group().replace(",", "."))


def analisar_pratos(texto: str, arquivo: str = "pratos.md") -> list[FichaPrato]:
    """``| Prato | Precisa de | Habilidades | Custo típico (R$) | Observação |`` (as categorias são só títulos ``##``)."""
    fichas: list[FichaPrato] = []
    categoria = ""
    for numero, bruta in enumerate(texto.splitlines(), 1):
        linha = bruta.strip()
        if linha.startswith("## "):
            categoria = linha[3:].strip()
            continue
        if not linha.startswith("|"):
            continue
        celulas = [c.strip() for c in linha.strip("|").split("|")]
        if not celulas or celulas[0].lstrip("-: ") == "" or celulas[0].lower() == "prato":
            continue  # separador e cabeçalho
        onde = f"{arquivo}, linha {numero}"
        if len(celulas) != 5:
            raise ValueError(f"{onde}: a tabela precisa de 5 colunas, veio {len(celulas)}")
        nome, precisa, habilidades, custo, observacao = celulas
        hab: list[str] = []
        for h in (p.strip() for p in habilidades.split(",")):
            if not h or h in ("-", "—"):
                continue
            hid = id_exato(h, (HABILIDADE,))
            if hid is None:
                raise ValueError(f"{onde}: '{h}' não é uma habilidade do catálogo")
            hab.append(hid)
        fichas.append(FichaPrato(nome, categoria, _grupos(precisa, onde), tuple(hab), _custo(custo, onde), observacao))
    return fichas


def analisar_ingredientes(texto: str, arquivo: str = "ingredientes.md") -> list[RegraIngrediente]:
    """``| Ingrediente | Precisa de | Serve para | Aviso |``; o ingrediente aceita sinônimos separados por ``/``."""
    regras: list[RegraIngrediente] = []
    for numero, celulas in _tabela(texto):
        if celulas[0].lower() == "ingrediente":
            continue
        onde = f"{arquivo}, linha {numero}"
        if len(celulas) != 4:
            raise ValueError(f"{onde}: a tabela precisa de 4 colunas, veio {len(celulas)}")
        nomes, precisa, para, aviso = celulas
        sinonimos = [n.strip() for n in nomes.split("/") if n.strip()]
        grupos = _grupos(precisa, onde)
        if not sinonimos or not grupos:
            raise ValueError(f"{onde}: informe o ingrediente e o que ele precisa")
        regras.append(RegraIngrediente(tuple(_palavras(n) for n in sinonimos), sinonimos[0], grupos, para, aviso))
    return regras


_LOCK = threading.Lock()
_CACHE: dict[str, tuple[tuple[int, ...], Base]] = {}
_VAZIA = Base({}, ())


def carregar(pasta: Optional[Path] = None) -> Base:
    """As fichas. Relê só quando um arquivo muda (editar a base vale sem reiniciar); arquivo com defeito é isolado."""
    pasta = pasta or raiz()
    if pasta is None:
        return _VAZIA
    arquivos = [pasta / "pratos.md", pasta / "ingredientes.md"]
    assinatura = tuple(a.stat().st_mtime_ns if a.exists() else 0 for a in arquivos)
    with _LOCK:
        em_cache = _CACHE.get(str(pasta))
        if em_cache and em_cache[0] == assinatura:
            return em_cache[1]
    pratos: dict[str, FichaPrato] = {}
    regras: list[RegraIngrediente] = []
    try:
        if arquivos[0].exists():
            pratos = {_chave_prato(f.nome): f for f in analisar_pratos(arquivos[0].read_text(encoding="utf-8"), "pratos.md")}
    except ValueError as exc:
        logger.warning("cozinha: %s", exc)
    try:
        if arquivos[1].exists():
            regras = analisar_ingredientes(arquivos[1].read_text(encoding="utf-8"), "ingredientes.md")
    except ValueError as exc:
        logger.warning("cozinha: %s", exc)
    base = Base(pratos, tuple(regras))
    with _LOCK:
        _CACHE[str(pasta)] = (assinatura, base)
    return base


def _chave_prato(nome: str) -> str:
    return " ".join(_palavras(re.sub(r"\([^)]*\)", " ", nome)))


def _palavras_do_prato(nome: str) -> frozenset[str]:
    return frozenset(_palavras(re.sub(r"[()]", " ", nome))) - {"de", "da", "do", "com", "e", "a", "o"}


def ficha_do_prato(nome: Optional[str], base: Optional[Base] = None) -> Optional[FichaPrato]:
    """A ficha do prato pelo nome. Igualdade (com ou sem o parêntese: 'Coxinha de frango' é 'Coxinha de frango (porção com 6)');
    senão o único prato cujas palavras contêm as do nome ('pf com bife' não vale; 'coxinha' vale). Ambíguo é ``None``."""
    base = base or carregar()
    if not nome or not str(nome).strip():
        return None
    achada = base.pratos.get(_chave_prato(str(nome)))
    if achada:
        return achada
    quer = _palavras_do_prato(str(nome))
    if not quer:
        return None
    candidatos = [f for f in base.pratos.values() if quer <= _palavras_do_prato(f.nome)]
    return candidatos[0] if len(candidatos) == 1 else None


def regra_do_ingrediente(nome: Optional[str], base: Optional[Base] = None) -> Optional[RegraIngrediente]:
    """A regra do ingrediente: o sinônimo mais específico (mais palavras) que o nome cita, como palavra inteira."""
    base = base or carregar()
    palavras = _palavras(str(nome or ""))
    melhor: Optional[RegraIngrediente] = None
    tamanho = 0
    for regra in base.regras:
        for termo in regra.termos:
            if len(termo) > tamanho and _contem(palavras, termo):
                melhor, tamanho = regra, len(termo)
    return melhor


def custo_tipico(nome: Optional[str]) -> Optional[float]:
    ficha = ficha_do_prato(nome)
    return ficha.custo_tipico if ficha else None


# ----------------------------------------------------------------------------- a conferência
def _ou(ids: Grupo) -> str:
    nomes = [POR_ID[i].rotulo for i in ids]
    return nomes[0] if len(nomes) == 1 else ", ".join(nomes[:-1]) + " ou " + nomes[-1]


def _e(partes: list[str]) -> str:
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " e " + partes[-1]


def _maiuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:]


def conferir(prato: Optional[str], ingredientes: Iterable[str], perfil: dict[str, Any]) -> dict[str, Any]:
    """Confere um prato (pela ficha) e os ingredientes (pelas regras) contra a cozinha dela.

    ``status``: ``nao_da`` (falta equipamento ou utensílio que ela disse não ter), ``atencao`` (ela disse que não sabe ou está
    aprendendo uma habilidade), ``confirmar`` (falta ela responder), ``ok`` ou ``sem_ficha`` (nada na base para conferir).
    ``mensagens`` já vêm prontas para a tela e para a consultora repetirem.
    """
    equip, tec = perfil.get("equipamentos") or {}, perfil.get("tecnicas") or {}
    base = carregar()
    ficha = ficha_do_prato(prato, base)
    exigencias: list[tuple[str, str, str, Grupo]] = []  # (origem, de, para, alternativas)
    if ficha:
        exigencias += [("prato", ficha.nome, "", g) for g in ficha.precisa]
    regras_achadas = 0
    for ing in ingredientes:
        regra = regra_do_ingrediente(ing, base)
        if regra:
            regras_achadas += 1
            exigencias += [("ingrediente", " ".join(str(ing).split()), regra.para, g) for g in regra.precisa]

    falta: list[dict[str, Any]] = []
    confirmar: list[dict[str, Any]] = []
    for origem, de, para, grupo in exigencias:
        estados = [estado_equipamento(equip, i) for i in grupo]
        if any(e in ("sim", "parcial") for e in estados):
            continue
        entrada = {"origem": origem, "de": de, "para": para, "precisa": _ou(grupo), "ids": list(grupo)}
        (falta if all(e == "nao" for e in estados) else confirmar).append(entrada)

    habilidades: dict[str, list[str]] = {"faltam": [], "aprendendo": [], "confirmar": []}
    for hid in (ficha.habilidades if ficha else ()):
        estado = estado_habilidade(tec, hid)
        if estado == "sim":
            continue
        habilidades[{"nao": "faltam", "parcial": "aprendendo"}.get(estado or "", "confirmar")].append(hid)

    if falta:
        status = "nao_da"
    elif habilidades["faltam"] or habilidades["aprendendo"]:
        status = "atencao"
    elif confirmar or habilidades["confirmar"]:
        status = "confirmar"
    else:
        status = "ok" if (ficha or regras_achadas) else "sem_ficha"
    return {"prato": ficha.nome if ficha else (prato or None), "ficha": ficha is not None, "status": status, "falta": falta,
            "confirmar": confirmar, "habilidades": habilidades, "observacao": ficha.observacao if ficha else "",
            "mensagens": _mensagens(ficha, prato, falta, confirmar, habilidades)}


def _mensagens(ficha: Optional[FichaPrato], prato: Optional[str], falta: list[dict[str, Any]], confirmar: list[dict[str, Any]],
               habilidades: dict[str, list[str]]) -> list[str]:
    """As frases prontas. O que ela disse que NÃO tem vem sozinho: com a cozinha dela o prato não sai, o resto é secundário."""
    nome = ficha.nome if ficha else (prato or "o prato")
    msgs: list[str] = []
    do_prato = [f["precisa"] for f in falta if f["origem"] == "prato"]
    if do_prato:
        msgs.append(f"Com a sua cozinha não dá para fazer {nome}: ele precisa de {_e(do_prato)}, e você marcou que não tem. "
                    "Os ingredientes não viram esse prato sem isso. Se você tem, marque em Minha cozinha; se não, vale escolher outro prato.")
    for f in (f for f in falta if f["origem"] == "ingrediente"):
        para = f["para"] or "esse prato"
        msgs.append(f"{_maiuscula(f['de'])} precisa de {f['precisa']} para virar {para}, e você marcou que não tem: com a sua cozinha "
                    f"não tem como fazer {para} com esse ingrediente. Se você tem, marque em Minha cozinha.")
    if msgs:
        return msgs
    if habilidades["faltam"]:
        msgs.append(f"Você marcou que não sabe {_e([POR_ID[h].frase for h in habilidades['faltam']])}, e {nome} depende disso. "
                    "Dá para aprender, mas não é o melhor prato para começar.")
    if habilidades["aprendendo"]:
        msgs.append(f"Você marcou que está aprendendo a {_e([POR_ID[h].frase for h in habilidades['aprendendo']])}, que {nome} pede: "
                    "comece com poucas porções para não perder ingrediente.")
    do_prato_a_confirmar = list(dict.fromkeys(f["precisa"] for f in confirmar if f["origem"] == "prato"))  # na ordem da ficha
    partes = []
    if do_prato_a_confirmar:
        partes.append("se tem " + _e(do_prato_a_confirmar))
    if habilidades["confirmar"]:
        partes.append("se sabe " + _e([POR_ID[h].frase for h in habilidades["confirmar"]]))
    if partes:
        msgs.append(f"Falta você dizer {_e(partes)}: {nome} depende disso. Responda em Minha cozinha ou aqui na conversa.")
    for f in (f for f in confirmar if f["origem"] == "ingrediente"):
        para = f["para"] or "esse prato"
        msgs.append(f"Falta você dizer se tem {f['precisa']}: {f['de']} precisa disso para virar {para}. Responda em Minha cozinha ou aqui na conversa.")
    return msgs


# ----------------------------------------------------------------------------- uso no Analytics e no carrinho
def anotar_ranking(ranking: list[dict[str, Any]], perfil: dict[str, Any]) -> list[dict[str, Any]]:
    """Acrescenta a cada prato do Analytics a conferência com a cozinha dela e o quanto cobrar (``preco``)."""
    for item in ranking:
        item["cozinha"] = conferir(item["prato"], [], perfil)
        ficha = ficha_do_prato(item["prato"])
        item["preco"] = None
        if ficha:
            item["preco"] = {**costing.quanto_cobrar(ficha.custo_tipico, item.get("preco_medio")), "custo_exemplo": True, "aviso": AVISO_CUSTO}
    return ranking


def alertas_do_carrinho(itens_do_carrinho: list[dict[str, Any]], perfil: dict[str, Any]) -> list[dict[str, Any]]:
    """Um aviso por prato aberto (ou por ingrediente avulso) que a cozinha dela não faz, faz com ressalva ou ainda não confirmou."""
    pratos: dict[str, list[dict[str, Any]]] = {}
    avulsos: list[dict[str, Any]] = []
    for linha in itens_do_carrinho:
        (pratos.setdefault(linha["prato"], []) if linha.get("prato") else avulsos).append(linha)
    alertas: list[dict[str, Any]] = []
    for nome_prato, linhas in pratos.items():
        c = conferir(nome_prato, [i["nome"] for i in linhas], perfil)
        if c["status"] in ("nao_da", "atencao", "confirmar"):
            alertas.append({"prato": nome_prato, "item_id": None, "nivel": c["status"], "mensagens": c["mensagens"]})
    for linha in avulsos:
        c = conferir(linha.get("motivo"), [linha["nome"]], perfil)
        if c["status"] in ("nao_da", "atencao", "confirmar"):
            alertas.append({"prato": None, "item_id": linha["id"], "nivel": c["status"], "mensagens": c["mensagens"]})
    return alertas
