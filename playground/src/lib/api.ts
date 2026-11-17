import type { Conversation, Estado, Negocio, NovaCozinha, NovoItem, NovoPrato, NovoSalvo, PerfilPrecificacao, PratosDaCozinha, RespostaAnalytics, ToolResult } from "../types";

// Carrinho, pratos salvos, perfil do negócio, Minha cozinha, Analytics e o passo a passo da consultora moram no plugin; o nginx leva /api/ até o serviço `api`.

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function erroDe(data: unknown, status: number): string {
  if (data && typeof data === "object" && "erro" in data) {
    const erro = (data as { erro: unknown }).erro;
    if (typeof erro === "string" && erro) return erro;
  }
  return `O carrinho respondeu ${status}.`;
}

async function chamar<T = Estado>(method: string, path: string, body?: unknown, opts: Pick<RequestInit, "signal" | "keepalive"> = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...opts,
      method,
      cache: "no-store",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Não consegui falar com o carrinho. Confere se o make run está rodando.", 0);
  }
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(erroDe(data, response.status), response.status);
  return data as T;
}

export const getEstado = () => chamar("GET", "/api/estado");
const historyTimeout = () => AbortSignal.timeout(8_000);
export const getConversas = () => chamar<{ geracao_dados: string | null; conversas: Conversation[] }>("GET", "/api/conversas", undefined, { signal: historyTimeout() });
export const gravarConversa = (conversa: Conversation, geracao_dados: string | null, keepalive = false) =>
  chamar<{ conversa: Conversation }>("PUT", `/api/conversas/${encodeURIComponent(conversa.id)}`, { geracao_dados, conversa }, { keepalive, signal: keepalive ? undefined : historyTimeout() });
export const apagarConversa = (id: string, geracao_dados: string | null) =>
  chamar<{ ok: boolean }>("DELETE", `/api/conversas/${encodeURIComponent(id)}`, { geracao_dados }, { signal: historyTimeout() });
export type ResumoMemoria = { conversas_salvas: number; arquivos_contexto: number; vetores_indexados: number; sessoes_indexadas: number };
export const getMemoria = (signal?: AbortSignal) => chamar<ResumoMemoria>("GET", "/api/memoria", undefined, { signal });
export type ItemDespensa = { nome: string; disponivel: string; preco_pago: number | null };
export const conferirDespensa = () => chamar<{ itens: ItemDespensa[]; estado: Estado }>("POST", "/api/despensa/conferir");
export const addItem = (item: NovoItem) => chamar("POST", "/api/carrinho/itens", item);
export const setQuantidade = (id: number, quantidade: number) => chamar("PATCH", `/api/carrinho/itens/${id}`, { quantidade });
export const removeItem = (id: number) => chamar("DELETE", `/api/carrinho/itens/${id}`);
export const clearCart = () => chamar("DELETE", "/api/carrinho");
export const addPrato = (prato: NovoPrato) => chamar("POST", "/api/carrinho/pratos", prato);
export const removePrato = (nome: string) => chamar("DELETE", `/api/carrinho/pratos/${encodeURIComponent(nome)}`);
export const saveSalvo = (salvo: NovoSalvo) => chamar("POST", "/api/salvos", salvo);
export const removeSalvo = (id: number) => chamar("DELETE", `/api/salvos/${id}`);
export const salvoToCart = (id: number) => chamar("POST", `/api/salvos/${id}/carrinho`);
export const saveNegocio = (negocio: Negocio) => chamar("PUT", "/api/negocio", negocio);
export const updateNegocio = (campos: Partial<Negocio>) => chamar("PATCH", "/api/negocio", campos);
export const saveCozinha = (cozinha: NovaCozinha) => chamar("PUT", "/api/cozinha", cozinha);
/** Cada prato da base com o que precisa e o que a cozinha dela tem. */
export const getPratosDaCozinha = () => chamar<PratosDaCozinha>("GET", "/api/cozinha/pratos");
export const getAnalises = () => chamar<RespostaAnalytics>("GET", "/api/analytics/analises");
export const saveTaxas = (perfil: PerfilPrecificacao) => chamar<RespostaAnalytics>("PUT", "/api/analytics/taxas", perfil);
/** O que cada chamada de ferramenta da consultora fez (o plugin guarda uma frase por chamada, com o mesmo id do streaming). */
export const getAtividade = (ids: string[]) =>
  chamar<{ atividade: Record<string, ToolResult> }>("GET", `/api/atividade?ids=${ids.map(encodeURIComponent).join(",")}`);
