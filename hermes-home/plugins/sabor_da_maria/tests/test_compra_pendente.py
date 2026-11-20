"""Uma cotação com unidade inválida não pode perder a ficha já montada.

A pendência diz o problema em palavras para a Dona Maria: o mesmo texto vai para a ficha, o cartão do chat e o aviso
de "não aprovado". O que o modelo deve fazer (ferramenta, campos) fica nos próximos passos e em ``como_resolver``.
"""

import json
import re
from copy import deepcopy

from sabor_da_maria import cozinha, falas, jornada, montagem, painel, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


# Nomes de ferramenta e de campo, e instruções escritas para o modelo: nada disso é fala para ela.
INTERNOS = re.compile(r"preco_compra_estimado|quantidade_compra|unidade_compra|conteudo_por_unidade|web_search|despensa_\w+|"
                      r"receita_conferir\w*|\binforme\b|\bpesquise\b|\bestime\b|\bsepare\b|Dona Maria")


class CompraPendenteTests(StoreTestCase):
    def conferir(self, ingrediente):
        dados = {"nome": "Prato em conferência", "porcoes": 4, "tempo_preparo_min": 30,
                 "ingredientes": [{**ingrediente, "preco_compra_estimado": 5,
                                   "quantidade_compra": 1, "unidade_compra": "colher de sopa"}]}
        resultado = json.loads(tools.receita_conferir_ingredientes(dados, session_id=SESSAO))
        self.assertTrue(resultado["success"], resultado)
        self.assertTrue(any("tamanho da embalagem" in p for p in resultado["pendencias"]), resultado)
        # O nome do campo orienta o modelo; não entra no texto que a Dona Maria lê.
        self.assertTrue(any("unidade_compra" in p for p in resultado["proximos_passos"]), resultado)
        self.assertFalse(any("unidade_compra" in texto for texto in
                             [*resultado["pendencias"], *(b["detalhe"] for b in resultado["bloqueios"])]), resultado)
        self.assertIsNotNone(Store().obter_receita(resultado["receita_id"]))
        self.assertIsNone(resultado["resumo"]["cmv_previa"])
        self.assertEqual(Store().reservas_ativas(), [])

    def test_item_da_despensa_com_falta_preserva_ficha(self):
        self.conferir({"nome": "Arroz branco tipo 1", "quantidade": 6, "unidade": "kg"})

    def test_item_ausente_preserva_ficha(self):
        self.conferir({"nome": "Maionese", "quantidade": 100, "unidade": "g"})

    def test_ingrediente_sem_preco_diz_o_problema_a_ela_e_o_que_fazer_so_ao_modelo(self):
        resultado = json.loads(tools.receita_conferir_ingredientes({
            "nome": "Carne moída com quiabo", "porcoes": 4, "tempo_preparo_min": 30, "ingredientes": [
                {"nome": "quiabo fresco", "quantidade": 500, "unidade": "g"},                # não está na despensa
                {"nome": "Arroz branco tipo 1", "quantidade": 6, "unidade": "kg"},           # tem 5 kg, com preço na planilha
                {"nome": "cobertura de chocolate", "quantidade": 200, "unidade": "g"},       # vendida por unidade, sem peso
                {"nome": "sal e pimenta a gosto", "quantidade": 5, "unidade": "g"},
                {"nome": "salsinha", "quantidade": 0, "unidade": "g"}]}, session_id=SESSAO))
        self.assertTrue(resultado["success"], resultado)
        self.assertIn("quiabo fresco: não está na despensa e ainda não tem preço de compra", resultado["pendencias"])
        self.assertIn("salsinha: a receita não traz a quantidade", resultado["pendencias"])
        self.assertEqual(len(resultado["pendencias"]), 4)
        para_ela = [*resultado["pendencias"], *(b["detalhe"] for b in resultado["bloqueios"] if b["tipo"] == "ingrediente_pendente"),
                    *(i["pendencia"] for i in resultado["ingredientes"] if i.get("pendencia")), resultado["diga_a_dona_maria"]]
        for texto in para_ela:
            self.assertNotRegex(texto, INTERNOS)
        # O modelo continua sabendo o que fazer, no campo que ele segue e ingrediente por ingrediente.
        self.assertIn("Falta preço de compra de: quiabo fresco.", resultado["proximo_passo"])
        self.assertIn("preco_compra_estimado, quantidade_compra e unidade_compra", resultado["proximo_passo"])
        passos = "\n".join(resultado["proximos_passos"])
        for trecho in ("quiabo fresco: pesquise o preço de mercado (web_search) ou pergunte a ela", "despensa_atualizar(conteudo_por_unidade)",
                       "sal e pimenta a gosto: separe as medidas", "salsinha: proponha uma medida em gramas ou mililitros"):
            self.assertIn(trecho, passos)
        itens = {i["ingrediente"]: i for i in resultado["ingredientes"]}
        self.assertTrue(itens["quiabo fresco"]["falta_preco"])
        self.assertNotIn("falta_preco", itens["Arroz branco tipo 1"])  # o que falta do arroz é estimado pelo preço da planilha
        self.assertNotIn("como_resolver", itens["Arroz branco tipo 1"])
        self.assertEqual(Store().reservas_ativas(), [])
        self.assertEqual(Store().orcamento()["saldo"], 80)


class AnotacoesFaltantesTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.cozinha_salvar({
            "equipamentos": {i.id: 6 if i.id == "fogao" else True for i in cozinha.CATALOGO if i.grupo != cozinha.HABILIDADE},
            "habilidades": {i.id: "sim" for i in cozinha.CATALOGO if i.grupo == cozinha.HABILIDADE},
            "rotina": {"tempo_por_cozinhada_min": 120, "gas": "botijão", "energia": "estável", "espaco_geladeira": "bastante"}})
        self.prato, = self.store.selecionar_pratos([{"nome": "Pizza de frango"}])
        self.ingredientes = [{"nome": "Farinha de trigo", "quantidade": 500, "unidade": "g"},
                             {"nome": "fermento biológico", "quantidade": 5, "unidade": "g"},
                             {"nome": "água", "quantidade": 250, "unidade": "ml"}]
        self.fonte = {"nome": "Pizza de frango", "url": "https://receitas.exemplo/pizza-de-frango", "porcoes": 4,
                      "tempo_preparo_min": 60, "ingredientes": ["500 g de farinha de trigo", "5 g de fermento biológico", "250 ml de água"],
                      "modo_preparo": "Misture a massa, deixe fermentar e asse no forno."}

    def guardar(self, ingredientes=None, **extras):
        ingredientes = deepcopy(self.ingredientes if ingredientes is None else ingredientes)
        dados = {"ingredientes": ingredientes, "resultado": tools._resolver_ingredientes(self.store, ingredientes),
                 "fonte_verificada": deepcopy(self.fonte), "tempo_preparo_min": 60,
                 "modo_preparo": self.fonte["modo_preparo"], **extras}
        receita_id = self.store.salvar_receita("Pizza de frango", self.fonte["url"], 4, dados, "conferida", prato_id=self.prato["id"])
        return self.store.obter_receita(receita_id)

    def sem_compromissos(self):
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.carrinho_itens(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)
        self.assertEqual(self.store.obter_prato(self.prato["id"])["status"], "selecionado")

    def test_faltante_persistido_em_sqlite_sem_apagar_fonte_ou_medidas(self):
        receita = self.guardar(medidas_de_uso_confirmadas={"alho": {"quantidade": 10, "unidade": "g"}})
        nota, = receita["dados"]["ingredientes_faltantes"]
        self.assertEqual((nota["ingrediente"], nota["estado"], nota["sem_preco"]), ("fermento biológico", "faltando", True))
        self.assertEqual(nota["quantidade_receita"], "5 g")
        reaberta = Store().obter_receita(receita["id"])
        self.assertEqual(reaberta, receita)
        self.assertEqual(reaberta["dados"]["fonte_verificada"], self.fonte)
        self.assertEqual(reaberta["dados"]["ingredientes"], self.ingredientes)
        self.assertIn("alho", reaberta["dados"]["medidas_de_uso_confirmadas"])
        self.sem_compromissos()

    def test_tool_avisa_e_salva_ficha_mesmo_sem_preco(self):
        resultado = json.loads(tools.receita_conferir_ingredientes({"prato_id": self.prato["id"], "porcoes": 4,
                              "tempo_preparo_min": 60, "ingredientes": self.ingredientes}, session_id=SESSAO))
        self.assertTrue(resultado["success"], resultado)
        self.assertTrue(resultado["rascunho_salvo"])
        self.assertIsNone(resultado["resumo"]["cmv_previa"])
        self.assertIn("fermento biológico: não cadastrado na despensa", resultado["diga_a_dona_maria"])
        self.assertIn("observações", resultado["diga_a_dona_maria"].lower())
        self.assertEqual(resultado["ingredientes_faltantes"], Store().obter_receita(resultado["receita_id"])["dados"]["ingredientes_faltantes"])
        self.sem_compromissos()

    def test_ausente_sem_medida_tambem_fica_anotado(self):
        resultado = tools._resolver_ingredientes(self.store, [{"nome": "fermento biológico"}])
        nota, = resultado["faltantes"]
        self.assertEqual(nota["quantidade_receita"], "medida a confirmar")
        self.assertIsNone(nota["quantidade_faltante_base"])
        self.assertTrue(nota["sem_preco"])
        self.assertTrue(resultado["pendencias"])

    def test_falta_parcial_e_reserva_de_outro_prato_entram_na_anotacao(self):
        item = self.store.obter("Farinha de trigo")
        necessario = item["disponivel_base"] + 1
        resultado = tools._resolver_ingredientes(self.store, [{"nome": item["nome"], "quantidade": necessario, "unidade": "kg"}])
        nota, = resultado["faltantes"]
        self.assertEqual(nota["quantidade_faltante_base"], 1)
        self.assertFalse(nota["sem_preco"])
        self.assertGreater(resultado["cmv_total"], 0)
        outro, = self.store.selecionar_pratos([{"nome": "Outro prato"}])
        with self.store.connect() as conn:
            conn.execute("UPDATE pratos SET status = 'aceito' WHERE id = ?", (outro["id"],))
            self.store._trocar_reservas(conn, outro["id"], [{"ingrediente_id": item["id"], "quantidade_base": item["estoque_base"]}])
        resultado = tools._resolver_ingredientes(self.store, [{"nome": item["nome"], "quantidade": 0.5, "unidade": "kg"}])
        self.assertEqual(resultado["faltantes"][0]["quantidade_faltante_base"], 0.5)

    def test_agua_e_derivado_disponivel_nao_sao_faltantes(self):
        resultado = tools._resolver_ingredientes(self.store, [{"nome": "molho de tomate", "quantidade": 150, "unidade": "g"},
                                                            {"nome": "água", "quantidade": 250, "unidade": "ml"}])
        self.assertEqual(resultado["faltantes"], [])
        self.assertEqual(resultado["compras"], [])
        self.assertTrue(resultado["preparos_caseiros"])

    def test_retomada_mostra_aviso_e_adaptacao_sem_confundir_com_aceite(self):
        receita = self.guardar()
        montagem.iniciar_turno(SESSAO, "faltante")
        painel._pergunta_da_retomada(self.store, self.prato, receita, SESSAO)
        texto = montagem.resposta_preparada(SESSAO, "faltante")
        self.assertIn("fermento biológico", texto)
        self.assertIn("Observações da ficha", texto)
        self.assertIn("Mantive fermento biológico na receita", texto)
        self.assertIn("Quer que eu pesquise o preço", texto)
        aval = tools._avaliar_prato(self.store, self.ingredientes, receita)
        pergunta = jornada.pergunta_pendente(self.prato, aval["bloqueios"], receita["dados"])
        self.assertEqual([o["rotulo"] for o in pergunta["opcoes"]], ["Pesquisar preço", "Adaptar sem comprar"])
        self.assertEqual(texto.count("?"), 1)
        self.assertNotIn("gás suficiente", texto)
        self.sem_compromissos()

    def test_cotacao_preserva_ficha_e_pergunta_preco_se_busca_falhar(self):
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["pizza"], "estado": "DF", "cidade": "Brasília"})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        # Ficha de uma versão própria. O resultado da pesquisa não é uma nova receita.
        receita = self.guardar(fonte_verificada=None)
        pergunta = jornada.resumo(self.store)["pergunta"]
        mensagem = pergunta["opcoes"][0]["mensagem"]

        def turno(texto, identificador, anterior):
            historico = [{"role": "assistant", "content": anterior}]
            falas.ao_iniciar_turno(session_id=SESSAO, conversation_history=historico, user_message=texto)
            return painel.ao_iniciar_turno(session_id=SESSAO, turn_id=identificador,
                                         conversation_history=historico, user_message=texto)["context"]

        contexto = turno(mensagem, "buscar-fermento", pergunta["texto"])
        self.assertIn("Ela pediu a cotação de fermento biológico para a ficha", contexto)
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "web_search")
        consulta = pesquisa.ajustar_consulta("web_search", {"query": "receita de pizza"}, SESSAO)
        self.assertIn("preço de fermento biológico", consulta["args"]["query"])
        pesquisa.depois_da_ferramenta("web_search", consulta["args"], "erro de rede", SESSAO)
        resposta = montagem.resposta_preparada(SESSAO, "buscar-fermento")
        self.assertIn(pesquisa.pergunta_do_preco("fermento biológico"), resposta)
        self.assertEqual(self.store.obter_receita(receita["id"]), receita)

        turno("R$ 4,90 por 10 g", "preco-fermento", resposta)
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "receita_conferir_ingredientes")
        cotada = json.loads(tools.receita_conferir_ingredientes(
            {"ingredientes": [{"nome": "fermento biológico", "quantidade": 30, "unidade": "g",
                               "preco_compra_estimado": 4.90, "quantidade_compra": 10, "unidade_compra": "g"}]}, session_id=SESSAO))
        self.assertTrue(cotada["success"], cotada)
        salva = self.store.obter_receita(cotada["receita_id"])
        self.assertEqual(cotada["prato_id"], self.prato["id"])
        self.assertEqual(salva["porcoes"], receita["porcoes"])
        self.assertEqual(salva["dados"]["modo_preparo"], receita["dados"]["modo_preparo"])
        self.assertEqual(salva["dados"]["ingredientes"][0::2], self.ingredientes[0::2])
        self.assertEqual(salva["dados"]["ingredientes"][1]["quantidade"], 5)
        self.assertEqual(salva["dados"]["ingredientes"][1]["preco_compra_estimado"], 4.90)
        self.assertIn("Cotação anotada na ficha", cotada["diga_a_dona_maria"])
        self.assertEqual(cotada["resumo"]["precisa_comprar"][0]["preco_estimado"], 4.90)
        self.assertEqual(cotada["ingredientes"][1]["custo_uso_compra"], 2.45)
        self.sem_compromissos()

    def test_vinculo_antigo_usa_tomate_para_molho_e_anota_falta_real(self):
        resultado = tools._resolver_ingredientes(self.store, [
            {"nome": "150 g de molho de tomate quanto baste", "quantidade": 150, "unidade": "g", "item_despensa": "molho de tomate"},
            {"nome": "1 un de 1/2 colher de fermento biológico", "quantidade": 5, "unidade": "g", "item_despensa": "fermento biológico"},
        ])
        molho, fermento = resultado["itens"]
        self.assertEqual(molho["item_despensa"], "Tomate")
        self.assertAlmostEqual(molho["usa_da_despensa_base"], 0.1875)
        self.assertTrue(fermento["falta_preco"])
        nome, = [a["ingrediente"] for a in resultado["faltantes"]]
        self.assertEqual(nome, "1 un de 1/2 colher de fermento biológico")
        self.assertEqual(pesquisa.pergunta_do_preco(nome), "Quanto custa fermento biológico e qual é a quantidade da embalagem?")
        self.sem_compromissos()

    def test_adaptar_em_outro_chat_salva_na_mesma_ficha_e_avanca_apos_resposta(self):
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["pizza"], "estado": "DF", "cidade": "Brasília"})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        ingredientes = [
            {"nome": "peito de frango", "quantidade": 300, "unidade": "g"},
            {"nome": "cebola a gosto", "quantidade": 100, "unidade": "g"},
            {"nome": "alho a gosto", "quantidade": 10, "unidade": "g"},
            {"nome": "150 g de molho de tomate quanto baste", "quantidade": 150, "unidade": "g", "item_despensa": "molho de tomate"},
            self.ingredientes[0],
            {"nome": "1 un de 1/2 colher de fermento biológico", "quantidade": 1, "unidade": "un"},
            {"nome": "quantidade não informada de sal a gosto"},
            {"nome": "água morna", "quantidade": 2, "unidade": "xícara"},
            {"nome": "quantidade não informada de fatias de queijo a gosto", "item_despensa": "mussarela"},
            {"nome": "orégano a gosto"},
        ]
        fonte = {**self.fonte, "ingredientes": [
            "300 g de peito de frango", "cebola a gosto", "alho a gosto", "molho de tomate quanto baste",
            "500 g de farinha de trigo", "1 1/2 colher de fermento biológico", "sal a gosto",
            "2 xícaras de água morna", "fatias de queijo a gosto", "orégano a gosto"]}
        antiga = self.guardar(ingredientes, fonte_verificada=fonte, sessao_da_conferencia="chat-antigo",
                              adaptacao="Omitir fermento.", modo_preparo="Omitir fermento e assar no forno.",
                              preparo_da_adaptacao="Omitir fermento e assar no forno.")
        sessao = "outro-chat"

        def turno(identidade, mensagem, anterior=""):
            historico = [{"role": "assistant", "content": anterior}] if anterior else []
            falas.ao_iniciar_turno(session_id=sessao, conversation_history=historico, user_message=mensagem)
            painel.ao_iniciar_turno(session_id=sessao, turn_id=identidade, conversation_history=historico, user_message=mensagem)
            return montagem.resposta_preparada(sessao, identidade)

        mensagem = f"Adapte o prato #{self.prato['id']}, Pizza de frango, com o estoque disponível, sem comprar 1/2 colher de fermento biologico."
        texto = turno("adaptar", mensagem)
        self.assertIn("Registrei na ficha a retirada de fermento biológico e orégano", texto)
        self.assertIn("Qual quantidade de sal", texto)
        self.assertNotIn("pesquise o preço", texto)
        prato, = self.store.pratos_em_andamento()
        nova = self.store.obter_receita(prato["receita_id"])
        self.assertNotEqual(nova["id"], antiga["id"])
        self.assertEqual(prato["nome"], "Pizza de frango")
        self.assertEqual(nova["dados"]["fonte_verificada"], fonte)
        self.assertEqual(nova["dados"]["ingredientes"][:5], ingredientes[:5])
        self.assertIn("massa firme", nova["dados"]["modo_preparo"])
        self.assertEqual({nota["estado"] for nota in nova["dados"]["ingredientes_faltantes"]}, {"omitido_na_adaptacao"})

        self.assertIn("Essa adaptação já está salva", turno("repetir", mensagem, texto))
        self.assertEqual(self.store.obter_prato(prato["id"])["receita_id"], nova["id"])
        pergunta = jornada.resumo(self.store)["pergunta"]
        self.assertEqual([opcao["rotulo"] for opcao in pergunta["opcoes"]], ["Usar 5 g"])
        texto = turno("cotacao-antiga", f"Pesquise o preço de fermento biológico para o prato #{prato['id']}.")
        self.assertIn("Fermento biológico ficou de fora", texto)
        self.assertIn(pergunta["texto"], texto)
        self.assertIsNone(pesquisa.ferramenta_da_conducao(sessao))
        for indice, entrada in enumerate(("5ml/L", "g/kg", "5 g/kg")):
            texto = turno(f"proporcao-{indice}", entrada,
                          "Quer que eu pesquise o preço do fermento biológico?" if indice == 0 else texto)
            self.assertIn("lote inteiro", texto)
            self.assertIn(pergunta["texto"], texto)
            self.assertEqual(self.store.obter_receita(nova["id"]), nova)
        texto = turno("sal", "5 g", texto)
        self.assertIn("Qual quantidade de fatias de queijo", texto)
        medidas = self.store.obter_receita(nova["id"])["dados"]["medidas_de_uso_confirmadas"]
        self.assertEqual(medidas["quantidade não informada de sal a gosto"]["quantidade"], 5)
        self.assertEqual(self.store.obter_receita(antiga["id"]), antiga)
        self.sem_compromissos()

    def test_montagem_com_estoque_estima_queijo_e_confirma_cozinha_junta(self):
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["pizza"], "estado": "DF", "cidade": "Brasília"})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        self.store.perfil_salvar({"equipamentos": {"forno": None, "forma": None}, "tecnicas": {"assar": None},
                                 "restricoes": {"gas": "risco de acabar", "energia": "cai às vezes", "espaco_geladeira": "pouco"}})
        ingredientes = [{"nome": "farinha de trigo", "quantidade": 500, "unidade": "g"},
                        {"nome": "água", "quantidade": 230, "unidade": "ml"},
                        {"nome": "sal a gosto", "quantidade": 5, "unidade": "g"},
                        {"nome": "fatias de queijo a gosto", "item_despensa": "mussarela"}]
        preparo = "Misture a farinha, a água e o sal. Abra uma massa fina na forma. Cubra com o queijo e asse no forno por 25 minutos."
        fonte = {**self.fonte, "ingredientes": ["500 g de farinha de trigo", "250 ml de água", "sal a gosto",
                                              "fatias de queijo a gosto", "5 g de fermento biológico"]}
        antiga = self.guardar(ingredientes, fonte_verificada=fonte, adaptacao="Massa fina sem fermentação.",
                              modo_preparo=preparo, preparo_da_adaptacao=preparo,
                              medidas_de_uso_confirmadas={"sal a gosto": {"quantidade": 5, "unidade": "g"}})

        def turno(identidade, mensagem):
            falas.ao_iniciar_turno(session_id=SESSAO, user_message=mensagem)
            painel.ao_iniciar_turno(session_id=SESSAO, turn_id=identidade, user_message=mensagem)
            return montagem.resposta_preparada(SESSAO, identidade)

        resposta = turno("montar-com-estoque", f"Monte a ficha do prato #{self.prato['id']} com o estoque disponível.")
        self.assertIn("150 g de fatias de queijo", resposta)
        nova = self.store.obter_receita(self.store.obter_prato(self.prato["id"])["receita_id"])
        self.assertEqual(nova["dados"]["medidas_de_uso_confirmadas"], antiga["dados"]["medidas_de_uso_confirmadas"])
        self.assertEqual(nova["dados"]["medidas_de_uso_estimadas"]["fatias de queijo a gosto"]["quantidade"], 150)
        self.assertEqual(nova["dados"]["fonte_verificada"], fonte)
        pergunta = jornada.resumo(self.store)["pergunta"]
        self.assertTrue(pergunta["confirmacoes_agrupadas"])
        self.assertIn("aceita preparar", turno("confirmar-cozinha", pergunta["opcoes"][0]["mensagem"]))
        self.assertEqual(jornada.resumo(self.store)["etapa"], "aceite")
        self.assertFalse(self.store.analises_preco())
        self.assertEqual(self.store.obter_receita(antiga["id"]), antiga)
        self.sem_compromissos()

    def test_anotar_nao_libera_cmv_incompleto(self):
        receita = self.guardar()
        resultado = json.loads(tools.prato_calcular_cmv({"receita_id": receita["id"]}, session_id=SESSAO))
        self.assertFalse(resultado["success"])
        self.assertTrue(resultado["bloqueios"])
        self.sem_compromissos()

    def test_omissao_com_preparo_proprio_preserva_historico_e_custa_so_o_usado(self):
        original = self.guardar()
        ingredientes = [self.ingredientes[0], self.ingredientes[2]]
        preparo = "Misture farinha e água, abra uma massa fina sem fermentação e asse no forno até estar completamente cozida."
        receita = self.guardar(ingredientes, adaptacao="Massa fina sem fermento, adaptação local para teste.",
                               modo_preparo=preparo, preparo_da_adaptacao=preparo)
        nota, = receita["dados"]["ingredientes_faltantes"]
        self.assertEqual(nota["estado"], "omitido_na_adaptacao")
        self.assertEqual(self.store.obter_receita(original["id"])["dados"]["ingredientes"], self.ingredientes)
        resultado = json.loads(tools.prato_calcular_cmv({"receita_id": receita["id"]}, session_id=SESSAO))
        self.assertTrue(resultado["success"], resultado)
        self.assertEqual(resultado["cmv_total"], tools._resolver_ingredientes(self.store, ingredientes)["cmv_total"])
        self.assertEqual(self.store.obter_prato(resultado["prato_id"])["dados"]["ingredientes_faltantes"][0]["estado"], "omitido_na_adaptacao")
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_tool_adapta_ficha_atual_e_antiga_preservando_observacao(self):
        for ficha_antiga in (False, True):
            with self.subTest(ficha_antiga=ficha_antiga):
                original = self.guardar()
                if ficha_antiga:
                    dados = deepcopy(original["dados"])
                    dados.pop("ingredientes_faltantes")
                    with self.store.connect() as conn:
                        conn.execute("UPDATE receitas SET dados = ? WHERE id = ?", (json.dumps(dados), original["id"]))
                preparo = "Misture farinha e água, abra massa fina sem fermentação e asse até cozinhar completamente."
                resultado = json.loads(tools.receita_conferir_ingredientes({
                    "prato_id": self.prato["id"], "porcoes": 4, "tempo_preparo_min": 60,
                    "ingredientes": [self.ingredientes[0], self.ingredientes[2]],
                    "adaptacao": "Massa fina sem fermento, adaptação local para testar.",
                    "modo_preparo": preparo}, session_id=SESSAO))
                self.assertTrue(resultado["success"], resultado)
                self.assertEqual(resultado["veredito"], "viavel", resultado)
                nota, = resultado["ingredientes_faltantes"]
                self.assertEqual(nota["estado"], "omitido_na_adaptacao")
                self.assertIn("não será usado nesta adaptação", resultado["diga_a_dona_maria"])
                salva = self.store.obter_receita(resultado["receita_id"])
                self.assertEqual(salva["dados"]["fonte_verificada"], self.fonte)
                self.assertEqual(salva["dados"]["ingredientes_faltantes"], [nota])
                self.assertEqual(salva["dados"]["modo_preparo"], preparo)
                self.sem_compromissos()

    def test_pedido_natural_de_adaptacao_chega_a_llm_sem_pergunta_pronta(self):
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["pizza"], "estado": "DF", "cidade": "Brasília"})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        original = self.guardar()
        mensagem = f"Quero salvar o prato #{self.prato['id']} sem fermento, usando o que tenho."
        falas.registrar(SESSAO, [], mensagem)
        contexto = painel.ao_iniciar_turno(session_id=SESSAO, turn_id="flexivel", user_message=mensagem)
        self.assertIn("ADAPTAÇÃO DELEGADA", contexto["context"])
        self.assertIn("ingrediente_original", contexto["context"])
        self.assertIn("leva dias", contexto["context"])
        self.assertIsNone(montagem.resposta_preparada(SESSAO, "flexivel"))
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "despensa_listar")
        pesquisa.depois_da_ferramenta(tool_name="despensa_listar", result='{"success":true}', session_id=SESSAO)
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "receita_conferir_ingredientes")
        pesquisa.depois_da_ferramenta(tool_name="receita_conferir_ingredientes", result='{"success":true}', session_id=SESSAO)
        self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))
        self.assertEqual(self.store.obter_receita(original["id"]), original)
        self.sem_compromissos()

    def test_fallback_sem_variante_local_delega_preparo_a_llm(self):
        self.guardar()
        with self.store.connect() as conn:
            conn.execute("UPDATE ingredientes SET estoque_base = 0 WHERE nome = 'Farinha de trigo'")
        mensagem = f"Adapte o prato #{self.prato['id']}, Pizza de frango, com o estoque disponível, sem comprar farinha de trigo."
        montagem.iniciar_turno(SESSAO, "fallback")
        contexto = painel._adaptacao_do_rascunho(self.store, falas.Fala(mensagem), SESSAO)
        self.assertIn("ADAPTAÇÃO DELEGADA", contexto)
        self.assertIsNone(montagem.resposta_preparada(SESSAO, "fallback"))
        self.sem_compromissos()

    def test_reconferencia_preserva_adaptacao_omitidos_e_preparo(self):
        self.guardar()
        preparo = "Abra farinha e água em massa fina sem fermentação e asse no forno até cozinhar completamente."
        salva = self.guardar([self.ingredientes[0], self.ingredientes[2]], adaptacao="Massa fina sem fermento.",
                             modo_preparo=preparo, preparo_da_adaptacao=preparo)
        resultado = json.loads(tools.receita_conferir_ingredientes({"prato_id": self.prato["id"]}, session_id=SESSAO))
        self.assertTrue(resultado["success"], resultado)
        self.assertEqual(resultado["veredito"], "viavel", resultado)
        nova = self.store.obter_receita(resultado["receita_id"])
        for campo in ("ingredientes", "adaptacao", "modo_preparo", "ingredientes_faltantes", "fonte_verificada"):
            self.assertEqual(nova["dados"][campo], salva["dados"][campo])
        self.sem_compromissos()

    def test_pedido_de_omissao_supre_campo_adaptacao_sem_restaurar_original(self):
        original = self.guardar()
        pesquisa.conduzir_adaptacao(SESSAO, self.store.obter_prato(self.prato["id"]), original)
        falas.registrar(SESSAO, [], f"Salve o prato #{self.prato['id']} sem fermento.")
        resultado = json.loads(tools.receita_conferir_ingredientes({
            "prato_id": self.prato["id"], "porcoes": 4, "fonte_url": "https://exemplo.invalid/massa-fina",
            "ingredientes": [self.ingredientes[0], self.ingredientes[2]],
            "modo_preparo": "Misture farinha e água, abra uma massa fina sem fermentação e asse no forno.",
            "tempo_preparo_min": 40, "tempo_preparo_base": "10 min para massa e 30 min de forno."}, session_id=SESSAO))
        self.assertEqual(resultado["veredito"], "viavel", resultado)
        self.assertEqual(resultado["ingredientes_faltantes"][0]["estado"], "omitido_na_adaptacao")
        self.assertIn("Adaptação local solicitada", resultado["fidelidade_fonte"]["adaptacao"])
        self.assertEqual(self.store.obter_receita(resultado["receita_id"])["fonte_url"], original["fonte_url"])
        self.sem_compromissos()

    def test_substituto_culinario_usa_custo_do_item_real_e_preserva_diferenca(self):
        self.fonte["ingredientes"].append("20 ml de azeite")
        self.guardar()
        oleo = {"nome": "Óleo de soja", "ingrediente_original": "azeite", "quantidade": 20, "unidade": "ml"}
        ingredientes = [self.ingredientes[0], self.ingredientes[2], oleo]
        resultado = json.loads(tools.receita_conferir_ingredientes({
            "prato_id": self.prato["id"], "porcoes": 4, "ingredientes": ingredientes,
            "adaptacao": "Massa fina sem fermento com óleo de soja no lugar do azeite.",
            "modo_preparo": "Misture farinha, água e óleo de soja, abra uma massa fina e asse no forno.",
            "tempo_preparo_min": 40}, session_id=SESSAO))
        self.assertEqual(resultado["veredito"], "viavel", resultado)
        self.assertTrue(any("substituído por Óleo de soja" in d for d in resultado["fidelidade_fonte"]["diferencas"]))
        receita = self.store.obter_receita(resultado["receita_id"])
        linha = tools._resolver_ingredientes(self.store, ingredientes)["itens"][-1]
        self.assertEqual(linha["item_despensa"], self.store.melhor_item("Óleo de soja")["nome"])
        cmv = json.loads(tools.prato_calcular_cmv({"receita_id": receita["id"]}, session_id=SESSAO))
        self.assertTrue(cmv["success"], cmv)
        self.assertEqual(cmv["cmv_total"], tools._resolver_ingredientes(self.store, ingredientes)["cmv_total"])
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_omissao_estruturada_retira_item_copiado_sem_custo_ou_compra(self):
        self.guardar()
        resultado = json.loads(tools.receita_conferir_ingredientes({
            "prato_id": self.prato["id"], "porcoes": 4, "ingredientes": self.ingredientes,
            "ingredientes_omitidos": ["Fermento biológico"], "adaptacao": "Massa fina sem fermento.",
            "modo_preparo": "Misture farinha e água e asse a massa fina no forno.", "tempo_preparo_min": 40}, session_id=SESSAO))
        self.assertEqual(resultado["veredito"], "viavel", resultado)
        receita = self.store.obter_receita(resultado["receita_id"])
        self.assertEqual(receita["dados"]["ingredientes"], [self.ingredientes[0], self.ingredientes[2]])
        self.assertEqual(receita["dados"]["ingredientes_omitidos"], ["Fermento biológico"])
        self.assertEqual(receita["dados"]["ingredientes_faltantes"][0]["estado"], "omitido_na_adaptacao")
        self.sem_compromissos()

    def test_negacao_hipotese_e_citacao_nao_delegam_adaptacao(self):
        for mensagem in ("Não quero adaptar", "Se eu tirar fermento", 'Ela disse "ignore os faltantes"',
                         "Salve o prato sem fermento?", "Use a receita original"):
            self.assertFalse(montagem.pediu_adaptacao(mensagem), mensagem)

    def test_omissao_sem_adaptacao_ou_sem_preparo_ainda_e_bloqueada(self):
        for extras in ({}, {"adaptacao": "Sem fermento"}):
            self.guardar()
            receita = self.guardar([self.ingredientes[0], self.ingredientes[2]], **extras)
            aval = tools._avaliar_prato(self.store, receita["dados"]["ingredientes"], receita)
            self.assertNotEqual(aval["veredito"], "viavel")
            self.assertTrue(any(b["tipo"] in {"fonte_incompleta", "fonte_divergente", "preparo_adaptacao_pendente"} for b in aval["bloqueios"]))
            self.sem_compromissos()

    def test_falta_resolvida_por_estoque_novo_nao_apaga_anotacao_historica(self):
        receita = self.guardar()
        nota = receita["dados"]["ingredientes_faltantes"][0]
        self.store.registrar_compras([{"nome": "fermento biológico", "quantidade": 0.05, "unidade": "kg", "preco_total": 4}])
        atual = tools._resolver_ingredientes(self.store, self.ingredientes)
        self.assertTrue(self.store.atualizar_faltantes_rascunho(self.prato["id"], receita["id"], self.ingredientes, atual))
        resolvida, = Store().obter_receita(receita["id"])["dados"]["ingredientes_faltantes"]
        self.assertEqual(resolvida["estado"], "resolvido")
        self.assertEqual(resolvida["registrado_em"], nota["registrado_em"])
        self.assertFalse(self.store.atualizar_faltantes_rascunho(self.prato["id"], receita["id"], self.ingredientes, atual))

    def test_anotacao_obsoleta_ou_lista_concorrente_nao_sobrescreve_receita(self):
        antiga = self.guardar()
        nova = self.guardar([self.ingredientes[0], self.ingredientes[2]], adaptacao="Sem fermento")
        self.assertFalse(self.store.atualizar_faltantes_rascunho(self.prato["id"], antiga["id"], self.ingredientes, antiga["dados"]["resultado"]))
        self.assertFalse(self.store.atualizar_faltantes_rascunho(self.prato["id"], nova["id"], self.ingredientes, antiga["dados"]["resultado"]))
        self.assertEqual(self.store.obter_receita(nova["id"]), nova)
        self.sem_compromissos()

    def test_leitura_publica_e_cardapio_expoem_observacao_sem_gravar(self):
        receita = self.guardar()
        self.assertEqual(jornada.pratos_atuais(self.store)[0]["ingredientes_faltantes"], receita["dados"]["ingredientes_faltantes"])
        consulta = json.loads(tools.cardapio_consultar({}))
        self.assertEqual(consulta["pratos_em_analise"][0]["ingredientes_faltantes"], receita["dados"]["ingredientes_faltantes"])
        self.assertEqual(self.store.obter_receita(receita["id"]), receita)
        self.sem_compromissos()
