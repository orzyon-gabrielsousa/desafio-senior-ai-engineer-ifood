"""Tempos explícitos do preparo ajudam a orientar a ficha sem virar o total."""

import json
import unittest

from sabor_da_maria import falas, fonte, jornada, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


PREPARO = ("Corte e tempere o frango e as batatas. "
           "Leve ao forno pré-aquecido a 200 graus e deixe assar por 25 minutos. "
           "Enquanto isso, prepare o creme. Adicione o creme por cima do frango. "
           "Leve ao forno novamente pré-aquecido a 200 graus e deixe assar por +15 minutos.")
FONTE = {
    "nome": "Frango com Batatas na Marmita",
    "url": "https://aquelareceita.com.br/frango-com-batatas-na-marmita",
    "porcoes": 3,
    "ingredientes": ["400 g de peito de frango", "300 g de batata"],
    "modo_preparo": PREPARO,
}


class TemposExplicitosTests(unittest.TestCase):
    def test_encadeia_as_etapas_de_forno_sem_somar_preparo_paralelo(self):
        self.assertEqual(fonte.tempo_minimo_de_forno(PREPARO), 40)
        self.assertEqual(fonte.tempo_minimo_de_forno("Asse no forno por uma hora."), None)
        self.assertEqual(fonte.tempo_minimo_de_forno("Asse no forno por 1,5 horas."), 90)

    def test_so_conta_acao_de_forno_com_tempo_obrigatorio(self):
        for etapa in ("Deixe o forno pré-aquecer por 20 minutos.",
                      "Deixe descansar por 60 minutos antes de levar ao forno.",
                      "Antes de assar no forno, deixe descansar por 50 minutos.",
                      "Se usar o forno, asse por 30 minutos.",
                      "Asse no forno ou na air fryer por 30 minutos.",
                      "Retire do forno e deixe esfriar por 20 minutos."):
            with self.subTest(etapa=etapa):
                self.assertIsNone(fonte.tempo_minimo_de_forno(etapa))
        self.assertEqual(fonte.tempo_minimo_de_forno("Asse a base no forno por 25 minutos. "
                                                   "Asse o acompanhamento no forno por 15 minutos."), 25)


class TempoDoPreparoNaFichaTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.perfil_salvar({
            "equipamentos": {"forno": False, "fogao": 4, "frigideira": True},
            "tecnicas": {"refogar": "sim"},
            "restricoes": {"tempo_por_cozinhada_min": 30, "gas": "botijão",
                           "energia": "estável", "espaco_geladeira": "bastante"},
        })
        self.prato, = self.store.selecionar_pratos([{"nome": FONTE["nome"], "fonte_url": FONTE["url"]}])
        self.ingredientes = fonte.ingredientes_publicados(FONTE)

    def salvar_original(self, fonte_verificada=None, **extras):
        return self.store.salvar_receita(FONTE["nome"], FONTE["url"], FONTE["porcoes"], {
            "ingredientes": self.ingredientes, "ficha_original_da_fonte": True,
            "fonte_verificada": fonte_verificada or FONTE, "modo_preparo": PREPARO, **extras,
        }, "conferida", prato_id=self.prato["id"])

    def conferir(self, **args):
        resultado = json.loads(tools.receita_conferir_ingredientes(
            {"prato_id": self.prato["id"], **args}, session_id=SESSAO))
        self.assertTrue(resultado["success"], resultado)
        return resultado

    def test_original_com_quarenta_minutos_de_forno_nao_repete_pergunta_de_duracao(self):
        for tempo in (None, 30):
            self.salvar_original(tempo_preparo_min=tempo)
            prato = jornada.pratos_atuais(self.store)[0]
            self.assertEqual(prato["veredito"], "inviavel_por_enquanto")
            self.assertEqual(prato["bloqueios"][0]["tipo"], "tempo_insuficiente")
            self.assertIn("pelo menos 40 min de forno", prato["bloqueios"][0]["detalhe"])
            self.assertNotIn("tempo_da_receita_pendente", {b["tipo"] for b in prato["bloqueios"]})
            self.assertEqual(self.store.reservas_ativas(), [])
        self.store.perfil_salvar({"restricoes": {"tempo_por_cozinhada_min": 60}})
        self.salvar_original()
        tipos = {b["tipo"] for b in jornada.pratos_atuais(self.store)[0]["bloqueios"]}
        self.assertIn("tempo_da_receita_pendente", tipos)
        self.assertNotIn("tempo_insuficiente", tipos)

    def test_reconferencia_preserva_estimativa_declarada_e_perguntas_esgotadas(self):
        self.salvar_original(tempo_preparo_min=52, tempo_preparo_origem="estimativa_declarada",
                             perguntas_esgotadas=["Uma pergunta que já fizemos?"])
        ficha = self.conferir(tempo_preparo_min=55, tempo_preparo_base="40 min de forno e 15 min de montagem.")
        dados = self.store.obter_receita(ficha["receita_id"])["dados"]
        self.assertEqual(dados["tempo_preparo_min"], 52)
        self.assertEqual(dados["tempo_preparo_origem"], "estimativa_declarada")
        self.assertEqual(dados["perguntas_esgotadas"], ["Uma pergunta que já fizemos?"])

    def test_fonte_publicada_tem_preferencia_e_lacuna_pode_usar_estimativa_dos_passos(self):
        self.salvar_original()
        base = "40 min de forno e cerca de 10 min de montagem."
        ficha = self.conferir(tempo_preparo_min=50, tempo_preparo_base=base)
        self.assertEqual(ficha["tempo_preparo_base"], base)
        self.assertEqual((ficha["tempo_preparo_min"], ficha["tempo_preparo_origem"]), (50, "estimativa_dos_passos"))
        self.assertEqual(self.conferir()["tempo_preparo_min"], 50)
        self.salvar_original(fonte_verificada={**FONTE, "tempo_preparo_min": 55})
        ficha = self.conferir(tempo_preparo_min=50, tempo_preparo_base=base)
        self.assertEqual((ficha["tempo_preparo_min"], ficha["tempo_preparo_origem"]), (55, "fonte_publicada"))
        self.assertIsNone(ficha["tempo_preparo_base"])

    def test_tempo_declarado_menor_que_o_minimo_pode_ser_corrigido_com_evidencia(self):
        self.salvar_original(tempo_preparo_min=30, tempo_preparo_origem="estimativa_declarada")
        ficha = self.conferir(tempo_preparo_min=50, tempo_preparo_base="40 min de forno e 10 de montagem.")
        self.assertEqual((ficha["tempo_preparo_min"], ficha["tempo_preparo_origem"]), (50, "estimativa_dos_passos"))
        self.assertEqual(ficha["bloqueios"][0]["tipo"], "tempo_insuficiente")

    def test_adaptacao_na_frigideira_tem_o_proprio_tempo_e_requisitos(self):
        self.salvar_original(fonte_verificada={**FONTE, "tempo_preparo_min": 50}, tempo_preparo_min=50,
                             tempo_preparo_origem="fonte_publicada", perguntas_esgotadas=["Tempo?"])
        mudancas = {"adaptacao": "Versão de frigideira em lote pequeno.",
                    "modo_preparo": "Refogue o frango na frigideira e cozinhe a batata em cubos no fogo médio, sem forno.",
                    "ingredientes": self.ingredientes}
        ficha = self.conferir(**mudancas)
        self.assertIsNone(ficha["tempo_preparo_min"])
        self.assertIn("tempo_da_receita_pendente", {b["tipo"] for b in ficha["bloqueios"]})
        ficha = self.conferir(**mudancas, tempo_preparo_min=25,
                              tempo_preparo_base="Cinco minutos para cortar e 20 no fogão.")
        self.assertEqual(ficha["veredito"], "viavel", ficha)
        self.assertIn("frigideira", ficha["equipamentos"]["ok"])
        dados = self.store.obter_receita(ficha["receita_id"])["dados"]
        self.assertNotIn("forno", dados["equipamentos_necessarios"])
        self.assertNotIn("perguntas_esgotadas", dados)
        self.assertEqual(jornada.pratos_atuais(self.store)[0]["veredito"], "viavel")
        self.assertEqual(self.store.reservas_ativas(), [])

    def test_reconferencia_por_id_ou_nome_preserva_fonte_verificada_de_outra_sessao(self):
        original = self.salvar_original(sessao_da_conferencia="outro-chat")
        outra_url = "https://receitas.exemplo/outra-marmita"
        for alvo in ({"prato_id": self.prato["id"]}, {"nome": self.prato["nome"]}):
            with self.subTest(alvo=alvo):
                retorno = json.loads(tools.receita_conferir_ingredientes({
                    **alvo, "fonte_url": outra_url, "ingredientes": self.ingredientes,
                }, session_id=SESSAO))
                self.assertFalse(retorno["success"], retorno)
                self.assertIn(FONTE["url"], retorno["error"])
                self.assertEqual(self.store.obter_prato(self.prato["id"])["receita_id"], original)
        outra_ficha = json.loads(tools.receita_conferir_ingredientes({
            "nome": "Outra marmita", "fonte_url": outra_url, "ingredientes": self.ingredientes,
        }, session_id=SESSAO))
        self.assertTrue(outra_ficha["success"], outra_ficha)
        self.assertEqual(self.store.obter_prato(self.prato["id"])["receita_id"], original)

    def test_mudar_fonte_exige_leitura_e_pedido_explicito(self):
        self.salvar_original()
        nova = {**FONTE, "url": "https://receitas.exemplo/outra-marmita", "tempo_preparo_min": 55}
        args = {"nome": self.prato["nome"], "fonte_url": nova["url"], "ingredientes": self.ingredientes}
        falas.registrar(SESSAO, [], "Troque a fonte do prato.")
        antes_da_leitura = json.loads(tools.receita_conferir_ingredientes(args, session_id=SESSAO))
        self.assertFalse(antes_da_leitura["success"])
        pesquisa.depois_da_ferramenta(tool_name="web_extract", result=json.dumps({"results": [{
            "url": nova["url"], "title": nova["nome"], "receitas": [nova],
        }]}), session_id=SESSAO)
        falas.registrar(SESSAO, [], "Confirme o tempo desse prato.")
        antes_do_pedido = json.loads(tools.receita_conferir_ingredientes(args, session_id=SESSAO))
        self.assertFalse(antes_do_pedido["success"])
        falas.registrar(SESSAO, [], "Troque a fonte do prato.")
        ficha = json.loads(tools.receita_conferir_ingredientes(args, session_id=SESSAO))
        self.assertTrue(ficha["success"], ficha)
        self.assertEqual(ficha["fonte_verificada"], nova)
