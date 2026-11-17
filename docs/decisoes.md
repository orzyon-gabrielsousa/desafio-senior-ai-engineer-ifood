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

**Teto de geração (15/11/2026).** Em testes o Qwen3-8B entrou em gerações sem fim (7 mil e 18 mil tokens num turno só) e a de 18 mil derrubou o llama.cpp com `CUDA error: the launch timed out`: a placa é dividida com o desktop e o driver mata o kernel que demora. O servidor agora sobe com `--n-predict 4096` (`LLAMA_N_PREDICT` no `.env`; `-1` desliga), 2 a 4 vezes o que uma resposta legítima daqui usa. **Alcance, medido:** sem `max_tokens` no pedido o servidor para em 4096 com `finish_reason: length`; um cliente que peça um `max_tokens` maior ultrapassa o teto (o llama.cpp trata `--n-predict` como padrão, não como limite). O Hermes não manda `max_tokens` por padrão (`agent.max_tokens` e o padrão do provider `custom` são nulos; as chamadas auxiliares só o enviam em rotas que o declaram), então todo o tráfego dele cai no teto. Não consegui provar o corte com tráfego do Hermes porque o modelo parou sozinho antes (3 453 tokens numa lista de 300 itens).

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
- **Frontend React**: entrou depois, como playground na mesma subida. O Hermes continua sendo o produto. Ver decisão 12.
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

## 12. Playground React na mesma subida

**Contexto.** A demo era a TUI. Para apresentar, a Dona Maria precisa conversar num chat com a cara do Sabor da Maria, sem a chave da API no JavaScript e sem um backend paralelo ao Hermes.

**Decisão.** App React (Vite) no serviço `playground` do compose. O nginx entrega o estático e faz proxy de `/hermes/` para o API server, injetando `Authorization`. O navegador fala só com a mesma origem. A continuidade da conversa usa `X-Hermes-Session-Id` (o histórico de tools fica no Hermes); se o header não voltar, o cliente reenvia o texto visível. Os eventos `hermes.tool.progress` viram o passo a passo da cozinha (despensa, receita, CMV, preço). O visual imita o chat do iFood para ambientar a demo: barra branca com a logo, vermelho `#ea1d2c`, balões (a Dona Maria em vermelho, a consultora em branco, com o "i" do wordmark como avatar), sugestões em chips e Nunito Sans no lugar da tipografia própria da marca.

**O que ficou de fora.** O painel lateral mostra as regras fixas do desafio (R$ 80, taxa de 10%), não um espelho do SQLite: estoque e CMV continuam saindo das tools, na conversa; só o orçamento, o carrinho e o saldo são ao vivo (decisão 13). O `clarify` continua só na TUI. A logo é o `IFood_logo.svg` do Wikimedia Commons, sem alterações, em `playground/src/assets/`: o arquivo não tem copyright (só forma e texto), mas a marca é do iFood e o uso aqui é só para a demo. A tela é uma aproximação do app, não uma cópia do design system.

**Consequências.** `make up` abre http://localhost:3000. `make chat` segue disponível quando a elicitação com opções importa.

## 13. Carrinho com saldo, perfil do negócio e a API da tela

**Contexto.** A Dona Maria precisa montar a lista de compras dentro dos R$ 80 vendo, a cada item, quanto já escolheu e quanto sobra, e a consultora precisa saber disso: avisar o saldo, apontar "você já pegou a couve" e sugerir pratos e ingredientes de acordo com o tipo de negócio dela (restaurante de hotel, arroz e feijão, bolos, pizzas). Ela mexe no carrinho pela tela ou pedindo no chat, e tudo fica no banco local. Isso pede um caminho entre o navegador e o SQLite que hoje só o Hermes enxerga.

**Decisão.**
- **Serviço `api`** (`hermes-home/plugins/sabor_da_maria/api.py`, só stdlib) usando a mesma `Store` do plugin: a tela e a consultora enxergam o mesmo carrinho, sem regra duplicada. Roda na **imagem do Hermes** (já no disco, mesmo Python em que o plugin é testado; nada novo para baixar), com o código do plugin e a pasta do SQLite montados; a porta 8700 só abre no loopback e o nginx do playground leva `/api/` até ela. Alternativas descartadas: o Hermes não tem registro de rota HTTP para plugin (só o dashboard, que exigiria ligar o painel e a autenticação); um servidor numa thread dentro do plugin tem efeito colateral escondido no `register()` e disputaria a porta com a TUI; comandar a tela por mensagens ao agente seria lento e não determinístico. Os dados ficam em `/data`, não em `/opt/data`: com o UID do host, a pasta da imagem deu *permission denied*.
- **Orçamento é regra dura.** `saldo = orçamento − compromissos de pratos aceitos − carrinho`. Item que não cabe é recusado (409 na API, `bloqueado: true` na tool) com quanto falta, igual na tela e no chat. O carrinho é rascunho: o aceite de prato continua olhando só os compromissos, e as linhas ligadas a uma receita saem do carrinho quando o prato é aceito (viram compras comprometidas), para o mesmo gasto não contar duas vezes.
- **4 tools no toolset** (`carrinho_consultar`, `carrinho_adicionar`, `carrinho_remover`, `carrinho_montar_da_receita`), em vez de uma por ação: o toolset é enxuto de propósito para o 8B. `carrinho_montar_da_receita` reaproveita o veredito único: só entra no carrinho receita `viavel`, com os preços que a conferência calculou, então a consultora não digita preço de cabeça.
- **Painel injetado no turno** (`painel.py`, hook `pre_llm_call`): saldo, itens e perfil do negócio chegam em todo turno junto da mensagem. Na primeira prova com o modelo real a consultora não chamou `carrinho_consultar` e ignorou o perfil do negócio; depender de o 8B lembrar de uma tool não é confiável, e o que a Dona Maria mexe na tela não passa por conversa nenhuma. O bloco tem marca própria e a trava de evidência o descarta.
- **Perfil do negócio** em opções fechadas (tipo, o que vende, público, observações), guardado em `config`; a Dona Maria edita pela tela ("Meu negócio") e a consultora só lê. **Atualizado na decisão 18:** o negócio passou a ser onde a loja fica, tipo de negócio e tipo de alimentação, e a consultora também grava. Sem texto livre no perfil, nada vai ao banco que não esteja no catálogo (`negocio.py`).

**O que ficou de fora.** "Receitas mais vendidas" não vinham de dado de venda: a consultora usava o perfil do negócio e, se quisesse, `web_search` citando a fonte (a decisão 16 trouxe uma base de exemplo para isso). Dinheiro continua em `float` com dois centavos. A tela não recebe push: ela recarrega o estado depois de cada turno, quando uma tool de carrinho ou de cardápio termina e quando a aba volta ao foco. Preço de item colocado pela consultora é o que ela informa (`preco_estimado: true` aparece na tela); o backend não tem como saber se a pesquisa estava certa.

**Consequências.** `make up` sobe também o `api`; `make restart` reinicia Hermes e API depois de mexer no plugin. O plugin passou a declarar 15 tools e 3 hooks (o `hermes plugins validate` confere; hoje são 18 tools, ver 15 e 16). Testes em `tests/test_carrinho_e_api.py` (saldo, estouro, ida e volta HTTP de verdade) e `tests/test_painel.py`.

## 14. Trava de evidência no perfil da cozinha

**Contexto.** Numa prova com o modelo real (14/11/2026, passo 4 do roteiro) o Qwen3-8B gravou no perfil técnicas (`molho_branco: sim`, `ponto_de_carne: aprendendo`...) e utensílios (escorredor, faca, tigela) que a Dona Maria nunca citou, contra o "não assume nada que ela não disse" do `SOUL.md`. Eram três causas somadas: o schema da própria tool trazia exemplos com esses valores e o modelo os copiou; o perfil só fecha com alguma técnica preenchida, então o modelo preenche para destravar; e o backend não tinha como saber de onde veio cada valor.

**Decisão.** `perfil_cozinha_salvar` só grava item que tem base no que ela disse (`falas.py`), com a verificação no backend, fora do alcance do modelo:
- Dois hooks públicos de plugin guardam as falas dela por sessão: `pre_llm_call` (histórico + mensagem do turno) e `post_tool_call` do `clarify` (a resposta chega no meio do turno, depois do primeiro hook; na TUI é assim que ela responde). Vale mensagem dela e resposta do `clarify`; o painel do carrinho é descartado.
- Cada item enviado precisa ter base: a fala cita o item (ou um sinônimo: "pressão" sustenta `panela_pressao`, "botijão" sustenta `gas`) com todas as palavras, comparando radicais de 4 letras (molhos/molho, assar/assados), e, se o valor é número, o número aparece (`2 horas` sustenta `120` minutos). Resposta curta (até 8 palavras) e resposta do `clarify` contam junto com a pergunta a que respondeu, senão ela nunca poderia dizer "tenho sim".
- Aceita parcialmente: o que tem base entra, o resto volta em `recusados` com o motivo e um aviso para perguntar em vez de completar. O perfil continua incompleto, então os gates existentes obrigam a conversa.
- Os valores de exemplo saíram do schema, a descrição passou a mandar não completar o perfil, e a observação livre também é conferida (cobertura das palavras ≥ 60%).
- `SABOR_DA_MARIA_CONFERIR_FALAS=0` desliga a conferência, só para depurar. Sem as falas da sessão (hook não registrado) a tool recusa explicando, em vez de gravar às cegas.

**O que ficou de fora.** Não verifica se o sim/não bate com a fala (`forno: true` para quem disse "não tenho forno" passa): isso fica para os evals. Radical de 4 letras é lexical, não semântico: "fritura" e "fritar" casam, "forno" e "fornecedor" também. A resposta do `clarify` na TUI foi coberta por teste com o formato real do Hermes e pela leitura do código dele, mas não foi exercitada de ponta a ponta com a TUI.

**Consequências.** Reprodução no teste `test_incidente_da_demo_o_modelo_inventou_tecnicas_e_utensilios` (mesma fala e mesmo payload da prova). Ao vivo, no mesmo cenário, o modelo mandou um perfil inteiro inventado já no primeiro turno (forno, panela de pressão, três técnicas, "50 litros", "60 min"): a tool recusou todos os itens e a consultora passou a perguntar. O Hermes roda revisões em segundo plano que reaproveitam o `session_id` e chamam as tools minutos depois; elas passam pela mesma trava, mas por isso o banco pode ser regravado por uma conversa antiga logo depois de um `make reset-dados`.

## 15. Prato aberto no carrinho e itens salvos

**Contexto.** Numa prova com o modelo real, "coxinha de frango" entrou no carrinho como um item só, "R$ 18,00, preço estimado": a Dona Maria não via de onde vinha o valor nem o que ela já tinha em casa. O pedido dela: quando for prato, detalhar os ingredientes e o valor de cada um, e poder salvar pratos e ingredientes avulsos.

**Decisão.**
- **Prato é sempre aberto.** A tabela `carrinho` ganhou `prato` (a que prato a linha pertence) e `ja_tem` (já está na despensa). O prato entra por `carrinho_adicionar_prato`, por `carrinho_montar_da_receita` (que agora abre o prato com o que ela compra e o que já tem) ou pela tela. Cada ingrediente precisa de nome e valor, e isso é validado no backend (`_linhas_do_prato`), não no prompt; o que ela já tem pode ficar sem valor.
- **O que ela já tem mostra o valor e não gasta saldo:** `saldo = orçamento − comprometido − Σ carrinho com ja_tem = 0`. O prato mostra "valor do prato" e "você compra". A entrada é atômica: um prato que não cabe no saldo não entra pela metade.
- **`carrinho_adicionar` (ingrediente avulso) recusa nome de prato** (`_e_prato`: os salvos e os da base de pratos mais vendidos, por igualdade, então "arroz" continua sendo ingrediente e "Arroz carreteiro" é prato) e devolve o passo seguinte. Alternativa descartada: heurística por palavras ("de", "com"), com falso positivo demais ("pão de queijo" é prato, "queijo ralado" não).
- **Salvos:** tabela `salvos` (tipo prato ou ingrediente, nome normalizado único por tipo, valores e ingredientes em JSON); mesmo nome atualiza. `salvos_salvar` guarda o que está no carrinho quando não vem lista; `carrinho_adicionar_prato(prato="nome")` sem ingredientes volta o salvo; a API tem POST e DELETE em `/api/salvos` e `/api/salvos/<id>/carrinho`.
- **Migração aditiva** (`PRAGMA table_info` + `ALTER TABLE` só quando falta a coluna): o banco criado antes continua valendo.
- A tela ganhou abas Carrinho e Salvos, o prato como cartão com os ingredientes e um formulário de prato com uma linha por ingrediente (quantidade, unidade, valor, "já tenho").
- O invólucro `_tool` passou a tratar `ValueError` e `KeyError` (pedido que não dá para atender) como recusa com `warning` sem traceback, e o erro chega ao modelo sem as aspas do `KeyError`.

**O que ficou de fora.** Editar a quantidade de um ingrediente dentro do prato (tira-se o ingrediente e adiciona-se de novo). `carrinho_adicionar_prato` é rascunho de compra e **não passa pela trava de viabilidade** (equipamentos, técnicas, perfil) que `carrinho_montar_da_receita` e o caminho do CMV ao cardápio aplicam: sem receita conferida não há como o backend saber o que o prato exige. A regra "confirmar que ela consegue cozinhar antes de comprar" fica, nesse caminho, no `SOUL.md` e no `AGENTS.md`, e o aceite no cardápio (decisão 11) segue com todas as travas. Dinheiro continua em `float`.

**Consequências.** 18 tools; `tests/test_pratos_e_salvos.py` (valor por ingrediente, "já tem" sem gastar saldo, prato recusado como item único, salvos, migração, tools e API). Ao vivo: pedir "uma coxinha de frango, R$ 18" fez a tool recusar e a consultora explicar que o prato precisa ser aberto; "pode ser esse prato, coloca no carrinho" resultou em cinco ingredientes com o valor de cada um, três marcados "já tem na despensa", e o saldo de R$ 29,00 certo.

## 16. Analytics e a base de conhecimento de pratos mais vendidos

Versão anterior da demonstração. A decisão sobre pesquisa regional e Analytics personalizado ao fim deste arquivo substitui esse uso das tabelas.

**Contexto.** Pedido: abaixo de "Meu negócio", um Analytics com os pratos mais vendidos e os preços por região para ajudar o delivery a montar pratos; a consultora pode consultar para dar dicas; a base fica em `prompts/base-de-conhecimento/pratos-mais-vendidos/`, por região e estado; os pratos são priorizados pela cidade e pela zona em que a loja está.

**Decisão.**
- **Base em Markdown no repositório:** `brasil.md`, `<região>/_regiao.md` e `<região>/<estado>.md` (com as cidades e zonas dentro). Cada seção tem uma frase de leitura e uma tabela de 7 colunas (posição, prato, categoria, preço médio, faixa, índice de vendas, ingredientes típicos). Legível por pessoa e pela consultora, editável sem código, versionada. Alternativas descartadas: JSON ou CSV (ruins de editar e de ler como base de conhecimento), tabela no SQLite (dado de referência não é estado da Dona Maria, e perderia o histórico no git), busca vetorial (desproporcional para ~700 linhas, e o 8B não ganha nada com embeddings aqui).
- **Leitor** `analytics.py`, só stdlib, com cache por `mtime` (editar a base vale sem reiniciar) e arquivo com defeito isolado (loga o arquivo e a linha e não derruba os outros).
- **Prioridade pelo lugar:** zona 1,0 > cidade 0,5 > estado 0,2 > região 0,1 > país 0,05. Cada peso é maior que a soma de todos os mais amplos, então quem lidera a zona da loja ganha de quem só vai bem no país. Prato que aparece em vários níveis soma pontos; o preço e os ingredientes vêm do nível mais local. Sem localização vale só o país, e tanto a tela quanto a consultora dizem como priorizar.
- **Lugar da loja** em "Meu negócio": estado (lista fechada, e a região sai dele), cidade (texto livre) e zona (lista). O perfil salvo antes disso continua valendo.
- **Quem lê:** a tool `analytics_consultar` (devolve também `diga_a_dona_maria`, com o aviso de base de exemplo, que ela copia), as três primeiras posições no painel do turno e `GET /api/analytics` para a tela (abas por nível, filtro por categoria, botão "Montar com a consultora").
- **Dados de exemplo, assumido em todo lugar.** Os números foram gerados para a demonstração (12 estados, 23 cidades, 45 zonas, 47 pratos; fatores por região, perfil de zona e custo) e **não são vendas reais**: cada arquivo, a tela e a frase da consultora dizem isso. O teste da base confere o formato de todos os arquivos (colunas, faixa, índice de 0 a 100, ordem, região coerente com a pasta) para que trocar por dados reais não quebre nada em silêncio.
- `./prompts` entra em `/prompts` (somente leitura) nos serviços `hermes` e `api`; `SABOR_DA_MARIA_BASE_CONHECIMENTO` aponta outra pasta se for preciso.

**O que ficou de fora.** Dado real de venda (o iFood tem; o desafio não entrega). Atualização automática da base. Estado sem arquivo usa a região e o país. A tela lê até 30 pratos por nível.

**Consequências.** `make test` com 132 testes (`tests/test_analytics.py`). Ao vivo, com a loja na Zona Sul de São Paulo, a consultora chamou `analytics_consultar` e abriu o "Prato feito (PF) com bife" (o primeiro do lugar); na versão testada ela citou só o primeiro e não avisou que era base de exemplo, e por isso a tool passou a devolver `diga_a_dona_maria`, com os cinco primeiros e o aviso, para ela copiar.


## 17. Minha cozinha, avisos de compatibilidade e o quanto cobrar

**Contexto.** Pedido: uma tela **Minha cozinha** acima de Meu negócio, onde a Dona Maria vê e marca o que tem (fogão, micro-ondas, outros equipamentos e utensílios) e o que sabe fazer, salva no banco; a consultora, logo na primeira conversa, pergunta o que o desafio manda descobrir (equipamentos e utensílios, técnicas, restrições operacionais), salva no banco embutido e consulta isso **toda vez que ela monta um prato**; em `/prompts`, o que cada prato e ingrediente precisa de cada equipamento e utensílio, com alerta quando o ingrediente que ela quer comprar não tem como virar aquela comida; no Analytics, quanto cobrar para o delivery lucrar depois da taxa; e o roteiro da demo sai da barra lateral (o passo "Contar a cozinha" pré-escrevia a resposta dela, em vez de a consultora perguntar).

**Decisão.**
- **Um registro só.** A tela grava na tabela `perfil` que a consultora já usava (`equipamentos`, `tecnicas`, `restricoes`); nada de tabela nova. O que um lado grava o outro mostra, e as travas do cardápio (decisão 11) já leem esse registro. Na tela não há trava de evidência (é ela quem marca; a decisão 14 é para o que o modelo quer gravar sozinho). `cozinha.py` tem o **catálogo fechado** (11 equipamentos, 9 utensílios, 15 habilidades, rotina em opções; os ids dos equipamentos são as chaves que a consultora já gravava) e valida o que chega, como o `negocio.py`. Variantes antigas da mesma coisa ("forno_eletrico", "airfryer", "molho_branco") são reconhecidas por palavra inteira e viram o item do catálogo na tela; o que a conversa gravou e o catálogo não tem ("massa fresca") aparece em "O que você contou na conversa", com × para apagar. Alternativa descartada: tabela própria de equipamentos, que criaria dois registros da mesma cozinha para manter em sincronia.
- **A consultora pergunta sozinha, na primeira mensagem.** O hook `pre_llm_call` recebe `is_first_turn` do Hermes; o painel do turno (decisão 13) traz a cozinha dela e, enquanto o checklist está incompleto, a ordem de perguntar com o **texto das perguntas já pronto** (só o que falta), já na primeira resposta quando é a primeira mensagem. Pronto num campo porque o 8B só repete com fidelidade o que chega pronto (lição do carrinho e do Analytics). O texto vem das pendências do `perfil_cozinha_consultar`, então o que ela marcou na tela ou já contou não é perguntado de novo. No playground a pergunta é em texto: o toolset HTTP não tem `clarify`, e o texto da pergunta no histórico é o que faz "tenho sim" valer como resposta na trava de evidência.
- **Fichas em Markdown em `/prompts`** (`cozinha-e-equipamentos/pratos.md` e `ingredientes.md`), no mesmo estilo e com o mesmo cuidado da base de pratos mais vendidos: leitor com cache por `mtime`, arquivo com defeito isolado, teste que acusa arquivo e linha, README da pasta. Cada um dos 47 pratos do Analytics tem ficha (o teste exige); `A ou B` expressa alternativas (`panela de pressão ou panela grande`); os nomes são os da tela, e nome fora do catálogo é erro. Ingredientes só entram quando dependem de um equipamento em quase todo uso (massa de pizza → forno), para não gerar aviso falso; vale a palavra inteira e o sinônimo mais específico ("feijão fradinho" ganha de "feijão").
- **Conferência** (`cozinha.conferir`): `nao_da` (ela disse que não tem), `atencao` (não sabe ou está aprendendo), `confirmar` (falta responder), `ok`, `sem_ficha`. As mensagens saem prontas do backend e são as mesmas na tela e nas tools; o que impede o prato vem sozinho (com a cozinha dela o prato não sai, o resto é secundário). **Aviso onde a compra é rascunho** (carrinho, Analytics, `carrinho_adicionar*`, a frase vai no começo de `diga_a_dona_maria`); **trava no caminho da receita ao cardápio**: `_somar_cozinha` soma ao que o modelo leu na receita (`equipamentos_necessarios`) o que a ficha do prato e as regras dos ingredientes exigem, sem repetir o que ele já listou, e o veredito único (decisão 11) bloqueia `prato_calcular_cmv`, `carrinho_montar_da_receita` e `cardapio_registrar` como qualquer requisito. Isso fecha o buraco que a decisão 15 deixou ("o modelo esquece de listar o forno da pizza, a ficha não") para os pratos da base.
- **Quanto cobrar** (`costing.quanto_cobrar`): preço mínimo = custo ÷ 0,90 arredondado **para cima** ao centavo (arredondar para baixo deixaria meio centavo de prejuízo por prato), preço para cada margem (a mesma conta e o mesmo arredondamento de R$ 0,50 do `preco_sugerir`) e o que sobra no preço do mercado. Uma conta só, no backend, para a tela (`GET /api/precos` na calculadora, `preco` em cada prato do Analytics) e para a consultora (`quanto_cobrar` em `analytics_consultar`). O custo de cada prato é um **custo típico de exemplo** (coluna das fichas), dito na tela e na frase da consultora; o custo real continua vindo de `prato_calcular_cmv`.
- **Roteiro da demo removido** da barra lateral (e a demo do README passou a começar pela pergunta da consultora).

**Prova com o modelo real (15/11/2026).** Na primeira conversa o Qwen3-8B cumprimentou e copiou as perguntas dos três grupos, e na resposta dela salvou os dados. Três problemas do 8B apareceram e foram resolvidos no backend, não no prompt: (1) gravava o tempo em horas (`2` em vez de `120`), porque a conferência de falas aceita o `2` de "2 horas"; agora `falas.unidade_do_numero` converte quando ela disse horas e recusa quando não dá para saber; (2) usava `observacoes` como balde para as técnicas e para a mensagem inteira; o campo saiu do schema que o modelo vê (o backend ainda aceita) e texto longo é recusado; (3) depois de salvar "confirmava" à Dona Maria técnicas que ela nunca disse; o resultado do salvamento agora traz o resumo pronto do que ficou no banco (`diga_a_dona_maria`) e as perguntas que faltam. Valores sem acento ("media", "botijao") casam com a opção da tela. Depois disso, três conversas novas seguidas perguntaram na primeira resposta e salvaram equipamentos, técnicas e rotina.

**O que ficou de fora.** Receita fora da base não tem ficha (vale o que o modelo leu); o aviso do carrinho não bloqueia; as fichas e os custos são de exemplo; "pode ter forno elétrico em outro lugar" não existe (ela marca na tela). A tela não cobre receitas novas: quem acrescenta uma opção ao catálogo mexe em `cozinha.py`. O 8B pode variar na hora de estruturar a resposta (a trava de evidência continua impedindo que grave o que ela não disse). Dinheiro continua em `float` com dois centavos.

**Consequências.** 204 testes (`tests/test_cozinha.py`: catálogo, tela ↔ banco ↔ conversa, fichas do repositório sempre no formato, conferência, avisos nas tools e no estado da tela, painel e primeira mensagem, API, quanto cobrar e os três problemas da prova real). Plugin 1.4.0, ainda 18 tools. `make restart` basta para a API e o Hermes pegarem o plugin; o playground precisa de `docker compose up -d --build --no-deps playground`.

## 18. Meu negócio enxuto, os pratos em Minha cozinha e o negócio perguntado na primeira conversa

**Contexto.** Pedido: Meu negócio coleta onde a empresa está (país, estado, cidade, zona), o tipo de negócio e o tipo de alimentação que é feita; "os pratos ficam no Minha cozinha", então "O que você vende", "Para quem você vende" e "Observações" saem de Meu negócio; e essas informações também são coletadas pela IA na primeira conversa e salvas no banco.

**Decisão.**
- **Perfil do negócio novo** (`negocio.py`): `tipo`, `alimentacao` (lista), `pais`, `estado`, `cidade`, `zona`; nenhum campo é obrigatório (ela pode preencher aos poucos). O que saiu (`vende`, `publico`, `observacoes`) não é mais lido nem gravado, e um perfil salvo antes continua legível (`Store.negocio()` devolve só os campos novos). O **tipo de alimentação** são 10 opções fechadas (comida caseira, marmitas, pizzas, massas, lanches, salgados, doces, peixes, japonesa, saudável) que apontam, uma a uma, para as categorias dos pratos da base (`pratos.md` e Analytics); um teste exige a correspondência nos dois sentidos. É a ponte entre o negócio e Minha cozinha: as categorias dela abrem primeiro na lista de pratos. País: só o Brasil por enquanto (a base do Analytics é brasileira); a tela mostra o campo desabilitado e o `negocio.normalizar` recusa outro valor.
- **Os pratos em Minha cozinha**: nova aba **Pratos** com os 47 pratos da base agrupados por categoria, cada um com o que precisa (equipamentos e utensílios: ✓ tem, ✗ falta, ? ainda não respondeu, com as alternativas `A ou B`) e as habilidades dela. É o "se possui os equipamentos, utensílios e habilidades para cada prato" do pedido original, calculado pela mesma conferência do carrinho e do Analytics (`cozinha.pratos_da_cozinha`, `GET /api/cozinha/pratos`): um resultado derivado, não um dado a mais no banco. A seleção por categoria ("o que você vende") deixou de existir; a lista por prato a substitui. Ao salvar, a tela fica aberta e a aba Pratos já mostra o efeito.
- **`negocio_salvar`** (19ª tool, plugin 1.5.0): como o perfil da cozinha, só grava o que ela disse, com a evidência conferida por campo: o estado pelo nome ou pela sigla escrita em maiúsculas (`pa` é palavra, `PA` é estado), a cidade pelas palavras, a **zona só como frase inteira** ("zona leste", "leste da cidade"; a palavra solta não vale: "Rio Grande do Sul, zona norte" não sustenta zona sul; resposta curta "leste" vale quando a pergunta foi sobre a zona), o tipo e a alimentação pelas palavras dela. Os enums vão no schema (zona, tipo, alimentação) e o estado e a cidade são texto; nome fora da lista volta como recusado. Faz merge, e os tipos de alimentação se somam (ela pode ir contando). O resultado traz o resumo pronto do que ficou salvo, uma regra de copiar só isso (o 8B misturava os exemplos das perguntas no resumo que dava a ela) e as perguntas que faltam.
- **A primeira mensagem pergunta tudo**: o painel do turno junta as perguntas do negócio e da cozinha numa lista só, numerada, só com o que falta, e manda salvar o negócio com `negocio_salvar` e a cozinha com `perfil_cozinha_salvar`. A zona é pedida junto com o lugar, mas não impede o negócio de ficar completo (nem toda cidade tem zona); o negócio incompleto não trava o aceite de prato (só a cozinha trava, decisão 11). SOUL, AGENTS e skill atualizados; o texto de boas-vindas e o primeiro atalho do chat falam do negócio e da cozinha.

**Prova com o modelo real (15/11/2026).** Três conversas novas (aberturas inéditas, banco sem o lixo antigo): em todas a consultora perguntou os seis blocos na primeira resposta; na resposta dela, o negócio foi salvo corretamente 3 de 3 (marmitaria, comida caseira e marmitas, Campinas/SP, sem zona), as técnicas 3 de 3 e a rotina 3 de 3. O 8B ainda deixou de gravar "não tenho air fryer" em 2 de 3 (a pergunta volta, e ela responde ou marca na tela) e reescreveu o resumo com técnicas que ela não disse em 1 de 3 (nada disso foi salvo: a trava de evidência segurou). Numa prova anterior, que reaproveitou uma conversa velha (a sessão do Hermes vem da primeira frase), o modelo mandou `zona: "leste"` copiado do lixo antigo do banco (uma restrição `regiao: "sao paulo zona leste"` que ele gravara quando não havia onde guardar o lugar da loja) e a trava recusou ("ela não falou da zona leste"). Esse tipo de lixo agora aparece em "O que você contou na conversa" de Minha cozinha, com × para apagar.

**O que ficou de fora.** Outros países. Migração do que um perfil antigo guardava em `vende` (some da leitura; não havia dado real a migrar). Uma restrição `regiao` já gravada pelo modelo não vira negócio sozinha: a Dona Maria a apaga pelo × e conta o lugar de novo (ou preenche Meu negócio). A negação ("não tenho air fryer") depende de o modelo mandar `false`; o backend não infere sinais da frase. Dinheiro continua em `float`.

**Consequências.** 234 testes (`tests/test_negocio.py`: catálogo e ponte com as categorias da base, o que ela diz → catálogo, trava de evidência do negócio, merge, perguntas do painel, lista de pratos e rota). Plugin 1.5.0, 19 tools. `make restart` basta para a API e o Hermes pegarem o plugin; o playground precisa de `docker compose up -d --build --no-deps playground`.

## 19. Cada ação da consultora à vista e a pesquisa de receitas (2.1) em ordem

**Contexto.** Pergunta: o item 2.1 do desafio (pesquisar receitas reais na internet a partir da despensa e do orçamento, apresentar as candidatas e pedir o feedback dela) estava implementado? E o chat precisa mostrar, enquanto acontece, cada ação da consultora: pesquisando na internet, conferindo preço, pensando.

**O que a verificação mostrou (15/11/2026, modelo real).** As ferramentas existiam (`web_search` com SearXNG, `web_extract` com o provider `receitas-local`) e o AGENTS.md descrevia o fluxo, mas o Qwen3-8B não o seguia: (1) pedido específico ("receitas de frango com batata"): uma busca e a resposta em cima dos trechos do resultado, sem ler a página nem olhar a despensa, com "dificuldade" e "tempo" que ninguém leu em lugar nenhum; (2) pedido aberto ("o que eu consigo fazer com o que tenho?"): nem buscou; (3) numa rodada com o roteiro só no painel, escreveu "vou pesquisar na web, um momento" e encerrou o turno; (4) depois de ler as páginas, abriu o prato no carrinho e inventou "estimativa" de preço do que faltava. A tela, por sua vez, mostrava só um chip com o nome da ferramenta: a busca digitada chega no evento `label` do streaming e o parser a ignorava.

**Decisão.**
- **O que o Hermes manda** (lido no código do `api_server`, não presumido): `hermes.tool.progress` com `tool`, `emoji`, `label` (o argumento principal: a busca digitada, o endereço da página), `toolCallId` e `status` (`running`/`completed`). Não traz o **resultado**, e não há evento de raciocínio do modelo.
- **Passo a passo no chat** (`playground`): cada ferramenta vira uma linha do balão, em ordem, com o verbo no gerúndio e o que está sendo feito enquanto roda ("Pesquisando na internet" + a busca; "Lendo a receita" + o site) e, quando termina, o que fez e achou, mais o tempo. "Pensando no próximo passo" (com os segundos) aparece entre uma ação e outra e "Escrevendo a resposta" quando o texto começa: é o intervalo entre os eventos, que é o que o modelo está fazendo ali. A barra "Agora" mostra a ação em curso e as quatro últimas. O balão mais recente fica aberto; os antigos recolhem (o `<details>` abre de novo).
- **O resultado de cada passo** (`atividade.py`): o hook público `post_tool_call` recebe argumentos e resultado de toda ferramenta, as do plugin e as do Hermes. O plugin escreve uma frase em português **só com o que a ferramenta devolveu** ("3 resultados · receitasdeminuto.com, tuta.com.br…", "Leu 2 páginas: Frango com Batata Fácil (10 ingredientes, rende 4 porções)", "Tem: frango (2 kg) · Não tem: creme de leite", a conta de cada ingrediente na conferência, "Travado: …" quando uma trava recusa, que não é falha) e guarda na tabela `atividade` do SQLite pelo `tool_call_id`; a tela busca por `GET /api/atividade?ids=` com espera crescente (a ferramenta termina antes de o plugin gravar). Sem a API o passo aparece só com o que o streaming mandou. Guarda as últimas 400 ações; nunca derruba o turno.
- **A pesquisa em ordem** (`pesquisa.py`): quando a mensagem dela pede receita ou ideia de prato (e **não** quando pergunta "quanto paguei?" ou "mostra o cardápio", que a despensa responde), o painel do turno traz o roteiro (despensa → `web_search` → `web_extract` nas 2 melhores páginas → `despensa_buscar` → texto com 2 ou 3 candidatas: nome, fonte, o que ela tem, o que falta, **sem preço**, e "Você gosta de cozinhar isso? Vê algum impedimento?") e o turno fica marcado por sessão. Nesse turno, o hook público `transform_tool_result` acrescenta `proximo_passo` ao resultado da despensa ("chame `web_search` agora, sem avisar"), da busca ("leia as 2 melhores páginas"), da leitura da página ("confira com `despensa_buscar` e responda em texto, sem usar o carrinho") e da conferência ("pergunte de cada uma"). Quem conduz é a ferramenta que acabou de responder, que um modelo pequeno respeita mais do que uma instrução do começo da mensagem (a mesma lição do `diga_a_dona_maria`). O carrinho, a conferência de ingredientes e o preço só entram depois que ela escolher: quem decide o que cozinhar é ela.
- **Menor:** `despensa_listar` aceita vários termos no `filtro` ("frango, batata": o modelo manda a lista inteira num campo só e antes recebia zero itens).

**Prova com o modelo real.** Quatro conversas novas (aberturas inéditas, perfil de teste completo, banco da demo restaurado depois): o pedido específico, o pedido aberto ("o que eu consigo fazer de marmita com o que tenho?") e os dois atalhos do chat ("Quero montar o cardápio com o que tenho na despensa", "Gosto de fazer frango e carne moída, para marmita") fizeram a cadeia completa **4 de 4**, em 17 a 20 s, terminaram em texto com as candidatas e a pergunta de feedback, sem tocar no carrinho e sem preço inventado. Antes do roteiro nas ferramentas era 0 de 2 (item 1 e 2 acima). Na tela, de uma conversa real: "Abriu a despensa" → "Pesquisou na internet «receita fácil de frango com batata para marmita» · 3 resultados" → "Leu 2 páginas" → "Conferiu a despensa" → "Escrevendo a resposta".

**O que ficou de fora.** O 8B ainda pergunta uma vez só ("você gosta de cozinhar frango com batata?") em vez de uma pergunta por candidata em parte das respostas, e o casamento da despensa às vezes diz que não tem um ingrediente que tem (visto com "tomate", que apareceu como "2 kg" numa rodada e "não tem" em outra): isso é do modelo ao resumir e do casamento aproximado, e entra nos evals. A frase de cada passo só existe para ferramentas que o plugin sabe descrever; as demais aparecem com o nome e, se houver, o texto que o Hermes mandar. "Pensando" é medido, não lido: o Hermes não envia o raciocínio do modelo. O preço de mercado do que falta continua sendo pesquisa (`web_search` de preço) ou resposta dela, depois da escolha.

**Consequências.** 271 testes (`test_atividade.py`: uma frase por ferramenta, trava como não-falha, gravação por chamada, as tools de verdade; `test_pesquisa.py`: quando vira pesquisa e quando não, a ordem do roteiro, o próximo passo por sessão). Plugin 1.6.0, 19 tools, 5 hooks registrados (dois `pre_llm_call`, dois `post_tool_call`, um `transform_tool_result`; `hermes plugins validate` confere que os declarados são os registrados). `make restart` e `docker compose up -d --build --no-deps playground`.

## 20. A stack só vive enquanto o `make run` roda

**Contexto.** Pedido: a stack não pode iniciar junto do sistema operacional; só sobe quando se roda `make run`, e Ctrl+C a derruba. Até aqui o `make up` subia tudo em segundo plano (`up -d`) e todo serviço tinha `restart: unless-stopped`. O Docker sobe no boot e religa container com `always` ou `unless-stopped`, então a inferência (GPU dividida com o desktop), o Hermes, o SearXNG e o playground voltavam a cada reinício da máquina, e não havia o que um Ctrl+C derrubasse. Não existe unit do systemd, cron nem autostart do projeto (conferido): o único gatilho era a política de restart.

**Decisão.**
- **`restart: "no"` em todos os serviços.** O daemon do Docker continua habilitado no boot (outros projetos dependem dele), mas não religa nada daqui. Os containers que já existiam receberam `docker update --restart=no` (mudar o compose não altera container existente), inclusive um `sabor-inference-max` esquecido do spike de 13/11, que também estava com `unless-stopped`.
- **`make run`**: `docker compose up --build --menu=false` em primeiro plano, com os logs na tela; Ctrl+C derruba tudo. Um `trap` no alvo garante o mesmo quando a aba do terminal é fechada (SIGHUP) ou o `compose` morre antes de parar os serviços: só roda `stop` se sobrou algo rodando, e termina com `==> stack parada`. O `--menu=false` tira o atalho `d` (desanexar) do Compose, que deixaria a stack viva depois de fechar o terminal. `make up` continua, só para segundo plano (scripts); `make down` derruba.
- **`api` com `stop_signal: SIGINT`.** A API é o PID 1 do container e o kernel ignora SIGTERM em PID 1 sem handler: com SIGTERM o `docker stop` levava 10,1 s e terminava em SIGKILL (código 137); com SIGINT, que o `main()` já trata como `KeyboardInterrupt`, leva 0,11 s e sai com 0.

**Prova (15/11/2026, num terminal de verdade, via PTY).** Com o `make run` no ar e tudo saudável, Ctrl+C parou os cinco serviços em 4,5 s, todos com código de saída 0 (`api` em 23 ms; playground e inferência em menos de 0,2 s; Hermes e SearXNG em cerca de 4 s), e `make run` voltou a subir tudo a partir do estado parado em 6,7 s. Fechar a aba do terminal com o `make run` no ar também parou tudo em 5 s, sem sobrar processo de `make` nem de `compose`. Não reiniciei a máquina: o que impede o boot de religar algo é a política `no`, conferida com `docker inspect` em cada container.

**O que ficou de fora.** A recuperação automática: com `restart: "no"`, uma inferência que cair (já vimos `CUDA error: the launch timed out`, que o Docker antes subia de volta) fica parada até um novo Ctrl+C e `make run`. `on-failure` manteria a recuperação, mas ficou a dúvida se um container morto no desligamento do sistema (código diferente de zero) voltaria no boot; como o pedido era que nada subisse, `no` não deixa dúvida.

**Consequências.** `make run` é o jeito de usar a stack; `make smoke`, `make chat`, `make status` e `make logs` rodam em outro terminal com ele no ar. Quem subir em segundo plano (`make up` ou `docker compose up -d`) derruba no fim com `make down`. Os avisos da tela ("Confere se o make run está rodando") passaram a apontar para o `make run`.

**Prontidão e rede.** O Docker chegou a iniciar `sabor-api` sem conectá-lo à rede, e o healthcheck pelo loopback passou mesmo com o nginx devolvendo 502. `make run` agora força a recriação dos contêineres, preservando os dados montados em `hermes-home/`. O healthcheck da API usa o nome `api` e verifica o SQLite; o playground espera a API e o Hermes e testa os dois pelo nginx.


## Pesquisa regional e Analytics a partir do aceite

O histórico de pedidos tem cobertura desigual. Mantemos os recortes publicados e receitas de referência em Markdown, com local, período e fonte. O hook injeta as fontes e os recortes aplicáveis em turnos de pesquisa; a LLM usa a despensa e o perfil para escolher o que pesquisar e o que apresentar. A busca é aberta, e os passos da tela vêm dos eventos reais de busca/leitura. Os rankings ilustrativos ficaram no leitor legado, inclusive para reconhecer nomes de prato no carrinho.

Depois que a Dona Maria aceita a versão viável de uma receita, a LLM escolhe dois ou três cenários de margem e redige uma recomendação. A tool valida as entradas e calcula os valores com o CMV do prato e a taxa de 10%, mantendo as contas consistentes entre conversa e tela. Os cenários ficam no SQLite sem reservar estoque; registrar o preço escolhido continua sendo a operação que reserva ingredientes e compromete orçamento.

O contrato do Playground é `GET /api/analytics/analises`, na mesma origem, sem parâmetros. Retorna `{"analises":[]}` quando não há proposta gravada. Cada entrada inclui identificação do prato, CMV, mínimo, cenários, recomendação e status (`proposta` ou `registrado`); os campos de preço escolhido só são preenchidos no registro. Remover um prato o retira da leitura. O endpoint é somente de leitura: o agente grava via `preco_sugerir` e pode consultar o mesmo estado via `analytics_analises_consultar`.
