"""Uma pergunta sobre ficha salva deve receber os dados atuais dessa ficha."""

from unittest.mock import patch

from sabor_da_maria import cozinha, falas, painel, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


class FocoPersistidoTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        equipamentos = {i.id: True for i in cozinha.CATALOGO if i.grupo in (cozinha.EQUIPAMENTO, cozinha.UTENSILIO)}
        equipamentos["fogao"] = 4
        self.store.perfil_salvar({"equipamentos": equipamentos,
                                 "tecnicas": {i.id: "sim" for i in cozinha.CATALOGO if i.grupo == cozinha.HABILIDADE},
                                 "restricoes": {"espaco_geladeira": "bastante", "tempo_por_cozinhada_min": 120,
                                                "gas": "botijão", "energia": "estável"}})

    def ficha(self, nome, ingredientes):
        prato, = self.store.selecionar_pratos([{"nome": nome}])
        dados = {"ingredientes": ingredientes, "tempo_preparo_min": 30,
                 "equipamentos_necessarios": ["forno"], "tecnicas_necessarias": ["assar"],
                 "veredito": "viavel"}  # A avaliação salva pode estar desatualizada.
        receita_id = self.store.salvar_receita(nome, None, 4, dados, "conferida", prato_id=prato["id"])
        return prato["id"], receita_id

    def contexto(self, mensagem):
        return painel.texto_do_painel(self.store, mensagem=mensagem)

    def estado(self):
        return {"pratos": self.store.pratos(), "receitas": [self.store.obter_receita(r["id"]) for r in self.store.receitas()],
                "perfil": self.store.perfil(), "orcamento": self.store.orcamento(),
                "reservas": self.store.reservas_ativas(), "analises": self.store.analises_preco()}

    def test_embalagem_sem_conteudo_relembra_ficha_e_pergunta_somente_medida(self):
        prato_id, receita_id = self.ficha("Cobertura para sobremesa", [
            {"nome": "Cobertura de chocolate", "quantidade": 100, "unidade": "g"}])
        antes = self.estado()
        with patch.object(self.store, "obter_receita", wraps=self.store.obter_receita) as ler_receita:
            contexto = self.contexto(f"Confirme o que falta antes de aprovar o prato #{prato_id}.")
        ler_receita.assert_called_once_with(receita_id)
        for dado in ("FICHA EM FOCO", f"receita_id={receita_id}", "100 g de Cobertura de chocolate",
                     "4 porções", "30 min", "precisa_confirmar", "forno", "assar", "peso", "embalagem"):
            self.assertIn(dado, contexto)
        self.assertIn("não peça o nome, a receita ou a lista novamente", contexto)
        self.assertIn("pergunte apenas o dado essencial", contexto)
        self.assertNotIn("# Analytics: preço depois do aceite", contexto)
        self.assertEqual(self.estado(), antes)

    def test_orcamento_da_ficha_explicita_100_reais_de_compras_e_80_disponiveis(self):
        prato_id, _ = self.ficha("Creme da casa", [
            {"nome": "Ingrediente complementar", "quantidade": 1, "unidade": "kg", "item_despensa": "",
             "preco_compra_estimado": 100, "quantidade_compra": 1, "unidade_compra": "kg"}])
        antes = self.estado()
        contexto = self.contexto(f"Confira as pendências do prato #{prato_id}.")
        self.assertIn("inviavel_por_enquanto", contexto)
        self.assertIn("Compras desta ficha: R$ 100,00; orçamento disponível para ela: R$ 80,00", contexto)
        self.assertIn("não cabem", contexto)
        self.assertEqual(self.estado(), antes)

    def test_hook_do_pedido_real_de_conferencia_injeta_a_ficha_sem_aprovar(self):
        prato_id, _ = self.ficha("Cobertura de chocolate da casa", [
            {"nome": "Cobertura de chocolate", "quantidade": 100, "unidade": "g"}])
        mensagem = (f"Quero preparar o prato #{prato_id}, Cobertura de chocolate da casa. "
                    "Confirme o que falta antes de aprovar. Não compre nem reserve nada.")
        antes = self.estado()
        falas.ao_iniciar_turno(session_id=SESSAO, user_message=mensagem)
        resultado = painel.ao_iniciar_turno(session_id=SESSAO, turn_id="conferencia-focal", user_message=mensagem)
        self.assertIn("FICHA EM FOCO", resultado["context"])
        self.assertIn("qual é o peso ou volume de uma embalagem", resultado["context"])
        self.assertEqual(self.estado(), antes)

    def test_nome_completo_ou_id_focam_apenas_a_receita_indicada(self):
        alvo, receita = self.ficha("Frango da casa", [{"nome": "Peito de frango", "quantidade": 100, "unidade": "g"}])
        self.ficha("Arroz especial", [{"nome": "Arroz branco tipo 1", "quantidade": 100, "unidade": "g"}])
        with patch.object(self.store, "obter_receita", wraps=self.store.obter_receita) as ler_receita:
            contexto = self.contexto("Confira a ficha de «Frango da casa».")
        ler_receita.assert_called_once_with(receita)
        self.assertIn(f"FICHA EM FOCO: prato #{alvo}, Frango da casa", contexto)
        self.assertNotIn("100 g de Arroz branco tipo 1", contexto)

    def test_retomada_do_botao_mantem_foco_sem_reiniciar_pesquisa(self):
        prato_id, _ = self.ficha("Cobertura para sobremesa", [
            {"nome": "Cobertura de chocolate", "quantidade": 100, "unidade": "g"}])
        mensagem = f"Quero continuar o prato escolhido #{prato_id} (Cobertura para sobremesa)."
        falas.ao_iniciar_turno(session_id=SESSAO, user_message=mensagem)
        resultado = painel.ao_iniciar_turno(session_id=SESSAO, turn_id="retomada-focal", user_message=mensagem)
        self.assertIn("FICHA EM FOCO", resultado["context"])
        self.assertIn("qual é o peso ou volume de uma embalagem", resultado["context"])
        self.assertNotIn("PESQUISA DE RECEITAS", resultado["context"])

    def test_sem_alvo_ou_multiplas_fichas_nao_avaliam_todas_as_receitas(self):
        primeiro, _ = self.ficha("Prato um", [{"nome": "Sal", "quantidade": 1, "unidade": "g"}])
        segundo, _ = self.ficha("Prato dois", [{"nome": "Sal", "quantidade": 1, "unidade": "g"}])
        with patch.object(painel, "_avaliar_prato") as avaliar:
            self.assertNotIn("FICHA EM FOCO", self.contexto("Tudo bem?"))
            varios = self.contexto(f"Confira #{primeiro} e #{segundo}.")
        avaliar.assert_not_called()
        self.assertIn("Fichas mencionadas", varios)
        self.assertIn("qual ficha quer revisar primeiro", varios)

    def test_recalcula_rotina_alterada_e_ignora_reserva_do_proprio_prato_aceito(self):
        ingredientes = [{"nome": "Peito de frango", "quantidade": 2, "unidade": "kg"}]
        prato_id, receita_id = self.ficha("Frango da casa", ingredientes)
        resultado = tools._resolver_ingredientes(self.store, ingredientes)
        self.store.salvar_prato("Frango da casa", receita_id, 4, 28, 7,
                                {"ingredientes": ingredientes, "resultado": resultado}, prato_id=prato_id)
        self.store.aceitar_prato(prato_id, 10, [], tools._reservas_de(resultado))
        antes = self.estado()
        contexto = self.contexto(f"Confira a ficha #{prato_id}.")
        self.assertIn("Veredito ATUAL desta ficha: viavel", contexto)
        self.assertIn("Compras desta ficha: R$ 0,00", contexto)
        self.assertEqual(self.estado(), antes)
        self.store.perfil_salvar({"restricoes": {"tempo_por_cozinhada_min": 5}})
        contexto = self.contexto(f"Confira #{prato_id}.")
        self.assertIn("inviavel_por_enquanto", contexto)
        self.assertIn("30 min", contexto)
        self.assertIn("5 min", contexto)
