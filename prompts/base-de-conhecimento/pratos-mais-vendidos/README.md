# Pedidos de delivery e receitas por região

A pasta [evidencias](evidencias) reúne dados publicados sobre pedidos e receitas para avaliar com o estoque inicial do Sabor da Maria. A consultora usa o recorte mais próximo da localização informada e a referência nacional como contexto. A pesquisa foi revisada em 15/11/2026.

- [Brasil](evidencias/brasil.md)
- [Centro-Oeste](evidencias/centro-oeste.md)
- [Nordeste](evidencias/nordeste.md)
- [Norte](evidencias/norte.md)
- [Sudeste](evidencias/sudeste.md)
- [Sul](evidencias/sul.md)

Cada arquivo traz ligações diretas para os levantamentos e receitas. Ao citar pedidos, diga o local, o período, a plataforma e o que foi medido. Uma alta de pedidos de pizza no Ceará, por exemplo, informa a variação dessa categoria no estado. Receitas típicas ajudam a formular hipóteses de cardápio. Os levantamentos consultados oferecem recortes diferentes e deixam lacunas no nível de prato, bairro e macrorregião.

Para propor um prato, abra a receita na web e confira a disponibilidade dos ingredientes, o rendimento e o modo de preparo. Confronte com a despensa atual, a cozinha e o orçamento. Trate as substituições como adaptações, valide com a Dona Maria e use os valores reais da ficha para precificar depois da escolha.

## Dados legados de demonstração

O arquivo `brasil.md` na raiz e os Markdown das subpastas por região são exemplos sintéticos do ranking antigo. Preços médios, faixas e índices nessas tabelas são dados ilustrativos. As evidências de pesquisa ficam em `evidencias/`. Os exemplos legados preservam as fichas consumidas pelo leitor anterior e o cadastro de nomes de pratos para reconhecimento.

Os arquivos legados mantêm o cabeçalho Markdown com `escopo`, `pais`, `regiao`, `estado` (quando aplicável), `fonte` e `atualizado`. As tabelas têm sete colunas: posição, prato, categoria, preço médio, faixa, índice de vendas e ingredientes típicos. Seções usam `## País`, `## Região`, `## Estado`, `## Cidade: …` e `### Zona …`. O leitor espera faixa no formato `mín–máx` e índice de 0 a 100 em ordem decrescente.

No Docker, `./prompts` é montado em `/prompts` para o Hermes e a API. Para usar outra base, defina `SABOR_DA_MARIA_BASE_CONHECIMENTO` com o caminho da pasta que contém `pratos-mais-vendidos/`.
