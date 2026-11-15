export type Role = "user" | "assistant";

export type ToolStatus = "running" | "done" | "error";

export type ToolTrace = {
  id: string;
  name: string;
  label: string;
  status: ToolStatus;
  preview?: string;
};

export type ChatMessage = {
  id: string;
  role: Role;
  content: string;
  tools: ToolTrace[];
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
  comprometido: number;
  carrinho: number;
  saldo: number;
  estourou: boolean;
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

export type Negocio = {
  tipo: string;
  vende: string[];
  publico: string;
  observacoes: string;
  /** Onde fica a loja: sigla do estado, cidade (texto livre) e zona. Vazio = não informado. */
  estado: string;
  cidade: string;
  zona: string;
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
  equipamentos?: Record<string, boolean | number>;
  habilidades?: Record<string, Nivel | null>;
  rotina?: Record<string, ValorRotina | null>;
  remover?: { grupo: Outro["grupo"]; chave: string }[];
};

/** Aviso da conferência com a cozinha: não dá (falta algo que ela disse não ter), atenção (habilidade), confirmar (falta responder). */
export type NivelAviso = "nao_da" | "atencao" | "confirmar";
export type AlertaCozinha = { prato: string | null; item_id: number | null; nivel: NivelAviso; mensagens: string[] };

export type Estado = {
  orcamento: Orcamento;
  carrinho: ItemCarrinho[];
  alertas: AlertaCozinha[];
  salvos: Salvos;
  negocio: Negocio | null;
  cozinha: Cozinha;
  opcoes: { tipos: Opcao[]; vende: Opcao[]; estados: OpcaoEstado[]; zonas: Opcao[]; cozinha: OpcoesCozinha };
};

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

// ---- Analytics: pratos mais vendidos, priorizados pelo lugar da loja.

export type EscopoId = "zona" | "cidade" | "estado" | "regiao" | "pais";
export type Visao = "prioridade" | EscopoId;

export type Local = {
  pais: string;
  regiao: string;
  estado: string;
  estado_nome: string;
  cidade: string;
  zona: string;
};

export type EscopoDisponivel = { escopo: EscopoId; rotulo: string; pratos: number; onde: string };

export type MaisVendido = {
  posicao: number;
  prato: string;
  categoria: string;
  preco_medio: number;
  faixa: string;
  ingredientes: string;
  /** Visão por escopo: índice de vendas (0–100) naquele escopo. */
  indice?: number;
  /** Visão "para a sua loja": prioridade (0–100) e a posição em cada escopo em que o prato aparece. */
  prioridade?: number;
  onde?: { escopo: EscopoId; posicao: number; indice: number }[];
  /** O prato conferido com a cozinha dela (ficha em prompts/base-de-conhecimento/cozinha-e-equipamentos). */
  cozinha?: ConferenciaCozinha;
  /** Quanto cobrar para lucrar depois da taxa (custo típico de exemplo). */
  preco?: QuantoCobrar | null;
};

export type StatusCozinha = NivelAviso | "ok" | "sem_ficha";
export type ConferenciaCozinha = {
  prato: string | null;
  ficha: boolean;
  status: StatusCozinha;
  falta: { origem: string; de: string; precisa: string; para: string }[];
  mensagens: string[];
  observacao: string;
};

export type Cenario = { nome: string; margem: number; preco: number; recebe: number; lucro: number };
export type Mercado = { preco: number; taxa_plataforma: number; recebe_liquido: number; cmv: number; lucro: number; viavel: boolean };
export type QuantoCobrar = {
  cmv: number;
  taxa: number;
  /** O menor preço sem prejuízo: custo ÷ 0,90, arredondado para cima ao centavo. */
  preco_minimo: number;
  cenarios: Cenario[];
  mercado: Mercado | null;
  custo_exemplo?: boolean;
  aviso?: string;
};

export type Analytics = {
  local: Local;
  local_definido: boolean;
  escopo: Visao;
  escopos_disponiveis: EscopoDisponivel[];
  aviso: string;
  fonte: string;
  ranking: MaisVendido[];
  leitura?: string;
  mensagem?: string;
};
