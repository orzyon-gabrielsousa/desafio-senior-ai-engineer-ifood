---
name: consultoria-cardapio
description: Playbook da consultoria do Sabor da Maria — da despensa ao cardápio (pesquisa de receitas, elicitação de equipamentos/técnicas/restrições, conferência de ingredientes vs orçamento, CMV e cenários de preço com taxa de 10%).
version: 1.0.0
author: Gabriel Sousa
license: MIT
metadata:
  hermes:
    tags: [Cozinha, Delivery, Precificação, CMV, Dona Maria]
    requires_toolsets: [web, sabor_da_maria]
    requires_tools: [web_search, web_extract, receita_conferir_ingredientes, prato_calcular_cmv, preco_sugerir]
---

# Consultoria de cardápio do Sabor da Maria

## Quando usar

Sempre que a Dona Maria quiser montar ou revisar o cardápio: descobrir o que cozinhar com a despensa, saber se consegue produzir um prato, o que precisa comprar e quanto cobrar.

## Referência rápida

| Etapa | Tool | Para quê |
|---|---|---|
| início | `despensa_listar` | estoque, custo unitário (R$/kg, R$/L, R$/un) e orçamento restante |
| 2.1 | `web_search` → `web_extract` | receitas reais com fonte; extração traz ingredientes estruturados |
| 2.2 | `perfil_cozinha_consultar` → `clarify` → `perfil_cozinha_salvar` | checklist de equipamentos, técnicas e restrições |
| 2.3 | `despensa_buscar`, `receita_conferir_ingredientes`, `despensa_atualizar` | o que tem, o que falta, custo, cabe no orçamento |
| 2.4 | `prato_calcular_cmv` → `preco_sugerir` → `cardapio_registrar` | CMV com memória de cálculo, cenários de preço, decisão dela |
| sempre | `cardapio_consultar`, `memory` | retomar contexto; guardar fatos duradouros |

## Procedimento

1. **Abertura.** Chame `despensa_listar`. Cumprimente, diga em uma frase o que você faz e pergunte o que ela gosta de cozinhar e para quem vai vender (marmita, lanche, doce, almoço de família). Se a memória já tem preferências, use-as.
2. **Pesquisa (2.1).** Monte 1 a 3 buscas em português combinando 2 ou 3 ingredientes que ela tem em quantidade (frango, carne moída, batata, arroz, feijão, ovos, macarrão) com o estilo que ela pediu. Exemplo: `web_search(query="receita fácil de frango com batata ao forno para marmita", limit=5)`. Prefira sites de receita conhecidos (TudoGostoso, Panelinha, Receitas Nestlé, Ana Maria Braga, Cybercook). Extraia as melhores: `web_extract(urls=[...])`. Apresente 2 ou 3 candidatas: nome, fonte, rendimento, tempo e por que encaixam na despensa. Pergunte: gosta de cozinhar isso? Vê algum impedimento?
3. **Elicitação (2.2), antes de qualquer aceite.** Chame `perfil_cozinha_consultar`. Para cada pendência, pergunte com `clarify` (até 5 perguntas, até 4 opções cada; o Hermes sempre adiciona "Outro"). Exemplo de perguntas: fogão de quantas bocas (2 / 4 / 5 ou mais), forno funciona (sim / não), panela de pressão (sim / não), air fryer (sim / não), liquidificador (sim / não), espaço na geladeira (pouco / médio / bastante), tempo por cozinhada (até 1h / 2h / 3h+), gás (botijão ok / encanado / risco de acabar), energia (estável / cai às vezes). Pergunte técnicas específicas da receita escolhida ("você já fez molho branco?", "sabe o ponto da carne?"). Salve tudo com `perfil_cozinha_salvar` e registre o essencial com `memory`.
4. **Ingredientes (2.3).** Para a receita que ela gostou, chame `receita_conferir_ingredientes` com **todos** os ingredientes nas medidas da receita e com `equipamentos_necessarios` e `tecnicas_necessarias` lidos do modo de preparo. Leia o `veredito`:
   - `viavel`: siga para o aceite.
   - `precisa_confirmar`: resolva `pendencias`, `perfil_pendente`, `nao_informado` e `parcial` perguntando a ela.
   - `inviavel_por_enquanto`: explique o motivo (equipamento/técnica faltando ou orçamento) e proponha substituição ou outra receita.
   Para ingrediente que não está na despensa, pesquise um preço de mercado (`web_search` "preço creme de leite 200g") ou pergunte a ela, e repasse `preco_compra_estimado`, `quantidade_compra`, `unidade_compra`. Se um item é vendido por unidade sem peso (cobertura de chocolate), pergunte o peso do pote e registre com `despensa_atualizar(conteudo_por_unidade=...)`.
5. **Aceite e CMV (2.4).** Confirme explicitamente: "Então fechamos o frango com batata? Você tem forno, já fez esse tipo de assado e vamos comprar só o creme de leite." Com o sim, chame `prato_calcular_cmv(nome, porcoes, receita_id=...)`. Explique o CMV com a `memoria_de_calculo`: cada linha é quantidade usada × custo unitário, e o custo unitário é o preço total pago ÷ quantidade comprada da planilha. Mostre CMV da receita e por porção.
6. **Preço.** Chame `preco_sugerir(cmv_por_porcao, prato_id)`. Apresente: a taxa de 10% (de cada R$ 10 vendidos ela recebe R$ 9), o preço mínimo (lucro zero) e os cenários com preço, quanto ela recebe e lucro por porção. Se ela sugerir um preço, simule com `precos_para_simular`. Pergunte qual ela quer adotar. **Ela decide.** Registre com `cardapio_registrar(prato_id, preco_escolhido)` e diga quanto sobrou do orçamento.
7. **Fechamento.** `cardapio_consultar` para resumir cardápio, compras e orçamento. Salve na memória as decisões. Pergunte se quer montar mais um prato.

## Exemplo de chamada (2.3)

```json
{"nome": "Frango com batatas ao forno", "fonte_url": "https://...", "porcoes": 4,
 "ingredientes": [
   {"nome": "peito de frango", "quantidade": 1, "unidade": "kg"},
   {"nome": "batata", "quantidade": 1, "unidade": "kg"},
   {"nome": "cebola média", "quantidade": 1, "unidade": "un"},
   {"nome": "dentes de alho", "quantidade": 3, "unidade": "dente"},
   {"nome": "azeite", "quantidade": 2, "unidade": "colher de sopa"},
   {"nome": "creme de leite", "quantidade": 200, "unidade": "g", "item_despensa": "",
    "preco_compra_estimado": 3.5, "quantidade_compra": 200, "unidade_compra": "g"},
   {"nome": "sal", "quantidade": 5, "unidade": "g"}],
 "equipamentos_necessarios": ["forno", "assadeira"],
 "tecnicas_necessarias": ["assado", "refogado"], "tempo_preparo_min": 60}
```

## Armadilhas

- Receita sem quantidade ("a gosto") precisa de uma estimativa numérica (sal 5 g, salsinha 10 g); diga que estimou.
- "1 xícara de farinha" vira gramas por densidade: a tool marca `estimativa: true`. Repasse se o item pesar no custo.
- Não confunda o custo da compra complementar (o pacote inteiro, que sai do orçamento de R$ 80) com o custo que entra no CMV (só a parte usada no prato). A tool devolve os dois.
- Ingredientes ambíguos (feijão carioca ou preto? mussarela ou parmesão?) aparecem com `confiavel: false` em `despensa_buscar`: pergunte.
- O modelo não deve calcular CMV de cabeça. Sempre pela tool, sempre mostrando a memória de cálculo.
- Nunca registre no cardápio sem ela escolher o preço explicitamente.

## Verificação

- `receita_conferir_ingredientes` sem `pendencias` e com `veredito: viavel` antes do aceite.
- `prato_calcular_cmv` com `memoria_de_calculo` apresentada à Dona Maria.
- `cardapio_consultar` mostrando o prato como `aceito`, com preço, e o orçamento abatido das compras.
