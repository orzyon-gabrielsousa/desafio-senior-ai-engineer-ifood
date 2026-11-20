"""Adaptações locais de rascunhos conferidos quando a pessoa dispensa a compra."""

from __future__ import annotations

import re
from typing import Any

from . import fonte
from .montagem import nome_da_medida
from .units import norm_text

_TEMPEROS_DISPENSAVEIS = {
    "oregano", "pimenta do reino", "pimenta preta", "salsinha", "cebolinha", "cheiro verde",
}
_FERMENTO_BIOLOGICO = re.compile(r"fermento biologico(?: seco| fresco)?")
_RECHEIOS_DISPENSAVEIS = re.compile(r"(?:presunto|calabresa)(?: fatiad[ao])?")
_INGREDIENTES_PIZZA = (
    ("farinha", r"farinha de trigo"),
    ("agua", r"agua(?: morna| potavel| filtrada| fria)?"),
    ("azeite", r"(?:azeite(?: de oliva(?: extra virgem)?)?|oleo(?: de soja| vegetal)?)"),
    ("ovo", r"ovos?"),
    ("sal", r"sal"),
    ("acucar", r"acucar"),
    ("frango", r"(?:peito de )?frango(?: cozido| desfiado| cozido e desfiado)?"),
    ("cebola", r"cebola(?: picada| fatiada)?"),
    ("alho", r"alho(?: picado| amassado)?"),
    ("tomate", r"tomates?(?: em rodelas| fatiados?| picados?)?"),
    ("molho", r"molho de tomate(?: caseiro)?"),
    ("queijo", r"(?:fatias de )?(?:queijo(?: mussarela| mucarela| mozarela)?|mussarela|mucarela|mozarela)"),
    ("tempero", r"oregano|pimenta do reino|pimenta preta|salsinha|cebolinha|cheiro verde"),
)
_INGREDIENTES_PIZZA_DE_ARROZ = (
    ("arroz", r"arroz(?: branco)? cozido"),
    ("farinha", r"farinha de trigo"),
    ("ovo", r"ovos?(?: inteiros?)?"),
    ("liquido", r"(?:leite(?: integral)?|agua)"),
    ("gordura", r"(?:oleo(?: de soja| vegetal)?|azeite(?: de oliva)?)"),
    ("queijo_massa", r"(?:queijo (?:parmesao )?ralado|queijo parmesao|parmesao ralado)"),
    ("frango", r"(?:peito de )?frango (?:cozido(?: e desfiado)?|desfiado)"),
    ("queijo_cobertura", r"(?:queijo )?(?:mussarela|mucarela|mozarela)"),
    ("tomate", r"tomates?(?: picados?)?(?: em fatias)?"),
    ("molho", r"(?:molho|extrato) de tomate"),
)
_DISPENSAVEIS_NA_PIZZA_DE_ARROZ = re.compile(r"fermento (?:em po|quimico)|presunto|oregano|sal e temperos?")


def _chave(nome: str) -> str:
    return norm_text(nome_da_medida(nome))


def _omissoes_da_ficha(dados: dict[str, Any]) -> dict[str, str]:
    registradas = dados.get("ingredientes_omitidos") or []
    notas = (nota["ingrediente"] for nota in dados.get("ingredientes_faltantes") or []
             if nota.get("estado") == "omitido_na_adaptacao" and nota.get("ingrediente"))
    return {_chave(nome): nome_da_medida(nome) for nome in (*registradas, *notas)}


def corrigir_pizza_de_arroz(receita: dict[str, Any]) -> dict[str, Any] | None:
    """Retoma uma adaptação incompleta com medidas da publicação e método sem fermento."""
    dados = receita["dados"]
    original = dados.get("fonte_verificada") or {}
    if (not original.get("porcoes") or receita.get("porcoes") != original["porcoes"]
            or not re.search(r"\bpizza\b", norm_text(original.get("nome", "")))):
        return None
    confirmadas = {_chave(nome): (nome, medida) for nome, medida in (dados.get("medidas_de_uso_confirmadas") or {}).items()}
    fora_da_versao = _omissoes_da_ficha(dados)
    por_funcao = {}
    omitidos = []
    for item in fonte.ingredientes_publicados(original):
        nome = item["nome"]
        chave = _chave(nome)
        confirmado = confirmadas.get(chave)
        if (chave in fora_da_versao or _DISPENSAVEIS_NA_PIZZA_DE_ARROZ.fullmatch(chave)) and not confirmado:
            omitidos.append("fermento em pó" if chave == "fermento em po" else nome_da_medida(nome).lower())
            continue
        funcao = next((funcao for funcao, padrao in _INGREDIENTES_PIZZA_DE_ARROZ if re.fullmatch(padrao, chave)), None)
        if not funcao or funcao in por_funcao or not (confirmado or item.get("quantidade")):
            return None  # Esta sequência de preparo não explica o uso de outro ingrediente.
        if confirmado:
            nome_atual, medida = confirmado
            item = {**item, "nome": nome_atual, "quantidade": medida["quantidade"], "unidade": medida["unidade"]}
        if chave == "extrato de tomate" and not confirmado:
            item = {**item, "nome": "Molho de tomate", "ingrediente_original": nome}
        por_funcao[funcao] = item
    if not {"arroz", "farinha", "ovo", "liquido", "gordura", "frango", "queijo_cobertura"} <= por_funcao.keys():
        return None
    if confirmadas.keys() - {_chave(item["nome"]) for item in por_funcao.values()}:
        return None
    liquido = "a água" if _chave(por_funcao["liquido"]["nome"]) == "agua" else "o leite"
    gordura = "do azeite" if _chave(por_funcao["gordura"]["nome"]).startswith("azeite") else "do óleo"
    molho = "o extrato de tomate" if _chave((por_funcao.get("molho") or {}).get("nome", "")) == "extrato de tomate" else "o molho de tomate"
    massa = ["o arroz cozido", "os ovos da ficha", liquido, "a farinha de trigo"]
    if "queijo_massa" in por_funcao:
        massa.append("o queijo ralado")
    cobertura = [rotulo for chave, rotulo in (("molho", molho), ("frango", "o frango já totalmente cozido e desfiado"),
                                               ("tomate", "o tomate fatiado"), ("queijo_cobertura", "a mussarela"))
                 if chave in por_funcao]
    preparo = (f"Bata {', '.join(massa)} no liquidificador e misture parte {gordura}. "
               "Use o restante para untar a assadeira. Espalhe a massa em camada fina e asse no forno preaquecido a 180 °C "
               "por cerca de 20 minutos, até firmar. "
               f"Distribua {', '.join(cobertura)} sobre a massa firme. "
               "Volte ao forno por mais 10 minutos, até a massa assar por completo e o queijo derreter.")
    adaptacao = "Massa de arroz pré-assada sem fermentação, com frango e queijo na cobertura. Mantive as medidas publicadas que cabem nessa versão."
    if omitidos:
        adaptacao += " Retirei " + ", ".join(omitidos) + " desta versão."
    if (por_funcao.get("molho") or {}).get("ingrediente_original"):
        adaptacao += " Usei molho de tomate no lugar do extrato."
    return {"ingredientes": list(por_funcao.values()), "porcoes": original["porcoes"], "ingredientes_omitidos": omitidos,
            "adaptacao": adaptacao, "modo_preparo": preparo, "tempo_preparo_min": 40,
            "tempo_preparo_base": "Estimativa local de 10 minutos de montagem e 30 minutos de forno. O tempo dos preparos caseiros é calculado à parte.",
            "equipamentos_necessarios": ["liquidificador", "assadeira", "forno"], "tecnicas_necessarias": ["assar"]}


def _preparo_sem_temperos(preparo: str, omitidos: list[str]) -> str | None:
    """Altera as instruções simples de tempero; frases ambíguas exigem outra proposta."""
    for nome in omitidos:
        termo = re.escape(_chave(nome)).replace(r"\ ", r"\s+").replace("oregano", "[oó]r[eé]gano")
        preparo = re.sub(
            rf"\b(?:finalize|tempere|salpique|polvilhe|decore)\s+(?:com\s+)?{termo}"
            r"(?:\s+a gosto)?\s+(?:e\s+)(?=sirva|leve|asse|reserve|misture)",
            "", preparo, flags=re.I)
        preparo = re.sub(
            rf"(?:^|(?<=[.!?])|\n)\s*(?:finalize|tempere|salpique|polvilhe|decore)\s+"
            rf"(?:com\s+)?{termo}(?:\s+a gosto)?\s*[.!](?=\s|$)",
            "", preparo, flags=re.I)
        preparo = re.sub(
            rf"(?:,\s+|\s+e\s+){termo}(?:\s+a gosto)?(?=\s*[,.;!?]|\s+e\s+|$)",
            "", preparo, flags=re.I)
        preparo = re.sub(rf"\s+com\s+{termo}(?:\s+a gosto)?(?=\s*[,.;!?]|$)", "", preparo, flags=re.I)
        if re.search(rf"\b{re.escape(_chave(nome))}\b", norm_text(preparo)):
            return None
    preparo = preparo.strip()
    return "Use os temperos da ficha atual. " + preparo if preparo else None


def _pizza_sem_fermento(dados: dict[str, Any], ingredientes: list[dict[str, Any]],
                       linhas: dict[str, dict[str, Any]]) -> tuple[str, str] | None:
    """Abre e pré-assa uma massa fina de farinha, água e os opcionais da ficha."""
    por_funcao: dict[str, dict[str, Any]] = {}
    for item in ingredientes:
        nome = str(item.get("nome") or "")
        funcao = next((chave for chave, padrao in _INGREDIENTES_PIZZA if re.fullmatch(padrao, _chave(nome))), None)
        if not funcao or (funcao in por_funcao and funcao != "tempero"):
            return None
        por_funcao[funcao] = item
    farinha = por_funcao.get("farinha")
    agua = por_funcao.get("agua")
    if not farinha or not agua or not ({"frango", "queijo"} & por_funcao.keys()):
        return None
    estoque_farinha = linhas.get(norm_text(farinha["nome"]), {})
    medida_agua = linhas.get(norm_text(agua["nome"]), {})
    massa_kg = estoque_farinha.get("necessario_base")
    agua_l = medida_agua.get("necessario_base")
    if (estoque_farinha.get("falta") != "nada" or estoque_farinha.get("unidade_base") != "kg"
            or medida_agua.get("falta") != "nada" or medida_agua.get("unidade_base") != "L"
            or not massa_kg or not agua_l):
        return None
    medidas_confirmadas = dados.get("medidas_de_uso_confirmadas") or {}
    ajustes = []
    if not 0.4 <= agua_l / massa_kg <= 0.62:
        if agua["nome"] in medidas_confirmadas:
            return None
        agua_ml = round(massa_kg * (460 if "ovo" in por_funcao else 540), 1)
        agua.update(quantidade=agua_ml, unidade="ml")
        ajustes.append(f"{agua_ml:g} ml de água")
    gordura = por_funcao.get("azeite")
    if gordura:
        linha_gordura = linhas.get(norm_text(gordura["nome"]), {})
        gordura_l = linha_gordura.get("necessario_base")
        if linha_gordura.get("unidade_base") == "L" and gordura_l and gordura_l / massa_kg > 0.1:
            if gordura["nome"] in medidas_confirmadas:
                return None
            gordura_ml = round(massa_kg * 50, 1)
            gordura.update(quantidade=gordura_ml, unidade="ml")
            ajustes.append(f"{gordura_ml:g} ml de {nome_da_medida(gordura['nome']).lower()}")
    massa = [rotulo for chave, rotulo in (("farinha", "a farinha"), ("sal", "o sal"), ("acucar", "o açúcar"),
                                         ("ovo", "o ovo"), ("azeite", "a gordura da massa")) if chave in por_funcao]
    preparo = [("Misture " + ", ".join(massa) + ". Acrescente a água da ficha aos poucos para formar uma massa firme. "
                "Deixe descansar por 5 minutos, divida em discos e abra em camada fina. "
                "Preasse os discos em forma no forno preaquecido a 220 °C por 12 minutos.")]
    if "frango" in por_funcao:
        preparo.append("Se o frango estiver cru, cozinhe-o completamente no fogão e desfie.")
    cobertura = [rotulo for chave, rotulo in (("frango", "o frango cozido"), ("cebola", "a cebola"), ("alho", "o alho"),
                                             ("molho", "o molho de tomate")) if chave in por_funcao]
    if cobertura:
        preparo.append("Aqueça no fogão até o recheio ficar firme, usando " + ", ".join(cobertura) + ".")
    itens_da_cobertura = ["o recheio"] if cobertura else []
    if "queijo" in por_funcao:
        itens_da_cobertura.append("o queijo da ficha")
    if "tomate" in por_funcao:
        itens_da_cobertura.append("o tomate fatiado")
    if "tempero" in por_funcao:
        itens_da_cobertura.append("os temperos da cobertura")
    preparo.append("Distribua sobre os discos " + " e ".join(itens_da_cobertura) +
                   ". Volte ao forno por 12 minutos, até a massa estar totalmente assada e dourada.")
    alteracoes = "Massa fina sem fermentação, aberta e pré-assada antes de receber a cobertura."
    if ajustes:
        alteracoes += " Para dar ponto à massa deste lote, ajuste para " + " e ".join(ajustes) + "."
    return " ".join(preparo), alteracoes


def _pizza_sem_fermento_com_fonte(receita: dict[str, Any], ingredientes: list[dict[str, Any]],
                                 linhas: dict[str, dict[str, Any]], omitidos: list[str]) -> tuple[dict[str, Any], list[str]] | None:
    dados = receita["dados"]
    # Ao corrigir o método, restaura contagem publicada de cebola e tomate do
    # mesmo lote se a versão improvisada trocou unidades por quilos inteiros.
    original = dados.get("fonte_verificada") or {}
    firmes = {_chave(nome) for nome in dados.get("medidas_de_uso_confirmadas") or {}}
    opcoes = {"cebola": [], "tomate": []}
    if receita["porcoes"] == original.get("porcoes"):
        for item in fonte.ingredientes_publicados(original):
            raiz = _chave(item["nome"]).split(" ", 1)[0]
            if raiz in opcoes and item.get("unidade") == "un" and item.get("quantidade"):
                opcoes[raiz].append(item)
    revisados = [dict(item) for item in ingredientes]
    for item in revisados:
        chave = _chave(item["nome"])
        propostas = opcoes.get(chave.split(" ", 1)[0], [])
        if (len(propostas) == 1 and chave not in firmes and norm_text(item.get("unidade") or "") == "kg"
                and (item.get("quantidade") or 0) >= propostas[0]["quantidade"]):
            item.update(quantidade=propostas[0]["quantidade"], unidade="un")
    pizza = _pizza_sem_fermento(dados, revisados, linhas)
    if not pizza:
        return None
    preparo, adaptacao = pizza
    rotulos = list(dict.fromkeys(nome_da_medida(n) for n in omitidos))
    adaptacao += " A versão usa o estoque e dispensa " + ", ".join(rotulos) + "."
    return ({"ingredientes": revisados, "porcoes": receita["porcoes"], "ingredientes_omitidos": rotulos,
             "modo_preparo": preparo, "adaptacao": adaptacao, "tempo_preparo_min": 60,
             "tempo_preparo_base": "Estimativa local para preparar a cobertura, abrir a massa e assar em duas etapas, sem fermentação."}, rotulos)


def _corrigir_pizza_sem_fermento(receita: dict[str, Any], ingredientes: list[dict[str, Any]],
                                linhas: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str]] | None:
    """Conserta a versão que já retirou o fermento mas ainda manda usar esse insumo."""
    dados = receita["dados"]
    omitidos = _omissoes_da_ficha(dados)
    if not any(_FERMENTO_BIOLOGICO.fullmatch(nome) for nome in omitidos):
        return None
    preparo = norm_text(dados.get("modo_preparo") or "")
    preparo = re.sub(r"\b(?:sem (?:usar |adicionar |colocar |incluir )?|"
                    r"nao (?:adicione|junte|incorpore|coloque|acrescente|use) )"
                    r"(?:o )?fermento(?: biologico(?: seco| fresco)?)?\b", "", preparo)
    if not re.search(r"\b(?:misture|adicione|junte|incorpore|coloque|acrescente)\b[^.]*\bfermento\b", preparo):
        return None
    if any(_chave(item["nome"]) in omitidos for item in ingredientes):
        return None
    return _pizza_sem_fermento_com_fonte(receita, ingredientes, linhas, list(omitidos.values()))


def propor(receita: dict[str, Any], resultado_ingredientes: dict[str, Any],
           ingrediente_solicitado: str | None = None) -> tuple[dict[str, Any], list[str]] | None:
    """Devolve os argumentos da nova versão e nomes omitidos, sem escrever na ficha."""
    dados = receita["dados"]
    ingredientes = dados.get("ingredientes") or []
    porcoes = receita.get("porcoes")
    if not porcoes or not ingredientes or any(not isinstance(i, dict) or not i.get("nome") for i in ingredientes):
        return None
    faltantes = {norm_text(nota["ingrediente"]) for nota in resultado_ingredientes.get("faltantes") or []}
    prato = norm_text(receita["nome"])
    pizza = re.search(r"\bpizza\b", prato)
    linhas = {norm_text(item["ingrediente"]): item for item in resultado_ingredientes.get("itens") or []}
    if pizza and not ingrediente_solicitado:
        if correcao := _corrigir_pizza_sem_fermento(receita, ingredientes, linhas):
            return correcao
    nomes_omitidos = [
        i["nome"] for i in ingredientes
        if norm_text(i["nome"]) in faltantes and _chave(i["nome"]) in _TEMPEROS_DISPENSAVEIS
        and not re.search(rf"\b{re.escape(_chave(i['nome']))}\b", prato)
    ]
    fermento = next((i["nome"] for i in ingredientes if norm_text(i["nome"]) in faltantes
                    and _FERMENTO_BIOLOGICO.fullmatch(_chave(i["nome"]))), None)
    preparo = str(dados.get("modo_preparo") or (dados.get("fonte_verificada") or {}).get("modo_preparo") or "")
    if fermento and pizza:
        recheios = [i["nome"] for i in ingredientes if norm_text(i["nome"]) in faltantes
                    and _RECHEIOS_DISPENSAVEIS.fullmatch(_chave(i["nome"]))
                    and not re.search(rf"\b{_chave(i['nome']).split(' ', 1)[0]}\b", prato)]
        omissoes = [fermento, *nomes_omitidos, *recheios]
        if not ingrediente_solicitado or _chave(ingrediente_solicitado) in {_chave(n) for n in omissoes}:
            restantes = [i for i in ingredientes if i["nome"] not in omissoes]
            if massa_fina := _pizza_sem_fermento_com_fonte(receita, restantes, linhas, omissoes):
                return massa_fina
    restantes = [dict(i) for i in ingredientes if i["nome"] not in nomes_omitidos]
    if not nomes_omitidos or not restantes or (ingrediente_solicitado and
            _chave(ingrediente_solicitado) not in {_chave(nome) for nome in nomes_omitidos}):
        return None
    preparo = _preparo_sem_temperos(preparo, nomes_omitidos)
    if not preparo:
        return None
    tempo = dados.get("tempo_preparo_min") or (dados.get("fonte_verificada") or {}).get("tempo_preparo_min")
    explicacao = str(dados.get("adaptacao") or "").strip()
    base_tempo = dados.get("tempo_preparo_base") or "Retirada do tempero sem mudar a duração prevista do preparo."
    rotulos = list(dict.fromkeys(nome_da_medida(nome) for nome in nomes_omitidos))
    adaptacao = " ".join(filter(None, [explicacao, "A versão usa o estoque e dispensa " + ", ".join(rotulos) + "."]))
    return ({"ingredientes": restantes, "porcoes": porcoes, "modo_preparo": preparo, "adaptacao": adaptacao,
             "tempo_preparo_min": tempo, "tempo_preparo_base": base_tempo}, rotulos)
