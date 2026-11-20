"""Depois das sugestões a tela oferece três respostas; cada uma leva a conversa adiante em vez de repetir a busca.

Mostrar opções não grava nada, então a jornada continua em "pesquisa" até a pessoa escolher. Sem uma resposta que
feche esse passo, a tela voltava a oferecer "ver sugestões", cada clique fazia outra busca e ninguém chegava ao prato.
"""

import json
import re
import shutil
import subprocess

from sabor_da_maria import cozinha, escolhas, falas, jornada, montagem, negocio, painel, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import REPO, SESSAO, StoreTestCase

# O que os botões do playground mandam à consultora (lib/prompts.ts e conducao-chat.tsx).
PEDIDO = ("Quero sua ajuda. Pesquise receitas reais para o meu negócio com o estoque disponível. "
          "Apresente poucas opções com fonte e custos, para eu escolher antes de montar a ficha.")
GOSTEI = "Escolho a opção {numero}."
NOVA_BUSCA = ("Quero sua ajuda. Pesquise outras receitas reais para o meu negócio com o estoque disponível, diferentes das que você já mostrou. "
              "Apresente poucas opções com fonte e custos, para eu escolher antes de montar a ficha.")
PRATO_PROPRIO = "Quero montar meu próprio prato. Pergunte o que tenho em mente."

URL_PIZZA = "https://www.tudogostoso.com.br/receita/11041-pizza-de-arroz.html"
URL_SOBRA = "https://receitas.globo.com/tipos-de-prato/pizzas/pizza-de-sobra-de-arroz.ghtml"
URL_XONGANI = "https://receitas.globo.com/tipos-de-prato/pizzas/pizza-de-arroz-da-ana-paula-xongani.ghtml"
URL_GALINHADA = "https://receitas.exemplo/galinhada-de-panela"

# A lista conferida pelo plugin, como saiu na conversa em que o defeito apareceu.
UMA = ("Estas são as receitas que consegui ler e verificar. Você escolhe antes de montar a ficha.\n\n"
       f"### 1. Pizza de arroz\nFonte: [Pizza de arroz]({URL_PIZZA})\n"
       "Ingredientes publicados: 2 copos americano de farinha de trigo; 2 copos americano de arroz cozido; 1 ovo.\n"
       "Precisa conferir: as medidas, a disponibilidade para este lote e o custo completo, depois da sua escolha.\n\n"
       "Qual opção você quer escolher? Pode escolher mais de uma ou pedir outras sugestões.")
# A lista escrita pelo modelo na mesma conversa, com quebras de linha em markdown e separadores.
DUAS = ("\n\n### 1. Pizza de sobra de arroz  \n"
        f"Fonte: [Pizza de sobra de arroz]({URL_SOBRA})  \n"
        "Ingredientes publicados: 2 xícaras (de chá) de sobras de arroz; 2 ovos.  \n\n"
        "Já tem:  \n- Arroz branco tipo 1 (5 kg)  \n- Ovos (30 un)  \n\n"
        "Precisa conferir ou comprar:  \n- Fermento em pó (nada parecido na despensa)  \n\n---\n\n"
        "### 2. Pizza de arroz da Ana Paula Xongani  \n"
        f"Fonte: [Transforme o arroz que sobrou em uma pizza deliciosa com a Ana Paula Xongani]({URL_XONGANI})  \n"
        "Ingredientes publicados: 400 gramas de arroz; 2 ovos; molho de tomate.  \n\n"
        "Já tem:  \n- Arroz branco tipo 1 (5 kg)  \n\n"
        "Precisa conferir ou comprar:  \n- Molho de tomate (nada parecido na despensa)  \n\n---\n\n"
        "Qual opção você quer escolher? Pode escolher mais de uma ou pedir outras sugestões.")


def pagina(nome, url):
    return {"url": url, "title": nome, "metadata": {"recipe_schema": True},
            "receitas": [{"nome": nome, "url": url, "porcoes": 4, "ingredientes": ["500 g de peito de frango", "2 xícaras de arroz"],
                          "modo_preparo": "Refogue o frango e cozinhe com o arroz em uma panela."}]}


class DecisaoDasSugestoesTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["pizza"], "estado": "DF", "cidade": "Brasília"})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")

    def turno(self, mensagem, turno, *respostas_anteriores):
        historico = []
        for i, resposta in enumerate(respostas_anteriores):
            historico += [{"role": "user", "content": PEDIDO if i == 0 else NOVA_BUSCA}, {"role": "assistant", "content": resposta}]
        falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=mensagem)
        retorno = painel.ao_iniciar_turno(session_id=SESSAO, turn_id=turno, conversation_history=historico, user_message=mensagem)
        self.assertIsNotNone(retorno)
        return retorno["context"]

    def ferramenta(self, nome, dados, args=None):
        return json.loads(pesquisa.depois_da_ferramenta(tool_name=nome, args=args, result=json.dumps(dados), session_id=SESSAO))

    def nada_comprometido(self):
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.analises_preco(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_gostei_grava_so_a_opcao_apontada_e_tira_a_jornada_da_pesquisa(self):
        self.assertEqual(jornada.resumo(self.store)["etapa"], "pesquisa")
        self.turno(GOSTEI.format(numero=2), "gostei", DUAS)
        prato, = self.store.pratos_em_andamento()
        self.assertEqual((prato["nome"], prato["status"], prato["fonte_url"]),
                         ("Pizza de arroz da Ana Paula Xongani", "selecionado", URL_XONGANI))
        # A escolha delega a leitura da fonte e a montagem dessa ficha; aceite, compra e preço continuam com ela.
        self.assertFalse(pesquisa.somente_sugestoes(SESSAO))
        self.assertEqual(pesquisa.alvo_escolhido(SESSAO)["fonte_url"], URL_XONGANI)
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "web_extract")
        # Com o rascunho salvo a jornada passa a acompanhar esse prato: a tela deixa de oferecer a busca.
        atual = jornada.resumo(self.store)
        self.assertEqual((atual["passo"], atual["prato_id"], atual["pratos_ativos"]), ("montagem", prato["id"], [prato["id"]]))
        # A escolha ainda não tem ficha. Não é uma versão viável esperando aceite: falta ler a receita.
        self.assertEqual((atual["etapa"], [p["tipo"] for p in atual["pendencias"]]), ("pendencias", ["receita_pendente"]))
        self.assertIsNone(atual["pergunta"])
        self.nada_comprometido()

    def test_gostei_com_uma_opcao_so_tambem_usa_o_numero_da_lista(self):
        self.turno(GOSTEI.format(numero=1), "gostei-unica", UMA)
        self.assertEqual([(p["nome"], p["fonte_url"]) for p in self.store.pratos_em_andamento()], [("Pizza de arroz", URL_PIZZA)])
        self.nada_comprometido()

    def test_nova_busca_e_conduzida_e_so_traz_receita_que_ela_ainda_nao_viu(self):
        contexto = self.turno(NOVA_BUSCA, "nova-busca", UMA)
        self.assertTrue(pesquisa.somente_sugestoes(SESSAO))
        self.assertIn("PESQUISA DE RECEITAS", contexto)
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "despensa_listar")

        despensa = self.ferramenta("despensa_listar", {"success": True})
        self.assertIn("web_search AGORA", despensa["proximo_passo"])
        self.assertIn("Pizza de arroz", despensa["proximo_passo"])
        self.assertIn("OUTRO prato", despensa["proximo_passo"])

        # A mesma página volta na busca sem "www." e com barra no fim: continua sendo a receita que ela já viu.
        busca = self.ferramenta("web_search", {"success": True, "data": {"web": [
            {"title": "Pizza de arroz", "url": "https://tudogostoso.com.br/receita/11041-pizza-de-arroz.html/"},
            {"title": "Galinhada de panela", "url": URL_GALINHADA}]}})
        self.assertEqual([r["url"] for r in busca["data"]["web"]], [URL_GALINHADA])
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "web_extract")

        leitura = self.ferramenta("web_extract", {"results": [
            pagina("Pizza de arroz", URL_PIZZA),
            pagina("Pizza de Arroz", "https://outro.exemplo/pizza-de-arroz"),  # o mesmo prato em outro site
            pagina("Galinhada de panela", URL_GALINHADA)]})
        self.assertEqual([p["url"] for p in leitura["results"]], [URL_GALINHADA])
        self.assertNotIn("Pizza", leitura["proximo_passo"])
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "despensa_buscar")

        self.ferramenta("despensa_buscar", {"success": True, "resultados": []})
        resposta = montagem.resposta_preparada(SESSAO, "nova-busca")
        self.assertTrue(resposta.startswith("Procurei opções diferentes das que você já viu."))
        self.assertNotIn("Pizza", resposta)
        self.assertEqual(resposta.count("?"), 1)
        self.assertEqual(escolhas.opcoes_exibidas(resposta), [{"numero": 1, "nome": "Galinhada de panela", "fonte_url": URL_GALINHADA}])
        self.assertEqual(self.store.pratos(), [])
        self.nada_comprometido()

    def test_nova_busca_lembra_de_todas_as_listas_da_conversa(self):
        self.turno(NOVA_BUSCA, "terceira-busca", UMA, DUAS)
        despensa = self.ferramenta("despensa_listar", {"success": True})
        for nome in ("Pizza de arroz", "Pizza de sobra de arroz", "Pizza de arroz da Ana Paula Xongani"):
            self.assertIn(nome, despensa["proximo_passo"])
        leitura = self.ferramenta("web_extract", {"results": [pagina("Pizza de sobra de arroz", URL_SOBRA),
                                                              pagina("Galinhada de panela", URL_GALINHADA)]})
        self.assertEqual([p["url"] for p in leitura["results"]], [URL_GALINHADA])

    def test_nova_busca_sem_receita_inedita_avisa_em_vez_de_repetir(self):
        self.turno(NOVA_BUSCA, "sem-novidade", UMA)
        repetida = {"success": True, "data": {"web": [{"title": "Pizza de arroz", "url": URL_PIZZA}]}}
        primeira = self.ferramenta("web_search", repetida)
        self.assertEqual(primeira["data"]["web"], [])
        self.assertIn("OUTRO prato", primeira["proximo_passo"])
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "web_search")
        self.assertIsNone(montagem.resposta_preparada(SESSAO, "sem-novidade"))

        segunda = self.ferramenta("web_extract", {"results": [pagina("Pizza de arroz", URL_PIZZA)]})
        self.assertEqual(segunda["results"], [])
        self.assertIn("Não faça outra busca ou leitura", segunda["proximo_passo"])
        self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))
        resposta = montagem.resposta_preparada(SESSAO, "sem-novidade")
        self.assertIn("Não encontrei outra receita completa, diferente das que você já viu", resposta)
        self.assertEqual(resposta.count("?"), 1)
        # Sem lista na resposta, a tela sai dos botões de escolha em vez de reapresentar o que ela recusou.
        self.assertEqual(escolhas.opcoes_exibidas(resposta), [])
        self.assertEqual(self.store.pratos(), [])
        self.nada_comprometido()

    def test_primeira_busca_nao_descarta_nem_anuncia_outra_busca(self):
        self.turno(PEDIDO, "primeira")
        despensa = self.ferramenta("despensa_listar", {"success": True})
        self.assertTrue(despensa["proximo_passo"].startswith(pesquisa.PASSO_APOS_DESPENSA))
        self.assertIn("Tipos de alimentação do cadastro: pizzas", despensa["proximo_passo"])
        busca = self.ferramenta("web_search", {"success": True, "data": {"web": [{"title": "Pizza de arroz", "url": URL_PIZZA}]}})
        self.assertEqual([r["url"] for r in busca["data"]["web"]], [URL_PIZZA])
        leitura = self.ferramenta("web_extract", {"results": [pagina("Pizza de arroz", URL_PIZZA)]})
        self.assertEqual([p["url"] for p in leitura["results"]], [URL_PIZZA])
        self.ferramenta("despensa_buscar", {"success": True, "resultados": []})
        resposta = montagem.resposta_preparada(SESSAO, "primeira")
        self.assertTrue(resposta.startswith("Estas são as receitas que consegui ler e verificar."))
        self.assertEqual([o["nome"] for o in escolhas.opcoes_exibidas(resposta)], ["Pizza de arroz"])

    def test_objetivo_salvo_prioriza_consulta_e_ordena_as_fontes_verificadas(self):
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": list(negocio.ALIMENTACAO),
                                  "estado": "DF", "cidade": "Brasília", "objetivo": "começar vendendo pizza"})
        saida = self.turno("Quero ver sugestões de pratos com a IA.", "prioridade-pizza")
        self.assertIn("Prioridade desta busca: pizza.", saida)
        self.assertTrue(pesquisa.somente_sugestoes(SESSAO))

        despensa = self.ferramenta("despensa_listar", {"success": True})
        self.assertIn("Prioridade desta busca: pizza.", despensa["proximo_passo"])
        ajuste = pesquisa.ajustar_consulta("web_search", {"query": "frango com batata para marmita site:exemplo.com"}, SESSAO)
        self.assertEqual(ajuste, {"action": "modify", "args": {"query": "receita de pizza"}})
        aberta = pesquisa.ajustar_consulta("web_search", {"query": "receita de pizza com tomate site:exemplo.com"}, SESSAO)
        self.assertEqual(aberta["args"]["query"], "receita de pizza com tomate")

        leitura = self.ferramenta("web_extract", {"results": [
            pagina("Frango com batata", URL_GALINHADA), pagina("Pizza de arroz", URL_PIZZA)]})
        self.assertEqual([p["url"] for p in leitura["results"]], [URL_PIZZA, URL_GALINHADA])
        self.ferramenta("despensa_buscar", {"success": True, "resultados": []})
        resposta = montagem.resposta_preparada(SESSAO, "prioridade-pizza")
        self.assertLess(resposta.index("### 1. Pizza"), resposta.index("Ideias secundárias"))
        self.assertIn("### 2. Frango com batata", resposta)
        self.assertEqual(self.store.pratos(), [])
        self.nada_comprometido()

    def test_so_receitas_fora_do_objetivo_abrem_uma_nova_tentativa(self):
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["marmita", "pizza"],
                                  "estado": "DF", "cidade": "Brasília", "objetivo": "começar vendendo pizza"})
        self.turno(PEDIDO, "somente-fora-da-prioridade")
        fora = {"results": [pagina("Frango com batata", URL_GALINHADA)]}
        self.assertEqual(self.ferramenta("web_extract", fora)["results"], [])
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "web_search")
        self.assertEqual(self.ferramenta("web_extract", fora)["results"], [])
        resposta = montagem.resposta_preparada(SESSAO, "somente-fora-da-prioridade")
        self.assertIn("Busquei receitas de **pizza**", resposta)
        self.assertEqual(escolhas.opcoes_exibidas(resposta), [])

    def test_ficha_da_opcao_escolhida_le_a_fonte_que_ela_ja_viu(self):
        # O filtro vale para buscar sugestões. A receita escolhida precisa justamente da página já mostrada.
        self.turno(GOSTEI.format(numero=1), "ficha-da-escolhida", UMA)
        leitura = self.ferramenta("web_extract", {"results": [pagina("Pizza de arroz", URL_PIZZA)]})
        self.assertEqual([p["url"] for p in leitura["results"]], [URL_PIZZA])
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "receita_conferir_ingredientes")

    def test_gostei_e_vamos_usar_aplica_escolha_ao_conferir_a_versao_publicada(self):
        self.store.perfil_salvar({"equipamentos": {"fogao": 4}, "tecnicas": {"refogar": "sim"},
                                 "restricoes": {"tempo_por_cozinhada_min": 90, "gas": "botijão",
                                                "energia": "estável", "espaco_geladeira": "bastante"}})
        nome = "Frango e arroz"
        anterior = UMA.replace("Pizza de arroz", nome).replace(URL_PIZZA, URL_GALINHADA)
        self.turno(f"Gostei de «{nome}», vamos usá-lo.", "escolha-para-usar", anterior)
        prato, = self.store.pratos()
        self.assertTrue(prato["dados"]["selecao"]["usar_quando_viavel"])
        self.assertEqual(self.store.reservas_ativas(), [])
        receita = pagina(nome, URL_GALINHADA)
        receita["receitas"][0]["tempo_preparo_min"] = 30
        self.ferramenta("web_extract", {"results": [receita]})

        resultado = json.loads(tools.receita_conferir_ingredientes({}, session_id=SESSAO))

        self.assertEqual(resultado["veredito"], "viavel", resultado)
        self.assertIn("Registrei o lote no histórico", montagem.resposta_preparada(SESSAO, "escolha-para-usar"))
        self.assertEqual(self.store.obter_prato(prato["id"])["status"], "cmv_calculado")
        self.assertEqual(len(self.store.carrinho_itens()), 2)
        self.assertEqual(len(self.store.reservas_ativas()), 2)
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def escolher_marmita_com_quiabo(self):
        """Sugestões e "Gostei" de uma receita real: quiabo não está na despensa e dois itens vêm sem medida."""
        url = "https://www.clickreceita.com.br/receita/marmita-carne-moida-com-quiabo.html"
        receita = {"url": url, "title": "Marmita de Carne Moída com Quiabo", "metadata": {"recipe_schema": True}, "receitas": [{
            "nome": "Marmita de Carne Moída com Quiabo", "url": url, "porcoes": 5, "tempo_preparo_min": 38.0,
            "ingredientes": ["600 g de carne moída", "500 g de quiabo fresco em rodelas", "1 cebola picada",
                             "Sal e pimenta a gosto", "Salsinha fresca", "2 xícaras de arroz branco cozido"],
            "modo_preparo": "Refogue a cebola, doure a carne moída e cozinhe o quiabo por 15 minutos."}]}
        self.store.cozinha_salvar({
            "equipamentos": {i.id: (6 if i.id == "fogao" else True) for i in cozinha.CATALOGO if i.grupo != cozinha.HABILIDADE},
            "habilidades": {i.id: "sim" for i in cozinha.CATALOGO if i.grupo == cozinha.HABILIDADE},
            "rotina": {"espaco_geladeira": "bastante", "tempo_por_cozinhada_min": 120, "gas": "botijão", "energia": "estável"}})
        self.turno(PEDIDO, "sugestoes")
        self.ferramenta("despensa_listar", {"success": True})
        self.ferramenta("web_search", {"success": True, "data": {"web": [{"title": "Marmita", "url": url}]}})
        self.ferramenta("web_extract", {"results": [receita]})
        self.ferramenta("despensa_buscar", {"success": True, "resultados": []})
        lista = montagem.resposta_preparada(SESSAO, "sugestoes")

        contexto = self.turno(GOSTEI.format(numero=1), "gostei", lista)
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "receita_conferir_ingredientes")  # a página já foi lida
        ficha = json.loads(tools.receita_conferir_ingredientes(
            {"ingredientes": [{"nome": "carne moída", "quantidade": 600, "unidade": "g"}]}, session_id=SESSAO))
        self.assertTrue(ficha["ficha_original_da_fonte"], ficha)
        texto = montagem.completar_resposta(response_text="rascunho do modelo", session_id=SESSAO, turn_id="gostei")
        return ficha, texto, contexto, lista

    def test_ficha_depois_do_gostei_fala_com_ela_sem_texto_escrito_para_o_modelo(self):
        ficha, texto, contexto, lista = self.escolher_marmita_com_quiabo()

        internos = re.compile(r"preco_compra_estimado|quantidade_compra|unidade_compra|web_search|despensa_\w+|receita_conferir\w*|"
                              r"medida não estruturada|medida de uso proposta|Dona Maria|\bNone\b")
        self.assertNotRegex(texto, internos)
        self.assertNotRegex(texto, r"\d\.0\b")  # 600.0 g, 38.0 min
        for trecho in ("600 g de carne moida", "Rendimento proposto: 5 porções.", "Preparo (cerca de 38 min):",
                       "A receita não traz a quantidade de: Sal e pimenta a gosto; Salsinha fresca. Vou propor essas medidas para você conferir.",
                       "Mantive quiabo fresco em rodelas na receita e anotei a falta na ficha. "
                       "Quer que eu pesquise o preço para completar o prato?"):
            self.assertIn(trecho, texto)
        self.assertEqual(texto.count("?"), 1)
        # O cartão e o painel lateral mostram a mesma pendência, nas mesmas palavras.
        atual = jornada.resumo(self.store)
        self.assertEqual((atual["etapa"], atual["descricao"]),
                         ("pendencias", "quiabo fresco em rodelas: não está na despensa e ainda não tem preço de compra"))
        # Quem resolve é a consultora: a orientação com os campos vai no resultado da ferramenta e no painel dela.
        self.assertIn("preco_compra_estimado, quantidade_compra e unidade_compra", ficha["proximo_passo"])
        self.assertNotIn("Faltantes sem preço", contexto)  # antes da ficha existir não há faltante conferido
        prato, = self.store.pratos_em_andamento()
        retomada = self.turno(f"Quero continuar o prato escolhido #{prato['id']} ({prato['nome']}).", "retomada", lista)
        self.assertIn("Faltantes sem preço: quiabo fresco em rodelas.", retomada)
        self.assertIn("Proponha uma versão com o estoque", retomada)
        self.assertIn("Se ela preferir manter o complemento", retomada)
        self.assertIn("Não cite nomes de campo para ela.", retomada)
        # Cotado o preço, o resultado da busca manda reconferir a ficha, não mexer na despensa.
        cotacao = self.ferramenta("web_search", {"success": True, "data": {"web": [{"title": "Quiabo kg", "url": "https://mercado.exemplo/quiabo"}]}},
                                  args={"query": "preço quiabo São Paulo"})
        self.assertIn(f"receita_conferir_ingredientes(prato_id={prato['id']})", cotacao["proximo_passo"])
        self.assertIn("quiabo fresco em rodelas", cotacao["proximo_passo"])
        self.assertIn("não registre compra nem altere a despensa", cotacao["proximo_passo"])
        self.nada_comprometido()

    def test_cotacao_de_um_complemento_mantem_a_ficha_copiada_da_fonte(self):
        ficha, _, _, lista = self.escolher_marmita_com_quiabo()
        prato_id, publicados = ficha["prato_id"], 6

        def cotar(turno, fala, ingredientes):
            self.turno(fala, turno, lista)
            resultado = json.loads(tools.receita_conferir_ingredientes({"prato_id": prato_id, "ingredientes": ingredientes}, session_id=SESSAO))
            return resultado, montagem.completar_resposta(response_text="rascunho do modelo", session_id=SESSAO, turn_id=turno)

        # O modelo pequeno reenvia só o que cotou, e ainda mexe na medida. A ficha continua sendo a da fonte.
        quiabo = {"nome": "quiabo fresco", "quantidade": 1, "unidade": "kg",
                  "preco_compra_estimado": 6.18, "quantidade_compra": 1, "unidade_compra": "kg"}
        cotada, texto = cotar("cotacao", "Sim, vamos resolver esse ponto.", [quiabo])
        self.assertTrue(cotada["ficha_original_da_fonte"], cotada)
        self.assertEqual(len(cotada["ingredientes"]), publicados)
        self.assertEqual(cotada["fidelidade_fonte"]["diferencas"], [])
        self.assertNotIn("fonte_incompleta", {b["tipo"] for b in cotada["bloqueios"]})
        self.assertEqual([(c["ingrediente"], c["preco_estimado"]) for c in cotada["resumo"]["precisa_comprar"]],
                         [("quiabo fresco em rodelas", 6.18)])
        self.assertNotIn("quiabo", " ".join(cotada["pendencias"]))
        salvos = self.store.obter_receita(cotada["receita_id"])["dados"]["ingredientes"]
        self.assertEqual((salvos[1]["nome"], salvos[1]["quantidade"], salvos[1]["unidade"]), ("quiabo fresco em rodelas", 500, "g"))
        for trecho in ("600 g de carne moida", "500 g de quiabo fresco em rodelas", "Os ingredientes da fonte estão representados"):
            self.assertIn(trecho, texto)
        self.assertNotIn("quiabo fresco em rodelas: não está na despensa", texto)
        self.assertNotIn("Diferenças da fonte", texto)
        self.assertEqual(texto.count("?"), 1)

        # Uma segunda conferência sem cotação nova não apaga o preço já cotado.
        outra, _ = cotar("outra", "Pode continuar.", [{"nome": "arroz branco cozido", "quantidade": 2, "unidade": "xicaras",
                                                      "preco_compra_estimado": 5, "quantidade_compra": 1, "unidade_compra": "kg"}])
        self.assertEqual(len(outra["ingredientes"]), publicados)
        self.assertIn(("quiabo fresco em rodelas", 6.18), [(c["ingrediente"], c["preco_estimado"]) for c in outra["resumo"]["precisa_comprar"]])

        # Se ela pede para mudar a receita, vale o que foi enviado, conferido contra a fonte como sempre.
        mudada, _ = cotar("mudanca", "Troque o quiabo por abobrinha e veja o preço.", [
            {"nome": "abobrinha", "quantidade": 500, "unidade": "g", "preco_compra_estimado": 4, "quantidade_compra": 1, "unidade_compra": "kg"}])
        self.assertFalse(mudada["ficha_original_da_fonte"])
        self.assertEqual(mudada["bloqueios"][0]["tipo"], "preparo_adaptacao_pendente")
        self.assertIn("Adaptação local solicitada", mudada["fidelidade_fonte"]["adaptacao"])
        self.nada_comprometido()

    def test_prato_proprio_pergunta_a_ideia_sem_pesquisar_nem_gravar(self):
        jornada.responder(self.store, "prato_proprio")  # o botão grava a escolha pela API antes de falar com a consultora
        self.turno(PRATO_PROPRIO, "proprio", UMA)
        resposta = montagem.resposta_preparada(SESSAO, "proprio")
        self.assertEqual(resposta, "Vamos montar do seu jeito. Qual prato você tem em mente, ou qual ingrediente quer usar?")
        self.assertFalse(pesquisa.pesquisando(SESSAO))
        self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))
        self.assertEqual(jornada.resumo(self.store)["etapa"], "proprio")
        self.assertEqual(self.store.pratos(), [])
        self.nada_comprometido()


class ContratoComATelaTests(StoreTestCase):
    """A tela decide quando mostrar os botões lendo a lista do chat; o plugin resolve a escolha lendo a mesma lista."""

    TELA = REPO / "playground" / "src"

    def setUp(self):
        super().setUp()
        if not (self.TELA / "lib" / "sugestoes.ts").exists():
            self.skipTest("playground fora deste ambiente")

    def test_frases_dos_botoes_sao_as_que_o_plugin_reconhece(self):
        prompts = (self.TELA / "lib" / "prompts.ts").read_text(encoding="utf-8")
        for frase in (PEDIDO, NOVA_BUSCA, PRATO_PROPRIO):
            self.assertIn(json.dumps(frase, ensure_ascii=False), prompts)
        self.assertIn("`Quero usar a opção ${numero}.`", (self.TELA / "conducao-chat.tsx").read_text(encoding="utf-8"))

    def test_tela_e_plugin_leem_as_mesmas_opcoes(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node não está instalado")
        textos = [UMA, DUAS, "", "Você prefere pizza ou galinhada?",
                  "### Próximos passos\n1. Escolha o que prefere.\n2. Confirme o rendimento.",
                  "### ✅ **1. Filé grelhado**\n- **Fonte:** [Receita](https://example.com/file)\n- **Já tem na despensa:** arroz\n\n"
                  "### ✅ **2. [Arroz de forno](https://example.com/arroz)**\n* **Fonte:** [Receita](https://example.com/arroz)\n* **Faltando:** requeijão",
                  "### 2. Só a segunda sobrou\nFonte: [Receita](https://example.com/segunda)\nJá tem: arroz",
                  "### 1. Sem fonte\nJá tem: arroz\n\n### 2. Sem ingredientes\nFonte: [Receita](https://example.com/b)"]
        script = (f"import {{ opcoesExibidas }} from {json.dumps(str(self.TELA / 'lib' / 'sugestoes.ts'))};\n"
                  "let entrada = ''; for await (const parte of process.stdin) entrada += parte;\n"
                  "console.log(JSON.stringify(JSON.parse(entrada).map(opcoesExibidas)));")
        saida = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(textos),
                               capture_output=True, text=True, timeout=60)
        if saida.returncode and "ERR_UNKNOWN_FILE_EXTENSION" in saida.stderr:
            self.skipTest("este node não executa TypeScript diretamente")
        self.assertEqual(saida.returncode, 0, saida.stderr)
        do_plugin = [[{"numero": o["numero"], "nome": o["nome"]} for o in escolhas.opcoes_exibidas(texto)] for texto in textos]
        self.assertEqual(json.loads(saida.stdout), do_plugin)
        self.assertEqual([len(opcoes) for opcoes in do_plugin], [1, 2, 0, 0, 0, 2, 0, 0])
