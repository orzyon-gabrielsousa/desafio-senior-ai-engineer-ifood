# Cozinha e equipamentos — base de conhecimento da conferência

Esta pasta responde **"o que cada prato e cada ingrediente precisam da cozinha"**: quais equipamentos e utensílios, quais habilidades e quanto custa, em média, fazer uma porção. A tela **Minha cozinha** guarda o que a Dona Maria **tem** e **sabe fazer**; a conferência cruza as duas coisas para avisar, antes de ela gastar o orçamento, que **com a cozinha dela não tem como fazer aquela comida com aquele ingrediente**.

> **Os custos daqui são de exemplo.** Foram criados para a demonstração do desafio técnico do iFood e não são medições reais (a tela e a consultora dizem isso). Os requisitos (forno para pizza, freezer para açaí...) são conhecimento de cozinha; troque o que não bater com a sua operação.

## Como funciona

1. A Dona Maria marca em **Minha cozinha** (menu, acima de Meu negócio) o que tem, o que sabe fazer e a rotina da cozinha, **ou conta para a consultora**, que pergunta na primeira conversa. Tudo vai para o SQLite embutido (tabela `perfil`, a mesma que a consultora lê) e aparece na tela.
2. Sempre que um prato aparece (Analytics, carrinho, conferência de receita), o plugin procura a ficha dele aqui e confere cada exigência contra o que está salvo.
3. O resultado é um destes, por prato:

| Situação | Quando | O que acontece |
|---|---|---|
| **Não dá** (`nao_da`) | ela marcou que **não tem** algo que a ficha exige | aviso vermelho: "com a sua cozinha não dá para fazer X"; no carrinho o aviso fica no prato; no caminho da receita ao cardápio vira bloqueio |
| **Atenção** (`atencao`) | ela marcou que **não sabe** ou **está aprendendo** uma habilidade do prato | aviso amarelo, sem bloquear a compra |
| **Confirmar** (`confirmar`) | ela **ainda não respondeu** se tem algo que a ficha exige | aviso cinza e a consultora pergunta |
| **Ok** (`ok`) | tem tudo e sabe tudo | nada a avisar |

Os avisos de **ingrediente** funcionam igual: se ela quer comprar *massa de pizza* e não tem forno, a mensagem diz que com esse ingrediente não tem como fazer pizza.

## Arquivos

| Arquivo | O que tem |
|---|---|
| `pratos.md` | uma linha por prato do Analytics: o que precisa, habilidades e custo típico por porção |
| `ingredientes.md` | ingredientes que dependem de um equipamento ou utensílio em quase todo uso |

### `pratos.md`

```markdown
## Pizza
| Prato | Precisa de | Habilidades | Custo típico (R$) | Observação |
|---|---|---|---|---|
| Pizza de calabresa | forno, forma | massa fermentada, assar | 16,00 | Pede forno bem quente. |
```

- **Prato** é o nome exato do Analytics (`pratos-mais-vendidos/`). A conferência acha o prato com ou sem o parêntese ("Coxinha de frango" vale para "Coxinha de frango (porção com 6)") e, se o nome for único entre os pratos, também por parte dele.
- **Precisa de** separa as exigências por vírgula; `A ou B` quer dizer que um dos dois basta (`fogão, panela de pressão ou panela grande`). Use `-` quando o prato não pede equipamento.
- **Habilidades** separa por vírgula, com os nomes da tabela abaixo.
- **Custo típico** é o custo dos ingredientes por porção. É com ele que o Analytics mostra **quanto cobrar** (custo ÷ 0,90 para não perder, mais a margem que ela quer).
- As categorias (`## Pizza`) são só títulos.

### `ingredientes.md`

```markdown
| Ingrediente | Precisa de | Serve para | Aviso |
|---|---|---|---|
| massa de pizza / disco de pizza | forno | pizza | Massa de pizza só vira pizza assada. |
```

- **Ingrediente** aceita sinônimos separados por `/`. Vale a **palavra inteira** no nome que ela digitou, e o sinônimo mais específico ganha ("feijão fradinho" vale mais que "feijão").
- **Serve para** é a comida que o ingrediente vira: é o que o aviso diz que não dá para fazer.
- Só entram ingredientes que dependem de um equipamento em quase todo uso. Ingrediente que serve para tudo (sal, óleo) não entra, para não gerar aviso falso.

## Nomes que as fichas aceitam

Escreva o nome da tela ou um dos nomes alternativos (sem acento e em minúsculas também vale). Um nome fora desta lista é erro, e o teste da base diz o arquivo e a linha.

**Equipamentos**

| Na tela | Também aceito nas fichas |
|---|---|
| Fogão | fogao a gas, fogao eletrico, cooktop |
| Forno | forno eletrico, forno a gas, forninho |
| Micro-ondas | microondas |
| Air fryer | airfryer, fritadeira eletrica, fritadeira |
| Liquidificador | |
| Batedeira | |
| Processador ou mixer | processador, mixer, multiprocessador |
| Geladeira | refrigerador |
| Freezer | congelador |
| Chapa ou grill | chapa, grill, sanduicheira |
| Churrasqueira ou grelha | churrasqueira, grelha |

**Panelas e utensílios**

| Na tela | Também aceito nas fichas |
|---|---|
| Panela de pressão | |
| Panela grande | caldeirão |
| Panela funda para fritar | panela funda, fritadeira de imersão, wok |
| Frigideira | |
| Forma, assadeira ou refratário | forma, assadeira, travessa, refratário |
| Faca de cozinha | faca |
| Balança de cozinha | balança |
| Cuscuzeira | |
| Esteira de sushi | esteira |

**Habilidades**

| Na tela | Como escrever na ficha |
|---|---|
| Cozinhar arroz e feijão | arroz e feijão |
| Refogar e temperar | refogar |
| Grelhar carnes e frango | grelhar |
| Fritar por imersão | fritar |
| Empanar | empanar |
| Modelar massas e salgados | modelar |
| Massas com fermento (pizza e pães) | massa fermentada |
| Assar no forno | assar |
| Molhos (tomate, branco, cremes) | molho |
| Bolos, pudins e doces | doce |
| Cozimentos longos | cozimento longo |
| Peixes e frutos do mar | peixe |
| Sushi e peixe cru | sushi |
| Montar lanches, saladas e bowls | montagem |
| Cuscuz, tapioca e angu | cuscuz, tapioca ou angu |

O catálogo (os ids e os nomes) mora em `hermes-home/plugins/sabor_da_maria/cozinha.py`: é ele que a tela mostra e que valida o que chega ao banco. Para uma opção nova na tela, acrescente o item lá e use o nome aqui.

## Trocar por dados reais

1. Edite as tabelas (ou acrescente prato e ingrediente). **Não precisa reiniciar nada**: o leitor relê quando um arquivo muda.
2. Troque os custos de exemplo pelo custo real da sua operação e tire o aviso de "custos de exemplo" do cabeçalho.
3. Rode `make test`: o teste da base abre os dois arquivos e acusa arquivo e linha de qualquer nome fora do catálogo, tabela fora do formato ou custo inválido, e confere que **todo prato do Analytics tem ficha**.

Para apontar para outra pasta, defina `SABOR_DA_MARIA_BASE_CONHECIMENTO` (a pasta que contém `cozinha-e-equipamentos/`). No Docker, `./prompts` entra em `/prompts` (somente leitura) nos serviços `hermes` e `api`.

## Quem lê esta pasta

- **Tela Analytics** (`GET /api/analytics`): o selo "dá para fazer / falta forno" de cada prato e o quanto cobrar.
- **Carrinho** (`GET /api/estado`): o aviso no prato aberto e no ingrediente avulso.
- **Tools da consultora**: `analytics_consultar`, `carrinho_adicionar_prato`, `carrinho_adicionar`, `carrinho_montar_da_receita` e `receita_conferir_ingredientes` (neste último, o que a ficha exige entra no veredito junto com o que o modelo leu na receita).
- **Painel do turno**: a cozinha dela, em uma linha, em toda mensagem.
