# Pratos mais vendidos — base de conhecimento do Analytics

Esta pasta é onde a consultora (e a tela **Analytics**) leem **o que mais vende, por quanto e onde**: por zona da cidade, cidade, estado, região e país. A consultora usa isso para dar dicas de pratos e ajudar a montar o carrinho; a tela mostra o mesmo ranking para a Dona Maria.

> **Os números daqui são de exemplo.** Foram criados para a demonstração do desafio técnico do iFood e **não são vendas reais**. Cada arquivo traz o aviso, a consultora é instruída a dizer "dados de exemplo" ao citar um número e a tela mostra um banner. Para usar de verdade, troque as tabelas pelos números da sua operação (veja "Trocar por dados reais").

## Como o lugar da loja prioriza os pratos

O lugar vem de **Meu negócio** (estado, cidade e zona). Quanto mais perto da loja, mais o ranking pesa:

| Escopo | Peso | Onde está |
|---|---|---|
| Zona da cidade | 1,00 | seção `### Zona …` dentro da cidade |
| Cidade | 0,50 | seção `## Cidade: …` |
| Estado | 0,20 | seção `## Estado` |
| Região | 0,10 | `<região>/_regiao.md` |
| País | 0,05 | `brasil.md` |

O prato que lidera a zona da loja ganha de um prato que só aparece bem na região ou no país (cada peso é maior que a soma de todos os mais amplos). Sem localização informada, vale só o ranking do país, e a consultora avisa que dá para priorizar preenchendo Meu negócio. Se a base não tem dados para a zona ou a cidade da loja, a consulta cai para o escopo seguinte, sem erro.

## Estrutura

```
pratos-mais-vendidos/
├── brasil.md                  # país
├── sudeste/
│   ├── _regiao.md             # região (toda região tem o seu)
│   ├── sao-paulo.md           # estado, com as cidades e zonas dentro
│   ├── rio-de-janeiro.md
│   └── minas-gerais.md
├── nordeste/  norte/  sul/  centro-oeste/   # mesma ideia
```

Um arquivo por estado reúne o estado, as cidades e as zonas. Os estados que ainda não têm arquivo usam a região e o país.

## Formato do arquivo

Cabeçalho com `escopo` (`pais`, `regiao` ou `estado`), `pais`, `regiao` e `estado` (a sigla), depois seções em Markdown. Cada seção tem **uma frase de leitura** (o que move as vendas ali) e **uma tabela de 7 colunas**:

```markdown
---
escopo: estado
pais: Brasil
regiao: Sudeste
estado: SP
fonte: de onde vêm os números
atualizado: 2026-11
---

# Pratos mais vendidos — São Paulo (SP)

## Estado
Uma frase sobre o que move as vendas no estado.

| # | Prato | Categoria | Preço médio (R$) | Faixa (R$) | Índice de vendas | Ingredientes típicos |
|---|---|---|---|---|---|---|
| 1 | Pizza de mussarela | pizza | 53,00 | 45–63 | 100 | massa, molho de tomate, mussarela, orégano |

## Cidade: São Paulo
Leitura da cidade.

| # | Prato | … |

### Zona Sul
Leitura da zona (as zonas ficam dentro da cidade).

| # | Prato | … |
```

Regras que o leitor confere (se faltar uma, o arquivo é ignorado e o motivo, com a linha, vai para o log):

- **Faixa** no formato `mín–máx`, com o mínimo menor ou igual ao máximo; o preço médio fica dentro dela.
- **Índice de vendas** de 0 a 100, em ordem decrescente: o 1º colocado tem 100.
- Zonas aceitas: `Centro`, `Zona Norte`, `Zona Sul`, `Zona Leste`, `Zona Oeste`. A cidade e o estado precisam bater com o que a Dona Maria escolhe em Meu negócio.
- `categoria` em minúsculas e sem acento extra (`prato do dia`, `pizza`, `salgado`, `sobremesa`, `lanche`…): é por ela que a consultora e a tela filtram.
- **Ingredientes típicos** são só referência para a conversa. Quem monta o carrinho de um prato é a consultora, pedindo o valor de cada ingrediente, nunca esta lista.

## Trocar por dados reais

1. Edite as tabelas (ou acrescente `### Zona …`, `## Cidade: …`, um estado novo). **Não precisa reiniciar nada**: o leitor relê quando um arquivo muda.
2. Troque a linha `fonte:` e o aviso de dados ilustrativos pela origem real dos números.
3. Rode `make test`: o teste da base abre todos os arquivos e acusa arquivo e linha de qualquer tabela fora do formato.

Para apontar para outra pasta, defina `SABOR_DA_MARIA_BASE_CONHECIMENTO` (a pasta que contém `pratos-mais-vendidos/`). No Docker, `./prompts` entra em `/prompts` (somente leitura) nos serviços `hermes` e `api`.

## Quem lê esta pasta

- **Tool `analytics_consultar`** (consultora): devolve o ranking priorizado pelo lugar da loja, ou de um escopo só (`zona`, `cidade`, `estado`, `regiao`, `pais`), filtrável por categoria.
- **Painel do turno**: os três primeiros entram no contexto de cada mensagem, para ela já saber o que vende ali.
- **API** `GET /api/analytics` e a tela **Analytics** do Playground.
