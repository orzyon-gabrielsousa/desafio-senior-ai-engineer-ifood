# Sabor da Maria — consultora de cardápio e precificação com Hermes Agent

Solução do desafio técnico **Senior AI Engineer** (iFood): o [Hermes Agent](https://github.com/nousresearch/hermes-agent) é a consultora de cardápio da Dona Maria, que está abrindo o delivery *Sabor da Maria*. O agente pesquisa receitas, usa as respostas sobre a cozinha, compara os ingredientes com a despensa e o orçamento de R$ 80 e propõe preços com base no custo do prato, na taxa fixa de **10% sobre a venda**, conforme o enunciado. Ela decide o preço final.

O modelo de linguagem, a busca via SearXNG, a extração de receitas e o SQLite rodam em Docker, sem chave de API. Ela pode conversar no playground em React (`http://localhost:3000`) ou na TUI (`make chat`). No playground, ela cadastra o negócio e a cozinha na própria conversa, monta o carrinho dentro do orçamento de R$ 80 e acompanha a escolha do prato até o preço. A consultora confere ingredientes e equipamentos; o Analytics separa o custo da porção, os descontos e a sobra estimada.

> Enunciado completo em [`docs/desafio-senior-ai-engineer.md`](docs/desafio-senior-ai-engineer.md). Decisões de arquitetura com alternativas descartadas em [`docs/decisoes.md`](docs/decisoes.md).

## Arquitetura

```
┌──────────────────────────── docker compose ────────────────────────────┐
│                                                                        │
│  navegador :3000 ──▶ playground (React + nginx)                        │
│                         │  /hermes  (proxy, chave só no servidor)       │
│                         │  /api     (proxy) ──▶ api: carrinho, salvos, │
│                         │                         perfil, analytics    │
│  TUI (make chat) ───────┼──────────────────────┐                       │
│                         ▼                      │                       │
│              ┌────────────────────────────────┴─────────────────────┐ │
│              │ hermes (nousresearch/hermes-agent:v0.23.1)            │ │
│              │  HERMES_HOME = ./hermes-home                         │ │
│              │   ├ SOUL.md              persona da consultora        │ │
│              │   ├ workspace/AGENTS.md  regras do negócio            │ │
│              │   ├ skills/…/consultoria-cardapio  playbook           │ │
│              │   ├ memories/            MEMORY.md + USER.md          │ │
│              │   ├ plugins/sabor_da_maria   tools, hooks e extrator  │ │
│              │   ├ sabor_da_maria.db    SQLite                       │ │
│              │   └ /prompts (ro)  base de conhecimento do Analytics  │ │
│              └────────────┬───────────────────────┬──────────────────┘ │
│                OpenAI API │                       │ /search?format=json│
│                           ▼                       ▼                    │
│        ┌────────────────────────────┐   ┌──────────────────────┐      │
│        │ inference (llama.cpp, GPU) │   │ searxng (metabusca)  │      │
│        │ Qwen3-8B Q4_K_M · 64k YaRN │   │ google/bing/brave/…  │      │
│        │ tool calling nativo        │   └──────────────────────┘      │
│        │ [perfil max: Modular MAX]  │        web_extract ──▶ receitas │
│        └────────────────────────────┘        (schema.org/Recipe)      │
└────────────────────────────────────────────────────────────────────────┘
```

| Peça | O que é | Por quê |
|---|---|---|
| **Hermes Agent** | produto; nada no core alterado | é o que o desafio avalia |
| **SOUL.md** | identidade e tom da consultora | sempre no prompt (slot 1) |
| **AGENTS.md** | regras do negócio, fluxo e "regras de ouro" | contexto do projeto carregado da pasta de trabalho |
| **Skill `consultoria-cardapio`** | playbook passo a passo com exemplos de chamadas | pré-carregada em `make chat`, consultável via `skill_view` |
| **Plugin `sabor_da_maria`** | Despensa, compras, perfil da cozinha e do negócio, conferência, CMV, preço, cardápio, carrinho, itens salvos e Analytics. Hooks de conversa e provider `receitas-local` de `web_extract`. | lógica determinística e travas fora do modelo; só stdlib |
| **SQLite** | despensa normalizada, perfil da cozinha (o que a consultora elicita e o que ela marca em Minha cozinha, o mesmo registro), receitas, pratos, compras, carrinho (com pratos abertos), pratos e ingredientes salvos, perfil do negócio (com o lugar da loja) | banco embutido, persistente entre sessões |
| **Base de conhecimento** | `prompts/base-de-conhecimento/`: evidências nacionais e regionais, fontes culinárias e guia de precificação | o plugin carrega as fontes e os recortes da região no turno de pesquisa; a LLM cruza com a despensa e lê receitas na web |
| **Fichas de cozinha** | `prompts/base-de-conhecimento/cozinha-e-equipamentos/`: `pratos.md` (equipamentos, utensílios e habilidades para cada prato do catálogo, com custo ilustrativo legado) e `ingredientes.md` (o que cada ingrediente precisa para virar comida) | a conferência avisa, antes de ela gastar o orçamento, que **com a cozinha dela não tem como fazer aquele prato ou aquela comida com aquele ingrediente**; trocar por dados reais é editar tabela |
| **API da tela** | serviço `api`: Python stdlib na própria imagem do Hermes, usando a mesma `Store` e o mesmo leitor de Analytics do plugin, porta 8700 só no loopback | a tela edita carrinho, pratos, salvos, perfil do negócio e Minha cozinha e lê o Analytics sobre os mesmos dados que a consultora; nenhuma regra duplicada, nada novo para baixar |
| **llama.cpp + Qwen3-8B** | servidor OpenAI-compatible com `tool_calls` nativos, 64k de contexto | roda na RTX 5070 Ti com folga; MAX fica como perfil experimental |
| **SearXNG** | busca web local, JSON, pt-BR | zero chaves, zero SaaS |
| **Playground** | React + nginx, porta 3000 | chat da consultora, carrinho com saldo ao vivo (pratos abertos e aba Salvos), Minha cozinha, perfil do negócio e Analytics (com o quanto cobrar); o proxy injeta a chave, o JS não a vê |
| **clarify + memory** | tools nativas do Hermes | elicitação com múltipla escolha na TUI; memória entre conversas |

## Como o fluxo do desafio foi implementado

A consultora conduz cinco etapas pelo chat: **Negócio → Seu estoque → Escolher caminho → Montar pratos → Preço e divulgação**. Ela apresenta uma decisão por vez. O negócio começa por atividade, comida e localização, com objetivo opcional; depois mostra estoque já pago e custos e espera a prontidão. “Agora não” pausa a condução entre conversas. Em seguida a pessoa escolhe ajuda da IA com pesquisa real ou seu próprio prato. Cozinha, ingredientes, complementos e carrinho ficam dentro da montagem, conferindo apenas os requisitos relevantes. Depois do aceite da versão, a pessoa escolhe o preço com a taxa fixa de 10%. A divulgação é opcional e posterior ao registro; seu simulador é uma projeção separada das vendas realizadas e dos R$ 80 de complementos.

O estado da jornada é salvo no SQLite; consultar ou navegar pelo carrossel não confirma nada. O carrossel mostra detalhes da etapa selecionada, sem a antiga lista “Agora/etapa pendente”. A página abre com um chat limpo e mantém conversas anteriores na lateral. Fichas longas permitem expandir ingredientes e preparo, mantendo a fonte, o rendimento, os custos e a próxima pergunta visíveis. Escolher prato próprio ou receber uma adaptação muda o foco de trabalho e preserva os rascunhos anteriores em Minha cozinha; não reserva estoque nem escolhe preço.

O cenário `conducao` foi validado com o modelo real: cadastro concluído → apresentação do estoque → pausa → prontidão → escolha de ajuda → pesquisa real sem nome ou URL fornecidos pela pessoa → adaptação conferida → aceite → preço → divulgação dispensada. A fonte pesquisada tinha ingredientes ausentes; a proposta adaptada preservou a fonte e explicitou as diferenças. O registro gerou seis reservas e manteve os R$ 80,00 de complementos. Nessa execução, a proposta levou 22,08 s, enquanto aceite, registro e recusa de publicidade levaram cerca de 0,12 s cada. São medições de um cenário, sem garantia de latência para outras buscas.

| Etapa | Onde está | Como funciona |
|---|---|---|
| **2.1 Pesquisa de receitas** | `despensa_listar` → `web_search` (SearXNG) → `web_extract` (provider `receitas-local`) → `despensa_buscar` → candidatas em texto | quando ela pede receita ou ideia de prato, o painel do turno traz o roteiro em ordem e o resultado de cada ferramenta leva o próximo passo (`pesquisa.py`): o extrator lê schema.org/Recipe (JSON-LD) e devolve ingredientes, rendimento, tempo e passos (sem JSON-LD, cai para o texto do `<article>`); a consultora confere os ingredientes na despensa e apresenta 2 ou 3 candidatas com a fonte, o que ela já tem e o que falta, e pergunta de cada uma **"Você gosta de cozinhar isso? Vê algum impedimento?"**; o carrinho e o preço só entram depois que ela escolher. Cada passo aparece no chat enquanto acontece (ver *Passo a passo à vista*) |
| **2.2 Elicitação de restrições** | Consulta e salvamento do perfil pelo agente; escolhas no chat e Minha cozinha | coleta progressiva: antes do aceite, o agente pergunta apenas os requisitos ainda desconhecidos da receita em questão. Os demais itens do catálogo podem ficar sem resposta. Equipamentos, técnicas, duração, energia, gás e refrigeração relevantes continuam no veredito; confirmações específicas valem só para o lote conferido |
| **2.3 Ingredientes** | `despensa_buscar`, `receita_conferir_ingredientes`, `despensa_atualizar` | casamento aproximado com sinônimos (cheiro-verde→salsinha, fubá→polenta...), conversão de medidas caseiras (colher, xícara, dente, "1 cebola média") com premissas explícitas, o que tem / falta / custo / cabe no orçamento, veredito `viavel` · `precisa_confirmar` · `inviavel_por_enquanto` |
| **2.4 CMV e preço** | `prato_calcular_cmv` → `preco_sugerir` → `cardapio_registrar` | CMV = Σ quantidade usada × custo unitário, incluindo compras complementares. O preço mínimo é CMV ÷ 0,90, com teto ao centavo; os cenários mostram o repasse de 90% e o lucro após os ingredientes. O registro só acontece com o preço escolhido por ela e reserva o estoque e o orçamento numa operação só |

A receita extraída fica preservada em `fonte_verificada`. A conferência compara a ficha proposta com os ingredientes, medidas e rendimento da fonte, acrescenta requisitos evidentes do preparo e mantém fichas incompletas como rascunhos. Uma adaptação guarda as diferenças e seu preparo, mostra esses dados no chat e exige aceite posterior antes dos cenários de preço. A retomada usa o rascunho único da mesma sessão e fonte, sem exigir que a pessoa repita um identificador interno.

Detalhes que a planilha esconde e o plugin trata: unidades compostas (`balde 2kg`, `un 500g`, `un 500ml`) normalizadas para kg/L; a **cobertura de chocolate** vem como `1 un` sem peso, então a tool sinaliza e o agente pergunta o peso do pote antes de usar em receita medida em gramas.

**Travas no backend.** O veredito da conferência (`viavel` · `precisa_confirmar` · `inviavel_por_enquanto`) não é só aviso: `prato_calcular_cmv`, `preco_sugerir` e `cardapio_registrar` recusam com `bloqueado: true` e a lista de `bloqueios` (forno não informado, técnica que ela disse não dominar, ingrediente sem preço, compra que não cabe nos R$ 80) enquanto o estado salvo não estiver `viavel`, mesmo que o modelo escreva na conversa que está tudo certo. O registro reavalia tudo contra o estado atual, recusa se CMV ou compras mudaram desde a apresentação (o preço foi escolhido em cima de outros números) e, quando aceita, reserva na despensa o que o prato usa e compromete as compras no orçamento numa transação só. Candidatas não consomem estoque; o segundo prato enxerga `disponivel = estoque − reservas`. Repetir o registro não duplica nada; `cardapio_remover` libera reserva e orçamento.

**Trava de evidência no perfil.** Numa prova com o modelo real o Qwen3-8B gravou no perfil da cozinha técnicas e utensílios que a Dona Maria nunca citou (copiando até os valores de exemplo do schema da tool). Agora `perfil_cozinha_salvar` só grava o que ela disse: dois hooks públicos de plugin (`pre_llm_call` e `post_tool_call` do `clarify`) guardam as falas dela por sessão (`falas.py`), e cada item enviado precisa ter base nelas (a fala cita o item ou um sinônimo e, se o valor é número, o número também aparece; "tenho sim" conta junto com a pergunta a que respondeu). O que não tem base volta em `recusados` e não entra; o perfil fica incompleto e o agente tem de perguntar. A conferência considera a polaridade: negativas explícitas substituem respostas positivas antigas, e afirmações ambíguas deixam o item pendente. Citações, hipóteses e falas de terceiros não viram fatos da cozinha. Os valores de exemplo saíram do schema.

**Passo a passo à vista.** O chat mostra cada ação da consultora no balão, em ordem e enquanto acontece: "Pesquisando na internet" com a busca digitada, "Lendo a receita" com o site, "Conferindo a despensa", "Comparando com a despensa e o orçamento", "Calculando o CMV"; entre uma ação e outra aparece "Pensando no próximo passo" com os segundos, e "Escrevendo a resposta" quando o texto começa. Terminada a ação, o passo ganha o que ela achou e quanto levou ("3 resultados · receitasdeminuto.com, tuta.com.br", "Leu 2 páginas: Frango com Batata Fácil (10 ingredientes, rende 4 porções)", "Tem: frango (2 kg), batata (3 kg) · Não tem: creme de leite", a conta de cada ingrediente da conferência, "Travado: falta a cozinha dela" quando uma trava recusa), com a lista completa ao abrir. A barra lateral "Agora" mostra a ação em curso e as últimas. Como funciona, sem tocar no core do Hermes: o streaming manda `hermes.tool.progress` (nome da ferramenta, o argumento principal e começo/fim), que não traz o **resultado**; o hook público `post_tool_call` recebe argumentos e resultado de **toda** ferramenta (as do plugin e as do Hermes, como `web_search`), `atividade.py` escreve a frase em português só com o que a ferramenta devolveu e guarda no SQLite pelo `tool_call_id`, e a tela junta as duas pontas (`GET /api/atividade?ids=`). "Pensando" é o intervalo entre os eventos: o Hermes não envia o raciocínio do modelo.

**Minha cozinha e compatibilidade.** A cozinha da Dona Maria é **um registro só** (tabela `perfil` do SQLite): a consultora pergunta durante a montagem, conforme os requisitos de cada receita (equipamentos e utensílios, técnicas que ela domina, restrições operacionais) e grava com `perfil_cozinha_salvar`; ela também marca tudo na tela **Minha cozinha** (menu, acima de Meu negócio): equipamentos, panelas e utensílios, 15 habilidades (sei / aprendendo / não sei) e a rotina (geladeira, tempo, gás, energia). O que um lado grava, o outro mostra: o que a conversa contou aparece na tela (o que não é do catálogo, como "massa fresca", vai para "O que você contou na conversa", com × para apagar) e o que ela marca vale para a consultora, o carrinho e as travas do cardápio. O painel do turno leva a cozinha a toda mensagem e orienta perguntas progressivas sobre os requisitos do preparo. Os itens irrelevantes do catálogo podem ficar sem resposta. Em `prompts/base-de-conhecimento/cozinha-e-equipamentos/` estão as fichas (`pratos.md` e `ingredientes.md`: o que cada prato e ingrediente precisa, `A ou B` para alternativas), lidas por `cozinha.py`. Com elas, a **conferência** classifica cada prato e cada ingrediente contra a cozinha dela: `nao_da` (ela disse que não tem), `atencao` (não sabe ou está aprendendo), `confirmar` (falta responder) ou `ok`. Em Minha cozinha cada prato ganha o selo ("Falta forma", "Dá para fazer"); no carrinho o aviso aparece no prato aberto ou no ingrediente avulso ("polpa de açaí precisa de freezer para virar açaí na tigela, e você marcou que não tem: com a sua cozinha não tem como fazer açaí na tigela com esse ingrediente") e nas tools vem pronto no começo de `diga_a_dona_maria`. É **aviso, não trava**, onde a compra é rascunho (a decisão é dela); no caminho da receita ao cardápio o que a ficha exige entra no veredito das travas existentes e bloqueia como qualquer requisito.

**Pratos em Minha cozinha.** A aba **Pratos** de Minha cozinha lista os 47 pratos da base agrupados por categoria (as do tipo de alimentação dela primeiro, abertas), cada um com o que precisa (✓ ela tem, ✗ falta, ? ainda não respondeu, com as alternativas "A ou B") e as habilidades que pede (sei / aprendendo / não sei / ?), e o selo "Dá para fazer", "Falta forno", "Atenção: habilidade" ou "A confirmar". É o "se ela possui os equipamentos, utensílios e habilidades para cada prato": a conta é a mesma conferência que embasa os avisos do carrinho (`cozinha.pratos_da_cozinha`, `GET /api/cozinha/pratos`).

**Quanto cobrar.** Depois do aceite do prato, a consultora propõe cenários de margem com o CMV daquela versão. O Analytics mostra o mínimo para cobrir os custos considerados, quanto sobra por porção e o preço adotado quando ela decidir. O volume para uma meta usa apenas os valores informados pela Dona Maria para o mesmo período.

**Meu negócio.** O negócio dela é **onde a loja fica** (país, estado, cidade e zona da cidade), o **tipo de negócio** (restaurante, marmitaria, pizzaria…) e o **tipo de alimentação** que ela faz (comida caseira, marmitas, pizzas, doces…); o que antes eram "o que você vende", "para quem" e "observações" saiu, e **os pratos moram em Minha cozinha**. Como a cozinha, é perguntado na primeira conversa e gravado no mesmo registro (SQLite, `config`) que a tela **Meu negócio** edita: a consultora usa a tool `negocio_salvar`, que só grava o que ela disse (cada valor precisa estar nas falas dela: o estado pelo nome ou pela sigla em maiúsculas, a cidade, a zona só como "zona leste"/"leste da cidade", o tipo e a alimentação pelas palavras dela; "Rio Grande do Sul, zona norte" não vira zona sul) e faz merge (os tipos de alimentação se somam). O painel do turno identifica a próxima etapa e orienta perguntas curtas sobre o que ainda falta, sem exigir uma lista completa antes de começar a pesquisa. O país é o Brasil por enquanto (a pesquisa regional é brasileira): a tela mostra o campo e a consultora cobre a pergunta junto do estado, mas não há outro país para escolher.

**Carrinho, saldo e perfil do negócio.** O carrinho é a lista de compras dela dentro do orçamento: `saldo = orçamento − compromissos de pratos aceitos − carrinho`, e item que não cabe no saldo é recusado (na tela e nas tools) com quanto falta. A tela (barra do topo, painel do carrinho, "Meu negócio", Analytics) fala com o serviço `api`; a consultora usa as tools `carrinho_consultar`, `carrinho_adicionar` (ingrediente avulso), `carrinho_adicionar_prato`, `carrinho_remover` e `carrinho_montar_da_receita` (esta última só com receita `viavel`). Como um modelo de 8B esquece de chamar tool, um hook injeta em todo turno um painel curto (saldo, carrinho, salvos, perfil e local do negócio; fontes e evidências regionais quando ela pede receitas), então ela sabe o que a Dona Maria mexeu na tela sem perguntar ("você já pegou a couve, o saldo é R$ 68,00"). Quando o prato é aceito, os itens dele saem do carrinho e viram compras comprometidas, para o mesmo gasto não contar duas vezes.

**Prato aberto e itens salvos.** Prato nunca entra no carrinho como um item só com preço fechado ("coxinha, R$ 18"): ele entra aberto, com cada ingrediente, a quantidade e o valor, mais o total do prato. O que ela já tem na despensa aparece com o valor e o selo "já tem", e **não gasta saldo** (só o que ela compra conta). `carrinho_adicionar` recusa nome de prato (os salvos e os da base de pratos mais vendidos) e manda abrir o prato; ingrediente sem valor ou prato sem valor por ingrediente nem entram. Pratos e ingredientes avulsos podem ser **salvos** (aba Salvos na tela, tool `salvos_salvar`) e voltam ao carrinho de um toque ou pelo nome, com os mesmos valores. A migração é aditiva (`ALTER TABLE` só quando falta a coluna), então um banco antigo continua valendo.

**Pesquisa regional e Analytics.** A pasta [de evidências](prompts/base-de-conhecimento/pratos-mais-vendidos/evidencias/) reúne dados públicos de pedidos e receitas para avaliar nas cinco regiões. A consultora recebe o recorte da loja e a [lista aberta de fontes culinárias](prompts/base-de-conhecimento/pesquisa-de-receitas/fontes.md). Ela pesquisa e lê receitas reais, compara os ingredientes com a despensa e cita a fonte. Recortes estaduais ou nacionais não viram rankings de bairro; pratos típicos entram como ideias. Os arquivos de ranking ilustrativo da demonstração anterior são mantidos para compatibilidade e ficam fora do contexto do modelo e da tela.

O [guia de precificação](prompts/base-de-conhecimento/analytics/precificacao.md) orienta a LLM a escolher margens, nomes e um cenário recomendado. O Analytics recalcula os cenários com a ficha e os custos atuais, preservando o preço já registrado. As regras da avaliação são fixas: **10% sobre a venda**, repasse `0,90 × P`, lucro `0,90 × P − CMV` e mínimo `CMV ÷ 0,90`, arredondado para cima ao centavo. Não são acrescentados encargos de pagamento nem impostos. Perfis antigos e tentativas de alterar taxas são normalizados para essa regra. A escolha de preço continua com Dona Maria e só `cardapio_registrar` reserva estoque e compromete o orçamento.

O [enunciado](docs/desafio-senior-ai-engineer.md) é a referência das regras de negócio, também registrada em [AGENTS.md](AGENTS.md). Por orientação do usuário, pedidos divergentes — como usar tarifas comerciais atuais — são ajustados ao contrato da avaliação.

## Vendas realizadas e atalhos do topo

O cabeçalho separa orçamento de complementos, vendas realizadas e quantidade de pratos salvos. Vendas diárias, semanais e mensais abrem o Analytics no período escolhido; Pratos abre Minha cozinha na aba Pratos; o nome do delivery abre Meu negócio. Sem vendas registradas no período, o topo exibe “—”. Rascunhos e pratos aprovados não viram vendas automaticamente.

O Analytics permite informar uma venda de um prato aprovado com quantidade, preço efetivamente praticado e data. Pelo chat, `vendas_registrar` registra a venda explicitamente informada na mensagem atual, e `vendas_consultar` consulta os totais. Previsões, aprovações e relatos de terceiros não viram vendas. O histórico admite estorno e restauração para correções. Cada envio tem uma chave de operação para que repetir uma tentativa não duplique a venda. Os períodos usam America/Sao_Paulo: hoje, semana de segunda a domingo e mês corrente.

A tabela aditiva de vendas preserva nome, preço e CMV da ficha no registro. O faturamento é a soma das vendas válidas; o repasse estimado aplica os 10% do desafio, e o lucro estimado desconta o CMV salvo. Custos fixos não entram nessa conta. Registrar ou estornar uma venda não inventa produção nem altera o estoque, compras ou o orçamento de R$ 80,00. A montagem continua como rascunho até o aceite e a escolha de preço; só os complementos comprometem saldo, e ingredientes já comprados entram no CMV sem novo débito.

## Memória e continuidade entre conversas

O SQLite da consultoria guarda estoque, cozinha, escolhas, fichas, análises e reservas como estado de negócio. O Hermes mantém as transcrições em `hermes-home/state.db`; `database-internals/conversas.sqlite3` espelha os chats do playground, e `database-internals/memoria/chat.sqlite3` indexa trechos e embeddings locais para recuperar contexto de outras sessões. Arquivos importados em `database-internals/contexto/` também podem ser consultados. O cache KV em VRAM e seus snapshots no SSD aceleram a inferência; as decisões persistidas continuam no SQLite. Detalhes em [memória local](database-internals/README.md) e [cache de inferência](inference/llamacpp/README.md). Conteúdo recuperado é contexto; compras e registros exigem confirmação no turno atual.

## Rodando

O ambiente validado é **Linux com GPU NVIDIA**. Prepare estes itens no computador:

- GNU Make, Bash, `curl`, `sha256sum` e `flock`. Em Debian/Ubuntu, os pacotes são `make bash curl coreutils util-linux`.
- Docker em execução e acessível pelo seu usuário, com Compose v2.28 ou posterior e Buildx. [Instalar Docker Engine](https://docs.docker.com/engine/install/) · [Instalar Docker Compose](https://docs.docker.com/compose/install/)
- Uma GPU NVIDIA acessível ao Docker, driver compatível com CUDA 13 (série 580 ou posterior) e NVIDIA Container Toolkit. A configuração padrão foi testada com uma RTX 5070 Ti de 16 GB. Reserve cerca de 15 GB para imagens e modelos e tenha conexão com a internet para os downloads e a busca de receitas. [Configurar NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

No Windows, o Docker Desktop oferece acesso à GPU NVIDIA com WSL2. Esse ambiente ainda precisa de validação com o projeto. A configuração CUDA atual requer NVIDIA e fica indisponível no macOS. [Suporte à GPU no Docker Desktop](https://docs.docker.com/desktop/features/gpu/)

Na raiz da cópia do repositório:

```bash
make install   # prepara configuração, imagens, modelos, frontend e SQLite
make run       # sobe os serviços; Ctrl+C encerra a execução
# em outro terminal, com o make run no ar:
make smoke     # smoke test ponta a ponta (tool calling, busca, plugin, resposta do agente)
make chat      # TUI do Hermes com o playbook pré-carregado
```

O `make install` verifica a GPU pelo Docker, baixa o Qwen3-8B (5,0 GB) e o modelo de embeddings (cerca de 640 MB), compila o playground e importa a despensa inicial da planilha para o SQLite. Python, Node.js e as bibliotecas vêm das imagens. O instalador reaproveita arquivos e dados existentes. Execute `make run` nas próximas sessões.

**Nada sobe com o sistema.** A stack vive só enquanto o `make run` está rodando: todos os serviços têm `restart: "no"` (o Docker sobe no boot, mas só religa container com `always` ou `unless-stopped`), Ctrl+C no terminal derruba tudo e fechar a aba do terminal também. Se um serviço cair, nada o religa sozinho: Ctrl+C e `make run` de novo. Para subir em segundo plano (scripts), `make up`; para derrubar, `make down`.

O playground abre em [http://localhost:3000](http://localhost:3000) junto com `make run`. As respostas da consultora chegam em fluxo e as ações aparecem no painel **Agente**. As opções clicáveis de cadastro permitem responder vários itens de uma vez. A montagem inicial de pratos pesquisa receitas reais automaticamente. A conferência preserva a ficha da fonte e aponta ingredientes omitidos ou medidas divergentes; versões adaptadas precisam ser apresentadas e aceitas como tal. Acima da caixa de mensagem, o globo permite pedir uma pesquisa explicitamente no próximo envio. A barra do topo mostra orçamento, carrinho e saldo, e o **Caminho do prato** mostra uma etapa de cada vez, com setas para avançar ou voltar. A chave da API fica no nginx; o navegador só fala com a mesma origem (`/hermes/` e `/api/`). Para mexer na interface com recarga rápida, `make playground-dev` (Node 20+ na máquina, Hermes já no ar).

Outros atalhos: `make ask Q="..."` (pergunta única), `make validate` (plugin e ferramentas), `make test` (suíte do plugin), `make restart` (Hermes e API), `make logs`, `make up` (segundo plano) e `make down`.

`make reset-dados` para os serviços da aplicação, arquiva os bancos e o histórico em `.sabor-backups/` e limpa as memórias da demonstração. Rode `make run` para iniciar novamente. A despensa inicial é recarregada da planilha e o playground limpa as conversas locais ao consultar o novo banco.

Na subida, o `make run` cria as pastas de runtime e recria os contêineres para renovar as conexões à rede do Docker. O SQLite da consultoria e os bancos internos do Hermes são preservados em `hermes-home/`. O playground só abre depois que o Hermes e a API confirmam que estão prontos; a API abre o SQLite e carrega a despensa automaticamente se for a primeira execução. `make status` mostra a saúde dos serviços, e `make smoke` também verifica as rotas de dados pelo nginx.

### Roteiro de demo (5–10 min)

1. No chat novo, preencha **Meu negócio** e **Minha cozinha** pelas opções da conversa. Clique de novo para desmarcar antes de enviar. É possível responder em texto, tanto no playground como na TUI.
2. Confira em **Meu negócio** e **Minha cozinha** se as respostas ficaram salvas. A aba **Pratos** de Minha cozinha mostra o que dá para fazer.
3. "Gosto de fazer frango e carne moída, para marmita." → busca receitas reais, extrai e apresenta 2–3 com fonte; pede feedback. A cozinha salva é consultada toda vez que ela monta um prato: se a receita precisa do que ela não tem, a conferência diz.
4. Conferência de ingredientes: o que ela tem, o que falta, custo, cabe nos R$ 80? Se um item é vendido por unidade sem peso, ele pergunta.
5. Ative o globo acima do campo de mensagem e peça uma receita. A consultora busca versões que aproveitam a despensa, mostra as fontes e pergunta quais interessam. Confirme equipamento, preparo, rendimento e ingredientes da versão. O **Analytics** mostra os cenários antes da escolha do preço. Depois de escolher o preço na conversa, a ficha exibe a decisão registrada.
6. Aceite → CMV com memória de cálculo → preço mínimo e 3 cenários → ela escolhe → cardápio registrado, estoque reservado e orçamento abatido (os itens da receita saem do carrinho e viram compras comprometidas). Se o agente tentar pular a elicitação, a tool recusa e devolve o que falta perguntar.
7. Fechar com `cardapio_consultar` e mostrar que a memória e o banco guardaram a cozinha e as decisões: uma conversa nova já sabe e **não pergunta de novo** o que está salvo.

## Estrutura do repositório

```
.
├── docker-compose.yml        inference-llamacpp (padrão) · inference-max (experimental) · searxng · hermes · api · playground
├── prompts/base-de-conhecimento/   evidências por região, fontes de receitas, fichas de cozinha e guia de Analytics
├── prompts/base-de-conhecimento/cozinha-e-equipamentos/ fichas: pratos.md (o que cada prato precisa e custo típico de exemplo) · ingredientes.md · README

├── .env.example              portas, modelo, UID/GID, chave do API server
├── Makefile                  install · run · up · smoke · chat · test · validate · reset-dados
├── data/despensa_dona_maria.xlsx   entrada do desafio (montada só-leitura no container)
├── hermes-home/              = $HERMES_HOME do container (só a customização é versionada)
│   ├── config.yaml           modelo local, web backends, toolsets enxutos, plugin habilitado
│   ├── SOUL.md               persona
│   ├── workspace/AGENTS.md   contexto do projeto (regras, fluxo, formato)
│   ├── skills/sabor-da-maria/consultoria-cardapio/SKILL.md
│   ├── memories/MEMORY.md, USER.md   sementes de memória
│   └── plugins/sabor_da_maria/
│       ├── plugin.yaml       manifesto das ferramentas, hooks e provider local
│       ├── __init__.py       register(ctx): tools, hooks e provider
│       ├── tools.py          handlers, travas (veredito único, evidência do perfil) + schemas (OpenAI function format)
│       ├── store.py          SQLite (despensa, perfil, receitas, pratos, compras, reservas, carrinho com pratos, salvos, negócio)
│       ├── falas.py          o que a Dona Maria disse (hooks) e a conferência de evidência
│       ├── painel.py         carrinho, saldo, perfil e guias do turno para pesquisa e preço
│       ├── jornada.py        próxima etapa da consultoria, retomada e coleta progressiva
│       ├── pesquisa.py       a pesquisa de receitas (2.1) em ordem: roteiro no painel e próximo passo no resultado de despensa, busca e leitura
│       ├── atividade.py      o que cada ferramenta fez, em português, guardado por chamada (hook post_tool_call) para o passo a passo do chat
│       ├── negocio.py        perfil do negócio (onde a loja fica, tipo de negócio e de alimentação; opções fechadas) e o que ainda falta perguntar
│       ├── cozinha.py        Minha cozinha: catálogo (equipamentos, utensílios, habilidades), perfil salvo, leitor das fichas e conferência de pratos e ingredientes
│       ├── analytics.py      leitor legado da base ilustrativa e catálogo de nomes usados pelo carrinho
│       ├── api.py            API HTTP da tela (carrinho, pratos, salvos, perfil do negócio, Minha cozinha, Analytics, passo a passo)
│       ├── xlsx_reader.py    leitor .xlsx em stdlib
│       ├── units.py          unidades da planilha + medidas caseiras + densidades
│       ├── matching.py       sinônimos + casamento aproximado
│       ├── costing.py        CMV, preço mínimo, cenários, simulação
│       ├── extract.py        provider receitas-local (urllib + JSON-LD Recipe + texto)
│       └── tests/            testes do domínio (unittest)
├── searxng/settings.yml      JSON ligado, pt-BR, engines barulhentos removidos
├── inference/llamacpp/README.md · inference/max/README.md
├── playground/               React (Vite) — chat no estilo do iFood, carrinho com pratos abertos e salvos, Minha cozinha, perfil do negócio, Analytics com o quanto cobrar
│   ├── Dockerfile            build estático + nginx com proxy /hermes → :8642 e /api → :8700
│   └── src/                  sessão via X-Hermes-Session-Id e progresso de tools
├── scripts/smoke_test.sh · scripts/avaliar_fluxo.py (conversa isolada e métricas)
└── docs/decisoes.md · docs/desafio-senior-ai-engineer.md
```

## Configuração do Hermes em uma tela

```yaml
model:    { default: qwen3-8b, provider: custom, base_url: http://inference:8000/v1, context_length: 65536 }
web:      { search_backend: searxng, extract_backend: receitas-local, keyless_fallback: false }
plugins:  { enabled: [sabor_da_maria] }
tools:    { tool_search: { enabled: "off" } }
platform_toolsets:
  cli:        [web, clarify, memory, skills, todo, sabor_da_maria]
  api_server: [web, memory, skills, todo, sabor_da_maria]
```

O toolset é enxuto de propósito: ~26 tools (≈31 KB de schema) em vez das ~40 do bundle `hermes-cli`. Um modelo de 8B escolhe melhor com menos opções, e a consultora não precisa (nem deve ter) terminal, arquivos ou browser. O `tool_search` (meta-tool que esconde as tools de plugin até o modelo procurá-las) fica desligado: com ele ligado o Qwen3-8B não via as tools da despensa e respondia perguntas de custo com `web_search`. Sem `keyless_fallback`, nenhuma requisição sai para SaaS mesmo se um backend falhar.

## Modelo e desempenho medidos

| Item | Valor (RTX 5070 Ti 16 GB, driver 595) |
|---|---|
| Modelo | `unsloth/Qwen3-8B-GGUF` Q4_K_M, modo não-pensante |
| Contexto | 65 536 tokens (YaRN ×2 sobre 32 768), KV cache K q8_0 / V q4_0 |
| VRAM | ≈12,5 GB em uso no total com o desktop (≈9 GB do servidor) |
| Velocidade | ≈135 tokens/s de geração, ≈3 000 tokens/s de prompt |
| Saída por geração | padrão 1 536 tokens; teto efetivo 2 048 no proxy (`LLAMA_MAX_OUTPUT_TOKENS`), mesmo se o cliente pedir mais |
| Latência do roteiro completo | pesquisa + leitura + proposta conferida: cerca de 10–12 s e 3 chamadas; aceite e registro: cerca de 0,1 s cada, sem nova inferência (medição isolada; não é garantia para toda receita) |

Para mais qualidade com a GPU livre: `LLAMA_MODEL_FILE=Qwen3-8B-Q6_K.gguf` no `.env` (`make model MODEL_FILE=Qwen3-8B-Q6_K.gguf`). O perfil padrão desativa thinking explicitamente no template. Consulte os controles de geração em [inferência local](inference/llamacpp/README.md) antes de alterar o modo ou o teto.

### Modular MAX (perfil `max`, experimental)

`make up-max` sobe `modular/max-nvidia-full:26.7.0` com `Qwen/Qwen3.5-4B` no lugar do llama.cpp. Em novembro de 2026 o `max serve` em Blackwell de consumo (sm_120) ainda está em validação pela Modular, só o Qwen3.5 tem parser de tool calling e não há streaming com function calling; por isso é perfil opcional, não o padrão. No spike feito nesta máquina o servidor subiu e planejou a memória da GPU, mas o worker foi morto pelo OOM killer do host durante a compilação do modelo (>16 GB de RAM); o relato completo e como repetir estão em [`inference/max/README.md`](inference/max/README.md).

## Testes e validação

```bash
make test        # testes do plugin: planilha, cozinha, despensa, pesquisa, SQLite, fluxo de preço e API
make validate    # hermes plugins validate (manifesto, registros, colisões, security scan) + toolsets + prompt-size
make smoke       # /health, tool_calls nativos, SearXNG JSON, plugin habilitado, resposta do agente com a despensa
make avaliar     # fluxo real isolado: pesquisa → fonte → adaptação → aceite → preços → reservas
```

A medição completa usa um gateway Hermes temporário, a planilha original e uma cozinha de teste. Não copia conversas reais nem altera os bancos da aplicação. Verifica busca e leitura reais, a ficha da fonte, diferenças da adaptação apresentadas antes do aceite, cenários com 10%, escolha do preço, reservas e orçamento. A saída JSON registra tempo, chamadas únicas de ferramenta, configuração e limpeza do runtime.

```bash
python3 scripts/avaliar_fluxo.py --saida /tmp/sabor-avaliacao
python3 scripts/avaliar_fluxo.py --caso cozinha-incompleta --saida /tmp/sabor-cozinha
python3 scripts/avaliar_fluxo.py --caso embalagem-sem-peso --saida /tmp/sabor-embalagem
python3 scripts/avaliar_fluxo.py --caso orcamento-insuficiente --saida /tmp/sabor-orcamento
python3 scripts/avaliar_fluxo.py --caso aceite-com-ressalva
python3 scripts/avaliar_fluxo.py --caso conducao --saida /tmp/sabor-ressalva
python3 scripts/avaliar_fluxo.py --caso vendas --saida /tmp/sabor-vendas
python3 scripts/avaliar_fluxo.py --caso escolha-dois --saida /tmp/sabor-escolhas
python3 scripts/avaliar_fluxo.py --caso nova-busca --saida /tmp/sabor-nova-busca
python3 scripts/avaliar_fluxo.py --caso retomada-fonte --saida /tmp/sabor-retomada
```

Os casos de pendência começam com fichas sintéticas já salvas e verificam se o chat explica o dado necessário sem aprovar ou reservar ingredientes. O caso vendas usa um prato aprovado sintético, registra e consulta uma venda com a IA local e verifica totais e preservação de estoque e orçamento. Os casos completo e conducao fazem pesquisa na internet. O caso conducao parte de cozinha parcial, pede continuidade após o cadastro sem nomear receita e segue até o preço escolhido. O roteiro inicialmente levou 293,1 s e 12 chamadas no primeiro turno; após os ajustes, três execuções do mesmo roteiro levaram 11,83 s, 11,91 s e 9,93 s, com 3 chamadas cada. Aceite e registro passaram a usar a resposta conferida do domínio, sem uma geração adicional. São medições de um cenário, sujeitas a variação dos buscadores e da máquina.

## Limitações conhecidas

- Um modelo de 8B às vezes afirma o que a Dona Maria "já tem" antes de conferir; as travas das tools impedem que isso vire CMV, preço ou prato no cardápio, mas o texto da conversa ainda pode adiantar uma conclusão. Na demo vale pedir "confere na despensa".
- Entradas de compra exigem no backend o pedido/aceite da pessoa, quantidade e preço declarados. `despensa_atualizar` não registra compras. A origem é a declaração confirmada da pessoa; não há integração com fornecedores.
- Conversões de medidas caseiras são estimativas (densidade, peso de "1 cebola"); a tool marca cada uma e o agente deve repassar as que pesam no custo.
- A saída de uma geração é limitada no proxy, com padrão de 1 536 e teto de 2 048 tokens. Respostas muito longas podem terminar com `finish_reason: length`; uma ficha parcial continua bloqueada. O middleware encerra propostas, análises e registros já conferidos sem pedir outra geração. A revisão automática de memória/skills em segundo plano fica desativada para não disputar a GPU; histórico, perfil, embeddings e `memory` continuam ativos.
- O SearXNG depende de buscadores públicos; se um engine bloquear, os outros cobrem. Sem internet não há pesquisa de receitas.
- A tool `clarify` é da TUI (`make chat`). O playground usa cadastros clicáveis e perguntas no chat; o API server não oferece a interface interativa de `clarify`.
- A interpretação de fala livre é conservadora: equipamentos, técnicas e disponibilidade de gás/energia são conferidos contra a declaração recente. Valores contraditórios são corrigidos quando a fala é inequívoca; incerteza explícita deixa o campo pendente. Isso não é uma interpretação geral de ironia ou referências indiretas. A resposta de `clarify` na TUI tem testes de contrato, mas não uma demonstração integral da TUI.
- O carrinho é rascunho de compra: o aceite de prato (`cardapio_registrar`) continua olhando só os compromissos, não o carrinho. Conversões e somas de ingredientes ainda usam `float`; a precificação e a divisão do custo por porção usam `Decimal`, com mínimo arredondado para cima ao centavo.
- **Cobertura dos dados públicos.** Os recortes de pedidos variam conforme o lugar, a plataforma, a data e o produto. A base não oferece ranking comparável de pratos para as cinco regiões nem estimativa de venda da Dona Maria.
- O aviso de compatibilidade **só avisa** no carrinho (rascunho de compra, a decisão é dela); quem bloqueia é o caminho da receita ao cardápio. Prato fora da base não tem ficha: vale o que a consultora leu na receita.
- Na prova real com o Qwen3-8B (15/11/2026) o modelo, sozinho, gravava o tempo em horas (`2` em vez de `120` minutos) e jogava técnicas e a mensagem inteira em texto livre. O plugin corrige no backend: horas viram minutos quando ela disse "horas", `observacoes` saiu do schema da tool, o texto livre longo é recusado e o resultado do salvamento traz o resumo pronto (`diga_a_dona_maria`) e as perguntas que faltam. Em três provas seguidas a consultora perguntou na primeira resposta e salvou equipamentos, técnicas e rotina; o 8B continua podendo variar, e a trava de evidência segue impedindo que grave o que ela não disse.

- `carrinho_adicionar_prato` é rascunho de compra: não passa pela trava de viabilidade (equipamentos, técnicas, perfil) que `carrinho_montar_da_receita`, `prato_calcular_cmv`, `preco_sugerir` e `cardapio_registrar` aplicam; "confirmar que ela consegue cozinhar antes de comprar" fica, para o carrinho aberto à mão, na regra do `SOUL.md`/`AGENTS.md` (na prova ao vivo a consultora completou o perfil da cozinha antes de abrir o prato), e o aceite no cardápio continua com todas as travas.
- `carrinho_adicionar_prato` aceita os valores que a consultora passa (da despensa, de pesquisa ou ditos por ela, marcados como estimados quando for o caso). Um modelo de 8B às vezes erra uma conta no texto da conversa (já o vi somar errado um "a comprar"); por isso o carrinho, o total do prato e o saldo vêm da tool, e a consultora é instruída a copiar o `diga_a_dona_maria`.

## Próximos passos

- Spike do perfil MAX na RTX 50 e comparação com o llama.cpp no mesmo roteiro.
- Bônus em Mojo: matcher de ingredientes exposto ao Python via `PythonModuleBuilder`.
