"""Uma receita lida não pode virar CMV de dois ingredientes por omissão da LLM."""

import json
from copy import deepcopy

from sabor_da_maria import extract, falas, fonte, montagem, pesquisa, precificacao, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase, diz


URL = "https://www.tudogostoso.com.br/receita/15354-frango-assado-com-batatas.html"
FONTE = {"nome": "Frango assado com batatas", "url": URL, "porcoes": 5, "tempo_preparo_min": 55,
         "ingredientes": ["1,5 kg de frango", "2 batatas grandes", "100 g de molho de tomate", "50 g de maionese",
                          "10 g de tempero para aves", "2 dentes de alho", "Sal a gosto"],
         "modo_preparo": "Coloque o frango e as batatas em uma assadeira. Leve ao forno e asse até dourar."}


def call(tool, **args):
    return json.loads(tools.HANDLERS[tool](args, session_id=SESSAO))


class FidelidadeDaFonteTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        pesquisa.esquecer_tudo()
        self.addCleanup(pesquisa.esquecer_tudo)
        self.store = Store()
        self.store.perfil_salvar({"equipamentos": {"fogao": 4, "forno": True, "forma": True, "panela_pressao": True,
                                                   "air_fryer": False, "liquidificador": True},
                                 "tecnicas": {"refogar": "sim", "assar": "sim"},
                                 "restricoes": {"tempo_por_cozinhada_min": 120, "espaco_geladeira": "bastante",
                                                "gas": "botijão", "energia": "estável"}})
        pesquisa.marcar_turno(SESSAO, "monte um prato para meu cardápio")
        pagina = {"url": URL, "title": FONTE["nome"], "receitas": [deepcopy(FONTE)],
                  "content": "# Frango assado com batatas\n## Ingredientes\n- 1,5 kg de frango\n## Modo de preparo\nAsse no forno.",
                  "metadata": {"recipe_schema": True}}
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps({"results": [pagina]}), session_id=SESSAO)

    def parcial(self, **extras):
        args = {"porcoes": 4, "tempo_preparo_min": 55,
                "ingredientes": [{"nome": "frango", "quantidade": 1, "unidade": "kg"},
                                 {"nome": "batata", "quantidade": 2, "unidade": "kg"}]}
        if extras.get("adaptacao"):
            args["modo_preparo"] = "Coloque frango e batatas em assadeira e asse no forno até o frango cozinhar por completo."
        return call("receita_conferir_ingredientes", **{**args, **extras})

    def test_ficha_parcial_persiste_rascunho_mas_nao_calcula_cmv_nem_reserva(self):
        ficha = self.parcial()
        self.assertTrue(ficha["success"], ficha)
        self.assertEqual(ficha["veredito"], "precisa_confirmar")
        self.assertEqual(ficha["bloqueios"][0]["tipo"], "fonte_incompleta")
        self.assertIsNone(ficha["resumo"]["cmv_previa"])
        self.assertEqual(ficha["fidelidade_fonte"]["faltantes"], FONTE["ingredientes"][2:])
        receita = self.store.obter_receita(ficha["receita_id"])
        self.assertEqual(receita["dados"]["fonte_verificada"], FONTE)
        self.assertIn("forno", receita["dados"]["equipamentos_necessarios"])
        self.assertIn("forma", receita["dados"]["equipamentos_necessarios"])
        self.assertIn("assar", receita["dados"]["tecnicas_necessarias"])
        pesquisa.marcar_turno(SESSAO, "Aceito esse prato")  # o snapshot persiste além do turno de pesquisa
        diz(f"Aceito o prato #{ficha['prato_id']}")
        cmv = call("prato_calcular_cmv", receita_id=ficha["receita_id"])
        self.assertFalse(cmv["success"], cmv)
        self.assertEqual(cmv["bloqueios"][0]["tipo"], "fonte_incompleta")
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.analises_preco(), [])
        analytics = precificacao.consultar(self.store)
        self.assertEqual(analytics["previas"], [])
        self.assertEqual(analytics["pendentes"][0]["prato_id"], ficha["prato_id"])

    def test_diferencas_de_quantidade_e_rendimento_sao_explicitas(self):
        montagem.iniciar_turno(SESSAO, "versao-adaptada")
        ficha = self.parcial(adaptacao="Versão simples com os ingredientes da despensa")
        self.assertEqual(ficha["veredito"], "viavel", ficha)
        self.assertIn("(adaptação)", ficha["versao"])
        self.assertIn("Diferenças da fonte", ficha["diga_a_dona_maria"])
        self.assertIn("1,5 kg de frango", ficha["diga_a_dona_maria"])
        self.assertIn("2 batatas grandes", ficha["diga_a_dona_maria"])
        self.assertIn("rendimento da fonte 5 porções → proposto 4", ficha["diga_a_dona_maria"])
        self.assertIn("maionese", ficha["diga_a_dona_maria"])
        self.assertEqual(self.store.reservas_ativas(), [])
        # Guardar uma proposta adaptada ainda não é aceite da Dona Maria.
        self.assertEqual(self.store.obter_prato(ficha["prato_id"])["status"], "selecionado")
        # O texto final vem dos dados da conferência mesmo que a LLM omita mudanças.
        resposta = montagem.completar_resposta("Fiz uma receita parecida. Aceita?", SESSAO, "versao-adaptada")
        for diferenca in ficha["fidelidade_fonte"]["diferencas"]:
            self.assertIn(diferenca, resposta)
        self.assertIn("2 batatas grandes → proposto 2 kg", resposta)

    def test_adaptacao_sem_preparo_fica_pendente_sem_perder_o_rascunho(self):
        ficha = self.parcial(adaptacao="Versão sem os molhos", modo_preparo="")
        self.assertEqual(ficha["veredito"], "precisa_confirmar")
        self.assertIn("preparo_adaptacao_pendente", [b["tipo"] for b in ficha["bloqueios"]])
        self.assertIsNotNone(self.store.obter_prato(ficha["prato_id"]))
        self.assertFalse(call("prato_calcular_cmv", receita_id=ficha["receita_id"])["success"])
        repetida = self.parcial(adaptacao="Versão sem molhos", modo_preparo=FONTE["modo_preparo"], prato_id=ficha["prato_id"])
        self.assertEqual(repetida["veredito"], "precisa_confirmar")
        self.assertIn("preparo_adaptacao_pendente", [b["tipo"] for b in repetida["bloqueios"]])

    def test_omitir_requisitos_do_modelo_nao_esconde_forno_da_fonte(self):
        self.store.perfil_salvar({"equipamentos": {"forno": False}})
        ficha = self.parcial(adaptacao="Versão simples sem os molhos", equipamentos_necessarios=None, tecnicas_necessarias=None)
        self.assertEqual(ficha["veredito"], "inviavel_por_enquanto")
        self.assertIn("equipamento_faltando", [b["tipo"] for b in ficha["bloqueios"]])
        self.assertIn("forno", ficha["equipamentos"]["faltando"])

    def test_nova_adaptacao_nao_reutiliza_aceite_dado_antes_de_ver_diferencas(self):
        diz("Aceito Frango assado com batatas nessa versão.")
        ficha = self.parcial(adaptacao="Versão simples com frango e batatas")
        cmv = call("prato_calcular_cmv", receita_id=ficha["receita_id"])
        self.assertTrue(cmv["success"], cmv)
        preco = call("preco_sugerir", prato_id=cmv["prato_id"], aceite_prato="Aceito Frango assado com batatas nessa versão.")
        self.assertFalse(preco["success"], preco)
        self.assertEqual(self.store.analises_preco(), [])
        pergunta = ficha["diga_a_dona_maria"] + f" Você aceita preparar o prato #{cmv['prato_id']} nessa versão?"
        falas.registrar(SESSAO, [{"role": "assistant", "content": pergunta}], "Sim, aceito essa adaptação.")
        preco = call("preco_sugerir", prato_id=cmv["prato_id"])
        self.assertTrue(preco["analise_salva"], preco)

    def test_retry_sem_nome_fonte_ou_id_recupera_rascunho_da_mesma_sessao(self):
        primeira = self.parcial()
        segunda = self.parcial()
        self.assertTrue(segunda["success"], segunda)
        self.assertEqual(segunda["prato_id"], primeira["prato_id"])
        self.assertEqual(len(self.store.pratos_em_andamento()), 1)
        self.assertEqual(self.store.obter_prato(primeira["prato_id"])["receita_id"], segunda["receita_id"])
        self.assertNotIn("prato_calcular_cmv", segunda["proximo_passo"])

    def test_retry_no_turno_seguinte_recupera_snapshot_sem_pesquisar_de_novo(self):
        primeira = self.parcial()
        pesquisa.marcar_turno(SESSAO, "vamos corrigir a ficha")
        segunda = self.parcial()
        self.assertTrue(segunda["success"], segunda)
        self.assertEqual(segunda["prato_id"], primeira["prato_id"])
        self.assertEqual(segunda["fonte_verificada"], FONTE)

    def test_cmv_sem_id_recupera_somente_ficha_unica_desta_sessao(self):
        ficha = self.parcial(adaptacao="Versão simples sem os molhos")
        self.assertEqual(ficha["veredito"], "viavel", ficha)
        self.assertIn(f"prato_calcular_cmv(receita_id={ficha['receita_id']})", ficha["proximo_passo"])
        cmv = call("prato_calcular_cmv")
        self.assertTrue(cmv["success"], cmv)
        self.assertEqual(cmv["prato_id"], ficha["prato_id"])
        outra = json.loads(tools.prato_calcular_cmv({}, session_id="outra-sessao"))
        self.assertFalse(outra["success"], outra)
        self.assertEqual(self.store.reservas_ativas(), [])

    def test_cmv_sem_id_nao_adivinha_entre_duas_fichas_da_mesma_sessao(self):
        for nome in ("Arroz da casa", "Arroz do dia"):
            self.store.salvar_receita(nome, None, 2, {
                "ingredientes": [{"nome": "arroz", "quantidade": 100, "unidade": "g"}],
                "tempo_preparo_min": 20, "sessao_da_conferencia": SESSAO,
            }, "conferida", criar_rascunho=True)
        resultado = call("prato_calcular_cmv")
        self.assertFalse(resultado["success"], resultado)
        self.assertEqual(resultado["bloqueios"][0]["tipo"], "ficha_ambigua")
        self.assertEqual(len(resultado["opcoes"]), 2)
        self.assertEqual(self.store.analises_preco(), [])

    def test_retry_nao_recupera_rascunho_de_outra_sessao(self):
        primeira = self.parcial()
        pesquisa.marcar_turno("outra-sessao", "monte um prato para meu cardápio")
        pagina = {"url": URL, "title": FONTE["nome"], "receitas": [deepcopy(FONTE)],
                  "content": "# Frango assado com batatas\n## Ingredientes\n- 1,5 kg de frango\n## Modo de preparo\nAsse no forno."}
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps({"results": [pagina]}), session_id="outra-sessao")
        resultado = json.loads(tools.receita_conferir_ingredientes({"porcoes": 4, "tempo_preparo_min": 55,
                               "ingredientes": [{"nome": "frango", "quantidade": 1, "unidade": "kg"}]}, session_id="outra-sessao"))
        self.assertFalse(resultado["success"], resultado)
        self.assertEqual(self.store.obter_prato(primeira["prato_id"])["receita_id"], primeira["receita_id"])

    def test_cobertura_completa_com_conversao_equivalente_nao_diverge(self):
        ingredientes = [
            {"nome": "frango", "quantidade": 1500, "unidade": "g"},
            {"nome": "batata", "quantidade": 2, "unidade": "grande"},
            {"nome": "molho de tomate", "quantidade": 100, "unidade": "g"},
            {"nome": "maionese", "quantidade": 50, "unidade": "g"},
            {"nome": "tempero para aves", "quantidade": 10, "unidade": "g"},
            {"nome": "alho", "quantidade": 2, "unidade": "dente"},
            {"nome": "sal", "quantidade": 5, "unidade": "g"},
        ]
        conferencia = fonte.conferir(FONTE, ingredientes, 5, tempo_preparo_min=55)
        self.assertEqual(conferencia["bloqueios"], [], conferencia)
        self.assertEqual(conferencia["faltantes"], [])
        self.assertTrue(conferencia["estimativas"])

    def test_ficha_copiada_da_fonte_nao_diverge_por_causa_dos_sinonimos_da_despensa(self):
        # Receita real que travou depois do "Gostei": "patinho" vira carne moída e "massa" vira macarrão no casamento
        # com a despensa, e a comparação dava nota baixa para o nome contra ele mesmo.
        contrato = {**FONTE, "nome": "Quiabo com carne", "porcoes": 8, "ingredientes": [
            "500 g de patinho cortado em cubinhos", "5 colheres sopa de massa de tomate", "200 g de queijo fresco em cubos",
            "1 xícara de farinha de rosca", "300 g de carne seca desfiada", "2 dentes de alho amassado", "Sal a gosto"]}
        conferencia = fonte.conferir(contrato, fonte.ingredientes_publicados(contrato), 8)
        self.assertEqual((conferencia["faltantes"], conferencia["diferencas"], conferencia["bloqueios"]), ([], [], []), conferencia)
        self.assertEqual(fonte.sem_medida(conferencia), ["Sal a gosto"])
        # Nome diferente continua sendo conferido: trocar o ingrediente aparece como diferença.
        trocada = [{**i, "nome": "frango em cubos"} if "patinho" in i["nome"] else i for i in fonte.ingredientes_publicados(contrato)]
        self.assertIn("retirado da fonte: 500 g de patinho cortado em cubinhos", fonte.conferir(contrato, trocada, 8)["diferencas"])

    def test_divergencia_diz_o_problema_sem_orientacao_escrita_para_o_modelo(self):
        contrato = {**FONTE, "ingredientes": ["1,5 kg de frango", "Suco de 1 limão"]}
        conferencia = fonte.conferir(contrato, fonte.ingredientes_publicados(contrato), 5)
        detalhe = conferencia["bloqueios"][0]["detalhe"]
        self.assertEqual(detalhe, "A ficha ainda não bate com a receita lida: Suco de 1 limão: a medida da receita ainda não foi "
                                  "convertida; a quantidade proposta é uma adaptação.")
        for interno in ("adaptacao", "Complete a ficha", "confirme a proposta"):
            self.assertNotIn(interno, detalhe)
        ficha = call("receita_conferir_ingredientes", nome=FONTE["nome"], fonte_url=URL, porcoes=5, tempo_preparo_min=55,
                     ingredientes=[{"nome": "frango", "quantidade": 1500, "unidade": "g"}])
        self.assertEqual(ficha["bloqueios"][0]["tipo"], "fonte_incompleta")
        self.assertIn("campo adaptacao", ficha["proximos_passos"][0])  # o como-fazer continua chegando ao modelo
        self.assertNotIn("adaptacao", ficha["bloqueios"][0]["detalhe"])

    def test_medidas_e_rendimento_nao_interpretaveis_nao_derrubam_guard(self):
        for linha in ("1/0 kg de frango", "½ kg de frango", "1,5kg de frango"):
            contrato = {**FONTE, "ingredientes": [linha], "porcoes": "rende o que der"}
            conferencia = fonte.conferir(contrato, [{"nome": "frango", "quantidade": 1, "unidade": "kg"}], 4)
            self.assertTrue(conferencia["bloqueios"], conferencia)

    def test_medidas_caseiras_publicadas_preservam_equivalencia(self):
        for linha, nome, qtd, unidade in (
                ("2 colheres (sopa) de óleo", "óleo", 2, "colher de sopa"),
                ("1 colher (chá) de sal", "sal", 1, "colher de chá"),
                ("1 xícara (chá) de arroz", "arroz", 1, "xícara"),
                ("½ kg de frango", "frango", 500, "g"),
                ("1,5kg de frango", "frango", 1500, "g")):
            with self.subTest(linha=linha):
                conferencia = fonte.conferir({"ingredientes": [linha]}, [{"nome": nome, "quantidade": qtd, "unidade": unidade}], 4)
                self.assertEqual(conferencia["bloqueios"], [], conferencia)
        quebrada = fonte.conferir({"ingredientes": ["1/0 kg de frango"]}, [{"nome": "frango", "quantidade": 1, "unidade": "kg"}], 4)
        self.assertTrue(quebrada["bloqueios"])
