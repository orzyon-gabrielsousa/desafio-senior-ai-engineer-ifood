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

import logging
import re
from typing import Any, Optional

from . import compras, costing, cozinha, escolhas, guias, negocio, pesquisa
from .falas import MARCA_PAINEL, extrair
from .store import Store
from .tools import _pendencias_perfil, avancar_cardapio_no_turno, registrar_troca_confirmada

logger = logging.getLogger(__name__)

MAX_ITENS = 12  # linhas do carrinho (e ingredientes de cada prato) citadas; o resto vira "e mais N"
MAX_SALVOS = 8
_CADASTRO_CHAT = re.compile(r"\[painel da tela: cadastro guiado no chat(?:; salvou (negocio|cozinha))?\]")


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


def _cozinha(store: Store, primeira_mensagem: bool, cadastro_guiado: bool = False,
             cadastro_enviado: Optional[str] = None) -> list[str]:
    """A cozinha dela e, enquanto faltar resposta do negócio (onde fica a loja, tipo de negócio e de alimentação) ou da cozinha, a ordem de
    perguntar, com o texto das perguntas já pronto."""
    perfil = store.perfil()
    pend = _pendencias_perfil(perfil)
    pend_negocio = negocio.pendencias(store.negocio())
    linhas = [f"Cozinha dela (menu Minha cozinha; o que ela marca lá ou conta aqui fica salvo): {cozinha.texto_do_perfil(perfil)}"]
    if not pend:
        linhas.append("Cozinha completa: não pergunte de novo o que está salvo. Ao sugerir ou montar um prato, confira com o que ela tem e sabe; "
                      "se um prato ou ingrediente não cabe na cozinha dela, avise e diga por quê (o campo alerta_da_cozinha das tools já traz a frase).")
    if cadastro_guiado:
        if cadastro_enviado:
            nome = "do negócio" if cadastro_enviado == "negocio" else "da cozinha"
            linhas.append(f"O formulário {nome} já gravou no banco as respostas enviadas. Confirme o recebimento em uma frase. "
                          "Não grave as mesmas respostas de novo nem reabra esse formulário nesta resposta.")
        tipo = ("negocio" if pend_negocio and cadastro_enviado != "negocio" else
                "cozinha" if pend and not pend_negocio and cadastro_enviado != "cozinha" else None)
        if tipo:
            nome = "do negócio" if tipo == "negocio" else "da cozinha"
            linhas.append(f"O cadastro {nome} tem campos em aberto. No chat, convide a pessoa em uma frase a marcar os dados "
                          f"e escreva [[cadastro:{tipo}]] sozinho numa linha para abrir o formulário clicável. "
                          "Faça o convite na abertura, depois de concluir a ficha anterior, quando a pessoa quiser completar o cadastro "
                          "ou quando os dados faltarem para seguir com o prato. Responda normalmente às outras perguntas. "
                          "Não liste as perguntas do cadastro em texto. Se ela já contou algum dado na mensagem, salve-o com "
                          "negocio_salvar ou perfil_cozinha_salvar antes de convidar.")
        if pend:
            linhas.append("Antes de aceitar um prato, confira se a cozinha está completa.")
        return linhas
    if not pend and not pend_negocio:
        return linhas
    falta = ("NEGÓCIO E COZINHA INCOMPLETOS" if pend and pend_negocio else "COZINHA INCOMPLETA" if pend else "NEGÓCIO INCOMPLETO")
    salvar = ("o negócio (onde fica a loja, tipo de negócio, tipo de alimentação) com negocio_salvar e a cozinha com perfil_cozinha_salvar"
              if pend and pend_negocio else "a cozinha com perfil_cozinha_salvar" if pend else
              "o negócio (onde fica a loja, tipo de negócio, tipo de alimentação) com negocio_salvar")
    antes = " Nenhum prato é aceito antes de a cozinha estar completa." if pend else ""
    if primeira_mensagem:
        linhas.append(f"{falta} E ESTA É A PRIMEIRA MENSAGEM DA CONVERSA: cumprimente, diga em uma frase o que você faz, responda o que ela pediu "
                      "(se pediu algo) e faça JÁ NESTA RESPOSTA as perguntas abaixo, em texto, copiando como estão (já só têm o que falta). "
                      f"Se a mensagem dela já responde alguma, salve ({salvar}) e pergunte só o resto.{antes}")
    else:
        total = len(pend) + len(pend_negocio)
        linhas.append(f"{falta} ({total} perguntas sem resposta): releia a última mensagem dela: se ela já falou do que falta, salve com {salvar} "
                      "(técnicas em 'tecnicas': nome → sim, nao ou aprendendo) e só pergunte o que sobrar, copiando as perguntas abaixo "
                      "sem repetir o que ela já respondeu." + ("" if not pend else " Antes de aceitar qualquer prato, a cozinha precisa estar completa."))
    linhas.append("PERGUNTAS PARA FAZER (copie):\n" + cozinha.perguntas_iniciais(pend_negocio, pend))
    return linhas


def texto_do_painel(store: Optional[Store] = None, primeira_mensagem: bool = False, mensagem: Any = None,
                    escolha: Optional[str] = None, cadastro_guiado: bool = False, cadastro_enviado: Optional[str] = None) -> str:
    store = store or Store()
    resumo = store.carrinho_resumo()
    partes = [f"orçamento {costing.brl(resumo['inicial'])}"]
    if resumo["gasto_confirmado"]:
        partes.append(f"já pago {costing.brl(resumo['gasto_confirmado'])}")
    if resumo["reservado_cardapio"]:
        partes.append(f"reservado para compras de pratos {costing.brl(resumo['reservado_cardapio'])}")
    partes.append(f"no carrinho {costing.brl(resumo['carrinho'])}")
    partes.append(f"saldo {costing.brl(resumo['saldo'])}")
    neg = store.negocio()
    linhas = [
        f"{MARCA_PAINEL} — dados atuais, não são fala dela]",
        "Saldo: " + " · ".join(partes) + ".",
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
    if isinstance(mensagem, str) and compras.pediu_registro(mensagem):
        linhas.append("Ela pediu entrada no estoque. Chame despensa_registrar_compras: use o receita_id da lista de compras que você mostrou, "
                      "ou os itens com quantidade e preço que ela informou agora. Copie o saldo devolvido. "
                      "Se faltarem os dados ou a confirmação da lista, peça só o que falta.")
    roteiro = pesquisa.roteiro_do_turno(mensagem)
    if roteiro:
        linhas.append(roteiro)
        linhas.extend(guias.pesquisa(neg, mensagem))
    guia_preco = guias.precificacao()
    if guia_preco:
        linhas.append(guia_preco)
    linhas += [
        "Use isto ao sugerir pratos e ingredientes e ao falar do carrinho. Se ela pedir um item que já está no carrinho, avise. "
        "Use somente os valores das ferramentas. O custo da receita inclui a parte usada da despensa. "
        "Já pago, reservado, no carrinho e estimativa de compras são valores distintos. "
        "Se uma ferramenta bloquear a aprovação, copie diga_a_dona_maria e aguarde a escolha sobre a troca antes de mudar o prato.",
        "[fim do painel]",
    ]
    return "\n".join(linhas)


def ao_iniciar_turno(is_first_turn: bool = False, user_message: Any = None, session_id: str = "",
                    conversation_history: Optional[list] = None, **_: Any) -> Optional[dict[str, str]]:
    """Hook ``pre_llm_call``: devolve o painel como contexto do turno (com o roteiro da pesquisa de receitas quando ela pede receita ou ideia,
    e o turno marcado para as ferramentas levarem o próximo passo). Nunca derruba o turno."""
    try:
        store = Store()
        cadastro = _CADASTRO_CHAT.search(user_message) if isinstance(user_message, str) else None
        if cadastro:
            user_message = _CADASTRO_CHAT.sub("", user_message).strip()
        escolha = None
        if isinstance(user_message, str) and session_id:
            falas = extrair(conversation_history or [], user_message)
            fala = falas[-1] if falas else None
            if fala:
                escolha = (escolhas.preparar_turno(store, fala.texto, escolhas.lista_da_escolha(falas))
                           or registrar_troca_confirmada(store, fala.texto, fala.resposta_a)
                           or avancar_cardapio_no_turno(store, session_id, falas))
                if not escolha and escolhas.pedido_de_escolha(fala.texto):
                    escolha = ("Ela quer guardar escolhas. Se os nomes estiverem claros na fala, chame pratos_selecionar. "
                               "Se não estiverem claros, peça os nomes ou números. Não volte à pesquisa nem escolha no lugar dela.")
        pesquisa.marcar_turno(session_id, None if escolha else user_message)
        return {"context": texto_do_painel(store=store, primeira_mensagem=bool(is_first_turn), mensagem=None if escolha else user_message,
                                            escolha=escolha, cadastro_guiado=cadastro is not None,
                                            cadastro_enviado=cadastro[1] if cadastro else None)}
    except Exception:  # noqa: BLE001 - sem o painel a consultora ainda tem as tools
        logger.exception("painel: não consegui montar o resumo do carrinho")
        return None
