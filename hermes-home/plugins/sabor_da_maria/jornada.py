"""Próxima ação da consultora, derivada dos registros atuais e sem gravar decisões.

O chat e o hook do agente usam a mesma leitura. A jornada organiza o trabalho;
ela não aceita um prato, não escolhe preço e não cria compras em nome da pessoa.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import costing, cozinha, falas, negocio
from .store import Store
from .units import norm_text

PERGUNTA_DIVULGACAO = "Quer que eu prepare a divulgação do seu cardápio com o preço e a margem que você escolheu?"
PEDIDO_DIVULGACAO = "Prepare a divulgação do meu cardápio."
PERGUNTA_RENDIMENTO = "Quantas porções este lote rende?"


def pediu_continuidade(mensagem: Any) -> bool:
    if not isinstance(mensagem, str):
        return False
    texto = norm_text(mensagem).strip(" .!?")
    return bool(re.fullmatch(
        r"(?:(?:agora|entao)[, ]+)?(?:vamos continuar|podemos continuar|pode continuar|continue|continuar|"
        r"retome|retomar|vamos seguir|pode seguir|prossiga|e agora|qual (?:e )?o proximo passo|"
        r"o que falta(?: agora)?|me ajude a continuar|conduza (?:o processo|a proxima etapa))", texto))


def aceitou_proxima_pesquisa(texto: str, resposta_anterior: str) -> bool:
    """Um sim responde só à oferta canônica da ficha recém-registrada neste chat."""
    atual = norm_text(texto).strip(" .!?")
    anterior = norm_text(resposta_anterior)
    ids = set(re.findall(r"#\s*(\d+)\b", resposta_anterior))
    return bool(re.fullmatch(r"(?:sim(?:[, .!]+(?:pode (?:pesquisar|montar)|quero(?: sim)?))?|pode (?:pesquisar|montar)|vamos)", atual)
                and re.match(r"registrei o prato \d+\b", anterior)
                and len(ids) == 1 and resposta_anterior.count("?") == 1
                and "quer que eu pesquise a proxima opcao com o estoque disponivel" in anterior)


def ficha_para_retomar(store: Store) -> dict[str, Any] | None:
    ativos = fichas_em_uso(store.pratos_em_andamento(), controle(store))
    analises = {a["prato_id"] for a in store.analises_preco()}
    abertas = sorted((p for p in ativos if p["status"] != "aceito"), key=lambda p: p["id"] in analises)
    return abertas[0] if abertas else None


def alvo_da_retomada(mensagem: Any, pratos: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Reconhece os botões de continuidade sem ignorar alterações na mensagem.

    Nome, número e URL precisam ser os dados já salvos. Uma retomada com troca,
    restrição ou quantidade adicional segue o fluxo normal de edição da ficha.
    """
    if not isinstance(mensagem, str):
        return None
    texto = norm_text(mensagem).strip(" .!?")
    encontrados = []
    for prato in pratos:
        nome, numero = prato["nome"], prato["id"]
        titulo = f"Vamos continuar «{nome}». Confira o que já está salvo e me conduza pelo próximo passo, perguntando só o que falta."
        candidatos = [titulo, f"Sobre o prato escolhido #{numero} ({nome}): {titulo}",
                      f"Quero continuar o prato escolhido #{numero} ({nome}).",
                      f"Quero continuar o prato escolhido #{numero}.",
                      f"Continue o prato #{numero}.", f"Vamos continuar o prato #{numero}.",
                      f"Vamos continuar {nome}.", f"Vamos continuar o prato {nome}.",
                      f"Vamos continuar o prato #{numero}, {nome}. Resolva comigo as pendências dessa versão."]
        for visivel in (f"Retome a próxima pergunta da ficha de «{nome}» para eu responder.",
                        f"Quero revisar a versão de «{nome}» para decidir se vou prepará-la.",
                        f"Vamos montar a ficha de «{nome}» com a minha despensa e a minha cozinha."):
            candidatos.append(f"Quero continuar o prato escolhido #{numero} ({nome}). {visivel} "
                              "Esta retomada não é uma resposta sobre a cozinha nem um aceite do prato. "
                              "Use os dados salvos e pergunte somente o que falta.")
        url = prato.get("fonte_url") or ((prato.get("dados") or {}).get("selecao") or {}).get("fonte_url")
        if url:
            completar = (f"Complete a ficha do prato #{numero} ({nome}) a partir da receita original salva [{url}]. "
                         "Leia novamente a fonte e preencha/valide todos os ingredientes, medidas, rendimento e preparo sem omitir ou substituir itens. "
                         "Se a fonte não trouxer um dado, explique só esse ponto. "
                         "Não transforme esta retomada em aceite, compra, reserva ou confirmação da cozinha.")
            candidatos.extend([completar, f"Quero continuar o prato escolhido #{numero} ({nome}). {completar}"])
            candidatos.append(f"Quero continuar o prato escolhido #{numero} ({nome}). Complete a ficha a partir da receita original salva em {url}. "
                              "Leia novamente a fonte e preencha ou valide todos os ingredientes, medidas, rendimento e preparo, sem omitir ou substituir itens. "
                              "Se a fonte não trouxer um dado, explique só esse ponto. "
                              "Não transforme esta retomada em aceite, compra, reserva ou confirmação da cozinha.")
        if texto in {norm_text(c).strip(" .!?") for c in candidatos}:
            encontrados.append(prato)
    return encontrados[0] if len(encontrados) == 1 else None


def pendencias_ordenadas(pendencias: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dados disponíveis na fonte são corrigidos antes de perguntar à pessoa."""
    return sorted(pendencias, key=lambda b: b.get("tipo") not in ("fonte_incompleta", "fonte_divergente"))


_CONDICOES_DO_LOTE = {
    "gas_a_confirmar": (
        "Você consegue garantir gás suficiente para preparar este lote?",
        "Tenho gás suficiente para preparar este lote.",
        "Não consigo garantir gás suficiente para preparar este lote.",
        "gás suficiente para o preparo",
    ),
    "energia_a_confirmar": (
        "Você consegue garantir energia suficiente para preparar e conservar este lote?",
        "Tenho energia suficiente para preparar e conservar este lote.",
        "Não consigo garantir energia suficiente para preparar e conservar este lote.",
        "energia suficiente para preparar e conservar",
    ),
    "refrigeracao_a_confirmar": (
        "Há espaço suficiente na geladeira para os ingredientes e as porções deste lote?",
        "Há espaço suficiente na geladeira para os ingredientes e as porções deste lote.",
        "Não há espaço suficiente na geladeira para os ingredientes e as porções deste lote.",
        "espaço na geladeira para os ingredientes e as porções",
    ),
}


def _confirmacao_isolada(pendencia: dict[str, Any]) -> tuple[str, str, str] | None:
    tipo = pendencia["tipo"]
    if tipo in _CONDICOES_DO_LOTE:
        _, resposta, _, rotulo = _CONDICOES_DO_LOTE[tipo]
        return tipo, rotulo, resposta
    nome = pendencia["detalhe"].split(":", 1)[0].strip()
    if re.search(r"\b(?:ou|e)\b|[,/]", norm_text(nome)):
        return None
    if tipo == "equipamento_nao_informado" and nome:
        chave = cozinha.chave_equipamento(nome)
        item = cozinha.POR_ID.get(chave)
        rotulo = f"{nome} funcionando" if item and item.grupo == cozinha.EQUIPAMENTO else nome
        return chave, rotulo, f"Tenho {rotulo}."
    if tipo == "tecnica_nao_informada":
        chave = cozinha.chave_tecnica(nome)
        if chave in cozinha.POR_ID:
            frase = cozinha.POR_ID[chave].frase
            return chave, f"habilidade para {frase}", f"Sei {frase}."
        if nome:
            return chave, f"habilidade para {nome.lower()}", f"Eu domino a técnica de {nome}."
    return None


def _pergunta_da_cozinha(prato: dict[str, Any], pendencias: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Agrupa somente o trecho inicial de confirmações específicas e independentes."""
    confirmacoes = {}
    so_tecnicas = pendencias[0]["tipo"] == "tecnica_nao_informada"
    for pendencia in pendencias:
        confirmacao = _confirmacao_isolada(pendencia)
        if confirmacao is None:
            break
        chave, rotulo, resposta = confirmacao
        if so_tecnicas and pendencia["tipo"] == "tecnica_nao_informada":
            nome = pendencia["detalhe"].split(":", 1)[0].strip()
            rotulo, resposta = nome.lower(), f"Eu domino a técnica de {nome}."
        confirmacoes.setdefault(chave, (rotulo, resposta))
    if len(confirmacoes) < 2:
        return None
    rotulos, respostas = zip(*confirmacoes.values())
    lista = " e ".join((", ".join(rotulos[:-1]), rotulos[-1]))
    resposta = " ".join(respostas)
    texto = (f"Para este lote, você sabe {lista}? Se ainda estiver aprendendo alguma técnica, diga qual."
             if so_tecnicas else f"Para este lote, você conta com {lista}? Se faltar algo, diga o que está disponível e o que falta.")
    return {"texto": texto,
            "confirmacoes_agrupadas": True,
            "opcoes": [{"rotulo": "Domino todas as técnicas" if so_tecnicas else "Confirmar estas condições", "resposta": resposta,
                        "mensagem": resposta + f" Continue o prato #{prato['id']}."}]}


def _pergunta(prato: dict[str, Any], pendencias: list[dict[str, Any]], dados: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not pendencias or any(b.get("tipo") in ("fonte_incompleta", "fonte_divergente") for b in pendencias):
        return None
    if (dados or {}).get("montagem_com_estoque") and (agrupada := _pergunta_da_cozinha(prato, pendencias)):
        return agrupada
    primeira = pendencias[0]
    if primeira["tipo"] == "ingrediente_pendente":
        from .montagem import nome_da_medida, pergunta_da_medida

        nome, _, detalhe = primeira["detalhe"].partition(": ")
        if detalhe == "não está na despensa e ainda não tem preço de compra" or detalhe.endswith("e ainda não há preço de compra"):
            rotulo = nome_da_medida(nome)
            cotar = f"Pesquise o preço de {rotulo} para o prato #{prato['id']}."
            adaptar = f"Adapte o prato #{prato['id']}, {prato['nome']}, com o estoque disponível, sem comprar {rotulo}."
            return {"texto": f"Mantive {rotulo} na receita e anotei a falta na ficha. Quer que eu pesquise o preço para completar o prato?",
                    "ingrediente_a_cotar": nome,
                    "opcoes": [{"rotulo": "Pesquisar preço", "resposta": cotar, "mensagem": cotar},
                               {"rotulo": "Adaptar sem comprar", "resposta": adaptar, "mensagem": adaptar}]}
        return pergunta_da_medida(prato, primeira, dados or {})
    sufixo = f" Continue o prato #{prato['id']}."
    if primeira["tipo"] in _CONDICOES_DO_LOTE:
        texto, sim, nao, _ = _CONDICOES_DO_LOTE[primeira["tipo"]]
        return {"texto": texto, "opcoes": [
            {"rotulo": "Sim, para este lote", "resposta": sim, "mensagem": sim + sufixo},
            {"rotulo": "Ainda não", "resposta": nao, "mensagem": nao + sufixo}]}
    if primeira["tipo"] == "tempo_disponivel_pendente":
        tempos = (("< 30 min", "Tenho menos de 30 minutos por cozinhada."),
                  ("1 hora", "Tenho 1 hora por cozinhada."),
                  ("> 2 horas", "Tenho mais de 2 horas por cozinhada."),
                  ("> 6 horas", "Tenho mais de 6 horas por cozinhada."))
        return {"texto": "Quanto tempo você consegue dedicar a esta cozinhada?", "opcoes": [
            {"rotulo": rotulo, "resposta": resposta, "mensagem": resposta + sufixo} for rotulo, resposta in tempos]}
    if primeira["tipo"] == "tempo_disponivel_exato_pendente":
        return {"texto": primeira["detalhe"], "opcoes": []}
    if primeira["tipo"] == "tempo_da_receita_pendente":
        return {"texto": falas.PERGUNTA_TEMPO_DA_RECEITA, "opcoes": []}
    if primeira["tipo"] == "rendimento_pendente":
        return {"texto": PERGUNTA_RENDIMENTO, "opcoes": []}
    if primeira["tipo"] == "fogao_capacidade_pendente":
        return {"texto": "Quantas bocas do seu fogão estão funcionando para este preparo?", "opcoes": [
            {"rotulo": f"{bocas} boca" if bocas == 1 else f"{bocas} bocas",
             "resposta": f"Tenho {bocas} {'boca' if bocas == 1 else 'bocas'} funcionando no meu fogão.",
             "mensagem": f"Tenho {bocas} {'boca' if bocas == 1 else 'bocas'} funcionando no meu fogão." + sufixo} for bocas in (1, 2, 4, 6)]}
    nome = primeira["detalhe"].split(":", 1)[0].strip()
    if primeira["tipo"] == "equipamento_nao_informado" and (confirmacao := _confirmacao_isolada(primeira)):
        _, rotulo, resposta = confirmacao
        return {"texto": f"Você tem {rotulo} para este preparo?", "opcoes": [
            {"rotulo": "Tenho", "resposta": resposta, "mensagem": resposta + sufixo},
            {"rotulo": "Não tenho", "resposta": f"Não tenho {nome}.", "mensagem": f"Não tenho {nome}." + sufixo}]}
    if primeira["tipo"] == "tecnica_nao_informada" and nome:
        return {"texto": f"Você sabe {nome} neste preparo?", "opcoes": [
            {"rotulo": "Sei fazer", "resposta": f"Sei {nome}.", "mensagem": f"Sei {nome}." + sufixo},
            {"rotulo": "Estou aprendendo", "resposta": f"Estou aprendendo a {nome}.", "mensagem": f"Estou aprendendo a {nome}." + sufixo},
            {"rotulo": "Não sei", "resposta": f"Não sei {nome}.", "mensagem": f"Não sei {nome}." + sufixo}]}
    return None


def pergunta_pendente(prato: dict[str, Any], pendencias: list[dict[str, Any]],
                      dados: dict[str, Any]) -> dict[str, Any] | None:
    pergunta = _pergunta(prato, pendencias, dados)
    return pergunta if pergunta and pergunta["texto"] not in (dados.get("perguntas_esgotadas") or []) else None


def perguntou_pendencia(texto: str, pergunta: dict[str, Any], tipo: str) -> bool:
    """Reconhece a pergunta da ficha mesmo quando a consultora a reformula."""
    if pergunta["texto"] in texto:
        return True
    if pergunta.get("confirmacoes_agrupadas"):
        return False
    if tipo == "fogao_capacidade_pendente":
        pergunta_real = norm_text(falas.pergunta_unica(texto))
        return bool(re.search(r"\bquantas\b", pergunta_real) and re.search(r"\bbocas\b", pergunta_real)
                    and re.search(r"\bfogao\b", pergunta_real))
    sinais = {
        "gas_a_confirmar": r"\b(?:gas|botijao)\b",
        "energia_a_confirmar": r"\b(?:energia|luz)\b",
        "refrigeracao_a_confirmar": r"\b(?:geladeira|freezer)\b",
    }
    if tipo not in sinais:
        return False
    for frase in re.findall(r"[^.!?\n]+\?", texto):
        normal = norm_text(frase)
        citados = {chave for chave, termo in sinais.items() if re.search(termo, normal)}
        if citados == {tipo} and re.search(r"\b(?:suficient\w*|espaco|garant\w*|cabem?|lote|preparo|ingredientes)\b", normal):
            return True
    return False


def pratos_atuais(store: Store) -> list[dict[str, Any]]:
    """Reavalia fichas para leitura, inclusive aprovadas, sem mexer nas reservas."""
    from .disponibilidade_da_ficha import listar
    from .montagem import nome_da_medida
    from .tools import _avaliar_prato

    pratos = store.pratos_em_andamento()
    for prato in pratos:
        receita = store.obter_receita(prato["receita_id"]) if prato.get("receita_id") else None
        if not receita:
            continue
        avaliador = (Store(store.db_path, store.xlsx_path, ignorar_prato_id=prato["id"])
                     if prato["status"] in ("cmv_calculado", "aceito") else store)
        aval = _avaliar_prato(avaliador, receita["dados"].get("ingredientes") or [], receita)
        notas = Store.anotacoes_faltantes(aval["resultado"], receita["dados"].get("ingredientes") or [],
                                         receita["dados"].get("ingredientes_faltantes") or [], receita["dados"].get("adaptacao"))
        prato.update({"veredito": aval["veredito"], "bloqueios": aval["bloqueios"], "ingredientes_faltantes": notas})
        prato["ingredientes_da_ficha"] = listar(avaliador, receita["dados"], aval["resultado"], notas)
        if medidas := receita["dados"].get("medidas_de_uso_estimadas"):
            prato["medidas_de_uso_estimadas"] = [{"ingrediente": nome_da_medida(nome), **medida}
                                                for nome, medida in medidas.items()]
    return pratos


def _resumo_das_fichas(store: Store, *, pratos: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Resumo público somente de leitura; pendências são reavaliadas, nunca presumidas."""
    from .tools import _avaliar_prato

    pratos = pratos if pratos is not None else pratos_atuais(store)
    revisoes = [p for p in pratos if p["status"] == "aceito" and p.get("bloqueios")]
    if revisoes:
        prato = revisoes[0]
        return {"etapa": "revisao", "titulo": "Vamos revisar um prato do cardápio",
                "descricao": prato["bloqueios"][0]["detalhe"], "acao": "Revisar com a consultora",
                "prato_id": prato["id"], "prato_nome": prato["nome"], "pendencias": prato["bloqueios"],
                "pergunta": _pergunta(prato, prato["bloqueios"]),
                "mensagem": f"Revise comigo o prato #{prato['id']}, {prato['nome']}. Confira as condições atuais e conduza a resolução das pendências.",
                "instrucao_agente": f"O prato #{prato['id']}, {prato['nome']}, foi aprovado antes, mas tem impedimentos atuais. Explique a mudança e conduza a revisão antes de produzir ou comprar. Não apague o registro nem libere as reservas sem pedido da pessoa. Não trate o preço histórico como nova escolha."}
    abertos = [p for p in pratos if p["status"] != "aceito"]
    if abertos:
        prato = abertos[0]
        receita = store.obter_receita(prato["receita_id"]) if prato.get("receita_id") else None
        if receita is None:
            # Escolha guardada sem ficha: a lista de bloqueios vem vazia do banco, e isso não quer dizer "viável".
            pendencias = [{"tipo": "receita_pendente", "detalhe": "A ficha precisa de receita real, medidas, rendimento e preparo."}]
        elif "bloqueios" in prato:
            pendencias = prato["bloqueios"]
        else:
            pendencias = _avaliar_prato(store, receita["dados"].get("ingredientes") or [], receita)["bloqueios"]
        pendencias = pendencias_ordenadas(pendencias)
        dados = receita["dados"] if receita else {}
        pergunta = pergunta_pendente(prato, pendencias, dados)
        esgotada = pergunta is None and _pergunta(prato, pendencias, dados) is not None
        base = {"prato_id": prato["id"], "prato_nome": prato["nome"], "pendencias": pendencias,
                "pergunta": pergunta, "pergunta_esgotada": esgotada}
        referencia = f"prato #{prato['id']}, {prato['nome']}"
        if pendencias:
            fonte_pendente = pendencias[0]["tipo"] in ("fonte_incompleta", "fonte_divergente")
            descricao = ("Confira as condições da cozinha para preparar este lote."
                         if pergunta and pergunta.get("confirmacoes_agrupadas") else pendencias[0]["detalhe"])
            instrucao = f"Retome a ficha {referencia}. "
            if dados.get("montagem_com_estoque"):
                instrucao += ("A versão com o estoque já foi delegada. Confira as medidas estimáveis e as adaptações em "
                              "conjunto. Peça só as condições reais da cozinha que ainda faltam, agrupando-as quando "
                              "possível. Depois da conferência, mostre a versão para aceite.")
            elif fonte_pendente:
                instrucao += ("Corrija primeiro as divergências usando a receita original já salva. Não peça à pessoa "
                              "ingredientes, medidas ou preparo disponíveis na fonte. Não pergunte sobre a cozinha nem "
                              "peça aceite enquanto a ficha divergir da fonte.")
            else:
                instrucao += "Explique o primeiro impedimento e proponha uma saída. "
                if dados.get("ingredientes_faltantes"):
                    instrucao += ("Mantenha na ficha os ingredientes sem estoque e anote o que falta. Para manter a receita, "
                                  "cote os complementos e confira o orçamento. Adapte os ingredientes somente se ela pedir. ")
                instrucao += ("Duas tentativas já foram usadas nessa pendência. Use evidências, estime se houver base ou proponha uma versão que dispense essa condição."
                              if esgotada else "Pergunte só o dado relevante que falta e salve a resposta. Não reabra o cadastro completo.")
            return {**base, "etapa": "pendencias", "titulo": "Vamos resolver o que falta",
                    "descricao": descricao, "acao": "Resolver com a consultora",
                    "mensagem": f"Vamos continuar o {referencia}. Resolva comigo as pendências dessa versão.",
                    "instrucao_agente": instrucao}
        analise = next((a for a in store.analises_preco() if a["prato_id"] == prato["id"]), None)
        if analise:
            return {**base, "etapa": "preco", "titulo": "Vamos escolher o preço",
                    "descricao": "A versão foi aceita e os cenários estão prontos. Você escolhe quanto cobrar.",
                    "acao": "Comparar preços no chat", "mensagem": f"Vamos continuar o {referencia}. Mostre os cenários atuais e me ajude a escolher o preço.",
                    "instrucao_agente": f"A versão do {referencia} já foi aceita. Use os cenários salvos, explique CMV e taxa fixa de 10%, e peça só a escolha do preço. Não peça novo aceite e não escolha por ela."}
        return {**base, "etapa": "aceite", "titulo": "Vamos conferir sua escolha",
                "descricao": "A ficha está viável. Falta você confirmar se gosta do prato e consegue preparar essa versão.",
                "acao": "Revisar a versão no chat", "mensagem": f"Vamos continuar o {referencia}. Mostre a versão conferida e o que falta para eu decidir.",
                "instrucao_agente": f"Apresente a versão viável do {referencia}, a fonte e eventuais adaptações. Pergunte se gosta e se vê impedimento. Uma retomada não é aceite; só calcule cenários depois da confirmação."}
    pend_negocio = negocio.pendencias(store.negocio())
    if pend_negocio and not pratos:
        return {"etapa": "negocio", "titulo": "Vamos conhecer seu negócio",
                "descricao": "Vou usar seu estilo de comida e sua região para orientar as sugestões.",
                "acao": "Começar com a consultora", "mensagem": "Me ajude a começar meu cardápio. Conduza o cadastro do negócio pelo chat.",
                "pendencias": pend_negocio,
                "instrucao_agente": "Conduza o cadastro do negócio no chat. Salve o que ela já contou e solicite apenas o que falta. O formulário clicável é uma ajuda, não peça para navegar ao menu."}
    if pratos:
        return {"etapa": "cardapio", "titulo": "Seu cardápio está tomando forma",
                "descricao": "Posso revisar os pratos salvos ou pesquisar a próxima opção com o estoque disponível.",
                "acao": "Planejar a próxima opção", "mensagem": "Pesquise e monte mais um prato para meu cardápio com o estoque disponível, respeitando as reservas dos pratos já aprovados.",
                "pendencias": [],
                "instrucao_agente": "Os pratos atuais estão registrados. Confirme o resultado, mostre o próximo trabalho possível e acompanhe revisões de ingredientes, custos e restrições. Não registre outro prato sem escolhas próprias."}
    return {"etapa": "pesquisa", "titulo": "Vamos encontrar seu primeiro prato",
            "descricao": "Vou pesquisar uma receita real com sua despensa e conferir a cozinha conforme o preparo escolhido.",
            "acao": "Montar um prato com a consultora", "mensagem": "Monte um prato para meu cardápio com a minha despensa. Pesquise uma receita real e confira comigo só o que faltar para preparar.",
            "pendencias": [],
            "instrucao_agente": "O negócio está conhecido e ainda não há ficha aberta. Conduza a pesquisa de uma receita real com o estoque disponível. Não devolva 'qual prato você quer?' nem exija completar todos os equipamentos e técnicas antes de pesquisar. Apresente uma versão e confira somente os requisitos dela antes do aceite."}


PASSOS = ("negocio", "estoque", "ajuda", "montagem", "divulgacao")
CHAVE_CONTROLE = "jornada_guiada_v1"


def controle(store: Store, *, pratos: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Leitura sem migração destrutiva: fichas antigas continuam no ponto em que estavam.

    Consultar estoque não significa que a pessoa esteja pronta. Só inferimos as
    etapas anteriores para fichas que já existiam antes deste controle guiado.
    """
    with store.connect() as conn:
        bruto = store._config_get(conn, CHAVE_CONTROLE)
    salvo = json.loads(bruto) if bruto else {}
    legado = not bruto and bool(pratos if pratos is not None else store.pratos_em_andamento())
    return {"estoque_confirmado": bool(salvo.get("estoque_confirmado", legado)),
            "pausado": bool(salvo.get("pausado", False)),
            "ajuda": salvo.get("ajuda", "ia" if legado else "") if salvo.get("ajuda", "ia" if legado else "") in ("", "ia", "proprio") else "",
            "divulgacao": salvo.get("divulgacao", "") if salvo.get("divulgacao", "") in ("", "interesse", "dispensada") else "",
            "proprio_pendente": bool(salvo.get("proprio_pendente", False)),
            "ultimo_prato_ao_escolher": int(salvo.get("ultimo_prato_ao_escolher", 0)),
            "selecao_atual": [i for i in salvo.get("selecao_atual", []) if type(i) is int and i > 0],
            "rascunhos_adiados": [i for i in salvo.get("rascunhos_adiados", []) if type(i) is int and i > 0]}


def inventario(store: Store) -> list[dict[str, Any]]:
    """Custos da planilha, na unidade de compra; estoque físico e livre separados."""
    return [{"nome": i["nome"], "quantidade": i["estoque_base"], "unidade": i["unidade_base"],
             "custo_unitario": i["custo_unitario_base"],
             "custo_total": round(i["estoque_base"] * i["custo_unitario_base"], 2) if i["custo_unitario_base"] is not None else None,
             "disponivel": i["disponivel_base"], "reservado": i["reservado_base"]}
            for i in store.ingredientes()]


def _opcao(rotulo: str, acao: str, mensagem: str) -> dict[str, str]:
    return {"rotulo": rotulo, "acao": acao, "resposta": mensagem, "mensagem": mensagem}


def fichas_em_uso(pratos: list[dict[str, Any]], estado: dict[str, Any]) -> list[dict[str, Any]]:
    # Escolher o próprio prato adia as propostas anteriores, sem removê-las ou
    # liberar reservas de pratos aprovados. Elas continuam na tela Minha cozinha.
    return [p for p in pratos if p["status"] == "aceito" or (p["id"] not in estado.get("rascunhos_adiados", []) and
            (estado["ajuda"] != "proprio" or p["id"] > estado["ultimo_prato_ao_escolher"] or p["id"] in estado.get("selecao_atual", [])))]


def continuar_escolhas(store: Store, ids: list[int]) -> None:
    """Nomes escolhidos satisfazem a pergunta do modo próprio, inclusive IDs antigos.

    Isto só muda o foco da jornada. Selecionar receitas não aceita a versão,
    não escolhe preço e não movimenta compras ou reservas.
    """
    dados = controle(store)
    if dados["ajuda"] != "proprio":
        return
    from .store import _LOCK
    with _LOCK, store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        dados = controle(store)
        if dados["ajuda"] != "proprio":
            return
        dados["proprio_pendente"] = False
        dados["selecao_atual"] = list(dict.fromkeys(ids))
        dados["rascunhos_adiados"] = [i for i in dados["rascunhos_adiados"] if i not in ids]
        store._config_set(conn, CHAVE_CONTROLE, json.dumps(dados, ensure_ascii=False))


def adiar_proposta(store: Store, anterior: int, nova: int) -> None:
    """Uma alternativa passa a ser a proposta em foco; não é aceite nem exclusão."""
    if anterior == nova:
        return
    original, proposta = store.obter_prato(anterior), store.obter_prato(nova)
    if not original or original["status"] == "aceito" or not proposta:
        return
    from .store import _LOCK
    with _LOCK, store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        dados = controle(store)
        dados["rascunhos_adiados"] = list(dict.fromkeys([*dados["rascunhos_adiados"], anterior]))
        store._config_set(conn, CHAVE_CONTROLE, json.dumps(dados, ensure_ascii=False))


def definir_rascunho_adiado(store: Store, prato_id: int, *, adiar: bool) -> bool:
    """Muda o foco da jornada mantendo a ficha e seu histórico."""
    from .store import _LOCK

    with _LOCK, store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        prato = conn.execute("SELECT status FROM pratos WHERE id = ?", (prato_id,)).fetchone()
        if not prato or prato["status"] != "selecionado":
            return False
        dados = controle(store)
        adiados = dados["rascunhos_adiados"]
        if adiar:
            if prato_id not in adiados:
                adiados.append(prato_id)
            else:
                return True
        elif prato_id in adiados:
            adiados.remove(prato_id)
            if dados["ajuda"] == "proprio":
                dados["proprio_pendente"] = False
                if prato_id not in dados["selecao_atual"]:
                    dados["selecao_atual"].append(prato_id)
        else:
            return False
        store._config_set(conn, CHAVE_CONTROLE, json.dumps(dados, ensure_ascii=False))
        return True


def resumo(store: Store, *, pratos: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    pratos = pratos if pratos is not None else pratos_atuais(store)
    estado = controle(store, pratos=pratos)
    if estado["proprio_pendente"] and any(p["id"] > estado["ultimo_prato_ao_escolher"] for p in pratos):
        estado = {**estado, "proprio_pendente": False}
    ativos = fichas_em_uso(pratos, estado)
    pend_negocio = negocio.pendencias(store.negocio())
    analises = {a["prato_id"] for a in store.analises_preco()}
    montados = bool(ativos) and all((p["status"] == "aceito" or p["id"] in analises) and not p.get("bloqueios") for p in ativos)
    publicados = bool(ativos) and all(p["status"] == "aceito" and not p.get("bloqueios") for p in ativos)
    plano = None
    if publicados and estado["divulgacao"] == "interesse":
        from .divulgacao import plano_atual

        plano = plano_atual(store, ativos)
    concluidas = {"negocio": not pend_negocio, "estoque": estado["estoque_confirmado"], "ajuda": bool(estado["ajuda"]),
                  "montagem": montados, "divulgacao": publicados and (bool(plano) or estado["divulgacao"] == "dispensada")}
    comum = {"controle": estado, "concluidas": concluidas, "pendencias": [], "pratos_ativos": [p["id"] for p in ativos]}
    if pend_negocio and not pratos:
        primeiro = pend_negocio[0]
        return {**comum, "passo": "negocio", "etapa": "negocio", "titulo": "Vamos conhecer seu negócio",
                "descricao": "Vamos combinar seu tipo de negócio, sua comida e onde você vende. Os R$ 80,00 são para complementos da despensa.",
                "acao": "Contar do meu negócio", "mensagem": "Vamos conhecer meu negócio, um passo por vez.",
                "pendencias": pend_negocio, "pergunta": {"texto": primeiro["pergunta"], "opcoes": []},
                "instrucao_agente": "Conheça o negócio antes de pesquisar receitas. Salve os dados espontâneos e faça só a primeira pergunta que falta. Pergunte pelo tipo de negócio, depois estilo de comida e localização; objetivo é opcional. Não apresente cadastro da cozinha nem pesquisa nesta etapa."}
    if not estado["estoque_confirmado"]:
        pausado = estado["pausado"]
        return {**comum, "passo": "estoque", "etapa": "pausa" if pausado else "estoque",
                "titulo": "Seguimos quando você quiser" if pausado else "Conheça o que já tem",
                "descricao": "Seu progresso está salvo. Nenhuma pesquisa, compra ou prato será iniciado enquanto você não confirmar." if pausado else
                    "Estes ingredientes já foram comprados. Usar a despensa entra nas despesas do lote. O caixa disponível para compras cobre os complementos.",
                "acao": "Estou pronta para começar", "mensagem": "Sim, estou pronta para começar a montar meus pratos.",
                "estoque": inventario(store),
                "pergunta": {"texto": "Está pronta para começar a montar seus pratos?", "opcoes": [
                    _opcao("Sim, vamos começar", "confirmar_estoque", "Sim, estou pronta para começar a montar meus pratos."),
                    *([] if pausado else [_opcao("Agora não", "pausar", "Agora não. Quero olhar meu estoque com calma.")])]},
                "instrucao_agente": "Apresente a despensa como estoque já pago, com quantidade e custo por unidade. Mostre um resumo curto e pergunte somente se ela está pronta. Se disser não, aguarde sem pesquisar, montar ou gastar. Um sim aqui confirma prontidão, não aceita prato nem preço."}
    if not estado["ajuda"]:
        local = negocio.descrever_local(store.negocio())
        return {**comum, "passo": "ajuda", "etapa": "ajuda", "titulo": "Como você quer montar seu prato?",
                "descricao": f"Posso buscar receitas reais que combinem com seu estoque e com {local or 'a região da sua loja'}, ou conferir um prato que você já tem em mente.",
                "acao": "Escolher como começar", "mensagem": "Quero escolher como montar meu prato.",
                "pergunta": {"texto": "Quer ajuda para pesquisar um prato ou prefere montar o seu?", "opcoes": [
                    _opcao("Quero ajuda da IA", "ajuda_ia", "Quero sua ajuda. Pesquise receitas reais para o meu negócio com o estoque disponível. Apresente poucas opções com fonte e custos, para eu escolher antes de montar a ficha."),
                    _opcao("Quero montar meu prato", "prato_proprio", "Quero montar meu próprio prato. Pergunte o que tenho em mente.")]},
                "instrucao_agente": "Ofereça as duas escolhas e aguarde. Não pesquise antes da escolha. Com ajuda da IA, considere estilo, ingredientes e região sem inventar ranking. Com prato próprio, pergunte o que ela tem em mente e confira a versão dela; não imponha sugestões da IA."}
    if estado["proprio_pendente"] or (estado["ajuda"] == "proprio" and not pratos):
        return {**comum, "passo": "montagem", "etapa": "proprio", "titulo": "Vamos montar o seu prato",
                "descricao": "Conte o prato que tem em mente ou o ingrediente que quer usar. Vou conferir a composição, sua cozinha e o que cabe no saldo.",
                "acao": "Contar meu prato", "mensagem": "Quero contar o prato que tenho em mente.",
                "instrucao_agente": "Ela escolheu criar o próprio prato. Se ainda não descreveu prato ou ingrediente, faça só essa pergunta. Se a mensagem atual já descreve o que quer, monte a nova ficha a partir desse pedido; não repita a pergunta inicial. Preserve as fichas anteriores. A pesquisa real pode apoiar a versão e suas técnicas, sem substituir a escolha dela. Não aceite nem debite automaticamente."}
    # Conclua a montagem das versões escolhidas antes de abrir os preços. A
    # ordem das fichas é trabalho operacional, não aceite pela pessoa.
    ordem = sorted(ativos, key=lambda p: (p["status"] == "aceito", p["id"] in analises))
    fichas = _resumo_das_fichas(store, pratos=ordem)
    passo = "divulgacao" if fichas["etapa"] in ("preco", "cardapio") else "montagem"
    if publicados:
        fichas = {**fichas, "etapa": "divulgacao", "titulo": "Seu cardápio está pronto para vender",
                  "descricao": "Os pratos têm preço escolhido. A consultora pode preparar as ações e as mensagens de divulgação.",
                  "acao": "Preparar divulgação", "mensagem": PEDIDO_DIVULGACAO,
                  "pergunta": {"texto": PERGUNTA_DIVULGACAO, "opcoes": [
                      _opcao("Preparar divulgação", "interesse_divulgacao", PEDIDO_DIVULGACAO),
                      _opcao("Agora não", "dispensar_divulgacao", "Não quero planejar divulgação agora.")]},
                  "instrucao_agente": "Os pratos estão registrados e a escolha de preço terminou. Resuma o resultado e pergunte se ela quer conhecer divulgação. É opcional. Só mostre modalidades oficiais verificadas; disponibilidade e investimento são conferidos no Portal do Parceiro. Nunca contrate campanha, prometa ranqueamento nem retire verba dos R$ 80 de complementos."}
        if estado["divulgacao"]:
            fichas["pergunta"] = None
            if estado["divulgacao"] == "interesse":
                fichas["descricao"] = "A consultora usa o cardápio, a margem e as vendas registradas para montar a divulgação."
            fichas["instrucao_agente"] = ("Ela preferiu deixar a divulgação para depois. Respeite e não ofereça novamente em cada resposta. O cardápio e os preços continuam salvos."
                                          if estado["divulgacao"] == "dispensada" else
                                          "Ela autorizou preparar o plano de divulgação. Use divulgacao_planejar para concluir ou mostrar o plano já salvo. Se ela indicar outros valores, use os dados informados. "
                                          "O plano local reúne ações, materiais e margem. A adesão a campanhas fica no Portal do Parceiro.")
        if plano:
            fichas.update(titulo="Divulgação preparada", acao="Ver plano de divulgação", plano_divulgacao=plano,
                          descricao="Os materiais, a estimativa de investimento e os próximos passos estão salvos.",
                          instrucao_agente="A etapa 5 já foi concluída e o plano está salvo. Responda sobre o plano quando ela pedir. "
                                          "Use divulgacao_planejar para consultar ou simular com novos dados informados por ela.")
    elif fichas["etapa"] == "pesquisa":
        fichas = {**fichas, "acao": "Pesquisar opções de pratos",
                  "mensagem": "Quero sua ajuda. Pesquise receitas reais para o meu negócio com o estoque disponível. Apresente poucas opções com fonte e custos, para eu escolher antes de montar a ficha.",
                  "instrucao_agente": "Ela escolheu ajuda da IA para conhecer opções. Pesquise duas ou três receitas reais coerentes com o negócio, a região e a despensa. Apresente fonte, ingredientes disponíveis e faltantes e custos unitários confirmados do estoque. Pergunte somente quais opções quer escolher e aguarde. Não salve rascunho, não monte a ficha nem peça condições da cozinha antes da escolha. Os requisitos relevantes da cozinha serão conferidos depois da escolha, antes do aceite. Se a fala atual pedir explicitamente montar um prato, essa delegação direta permite montar uma proposta, ainda sem aceite nem escolha de preço."}
    return {**comum, **fichas, "passo": passo}


def responder(store: Store, acao: str) -> dict[str, Any]:
    """Escolha explícita, persistida e validada. Navegar ou consultar não chama isto."""
    if not isinstance(acao, str) or acao not in {"confirmar_estoque", "pausar", "retomar", "ajuda_ia", "prato_proprio", "interesse_divulgacao", "dispensar_divulgacao"}:
        raise ValueError("escolha uma das opções desta etapa")
    # A escrita é serializada com a leitura que valida as condições do controle.
    from .store import _LOCK
    store.ensure_loaded()
    with _LOCK, store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        atual = resumo(store)
        dados = atual["controle"]
        if negocio.pendencias(store.negocio()):
            raise ValueError("vamos primeiro conhecer o tipo de negócio, a comida e a localização")
        if acao in ("confirmar_estoque", "pausar", "retomar"):
            if dados["estoque_confirmado"]:
                if acao != "confirmar_estoque":
                    raise ValueError("essa decisão pertence à apresentação do estoque")
            else:
                dados["pausado"] = acao == "pausar"
                if acao == "confirmar_estoque":
                    dados["estoque_confirmado"] = True
        elif acao in ("ajuda_ia", "prato_proprio"):
            if not dados["estoque_confirmado"] or dados["pausado"]:
                raise ValueError("confirme primeiro que está pronta para começar com o estoque apresentado")
            dados["ajuda"] = "ia" if acao == "ajuda_ia" else "proprio"
            dados["proprio_pendente"] = acao == "prato_proprio"
            dados["selecao_atual"] = []
            dados["ultimo_prato_ao_escolher"] = max((p["id"] for p in store.pratos_em_andamento()), default=0)
        else:
            pratos = fichas_em_uso(pratos_atuais(store), dados)
            if not pratos or any(p["status"] != "aceito" or p.get("bloqueios") for p in pratos):
                raise ValueError("vamos concluir os pratos viáveis e escolher os preços antes da divulgação")
            dados["divulgacao"] = "interesse" if acao == "interesse_divulgacao" else "dispensada"
        store._config_set(conn, CHAVE_CONTROLE, json.dumps(dados, ensure_ascii=False))
    return resumo(store)


def negou_pesquisa(texto: Any) -> bool:
    if not isinstance(texto, str):
        return False
    normal = norm_text(texto)
    return bool(re.search(r"\b(?:ainda nao|nao decidi|nao (?:\w+\s+){0,2}(?:pesquis\w*|busqu\w*|procur\w*)|sem pesquisar)\b", normal))


def delegou_ajuda(texto: Any) -> bool:
    if not isinstance(texto, str) or re.search(r"[?\n`\"“”]|^\s*>", texto) or negou_pesquisa(texto):
        return False
    normal = norm_text(texto)
    if re.search(r"\b(?:talvez|mas|ainda nao|nao decidi|se eu|caso eu)\b", normal):
        return False
    return bool(re.match(r"(?:quero (?:sua ajuda|ajuda da ia)|prefiro (?:sua ajuda|ajuda da ia))\b", normal))


def acao_da_fala(estado: dict[str, Any], texto: str, anterior: str = "") -> str | None:
    """Respostas literais da pessoa; um sim só responde à pergunta desta etapa.

    Intenções do contexto recuperado e referências a perguntas antigas não
    avançam o controle. As ações clicadas já passam pela mesma validação na API.
    """
    atual = re.sub(r"\s+", " ", re.sub(r"[,;.!]", " ", norm_text(texto))).strip()
    antes = norm_text(anterior)
    if not atual or re.search(r"[?\n`\"“”]|^\s*>", texto):
        return None
    simples_sim = bool(re.fullmatch(r"(?:sim|sim vamos comecar|sim estou pronta(?: para comecar(?: a montar meus pratos)?)?|estou pronta(?: para comecar(?: a montar meus pratos)?)?|pode comecar|vamos comecar)", atual))
    simples_nao = bool(re.fullmatch(r"(?:nao|agora nao|ainda nao|nao estou pronta|quero esperar|agora nao quero olhar meu estoque com calma)", atual))
    if estado["etapa"] in ("estoque", "pausa"):
        prontidao = "pronta para comecar" in atual or "pronto para comecar" in atual or "vamos comecar" in atual
        pergunta = "pronta para comecar a montar" in antes or "pronto para comecar a montar" in antes
        if simples_sim and (prontidao or pergunta):
            return "confirmar_estoque"
        if simples_nao and (pergunta or estado["etapa"] == "pausa"):
            return "pausar"
        if re.fullmatch(r"(?:retomar|vamos continuar|podemos continuar|pode continuar)", atual) and estado["controle"]["pausado"]:
            return "retomar"
    if estado["controle"]["estoque_confirmado"] and not estado["controle"]["pausado"]:
        if re.fullmatch(r"(?:eu )?(?:quero|prefiro) (?:montar |fazer |usar )?(?:o )?meu (?:proprio )?prato(?: pergunte o que tenho em mente)?", atual.replace(". ", " ")):
            return "prato_proprio"
        if estado["etapa"] in ("ajuda", "proprio") and (
                delegou_ajuda(texto)
                or not negou_pesquisa(texto) and re.fullmatch(r"(?:pesquise|procure|busque|monte|monta)(?: um prato| uma receita)(?: para mim| para meu cardapio)?", atual)):
            return "ajuda_ia"
    if estado["etapa"] == "divulgacao":
        pergunta_recente = re.split(r"[.!?]", anterior.rsplit("?", 1)[0])[-1] if "?" in anterior else ""
        convite_atual = norm_text(pergunta_recente)
        convite = any(p in convite_atual for p in ("quer conhecer as opcoes de divulgacao do ifood",
                                                   "quer que eu analise a divulgacao do seu cardapio"))
        convite = convite or bool(re.search(
            r"\b(?:quer|vamos|posso|podemos|gostaria de) (?:que eu )?(?:prepar|mont|planej|organiz|analis|cuid)\w+ "
            r"(?:o plano de |a |o |da )?(?:divulgacao|marketing)\b", convite_atual))
        convite_antigo = (estado["controle"]["divulgacao"] == "interesse"
                          and "quer simular com um valor de investimento e porcoes previstas" in convite_atual)
        confirmou = simples_sim or bool(re.fullmatch(
            r"(?:sim )?(?:pode(?: (?:analisar|simular|fazer|seguir|preparar|divulgar))?|aceito|aprovo|quero(?: sim| conhecer| analisar)?|vamos (?:simular|analisar|divulgar))", atual))
        pediu_plano = bool(re.match(
            r"^(?:(?:ok|beleza|sim|por favor|pode|vamos) )*(?:quero (?:que (?:voce )?)?)?"
            r"(?:prepare|preparar|analise|analisar|monte|montar|faca|fazer|aprovo|autorizo) "
            r"(?:o plano de |a |o )?(?:divulgacao|marketing)\b|"
            r"^(?:(?:agora|ok|beleza|sim) )*(?:(?:pode|vamos) divulgar|divulgue)\b", atual))
        if pediu_plano or atual.startswith("quero conhecer as opcoes de divulgacao") or confirmou and (convite or convite_antigo):
            return "interesse_divulgacao"
        if atual in ("nao quero planejar divulgacao agora", "nao quero divulgacao", "agora nao quero publicidade",
                     "vou deixar a publicidade para depois") or simples_nao and (convite or convite_antigo):
            return "dispensar_divulgacao"
    return None


def resposta_curta(store: Store, estado: dict[str, Any] | None = None) -> str:
    atual = estado or resumo(store)
    if atual["etapa"] == "negocio":
        return "Vamos por partes. Você tem R$ 80,00 para complementar o que já está na despensa. " + atual["pergunta"]["texto"]
    if atual["etapa"] in ("estoque", "pausa"):
        if atual["etapa"] == "pausa":
            return "Tudo bem, seu progresso está salvo. Vou aguardar você confirmar que está pronta. Não iniciei pesquisa, compras ou montagem."
        itens = atual["estoque"]
        # A lista completa e seus custos ficam no bloco interativo desta etapa.
        amostra = "; ".join(f"{i['nome']}: {i['quantidade']:g} {i['unidade']} ({costing.brl(i['custo_unitario'])}/{i['unidade']})"
                            for i in itens[:4] if i["custo_unitario"] is not None)
        saldo = store.carrinho_resumo()["saldo"]
        return (f"Seu negócio está salvo. A despensa tem {len(itens)} ingredientes já comprados. " + (amostra + ". " if amostra else "") +
                f"A lista completa está logo abaixo. Você tem {costing.brl(saldo)} disponíveis para compras novas. Ao aceitar um prato, o custo do lote entra nas despesas. " + atual["pergunta"]["texto"])
    if atual["etapa"] == "ajuda":
        return atual["descricao"] + " " + atual["pergunta"]["texto"]
    if atual["etapa"] == "proprio":
        return "Vamos montar do seu jeito. Qual prato você tem em mente, ou qual ingrediente quer usar?"
    if atual["etapa"] == "divulgacao":
        escolha = atual["controle"]["divulgacao"]
        if escolha == "dispensada":
            return "Combinado, divulgação fica para depois. Seu cardápio e os preços continuam salvos; você pode acompanhar as vendas realizadas no Analytics."
        if escolha == "interesse":
            return atual["descricao"]
        return atual["descricao"] + " " + atual["pergunta"]["texto"]
    return atual["descricao"]


def contexto(store: Store, *, conduzir: bool = True, estado: dict[str, Any] | None = None) -> str:
    estado = estado or resumo(store)
    if not conduzir:
        return ("CONDUÇÃO DA JORNADA (estado atual; não é fala nem confirmação da pessoa):\n"
                f"Próxima etapa disponível: {estado['etapa']}. {estado['titulo']}. "
                "Neste turno responda ao pedido específico dela. Não inicie pesquisa, reabra cadastro ou mude de ficha só por haver uma etapa disponível. "
                "Depois acompanhe a continuidade quando ela pedir ou responder à etapa atual.")
    return ("CONDUÇÃO DA JORNADA (estado atual; não é fala nem confirmação da pessoa):\n"
            f"Etapa: {estado['etapa']}. {estado['instrucao_agente']}\n"
            + (f"Pergunta atual: {estado['pergunta']['texto']}\n" if estado.get("pergunta") else "") +
            "Você acompanha e executa a parte operacional pelo chat: pesquisa, confere, salva respostas, explica custos e conduz o próximo passo. "
            "Os painéis são apoio opcional. Resolva o que as ferramentas permitem antes de perguntar; não invente fatos, aceite, preço ou compras. "
            "Faça UMA pergunta ou decisão por vez. Depois de respondida, mostre somente a próxima etapa. "
            "Carrinho, cozinha e ingredientes são partes internas da montagem; não apresente o roteiro inteiro nem um cadastro completo antecipado. "
            "Se ela fizer outra pergunta, responda primeiro e mantenha a continuidade sem forçar uma etapa.")
