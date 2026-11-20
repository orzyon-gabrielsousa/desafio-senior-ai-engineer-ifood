import sqlite3

from sabor_da_maria import api, tools
from sabor_da_maria.store import Store

from ._base import StoreTestCase


class HistoricoComprasTests(StoreTestCase):
    def ficha(self, store, nome, ingredientes):
        resultado = tools._resolver_ingredientes(store, ingredientes)
        self.assertEqual(resultado["pendencias"], [])
        dados = {"ingredientes": ingredientes, "resultado": resultado}
        receita_id = store.salvar_receita(nome, None, 4, dados, "conferida")
        prato_id = store.salvar_prato(nome, receita_id, 4, resultado["cmv_total"], resultado["cmv_total"] / 4, dados)
        return prato_id, receita_id, resultado

    def test_estoque_usado_no_derivado_fica_no_historico_sem_novo_gasto(self):
        store = Store()
        tomate_antes = store.obter("Tomate")["disponivel_base"]
        prato_id, receita_id, resultado = self.ficha(store, "Pizza de frango", [
            {"nome": "molho de tomate", "quantidade": 150, "unidade": "g"},
            {"nome": "peito de frango", "quantidade": 300, "unidade": "g"},
        ])
        self.assertEqual(api.estado(store)["historico_compras"], {"lotes": [], "avulsas": []})
        reservas = tools._reservas_de(resultado)
        store.aceitar_prato(prato_id, 14, [], reservas)

        estado = api.estado(store)
        lote, = estado["historico_compras"]["lotes"]
        self.assertTrue(lote["ativo"])
        self.assertEqual(lote["receita_id"], receita_id)
        self.assertEqual(lote["cmv_total"], resultado["cmv_total"])
        molho, frango = lote["insumos"]
        self.assertEqual(molho["ingrediente"], "Tomate")
        self.assertEqual(molho["derivado"], "molho de tomate")
        self.assertEqual(molho["quantidade"], resultado["itens"][0]["usa_da_despensa"])
        self.assertEqual(frango["origem"], "despensa")
        self.assertEqual(estado["orcamento"]["saldo"], 80)
        self.assertAlmostEqual(store.obter("Tomate")["disponivel_base"], tomate_antes - .1875)

        store.aceitar_prato(prato_id, 15, [], reservas)
        self.assertEqual(store.historico_compras()["lotes"], [lote])
        store.remover_prato(prato_id)
        self.assertEqual(store.historico_compras()["lotes"], [{**lote, "ativo": False, "status": "encerrado"}])
        self.assertEqual(store.obter("Tomate")["disponivel_base"], tomate_antes)

    def test_lote_aceito_na_etapa_quatro_apropria_custo_e_quantidades_uma_vez(self):
        store = Store()
        tomate_antes = store.obter("Tomate")["disponivel_base"]
        prato_id, receita_id, resultado = self.ficha(store, "Pizza de batata com frango", [
            {"nome": "Batata", "quantidade": 300, "unidade": "g"},
            {"nome": "Sal", "quantidade": 5, "unidade": "g"},
            {"nome": "Manteiga", "quantidade": 10, "unidade": "g"},
            {"nome": "Molho de tomate", "quantidade": 45, "unidade": "g"},
            {"nome": "Peito de frango", "quantidade": 60, "unidade": "g"},
            {"nome": "Queijo mussarela", "quantidade": 30, "unidade": "g"},
        ])
        self.assertEqual(resultado["cmv_total"], 4.70)
        self.assertEqual(api.estado(store)["orcamento"]["despesas"], 0)
        self.assertFalse(store.historico_compras()["lotes"])
        reservas = tools._reservas_de(resultado)
        for _ in range(2):
            store.salvar_analise_preco(prato_id, {"cenarios": []}, receita_id_esperada=receita_id,
                                      reservas=reservas, da_despensa=tools._itens_da_despensa_para_carrinho(resultado), resultado=resultado)
        estado = api.estado(store)
        lote, = estado["historico_compras"]["lotes"]
        self.assertEqual((lote["status"], lote["custo_despensa"]), ("preco_pendente", 4.70))
        self.assertEqual((estado["orcamento"]["despesas"], estado["orcamento"]["saldo_gerencial"], estado["orcamento"]["saldo"]), (4.70, 75.30, 80.00))
        molho = next(i for i in lote["insumos"] if i["ingrediente"] == "Tomate")
        self.assertEqual((molho["quantidade_base"], molho["unidade_base"], molho["derivado"]), (.05625, "kg", "Molho de tomate"))
        self.assertAlmostEqual(store.obter("Tomate")["disponivel_base"], tomate_antes - .05625)
        with store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM historico_prato_insumos WHERE lote_id = ?", (lote["id"],)).fetchone()[0], 6)

        store.aceitar_prato(prato_id, 15, [], reservas)
        publicado, = store.historico_compras()["lotes"]
        self.assertEqual(publicado, {**lote, "status": "no_cardapio"})
        self.assertEqual(store.orcamento()["saldo_gerencial"], 75.30)
        store.remover_prato(prato_id)
        self.assertEqual(store.orcamento()["despesas"], 0)
        self.assertEqual(store.obter("Tomate")["disponivel_base"], tomate_antes)

    def test_complemento_do_lote_substitui_previsao_pelo_pagamento_sem_dupla_despesa(self):
        store = Store()
        prato_id, receita_id, resultado = self.ficha(store, "Arroz com jambu", [{
            "nome": "Jambu fresco", "quantidade": 100, "unidade": "g", "preco_compra_estimado": 8,
            "quantidade_compra": 250, "unidade_compra": "g",
        }])
        store.carrinho_adicionar("Item avulso", 3)
        self.assertEqual(store.orcamento()["despesas"], 0)
        store.salvar_analise_preco(prato_id, {"cenarios": []}, receita_id_esperada=receita_id, compras=resultado["compras"])
        saldo = store.orcamento()
        self.assertEqual((saldo["despesa_complementos"], saldo["despesas"], saldo["saldo_gerencial"], saldo["saldo"]), (8, 8, 72, 69))
        complemento = next(i for i in store.carrinho_itens() if i["receita_id"] == receita_id)
        store.registrar_compra("Jambu fresco", .25, "kg", 8, novo=True, carrinho_id=complemento["id"])
        lote, = store.historico_compras()["lotes"]
        self.assertEqual(lote["complementos"][0]["status"], "paga")
        self.assertEqual(store.orcamento()["despesas"], 8)

        ingredientes = [{"nome": "Jambu fresco", "quantidade": 100, "unidade": "g"}]
        atualizado = tools._resolver_ingredientes(store, ingredientes)
        dados = {"ingredientes": ingredientes, "resultado": atualizado}
        store.aceitar_prato(prato_id, 15, [], tools._reservas_de(atualizado), dados=dados)
        self.assertEqual(store.orcamento()["despesas"], 8)
        self.assertEqual(store.orcamento()["despesa_despensa"], 0)
        self.assertEqual(store.historico_compras()["lotes"][0]["custo_despensa"], 3.2)

    def test_lote_apos_reabrir_sai_da_despesa_mas_preserva_fotografia(self):
        store = Store()
        prato_id, receita_id, resultado = self.ficha(store, "Arroz com jambu", [{
            "nome": "Jambu fresco", "quantidade": 100, "unidade": "g", "preco_compra_estimado": 8,
            "quantidade_compra": 250, "unidade_compra": "g",
        }])
        store.salvar_analise_preco(prato_id, {"cenarios": []}, receita_id_esperada=receita_id, compras=resultado["compras"])
        self.assertEqual(store.orcamento()["despesas"], 8)
        lote, = store.historico_compras()["lotes"]
        self.assertEqual(store.carrinho_limpar(), 1)
        self.assertEqual(store.historico_compras()["lotes"], [{**lote, "ativo": False, "status": "encerrado"}])
        self.assertEqual(store.obter_prato(prato_id)["status"], "selecionado")
        self.assertEqual(store.orcamento()["despesas"], 0)

    def test_complemento_comprometido_passa_a_pago_sem_debitar_duas_vezes(self):
        store = Store()
        prato_id, receita_id, resultado = self.ficha(store, "Arroz com jambu", [{
            "nome": "Jambu fresco", "quantidade": 100, "unidade": "g",
            "preco_compra_estimado": 8, "quantidade_compra": 250, "unidade_compra": "g",
        }])
        compras = resultado["compras"]
        store.carrinho_adicionar("Jambu fresco", 8, 250, "g", receita_id=receita_id)
        self.assertEqual(store.orcamento()["saldo"], 72)
        store.aceitar_prato(prato_id, 14, compras)
        self.assertEqual(store.carrinho_itens(), [])
        self.assertEqual(store.orcamento()["saldo"], 72)
        lote, = store.historico_compras()["lotes"]
        compromisso, = lote["complementos"]
        self.assertEqual(compromisso["status"], "comprometida")
        self.assertEqual(lote["insumos"][0]["quantidade"], "100 g")
        self.assertEqual(lote["insumos"][0]["custo_uso"], 3.2)

        store.aceitar_prato(prato_id, 14, compras)
        self.assertEqual(store.historico_compras()["lotes"], [lote])
        store.registrar_compra("Jambu fresco", .25, "kg", 8, novo=True, compra_id=compromisso["id"])
        store.remover_prato(prato_id)
        historico = store.historico_compras()
        self.assertFalse(historico["lotes"][0]["ativo"])
        paga, = historico["lotes"][0]["complementos"]
        self.assertEqual((paga["id"], paga["status"], paga["valor"]), (compromisso["id"], "paga", 8))
        self.assertEqual(historico["avulsas"], [])
        self.assertEqual(store.orcamento()["saldo"], 72)
        self.assertEqual(store.orcamento()["despesas"], 8)

    def test_compra_da_ficha_registrada_antes_do_preco_aparece_no_lote_ao_aprovar(self):
        store = Store()
        receita_id = store.salvar_receita("Arroz com jambu", None, 4, {}, "conferida")
        cart = store.carrinho_adicionar("Jambu fresco", 8, .25, "kg", receita_id=receita_id)
        store.registrar_compra("Jambu fresco", .25, "kg", 8, novo=True, carrinho_id=cart["id"])
        avulsa, = store.historico_compras()["avulsas"]
        ingredientes = [{"nome": "Jambu fresco", "quantidade": 100, "unidade": "g"}]
        resultado = tools._resolver_ingredientes(store, ingredientes)
        prato_id = store.salvar_prato("Arroz com jambu", receita_id, 4, 3.2, .8,
                                     {"ingredientes": ingredientes, "resultado": resultado})
        store.aceitar_prato(prato_id, 14, resultado["compras"], tools._reservas_de(resultado))
        historico = store.historico_compras()
        self.assertEqual(historico["lotes"][0]["complementos"], [avulsa])
        self.assertEqual(historico["avulsas"], [])
        self.assertEqual(store.orcamento()["saldo"], 72)

    def test_base_antiga_reconstitui_lote_aprovado_e_preserva_compras(self):
        store = Store()
        prato_id, _, resultado = self.ficha(store, "Molho de tomate", [
            {"nome": "molho de tomate", "quantidade": 150, "unidade": "g"},
        ])
        store.aceitar_prato(prato_id, 14, [], tools._reservas_de(resultado))
        store.registrar_compra("Tomate", .25, "kg", 1.75)
        with sqlite3.connect(store.db_path) as conn:
            conn.executescript("""
                DROP TABLE historico_pratos;
                ALTER TABLE compras DROP COLUMN historico_prato_id;
                ALTER TABLE compras DROP COLUMN receita_origem_id;
            """)
        historico = Store().historico_compras()
        lote, = historico["lotes"]
        paga, = historico["avulsas"]
        self.assertEqual(lote["prato_id"], prato_id)
        self.assertEqual(lote["insumos"][0]["ingrediente"], "Tomate")
        self.assertIsNone(lote["registrado_em"])
        self.assertEqual((paga["ingrediente"], paga["valor"]), ("Tomate", 1.75))
