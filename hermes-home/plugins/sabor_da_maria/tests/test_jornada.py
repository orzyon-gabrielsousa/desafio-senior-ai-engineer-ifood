"""Condução incremental usa o estado salvo sem assumir decisões da pessoa."""

import json
from unittest.mock import patch

from sabor_da_maria import api, falas, jornada, montagem, painel, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


class JornadaTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        pesquisa.esquecer_tudo()
        self.addCleanup(pesquisa.esquecer_tudo)
        self.store = Store()
        self.store.negocio_salvar({"tipo": "marmitaria", "alimentacao": ["marmita"], "estado": "SP", "cidade": "São Paulo"})
        self.store.perfil_salvar({"equipamentos": {"forno": True, "forma": True}, "tecnicas": {"assar": "sim"},
                                 "restricoes": {"tempo_por_cozinhada_min": 120, "gas": "botijão", "energia": "estável", "espaco_geladeira": "médio"}})

    def ficha(self):
        prato, = self.store.selecionar_pratos([{"nome": "Batata assada da casa"}])
        dados = {"ingredientes": [{"nome": "Batata", "quantidade": 200, "unidade": "g"}],
                 "tempo_preparo_min": 40, "equipamentos_necessarios": ["forno", "forma"], "tecnicas_necessarias": ["assar"]}
        rid = self.store.salvar_receita(prato["nome"], None, 2, dados, "conferida", prato_id=prato["id"])
        return prato["id"], rid, dados

    def test_cozinha_parcial_nao_bloqueia_receita_com_requisitos_atendidos(self):
        _, rid, dados = self.ficha()
        self.assertTrue(tools._pendencias_perfil(self.store.perfil()))
        aval = tools._avaliar_prato(self.store, dados["ingredientes"], self.store.obter_receita(rid))
        self.assertEqual(aval["veredito"], "viavel", aval)
        self.assertEqual(jornada.resumo(self.store)["etapa"], "aceite")
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.analises_preco(), [])

    def test_falta_relevante_e_reavaliada_apos_mudar_perfil(self):
        _, rid, dados = self.ficha()
        antes = self.store.obter_receita(rid)["dados"]
        self.store.perfil_salvar({"equipamentos": {"forno": False}})
        resumo = jornada.resumo(self.store)
        self.assertEqual(resumo["etapa"], "pendencias")
        self.assertIn("equipamento_faltando", [p["tipo"] for p in resumo["pendencias"]])
        self.assertNotIn("liquidificador", str(resumo["pendencias"]))
        self.assertEqual(self.store.obter_receita(rid)["dados"], antes)

    def test_operacao_relevante_ausente_ainda_bloqueia(self):
        _, rid, dados = self.ficha()
        # Retira só a rotina, simulando cadastro inicial parcial em outro banco.
        with self.store.connect() as conn:
            conn.execute("DELETE FROM perfil WHERE chave = 'restricoes'")
        aval = tools._avaliar_prato(self.store, dados["ingredientes"], self.store.obter_receita(rid))
        self.assertEqual(aval["veredito"], "precisa_confirmar")
        self.assertTrue({"tempo_disponivel_pendente", "gas_a_confirmar"} <= {p["tipo"] for p in aval["bloqueios"]})
        self.assertNotIn("perfil_pendente", [p["tipo"] for p in aval["bloqueios"]])

    def test_pos_cadastro_apresenta_estoque_e_aguarda_prontidao_sem_pesquisar(self):
        antes = self.store.pratos(), self.store.reservas_ativas(), self.store.orcamento()
        resposta = painel.ao_iniciar_turno(session_id=SESSAO, turn_id="cadastro",
            user_message="Enviei meu cadastro da cozinha. Conduza os próximos passos.\n[painel da tela: cadastro guiado no chat; salvou cozinha]")
        self.assertNotIn("MONTAR UM PRATO PARA O CARDÁPIO", resposta["context"])
        self.assertFalse(pesquisa.montando(SESSAO))
        self.assertEqual(jornada.resumo(self.store)["etapa"], "estoque")
        pronta = montagem.resposta_preparada(SESSAO, "cadastro")
        self.assertIn("ingredientes já comprados", pronta)
        self.assertIn("Está pronta para começar", pronta)
        self.assertEqual((self.store.pratos(), self.store.reservas_ativas(), self.store.orcamento()), antes)

    def test_retomada_generica_foca_ficha_unica_sem_aceitar(self):
        pid, _, _ = self.ficha()
        antes = self.store.pratos()
        resposta = painel.ao_iniciar_turno(session_id=SESSAO, turn_id="retomada", user_message="Podemos continuar?")
        self.assertIn(f"FICHA EM FOCO: prato #{pid}", resposta["context"])
        self.assertIn("não peça o nome", resposta["context"])
        self.assertEqual(self.store.pratos(), antes)
        self.assertEqual(self.store.analises_preco(), [])
        self.assertFalse(pesquisa.pesquisando(SESSAO))

    def test_botao_de_aceite_explicito_avanca_e_retomada_nao_repete_aceite(self):
        pid, _, _ = self.ficha()
        antiga = f"Sobre o prato escolhido #{pid} (Batata assada da casa): Gostei da versão e não vejo impedimentos."
        falas.ao_iniciar_turno(session_id=SESSAO, user_message=antiga)
        painel.ao_iniciar_turno(session_id=SESSAO, turn_id="cta-antigo", user_message=antiga)
        self.assertEqual(self.store.analises_preco(), [])
        aceite = f"Aceito preparar o prato #{pid} (Batata assada da casa) nessa versão, sem impedimentos. Confira a viabilidade e mostre os cenários para eu escolher o preço."
        falas.ao_iniciar_turno(session_id=SESSAO, user_message=aceite)
        painel.ao_iniciar_turno(session_id=SESSAO, turn_id="cta-direto", user_message=aceite)
        self.assertEqual(len(self.store.analises_preco()), 1)
        self.assertEqual(jornada.resumo(self.store)["etapa"], "preco")
        self.assertEqual([r["quantidade"] for r in self.store.reservas_do_prato(pid)], ["200 g"])
        self.assertTrue(self.store.carrinho_itens()[0]["ja_tem"])
        self.assertEqual(self.store.orcamento()["saldo"], 80)
        retomada = painel.texto_do_painel(self.store, mensagem="Podemos continuar?")
        self.assertIn("Não peça o aceite novamente", retomada)
        self.assertIn("peça somente a escolha do preço", retomada)

    def test_api_jornada_multipla_conduz_primeira_ficha_sem_aceitar_por_ela(self):
        pid, _, _ = self.ficha()
        self.store.selecionar_pratos([{"nome": "Segundo prato"}])
        antes = self.store.pratos()
        estado = api.estado(self.store)
        self.assertEqual(estado["jornada"]["etapa"], "aceite")
        self.assertEqual(estado["jornada"]["prato_id"], pid)
        self.assertEqual(len(estado["pratos_escolhidos"]), 2)
        self.assertEqual(jornada.ficha_para_retomar(self.store)["id"], pid)
        self.assertEqual(self.store.pratos(), antes)
        self.assertEqual(self.store.analises_preco(), [])
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertTrue(all(p["preco_venda"] is None for p in self.store.pratos()))

    def test_aprovado_com_impedimento_atual_entra_em_revisao_sem_mudar_reservas(self):
        pid, rid, dados = self.ficha()
        receita = self.store.obter_receita(rid)
        aval = tools._avaliar_prato(self.store, dados["ingredientes"], receita)
        self.store.salvar_prato(receita["nome"], rid, 2, aval["resultado"]["cmv_total"], 1.0, dados, prato_id=pid)
        self.store.aceitar_prato(pid, 8.0, [], tools._reservas_de(aval["resultado"]))
        self.store.perfil_salvar({"equipamentos": {"forno": False}})
        antes = self.store.pratos(), self.store.reservas_ativas(), self.store.orcamento()
        estado = api.estado(self.store)
        self.assertEqual(estado["jornada"]["etapa"], "revisao")
        self.assertEqual(estado["pratos_escolhidos"][0]["status"], "aceito")
        self.assertEqual(estado["pratos_escolhidos"][0]["preco_venda"], 8.0)
        self.assertTrue(estado["pratos_escolhidos"][0]["bloqueios"])
        self.assertEqual((self.store.pratos(), self.store.reservas_ativas(), self.store.orcamento()), antes)

    def test_pergunta_clicavel_somente_gera_fato_especifico_a_confirmar(self):
        pid, _, _ = self.ficha()
        self.store.perfil_salvar({"equipamentos": {"forno": None}})
        resumo = jornada.resumo(self.store)
        pergunta = resumo["pergunta"]
        self.assertIn("forno", pergunta["texto"])
        self.assertEqual([p["rotulo"] for p in pergunta["opcoes"]], ["Tenho", "Não tenho"])
        self.assertEqual(pergunta["opcoes"][0]["mensagem"], f"Tenho forno funcionando. Continue o prato #{pid}.")
        self.assertNotIn("Aceito", str(pergunta))
        self.assertIsNone(self.store.perfil()["equipamentos"]["forno"])

    def test_sim_apos_registro_oferecer_proxima_receita_inicia_so_a_pesquisa(self):
        pid, rid, dados = self.ficha()
        receita = self.store.obter_receita(rid)
        aval = tools._avaliar_prato(self.store, dados["ingredientes"], receita)
        self.store.salvar_prato(receita["nome"], rid, 2, aval["resultado"]["cmv_total"], 1.0, dados, prato_id=pid)
        self.store.aceitar_prato(pid, 8.0, [], tools._reservas_de(aval["resultado"]))
        pergunta = (f"Registrei o prato #{pid}, **Batata assada da casa**, a R$ 8,00.\n"
                    "Quer que eu pesquise a próxima opção com o estoque disponível?")
        antes = self.store.pratos(), self.store.reservas_ativas(), self.store.orcamento()
        contexto = painel.ao_iniciar_turno(session_id=SESSAO, turn_id="proxima",
            conversation_history=[{"role": "assistant", "content": pergunta}], user_message="Sim")["context"]
        self.assertIn("MONTAR UM PRATO PARA O CARDÁPIO", contexto)
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "despensa_listar")
        self.assertEqual((self.store.pratos(), self.store.reservas_ativas(), self.store.orcamento()), antes)
        for resposta, anterior in [("Não", pergunta), ("Sim, mas não pesquise", pergunta),
                                   ("Sim", "Você tem forno?"), ("Sim", pergunta.replace(f"#{pid}", f"#{pid} e #99")),
                                   ("Sim", "")]:
            self.assertFalse(jornada.aceitou_proxima_pesquisa(resposta, anterior))
        contexto = painel.ao_iniciar_turno(session_id="outra-conversa", user_message="Sim")["context"]
        self.assertNotIn("MONTAR UM PRATO PARA O CARDÁPIO", contexto)

    def test_pedido_explicito_de_proximo_prato_arma_cadeia_da_consultora(self):
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        contexto = painel.ao_iniciar_turno(session_id=SESSAO,
            user_message="Pesquise e monte mais um prato para meu cardápio com o estoque disponível.")["context"]
        self.assertIn("MONTAR UM PRATO PARA O CARDÁPIO", contexto)
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "despensa_listar")

    def test_pergunta_paralela_nao_dispara_pesquisa_conduzida(self):
        resultado = painel.ao_iniciar_turno(session_id=SESSAO, turn_id="duvida", user_message="Qual é o saldo?")
        self.assertNotIn("MONTAR UM PRATO PARA O CARDÁPIO", resultado["context"])
        self.assertNotIn("Conduza a pesquisa de uma receita", resultado["context"])
        self.assertFalse(pesquisa.pesquisando(SESSAO))

    def test_consultar_despensa_nao_permite_reset_mesmo_se_modelo_enviar_flag(self):
        self.store.ensure_loaded()
        with self.store.connect() as conn:
            conn.execute("UPDATE ingredientes SET estoque_base = 0.123, preco_total = 19.99 WHERE id = (SELECT min(id) FROM ingredientes)")
        antes = self.store.ingredientes()
        with patch.object(Store, "ensure_loaded", side_effect=AssertionError("não pode importar")):
            retorno = json.loads(tools.despensa_listar({"recarregar": True}))
        self.assertFalse(retorno["success"])
        self.assertTrue(retorno["bloqueado"])
        self.assertEqual(self.store.ingredientes(), antes)
        self.assertNotIn("recarregar", tools.SCHEMAS["despensa_listar"]["parameters"]["properties"])
        self.assertFalse(tools.SCHEMAS["despensa_listar"]["parameters"]["additionalProperties"])
        self.assertTrue(json.loads(tools.despensa_listar({"recarregar": False}))["success"])
        self.assertEqual(self.store.ingredientes(), antes)
