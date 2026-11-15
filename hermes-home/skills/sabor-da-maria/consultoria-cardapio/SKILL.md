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
| carrinho | `carrinho_consultar`, `carrinho_montar_da_receita`, `carrinho_adicionar_prato`, `carrinho_adicionar`, `carrinho_remover` | lista de compras dentro do saldo; prato sempre aberto, com o valor de cada ingrediente |
| salvos | `salvos_salvar` | guarda um prato (com ingredientes e valores) ou um ingrediente avulso para usar de novo |
| mais vendidos | `analytics_consultar` | pratos que mais vendem na zona, cidade, estado, região e país da loja (base de exemplo) |
| 2.4 | `prato_calcular_cmv` → `preco_sugerir` → `cardapio_registrar` | CMV com memória de cálculo, cenários de preço, decisão dela; só funcionam com veredito `viavel` (senão devolvem `bloqueado: true` + `bloqueios`) |
| desistiu | `cardapio_remover` | tira o prato do cardápio: libera o estoque reservado e devolve o orçamento |
| sempre | `cardapio_consultar`, `memory` | retomar contexto; guardar fatos duradouros |

## Procedimento

1. **Abertura.** Chame `despensa_listar`. Cumprimente, diga em uma frase o que você faz e pergunte o que ela gosta de cozinhar e para quem vai vender (marmita, lanche, doce, almoço de família). Se a memória já tem preferências, use-as.
2. **Pesquisa (2.1).** Monte 1 a 3 buscas em português combinando 2 ou 3 ingredientes que ela tem em quantidade (frango, carne moída, batata, arroz, feijão, ovos, macarrão) com o estilo que ela pediu. Exemplo: `web_search(query="receita fácil de frango com batata ao forno para marmita", limit=5)`. Prefira sites de receita conhecidos (TudoGostoso, Panelinha, Receitas Nestlé, Ana Maria Braga, Cybercook). Extraia as melhores: `web_extract(urls=[...])`. Apresente 2 ou 3 candidatas: nome, fonte, rendimento, tempo e por que encaixam na despensa. Pergunte: gosta de cozinhar isso? Vê algum impedimento?
3. **Elicitação (2.2), antes de qualquer aceite.** Chame `perfil_cozinha_consultar`. Para cada pendência, pergunte com `clarify` (até 5 perguntas, até 4 opções cada; o Hermes sempre adiciona "Outro"). Exemplo de perguntas: fogão de quantas bocas (2 / 4 / 5 ou mais), forno funciona (sim / não), panela de pressão (sim / não), air fryer (sim / não), liquidificador (sim / não), espaço na geladeira (pouco / médio / bastante), tempo por cozinhada (até 1h / 2h / 3h+), gás (botijão ok / encanado / risco de acabar), energia (estável / cai às vezes). Pergunte técnicas específicas da receita escolhida ("você já fez molho branco?", "sabe o ponto da carne?"). Salve com `perfil_cozinha_salvar` **só o que ela respondeu**: a tool confere cada item com as falas dela e devolve em `recusados` o que ela não disse (se ela não falou de uma técnica, o item não entra; pergunte). Registre o essencial com `memory`.
4. **Ingredientes (2.3).** Para a receita que ela gostou, chame `receita_conferir_ingredientes` com **todos** os ingredientes nas medidas da receita e com `equipamentos_necessarios` e `tecnicas_necessarias` lidos do modo de preparo. Leia o `veredito`:
   - `viavel`: siga para o aceite.
   - `precisa_confirmar`: resolva `pendencias`, `perfil_pendente`, `nao_informado` e `parcial` perguntando a ela.
   - `inviavel_por_enquanto`: explique o motivo (equipamento/técnica faltando ou orçamento) e proponha substituição ou outra receita.
   Para ingrediente que não está na despensa, pesquise um preço de mercado (`web_search` "preço creme de leite 200g") ou pergunte a ela, e repasse `preco_compra_estimado`, `quantidade_compra`, `unidade_compra`. Se um item é vendido por unidade sem peso (cobertura de chocolate), pergunte o peso do pote e registre com `despensa_atualizar(conteudo_por_unidade=...)`.
   **Carrinho.** Com a receita `viavel`, chame `carrinho_montar_da_receita(receita_id)`: ele abre o prato no carrinho (o que ela compra, com os preços da conferência, e o que já tem, com o custo e sem gastar saldo) e devolve o saldo. Copie a ela o campo `diga_a_dona_maria` como veio (cada ingrediente com o valor, o total do prato, orçamento, no carrinho, saldo), sem refazer as contas.
   **Dicas de pratos.** Comece por `carrinho_consultar` (perfil do negócio com o lugar da loja, itens, saldo, salvos) e por `analytics_consultar` (os mais vendidos priorizados pela zona e cidade dela; diga que é a base de exemplo). Proponha 2 ou 3 pratos coerentes com o tipo de negócio e o que ela vende. Escolhido o prato, **abra os ingredientes com o valor de cada um** e só ponha no carrinho o que ela aceitar: `carrinho_adicionar_prato(prato, ingredientes=[{nome, quantidade, unidade, preco_total, ja_tem}])`, com o preço dela ou pesquisado e `precos_estimados: true`. `carrinho_adicionar` é só para ingrediente avulso (nome de prato ele recusa); `carrinho_remover` tira um ingrediente ou o prato inteiro.
   **Salvar.** Se ela quiser guardar um prato ou ingrediente, `salvos_salvar`; o prato salvo volta com `carrinho_adicionar_prato(prato="nome")`.
5. **Aceite e CMV (2.4).** Confirme explicitamente: "Então fechamos o frango com batata? Você tem forno, já fez esse tipo de assado e vamos comprar só o creme de leite." Com o sim, chame `prato_calcular_cmv(receita_id=...)`. Se voltar `bloqueado: true`, ainda não há CMV: leia `bloqueios` e `proximos_passos`, resolva com ela (clarify + `perfil_cozinha_salvar`, `despensa_atualizar`) e chame de novo com o mesmo `receita_id`. Com `success: true`, explique o CMV com a `memoria_de_calculo`: cada linha é quantidade usada × custo unitário, e o custo unitário é o preço total pago ÷ quantidade comprada da planilha. Mostre CMV da receita e por porção.
6. **Preço.** Chame `preco_sugerir(prato_id)` (o CMV usado é o calculado pela tool). Apresente: a taxa de 10% (de cada R$ 10 vendidos ela recebe R$ 9), o preço mínimo (lucro zero) e os cenários com preço, quanto ela recebe e lucro por porção. Se ela sugerir um preço, simule com `precos_para_simular`. Pergunte qual ela quer adotar. **Ela decide.** Registre com `cardapio_registrar(prato_id, preco_escolhido)`: ele reavalia o prato contra o estado atual, reserva na despensa o que o prato usa e abate as compras do orçamento. Se recusar com `numeros_mudaram` (outro prato reservou estoque), recalcule o CMV, apresente de novo e peça a escolha outra vez. Diga quanto sobrou do orçamento. Se ela desistir de um prato aceito, `cardapio_remover(prato_id)`.
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
- Não tente contornar um `bloqueado: true`: a tool não vai aceitar; o caminho é resolver o bloqueio com ela e salvar a resposta.
- Pendência de preço não se resolve inventando compra: `despensa_atualizar(compra=...)` só registra compra que ela fez de verdade. Para o que falta, pesquise o preço ou pergunte a ela e repasse em `preco_compra_estimado` / `quantidade_compra` / `unidade_compra`.
- Perfil recusado não se contorna reenviando o mesmo item: `recusados` quer dizer que ela não falou daquilo. Pergunte com as palavras do item ("você já fez molho branco?") e salve só a resposta.
- Carrinho passa do saldo? A tool recusa com `bloqueado: true` e `falta`: diga quanto falta e ofereça tirar ou trocar um item. Quando o prato é aceito em `cardapio_registrar`, os itens dele saem do carrinho e viram compras comprometidas (o saldo não muda).
- Prato no carrinho nunca é um item só com preço fechado ("coxinha, R$ 18"): sempre os ingredientes, cada um com quantidade e valor. O que ela já tem entra com `ja_tem: true` (aparece o valor e não gasta saldo).
- Os números do Analytics são de exemplo: diga isso toda vez que citar preço médio ou posição, e não os apresente como estatística real. Sem a localização da loja só vale o ranking do país.
- Candidatas não consomem estoque; só `cardapio_registrar` reserva. Por isso a conferência do segundo prato já mostra `reservado_por_outros_pratos` e `tem_na_despensa` descontado.

## Verificação

- `receita_conferir_ingredientes` sem `pendencias` e com `veredito: viavel` antes do aceite.
- `prato_calcular_cmv` com `success: true` (sem `bloqueado`) e `memoria_de_calculo` apresentada à Dona Maria.
- `cardapio_consultar` mostrando o prato como `aceito`, com preço, o orçamento abatido das compras e o estoque do prato em `estoque_reservado`.
