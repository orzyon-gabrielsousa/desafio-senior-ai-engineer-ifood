"""Carrinho com saldo, perfil do negócio e a API que a tela usa: tudo no mesmo SQLite das tools."""

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from sabor_da_maria import api, negocio, tools
from sabor_da_maria.store import OrcamentoEstourado, Store

from ._base import FALA_COZINHA, SESSAO, StoreTestCase, diz


def call(name, **args):
    return json.loads(tools.HANDLERS[name](args, session_id=SESSAO))


class CarrinhoStoreTests(StoreTestCase):
    def test_saldo_acompanha_o_carrinho(self):
        store = Store()
        self.assertEqual(store.carrinho_resumo(), {"inicial": 80.0, "comprometido": 0.0, "gasto_confirmado": 0.0,
                                                  "reservado_cardapio": 0.0, "carrinho": 0.0, "saldo": 80.0, "estourou": False})
        item = store.carrinho_adicionar("Couve", 12, 1, "maço")
        self.assertEqual((item["nome"], item["preco_total"], item["estimado"]), ("Couve", 12.0, False))
        resumo = store.carrinho_resumo()
        self.assertEqual((resumo["carrinho"], resumo["saldo"]), (12.0, 68.0))
        self.assertEqual(store.orcamento()["restante"], 80.0)  # as travas de prato continuam olhando só os compromissos

    def test_mesmo_item_soma_quantidade_e_preco(self):
        store = Store()
        store.carrinho_adicionar("Couve", 4, 1, "maço")
        item = store.carrinho_adicionar("couve", 4, 1, "maço", estimado=True)
        self.assertEqual((item["quantidade"], item["preco_total"], item["estimado"]), (2.0, 8.0, True))
        self.assertEqual(len(store.carrinho_itens()), 1)
        store.carrinho_adicionar("Couve", 5, 1, "kg")  # outra unidade, outra linha
        self.assertEqual(len(store.carrinho_itens()), 2)

    def test_nao_deixa_passar_do_saldo(self):
        store = Store()
        store.carrinho_adicionar("Filé", 70, 1, "kg")
        with self.assertRaises(OrcamentoEstourado) as ctx:
            store.carrinho_adicionar("Camarão", 25)
        self.assertEqual((ctx.exception.falta, ctx.exception.saldo), (15.0, 10.0))
        self.assertEqual(len(store.carrinho_itens()), 1)
        store.carrinho_adicionar("Alho", 10)  # gasta exatamente o saldo
        self.assertEqual(store.carrinho_resumo()["saldo"], 0.0)

    def test_entradas_invalidas(self):
        store = Store()
        for nome, preco, qtd in (("", 5, 1), ("Sal", 0, 1), ("Sal", -3, 1), ("Sal", 5, 0), ("x" * 81, 5, 1)):
            with self.assertRaises(ValueError, msg=(nome, preco, qtd)):
                store.carrinho_adicionar(nome, preco, qtd)
        self.assertEqual(store.carrinho_itens(), [])

    def test_quantidade_mantem_o_preco_por_unidade(self):
        store = Store()
        item = store.carrinho_adicionar("Tomate", 10, 2, "kg")
        item = store.carrinho_atualizar_quantidade(item["id"], 3)
        self.assertEqual((item["quantidade"], item["preco_total"]), (3.0, 15.0))
        item = store.carrinho_atualizar_quantidade(item["id"], 1)
        self.assertEqual(item["preco_total"], 5.0)
        with self.assertRaises(OrcamentoEstourado):
            store.carrinho_atualizar_quantidade(item["id"], 100)
        with self.assertRaises(ValueError):
            store.carrinho_atualizar_quantidade(item["id"], 0)
        with self.assertRaises(KeyError):
            store.carrinho_atualizar_quantidade(999, 2)

    def test_remover_por_id_nome_e_ambiguidade(self):
        store = Store()
        couve = store.carrinho_adicionar("Couve manteiga", 6)
        store.carrinho_adicionar("Couve-flor", 8)
        store.carrinho_adicionar("Alho", 3)
        with self.assertRaises(ValueError) as ctx:
            store.carrinho_remover(nome="couve")
        self.assertIn("Couve manteiga", str(ctx.exception))
        self.assertEqual([i["nome"] for i in store.carrinho_remover(nome="alho")], ["Alho"])
        self.assertEqual([i["nome"] for i in store.carrinho_remover(item_id=couve["id"])], ["Couve manteiga"])
        with self.assertRaises(KeyError):
            store.carrinho_remover(nome="abacate")
        self.assertEqual(store.carrinho_limpar(), 1)
        self.assertEqual(store.carrinho_resumo()["saldo"], 80.0)

    def test_montar_da_receita_troca_as_linhas_e_respeita_o_saldo(self):
        store = Store()
        compras = [{"ingrediente": "creme de leite", "quantidade": 200, "unidade": "g", "preco_estimado": 3.5},
                   {"ingrediente": "queijo", "quantidade": 1, "unidade": "kg", "preco_estimado": 30.0}]
        itens = store.carrinho_montar_da_receita(1, "Gratinado", compras)
        self.assertEqual((len(itens), store.carrinho_resumo()["carrinho"]), (2, 33.5))
        self.assertTrue(all(i["estimado"] and i["receita_id"] == 1 for i in itens))
        store.carrinho_montar_da_receita(1, "Gratinado", compras)  # repetir não duplica
        self.assertEqual(store.carrinho_resumo()["carrinho"], 33.5)
        store.carrinho_adicionar("Alho", 40)  # saldo: 80 − 33,5 − 40 = 6,5
        with self.assertRaises(OrcamentoEstourado):
            store.carrinho_montar_da_receita(2, "Outro", [{"ingrediente": "salmão", "quantidade": 1, "unidade": "kg", "preco_estimado": 7.0}])
        self.assertEqual(store.carrinho_baixar_receita(1), 2)
        self.assertEqual(store.carrinho_resumo()["carrinho"], 40.0)


class NegocioTests(StoreTestCase):
    def test_salvar_e_ler(self):
        store = Store()
        self.assertIsNone(store.negocio())
        salvo = store.negocio_salvar({"tipo": "restaurante_hotel", "alimentacao": ["caseira", "doces", "pizza", "doces"],
                                      "estado": "sp", "cidade": "  São   Paulo ", "zona": "sul"})
        self.assertEqual(salvo["alimentacao"], ["caseira", "doces", "pizza"])
        self.assertEqual((salvo["pais"], salvo["estado"], salvo["cidade"], salvo["zona"]), ("Brasil", "SP", "São Paulo", "sul"))
        self.assertEqual(Store().negocio()["tipo"], "restaurante_hotel")
        frase = negocio.descrever(store.negocio())
        self.assertIn("Restaurante de hotel", frase)
        self.assertIn("Doces e sobremesas", frase)
        self.assertIn("local: Zona Sul, São Paulo/SP", frase)

    def test_nenhum_campo_e_obrigatorio_e_perfil_antigo_continua_legivel(self):
        store = Store()
        self.assertEqual(store.negocio_salvar({})["tipo"], "")  # ela pode preencher aos poucos
        # o que um perfil de antes guardava (o que vende, para quem, observações) não aparece mais; o resto continua valendo
        with store.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO config(chave, valor) VALUES('negocio', ?)",
                         ('{"tipo": "marmitaria", "vende": ["marmitas"], "publico": "hóspedes", "observacoes": "x", "estado": "MG", "cidade": "Uberlândia", "zona": ""}',))
        lido = Store().negocio()
        self.assertEqual((lido["tipo"], lido["estado"], lido["cidade"], lido["alimentacao"], lido["pais"]), ("marmitaria", "MG", "Uberlândia", [], "Brasil"))
        self.assertNotIn("vende", lido)
        self.assertNotIn("publico", lido)

    def test_conversa_so_muda_o_que_vier_e_soma_a_alimentacao(self):
        store = Store()
        store.negocio_atualizar({"estado": "SP", "cidade": "Campinas"})
        store.negocio_atualizar({"tipo": "marmitaria", "alimentacao": ["marmita"]})
        salvo = store.negocio_atualizar({"alimentacao": ["pizza", "marmita"], "zona": "leste"})
        self.assertEqual((salvo["estado"], salvo["cidade"], salvo["tipo"], salvo["zona"], salvo["alimentacao"]), ("SP", "Campinas", "marmitaria", "leste", ["marmita", "pizza"]))
        self.assertEqual(Store().negocio_salvar({"estado": "RJ"})["alimentacao"], [])  # a tela manda o formulário inteiro: substitui

    def test_opcoes_fechadas(self):
        for invalido in ({"tipo": "banca"}, {"alimentacao": ["sushi"]}, {"alimentacao": "pizza"}, {"pais": "Portugal"}, {"estado": "XX"}, {"zona": "nordeste"}, "texto"):
            with self.assertRaises(ValueError, msg=invalido):
                negocio.normalizar(invalido)
        self.assertIn("ainda não preencheu", negocio.descrever(None))


def _perfil_completo():
    diz(FALA_COZINHA)
    call("perfil_cozinha_salvar",
         equipamentos={"fogão": 4, "forno": True, "panela de pressão": True, "air fryer": False, "liquidificador": True},
         restricoes={"espaco_geladeira": "médio", "tempo_por_cozinhada_min": 120, "gas": "botijão ok", "energia": "estável"},
         tecnicas={"refogado": "sim"})


def _receita_com_compra(nome="Frango cremoso"):
    return call("receita_conferir_ingredientes", nome=nome, porcoes=4, ingredientes=[
        {"nome": "peito de frango", "quantidade": 1, "unidade": "kg"},
        {"nome": "creme de leite", "quantidade": 200, "unidade": "g", "item_despensa": "",
         "preco_compra_estimado": 3.5, "quantidade_compra": 200, "unidade_compra": "g"}],
        equipamentos_necessarios=["forno"], tecnicas_necessarias=["refogado"])


class CarrinhoToolsTests(StoreTestCase):
    def test_consultora_adiciona_e_avisa_o_saldo(self):
        out = call("carrinho_adicionar", nome="couve", preco_total="12,00", quantidade=1, unidade="maço", preco_estimado=True)
        self.assertTrue(out["success"], out)
        self.assertEqual(out["resumo"]["saldo"], 68.0)
        self.assertEqual(out["diga_a_dona_maria"], "orçamento R$ 80,00 · no carrinho R$ 12,00 · saldo R$ 68,00")
        self.assertTrue(out["adicionado"]["estimado"])
        consulta = call("carrinho_consultar")
        self.assertEqual([i["nome"] for i in consulta["carrinho"]], ["couve"])
        self.assertIn("ainda não preencheu", consulta["negocio_resumo"])
        saiu = call("carrinho_remover", nome="couve")
        self.assertEqual((saiu["resumo"]["saldo"], saiu["removidos"][0]["nome"]), (80.0, "couve"))

    def test_estouro_vira_bloqueio_com_quanto_falta(self):
        out = call("carrinho_adicionar", nome="lagosta", preco_total=95)
        self.assertFalse(out["success"])
        self.assertTrue(out["bloqueado"])
        self.assertEqual(out["falta"], 15.0)
        self.assertIn("saldo R$ 80,00", out["diga_a_dona_maria"])
        self.assertEqual(call("carrinho_consultar")["carrinho"], [])

    def test_erros_voltam_como_resposta(self):
        self.assertFalse(call("carrinho_adicionar", nome="sal")["success"])
        self.assertFalse(call("carrinho_remover")["success"])
        self.assertFalse(call("carrinho_remover", nome="abacate")["success"])
        self.assertTrue(call("carrinho_remover", limpar=True)["success"])

    def test_receita_so_entra_no_carrinho_se_for_viavel(self):
        receita = _receita_com_compra()
        out = call("carrinho_montar_da_receita", receita_id=receita["receita_id"])
        self.assertFalse(out["success"])
        self.assertTrue(out["bloqueado"])
        self.assertEqual({b["tipo"] for b in out["bloqueios"]}, {"perfil_pendente", "equipamento_nao_informado", "tecnica_nao_informada"})
        self.assertEqual(call("carrinho_consultar")["carrinho"], [])

    def test_receita_viavel_monta_o_carrinho_e_o_aceite_converte_em_compra(self):
        _perfil_completo()
        receita = _receita_com_compra()
        self.assertEqual(receita["veredito"], "viavel", receita["bloqueios"])
        out = call("carrinho_montar_da_receita", receita_id=receita["receita_id"])
        self.assertTrue(out["success"], out)
        # O prato vem aberto: o que ela já tem (com o custo, sem gastar saldo) e o que compra, cada um com o seu valor.
        grupo = out["prato"]
        self.assertEqual(grupo["prato"], "Frango cremoso")
        self.assertEqual([(i["nome"], i["preco_total"], i["ja_tem"]) for i in grupo["ingredientes"]],
                         [("peito de frango", 14.0, True), ("creme de leite", 3.5, False)])
        self.assertEqual((grupo["valor_do_prato"], grupo["a_comprar"]), (17.5, 3.5))
        self.assertEqual(out["resumo"]["saldo"], 76.5)  # só o creme de leite gasta saldo
        self.assertIn("- peito de frango (1 kg): R$ 14,00 — já tem na despensa", out["diga_a_dona_maria"])
        self.assertIn("- creme de leite (200 g): R$ 3,50", out["diga_a_dona_maria"])
        call("carrinho_montar_da_receita", receita_id=receita["receita_id"])
        self.assertEqual(len(call("carrinho_consultar")["pratos"][0]["ingredientes"]), 2)  # repetir não duplica
        call("carrinho_adicionar", nome="couve", preco_total=12)
        cmv = call("prato_calcular_cmv", receita_id=receita["receita_id"])
        registro = call("cardapio_registrar", prato_id=cmv["prato_id"], preco_escolhido=30)
        self.assertTrue(registro["success"], registro)
        self.assertEqual(registro["orcamento"]["comprometido"], 3.5)
        self.assertEqual(registro["linhas_do_carrinho_que_viraram_compras"], 2)
        depois = call("carrinho_consultar")
        self.assertEqual([i["nome"] for i in depois["carrinho"]], ["couve"])  # só a linha da receita saiu
        self.assertEqual(depois["resumo"]["saldo"], 64.5)  # 80 − 3,5 comprometidos − 12 de couve
        self.assertIn("saldo R$ 64,50", registro["diga_a_dona_maria"])

    def test_receita_que_nao_cabe_no_saldo_e_recusada(self):
        _perfil_completo()
        call("carrinho_adicionar", nome="filé", preco_total=78)
        receita = _receita_com_compra()
        out = call("carrinho_montar_da_receita", receita_id=receita["receita_id"])
        self.assertFalse(out["success"])
        self.assertTrue(out["bloqueado"])
        self.assertEqual(out["falta"], 1.5)


class ApiTests(StoreTestCase):
    def test_estado_inicial(self):
        status, dados = api.tratar("GET", "/api/estado")
        self.assertEqual(status, 200)
        self.assertEqual(dados["orcamento"]["saldo"], 80.0)
        self.assertEqual(dados["carrinho"], [])
        self.assertIsNone(dados["negocio"])
        self.assertIn("restaurante_hotel", [t["id"] for t in dados["opcoes"]["tipos"]])

    def test_cadastro_guiado_grava_respostas_parciais_e_distingue_negativa_de_nao_respondida(self):
        Store().perfil_salvar({"equipamentos": {"forno_eletrico": True, "fogao_bocas": 4}})
        status, dados = api.tratar("PUT", "/api/cozinha", {
            "equipamentos": {"forno": None, "panela_pressao": False},
            "habilidades": {"assar": "aprendendo"},
        })
        self.assertEqual(status, 200)
        self.assertEqual(dados["cozinha"]["equipamentos"], {"fogao": 4, "panela_pressao": False})
        self.assertEqual(dados["cozinha"]["habilidades"], {"assar": "aprendendo"})
        self.assertTrue(dados["cadastro"]["cozinha_pendente"])
        self.assertIn("forno", dados["cadastro"]["cozinha_campos"])
        self.assertNotIn("panela_pressao", dados["cadastro"]["cozinha_campos"])
        self.assertEqual(Store().cozinha()["equipamentos"], dados["cozinha"]["equipamentos"])

    def test_cadastro_guiado_do_negocio_preserva_campos_omitidos(self):
        loja = {"tipo": "restaurante_hotel", "alimentacao": ["caseira"], "estado": "SP", "cidade": "São Paulo"}
        Store().negocio_salvar(loja)
        status, dados = api.tratar("PATCH", "/api/negocio", {"zona": "sul"})
        self.assertEqual(status, 200)
        self.assertEqual({campo: dados["negocio"][campo] for campo in loja}, loja)
        self.assertEqual(dados["negocio"]["zona"], "sul")
        self.assertFalse(dados["cadastro"]["negocio_pendente"])
        self.assertEqual(Store().negocio(), dados["negocio"])

    def test_conferir_despensa_avanca_sem_lancar_compra(self):
        antes = api.tratar("GET", "/api/estado")[1]
        self.assertFalse(antes["despensa_consultada"])
        status, conferida = api.tratar("POST", "/api/despensa/conferir")
        self.assertEqual(status, 200)
        self.assertTrue(conferida["itens"])
        self.assertTrue(conferida["estado"]["despensa_consultada"])
        self.assertEqual(conferida["estado"]["orcamento"], antes["orcamento"])
        self.assertEqual(api.tratar("POST", "/api/despensa/conferir"), (status, conferida))
        self.assertTrue(api.tratar("GET", "/api/estado")[1]["despensa_consultada"])

    def test_carrinho_pela_tela_aparece_para_a_consultora(self):
        status, dados = api.tratar("POST", "/api/carrinho/itens", {"nome": "Couve", "preco_total": "12,00", "quantidade": 1, "unidade": "maço"})
        self.assertEqual(status, 201)
        self.assertEqual(dados["orcamento"]["saldo"], 68.0)
        item_id = dados["carrinho"][0]["id"]
        self.assertEqual(call("carrinho_consultar")["resumo"]["carrinho"], 12.0)  # mesma base que a consultora lê
        status, dados = api.tratar("PATCH", f"/api/carrinho/itens/{item_id}", {"quantidade": 2})
        self.assertEqual((status, dados["orcamento"]["carrinho"]), (200, 24.0))
        status, dados = api.tratar("DELETE", f"/api/carrinho/itens/{item_id}")
        self.assertEqual((status, dados["carrinho"]), (200, []))
        api.tratar("POST", "/api/carrinho/itens", {"nome": "Alho", "preco_total": 5})
        status, dados = api.tratar("DELETE", "/api/carrinho")
        self.assertEqual((status, dados["orcamento"]["saldo"]), (200, 80.0))

    def test_consultora_mexe_e_a_tela_ve(self):
        call("carrinho_adicionar", nome="couve", preco_total=12)
        self.assertEqual(api.tratar("GET", "/api/estado")[1]["orcamento"]["saldo"], 68.0)

    def test_estouro_devolve_409_com_o_estado(self):
        status, dados = api.tratar("POST", "/api/carrinho/itens", {"nome": "Lagosta", "preco_total": 95})
        self.assertEqual(status, 409)
        self.assertEqual(dados["falta"], 15.0)
        self.assertEqual(dados["estado"]["orcamento"]["saldo"], 80.0)

    def test_erros_de_entrada(self):
        casos = [
            ("POST", "/api/carrinho/itens", {"nome": "Sal"}, 400),
            ("POST", "/api/carrinho/itens", {"nome": "Sal", "preco_total": "abc"}, 400),
            ("POST", "/api/carrinho/itens", {"nome": "Sal", "preco_total": True}, 400),
            ("POST", "/api/carrinho/itens", {"nome": "Sal", "preco_total": float("inf")}, 400),
            ("POST", "/api/carrinho/itens", ["lista"], 400),
            ("PATCH", "/api/carrinho/itens/abc", {"quantidade": 2}, 400),
            ("PATCH", "/api/carrinho/itens/99", {"quantidade": 2}, 404),
            ("DELETE", "/api/carrinho/itens/99", None, 404),
            ("PUT", "/api/negocio", {"tipo": "banca"}, 400),
            ("GET", "/api/carrinho", None, 405),
            ("GET", "/api/nada", None, 404),
            ("GET", "/fora", None, 404),
        ]
        for metodo, caminho, corpo, esperado in casos:
            status, dados = api.tratar(metodo, caminho, corpo)
            self.assertEqual(status, esperado, (metodo, caminho, corpo, dados))
            self.assertIn("erro", dados)

    def test_perfil_do_negocio_pela_tela(self):
        status, dados = api.tratar("PUT", "/api/negocio", {"tipo": "restaurante_hotel", "alimentacao": ["caseira", "doces", "pizza"], "estado": "SP", "cidade": "São Paulo"})
        self.assertEqual(status, 200)
        self.assertEqual(dados["negocio"]["tipo"], "restaurante_hotel")
        self.assertEqual([o["id"] for o in dados["opcoes"]["alimentacao"]][:3], ["caseira", "marmita", "pizza"])
        self.assertNotIn("vende", dados["opcoes"])
        consulta = call("carrinho_consultar")
        self.assertEqual(consulta["negocio"]["alimentacao"], ["caseira", "doces", "pizza"])
        self.assertIn("Restaurante de hotel", consulta["negocio_resumo"])
        self.assertEqual(api.tratar("PUT", "/api/negocio", {"alimentacao": ["sushi"]})[0], 400)

    def test_ida_e_volta_http_de_verdade(self):
        servidor = api.criar_servidor("127.0.0.1", 0)
        porta = servidor.server_address[1]
        thread = threading.Thread(target=servidor.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{porta}"
        try:
            def pedir(metodo, caminho, corpo=None, bruto=None):
                dados = bruto if bruto is not None else (json.dumps(corpo).encode() if corpo is not None else None)
                req = urllib.request.Request(base + caminho, data=dados, method=metodo, headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        return resp.status, json.loads(resp.read())
                except urllib.error.HTTPError as exc:
                    with exc:
                        return exc.code, json.loads(exc.read())

            for config in ("SABOR_DA_MARIA_DB", "SABOR_DA_MARIA_XLSX"):
                with self.subTest(config=config), patch.dict(os.environ, {config: self._tmp.name}):
                    with self.assertLogs(api.logger, level="ERROR"):
                        status, dados = pedir("GET", "/api/saude")
                    self.assertEqual((status, dados), (503, {"erro": "banco de dados indisponível"}))

            self.assertEqual(pedir("GET", "/api/saude"), (200, {"status": "ok"}))
            status, dados = pedir("POST", "/api/carrinho/itens", {"nome": "Couve", "preco_total": 12})
            self.assertEqual((status, dados["orcamento"]["saldo"]), (201, 68.0))
            status, dados = pedir("POST", "/api/carrinho/itens", {"nome": "Lagosta", "preco_total": 95})
            self.assertEqual((status, dados["falta"]), (409, 27.0))
            self.assertEqual(pedir("POST", "/api/carrinho/itens", bruto=b"{nao e json")[0], 400)
        finally:
            servidor.shutdown()
            servidor.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
