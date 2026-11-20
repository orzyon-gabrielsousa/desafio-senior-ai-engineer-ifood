---
name: gestao-estoque-despesas
description: "Registra o lote ao concluir a montagem e explica as quantidades da despensa, as despesas e o saldo."
version: 1.0.0
author: Gabriel Sousa
license: MIT
metadata:
  hermes:
    tags: [Despensa, Despesas, Cardápio, Dona Maria]
    requires_toolsets: [sabor_da_maria]
    requires_tools: [preco_sugerir, cardapio_consultar, carrinho_consultar, despensa_registrar_compras]
---

# Gestão de estoque e despesas

1. A etapa 4 termina no aceite explícito de uma versão viável. Use `preco_sugerir` para gravar o lote e suas quantidades e custos. A ferramenta separa os insumos do estoque disponível. Se há um derivado, registre a matéria-prima do estoque. Itens omitidos ficam nas observações da ficha.
2. Mostre o orçamento inicial, as despesas e o saldo devolvidos pela ferramenta. `saldo_gerencial` é `inicial − despesas`. As despesas incluem o valor do estoque destinado aos lotes e os valores de compras novas pagos ou comprometidos, sem contar o mesmo insumo duas vezes. Consulte o histórico para detalhar a quantidade e o custo de cada insumo.
3. Para decidir se um complemento cabe no orçamento do desafio, use o campo `saldo`: ele indica quanto ainda está disponível para compras novas. O estoque já pago entra no CMV e nas despesas do lote. Uma compra realizada exige a confirmação do que foi comprado e do valor pago antes de usar `despensa_registrar_compras`.
4. Na etapa 5, registre o preço escolhido com `cardapio_registrar`. Reaproveite o lote existente. Em retomadas, leia o estado antes de agir e só relate os valores calculados e persistidos.
