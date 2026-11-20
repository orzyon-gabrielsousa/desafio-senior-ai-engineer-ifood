"""Painel para a consultora: carrinho, saldo, itens salvos, negócio, cozinha e guias de pesquisa e preço.

Pedir que o modelo lembre de chamar ``carrinho_consultar`` antes de sugerir não é confiável (um 8B pula
o passo), e o que a Dona Maria mexe na tela não passa por conversa nenhuma. Então o plugin injeta este
resumo curto na mensagem do turno pelo hook ``pre_llm_call`` (o Hermes junta o contexto dos plugins à
mensagem do usuário, nunca ao prompt de sistema). A consultora sempre sabe o saldo, o que já foi
escolhido (os pratos abertos, com ingredientes e valores), o que ela salvou e para que tipo de negócio e
em que lugar sugerir. Em turnos de pesquisa, inclui as fontes e evidências da região.

A **cozinha dela** (equipamentos, utensílios, técnicas e rotina, salvos no SQLite) também vai em todo turno, porque é
ela que decide se um prato pode ser aceito. Enquanto o perfil está incompleto o painel manda perguntar, com o texto das
perguntas já pronto (a lição do carrinho: o 8B só repete com fidelidade o que chega pronto num campo), e na primeira
mensagem da conversa manda perguntar já na primeira resposta. Hermes avisa o hook com ``is_first_turn``.

Quando a mensagem dela pede receita ou ideia de prato, o painel também traz o roteiro da pesquisa em ordem (despensa → web_search →
web_extract → despensa_buscar → candidatas com a pergunta de feedback), de ``pesquisa.py``.

O bloco começa com ``MARCA_PAINEL`` para a trava de evidência (falas.py) ignorá-lo: são dados da tela,
não palavras dela.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from . import adaptacao_estoque, compras, costing, cozinha, escolhas, falas as falas_mod, guias, jornada, montagem, negocio, pesquisa
from .falas import MARCA_PAINEL, extrair
from .store import Store
from .tools import (_avaliar_prato, _motivo_pendente, _pendencias_perfil, _texto_preparos_caseiros, _texto_saldo,
                    atualizar_declaracoes_do_turno, avancar_cardapio_no_turno, divulgacao_planejar,
                    receita_conferir_ingredientes, registrar_condicoes_do_lote, registrar_troca_confirmada)
from .units import norm_text

logger = logging.getLogger(__name__)

MAX_ITENS = 12  # linhas do carrinho (e ingredientes de cada prato) citadas; o resto vira "e mais N"
MAX_SALVOS = 8
_CADASTRO_CHAT = re.compile(r"\[painel da tela: cadastro guiado no chat(?:; salvou (negocio|cozinha))?\]")
_PERGUNTA_BUSCA_RAPIDA = "Quer que eu procure essa versão mais rápida?"


def _qtd(i: dict[str, Any]) -> str:
    return f"{i['quantidade']:g}" + (f" {i['unidade']}" if i.get("unidade") else "")


def _item(i: dict[str, Any]) -> str:
    extras = (", já tem" if i.get("ja_tem") else "") + (", preço estimado" if i.get("estimado") else "")
    return f"{i['nome']} ({_qtd(i)}, {costing.brl(i['preco_total'])}{extras})"


def _mais(lista: list[Any], limite: int) -> str:
    return f"; e mais {len(lista) - limite} itens" if len(lista) > limite else ""


def _carrinho(store: Store) -> str:
    pratos = store.carrinho_pratos()
    avulsos = [i for i in store.carrinho_itens() if not i["prato"]]
    partes = []
    for g in pratos:
        ings = "; ".join(_item(i) for i in g["ingredientes"][:MAX_ITENS]) + _mais(g["ingredientes"], MAX_ITENS)
        partes.append(f"PRATO {g['prato']} {costing.brl(g['valor_do_prato'])} [{ings}]")
    partes += [_item(i) for i in avulsos[:MAX_ITENS]]
    return " | ".join(partes) + _mais(avulsos, MAX_ITENS) if partes else "vazio"


def _salvos(store: Store) -> str:
    s = store.salvos()
    if not s["pratos"] and not s["ingredientes"]:
        return "nada ainda"
    pratos = "; ".join(f"{p['nome']} ({costing.brl(p['preco_total'])})" for p in s["pratos"][:MAX_SALVOS])
    ingredientes = "; ".join(f"{i['nome']} ({costing.brl(i['preco_total'])})" for i in s["ingredientes"][:MAX_SALVOS])
    return f"pratos: {pratos or 'nenhum'} | ingredientes: {ingredientes or 'nenhum'}"


def _ids_em_foco(mensagem: Any, pratos: list[dict[str, Any]]) -> set[int]:
    """Somente um ID explícito ou ocorrência completa do nome identifica o alvo."""
    if not isinstance(mensagem, str):
        return set()
    ids = {int(n) for n in re.findall(r"(?<!\w)#\s*(\d+)\b", mensagem)}
    if not ids:
        texto = norm_text(mensagem)
        # O nome completo identifica o alvo; um fragmento não escolhe por ela.
        citados = []
        for prato in pratos:
            nome = norm_text(prato["nome"])
            citados.extend((m.start(), m.end(), prato["id"]) for m in
                           re.finditer(rf"(?<!\w){re.escape(nome)}(?!\w)", texto))
        # Se dois nomes se sobrepõem na mesma ocorrência, vale o mais específico.
        escolhidos = []
        for inicio, fim, prato_id in sorted(citados, key=lambda p: p[1] - p[0], reverse=True):
            if not any(inicio < outro_fim and fim > outro_inicio for outro_inicio, outro_fim, _ in escolhidos):
                escolhidos.append((inicio, fim, prato_id))
        ids = {prato_id for _, _, prato_id in escolhidos}
    return ids


def _pediu_alternativa(texto: str) -> bool:
    literal = re.sub(r"```[\s\S]*?```|`[^`]*`|\"[^\"]*\"|'[^']*'|“[^”]*”|«[^»]*»", " ", texto)
    normal = norm_text(literal)
    for pedido in re.finditer(r"\b(?:adapt\w*|troq\w*|substitu\w*|alternativ\w*|equivalente\w*|similar\w*)\b", normal):
        antes = normal[:pedido.start()]
        if not re.search(r"\b(?:nao|sem)\s+(?:(?:quero|preciso|pode)\s+)?(?:(?:que\s+)?voce\s+)?(?:uma\s+)?$", antes):
            return True
    return False


def _bloqueio_relacionado(bloqueio: dict[str, Any], mudancas: dict[str, Any]) -> bool:
    tipo = bloqueio["tipo"]
    if tipo == "fogao_capacidade_pendente":
        return "fogao" in (mudancas.get("equipamentos") or {})
    if tipo.startswith("equipamento_"):
        campos = mudancas.get("equipamentos") or {}
        nome = bloqueio["detalhe"].split(":", 1)[0]
        nomes = {cozinha.chave_equipamento(item) for item in re.split(r"\s+ou\s+", nome, flags=re.I)}
        return bool(nomes & {cozinha.chave_equipamento(chave) for chave in campos})
    if tipo.startswith("tecnica_"):
        campos = mudancas.get("tecnicas") or {}
        nome = bloqueio["detalhe"].split(":", 1)[0]
        chave = cozinha.chave_tecnica(nome)
        return chave in campos or any(cozinha.chave_tecnica(campo) == chave for campo in campos)
    operacao = {"gas": {"gas_indisponivel", "gas_a_confirmar"},
                "energia": {"energia_indisponivel", "energia_a_confirmar"}}
    return any(tipo in tipos for chave, tipos in operacao.items() if chave in (mudancas.get("restricoes") or {}))


def _impedimento_declarado(store: Store, mensagem: str, mudancas: dict[str, Any], sessao: str) -> str | None:
    """Não deixa um aceite genérico encobrir uma condição contrária recém-declarada."""
    if not mudancas or _pediu_alternativa(mensagem):
        return None
    ids = _ids_em_foco(mensagem, store.pratos_em_andamento())
    if len(ids) != 1:
        return None
    prato = store.obter_prato(next(iter(ids)))
    receita = store.obter_receita(prato["receita_id"]) if prato and prato.get("receita_id") else None
    if receita is None:
        return None
    ingredientes = receita["dados"].get("ingredientes") or prato["dados"].get("ingredientes") or []
    avaliador = (Store(store.db_path, store.xlsx_path, ignorar_prato_id=prato["id"])
                 if prato["status"] == "aceito" else store)
    aval = _avaliar_prato(avaliador, ingredientes, receita)
    relacionados = [b for b in aval["bloqueios"] if _bloqueio_relacionado(b, mudancas)]
    if not relacionados:
        return None
    tipos = {b["tipo"] for b in relacionados}
    if "gas_a_confirmar" in tipos:
        pergunta = "O equipamento usado nesta receita é elétrico ou depende de gás?"
    elif "energia_a_confirmar" in tipos:
        pergunta = "Você consegue garantir energia suficiente para preparar e conservar este lote?"
    elif "equipamento_nao_informado" in tipos:
        pergunta = "Você consegue confirmar se tem esse equipamento funcionando?"
    elif "tecnica_parcial" in tipos:
        pergunta = "Você já consegue executar essa técnica neste preparo ou prefere uma alternativa?"
    elif re.search(r"\bnao quero (?:adaptar|uma adaptacao)\b", norm_text(mensagem)):
        pergunta = "Quer escolher outro prato compatível com sua cozinha?"
    else:
        pergunta = "Quer que eu procure uma adaptação compatível com sua cozinha?"
    linhas = ["Considerei o que você informou agora sobre a cozinha.",
              f"A versão atual do prato #{prato['id']}, **{prato['nome']}**, **não pode ser aprovada nas condições informadas**.",
              "\n".join(f"- {b['detalhe']}" for b in relacionados), pergunta]
    montagem.registrar_resposta(sessao, linhas, substituir=True, prioridade=20)
    return "Condição atualizada; apresente este impedimento antes de qualquer preço ou compra:\n" + "\n\n".join(linhas)


def _ficha_em_foco(store: Store, mensagem: Any, pratos: list[dict[str, Any]]) -> list[str]:
    """Expande só a ficha mencionada, usando a receita e as condições atuais."""
    ids = _ids_em_foco(mensagem, pratos)
    if not ids and jornada.pediu_continuidade(mensagem):
        retomada = jornada.ficha_para_retomar(store)
        ids = {retomada["id"]} if retomada else set()
    if not ids:
        return []
    if len(ids) > 1:
        nomes = {p["id"]: p["nome"] for p in pratos}
        opcoes = "; ".join(f"#{i} {nomes.get(i, '(não localizado)')}" for i in sorted(ids))
        return [f"Fichas mencionadas: {opcoes}. Confirme qual ficha quer revisar primeiro; não escolha nem aprove por ela."]
    prato_id, = ids
    prato = store.obter_prato(prato_id)
    if prato is None:
        return [f"A ficha #{prato_id} não foi encontrada. Confirme o identificador ou nome; não use outra ficha no lugar."]
    linhas = [f"FICHA EM FOCO: prato #{prato_id}, {prato['nome']} (estado salvo: {prato['status']})."]
    receita = store.obter_receita(prato["receita_id"]) if prato.get("receita_id") else None
    if receita is None:
        fonte_url = (prato["dados"].get("selecao") or {}).get("fonte_url")
        linhas.append("Nome e número do prato já estão salvos; não peça para confirmá-los novamente. "
                      + (f"Leia a fonte escolhida {fonte_url} e use os ingredientes, medidas e rendimento publicados para montar a ficha. "
                         if fonte_url else "Este rascunho ainda precisa de ingredientes, medidas e rendimento. ")
                      + "Pergunte somente o dado essencial que continuar desconhecido após consultar a fonte e o estoque.")
        return linhas
    ingredientes = (receita["dados"].get("ingredientes") or prato["dados"].get("ingredientes") or [])
    resumo = []
    for ingrediente in ingredientes[:MAX_ITENS]:
        if isinstance(ingrediente, dict):
            quantidade = ingrediente.get("quantidade")
            medida = f"{quantidade:g}" if type(quantidade) in (int, float) else str(quantidade or "quantidade não informada")
            resumo.append(f"{medida} {ingrediente.get('unidade') or ''} de {ingrediente.get('nome') or 'ingrediente não informado'}".strip())
        else:
            resumo.append(str(ingrediente))
    linhas.append(f"A receita já está salva (receita_id={receita['id']}); não peça o nome, a receita ou a lista novamente.")
    linhas.append("Ingredientes salvos: " + ("; ".join(resumo) + _mais(ingredientes, MAX_ITENS) if resumo else "lista pendente") + ".")
    porcoes = receita.get("porcoes") or prato.get("porcoes")
    linhas.append("Rendimento salvo: " + (f"{porcoes:g} porções." if type(porcoes) in (int, float) else "a confirmar."))
    avaliador = (Store(store.db_path, store.xlsx_path, ignorar_prato_id=prato_id)
                 if prato["status"] == "aceito" else store)
    aval = _avaliar_prato(avaliador, ingredientes, receita)
    requisitos = aval["requisitos"]
    notas = Store.anotacoes_faltantes(aval["resultado"], ingredientes,
                                     receita["dados"].get("ingredientes_faltantes") or [], receita["dados"].get("adaptacao"))
    if notas:
        linhas.append("Observações de ingredientes na ficha: " + " ".join(a["observacao"] for a in notas))
        linhas.append(montagem.INSTRUCAO_ADAPTACAO)
    linhas.append("Requisitos desta versão: equipamentos " + (", ".join(requisitos["equipamentos_necessarios"]) or "nenhum específico informado")
                  + "; técnicas " + (", ".join(requisitos["tecnicas_necessarias"]) or "nenhuma específica informada")
                  + "; tempo " + (f"{requisitos['tempo_preparo_min']:g} min." if requisitos["tempo_preparo_min"] else "a confirmar."))
    if texto_caseiro := _texto_preparos_caseiros(aval["resultado"]):
        linhas.append(texto_caseiro)
        linhas.append("Considere esses preparos simples subentendidos no trabalho da cozinha. Não peça compra do derivado pronto nem uma confirmação separada para cada preparo; apresente-os na ficha antes do aceite final.")
    linhas.append(f"Veredito ATUAL desta ficha: {aval['veredito']}.")
    orcamento = aval["orcamento"]
    compras = ("Compras desta ficha ainda a conferir" if aval["resultado"]["pendencias"] else
               f"Compras desta ficha: {costing.brl(orcamento['compras_desta_receita'])}")
    linhas.append(f"{compras}; orçamento disponível para ela: {costing.brl(orcamento['restante'])}.")
    if aval["bloqueios"]:
        pendencias = jornada.pendencias_ordenadas(aval["bloqueios"])
        linhas.append("Pendências atuais da ficha:\n" + "\n".join(f"- {b['detalhe']}" for b in pendencias[:6])
                      + _mais(pendencias, 6))
        dados = receita["dados"]
        tipos = {b["tipo"] for b in pendencias}
        if tipos & {"tempo_da_receita_pendente", "tempo_insuficiente"}:
            preparo = dados.get("modo_preparo") or (dados.get("fonte_verificada") or {}).get("modo_preparo")
            if preparo:
                linhas.append(f"Preparo salvo da versão em foco: {str(preparo)[:2200]}")
        pergunta = jornada._pergunta(prato, pendencias, dados)
        if pergunta and not jornada.pergunta_pendente(prato, pendencias, dados):
            linhas.append(f"Duas tentativas já foram usadas para a pergunta «{pergunta['texto']}». "
                          "Use as respostas anteriores e os dados salvos. Se faltar duração, consulte a fonte salva ou estime "
                          "com base nas etapas. Para registrar uma estimativa na versão original, chame "
                          f"receita_conferir_ingredientes com prato_id={prato_id}, tempo_preparo_min e tempo_preparo_base, "
                          "preservando a fonte, os ingredientes e o preparo. "
                          "Se a informação for indispensável e não puder ser deduzida, proponha uma versão que dispense "
                          "essa condição ou outra receita compatível. Passe para essa decisão sem repetir a pergunta.")
        elif "tempo_insuficiente" in tipos:
            linhas.append("O tempo dessa versão já foi comparado ao da Dona Maria. Explique a incompatibilidade usando a "
                          "evidência do preparo; um mínimo de forno não inclui a montagem. Proponha um método mais rápido "
                          "que cozinhe todos os ingredientes que precisam de cocção. Não encurte a receita pulando uma "
                          "etapa de cozimento: se retirar a etapa, retire também os ingredientes que dependem dela, como "
                          "uma cobertura com ovo cru. Se não houver uma adaptação com base suficiente, busque outra receita "
                          "real com duração compatível. Use o tempo disponível já salvo e reconfira a proposta antes do aceite.")
        if pendencias[0]["tipo"] in ("fonte_incompleta", "fonte_divergente"):
            linhas.append("Corrija primeiro a ficha se ela pediu a original, usando os dados da fonte; "
                          "se delegou uma versão do estoque, registre adaptacao e o preparo próprio, sem restaurar ingredientes retirados. "
                          "Não pergunte à pessoa dados disponíveis na fonte. A diferença declarada não bloqueia uma versão local viável. "
                          "Não aprove, reserve, compre ou simule preço nesta retomada.")
            return linhas
        embalagens = [i["ingrediente"] for i in aval["resultado"]["itens"]
                      if "peso/volume" in str(i.get("pendencia") or "")]
        if embalagens:
            linhas.append(f"Pergunta necessária: qual é o peso ou volume de uma embalagem de {embalagens[0]}? "
                          "Peça a medida em g/kg ou ml/L; não estime o conteúdo da embalagem.")
        # A pendência que ela lê diz só o que falta. Como resolver fica aqui, no contexto que só a consultora vê.
        sem_preco = [str(i["ingrediente"]) for i in aval["resultado"]["itens"] if i.get("falta_preco")]
        if sem_preco:
            linhas.append("Faltantes sem preço: " + "; ".join(sem_preco[:4]) + _mais(sem_preco, 4) + ". Já ficam anotados na ficha. "
                          "Proponha uma versão com o estoque, por substituição, preparo caseiro ou omissão declarada. "
                          "Se ela preferir manter o complemento, pesquise ou pergunte o preço "
                          "e reconfira com preco_compra_estimado, quantidade_compra e unidade_compra. Não cite nomes de campo para ela.")
        linhas.append("Responda sobre esta ficha e pergunte apenas o dado essencial que falta para resolver a pendência. "
                      "Não ofereça pesquisar ou montar outra receita antes de explicar o impedimento da ficha já salva. "
                      "Não aprove, não reserve estoque e não simule preço enquanto houver bloqueio.")
    else:
        linhas.append("A ficha está viável nas condições atuais. Mostre a conferência; viabilidade não substitui o aceite nem a escolha do preço.")
        if any(a["prato_id"] == prato_id for a in store.analises_preco()):
            linhas.append("Esta versão já tem aceite e análise de preço salvos. Não peça o aceite novamente numa retomada. "
                          "Consulte analytics_analises_consultar para apresentar os cenários com os custos atuais e peça somente a escolha do preço. "
                          "Se os números mudaram, explique a diferença antes da nova escolha; não use valores de uma mensagem antiga.")
        elif prato["status"] == "aceito":
            linhas.append("O prato já está registrado. Informe o preço salvo e acompanhe a revisão solicitada; não trate uma retomada como pedido de aprovar outro prato.")
    return linhas


def _pergunta_da_retomada(store: Store, prato: dict[str, Any], receita: dict[str, Any], sessao: str,
                         *, abertura: str = "") -> None:
    """Encaminha um impedimento comprovado ou a próxima pergunta ainda aberta."""
    if prato["status"] != "selecionado":
        return
    aval = _avaliar_prato(store, receita["dados"].get("ingredientes") or [], receita)
    if aval["resultado"]["faltantes"] or receita["dados"].get("ingredientes_faltantes"):
        store.atualizar_faltantes_rascunho(prato["id"], receita["id"], receita["dados"].get("ingredientes") or [], aval["resultado"])
    notas = Store.anotacoes_faltantes(aval["resultado"], receita["dados"].get("ingredientes") or [],
                                     receita["dados"].get("ingredientes_faltantes") or [], receita["dados"].get("adaptacao"))
    observacoes = "Observações da ficha: " + " ".join(a["observacao"] for a in notas) if notas else ""
    medidas_estimadas = montagem.texto_medidas_estimadas(receita["dados"])
    if aval["bloqueios"] and aval["bloqueios"][0]["tipo"] == "tempo_insuficiente":
        detalhe = aval["bloqueios"][0]["detalhe"]  # já vem escrito para ela, sem a orientação ao modelo
        proxima = ("Posso buscar uma versão mais rápida com os ingredientes principais e um preparo que dispense o forno."
                   if aval["requisitos"].get("tempo_minimo_de_forno") else
                   "Posso buscar uma versão mais rápida que aproveite os ingredientes principais.")
        montagem.registrar_resposta(sessao, [abertura or f"Revisei **{prato['nome']}** (prato #{prato['id']}).",
                                             detalhe[:1].upper() + detalhe[1:].rstrip(".") + ".", observacoes, medidas_estimadas,
                                             proxima + " " + _PERGUNTA_BUSCA_RAPIDA], substituir=True, prioridade=20)
        return
    if aval["bloqueios"] and aval["bloqueios"][0]["tipo"] in {
            "gas_indisponivel", "energia_indisponivel", "refrigeracao_insuficiente",
            "equipamento_faltando", "tecnica_faltando", "tecnica_parcial"}:
        montagem.registrar_resposta(sessao, [abertura or f"A ficha do prato #{prato['id']}, **{prato['nome']}**, continua como rascunho.",
                                             aval["bloqueios"][0]["detalhe"], observacoes, medidas_estimadas,
                                             "Quer que eu sugira uma adaptação compatível com as condições que você informou?"],
                                     substituir=True, prioridade=20)
        return
    pergunta = jornada.pergunta_pendente(prato, aval["bloqueios"], receita["dados"])
    if pergunta:
        montagem.registrar_resposta(sessao, [abertura or f"A ficha do prato #{prato['id']}, **{prato['nome']}**, continua salva como rascunho.",
                                             observacoes, medidas_estimadas, pergunta["texto"]], substituir=True, prioridade=20)
    elif not aval["bloqueios"]:
        dados = receita["dados"]
        fonte_url = receita.get("fonte_url")
        linhas = [abertura, f"Conferi a ficha do prato #{prato['id']}, **{prato['nome']}**. Esta versão está viável na sua cozinha.",
                  observacoes, medidas_estimadas]
        if fonte_url:
            linhas.append(f"Fonte: [{prato['nome']}]({fonte_url}).")
        if dados.get("adaptacao"):
            linhas.append(f"Adaptação desta versão: {dados['adaptacao']}")
        medidas = dados.get("medidas_de_uso_confirmadas") or {}
        if medidas:
            linhas.append("Medidas de uso locais confirmadas por você (não publicadas na fonte): " + "; ".join(
                f"{m['quantidade']:g} {m['unidade']} de {montagem.nome_da_medida(nome)}" for nome, m in medidas.items()) + ".")
        ingredientes = "; ".join(f"{i['quantidade_receita']} de {i['ingrediente']}" for i in aval["resultado"]["itens"])
        linhas.append(f"Ingredientes do lote ({receita['porcoes']:g} porções): {ingredientes}.")
        if dados.get("modo_preparo"):
            linhas.append(f"Preparo: {dados['modo_preparo']}")
        if texto_caseiro := _texto_preparos_caseiros(aval["resultado"]):
            linhas.append(texto_caseiro)
            linhas.append(f"Tempo total estimado com esses preparos: {aval['requisitos']['tempo_preparo_min']:g} min.")
        linhas.extend([f"Custo dos ingredientes usados: {costing.brl(aval['resultado']['cmv_total'])} por lote. "
                       f"Compras complementares: {costing.brl(aval['resultado']['compras_total_estimado'])}.",
                       "Ainda não reservei ingredientes nem comprometi o saldo desta ficha.",
                       f"Você gosta dessa versão e aceita preparar o prato #{prato['id']}, {prato['nome']}, sem impedimentos?"])
        montagem.registrar_resposta(sessao, linhas, substituir=True, prioridade=20)
    elif abertura:
        montagem.registrar_resposta(sessao, [abertura, observacoes, medidas_estimadas, aval["bloqueios"][0]["detalhe"]],
                                     substituir=True, prioridade=20)


def _conducao_adaptacao_llm(store: Store, fala: falas_mod.Fala) -> str | None:
    if not montagem.pediu_adaptacao(fala.texto):
        return None
    pratos = store.pratos_em_andamento()
    ids = _ids_em_foco(fala.texto, pratos) or _ids_em_foco(fala.resposta_a, pratos)
    abertos = [p for p in pratos if p["status"] == "selecionado" and p.get("receita_id")
               and (not ids or p["id"] in ids)]
    if not abertos and not ids:
        return None
    if len(abertos) != 1:
        return "Ela pediu uma adaptação. Confirme qual ficha deseja adaptar; não escolha nem altere outro prato."
    prato, = abertos
    receita = store.obter_receita(prato["receita_id"])
    referencia = ""
    if receita:
        itens = receita["dados"].get("ingredientes") or []
        pedidos = {norm_text(montagem.nome_da_medida(n)) for n in montagem.omissoes_pedidas(fala.texto, itens)}
        if pedidos:
            resultado = _avaliar_prato(store, itens, receita)["resultado"]
            proposta = adaptacao_estoque.propor(receita, resultado)
            if proposta and pedidos <= {norm_text(n) for n in proposta[1]}:
                ficha, omitidos = proposta
                referencia = ("\nHá uma versão local de referência para as omissões pedidas. Use estes argumentos "
                              "em receita_conferir_ingredientes se atendem ao pedido. Preserve as etapas e suas medidas como conjunto. "
                              + json.dumps({**ficha, "prato_id": prato["id"], "ingredientes_omitidos": omitidos}, ensure_ascii=False))
    return (f"ADAPTAÇÃO DELEGADA: trabalhe no prato #{prato['id']}, {prato['nome']}, receita_id={prato['receita_id']}. "
            "A LLM deve propor e salvar a versão, não encerrar dizendo que não existe uma variante no catálogo local. "
            + (guias.adaptacao_despensa() or montagem.INSTRUCAO_ADAPTACAO) + referencia)


def _montar_com_estoque(store: Store, prato: dict[str, Any], receita: dict[str, Any], sessao: str) -> dict[str, Any] | None:
    """Aplica soluções com base conhecida. A consultora assume as combinações restantes."""
    dados = receita["dados"]
    itens = dados.get("ingredientes") or []
    aval = _avaliar_prato(store, itens, receita)
    proposta = adaptacao_estoque.propor(receita, aval["resultado"])
    args = {chave: dados[chave] for chave in ("adaptacao", "modo_preparo", "tempo_preparo_min", "tempo_preparo_base",
                                            "equipamentos_necessarios", "tecnicas_necessarias", "ingredientes_omitidos") if chave in dados}
    args.update(ingredientes=itens, porcoes=receita["porcoes"])
    if proposta:
        args.update(proposta[0])
    elif any(b["tipo"] == "preparo_adaptacao_pendente" for b in aval["bloqueios"]):
        correcao = adaptacao_estoque.corrigir_pizza_de_arroz(receita)
        if correcao:
            teste = _avaliar_prato(store, correcao["ingredientes"], {**receita, "dados": {**dados, **correcao}})["resultado"]
            if not (teste["pendencias"] or teste["compras"]):
                args.update(correcao)
                proposta = correcao
    _, estimadas = montagem.estimar_medidas_do_lote({**receita, "dados": {**dados, **args}})
    if not (proposta or estimadas):
        if any(b["tipo"] in ("ingrediente_pendente", "fonte_incompleta", "fonte_divergente", "preparo_adaptacao_pendente")
               for b in aval["bloqueios"]):
            return None
        if dados.get("montagem_com_estoque"):
            return receita
    pesquisa.marcar_turno(sessao, None)
    resultado = json.loads(receita_conferir_ingredientes({**args, "prato_id": prato["id"]}, session_id=sessao,
                                                        receita_esperada=receita["id"], montagem_com_estoque=True))
    return store.obter_receita(resultado["receita_id"]) if resultado["success"] else None


def _adaptacao_do_rascunho(store: Store, fala: falas_mod.Fala, sessao: str) -> str | None:
    if re.search(r"[?\n`'\"“”]|^\s*>", fala.texto):
        return None
    texto = norm_text(fala.texto).strip(" .!")
    montagem_delegada = re.fullmatch(r"monte a ficha do prato (\d+) com o estoque disponivel", texto)
    pedido = re.fullmatch(r"(?:adapte|adapta|pode adaptar) o prato (\d+), (.+?), com o estoque disponivel, sem comprar (.+)", texto)
    ignorar = re.fullmatch(r"(?:ignore|ignora|pule) os ingredientes (?:faltantes|que (?:eu )?nao tenho|que nao tem)", texto)
    if not (montagem_delegada or pedido or ignorar):
        return _conducao_adaptacao_llm(store, fala)
    comando_com_id = montagem_delegada or pedido
    prato = store.obter_prato(int(comando_com_id[1])) if comando_com_id else jornada.ficha_para_retomar(store)
    if not prato or prato["status"] != "selecionado" or not prato.get("receita_id"):
        return None
    if pedido and pedido[2] != norm_text(prato["nome"]):
        return None
    receita = store.obter_receita(prato["receita_id"])
    if not receita:
        return None
    if montagem_delegada or ignorar:
        montada = _montar_com_estoque(store, prato, receita, sessao)
        if not montada:
            return _conducao_adaptacao_llm(store, fala)
        _pergunta_da_retomada(store, prato, montada, sessao,
                             abertura=f"Montei a ficha de **{prato['nome']}** com o estoque disponível.")
        return "Mostre a adaptação e as estimativas gravadas, seguidas das condições reais da cozinha que ainda faltam."
    dados = receita["dados"]
    alvo = norm_text(montagem.nome_da_medida(pedido[3])) if pedido else None
    ingredientes = dados.get("ingredientes") or []
    avaliacao = _avaliar_prato(store, ingredientes, receita)
    faltantes = avaliacao["resultado"]["faltantes"]
    nomes = {norm_text(montagem.nome_da_medida(f["ingrediente"])) for f in faltantes}
    if alvo and alvo not in nomes:
        ja_omitido = any(a.get("estado") == "omitido_na_adaptacao"
                         and norm_text(montagem.nome_da_medida(a["ingrediente"])) == alvo
                         for a in dados.get("ingredientes_faltantes") or [])
        tem_na_ficha = any(isinstance(item, dict) and norm_text(montagem.nome_da_medida(item["nome"])) == alvo
                           for item in ingredientes)
        if ja_omitido or tem_na_ficha:
            abertura = ("Essa adaptação já está salva na ficha." if ja_omitido else
                        "Já consigo aproveitar o estoque para esse ingrediente.")
            _pergunta_da_retomada(store, prato, receita, sessao, abertura=abertura)
            return "Retome somente a próxima pendência da ficha."
        return None
    if not faltantes:
        _pergunta_da_retomada(store, prato, receita, sessao, abertura="Conferi o estoque para os ingredientes desta ficha.")
        return "Retome somente a próxima pendência da ficha."
    proposta = adaptacao_estoque.propor(receita, avaliacao["resultado"], alvo)
    if proposta is None:
        return _conducao_adaptacao_llm(store, fala)

    args, omitidos = proposta
    pesquisa.marcar_turno(sessao, None)
    resultado = json.loads(receita_conferir_ingredientes({**args, "prato_id": prato["id"]}, session_id=sessao,
                                                        receita_esperada=receita["id"]))
    if not resultado["success"]:
        atual = store.obter_prato(prato["id"])
        if atual and atual["status"] == "selecionado" and atual.get("receita_id") != receita["id"]:
            nova = store.obter_receita(atual["receita_id"])
            if nova:
                _pergunta_da_retomada(store, atual, nova, sessao, abertura="A ficha foi atualizada enquanto eu conferia essa adaptação.")
                return "Retome a versão atual da ficha."
        montagem.registrar_resposta(sessao, [f"A ficha de **{prato['nome']}** continua salva.",
                                             "Não consegui salvar essa adaptação para o lote atual. Quer revisar a ficha comigo?"],
                                     substituir=True, prioridade=20)
        return "Informe a situação da ficha atual."
    nova = store.obter_receita(resultado["receita_id"])
    jornada.continuar_escolhas(store, [prato["id"]])
    retirados = " e ".join(filter(None, [", ".join(omitidos[:-1]), omitidos[-1]]))
    abertura = (f"Salvei a adaptação do prato #{prato['id']}, **{prato['nome']}**, com o estoque disponível. "
                f"Registrei na ficha a retirada de {retirados}.")
    _pergunta_da_retomada(store, prato, nova, sessao, abertura=abertura)
    return "A adaptação pedida está salva na mesma ficha. Apresente a próxima pendência sem pedir nova confirmação da lista."


def _guardar_para_depois(store: Store, texto: str, sessao: str) -> str | None:
    pedido = re.fullmatch(r"deixe o prato (\d+) como rascunho para depois", norm_text(texto).strip(" .!"))
    if not pedido or re.search(r"[?\n`'\"“”«»]|^\s*>", texto):
        return None
    prato = store.obter_prato(int(pedido[1]))
    if not prato or not jornada.definir_rascunho_adiado(store, prato["id"], adiar=True):
        return None
    proxima = jornada.resumo(store)
    linhas = [f"Guardei **{prato['nome']}** como rascunho em **Pratos atuais** para você retomar quando quiser."]
    if proxima.get("prato_nome") and proxima.get("prato_id") != prato["id"]:
        linhas.append(f"A próxima ficha em andamento é **{proxima['prato_nome']}**.")
    elif proxima["etapa"] in ("divulgacao", "concluido"):
        linhas.append("Agora você pode seguir com os pratos já registrados no cardápio.")
    else:
        linhas.append("Você pode seguir montando outro prato.")
    montagem.registrar_resposta(sessao, linhas, substituir=True, prioridade=20)
    return "Confirme que o rascunho foi guardado e apresente o próximo passo do cardápio."


def _resposta_da_pendencia(store: Store, falas: list[falas_mod.Fala], sessao: str,
                          condicoes: dict[str, tuple[str, str]], *, confirma_grupo: bool = False) -> tuple[dict[str, Any] | None, bool]:
    """Associa a resposta à pergunta feita no chat e à ficha mencionada."""
    fala = falas[-1]
    pratos = store.pratos_em_andamento()
    ids_da_resposta = _ids_em_foco(fala.texto, pratos)
    ids = (ids_da_resposta if confirma_grupo or montagem.tem_assinatura_da_medida(fala.texto) else
           _ids_em_foco(fala.resposta_a, pratos) or ids_da_resposta)
    abertos = [p for p in jornada.fichas_em_uso(pratos, jornada.controle(store))
               if p["status"] == "selecionado" and p.get("receita_id") and (not ids or ids == {p["id"]})]
    if len(abertos) != 1:
        return None, False
    prato, = abertos
    if ids_da_resposta and ids_da_resposta != {prato["id"]}:
        return None, False
    receita = store.obter_receita(prato["receita_id"])
    if receita is None:
        return None, False
    dados = receita["dados"]
    aval = _avaliar_prato(store, dados.get("ingredientes") or [], receita)
    if condicoes:
        registrar_condicoes_do_lote(store, prato, receita, aval, condicoes)
        literal = re.sub(r"\b(?:continue|vamos continuar) o prato \d+[. ]*$", "", norm_text(fala.texto)).strip(" .")
        frases = {norm_text(evidencia).strip(" .") for _, evidencia in condicoes.values()}
        pura = bool(literal) and set(re.split(r"\.\s+", literal)) <= frases
        return prato, pura or confirma_grupo
    if confirma_grupo:
        return prato, True
    if montagem.salvar_medida_do_turno(store, prato, receita, fala, aval["bloqueios"]):
        return prato, True
    if not receita.get("porcoes") and not re.search(r"[?`\"“”<>]", fala.texto):
        texto = norm_text(fala.texto).strip(" .!")
        numero = r"(\d+(?:[.,]\d+)?)"
        porcoes = re.fullmatch(rf"(?:(?:o|este|esse) lote |(?:a|esta|essa) receita )?(?:rende|da) {numero} porcoes?", texto)
        if not porcoes and jornada.PERGUNTA_RENDIMENTO in fala.resposta_a:
            porcoes = re.fullmatch(rf"{numero}(?: porcoes?)?", texto)
        if porcoes and store.atualizar_pendencia_rascunho(prato["id"], receita["id"], porcoes=float(porcoes[1].replace(",", "."))):
            return prato, True
    pergunta = jornada._pergunta(prato, aval["bloqueios"], dados)
    if pergunta and pergunta.get("medida") and montagem.parece_resposta_de_medida(fala.texto):
        rotulo = pergunta["medida"]["rotulo"]
        montagem.registrar_resposta(sessao, [f"Estamos definindo {rotulo} para o lote inteiro. Digite um número com uma única unidade, como **5 g**.",
                                             pergunta["texto"]], substituir=True, prioridade=20)
        return prato, False
    falou_da_duracao = falas_mod.PERGUNTA_TEMPO_DA_RECEITA in fala.resposta_a
    if falou_da_duracao and any(b["tipo"] == "tempo_da_receita_pendente" for b in aval["bloqueios"]):
        tempo = falas_mod.tempo_da_receita_declarado(fala)
        minimo = aval["requisitos"].get("tempo_minimo_de_forno") or 0
        if tempo is not None and tempo >= minimo and store.atualizar_pendencia_rascunho(prato["id"], receita["id"], tempo=tempo):
            return prato, True
    tipo = aval["bloqueios"][0]["tipo"] if aval["bloqueios"] else ""
    if pergunta and jornada.perguntou_pendencia(fala.resposta_a, pergunta, tipo):
        texto = pergunta["texto"]
        tentativas = 0
        for anterior in reversed(falas) if tipo != "ingrediente_pendente" else []:
            ids_anteriores = _ids_em_foco(anterior.resposta_a, pratos) or _ids_em_foco(anterior.texto, pratos)
            if ids_anteriores and ids_anteriores != {prato["id"]}:
                break
            if jornada.perguntou_pendencia(anterior.resposta_a, pergunta, tipo) and (ids_anteriores or len(pratos) == 1):
                tentativas += 1
                if tentativas == 2:
                    store.atualizar_pendencia_rascunho(prato["id"], receita["id"], pergunta_esgotada=texto)
                    break
        sem_resposta = norm_text(fala.texto).strip(" .!") in {
            "nao sei", "ainda nao sei", "nao tenho certeza", "nao consigo informar",
        }
        return prato, sem_resposta
    if falou_da_duracao:
        return prato, falas_mod.tempo_da_receita_declarado(fala) is not None
    return None, False


def _cotacao_no_turno(store: Store, fala: falas_mod.Fala, sessao: str) -> tuple[int, str, bool] | None:
    if re.search(r"[?\n`'\"“”«»]|^\s*>", fala.texto):
        return None
    texto = norm_text(fala.texto).rstrip(" .!")
    comando = re.fullmatch(r"pesquise o preco de (.+) para o prato (\d+)", texto)
    sim = texto in {"sim", "sim, pode pesquisar", "pode pesquisar", "quero manter o ingrediente"}
    preco_informado = bool(re.fullmatch(
        r"(?:(?:o pacote )?custa )?(?:r )?\d+(?:[.,]\d+)?(?: reais?)?"
        r"(?: por |,? e (?:vem|tem) |, )\d+(?:[.,]\d+)? ?(?:kg|g|ml|l|unidades?|un)",
        texto))
    if not (comando or sim or preco_informado):
        return None
    pratos = store.pratos_em_andamento()
    ids = {int(comando[2])} if comando else _ids_em_foco(fala.resposta_a, pratos)
    if len(ids) > 1:
        return None
    prato = store.obter_prato(next(iter(ids))) if ids else jornada.ficha_para_retomar(store)
    if not prato or prato["status"] != "selecionado" or not prato.get("receita_id"):
        return None
    receita = store.obter_receita(prato["receita_id"])
    if not receita:
        return None
    dados = receita["dados"]
    aval = _avaliar_prato(store, dados.get("ingredientes") or [], receita)
    if comando:
        alvo = norm_text(montagem.nome_da_medida(comando[1]))
        notas = Store.anotacoes_faltantes(aval["resultado"], dados.get("ingredientes") or [],
                                         dados.get("ingredientes_faltantes") or [], dados.get("adaptacao"))
        nota = next((n for n in notas if n["estado"] == "omitido_na_adaptacao"
                     and norm_text(montagem.nome_da_medida(n["ingrediente"])) == alvo), None)
        if nota:
            rotulo = montagem.nome_da_medida(nota["ingrediente"])
            _pergunta_da_retomada(store, prato, receita, sessao,
                                 abertura=f"A ficha adaptada já está salva. {rotulo.capitalize()} ficou de fora desta versão.")
            return None
    pergunta = jornada.pergunta_pendente(prato, aval["bloqueios"], dados)
    if not pergunta or not (ingrediente := pergunta.get("ingrediente_a_cotar")):
        return None
    if comando and texto != norm_text(pergunta["opcoes"][0]["mensagem"]).rstrip(" .!"):
        return None
    if sim and pergunta["texto"] not in fala.resposta_a:
        return None
    if preco_informado and pesquisa.pergunta_do_preco(ingrediente) not in fala.resposta_a:
        return None
    return prato["id"], ingrediente, preco_informado


def _requisitos_declaraveis(store: Store, estado: dict[str, Any], prato_id: int | None = None) -> dict[str, tuple[str, ...]]:
    prato_id = prato_id if prato_id is not None else estado.get("prato_id")
    if not prato_id:
        return {"tecnicas": (), "equipamentos": ()}
    prato = store.obter_prato(prato_id)
    receita = store.obter_receita(prato["receita_id"]) if prato and prato.get("receita_id") else None
    dados = receita["dados"] if receita else {}
    pendencias = (estado.get("pendencias") or ()) if prato_id == estado.get("prato_id") else ()
    tecnicas = [*(dados.get("tecnicas_necessarias") or ()),
                *(b["detalhe"].split(":", 1)[0] for b in pendencias if b.get("tipo", "").startswith("tecnica_"))]
    equipamentos = [*(dados.get("equipamentos_necessarios") or ()),
                     *(b["detalhe"].split(":", 1)[0] for b in pendencias if b.get("tipo", "").startswith("equipamento_"))]
    return {"tecnicas": tuple(dict.fromkeys(tecnicas)),
            "equipamentos": tuple(dict.fromkeys(item.strip() for nome in equipamentos
                                               for item in re.split(r"\s+ou\s+", nome, flags=re.I)))}


def _alvo_da_resposta_salva(store: Store, fala: falas_mod.Fala, mudancas: dict[str, Any],
                            antes: dict[str, Any], requisitos_da_ficha: dict[str, tuple[str, ...]],
                            prato_explicito: int | None) -> dict[str, Any] | None:
    """A resposta validada resolve só a pergunta anterior, nunca um aceite ou uma escolha de receita."""
    pergunta = antes.get("pergunta")
    pendencias = antes.get("pendencias") or []
    if antes.get("passo") not in ("montagem", "concluido"):
        return None
    literal = norm_text(fala.texto).strip(" .!")
    tecnica = (bool({cozinha.chave_tecnica(n) for n in requisitos_da_ficha["tecnicas"]} & (mudancas.get("tecnicas") or {}).keys())
               and re.match(r"^(?:eu )?(?:(?:nao |nunca )?(?:sei|domino|consigo|faco)|estou aprendendo)\b", literal))
    equipamento = (bool({cozinha.chave_equipamento(n) for n in requisitos_da_ficha["equipamentos"]} & (mudancas.get("equipamentos") or {}).keys())
                   and re.match(r"^(?:sim[,]? )?(?:eu )?(?:(?:nao |nunca )?(?:tenho|possuo|uso)|comprei|ganhei|adquiri|meu|minha)\b", literal))
    declarada = bool((tecnica or equipamento)
                     and not re.search(r"\b(?:quero|prefiro|aceito|aprovo|pesquise|adapte|troque|monte)\b", literal))
    if declarada and prato_explicito is not None:
        return store.obter_prato(prato_explicito)
    if not declarada and (not pergunta or not pendencias or not _bloqueio_relacionado(pendencias[0], mudancas)):
        return None
    bocas = falas_mod.bocas_fogao_declaradas(fala)
    curta = norm_text(fala.texto).strip(" .!") in {
        "sim", "tenho", "tenho sim", "funciona", "funciona sim", "sei", "sei sim", "sei fazer",
        "nao", "nao tenho", "nao sei", "nao sei fazer", "nunca fiz", "ainda nao",
        "estou aprendendo", "aprendendo", "mais ou menos",
    }
    if not (curta or declarada) and bocas is None:
        return None
    explicita = bocas is not None and falas_mod.bocas_fogao_declaradas(falas_mod.extrair([], fala.texto)[-1]) is not None
    if not (explicita or declarada) and not jornada.perguntou_pendencia(fala.resposta_a, pergunta, pendencias[0]["tipo"]):
        return None
    pratos = store.pratos_em_andamento()
    ids = _ids_em_foco(fala.resposta_a, pratos) | _ids_em_foco(fala.texto, pratos)
    if ids and ids != {antes.get("prato_id")}:
        return None
    if not ids:
        abertos = [p for p in jornada.fichas_em_uso(pratos, jornada.controle(store)) if p["status"] == "selecionado"]
        if len(abertos) != 1:
            return None
    return store.obter_prato(antes["prato_id"])


def _alvo_do_tempo_salvo(store: Store, fala: Any, mudancas: dict[str, Any]) -> dict[str, Any] | None:
    """Encaminha uma resposta pura de disponibilidade já validada e persistida.

    O reconhecimento numérico pertence à validação da declaração. Aqui só
    verificamos que não há outra solicitação para a LLM atender neste turno.
    """
    declarados = mudancas.get("restricoes") or {}
    minutos = declarados.get("tempo_por_cozinhada_min")
    faixa = declarados.get("tempo_por_cozinhada_faixa")
    salvos = store.perfil().get("restricoes") or {}
    exato_salvo = (not isinstance(minutos, bool) and isinstance(minutos, (int, float)) and minutos > 0
                   and salvos.get("tempo_por_cozinhada_min") == minutos)
    faixa_salva = (isinstance(faixa, dict) and set(faixa) == {"operador", "minutos"}
                   and faixa["operador"] in {"menor_que", "maior_que"}
                   and type(faixa["minutos"]) in (int, float) and faixa["minutos"] > 0
                   and salvos.get("tempo_por_cozinhada_faixa") == faixa
                   and salvos.get("tempo_por_cozinhada_min") is None)
    if (not (exato_salvo or faixa_salva) or not fala or not isinstance(fala.texto, str) or "?" in fala.texto):
        return None
    pratos = store.pratos_em_andamento()
    ids = _ids_em_foco(fala.texto, pratos)
    if len(ids) > 1:
        return None
    texto = norm_text(fala.texto).strip(" .!?")
    for prato in pratos:
        for sufixo in (f"Continue o prato #{prato['id']}", f"Vamos continuar o prato #{prato['id']}",
                       f"Quero continuar o prato escolhido #{prato['id']} ({prato['nome']})"):
            final = norm_text(sufixo)
            if texto.endswith(" " + final) or texto.endswith(". " + final):
                texto = texto[:-len(final)].strip(" .!?")
    sem_medidas = re.sub(r"(?<!\w)\d+(?:[,.]\d+)?(?!\w)", " ", texto)
    vocabulario = {"eu", "tenho", "ate", "no", "maximo", "um", "uma", "dois", "duas", "tres", "quatro", "cinco",
                   "seis", "sete", "oito", "nove", "dez", "meia", "meio", "hora", "horas", "h", "min", "minuto", "minutos",
                   "e", "de", "por", "a", "cada", "para", "esta", "essa", "nesta", "nessa", "cozinhada", "cozinhadas",
                   "posso", "consigo", "dedicar", "disponho", "disponivel", "durante", "cozinhar", "preparar", "menos", "mais"}
    if any(palavra not in vocabulario for palavra in re.findall(r"\w+", sem_medidas)):
        return None
    if ids:
        return next((p for p in pratos if p["id"] in ids), None)
    abertos = [p for p in jornada.fichas_em_uso(pratos, jornada.controle(store)) if p["status"] == "selecionado"]
    return abertos[0] if len(abertos) == 1 else None


def _cozinha(store: Store, primeira_mensagem: bool, cadastro_guiado: bool = False,
             cadastro_enviado: Optional[str] = None) -> list[str]:
    """Cadastro incremental: a ficha define o que precisa ser perguntado agora."""
    perfil = store.perfil()
    pend = _pendencias_perfil(perfil)
    pend_negocio = negocio.pendencias(store.negocio())
    linhas = [f"Cozinha dela (o que conta no chat ou marca na tela fica salvo): {cozinha.texto_do_perfil(perfil)}"]
    if not pend:
        linhas.append("Cozinha completa: não pergunte de novo o que está salvo. Confira os requisitos específicos de cada versão.")
    else:
        linhas.append("O cadastro geral da cozinha está parcial. Isso não impede pesquisar nem libera um prato: "
                      "confira e pergunte somente equipamentos, técnicas, tempo e condições operacionais relevantes ao preparo escolhido. "
                      "Não imponha preencher o catálogo inteiro nem todos os itens gerais antes de seguir.")
    if cadastro_enviado:
        nome = "do negócio" if cadastro_enviado == "negocio" else "da cozinha"
        linhas.append(f"O formulário {nome} já gravou no banco as respostas enviadas. Confirme o recebimento em uma frase "
                      "e conduza a próxima ação indicada na jornada nesta mesma resposta. Não grave os mesmos dados de novo.")
    if pend_negocio and not store.pratos_em_andamento():
        if cadastro_guiado:
            linhas.append("Para conhecer o negócio sem sair do chat, apresente [[cadastro:negocio]] sozinho numa linha. "
                          "Salve antes qualquer informação espontânea com negocio_salvar. Não encaminhe a pessoa ao menu.")
        else:
            linhas.append("Conheça o negócio aos poucos. Pergunte só o primeiro dado necessário e salve com negocio_salvar; "
                          "não apresente um interrogatório sobre todos os campos de uma vez.")
            linhas.append("Pergunta inicial do negócio: " + pend_negocio[0]["pergunta"])
    if cadastro_guiado and pend and not pend_negocio:
        linhas.append("Se a pessoa pedir para completar a cozinha, ofereça [[cadastro:cozinha]] sozinho numa linha. "
                      "Esse formulário é opcional; fora desse pedido conduza a receita e só colete os requisitos necessários pelo chat.")
    return linhas


def texto_do_painel(store: Optional[Store] = None, primeira_mensagem: bool = False, mensagem: Any = None,
                    escolha: Optional[str] = None, cadastro_guiado: bool = False, cadastro_enviado: Optional[str] = None,
                    mensagem_foco: Any = None, pesquisa_conduzida: bool = False, somente_sugestoes: bool = False,
                    foco_busca: pesquisa.FocoDaBusca | None = None) -> str:
    store = store or Store()
    resumo = store.carrinho_resumo()
    neg = store.negocio()
    controle_jornada = jornada.controle(store)
    linhas = [
        f"{MARCA_PAINEL} — dados atuais, não são fala dela]",
        "Saldo: " + _texto_saldo(resumo) + ".",
        f"Carrinho: {_carrinho(store)}.",
        f"Salvos (Meus pratos e ingredientes): {_salvos(store)}.",
        f"Negócio: {negocio.descrever(neg)}",
    ]
    pratos = store.pratos_em_andamento()
    estados = {"selecionado": "rascunho", "cmv_calculado": "em precificação", "aceito": "no cardápio"}
    itens = [f"#{p['id']} {p['nome']} ({estados[p['status']]})" for p in pratos[:MAX_SALVOS]]
    linhas.append("Pratos escolhidos: " + ("; ".join(itens) + _mais(pratos, MAX_SALVOS) if itens else "nenhum") + ".")
    if escolha:
        linhas.append(escolha)
    linhas += _cozinha(store, primeira_mensagem, cadastro_guiado, cadastro_enviado)
    solicitacao = mensagem if mensagem_foco is None else mensagem_foco
    configurado = controle_jornada["configuracao_concluida"]
    sobre_divulgacao = ((configurado or controle_jornada["divulgacao"] == "interesse") and isinstance(solicitacao, str)
                        and re.search(r"\b(?:divulgacao|publicidade|anuncios?|anunciar|campanha|investimento)\b", norm_text(solicitacao)))
    novo_prato_pendente = controle_jornada["proprio_pendente"] and not any(
        p["id"] > controle_jornada["ultimo_prato_ao_escolher"] for p in pratos)
    foco = [] if novo_prato_pendente or sobre_divulgacao else _ficha_em_foco(store, solicitacao, pratos)
    linhas += foco
    jornada_atual = (jornada.resumo(store, pratos=pratos) if not foco else
                    {"passo": "montagem", "etapa": "pendencias", "controle": controle_jornada})
    if not foco:
        conduzir = (mensagem is None or bool(cadastro_enviado) or pesquisa_conduzida
                    or jornada_atual["etapa"] == "proprio"
                    or jornada.pediu_continuidade(mensagem) or pesquisa.pede_receita(mensagem))
        linhas.append(jornada.contexto(store, conduzir=conduzir, estado=jornada_atual))
    elif configurado:
        linhas.append(jornada.INSTRUCAO_CONCLUSAO)
    else:
        linhas.append("Conduza essa ficha pelo chat. Resolva a próxima ação com as ferramentas e pergunte só a decisão ou o dado que faltar; os painéis são apoio opcional.")
    if isinstance(mensagem, str) and compras.pediu_registro(mensagem):
        linhas.append("Ela pediu entrada no estoque. Chame despensa_registrar_compras: use o receita_id da lista de compras que você mostrou, "
                      "ou os itens com quantidade e preço que ela informou agora. Copie o saldo devolvido. "
                      "Se faltarem os dados ou a confirmação da lista, peça só o que falta.")
    cedo = jornada_atual["passo"] in ("negocio", "estoque", "ajuda")
    roteiro = (None if cedo else pesquisa.ROTEIRO if somente_sugestoes else
               pesquisa.ROTEIRO_MONTAR if pesquisa_conduzida else pesquisa.roteiro_do_turno(mensagem))
    if roteiro:
        linhas.append(roteiro)
        foco_busca = foco_busca or pesquisa.foco_da_busca(neg, mensagem)
        if foco_busca:
            linhas.append(foco_busca.instrucao())
        linhas.extend(guias.pesquisa(neg, mensagem))
    # A conferência focal já diz o próximo passo. O guia geral de cenários
    # desvia um modelo pequeno para o preço quando ainda falta uma medida.
    guia_preco = guias.precificacao() if jornada_atual["etapa"] == "preco" and not foco else None
    if guia_preco:
        linhas.append(guia_preco)
    pediu_contas = isinstance(mensagem, str) and re.search(r"\b(?:despesas?|orcamento|saldo)\b", norm_text(mensagem))
    if (jornada_atual["etapa"] in ("aceite", "preco") or pediu_contas) and (skill := guias.gestao_estoque_despesas()):
        linhas.append(skill)
    if sobre_divulgacao or (jornada_atual["passo"] == "divulgacao" and controle_jornada["divulgacao"] == "interesse"):
        linhas.append(guias.divulgacao())
    linhas += [
        "Responda sempre em português do Brasil, da primeira à última mensagem, mesmo que a mensagem dela chegue em outro idioma; só mude se ela pedir explicitamente.",
        "Use isto ao sugerir pratos e ingredientes e ao falar do carrinho. Se ela pedir um item que já está no carrinho, avise. "
        "Use somente os valores das ferramentas. O custo da receita inclui a parte usada da despensa. "
        "Já pago, reservado, no carrinho e estimativa de compras são valores distintos. "
        "Se uma ferramenta bloquear a aprovação, copie diga_a_dona_maria e aguarde a escolha sobre a troca antes de mudar o prato.",
        "[fim do painel]",
    ]
    return "\n".join(linhas)


def ao_iniciar_turno(is_first_turn: bool = False, user_message: Any = None, session_id: str = "", turn_id: str = "",
                    conversation_history: Optional[list] = None, **_: Any) -> Optional[dict[str, str]]:
    """Hook ``pre_llm_call``: devolve o painel como contexto do turno (com o roteiro da pesquisa de receitas quando ela pede receita ou ideia,
    e o turno marcado para as ferramentas levarem o próximo passo). Nunca derruba o turno."""
    try:
        store = Store()
        montagem.iniciar_turno(session_id, turn_id)
        cadastro = _CADASTRO_CHAT.search(user_message) if isinstance(user_message, str) else None
        if cadastro:
            user_message = _CADASTRO_CHAT.sub("", user_message).strip()
        escolha = None
        fala = None
        falas: list[Any] = []
        mudancas: dict[str, Any] = {}
        acao_jornada = None
        ficha_da_resposta = None
        retomar_pergunta = False
        tempo_salvo = None
        if isinstance(user_message, str) and session_id:
            falas = extrair(conversation_history or [], user_message)
            fala = falas[-1] if falas else None
            if fala:
                antes = jornada.resumo(store)
                adiada = set(antes["controle"]["rascunhos_adiados"])
                if adiada:
                    retomar = jornada.alvo_da_retomada(fala.texto, store.pratos_em_andamento())
                    if retomar and retomar["id"] in adiada and jornada.definir_rascunho_adiado(store, retomar["id"], adiar=False):
                        antes = jornada.resumo(store)
                acao_jornada = jornada.acao_da_fala(antes, fala.texto, fala.resposta_a)
                if acao_jornada and not (acao_jornada == "interesse_divulgacao"
                                         and antes["controle"]["divulgacao"] == "interesse"):
                    jornada.responder(store, acao_jornada)
                prato_explicito = escolhas.id_mencionado(fala.texto)
                if prato_explicito not in antes.get("pratos_ativos", []):
                    prato_explicito = None
                requisitos_da_ficha = _requisitos_declaraveis(store, antes, prato_explicito)
                mudancas = atualizar_declaracoes_do_turno(store, fala, tecnicas_da_ficha=requisitos_da_ficha["tecnicas"],
                                                        equipamentos_da_ficha=requisitos_da_ficha["equipamentos"])
                andamento = jornada.resumo(store)
                condicoes_do_lote = falas_mod.condicoes_do_lote(fala.texto)
                pergunta_anterior = antes.get("pergunta") or {}
                confirma_grupo = bool(pergunta_anterior.get("confirmacoes_agrupadas") and any(
                    norm_text(fala.texto) == norm_text(opcao["mensagem"]) for opcao in pergunta_anterior["opcoes"]))
                confirma_sem_detalhe = (pergunta_anterior.get("confirmacoes_agrupadas")
                    and norm_text(fala.texto).strip(" .!") in {"sim", "nao", "sei", "sei fazer", "nao sei", "tenho", "tenho sim", "nao tenho"})
                if confirma_sem_detalhe:
                    rotulo = pergunta_anterior["opcoes"][0]["rotulo"]
                    montagem.registrar_resposta(session_id, [
                        f"Use **{rotulo}** se todas as condições listadas se aplicam. Se preferir responder no chat, diga quais você pode confirmar."
                    ], substituir=True, prioridade=20)
                elif andamento["passo"] in ("montagem", "concluido"):
                    ficha_da_resposta, retomar_pergunta = _resposta_da_pendencia(store, falas, session_id, condicoes_do_lote,
                                                                                confirma_grupo=confirma_grupo)
                resposta_salva = _alvo_da_resposta_salva(store, fala, mudancas, antes, requisitos_da_ficha, prato_explicito)
                if resposta_salva:
                    ficha_da_resposta, retomar_pergunta = resposta_salva, True
                tempo_salvo = _alvo_do_tempo_salvo(store, fala, mudancas)
                alvo_retomado = (ficha_da_resposta if retomar_pergunta else None) or tempo_salvo
                pendencias_antes = antes.get("pendencias") or []
                if (not alvo_retomado and pergunta_anterior and pendencias_antes
                        and pergunta_anterior["texto"] in fala.resposta_a and _bloqueio_relacionado(pendencias_antes[0], mudancas)
                        and any(norm_text(fala.texto) in (norm_text(o["resposta"]), norm_text(o["mensagem"]))
                                for o in pergunta_anterior["opcoes"])):
                    ficha_da_resposta = store.obter_prato(antes["prato_id"])
                    retomar_pergunta = bool(ficha_da_resposta)
                    alvo_retomado = ficha_da_resposta
                prato_em_conferencia = (alvo_retomado["id"] if alvo_retomado
                                        and all(estado == "sim" for estado, _ in condicoes_do_lote.values()) else None)
                if andamento["passo"] not in ("negocio", "estoque", "ajuda") and not (
                        escolha := _guardar_para_depois(store, fala.texto, session_id)):
                    if andamento["controle"]["proprio_pendente"]:
                        # A pessoa respondeu qual prato quer; essa resposta
                        # também pode escolher duas sugestões anteriores. Um pedido
                        # nomeado da despensa ("pizza de mussarela com o que tenho",
                        # "sem azeitona") monta a ficha local em vez de perguntar de novo.
                        escolha = (_adaptacao_do_rascunho(store, fala, session_id)
                                   or escolhas.preparar_turno(store, fala.texto, escolhas.lista_da_escolha(falas))
                                   or montagem.propor_turno(store, falas, session_id))
                    else:
                        escolha = (_adaptacao_do_rascunho(store, fala, session_id)
                                   or _impedimento_declarado(store, fala.texto, mudancas, session_id)
                                   or escolhas.preparar_turno(store, fala.texto, escolhas.lista_da_escolha(falas))
                                   or registrar_troca_confirmada(store, fala.texto, fala.resposta_a)
                                   or avancar_cardapio_no_turno(store, session_id, falas,
                                                               prato_em_conferencia=prato_em_conferencia)
                                   or montagem.propor_turno(store, falas, session_id))
                if not escolha and escolhas.pedido_de_escolha(fala.texto):
                    escolha = ("Ela quer guardar escolhas. Se os nomes estiverem claros na fala, chame pratos_selecionar. "
                               "Se não estiverem claros, peça os nomes ou números. Não volte à pesquisa nem escolha no lugar dela.")
        adaptacao_llm = bool(fala and escolha and montagem.pediu_adaptacao(fala.texto)
                             and not montagem.resposta_preparada(session_id, turn_id))
        estado_jornada = jornada.resumo(store)
        cedo = estado_jornada["passo"] in ("negocio", "estoque", "ajuda")
        proprio = estado_jornada["etapa"] == "proprio" or acao_jornada == "prato_proprio"
        ajuda_pedida = jornada.delegou_ajuda(user_message)
        pesquisa_negada = jornada.negou_pesquisa(user_message)
        versao_rapida = None
        resposta_busca = norm_text(fala.texto).strip(" .!") if fala else ""
        if (fala and fala.resposta_a.rstrip().endswith(_PERGUNTA_BUSCA_RAPIDA) and re.fullmatch(
                r"sim|(?:sim[, ]+)?(?:pode(?: (?:procurar|buscar|pesquisar))?|quero(?: sim| seguir com essa adaptacao)?)",
                resposta_busca)):
            pratos = store.pratos_em_andamento()
            ids = _ids_em_foco(fala.resposta_a, pratos)
            versao_rapida = next((p for p in pratos if ids == {p["id"]} and p["status"] == "selecionado"), None)
        pesquisa_conduzida = bool(not cedo and not proprio and not escolha and not pesquisa_negada and (
            versao_rapida or pesquisa.pediu_montar_um(user_message)
            or (estado_jornada["etapa"] == "pesquisa"
                and (acao_jornada == "ajuda_ia" or ajuda_pedida or jornada.pediu_continuidade(user_message)
                     or pesquisa.pede_receita(user_message)))
            or (estado_jornada["etapa"] in ("cardapio", "divulgacao") and fala
                and jornada.aceitou_proxima_pesquisa(fala.texto, fala.resposta_a))))
        sugestoes_conduzidas = bool(pesquisa_conduzida and (versao_rapida or (
            not pesquisa.pediu_montar_um(user_message) and estado_jornada["etapa"] == "pesquisa")))
        apresentacao = isinstance(user_message, str) and bool(re.fullmatch(
            r"(?:oi|ola|bom dia|boa tarde|boa noite|vamos comecar|quero comecar|por onde comecamos)", norm_text(user_message).strip(" .!?")))
        prontidao_ja_salva = bool(estado_jornada["etapa"] == "ajuda" and isinstance(user_message, str)
                                  and norm_text(user_message).strip(" .!") == "sim, estou pronta para comecar a montar meus pratos")
        parada_guiada = bool((cedo and (cadastro or acao_jornada or apresentacao or prontidao_ja_salva or jornada.pediu_continuidade(user_message)
                                        or pesquisa.pede_receita(user_message) or pesquisa.pediu_montar_um(user_message)))
                              or (proprio and (acao_jornada == "prato_proprio" or jornada.pediu_continuidade(user_message)))
                              or acao_jornada == "dispensar_divulgacao")
        if parada_guiada:
            montagem.registrar_resposta(session_id, [jornada.resposta_curta(store, estado_jornada)], substituir=True, prioridade=40)
        logger.info("Condução do turno: sessão=%s turno=%s tipo_mensagem=%s cadastro=%s salvo=%s etapa=%s conduzida=%s escolha=%s",
                    session_id, turn_id, type(user_message).__name__, bool(cadastro), cadastro[1] if cadastro else None,
                    estado_jornada["etapa"], pesquisa_conduzida, bool(escolha))
        # Quem pede outra busca já viu as opções anteriores desta conversa: elas não voltam como novidade.
        ja_mostradas = ([opcao for anterior in falas for opcao in escolhas.opcoes_exibidas(anterior.resposta_a)]
                        if sugestoes_conduzidas else [])
        if versao_rapida and sugestoes_conduzidas:
            ja_mostradas.append(versao_rapida)
        mensagem_da_busca = ("monte um prato para meu cardápio" if pesquisa_conduzida else
                             None if escolha or cedo or proprio or pesquisa_negada else user_message)
        foco_busca = (pesquisa.foco_da_busca(store.negocio(), f"receita de {versao_rapida['nome']}" if versao_rapida else user_message)
                      if mensagem_da_busca and (pesquisa_conduzida or pesquisa.pede_receita(mensagem_da_busca)) else None)
        cotacao = _cotacao_no_turno(store, fala, session_id) if fala and not (cedo or escolha or pesquisa_conduzida) else None
        pesquisa.marcar_turno(session_id, mensagem_da_busca, conduzida=pesquisa_conduzida,
                             somente_sugestoes=sugestoes_conduzidas, ja_mostradas=ja_mostradas, foco=foco_busca,
                             cotacao=cotacao[:2] if cotacao else None, cotacao_informada=bool(cotacao and cotacao[2]),
                             planejar_divulgacao=acao_jornada == "interesse_divulgacao")
        mensagem_foco = user_message
        if acao_jornada == "interesse_divulgacao":
            plano = json.loads(divulgacao_planejar({}, session_id=session_id))
            if plano["success"]:
                escolha = "O plano de divulgação foi preparado neste turno. Mostre a conclusão e as ações já preparadas."
            else:
                escolha = "O plano de divulgação ficou pendente. Explique este resultado: " + _motivo_pendente(plano)
        if adaptacao_llm and fala:
            pratos = store.pratos_em_andamento()
            ids = _ids_em_foco(fala.texto, pratos) or _ids_em_foco(fala.resposta_a, pratos)
            alvos = [p for p in pratos if p["status"] == "selecionado" and p.get("receita_id")
                     and (not ids or p["id"] in ids)]
            if len(alvos) == 1:
                alvo, = alvos
                receita = store.obter_receita(alvo["receita_id"])
                if receita:
                    pesquisa.conduzir_adaptacao(session_id, alvo, receita)
                    mensagem_foco = f"Quero continuar o prato escolhido #{alvo['id']}"
        if cotacao:
            prato_id, ingrediente, preco_informado = cotacao
            mensagem_foco = f"Quero continuar o prato escolhido #{prato_id}"
            escolha = ((f"Ela informou preço e quantidade da embalagem de {ingrediente} para a ficha #{prato_id}. "
                        "Chame receita_conferir_ingredientes para essa ficha com apenas o ingrediente cotado em ingredientes, "
                        "usando os valores que ela informou em preco_compra_estimado, quantidade_compra e unidade_compra.")
                       if preco_informado else
                       (f"Ela pediu a cotação de {ingrediente} para a ficha #{prato_id}. "
                        "Use web_search para pesquisar preço e tamanho da embalagem. Reconfira a mesma ficha com a cotação. "
                        "Se esses dados não estiverem disponíveis, peça o preço e a quantidade numa única pergunta."))
        if fala and not cedo and not pesquisa_negada and not adaptacao_llm:
            # Uma escolha explícita já autoriza a leitura e a montagem da
            # primeira ficha, não seu aceite. Use o alvo desta escolha para
            # não retomar um rascunho antigo por engano.
            novas_escolhas = escolhas.escolhidas(fala.texto, escolhas.lista_da_escolha(falas)) if escolha else []
            alvos = {p["nome"] for p in novas_escolhas}
            primeiro = next((p for p in store.pratos_em_andamento() if p["nome"] in alvos), None)
            if primeiro and novas_escolhas:
                mensagem_foco = f"Quero continuar o prato escolhido #{primeiro['id']}"
            retomada = jornada.alvo_da_retomada(fala.texto, store.pratos_em_andamento())
            if retomada is None and jornada.pediu_continuidade(fala.texto):
                retomada = jornada.ficha_para_retomar(store)
            if primeiro is None:
                primeiro = retomada
            if primeiro:
                receita = store.obter_receita(primeiro["receita_id"]) if primeiro.get("receita_id") else None
                conduzida = pesquisa.conduzir_escolhido(session_id, primeiro, receita=receita,
                                                        revisar_fonte=bool(retomada and retomada["id"] == primeiro["id"]))
                if retomada and receita and not conduzida:
                    dados = receita["dados"]
                    montar = (dados.get("montagem_com_estoque") or dados.get("adaptacao")) and (
                        dados.get("montagem_com_estoque") or montagem.estimar_medidas_do_lote(receita)[1])
                    revisada = _montar_com_estoque(store, retomada, receita, session_id) if montar else None
                    if montar and not revisada and pesquisa.conduzir_adaptacao(session_id, retomada, receita):
                        mensagem_foco = f"Quero continuar o prato escolhido #{retomada['id']}"
                        escolha = guias.adaptacao_despensa() or montagem.INSTRUCAO_ADAPTACAO
                    else:
                        _pergunta_da_retomada(store, retomada, revisada or receita, session_id)
            if not escolha and not pesquisa_conduzida:
                foco = ficha_da_resposta or tempo_salvo
                if foco:
                    mensagem_foco = f"Quero continuar o prato escolhido #{foco['id']}"
                if retomar_pergunta:
                    tempo_salvo = ficha_da_resposta
                receita_tempo = store.obter_receita(tempo_salvo["receita_id"]) if tempo_salvo and tempo_salvo.get("receita_id") else None
                if receita_tempo:
                    _pergunta_da_retomada(store, tempo_salvo, receita_tempo, session_id)
        if versao_rapida and sugestoes_conduzidas:
            mensagem_foco = f"Quero continuar o prato escolhido #{versao_rapida['id']}"
            escolha = (f"Dona Maria pediu uma opção mais rápida para {versao_rapida['nome']}. Pesquise outra receita real que use "
                       "os ingredientes principais e compare o tempo total publicado com a disponibilidade salva. "
                       "Inclua o limite em minutos na consulta. Priorize métodos que dispensem o forno e dê preferência a uma "
                       "fonte que publique o tempo total.")
        return {"context": texto_do_painel(store=store, primeira_mensagem=bool(is_first_turn), mensagem=None if escolha else user_message,
                                            escolha=escolha, cadastro_guiado=cadastro is not None,
                                            cadastro_enviado=cadastro[1] if cadastro else None, mensagem_foco=mensagem_foco,
                                            pesquisa_conduzida=pesquisa_conduzida, somente_sugestoes=sugestoes_conduzidas,
                                            foco_busca=foco_busca)}
    except Exception:  # noqa: BLE001 - sem o painel a consultora ainda tem as tools
        logger.exception("painel: não consegui montar o resumo do carrinho")
        return None
