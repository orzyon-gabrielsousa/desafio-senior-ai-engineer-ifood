const BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

/** 12.5 → "R$ 12,50" (com espaço não separável, para o R$ não ficar sozinho na quebra de linha). */
export function brl(value: number): string {
  return BRL.format(value);
}

/** 2 + "kg" → "2 kg"; 0.25 + "L" → "0,25 L". */
export function quantidade(value: number, unidade: string | null): string {
  const numero = Number.isInteger(value) ? String(value) : value.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
  return unidade ? `${numero} ${unidade}` : numero;
}

/** "12,50", "1.250,00" ou "12.50" → número; texto vazio ou que não é número → NaN. */
export function lerNumero(texto: string): number {
  let limpo = texto.trim().replace(/^R\$\s*/i, "");
  if (limpo.includes(",")) limpo = limpo.replace(/\./g, "").replace(",", ".");
  return limpo === "" ? Number.NaN : Number(limpo);
}

/** Sem acento e em minúsculas, para comparar nomes ("Coxinha" e "coxínha" são o mesmo prato). */
export function chave(texto: string): string {
  return texto.normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim().toLowerCase();
}
