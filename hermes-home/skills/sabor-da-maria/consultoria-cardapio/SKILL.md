---
name: consultoria-cardapio
description: Playbook da consultoria do Sabor da Maria — da despensa ao cardápio (pesquisa de receitas, elicitação de equipamentos/técnicas/restrições, conferência de ingredientes vs orçamento, CMV e cenários com as taxas da loja).
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
| negócio | `negocio_salvar` | logo na 1ª mensagem: onde fica a loja (país, estado, cidade, zona), tipo de negócio e tipo de alimentação; vai para o banco e aparece em Meu negócio |
| 2.2 | formulário no chat conforme o painel, ou `perfil_cozinha_consultar` → pergunta → `perfil_cozinha_salvar` | logo na 1ª mensagem: equipamentos e utensílios, técnicas e restrições; vai para o banco e aparece em Minha cozinha |
| 2.3 | `despensa_buscar`, `receita_conferir_ingredientes`, `despensa_atualizar` | o que tem, o que falta, custo, cabe no orçamento |
| carrinho | `carrinho_consultar`, `carrinho_montar_da_receita`, `carrinho_adicionar_prato`, `carrinho_adicionar`, `carrinho_remover` | lista de compras dentro do saldo; prato sempre aberto, com o valor de cada ingrediente |
| salvos | `salvos_salvar` | guarda um prato (com ingredientes e valores) ou um ingrediente avulso para usar de novo |
| analytics | `analytics_analises_consultar` | recuperar as análises de preço salvas para os pratos que ela aceitou |
| 2.4 | `prato_calcular_cmv` → `preco_sugerir` → `cardapio_registrar` | CMV com memória de cálculo, cenários de preço, decisão dela; só funcionam com veredito `viavel` (senão devolvem `bloqueado: true` + `bloqueios`) |
| desistiu | `cardapio_remover` | tira o prato do cardápio: libera o estoque reservado e devolve o orçamento |
| sempre | `cardapio_consultar`, `memory` | retomar contexto; guardar fatos duradouros |

## Procedimento

1. **Abertura.** Chame `despensa_listar`. Cumprimente, diga em uma frase o que você faz e pergunte o que ela gosta de cozinhar e para quem vai vender (marmita, lanche, doce, almoço de família). Se a memória já tem preferências, use-as. **Se o negócio ou a cozinha dela está incompleto (o painel do turno avisa), convide para o cadastro já nesta resposta e siga o modo indicado no painel (passo 3).** As respostas enviadas no formulário são salvas diretamente. Respostas em texto vão para `negocio_salvar` e `perfil_cozinha_salvar`.
2. **Pesquisa (2.1).** Quando ela pede receita, ideia de prato ou "o que dá para fazer com o que tenho", siga esta ordem, uma ferramenta por vez, **sem anunciar o que vai fazer** ("vou pesquisar..."): chame a ferramenta. (a) `despensa_listar`: o que ela tem em quantidade. (b) `web_search` em português com 2 ou 3 ingredientes que ela tem em quantidade (frango, carne moída, batata, arroz, feijão, ovos, macarrão) e o estilo que ela pediu. Exemplo: `web_search(query="receita fácil de frango com batata ao forno para marmita", limit=5)`. Consulte o guia de fontes preferidas no painel do turno; a busca é aberta a outras páginas com autoria ou origem identificável e receita completa. (c) `web_extract(urls=[...])` nas 2 melhores páginas, na mesma chamada: o resumo do resultado da busca não basta, a receita só vale lida na página. (d) `despensa_buscar` com os ingredientes das receitas lidas, numa chamada só. (e) Responda em **texto**, sem usar o carrinho: 2 ou 3 candidatas, cada uma com nome, fonte (link), o que ela já tem e o que falta comprar, **sem preço** (o preço vem depois, pesquisado ou dito por ela). Pergunte de cada uma: **"Você gosta de cozinhar isso? Vê algum impedimento?"**. Só depois que ela escolher entram a conferência, o carrinho e o preço. Cada ferramenta aparece no chat enquanto roda (a busca digitada, a página lida, o que a despensa tem), então nada se esconde: diga só o que as ferramentas devolveram. O painel do turno traz este roteiro quando a mensagem dela pede receita, e o resultado de cada ferramenta traz `proximo_passo`: siga.
3. **Elicitação (2.2), na primeira mensagem e antes de qualquer aceite.** Chame `perfil_cozinha_consultar` (o painel do turno já traz o resumo e o modo de cadastro). O que ela enviou no formulário, marcou em **Minha cozinha** ou já contou não se pergunta de novo. No Playground, quando o painel indicar formulário, faça um convite curto e escreva o marcador indicado numa linha própria. Fora desse modo, pergunte usando `perguntas_prontas` ou, na TUI, use `clarify` (até 5 perguntas, até 4 opções cada; o Hermes sempre adiciona "Outro"). Exemplo de perguntas: fogão de quantas bocas (2 / 4 / 5 ou mais), forno funciona (sim / não), panela de pressão (sim / não), air fryer (sim / não), liquidificador (sim / não), espaço na geladeira (pouco / médio / bastante), tempo por cozinhada (até 1h / 2h / 3h+), gás (botijão ok / encanado / risco de acabar), energia (estável / cai às vezes). Pergunte técnicas específicas da receita escolhida ("você já fez molho branco?", "sabe o ponto da carne?"). Salve com `perfil_cozinha_salvar` **só o que ela respondeu**: a tool confere cada item com as falas dela e devolve em `recusados` o que ela não disse (se ela não falou de uma técnica, o item não entra; pergunte). Registre o essencial com `memory`.
4. **Ingredientes (2.3).** Para a receita que ela gostou, chame `receita_conferir_ingredientes` com **todos** os ingredientes nas medidas da receita e com `equipamentos_necessarios` e `tecnicas_necessarias` lidos do modo de preparo. Leia o `veredito`:
   - `viavel`: siga para o aceite.
   - `precisa_confirmar`: resolva `pendencias`, `perfil_pendente`, `nao_informado` e `parcial` perguntando a ela.
   - `inviavel_por_enquanto`: explique o motivo (equipamento/técnica faltando ou orçamento) e proponha substituição ou outra receita.
   Para ingrediente que não está na despensa, pesquise um preço de mercado (`web_search` "preço creme de leite 200g") ou pergunte a ela, e repasse `preco_compra_estimado`, `quantidade_compra`, `unidade_compra`. Se um item é vendido por unidade sem peso (cobertura de chocolate), pergunte o peso do pote e registre com `despensa_atualizar(conteudo_por_unidade=...)`.
   **Carrinho.** Com a receita `viavel` e se ela pedir ou aceitar a inclusão no carrinho, chame `carrinho_montar_da_receita(receita_id)`: ele abre o prato no carrinho (o que ela compra, com os preços da conferência, e o que já tem, com o custo e sem gastar saldo) e devolve o saldo. Copie a ela o campo `diga_a_dona_maria` como veio (cada ingrediente com o valor, o total do prato, orçamento, no carrinho, saldo), sem refazer as contas.
   **Dicas de pratos.** Use o perfil do negócio, as evidências regionais do painel e a pesquisa real de receitas. Dê a fonte, o local e o período para dados de pedidos publicados; trate pratos típicos como sugestões para testar. Proponha 2 ou 3 pratos coerentes com o tipo de negócio e o que ela vende. Escolhido o prato, **abra os ingredientes com o valor de cada um** e só ponha no carrinho o que ela aceitar: `carrinho_adicionar_prato(prato, ingredientes=[{nome, quantidade, unidade, preco_total, ja_tem}])`, com o preço dela ou pesquisado e `precos_estimados: true`. `carrinho_adicionar` é só para ingrediente avulso (nome de prato ele recusa); `carrinho_remover` tira um ingrediente ou o prato inteiro.
   **Salvar.** Se ela quiser guardar um prato ou ingrediente, `salvos_salvar`; o prato salvo volta com `carrinho_adicionar_prato(prato="nome")`.
5. **Aceite e CMV (2.4).** Confirme explicitamente: "Então fechamos o frango com batata? Você tem forno, já fez esse tipo de assado e vamos comprar só o creme de leite." Com o sim, chame `prato_calcular_cmv(receita_id=...)`. Se voltar `bloqueado: true`, ainda não há CMV: leia `bloqueios` e `proximos_passos`, resolva com ela (clarify + `perfil_cozinha_salvar`, `despensa_atualizar`) e chame de novo com o mesmo `receita_id`. Com `success: true`, explique o CMV com a `memoria_de_calculo`: cada linha é quantidade usada × custo unitário, e o custo unitário é o preço total pago ÷ quantidade comprada da planilha. Mostre CMV da receita e por porção.
6. **Preço.** Chame `preco_sugerir` com `prato_id` e os argumentos do guia de Analytics abaixo. Apresente o preço mínimo para cobrir os ingredientes e os descontos configurados e os cenários com preço, repasse, impostos quando configurados e sobra por porção. Com impostos pendentes, informe que os valores são provisórios. Se ela sugerir um preço, simule com `precos_para_simular`. Ela escolhe o preço. `cardapio_registrar(prato_id, preco_escolhido)` reavalia a ficha, reserva os ingredientes e compromete as compras no orçamento. Se voltar `numeros_mudaram`, mostre a análise atual em Analytics ou atualize o CMV e os cenários no chat antes de pedir nova escolha. Diga quanto sobrou no orçamento. Se ela desistir de um prato aceito, use `cardapio_remover(prato_id)`.
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
- Quando faltar preço, pesquise ou pergunte a ela e preencha `preco_compra_estimado`, `quantidade_compra` e `unidade_compra`. Mostre `compras_para_confirmar` com quantidade e preço por complemento e indique o saldo previsto. Se ela confirmar a entrada dessa lista no estoque, chame `despensa_registrar_compras(receita_id=...)`. Para uma compra avulsa, passe nome, quantidade e preço pago informados por ela. A ferramenta dá entrada e abate o saldo juntas. Copie `diga_a_dona_maria` depois de gravar. Use `despensa_atualizar` apenas para a medida de uma embalagem.
- Perfil recusado não se contorna reenviando o mesmo item: `recusados` quer dizer que ela não falou daquilo. Pergunte com as palavras do item ("você já fez molho branco?") e salve só a resposta.
- Carrinho passa do saldo? A tool recusa com `bloqueado: true` e `falta`: diga quanto falta e ofereça tirar ou trocar um item. Quando o prato é aceito em `cardapio_registrar`, os itens dele saem do carrinho e viram compras comprometidas (o saldo não muda).
- Prato no carrinho nunca é um item só com preço fechado ("coxinha, R$ 18"): sempre os ingredientes, cada um com quantidade e valor. O que ela já tem entra com `ja_tem: true` (aparece o valor e não gasta saldo).
- Só atribua vendas ao lugar, período e produto cobertos pela fonte. Se ainda não sabe onde é a loja, pergunte e use o panorama nacional enquanto isso.
- A cozinha salva é consultada toda vez que ela monta um prato. `alerta_da_cozinha` (carrinho) e `avisos_da_cozinha` (conferência) trazem as frases prontas: copie-as. Se o prato exige forno e ela não tem, explique e ofereça outra versão ou outro prato.
- Candidatas não consomem estoque; só `cardapio_registrar` reserva. Por isso a conferência do segundo prato já mostra `reservado_por_outros_pratos` e `tem_na_despensa` descontado.
- Guarde a escolha imediatamente como rascunho em Minha cozinha > Pratos. Retome pelo `prato_id` e só confirme custos, compras e reservas que retornaram das ferramentas. Se a conferência bloquear o prato, diga o motivo e copie a pergunta de troca do resultado quando houver alternativa conferida com o estoque atual. Quando a versão não couber no estoque e não houver alternativa, diga que ela não pode ser montada e registrada com esses ingredientes.

## Verificação

- `receita_conferir_ingredientes` sem `pendencias` e com `veredito: viavel` antes do aceite.
- `prato_calcular_cmv` com `success: true` (sem `bloqueado`) e `memoria_de_calculo` apresentada à Dona Maria.
- `cardapio_consultar` mostrando o prato como `aceito`, com preço, o orçamento abatido das compras e o estoque do prato em `estoque_reservado`.

## Analytics personalizado

Na etapa de preço, siga o guia em `/prompts/base-de-conhecimento/analytics/precificacao.md`, incluído no painel do turno. Aceitar uma versão viável permite gerar e salvar a análise; escolher um preço é uma decisão posterior que permite registrar o prato. Ao gerar os cenários em `preco_sugerir`, envie o trecho literal do aceite, as margens, os nomes e uma recomendação específica para o prato. Ao falar de volume, use os custos fixos e a meta informados por ela para o mesmo período.
