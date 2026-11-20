export type Role = "user" | "assistant";

export type ToolStatus = "running" | "done" | "error";

/** O que a ferramenta fez, em uma frase (guardada pelo plugin, entregue por GET /api/atividade). */
export type ToolResult = {
  titulo: string;
  resumo: string;
  itens: string[];
  entrada: string;
  ok: boolean;
  duracao_ms: number | null;
};

export type ToolTrace = {
  id: string;
  name: string;
  label: string;
  status: ToolStatus;
  preview?: string;
  /** O argumento principal que o Hermes manda no streaming: a busca digitada, a página lida. */
  detail?: string;
  startedAt?: number;
  endedAt?: number;
  result?: ToolResult;
};

export type ChatMessage = {
  id: string;
  role: Role;
  content: string;
  tools: ToolTrace[];
  webSearch?: boolean;
  cadastroEnviado?: "negocio" | "cozinha";
  /** Ela recusou as sugestões mostradas até aqui (nova busca ou prato próprio): a tela não as oferece de novo. */
  descartaSugestoes?: boolean;
  error?: boolean;
  pending?: boolean;
  startedAt?: number;
  at?: number;
};

export type Conversation = {
  id: string;
  title: string;
  sessionId: string | null;
  messages: ChatMessage[];
  updatedAt: number;
};

export type Online = boolean | null;

export type ItemCarrinho = {
  id: number;
  nome: string;
  quantidade: number;
  unidade: string | null;
  preco_total: number;
  estimado: boolean;
  origem: string;
  motivo: string | null;
  /** Nome do prato a que a linha pertence; null é um ingrediente avulso. */
  prato: string | null;
  /** Já está na despensa: conta no valor do prato, mas não gasta saldo. */
  ja_tem: boolean;
};

export type Orcamento = {
  inicial: number;
  despesas?: number;
  despesa_despensa?: number;
  saldo_gerencial?: number;
  comprometido: number;
  gasto_confirmado?: number;
  reservado_cardapio?: number;
  carrinho: number;
  /** Verba disponível para comprar; use este saldo ao validar uma nova compra. */
  saldo: number;
  estourou: boolean;
};

export type CompraPaga = {
  ingrediente: string;
  quantidade: number;
  unidade: string;
  preco_total: number;
};

export type CompraHistorico = {
  id: number;
  ingrediente: string;
  quantidade: number | null;
  unidade: string | null;
  valor: number;
  registrado_em: string;
  status: "paga" | "comprometida" | "planejada" | "cancelada";
};

export type HistoricoCompras = {
  lotes: {
    id: number;
    prato_id: number;
    receita_id: number | null;
    registrado_em: string | null;
    ativo: boolean;
    status?: "preco_pendente" | "no_cardapio" | "encerrado";
    prato: string;
    porcoes: number | null;
    cmv_total: number | null;
    insumos: {
      ingrediente: string;
      quantidade: string | null;
      custo_uso: number | null;
      origem: "despensa" | "complemento" | "preparo" | "ficha";
      derivado?: string;
    }[];
    complementos: CompraHistorico[];
  }[];
  avulsas: CompraHistorico[];
};

/** Um ingrediente de um prato, com o seu valor. */
export type LinhaPrato = {
  nome: string;
  quantidade: number;
  unidade: string | null;
  preco_total: number;
  ja_tem: boolean;
};

/** Prato ou ingrediente guardado em "Salvos" para voltar ao carrinho depois. */
export type Salvo = {
  id: number;
  tipo: "prato" | "ingrediente";
  nome: string;
  quantidade: number | null;
  unidade: string | null;
  preco_total: number;
  ingredientes: LinhaPrato[];
};

export type Salvos = { pratos: Salvo[]; ingredientes: Salvo[] };

export type Opcao = { id: string; label: string };
export type OpcaoEstado = Opcao & { regiao: string };

/** O negócio: onde a loja fica, que tipo de negócio é e que tipo de alimentação ela faz. Os pratos ficam em Minha cozinha. */
export type Negocio = {
  tipo: string;
  /** Tipo de alimentação que ela faz (ids de opcoes.alimentacao). */
  alimentacao: string[];
  pais: string;
  /** Onde fica a loja: sigla do estado, cidade (texto livre) e zona. Vazio = não informado. */
  estado: string;
  cidade: string;
  zona: string;
  /** Objetivo informado pela pessoa; opcional para perfis já cadastrados. */
  objetivo?: string;
};

// ---- Minha cozinha: o que ela tem, o que sabe fazer e a rotina (o mesmo perfil que a consultora lê e grava).

export type Nivel = "sim" | "aprendendo" | "nao";
export type OpcaoCozinha = { id: string; label: string; dica: string };
export type ValorRotina = string | number;

export type OpcoesCozinha = {
  equipamentos: OpcaoCozinha[];
  utensilios: OpcaoCozinha[];
  habilidades: OpcaoCozinha[];
  niveis: Opcao[];
  bocas: number[];
  rotina: { id: string; label: string; opcoes: { valor: ValorRotina; label: string }[] }[];
};

/** O que a conversa gravou e o catálogo não tem ("massa fresca", observações): a tela mostra e deixa apagar. */
export type Outro = { grupo: "equipamentos" | "tecnicas" | "restricoes" | "observacoes"; chave: string; nome: string; texto: string };

export type Cozinha = {
  /** id do catálogo → tem (true, ou o número de bocas do fogão) ou não tem (false). Ausente = ainda não respondeu. */
  equipamentos: Record<string, boolean | number>;
  habilidades: Record<string, Nivel>;
  rotina: Record<string, ValorRotina>;
  resumo: { total: number; respondidos: number; tem: string[]; nao_tem: string[]; sem_resposta: string[]; sabe: string[]; aprendendo: string[]; nao_sabe: string[] };
  outros: Outro[];
};

export type NovaCozinha = {
  equipamentos?: Record<string, boolean | number | null>;
  habilidades?: Record<string, Nivel | null>;
  rotina?: Record<string, ValorRotina | null>;
  remover?: { grupo: Outro["grupo"]; chave: string }[];
};

/** Um prato da base com o que ele precisa e o que a cozinha dela tem: a lista de pratos de Minha cozinha. */
export type RequisitoPrato = { nome: string; estado: "ok" | "falta" | "confirmar"; com: string };
export type HabilidadePrato = { nome: string; nivel: Nivel | null };
export type PratoDaCozinha = {
  prato: string;
  status: "ok" | "atencao" | "confirmar" | "nao_da";
  precisa: RequisitoPrato[];
  habilidades: HabilidadePrato[];
  mensagem: string;
  custo_tipico: number;
  observacao: string;
};
export type CategoriaDePratos = { nome: string; da_loja: boolean; pratos: PratoDaCozinha[] };
export type PratosDaCozinha = {
  resumo: { total: number; ok: number; atencao: number; confirmar: number; nao_da: number };
  categorias: CategoriaDePratos[];
};

export type IngredienteFaltante = {
  ingrediente: string;
  item_despensa: string | null;
  quantidade_receita: string;
  quantidade_faltante_base: number | null;
  unidade_base: string | null;
  motivo: string;
  estado: "faltando" | "resolvido" | "omitido_na_adaptacao";
  observacao: string;
  registrado_em: string;
};

export type PlanoDivulgacao = {
  criado_em: string;
  prato: { id: number; nome: string; preco_venda: number; lucro_por_porcao: number };
  volume: { porcoes: number; dias: number; origem: "historico" | "informado" | "lote_hipotetico" };
  campanha: {
    objetivo: string;
    canal: string;
    estrategia: string;
    titulo: string;
    descricao: string;
    legenda: string;
    investimento: number;
    lucro_previsto_por_porcao?: number;
    teto_para_margem: number;
    margem_minima: number | null;
    origem_margem: "informada" | "cenario_escolhido" | "sem_meta";
    tarefas: string[];
  };
};

export type PratoEscolhido = {
  id: number;
  nome: string;
  status: "selecionado" | "cmv_calculado" | "aceito";
  receita_id: number | null;
  fonte_url: string | null;
  porcoes: number | null;
  cmv_total: number | null;
  cmv_porcao: number | null;
  preco_venda: number | null;
  compras_estimadas?: number | null;
  custo_da_despensa?: number | null;
  veredito?: string | null;
  bloqueios?: { tipo: string; detalhe: string }[];
  pendencias?: string[];
  analise_preco_salva?: boolean;
  ingredientes_faltantes?: IngredienteFaltante[];
  ingredientes_da_ficha?: IngredienteDaFicha[];
  medidas_de_uso_estimadas?: { ingrediente: string; quantidade: number; unidade: string; base: string }[];
};

export type IngredienteDaFicha = {
  ingrediente: string;
  quantidade: string | null;
  estado: "disponivel" | "faltando" | "a_conferir";
  item_despensa: string | null;
  uso_despensa: string | null;
  falta: string | null;
  derivado: boolean;
  omitido: boolean;
  substitui: string | null;
};

/** Aviso da conferência com a cozinha: não dá (falta algo que ela disse não ter), atenção (habilidade), confirmar (falta responder). */
export type NivelAviso = "nao_da" | "atencao" | "confirmar";
export type AlertaCozinha = { prato: string | null; item_id: number | null; nivel: NivelAviso; mensagens: string[] };

export type MedidaPendente = { rotulo: string; cotacao: string };

export type Estado = {
  geracao_dados?: string | null;
  perfil_precificacao?: PerfilPrecificacao;
  orcamento: Orcamento;
  compras_pagas?: CompraPaga[];
  historico_compras?: HistoricoCompras;
  carrinho: ItemCarrinho[];
  alertas: AlertaCozinha[];
  salvos: Salvos;
  despensa_consultada?: boolean;
  negocio: Negocio | null;
  cozinha: Cozinha;
  cadastro?: { negocio_pendente: boolean; cozinha_pendente: boolean; cozinha_campos: string[] };
  pratos_escolhidos?: PratoEscolhido[];
  vendas_resumo?: ResumoVendas;
  jornada?: {
    etapa: "negocio" | "estoque" | "ajuda" | "proprio" | "pausa" | "pesquisa" | "escolha" | "pendencias" | "aceite" | "preco" | "cardapio" | "revisao" | "divulgacao" | "concluido";
    passo?: PassoJornada;
    concluidas?: Record<PassoJornada, boolean>;
    titulo: string;
    descricao: string;
    acao: string;
    mensagem: string;
    pendencias: (string | { tipo: string; detalhe: string })[];
    pergunta?: { texto: string; opcoes: { rotulo: string; resposta?: string; mensagem: string; acao?: AcaoJornada }[]; medida?: MedidaPendente } | null;
    pergunta_esgotada?: boolean;
    plano_divulgacao?: PlanoDivulgacao;
    controle?: {
      pausado: boolean;
      estoque_confirmado: boolean;
      ajuda: "" | "ia" | "proprio";
      proprio_pendente?: boolean;
      ultimo_prato_ao_escolher?: number;
      rascunhos_adiados?: number[];
      divulgacao: "" | "interesse" | "dispensada";
    };
    estoque?: { nome: string; quantidade: number; unidade: string; custo_unitario: number | null; custo_total: number | null; disponivel: number; reservado: number }[];
    prato_id?: number;
    /** Fichas desta escolha; as demais continuam salvas em Minha cozinha. */
    pratos_ativos?: number[];
    prato_nome?: string;
    pratos?: { id: number; nome: string }[];
  };
  opcoes: { tipos: Opcao[]; alimentacao: Opcao[]; paises: Opcao[]; estados: OpcaoEstado[]; zonas: Opcao[]; cozinha: OpcoesCozinha };
};

export type PassoJornada = "negocio" | "estoque" | "ajuda" | "montagem" | "divulgacao" | "concluido";
export type AcaoJornada = "confirmar_estoque" | "pausar" | "retomar" | "ajuda_ia" | "prato_proprio" | "interesse_divulgacao" | "dispensar_divulgacao";

export type NovoItem = {
  nome: string;
  quantidade: number;
  unidade: string;
  preco_total: number;
};

export type NovoPrato = { prato: string; ingredientes: LinhaPrato[] };

export type NovoSalvo =
  | { tipo: "prato"; nome: string; ingredientes: LinhaPrato[] }
  | { tipo: "ingrediente"; nome: string; quantidade: number; unidade: string; preco_total: number };

/** O que o carrinho oferece à tela: cada ação devolve o texto do erro, ou null quando deu certo. */
export type Acao = Promise<string | null>;

export type CarrinhoAcoes = {
  addItem: (item: NovoItem) => Acao;
  addPrato: (prato: NovoPrato) => Acao;
  setQuantidade: (id: number, quantidade: number) => Acao;
  removeItem: (id: number) => Acao;
  removePrato: (nome: string) => Acao;
  clear: () => Acao;
  saveSalvo: (salvo: NovoSalvo) => Acao;
  removeSalvo: (id: number) => Acao;
  salvoToCart: (id: number) => Acao;
};

// ---- Analytics: regras do desafio e projeções dos pratos.

export type PeriodoVendas = "dia" | "semana" | "mes";
export type ResumoPeriodoVendas = {
  inicio: string;
  fim: string;
  quantidade: number;
  registros: number;
  faturamento_bruto: number;
  taxa_plataforma: number;
  recebe_liquido: number;
  cmv_total: number;
  lucro_estimado: number;
};
export type ResumoVendas = {
  fuso: string;
  referencia: string;
  possui_registros: boolean;
  periodos: Record<PeriodoVendas, ResumoPeriodoVendas>;
};
export type VendaReal = Omit<ResumoPeriodoVendas, "inicio" | "fim" | "registros"> & {
  id: number;
  operacao: string;
  prato_id: number;
  prato: string;
  preco_unitario: number;
  cmv_unitario: number;
  vendido_em: string;
  registrado_em: string;
  status: "confirmada" | "estornada";
  estornado_em: string | null;
};
export type RespostaVendas = {
  periodo: PeriodoVendas;
  inicio: string;
  fim: string;
  fuso: string;
  resumo: ResumoPeriodoVendas;
  vendas: VendaReal[];
  limite: number;
  total_registros: number;
  truncado: boolean;
};
export type NovaVenda = {
  prato_id: number;
  quantidade: number;
  preco_unitario?: number;
  vendido_em?: string;
  operacao: string;
};

export type PlanoPrecificacao = "desafio";

export type PerfilPrecificacao = {
  plano: PlanoPrecificacao;
  comissao: number;
  pagamento: number;
  pagamento_online: boolean;
  imposto: number | null;
};

export type RespostaAnalytics = {
  analises: AnalisePreco[];
  previas: AnalisePreco[];
  pendentes: { prato_id: number; prato: string; motivo: string }[];
  perfil_precificacao: PerfilPrecificacao;
  planos: Record<PlanoPrecificacao, { rotulo: string; comissao: number; pagamento: number }>;
  referencias: { documento: string; secao: string };
};

export type ResultadoPreco = {
  taxa_plataforma: number;
  taxa_comissao: number;
  taxa_pagamento: number;
  impostos: number | null;
  repasse_ifood: number;
  recebe_liquido: number;
  lucro: number;
  margem_sobre_liquido: number | null;
};

export type CenarioPreco = {
  nome: string;
  margem_alvo: number;
  preco_sugerido: number;
  com_preco_sugerido: ResultadoPreco;
  porcoes_para_meta?: number | null;
};

export type AnalisePreco = {
  prato_id: number;
  prato: string;
  status: "previa" | "proposta" | "registrado";
  porcoes: number;
  cmv_total: number;
  cmv_por_porcao: number;
  preco_minimo: number;
  preco_minimo_com_sobra: number;
  taxa_plataforma: number;
  taxa_impostos: number | null;
  cotacao: string;
  cenarios: CenarioPreco[];
  cenario_recomendado: number | null;
  recomendacao: string;
  preco_escolhido: number | null;
  resultado_escolhido: ResultadoPreco | null;
  cmv_estimado: boolean;
  lucro_alvo: number | null;
  custos_fixos: number | null;
  atualizado_em: string;
};
