# Decisões de arquitetura

Registro curto, no estilo ADR, das decisões que moldaram o projeto. O README traz a visão geral; aqui está o **porquê** de cada escolha e o que foi descartado.

## 1. O Hermes Agent é o produto, não um componente

**Contexto.** O desafio pede para *instalar e customizar* o Hermes Agent. A primeira ideia era um backend próprio (Python/Mojo) com o Hermes "plugado" e um frontend React.

**Decisão.** Inverter: o Hermes é a aplicação. Toda a lógica de negócio entra pelos pontos de extensão oficiais (`SOUL.md`, `AGENTS.md`, plugin de tools, skill, memória, config) e nada no core é alterado. Interfaces vêm de graça: TUI, API server OpenAI-compatible (porta 8642) e webapp.

**Consequências.** O repositório é essencialmente um `HERMES_HOME` versionado mais a infraestrutura para rodá-lo. Um frontend React continua possível em cima do API server, mas é opcional. O avaliador vê exatamente o que o enunciado pediu.

## 2. Inferência 100% local com llama.cpp; MAX como perfil experimental

**Contexto.** Requisito próprio: tudo roda localmente numa RTX 5070 Ti (16 GB, Blackwell sm_120). O Hermes exige do servidor duas coisas: `tool_calls` nativos no formato OpenAI (não há fallback por texto) e contexto ≥ 64 000 tokens (abaixo disso ele recusa o modelo).

**Alternativas.**

| Motor | Tool calling com Qwen3 | RTX 50 | Veredito |
|---|---|---|---|
| llama.cpp (`server-cuda13`) | nativo via `--jinja` (template Hermes) | suportado | **padrão** |
| vLLM 0.33 | `--tool-call-parser hermes` | suportado | alternativa válida, imagem pesada |
| Ollama | sem `tool_choice`, contexto 4k por padrão, streaming+tools trava no Hermes | suportado | descartado |
| Modular MAX 26.7 | só Qwen3.5 tem parser; sem streaming com tools; `max serve` em Blackwell de consumo com épico aberto e bug de driver (595 → corrigido no 615) | experimental | **perfil opcional** |

**Decisão.** llama.cpp + `Qwen3-8B` GGUF como padrão; MAX + `Qwen3.5-4B` como perfil `max` do compose com roteiro de spike documentado em `inference/max/README.md`.

**Consequências.** Latência boa (≈135 tokens/s de geração, ≈3 000 tokens/s de prompt na 5070 Ti), tool calling confiável, uso de VRAM ≈ 9 GB para o servidor. O MAX fica como diferencial a validar, não como dependência: no spike do dia 1 o `max serve` subiu e planejou a GPU, mas o worker foi morto pelo OOM killer do host (>16 GB de RAM na compilação), então a validação de kernels sm_120 e do parser de tool calling ficou para uma máquina com mais RAM livre (`inference/max/README.md`).

## 3. Contexto de 64k com YaRN e KV cache quantizado

**Contexto.** O Qwen3-8B foi treinado com 40 960 tokens; o llama.cpp limita o slot ao contexto de treino. O Hermes exige 64k.

**Decisão.** `--rope-scaling yarn --rope-scale 2 --yarn-orig-ctx 32768` (receita da própria Qwen para estender contexto), `--ctx-size 65536`, KV cache K em q8_0 e V em q4_0. `model.context_length: 65536` fixado no Hermes para não depender da autodetecção (o servidor anuncia 131 072).

**Consequências.** Slot real de 65 536 tokens confirmado nos logs; VRAM total da máquina em ≈12,5 GB com o desktop ligado. A compressão de contexto do Hermes dispara em ≈55k tokens, dentro do que o servidor aceita.

## 4. Plugin em biblioteca padrão, sem rebuild da imagem

**Contexto.** A imagem oficial `nousresearch/hermes-agent` só *valida* `python_dependencies` do plugin; não instala nada. openpyxl, pandas etc. exigiriam uma imagem derivada.

**Decisão.** Plugin `sabor_da_maria` 100% stdlib: `zipfile` + `xml.etree` para ler o `.xlsx`, `sqlite3` para persistir, `urllib` + `html.parser` para extrair receitas, `difflib` para casamento de nomes.

**Consequências.** A imagem oficial roda o plugin sem modificação (`hermes plugins validate` passa com o plugin no *plugin host* isolado). Testes unitários rodam no host com `python3 -m unittest`, sem instalar nada.

## 5. Banco embutido: SQLite dentro do `HERMES_HOME`

**Contexto.** Requisito próprio de banco interno/embedded; o Hermes já usa SQLite para sessões e memória.

**Decisão.** Um arquivo `sabor_da_maria.db` (tabelas `ingredientes`, `perfil`, `receitas`, `pratos`, `compras`, `config`) carregado da planilha na primeira chamada e persistido entre sessões. `make reset-dados` zera.

**Consequências.** Zero serviços extras. Perfil da cozinha e decisões de cardápio sobrevivem entre conversas e são consultáveis pelo agente (`cardapio_consultar`).

## 6. Busca local com SearXNG e extração própria

**Contexto.** O `web_search` do Hermes tem provider SearXNG embutido, mas a *extração* de página depende de SaaS (Firecrawl, Tavily, Exa, Parallel) ou do anel keyless, que também é nuvem.

**Decisão.** SearXNG em container (JSON habilitado, idioma pt-BR) e um provider de extração próprio registrado pelo plugin (`web.extract_backend: receitas-local`) que lê schema.org/Recipe em JSON-LD e cai para texto legível. `web.keyless_fallback: false` e `keyless_rescue: false` garantem que nenhuma chamada sai para SaaS.

**Consequências.** Receitas chegam estruturadas (ingredientes, rendimento, tempo, passos) na maioria dos sites brasileiros. Risco: buscadores podem bloquear o SearXNG; mitigado com vários engines habilitados.

## 7. Toolset enxuto e elicitação com `clarify`

**Contexto.** Um modelo de 8B acerta mais com poucas tools bem descritas. O bundle padrão `hermes-cli` traz ~40 tools (terminal, arquivos, browser, cron, imagem...), irrelevantes e perigosas para uma consultora.

**Decisão.** `platform_toolsets.cli: [web, clarify, memory, skills, todo, sabor_da_maria]` (≈18 tools) e `tools.tool_search.enabled: off`. A elicitação do desafio (2.2) usa a tool nativa `clarify` (até 5 perguntas de múltipla escolha por vez) guiada por um checklist que o plugin mantém (`perfil_cozinha_consultar` devolve as pendências e a pergunta sugerida).

**Por que desligar o `tool_search`.** O Hermes, por padrão, esconde as tools de plugin atrás de um meta-tool de busca (`tool_search`): o modelo vê só as ~7 tools "core" e precisa procurar as outras. Nos testes, o Qwen3-8B respondia "quanto custa o arroz na despensa?" com `web_search` em 3 de 3 tentativas porque simplesmente não enxergava `despensa_buscar`. Com o bridge desligado e as 18 tools no prompt (~14 KB de schema, irrelevante em 64k), o comportamento corrige. Foi o bug mais importante encontrado no dia 1, e só apareceu lendo o `agent.log` (`tool_search activated (tier 1): 7 core/visible tools kept, 11 deferred`).

**Consequências.** Sem terminal nem acesso a arquivos no agente, mesmo via API server. O fluxo "não deixe ela comprar antes de confirmar que consegue cozinhar" vira um veredito explícito da tool (`viavel` / `precisa_confirmar` / `inviavel_por_enquanto`); desde a decisão 11 esse veredito também trava CMV, preço e registro.

## 8. Memória: curta e com dono

**Decisão.** `MEMORY.md` e `USER.md` começam com sementes mínimas (regras do negócio, o que ainda não se sabe da Dona Maria) e o agente é instruído a salvar equipamentos, técnicas, preferências e decisões. Limites padrão do Hermes (2 200 / 1 375 caracteres) mantidos.

## 9. Repositório = `HERMES_HOME` versionado com lista branca

**Contexto.** O Hermes cria dezenas de arquivos de runtime no `HERMES_HOME` (state.db, sessions, logs, cache, `.env` gerado, skills embutidas sincronizadas).

**Decisão.** Montar `./hermes-home` como `/opt/data` e versionar só a customização via lista branca no `.gitignore`. `HERMES_UID/GID` mapeiam o usuário do host para os arquivos não ficarem como root. Marcador `.no-bundled-skills` evita que as 38 skills embutidas entrem no prompt.

## 10. O que ficou de fora (e por quê)

- **Mojo**: não há cálculo pesado no problema; a interoperação Python↔Mojo está em beta e exigiria o compilador na imagem. Cabe como bônus, não como base.
- **Frontend React**: a TUI e o webapp do Hermes já mostram o agente; o API server (8642) está ligado e com CORS para um React futuro.
- **Firecrawl self-hosted**: resolveria extração com JavaScript pesado, mas custa Redis + Playwright; o extrator local com schema.org cobre os sites de receita.

## 11. Travas no backend e reserva de estoque

**Contexto.** Até aqui o veredito da conferência (`viavel` / `precisa_confirmar` / `inviavel_por_enquanto`) era informativo: `prato_calcular_cmv` e `cardapio_registrar` aceitavam qualquer prato, e um modelo de 8B que "concluísse" na conversa que estava tudo certo conseguia registrar um prato com forno e técnicas ainda sem resposta. Aceitar um prato também não tocava a despensa: dois pratos com 1,5 kg de frango cada passavam contra os 2 kg da planilha; só o orçamento era compartilhado.

**Decisão.** Um veredito único (`_avaliar` em `tools.py`) é calculado pela conferência, pelo CMV e pelo registro, sempre contra o estado **atual** salvo no SQLite (perfil, despensa disponível, orçamento restante), nunca contra o texto da conversa:

- `prato_calcular_cmv` exige `receita_id` de uma receita conferida (é dela que vêm equipamentos e técnicas necessários) e recusa com `bloqueado: true` + `bloqueios` + `proximos_passos` se o veredito não for `viavel`. Numa recusa nada é gravado.
- `preco_sugerir` exige `prato_id`; o CMV usado é o calculado pela tool (um `cmv_por_porcao` avulso é ignorado, com aviso).
- `cardapio_registrar` reavalia o prato, recusa se não estiver `viavel` e recusa com `numeros_mudaram` se CMV por porção ou total de compras diferirem do que foi apresentado (o preço foi escolhido em cima daqueles números). Quando aceita, numa transação só: status, preço, compras comprometidas e **reservas** (tabela `reservas`: o que o prato usa da despensa). É idempotente por `prato_id`: compras e reservas anteriores do mesmo prato são substituídas, então retry ou troca de preço não duplicam nada.
- Disponibilidade = estoque − reservas dos pratos `aceito`. Candidatas não consomem estoque; a conferência do segundo prato já mostra `reservado_por_outros_pratos` e `falta` descontados.
- `cardapio_remover` (11ª tool) tira o prato do cardápio, libera as reservas e cancela as compras comprometidas. Compra real já lançada com `despensa_atualizar(compra=...)` não é desfeita.

**O que ficou de fora.** Dinheiro continua em `float` com arredondamento a dois centavos (Decimal seria o próximo passo), e não há versionamento de plano nem chave de idempotência explícita: a idempotência vem de `prato_id` e da substituição em bloco. O backend também não distingue compra real de inventada em `despensa_atualizar(compra=...)`: na prova com o modelo real, mandado "registrar sem perguntar", o Qwen3-8B tentou destravar pendências de preço lançando compras fictícias (a trava segurou, nada entrou no cardápio); a proibição ficou em `AGENTS.md` e na skill.

**Consequências.** O modelo não consegue "pular a etapa 2" nem por prompt: o caminho até o preço passa obrigatoriamente por perfil completo, equipamentos/técnicas da receita confirmados, ingredientes sem pendência e compras dentro do saldo. Pratos aceitos antes desta versão não têm reserva; basta registrá-los de novo com `cardapio_registrar` (que agora reavalia). `GateTests` e o fluxo completo em `tests/test_costing_and_tools.py` cobrem recusa por perfil pendente, forno ausente, orçamento estourado, CMV sem receita, preço sem prato, números mudados, retry sem duplicar e remoção liberando reserva.
