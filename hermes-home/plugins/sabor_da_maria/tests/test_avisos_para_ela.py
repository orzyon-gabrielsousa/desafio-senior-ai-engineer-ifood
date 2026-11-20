"""Os avisos de bloqueio são lidos pela Dona Maria no cartão do chat, no painel lateral e nas respostas prontas.

Eles dizem o problema falando com ela. "Dona Maria tem 30 min", "ela ainda não disse se tem" e "adapte o preparo ou
escolha outra receita" eram frases para o modelo: o que fazer com cada bloqueio ele recebe nos próximos passos da
conferência e no painel do turno.
"""

import re

from sabor_da_maria import cozinha, fonte, jornada, montagem, painel, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase

PREPARO = ("Corte e tempere o frango e as batatas. Leve ao forno pré-aquecido a 200 graus e deixe assar por 25 minutos. "
           "Adicione o creme por cima do frango. Leve ao forno novamente e deixe assar por +15 minutos.")
FONTE = {"nome": "Frango com Batatas na Marmita", "url": "https://aquelareceita.com.br/frango-com-batatas-na-marmita",
         "porcoes": 3, "ingredientes": ["400 g de peito de frango", "300 g de batata"], "modo_preparo": PREPARO}
PARA_O_MODELO = re.compile(r"Dona Maria|\b[Aa]dapte\b|\bescolha outra\b|~")
SEM_EQUIPAMENTO = {"ok": [], "faltando": [], "nao_informado": []}


class AvisoDeTempoTests(StoreTestCase):
    def aviso(self, restricoes, tempo, forno=None):
        operacao = tools._restricoes_relevantes({"restricoes": restricoes}, tempo, SEM_EQUIPAMENTO, [], tempo_minimo_de_forno=forno)
        return [b["detalhe"] for b in operacao["impedimentos"] if b["tipo"] == "tempo_insuficiente"]

    def test_as_tres_formas_do_aviso_falam_com_ela(self):
        menos_de_30 = {"tempo_por_cozinhada_faixa": {"operador": "menor_que", "minutos": 30}}
        casos = (
            (self.aviso({"tempo_por_cozinhada_min": 30}, 55), "A receita leva cerca de 55 min e você disse ter 30 min por cozinhada."),
            (self.aviso(menos_de_30, 45), "A receita leva cerca de 45 min e você disse ter menos de 30 min por cozinhada."),
            (self.aviso({"tempo_por_cozinhada_min": 30}, None, forno=40),
             "A receita precisa de pelo menos 40 min de forno e você disse ter 30 min por cozinhada."),
            (self.aviso(menos_de_30, None, forno=40),
             "A receita precisa de pelo menos 40 min de forno e você disse ter menos de 30 min por cozinhada."),
        )
        for avisos, esperado in casos:
            self.assertEqual(avisos, [esperado])
            self.assertNotRegex(esperado, PARA_O_MODELO)

    def test_cartao_painel_e_resposta_pronta_mostram_a_mesma_frase(self):
        # O estado em que o teste dele parou: 30 min por cozinhada e uma receita com 40 min de forno.
        store = Store()
        store.perfil_salvar({"equipamentos": {"forno": True, "forma": True}, "tecnicas": {"assar": "sim"},
                             "restricoes": {"tempo_por_cozinhada_min": 30, "gas": "botijão", "energia": "estável",
                                            "espaco_geladeira": "bastante"}})
        prato, = store.selecionar_pratos([{"nome": FONTE["nome"], "fonte_url": FONTE["url"]}])
        receita_id = store.salvar_receita(FONTE["nome"], FONTE["url"], FONTE["porcoes"], {
            "ingredientes": fonte.ingredientes_publicados(FONTE), "ficha_original_da_fonte": True,
            "fonte_verificada": FONTE, "modo_preparo": PREPARO}, "conferida", prato_id=prato["id"])
        frase = "A receita precisa de pelo menos 40 min de forno e você disse ter 30 min por cozinhada."

        atual = jornada.resumo(store)
        self.assertEqual((atual["etapa"], atual["descricao"]), ("pendencias", frase))  # painel lateral
        ficha = next(p for p in jornada.pratos_atuais(store) if p["id"] == prato["id"])
        self.assertEqual(ficha["bloqueios"][0], {"tipo": "tempo_insuficiente", "detalhe": frase})  # cartão do chat

        montagem.iniciar_turno(SESSAO, "revisao")
        painel._pergunta_da_retomada(store, store.obter_prato(prato["id"]), store.obter_receita(receita_id), SESSAO)
        resposta = montagem.resposta_preparada(SESSAO, "revisao")
        self.assertEqual(resposta.split("\n\n"), [
            f"Revisei **{FONTE['nome']}** (prato #{prato['id']}).", frase,
            "Posso buscar uma versão mais rápida com os ingredientes principais e um preparo que dispense o forno. "
            "Quer que eu procure essa versão mais rápida?"])
        self.assertNotRegex(resposta, PARA_O_MODELO)
        self.assertEqual(resposta.count("?"), 1)
        # A orientação continua chegando ao modelo, fora do que ela lê.
        aval = tools._avaliar_prato(store, fonte.ingredientes_publicados(FONTE), store.obter_receita(receita_id))
        self.assertTrue(any("proponha saída" in passo for passo in aval["proximos_passos"]))


# Terceira pessoa, ordem ao modelo e nome de campo ou de ferramenta: nada disso é fala para ela.
NAO_E_FALA_PARA_ELA = re.compile(r"Dona Maria|\b[Ee]la\b|\b(?:confirme|adapte|resolva|reduza|informe|consulte|confira|pesquise|proponha)\b|"
                                 r"modo_preparo|adaptacao\b|perfil_cozinha|_salvar|~")


class AvisosDaFichaTests(StoreTestCase):
    def avaliar(self, perfil, **dados):
        store = Store()
        store.perfil_salvar(perfil)
        receita = {"nome": "Frango assado", "porcoes": 4, "dados": {"tempo_preparo_min": dados.pop("tempo", 50), **dados}}
        return tools._avaliar_prato(store, [{"nome": "Peito de frango", "quantidade": 500, "unidade": "g"}], receita)

    def test_avisos_de_cozinha_e_operacao_falam_com_ela(self):
        situacoes = {
            "cozinha vazia": self.avaliar({}, equipamentos_necessarios=["forno", "forma"], tecnicas_necessarias=["assar"], tempo=None),
            "ela disse que não tem": self.avaliar(
                {"equipamentos": {"forno": False, "forma": True}, "tecnicas": {"assar": "nao", "molhos": "aprendendo"},
                 "restricoes": {"tempo_por_cozinhada_min": 120, "gas": "acabou", "energia": "sem energia", "espaco_geladeira": "lotada"}},
                equipamentos_necessarios=["forno a gás", "forma", "liquidificador"], tecnicas_necessarias=["assar", "molho branco"]),
            "instável e pouco espaço": self.avaliar(
                {"equipamentos": {"forno": True, "forma": True, "fogao": 4}, "tecnicas": {"assar": "sim"},
                 "restricoes": {"tempo_por_cozinhada_min": 120, "gas": "falta às vezes", "energia": "cai às vezes", "espaco_geladeira": "pouco"}},
                equipamentos_necessarios=["forno", "forma"], tecnicas_necessarias=["assar"]),
        }
        vistos = {}
        for nome, aval in situacoes.items():
            for bloqueio in aval["bloqueios"]:
                with self.subTest(situacao=nome, tipo=bloqueio["tipo"]):
                    self.assertNotRegex(bloqueio["detalhe"], NAO_E_FALA_PARA_ELA)
                vistos.setdefault(bloqueio["tipo"], bloqueio["detalhe"])
        self.assertEqual({tipo: vistos[tipo] for tipo in (
            "tempo_da_receita_pendente", "energia_indisponivel", "energia_a_confirmar", "gas_indisponivel", "gas_a_confirmar",
            "refrigeracao_insuficiente", "equipamento_faltando", "tecnica_faltando", "equipamento_nao_informado",
            "tecnica_nao_informada", "tecnica_parcial")}, {
            "tempo_da_receita_pendente": "Falta o tempo total de preparo desta versão.",
            "energia_indisponivel": "A receita usa liquidificador e refrigeração dos ingredientes, e você disse que não tem energia disponível.",
            "energia_a_confirmar": "A receita usa refrigeração dos ingredientes, e a energia não está garantida para este preparo.",
            "gas_indisponivel": "A receita usa equipamento a gás e você disse que o gás não está disponível.",
            "gas_a_confirmar": "O gás não está garantido para o fogão ou o forno desta receita.",
            "refrigeracao_insuficiente": "A receita precisa refrigerar ingredientes ou o lote pronto, e você disse que não há espaço na geladeira.",
            "equipamento_faltando": "forno a gás: você disse que não tem",
            "tecnica_faltando": "assar: você disse que não domina",
            "equipamento_nao_informado": "forno: você ainda não disse se tem",
            "tecnica_nao_informada": "assar: você ainda não disse se domina",
            "tecnica_parcial": "molho branco: você disse que domina só em parte",
        })
        pouco = next(b["detalhe"] for b in situacoes["instável e pouco espaço"]["bloqueios"] if b["tipo"] == "refrigeracao_a_confirmar")
        self.assertEqual(pouco, "Você disse que há pouco espaço na geladeira, e este lote precisa de refrigeração.")

    def test_o_nome_antes_dos_dois_pontos_continua_virando_pergunta(self):
        aval = self.avaliar({"restricoes": {"tempo_por_cozinhada_min": 120, "gas": "botijão", "energia": "estável", "espaco_geladeira": "bastante"}},
                            equipamentos_necessarios=["forno"], tecnicas_necessarias=["assar"])
        perguntas = {b["tipo"]: jornada._pergunta({"id": 7}, [b]) for b in aval["bloqueios"]}
        self.assertEqual(perguntas["equipamento_nao_informado"]["texto"], "Você tem forno funcionando para este preparo?")
        self.assertEqual(perguntas["tecnica_nao_informada"]["texto"], "Você sabe assar neste preparo?")
        # O painel reconhece o equipamento do aviso para saber se a declaração dela tem a ver com esta ficha.
        forno = next(b for b in aval["bloqueios"] if b["tipo"] == "equipamento_nao_informado")
        self.assertTrue(painel._bloqueio_relacionado(forno, {"equipamentos": {"forno": False}}))
        self.assertFalse(painel._bloqueio_relacionado(forno, {"equipamentos": {"liquidificador": False}}))
        self.assertEqual(cozinha.id_por_termos(forno["detalhe"], (cozinha.EQUIPAMENTO, cozinha.UTENSILIO)), "forno")

    def test_a_orientacao_continua_chegando_ao_modelo(self):
        aval = self.avaliar({"equipamentos": {"forno": False}, "tecnicas": {"assar": "aprendendo"},
                             "restricoes": {"tempo_por_cozinhada_min": 120, "gas": "falta às vezes", "energia": "estável", "espaco_geladeira": "bastante"}},
                            equipamentos_necessarios=["forno", "liquidificador"], tecnicas_necessarias=["assar"])
        passos = " | ".join(aval["proximos_passos"])
        for trecho in ("proponha saída", "confirmacoes_operacionais", "perfil_cozinha_salvar"):
            self.assertIn(trecho, passos)
        conferencia = {"adaptacao": "sem o creme", "diferencas": ["retirado da fonte: creme"], "bloqueios": []}
        tools._conferir_preparo_adaptado(conferencia, "", "Asse o frango.")
        self.assertEqual(conferencia["bloqueios"][0]["detalhe"], "A versão adaptada muda ingredientes e ainda não tem um modo de preparo próprio.")
        tools._avaliar_fonte(aval, conferencia)
        self.assertIn("modo_preparo próprio", aval["proximos_passos"][0])

    def test_preparo_adaptado_que_ainda_pede_omitido_bloqueia_aceite(self):
        conferencia = {"adaptacao": "Pizza sem fermento", "diferencas": ["retirado da fonte: fermento"], "bloqueios": []}
        teste = "Misture a farinha e adicione fermento biológico seco. Deixe crescer."
        tools._conferir_preparo_adaptado(conferencia, teste, "Misture os ingredientes.", omitidos=["Fermento biológico seco"])
        self.assertEqual(conferencia["bloqueios"][0]["tipo"], "preparo_adaptacao_pendente")
        conferencia["bloqueios"].clear()
        tools._conferir_preparo_adaptado(conferencia, "Misture a massa sem fermento biológico seco.",
                                       "Misture os ingredientes.", omitidos=["Fermento biológico seco"])
        self.assertEqual(conferencia["bloqueios"], [])

    def test_avisos_de_ingrediente_dizem_o_problema_e_o_modelo_recebe_o_que_fazer(self):
        store = Store()
        resolvido = tools._resolver_ingredientes(store, [
            {"nome": "sal", "quantidade": 5, "unidade": "g"},
            {"nome": "sal", "quantidade": 2, "unidade": "g"},                                                   # a fonte repete o ingrediente
            {"nome": "coxinha da asa de frango", "item_despensa": "Peito de frango", "quantidade": 500, "unidade": "g"},  # outro corte
            {"nome": "quiabo", "item_despensa": "Peito de frango", "quantidade": 300, "unidade": "g"}])        # não é o mesmo ingrediente
        self.assertEqual(resolvido["pendencias"], [
            "sal: aparece em mais de uma linha da receita, e as quantidades precisam ser somadas",
            "coxinha da asa de frango: Peito de frango é outro corte, diferente do que a receita pede",
            "quiabo: Peito de frango não corresponde ao ingrediente da receita"])
        for texto in resolvido["pendencias"]:
            self.assertNotRegex(texto, NAO_E_FALA_PARA_ELA)
            self.assertNotRegex(texto, r"\b(?:some|[Cc]onfirme|[Cc]onfira|use)\b")
        passos = " | ".join(tools._avaliar(resolvido, {"faltando": [], "nao_informado": []}, {"faltando": [], "nao_informado": [], "parcial": []},
                                          {"cabe_no_orcamento": True}, [])["proximos_passos"])
        for trecho in ("some numa única linha da ficha as quantidades de: sal",
                       "coxinha da asa de frango: confirme com ela uma adaptação ou use o ingrediente da receita",
                       "quiabo: confira o item correto da despensa ou proponha uma adaptação explícita"):
            self.assertIn(trecho, passos)
