import type { Analytics, Estado, Negocio, NovoItem, NovoPrato, NovoSalvo, Visao } from "../types";

// Carrinho, pratos salvos, perfil do negócio e Analytics moram no plugin; o nginx leva /api/ até o serviço `api`.

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

async function chamar<T = Estado>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      cache: "no-store",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Não consegui falar com o carrinho. Confere se o make up subiu a API.", 0);
  }
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(erroDe(data, response.status), response.status);
  return data as T;
}

export const getEstado = () => chamar("GET", "/api/estado");
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
export const getAnalytics = (visao: Visao, limite = 30) =>
  chamar<Analytics>("GET", `/api/analytics?escopo=${visao}&limite=${limite}`);
