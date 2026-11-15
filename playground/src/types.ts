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

export type Estado = {
  orcamento: Orcamento;
  carrinho: ItemCarrinho[];
  salvos: Salvos;
  negocio: Negocio | null;
  opcoes: { tipos: Opcao[]; vende: Opcao[]; estados: OpcaoEstado[]; zonas: Opcao[] };
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
