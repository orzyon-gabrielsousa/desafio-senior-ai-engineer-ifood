"""Retomar corrige os dados já disponíveis antes de perguntar à pessoa."""

from copy import deepcopy
from types import SimpleNamespace

from sabor_da_maria import falas, fonte, jornada, montagem, painel, pesquisa, tools
from sabor_da_maria.store import Store

from ._base import SESSAO, StoreTestCase


NOME = "Frango com Batata Fácil"
URL = "https://receitas.exemplo/frango-com-batata-facil/"
FONTE = {"nome": NOME, "url": URL, "porcoes": 4,
         "ingredientes": ["500 g de peito de frango", "500 g de batata", "1/2 cebola", "1 colher (sopa) de azeite"],
         "modo_preparo": "Refogue o frango e a cebola no azeite e cozinhe com a batata até ficar macio."}
CTA = f"Vamos continuar «{NOME}». Confira o que já está salvo e me conduza pelo próximo passo, perguntando só o que falta."
PERGUNTA_DURACAO = "Quanto tempo costuma levar esse preparo completo, incluindo preparo e cozimento?"


class RetomadaDaFonteTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["caseira"], "estado": "SP", "cidade": "São Paulo"})
        jornada.responder(self.store, "confirmar_estoque")
        jornada.responder(self.store, "ajuda_ia")
        self.prato, = self.store.selecionar_pratos([{"nome": NOME, "fonte_url": URL}])
        self.parcial = [{"nome": "peito de frango", "quantidade": 500, "unidade": "g"},
                        {"nome": "batata", "quantidade": 500, "unidade": "g"},
                        {"nome": "cebola", "quantidade": .5, "unidade": "kg"}]
        self.receita_id = self.salvar(self.parcial, tempo=32)

    def salvar(self, ingredientes, *, tempo=None, adaptacao=None):
        dados = {"ingredientes": deepcopy(ingredientes), "fonte_verificada": deepcopy(FONTE),
                 "tempo_preparo_min": tempo, "equipamentos_necessarios": ["fogao"], "tecnicas_necessarias": ["refogar"],
                 "modo_preparo": FONTE["modo_preparo"], "adaptacao": adaptacao,
                 "fidelidade_fonte": fonte.conferir(FONTE, ingredientes, 4, adaptacao or "", tempo)}
        dados["bloqueios"] = dados["fidelidade_fonte"]["bloqueios"]
        return self.store.salvar_receita(NOME, URL, 4, dados, "conferida", prato_id=self.prato["id"])

    def hook(self, texto, turno="retomada", *, historico=None):
        falas.ao_iniciar_turno(session_id=SESSAO, user_message=texto, conversation_history=historico)
        return painel.ao_iniciar_turno(session_id=SESSAO, turn_id=turno, user_message=texto,
                                      conversation_history=historico)

    def estado(self):
        return (self.store.pratos(), self.store.receitas(), self.store.perfil(), self.store.orcamento(),
                self.store.reservas_ativas(), self.store.analises_preco())

    def completar_cta(self):
        return (f"Complete a ficha do prato #1 ({NOME}) a partir da receita original salva [{URL}]. "
                "Leia novamente a fonte e preencha/valide todos os ingredientes, medidas, rendimento e preparo sem omitir ou substituir itens. "
                "Se a fonte não trouxer um dado, explique só esse ponto. "
                "Não transforme esta retomada em aceite, compra, reserva ou confirmação da cozinha.")

    def completar_cta_front(self):
        return (f"Quero continuar o prato escolhido #1 ({NOME}). Complete a ficha a partir da receita original salva em {URL}. "
                "Leia novamente a fonte e preencha ou valide todos os ingredientes, medidas, rendimento e preparo, sem omitir ou substituir itens. "
                "Se a fonte não trouxer um dado, explique só esse ponto. "
                "Não transforme esta retomada em aceite, compra, reserva ou confirmação da cozinha.")

    def test_montagem_integral_so_reconhece_delegacao_sem_alteracoes(self):
        canonica = ("Quero sua ajuda. Pesquise uma receita real para o meu negócio com o estoque disponível e monte uma proposta, sem aprovar por mim.")
        for texto in (canonica, "monte um prato para meu cardápio", "monte uma receita para mim", "quero montar um prato"):
            self.assertTrue(pesquisa.montagem_integral(texto), texto)
        for texto in (canonica + " sem limão", "monte um prato sem limão", "monte um prato para 10 porções",
                      "monte um prato para meu cardápio, pode propor adaptação", "não monte um prato", "continue",
                      "monte um prato para meu cardápio?", "monte um prato com frango"):
            self.assertFalse(pesquisa.montagem_integral(texto), texto)

    def test_nome_id_e_botoes_conhecidos_identificam_so_o_alvo_salvo(self):
        pratos = self.store.pratos_em_andamento()
        for texto in (CTA, f"Sobre o prato escolhido #1 ({NOME}): {CTA}", "Continue o prato #1.",
                      f"Quero continuar o prato escolhido #1 ({NOME}).", self.completar_cta(),
                      f"Quero continuar o prato escolhido #1 ({NOME}). {self.completar_cta()}", self.completar_cta_front()):
            self.assertEqual(jornada.alvo_da_retomada(texto, pratos)["id"], 1, texto)
        for texto in (CTA + " sem azeite", "Continue o prato #1 para 10 porções", CTA + " e compre os ingredientes",
                      "Continue o prato #2.", self.completar_cta().replace(URL, "https://outra.exemplo/")):
            self.assertIsNone(jornada.alvo_da_retomada(texto, pratos), texto)

    def test_retomada_corrige_fonte_salva_antes_de_perguntar_cozinha(self):
        antes = self.estado()
        for texto in (CTA, "Vamos continuar", self.completar_cta(), self.completar_cta_front()):
            retorno = self.hook(texto)
            self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "receita_conferir_ingredientes")
            self.assertEqual(pesquisa.alvo_escolhido(SESSAO), {"id": 1, "nome": NOME, "fonte_url": URL,
                                                          "receita_id": self.receita_id, "revisao_fonte": True})
            self.assertEqual(pesquisa.ficha_lida(SESSAO, URL), FONTE)
            self.assertIsNone(montagem.resposta_preparada(SESSAO, "retomada"))
            self.assertFalse(pesquisa.montando(SESSAO))
            self.assertIn("Corrija primeiro a ficha", retorno["context"])
            self.assertNotIn("Pergunta necessária", retorno["context"])
            self.assertEqual(self.estado(), antes)
        estado = jornada.resumo(self.store)
        self.assertIn(estado["pendencias"][0]["tipo"], ("fonte_incompleta", "fonte_divergente"))
        self.assertIsNone(estado["pergunta"])

    def test_adaptacao_id_fonte_trocada_e_receita_ja_aprovada_nao_sao_sobrescritos(self):
        prato = self.store.obter_prato(1)
        receita = self.store.obter_receita(self.receita_id)
        for alterada in ({**receita, "id": 999}, {**receita, "fonte_url": "https://outra.exemplo/"},
                         {**receita, "dados": {**receita["dados"], "adaptacao": "sem azeite"}},
                         {**receita, "dados": {**receita["dados"], "fonte_verificada": {**FONTE, "url": "https://outra.exemplo/"}}}):
            self.assertFalse(pesquisa.conduzir_escolhido(SESSAO, prato, receita=alterada, revisar_fonte=True))
        self.assertFalse(pesquisa.conduzir_escolhido(SESSAO, {**prato, "status": "aceito"}, receita=receita, revisar_fonte=True))
        self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))

    def test_depois_da_correcao_repetir_cta_mostra_pergunta_atual_aberta(self):
        self.salvar(fonte.ingredientes_publicados(FONTE))
        antes = self.estado()
        for turno in ("primeiro-clique", "clique-repetido"):
            self.hook(CTA, turno)
            self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))
            resposta = montagem.resposta_preparada(SESSAO, turno)
            self.assertIsNotNone(resposta)
            self.assertEqual(resposta.count("?"), 1)
            self.assertIn("Quanto tempo você consegue dedicar a esta cozinhada?", resposta)
            self.assertNotIn("gás", resposta)
            self.assertEqual(self.estado(), antes)
        self.store.perfil_salvar({"restricoes": {"tempo_por_cozinhada_min": 60}})
        self.hook(CTA, "tempo-disponivel-informado")
        resposta = montagem.resposta_preparada(SESSAO, "tempo-disponivel-informado")
        self.assertIn("Quanto tempo costuma levar esse preparo completo", resposta)
        self.assertEqual(resposta.count("?"), 1)
        self.assertNotIn("gás", resposta)

    def test_fonte_bloqueada_nao_oferece_botao_de_gas(self):
        pergunta = jornada._pergunta({"id": 1}, [{"tipo": "gas_a_confirmar", "detalhe": "gás pendente"},
                                               {"tipo": "fonte_incompleta", "detalhe": "Falta azeite da fonte."}])
        self.assertIsNone(pergunta)
        tempo = jornada._pergunta({"id": 1}, [{"tipo": "tempo_da_receita_pendente", "detalhe": "duração não publicada"}])
        self.assertEqual(tempo["opcoes"], [])
        self.assertIn("preparo e cozimento", tempo["texto"])

    def test_tempo_salvo_exige_valor_persistido_e_resposta_sem_edicao(self):
        declarados = {"restricoes": {"tempo_por_cozinhada_min": 60}}
        mensagem = "Tenho até 1 hora por cozinhada. Continue o prato #1."
        self.assertIsNone(painel._alvo_do_tempo_salvo(self.store, SimpleNamespace(texto=mensagem), declarados))
        self.store.perfil_salvar(declarados)
        for texto in (mensagem, "Tenho até 1 hora por cozinhada.", "1 hora."):
            self.assertEqual(painel._alvo_do_tempo_salvo(self.store, SimpleNamespace(texto=texto), declarados)["id"], 1)
        for texto in (mensagem + " Mas sem azeite.", mensagem + " Faça dez porções.", mensagem + " Aceito o prato.",
                      "Tenho até 1 hora por cozinhada. Troque o frango por peixe.", "Tenho até 1 hora por cozinhada?",
                      mensagem.replace("#1", "#999"), mensagem + " Continue o prato #2."):
            self.assertIsNone(painel._alvo_do_tempo_salvo(self.store, SimpleNamespace(texto=texto), declarados), texto)
        self.store.selecionar_pratos([{"nome": "Segundo rascunho"}])
        self.assertIsNone(painel._alvo_do_tempo_salvo(self.store, SimpleNamespace(texto="1 hora."), declarados))

    def test_resposta_real_de_disponibilidade_salva_e_avanca_sem_pular_duracao_da_receita(self):
        receita_id = self.salvar(fonte.ingredientes_publicados(FONTE))
        receita_antes = self.store.obter_receita(receita_id)
        self.hook("Tenho até 1 hora por cozinhada. Continue o prato #1.", "respondeu-tempo")
        self.assertEqual(self.store.perfil()["restricoes"]["tempo_por_cozinhada_min"], 60)
        resposta = montagem.resposta_preparada(SESSAO, "respondeu-tempo")
        self.assertIsNotNone(resposta)
        self.assertEqual(resposta.count("?"), 1)
        self.assertIn("Quanto tempo costuma levar esse preparo completo", resposta)
        self.assertNotIn("gás", resposta)
        self.assertNotIn("bocas", resposta)
        self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))
        self.assertEqual(self.store.obter_receita(receita_id), receita_antes)
        self.assertEqual(self.store.obter_prato(1)["receita_id"], receita_id)
        self.assertEqual(self.store.obter_prato(1)["status"], "selecionado")
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.analises_preco(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

    def test_faixa_real_salva_avanca_sem_inventar_maximo_exato(self):
        receita_id = self.salvar(fonte.ingredientes_publicados(FONTE))
        for frase, operador, minutos in (("Tenho menos de 30 minutos por cozinhada.", "menor_que", 30),
                                         ("Tenho mais de 2 horas por cozinhada.", "maior_que", 120)):
            self.hook(frase + " Continue o prato #1.", "respondeu-faixa")
            restricoes = self.store.perfil()["restricoes"]
            self.assertIsNone(restricoes.get("tempo_por_cozinhada_min"))
            self.assertEqual(restricoes["tempo_por_cozinhada_faixa"], {"operador": operador, "minutos": minutos})
            resposta = montagem.resposta_preparada(SESSAO, "respondeu-faixa")
            self.assertIsNotNone(resposta)
            self.assertEqual(resposta.count("?"), 1)
            self.assertIn("Quanto tempo costuma levar esse preparo completo", resposta)
            self.assertIsNone(pesquisa.ferramenta_da_conducao(SESSAO))
            self.assertEqual(self.store.obter_prato(1)["receita_id"], receita_id)
            self.assertEqual(self.store.reservas_ativas(), [])

    def test_resposta_a_duracao_da_receita_nao_troca_a_disponibilidade(self):
        receita_id = self.salvar(fonte.ingredientes_publicados(FONTE))
        disponibilidade = "Tenho menos de 30 minutos por cozinhada."
        self.hook(disponibilidade, "disponibilidade")
        pergunta = montagem.resposta_preparada(SESSAO, "disponibilidade")
        self.assertIsNotNone(pergunta)
        self.assertTrue(pergunta.endswith(PERGUNTA_DURACAO))
        perfil = self.store.perfil()
        historico = [{"role": "user", "content": disponibilidade},
                     {"role": "assistant", "content": pergunta},
                     {"role": "user", "content": "30 minutos"}]

        self.hook("30 minutos", "duracao-declarada", historico=historico)

        receita = self.store.obter_receita(receita_id)
        prato = self.store.obter_prato(self.prato["id"])
        self.assertEqual(receita["dados"]["tempo_preparo_min"], 30)
        self.assertEqual(receita["dados"]["tempo_preparo_origem"], "estimativa_declarada")
        self.assertEqual(self.store.perfil(), perfil)
        self.assertEqual(prato["receita_id"], receita_id)
        self.assertEqual(prato["status"], "selecionado")
        estado = jornada.resumo(self.store)
        tipos = {pendencia["tipo"] for pendencia in estado["pendencias"]}
        self.assertIn("tempo_insuficiente", tipos)
        self.assertNotIn("tempo_da_receita_pendente", tipos)
        self.assertNotIn("tempo_disponivel_pendente", tipos)
        self.assertEqual(self.store.reservas_ativas(), [])
        self.assertEqual(self.store.analises_preco(), [])
        self.assertEqual(self.store.orcamento()["saldo"], 80)

        proposta = montagem.resposta_preparada(SESSAO, "duracao-declarada")
        self.assertIn("versão mais rápida", proposta)
        self.assertNotIn(PERGUNTA_DURACAO, proposta)
        historico.append({"role": "assistant", "content": proposta})

        retorno = self.hook("Sim, pode procurar.", "busca-mais-rapida", historico=historico)

        self.assertIn("compare o tempo total publicado", retorno["context"])
        self.assertTrue(pesquisa.somente_sugestoes(SESSAO))
        self.assertEqual(pesquisa.ferramenta_da_conducao(SESSAO), "despensa_listar")
        self.assertEqual(self.store.obter_prato(self.prato["id"])["receita_id"], receita_id)

    def test_duas_tentativas_passam_a_duracao_para_a_consultora(self):
        receita_id = self.salvar(fonte.ingredientes_publicados(FONTE))
        self.store.perfil_salvar({"restricoes": {"tempo_por_cozinhada_min": 60}})
        self.hook(CTA, "pedir-duracao")
        primeira_pergunta = montagem.resposta_preparada(SESSAO, "pedir-duracao")
        self.assertIsNotNone(primeira_pergunta)
        self.assertTrue(primeira_pergunta.endswith(PERGUNTA_DURACAO))
        historico = [{"role": "user", "content": CTA},
                     {"role": "assistant", "content": primeira_pergunta}]

        self.hook("Não sei", "primeira-tentativa", historico=historico)

        segunda_pergunta = montagem.resposta_preparada(SESSAO, "primeira-tentativa")
        self.assertIsNotNone(segunda_pergunta)
        self.assertTrue(segunda_pergunta.endswith(PERGUNTA_DURACAO))
        self.assertEqual(jornada.resumo(self.store)["pergunta"]["texto"], PERGUNTA_DURACAO)
        historico += [{"role": "user", "content": "Não sei"},
                      {"role": "assistant", "content": segunda_pergunta},
                      {"role": "user", "content": "Não sei"}]

        retorno = self.hook("Não sei", "segunda-tentativa", historico=historico)

        self.assertIsNone(montagem.resposta_preparada(SESSAO, "segunda-tentativa"))
        self.assertIn("Duas tentativas", retorno["context"])
        self.assertNotIn(f"Pergunta atual: {PERGUNTA_DURACAO}", retorno["context"])
        self.assertIsNone(jornada.resumo(self.store)["pergunta"])
        self.assertIsNone(self.store.obter_receita(receita_id)["dados"]["tempo_preparo_min"])
        self.assertEqual(self.store.obter_prato(self.prato["id"])["status"], "selecionado")

        retomada = self.hook(CTA, "retomar-depois-das-tentativas")

        self.assertIsNone(montagem.resposta_preparada(SESSAO, "retomar-depois-das-tentativas"))
        self.assertIn("Duas tentativas", retomada["context"])
        self.assertIsNone(jornada.resumo(self.store)["pergunta"])

    def test_pergunta_da_duracao_de_outro_prato_nao_consumiu_tentativa_da_ficha(self):
        self.salvar(fonte.ingredientes_publicados(FONTE))
        self.store.perfil_salvar({"restricoes": {"tempo_por_cozinhada_min": 60}})
        outro, = self.store.selecionar_pratos([{"nome": "Segundo prato"}])
        pergunta_do_outro = f"Vamos conferir o prato #{outro['id']}, **{outro['nome']}**.\n\n{PERGUNTA_DURACAO}"
        self.hook(CTA, "pedir-duracao-da-ficha")
        pergunta_da_ficha = montagem.resposta_preparada(SESSAO, "pedir-duracao-da-ficha")
        self.assertIsNotNone(pergunta_da_ficha)
        self.assertTrue(pergunta_da_ficha.endswith(PERGUNTA_DURACAO))
        historico = [{"role": "assistant", "content": pergunta_do_outro},
                     {"role": "user", "content": "Não sei"},
                     {"role": "user", "content": CTA},
                     {"role": "assistant", "content": pergunta_da_ficha}]

        self.hook("Não sei", "primeira-tentativa-da-ficha", historico=historico)

        resposta = montagem.resposta_preparada(SESSAO, "primeira-tentativa-da-ficha")
        self.assertIsNotNone(resposta)
        self.assertTrue(resposta.endswith(PERGUNTA_DURACAO))
        self.assertEqual(jornada.resumo(self.store)["pergunta"]["texto"], PERGUNTA_DURACAO)


class RetomadaPizzaDeArrozTests(StoreTestCase):
    def test_montar_com_estoque_corrige_preparo_e_preserva_as_medidas_da_fonte(self):
        store = Store()
        store.negocio_salvar({"tipo": "restaurante", "alimentacao": ["caseira"], "estado": "DF", "cidade": "Brasília"})
        jornada.responder(store, "confirmar_estoque")
        jornada.responder(store, "ajuda_ia")
        nome = "Pizza de arroz"
        url = "https://receitas.exemplo/pizza-de-arroz"
        preparo = "Bata a massa no liquidificador. Refogue o frango e acrescente o extrato. Cubra com presunto, mussarela e orégano. Asse no forno por 30 minutos."
        original = {"nome": nome, "url": url, "porcoes": 6, "tempo_preparo_min": 30, "modo_preparo": preparo,
                    "ingredientes": ["2 copos americano de farinha de trigo", "2 colheres de sopa de óleo", "1 colher de fermenro em pó",
                                     "2 copos americanos de arroz cozido", "1 copo de queijo ralado", "1 ovo", "1 copo a leite",
                                     "1 peito de frango cozido e desfiado", "200 g de presunto", "200 g de mussarela", "Sal e tempero a gosto",
                                     "1 tomate picado em fatias", "2 colheres de sopa de óleo", "2 colheres de extrato de tomate", "Orégano a gosto"]}
        prato, = store.selecionar_pratos([{"nome": nome, "fonte_url": url}])
        antiga = store.salvar_receita(nome, url, 6, {
            "fonte_verificada": original, "adaptacao": "Versão sem presunto", "modo_preparo": preparo, "preparo_da_adaptacao": preparo,
            "tempo_preparo_min": 30, "montagem_com_estoque": True,
            "ingredientes_omitidos": ["presunto", "orégano"],
            "ingredientes": [{"nome": "arroz cozido", "quantidade": 2.5, "unidade": "kg"},
                             {"nome": "farinha de trigo", "quantidade": 500, "unidade": "g", "item_despensa": "Farinha de mandioca"},
                             {"nome": "frango cozido e desfiado", "quantidade": 1.5, "unidade": "kg"}],
        }, "conferida", prato_id=prato["id"])
        self.assertEqual(jornada.resumo(store)["pendencias"][0]["tipo"], "preparo_adaptacao_pendente")
        texto = f"Monte a ficha do prato #{prato['id']} com o estoque disponível."
        for turno in ("correcao", "repeticao"):
            falas.ao_iniciar_turno(session_id=SESSAO, user_message=texto)
            painel.ao_iniciar_turno(session_id=SESSAO, turn_id=turno, user_message=texto)
            resposta = montagem.resposta_preparada(SESSAO, turno)
            self.assertIn("Quanto tempo você consegue dedicar", resposta)
            self.assertNotIn("modo de preparo próprio", resposta)

        atualizado = store.obter_prato(prato["id"])
        self.assertEqual(atualizado["status"], "selecionado")
        self.assertNotEqual(atualizado["receita_id"], antiga)
        ficha = store.obter_receita(atualizado["receita_id"])
        self.assertEqual(ficha["dados"]["fonte_verificada"], original)
        self.assertNotEqual(ficha["dados"]["modo_preparo"], preparo)
        self.assertIn("fermento em pó", ficha["dados"]["ingredientes_omitidos"])
        self.assertNotIn("presunto", ficha["dados"]["modo_preparo"])
        revisao = tools._avaliar_prato(store, ficha["dados"]["ingredientes"], ficha)
        self.assertEqual(revisao["resultado"]["compras"], [])
        self.assertEqual(revisao["resultado"]["pendencias"], [])
        medidas = {linha["item_despensa"]: linha["usa_da_despensa_base"] for linha in revisao["resultado"]["itens"]}
        self.assertAlmostEqual(medidas["Arroz branco tipo 1"], .1064)
        self.assertAlmostEqual(medidas["Peito de frango"], .3)
        self.assertAlmostEqual(medidas["Farinha de trigo"], .209)
        self.assertAlmostEqual(medidas["Óleo de soja"], .06)
        self.assertEqual(store.reservas_ativas(), [])
        self.assertEqual(store.orcamento()["despesas"], 0)
