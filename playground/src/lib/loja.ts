import type { Estado, ItemCarrinho } from "../types";

export type GrupoPrato = {
  nome: string;
  linhas: ItemCarrinho[];
  /** Valor do prato inteiro: tudo o que está nele, inclusive o que ela já tem. */
  valor: number;
  /** O que ela ainda compra (as linhas "já tem" não gastam saldo). */
  aComprar: number;
};

/** Separa o carrinho em pratos abertos (cada um com as suas linhas) e ingredientes avulsos, na ordem em que entraram. */
export function agrupar(itens: ItemCarrinho[]): { pratos: GrupoPrato[]; avulsos: ItemCarrinho[] } {
  const pratos = new Map<string, GrupoPrato>();
  const avulsos: ItemCarrinho[] = [];
  for (const item of itens) {
    if (!item.prato) {
      avulsos.push(item);
      continue;
    }
    const grupo = pratos.get(item.prato) ?? { nome: item.prato, linhas: [], valor: 0, aComprar: 0 };
    grupo.linhas.push(item);
    grupo.valor += item.preco_total;
    if (!item.ja_tem) grupo.aComprar += item.preco_total;
    pratos.set(item.prato, grupo);
  }
  return { pratos: [...pratos.values()], avulsos };
}

/** Quantas coisas há no carrinho: um prato aberto conta uma vez, não uma por ingrediente. */
export function contarItens(itens: ItemCarrinho[]): number {
  const { pratos, avulsos } = agrupar(itens);
  return pratos.length + avulsos.length;
}

/** "Zona Sul, São Paulo" a partir do perfil salvo; vazio enquanto a loja não tem localização. */
export function localCurto(estado: Estado | null): string {
  const negocio = estado?.negocio;
  if (!estado || !negocio?.estado) return "";
  const zona = estado.opcoes.zonas.find((opcao) => opcao.id === negocio.zona)?.label;
  const lugar = negocio.cidade || estado.opcoes.estados.find((opcao) => opcao.id === negocio.estado)?.label || negocio.estado;
  return [zona, lugar].filter(Boolean).join(", ");
}
