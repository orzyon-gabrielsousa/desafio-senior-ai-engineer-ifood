---
name: consultoria-cardapio
description: "Conduza a Dona Maria pelo chat, da despensa ao cardápio: recupere o estado, pesquise e adapte pratos, resolva pendências, salve decisões e avance para custos e preço com o aceite dela."
version: 1.2.0
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

## Condução contínua

Você gerencia o processo. Leia a jornada e a ficha em foco no painel do turno, execute o trabalho que já pode fazer e peça somente a resposta que depende da Dona Maria. Ela não precisa saber qual etapa vem depois nem abrir um menu para você avançar. As escolhas clicáveis no chat são respostas dela; os menus são atalhos de consulta e correção.

- Se ela diz “continue”, retome a decisão da etapa atual. Sem ficha, só pesquise depois da prontidão e da escolha de ajuda da IA. Se escolheu prato próprio, parta da ideia dela. Uma retomada não confirma prontidão, aceite ou preço.
- Depois de um cadastro enviado, confirme em uma frase e apresente apenas a próxima decisão: negócio concluído leva ao estoque e à prontidão, sem pesquisa automática. Não regrave o formulário nem reabra o cadastro enviado.
- Pergunte requisitos da versão, não todo o catálogo. A falta de uma resposta sobre sushi não bloqueia um assado; a falta de confirmação do forno bloqueia o aceite de uma versão que precisa dele. É possível pesquisar e criar rascunhos antes de completar a cozinha.
- Se a informação já está no painel ou em uma ferramenta atual, use-a. Só faça nova consulta para obter detalhes ausentes ou reconferir algo que mudou. Use memória e histórico para preferências e referências; confirme estoque, saldo e preços no estado atual.
- Em **Montar pratos**, use o que já sabe para preencher as medidas e conferir uma adaptação antes de pedir respostas. Resolva juntas as quantidades sem medida, as substituições e os preparos caseiros. Depois de guardar a versão, reúna numa única mensagem os fatos relevantes da cozinha que dependem da Dona Maria. Se faltar um requisito essencial, mantenha o rascunho e ofereça uma versão que possa ser conferida.
- Resolva falhas de ferramenta e argumentos com as fichas conhecidas. A falta de um ID interno não é uma tarefa para a Dona Maria. Se duas opções continuam ambíguas, mostre seus nomes e peça a escolha.
- Antes de parar por uma incompatibilidade, busque uma alternativa que caiba na despensa e na cozinha, confira-a e peça aceite da troca. Se nenhuma versão for confirmada, diga o impedimento e ofereça outro prato sem prometer um resultado inexistente.
- Preferências, condições reais, compra realizada, aceite da versão e preço são decisões dela. Você pesquisa, preenche a ficha, confere, calcula, registra e explica o resultado real. Não conclua com promessa de trabalho que pode executar agora.

## Referência rápida

| Etapa | Tool | Para quê |
|---|---|---|
| início | `despensa_listar` | estoque, custo unitário (R$/kg, R$/L, R$/un) e orçamento restante |
| 2.1 | `web_search` → `web_extract` | receitas reais com fonte; extração traz ingredientes estruturados |
| negócio | `negocio_salvar` | colher os dados necessários da loja ao longo da conversa; salvar somente o que ela informou |
| 2.2 | formulário no chat conforme o painel, ou `perfil_cozinha_consultar` → pergunta → `perfil_cozinha_salvar` | equipamentos, técnicas e restrições relevantes à receita; conhecer todos antes do aceite, sem exigir todo o catálogo |
| escolha | `pratos_selecionar` | guarda as opções que ela acabou de escolher e devolve um `prato_id` por rascunho |
| 2.3 | `despensa_buscar`, `receita_conferir_ingredientes`, `despensa_atualizar` | o que tem, o que falta, custo, cabe no orçamento |
| carrinho | `carrinho_consultar`, `carrinho_montar_da_receita`, `carrinho_adicionar_prato`, `carrinho_adicionar`, `carrinho_remover` | lista de compras dentro do saldo; prato sempre aberto, com o valor de cada ingrediente |
| salvos | `salvos_salvar` | guarda um prato (com ingredientes e valores) ou um ingrediente avulso para usar de novo |
| analytics | `analytics_analises_consultar` | recuperar as análises de preço salvas para os pratos que ela aceitou |
| 2.4 | `prato_calcular_cmv` → `preco_sugerir` → `cardapio_registrar` | CMV com memória de cálculo, cenários de preço, decisão dela; só funcionam com veredito `viavel` (senão devolvem `bloqueado: true` + `bloqueios`) |
| desistiu | `cardapio_remover` | tira o prato do cardápio: libera o estoque reservado e devolve o orçamento |
| sempre | `cardapio_consultar`, `memory` | retomar contexto; guardar fatos duradouros |

## Procedimento

1. **Abertura ou retomada.** Leia a jornada e os dados atuais do painel. Retome uma ficha em andamento antes de reiniciar a pesquisa. Sem ficha, consulte a despensa e use o negócio e as preferências conhecidos para propor um prato. Se falta informação essencial para essa direção, faça uma pergunta específica ou ofereça o cadastro clicável indicado no painel. Não obrigue quem já pediu uma receita a preencher todo o catálogo da cozinha. Respostas enviadas no formulário são salvas diretamente; respostas em texto vão para `negocio_salvar` e `perfil_cozinha_salvar`.
2. **Pesquisa (2.1).** Comece com `despensa_listar` para ver as quantidades disponíveis. Em pesquisas gerais, formule a consulta pública com `web_search` pelo objetivo de venda e o tipo de alimentação salvos. Um pedido específico neste turno orienta a consulta. Acrescente ingredientes disponíveis a partir da categoria e filtre pelas habilidades, equipamentos e tempo conhecidos. Use `web_extract` para ler a página direta e `despensa_buscar` para verificar os ingredientes. Cada candidata tem sua própria lista de ingredientes e fonte. Se ela pediu ideias, mostre primeiro as opções da prioridade com o que tem e o que falta. Outras categorias podem complementar a proposta. Pergunte quais ela prefere. Se pediu para montar um prato, prepare uma ficha única conforme o próximo tópico. Siga o `proximo_passo` das ferramentas e o painel do turno.
3. **Elicitação (2.2), concluída antes do aceite.** Use o resumo da cozinha no painel ou `perfil_cozinha_consultar` para detalhes ausentes. Reaproveite o que ela enviou no formulário, marcou em **Minha cozinha** ou já contou. No Playground, quando o painel indicar formulário, faça um convite curto e escreva `[[cadastro:cozinha]]` numa linha própria. Para o negócio, use `[[cadastro:negocio]]`. Ela também pode responder por texto. Agrupe as perguntas relevantes ao lote numa mensagem, como forno funcionando, habilidade para assar, gás, energia, refrigeração e tempo disponível. Salve com `perfil_cozinha_salvar` **só o que ela respondeu**. A ferramenta confere cada item com as falas dela e devolve as divergências em `recusados`. Registre com `memory` apenas fatos duradouros da cozinha.
4. **Ingredientes (2.3).** Para o prato que ela escolheu, chame `receita_conferir_ingredientes` com todos os insumos e quantidades da versão proposta e com `equipamentos_necessarios`, `tecnicas_necessarias` e `tempo_preparo_min`. Use o tempo publicado quando o método for o mesmo. Se estimar, considere montagem, cocção e descansos relevantes para o lote e envie a base em `tempo_preparo_base`. Compare com o tempo que ela informou ter. Procure outro método quando a versão não couber nas condições da cozinha. A ferramenta informa o `veredito`.
   - `viavel`: siga para o aceite.
   - `precisa_confirmar`: resolva o que puder pela ficha, fonte e despensa. Peça juntas as confirmações da cozinha que só ela pode dar. Se faltar uma preferência essencial ou um insumo que o preparo realmente exige, apresente as opções concretas.
   - `inviavel_por_enquanto`: explique o motivo (equipamento/técnica faltando ou orçamento) e proponha substituição ou outra receita.
   **Proximidade e complementos.** Use a fonte como referência ao montar com a despensa. Consulte candidatos por nome, sinônimo e semelhança e avalie a função culinária. Use o nome do insumo consumido e registre o correspondente da fonte em `ingrediente_original` quando houver troca. Conte o consumo da matéria-prima, o rendimento e o tempo dos derivados. Para um faltante, use equivalente adequado, omita um ingrediente dispensável ou altere o preparo para funcionar sem ele. Reúna todas as quantidades locais ainda sem medida, estimando a partir do preparo e do tamanho do lote. Preserve as medidas já confirmadas. Salve no mesmo `prato_id` os ingredientes realmente usados, `adaptacao`, `modo_preparo` próprio e duração. Anote as faltas e omissões e preserve a fonte. Apresente ajustes e estimativas juntos antes do aceite final. Cote com `web_search` apenas o complemento necessário que ela quiser manter. Se a conta depender do conteúdo desconhecido de uma embalagem já comprada, peça essa informação específica. Uma falta essencial sem alternativa fica anotada como pendência do rascunho.
   **Carrinho.** Com a receita `viavel` e se ela pedir ou aceitar a inclusão no carrinho, chame `carrinho_montar_da_receita(receita_id)`: ele abre o prato no carrinho (o que ela compra, com os preços da conferência, e o que já tem, com o custo e sem gastar saldo) e devolve o saldo. Copie a ela o campo `diga_a_dona_maria` como veio (cada ingrediente com o valor, o total do prato, orçamento, no carrinho, saldo), sem refazer as contas.
   **Dicas de pratos.** Ordene as opções conforme a prioridade de venda do cadastro e a pesquisa real de receitas. Se ela pedir dados de demanda, dê a fonte, o local e o período dos pedidos publicados. Trate pratos típicos como sugestões para testar. Escolhido o prato, **abra os ingredientes com o valor de cada um** e só ponha no carrinho o que ela aceitar: `carrinho_adicionar_prato(prato, ingredientes=[{nome, quantidade, unidade, preco_total, ja_tem}])`, com o preço dela ou pesquisado e `precos_estimados: true`. `carrinho_adicionar` é só para ingrediente avulso (nome de prato ele recusa); `carrinho_remover` tira um ingrediente ou o prato inteiro.
   **Salvar.** Se ela quiser guardar um prato ou ingrediente, `salvos_salvar`; o prato salvo volta com `carrinho_adicionar_prato(prato="nome")`.
5. **Aceite e CMV (2.4).** Peça o aceite explícito do prato na versão conferida e recapitule as condições relevantes. Com o sim, chame `prato_calcular_cmv(receita_id=...)`. Se voltar `bloqueado: true`, leia `bloqueios` e `proximos_passos`, resolva com ela (clarify + `perfil_cozinha_salvar`, `despensa_atualizar`) e chame de novo com o mesmo `receita_id`. Com `success: true`, explique o CMV com a `memoria_de_calculo`. Cada linha é quantidade usada × custo unitário, e o custo unitário é o preço total pago ÷ quantidade comprada da planilha. Mostre CMV da receita e por porção.
6. **Preço.** Chame `preco_sugerir` com `prato_id` e os argumentos do guia de Analytics abaixo. Com o aceite da versão, a ferramenta registra o lote e atualiza estoque disponível, despesas e saldo. Reaproveite os valores gravados conforme a skill `gestao-estoque-despesas`. Apresente o preço mínimo, arredondado para cima ao centavo, e cenários com a taxa fixa de **10%** do desafio, repasse `0,90 × P` e lucro `0,90 × P − CMV`. Ela escolhe o preço. `cardapio_registrar(prato_id, preco_escolhido)` reavalia a ficha e publica o preço usando o mesmo lote. Se voltar `numeros_mudaram`, mostre a análise atual antes de pedir nova escolha. Se ela desistir de um prato aceito, use `cardapio_remover(prato_id)`.
7. **Continuidade.** Use o resultado do registro ou `cardapio_consultar` para resumir cardápio, reservas, compras e orçamento. Salve decisões duradouras na memória. Se há outras fichas escolhidas em andamento, conduza a próxima pendência delas. Caso contrário, ofereça montar mais um prato aproveitando o saldo e o estoque disponíveis.

## Montar, recuperar ou adaptar um prato

- **“Monte um prato para meu cardápio”.** Ela delegou a primeira proposta. Escolha um prato com os ingredientes e as técnicas confirmados, dê um nome que descreva a versão e monte a ficha com `receita_conferir_ingredientes`. Use o rendimento da fonte ou declare um teste de quatro porções quando ela não indicou porções e a receita for criação da casa. Use medidas de preparo realistas e registro de peso cru ou cozido quando isso mudar a quantidade. Mostre a ficha e peça o aceite da versão.
- **“Sim, gostei desses dois, quero os dois”.** Use a lista de opções já mostrada. Se o painel registrou as escolhas, confirme ambas e continue usando seus IDs. Caso contrário, chame `pratos_selecionar` com os dois nomes da lista. Passe o `prato_id` correto de cada rascunho ao conferir os ingredientes. Trate cada ficha separadamente. Se o número pedido e o número de opções não coincidirem, pergunte quais entre as opções disponíveis.
- **“Monte um equivalente com esses mesmos ingredientes”.** Retome a ficha ou a sugestão da conversa e confira os itens disponíveis na despensa. Preserve o ingrediente principal e o tipo de preparo sempre que possível. Use alternativas compatíveis confirmadas no estoque e quantidades para o rendimento da nova ficha. Informe o que foi mantido, omitido ou substituído. Se usar a web, cite a fonte como referência de inspiração para a adaptação. Apresente a nova versão para aceite e mantenha a escolha anterior até ela confirmar a troca. Se nenhum equivalente couber, diga qual ingrediente ou técnica impede a montagem e ofereça uma direção concreta para seguir.
- **Se a ferramenta pede nome ou ID.** Recupere o `prato_id` dos escolhidos no painel ou o nome da sugestão na conversa. Para uma criação delegada, escolha um nome pela composição e envie `nome` com os ingredientes, as quantidades, o rendimento e os requisitos na mesma chamada. Corrija o argumento e tente de novo uma vez. Pergunte à Dona Maria apenas se restarem referências conflitantes que alterem a receita, como dois pratos incompatíveis ou uma restrição essencial.
- **Ao apresentar a ficha.** Diferencie a quantidade usada da quantidade em estoque. Use os valores e o `veredito` devolvidos pela conferência. Rascunho ou adaptação em revisão ainda aguarda aceite, confirmação de compras quando for necessário e escolha posterior do preço para registro no cardápio.

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

- Para itens a gosto ou sem medida publicada, use estimativas numéricas compatíveis com o tamanho do lote. Apresente as quantidades propostas juntas e permita que a Dona Maria ajuste qualquer uma na revisão da ficha.
- "1 xícara de farinha" vira gramas por densidade: a tool marca `estimativa: true`. Repasse se o item pesar no custo.
- Não confunda o custo da compra complementar (o pacote inteiro, que sai do orçamento de R$ 80) com o custo que entra no CMV (só a parte usada no prato). A tool devolve os dois.
- `confiavel: false` em `despensa_buscar` indica semelhança incerta, não proibição de adaptação nem probabilidade de sucesso. Avalie uma substituição culinária plausível, use o ingrediente real do estoque e explique a diferença. Pergunte apenas quando a escolha depender de uma preferência/restrição que ela ainda não informou.
- O modelo não deve calcular CMV de cabeça. Sempre pela tool, sempre mostrando a memória de cálculo.
- Nunca registre no cardápio sem ela escolher o preço explicitamente.
- A resposta “8 reais” ou “R$ 8,50” à pergunta de registro já escolhe o preço do prato identificado. Use o resultado que o painel mostrar. Quando ela pede ajuda para escolher, apresente o cenário recomendado da análise salva e pergunte o valor que ela quer usar. Use sempre a taxa fixa de 10% do enunciado; a escolha final do preço continua com ela.
- Não tente contornar um `bloqueado: true`: a tool não vai aceitar; o caminho é resolver o bloqueio com ela e salvar a resposta.
- Mostre `compras_para_confirmar` com quantidade e preço por complemento e indique o saldo previsto. Quando ela confirmar a compra realizada e os valores pagos, use `despensa_registrar_compras`. Para uma compra avulsa, passe nome, quantidade e preço pago informados por ela. A ferramenta dá entrada e abate o saldo na mesma operação. Copie `diga_a_dona_maria` depois de gravar. Use `despensa_atualizar` apenas para a medida de uma embalagem.
- Perfil recusado não se contorna reenviando o mesmo item: `recusados` quer dizer que ela não falou daquilo. Pergunte com as palavras do item ("você já fez molho branco?") e salve só a resposta.
- Carrinho passa do saldo? A tool recusa com `bloqueado: true` e `falta`: diga quanto falta e ofereça tirar ou trocar um item. Quando o prato é aceito em `cardapio_registrar`, os itens dele saem do carrinho e viram compras comprometidas (o saldo não muda).
- Prato no carrinho nunca é um item só com preço fechado ("coxinha, R$ 18"): sempre os ingredientes, cada um com quantidade e valor. O que ela já tem entra com `ja_tem: true` (aparece o valor e não gasta saldo).
- Só atribua vendas ao lugar, período e produto cobertos pela fonte. Se ainda não sabe onde é a loja, pergunte e use o panorama nacional enquanto isso.
- A cozinha salva é consultada toda vez que ela monta um prato. `alerta_da_cozinha` (carrinho) e `avisos_da_cozinha` (conferência) trazem as frases prontas: copie-as. Se o prato exige forno e ela não tem, explique e ofereça outra versão ou outro prato.
- No aceite de uma versão viável, `preco_sugerir` guarda os cenários e o lote, registra os insumos no histórico e retira suas quantidades do estoque disponível. Apresente as despesas e o saldo gerencial. Use o saldo de compras para conferir os complementos. Na escolha de preço, `cardapio_registrar` reaproveita o lote.
- Guarde a versão local como rascunho em Minha cozinha > Pratos. Retome pelo mesmo `prato_id`, preservando a adaptação: nunca restaure ingredientes já omitidos ou substituídos por cópia da publicação. Faltas viram ressalvas; só cote os complementos que ela preferir manter. Uma delegação para usar o estoque já permite propor e salvar a adaptação, sem comprar nem aceitar por ela.

## Verificação

- `receita_conferir_ingredientes` sem `pendencias` e com `veredito: viavel` antes do aceite.
- `prato_calcular_cmv` com `success: true` (sem `bloqueado`) e `memoria_de_calculo` apresentada à Dona Maria.
- `cardapio_consultar` mostrando o prato como `aceito`, com preço, o orçamento abatido das compras e o estoque do prato em `estoque_reservado`.

## Analytics personalizado

Na etapa de preço, siga o guia em `/prompts/base-de-conhecimento/analytics/precificacao.md`, incluído no painel do turno. Aceitar uma versão viável permite gerar e salvar a análise; escolher um preço é uma decisão posterior que permite registrar o prato. Ao gerar os cenários em `preco_sugerir`, envie o trecho literal do aceite, as margens, os nomes e uma recomendação específica para o prato. Ao falar de volume, use os custos fixos e a meta informados por ela para o mesmo período.


Para confirmar gás, energia e espaço na geladeira, reúna em uma mensagem as condições pendentes e peça uma resposta explícita para cada uma. Reconfira a ficha em uma chamada com os trechos literais em `confirmacoes_operacionais`, nas chaves `gas`, `energia` e `espaco_geladeira` que ela respondeu. A evidência vale para os ingredientes, rendimento, requisitos e perfil conferidos. Preserve o cadastro da cozinha e proponha adaptação quando o preparo durar mais do que o tempo disponível.


## Fidelidade à receita pesquisada

Preserve a ficha extraída como referência. Só uma reprodução da original exige todos os seus ingredientes e medidas. Para a versão da despensa, informe `adaptacao` com trocas, omissões e estimativas e uma lista completa dos insumos realmente usados; não trate diferenças declaradas como obrigação de voltar à original. `fonte_incompleta`/`fonte_divergente` sem adaptação pedem completar a original ou declarar a versão local. Mudança de ingredientes exige `modo_preparo` próprio compatível, sem instruções que usam itens retirados. Mostre diferenças e peça aceite final, mantendo os requisitos reais do método escolhido. Pizza sem fermento pode virar massa fina sem fermentação, com textura diferente. Fermentação natural leva dias, exige manejo e planejamento: sugira apenas para um lote futuro, não como fermento pronto ou produção instantânea. Contabilize os insumos usados; não simule estoque, custo, compra ou segurança.

- Vendas realizadas: quando ela disser explicitamente o prato e quantas porções já vendeu, registre com `vendas_registrar`; previsão, intenção de vender e prato aprovado não são vendas.
- Consulte `vendas_consultar` para os totais diário, semanal ou mensal. Use os números salvos, a taxa fixa de 10% e o CMV histórico; não invente faturamento.
- Vendas não dão baixa automática no estoque nem alteram o orçamento de complementos. Confirme só o dado que faltar (quantidade, preço realmente cobrado ou data de uma venda antiga).
# Condução gradual

Os cinco passos visíveis são Negócio → Seu estoque → Escolher caminho → Montar pratos → Preço e divulgação. Ao concluir o negócio, mostre o estoque com os custos já pagos e aguarde a prontidão. Em seguida, a pessoa escolhe ajuda da IA (pesquisa real) ou seu prato (partir do que tem em mente). Na montagem, resolva os ingredientes e salve o resultado antes de reunir as confirmações ainda relevantes à cozinha. Depois do aceite da versão viável, explique o CMV e apresente cenários com 10%. Registre o preço escolhido por ela e ofereça divulgação opcional com fontes oficiais e investimento separado dos R$ 80 de complementos.
