import { useEffect, useRef, useState } from "react";
import type { Conversation, ToolResult } from "../types";
import { getAtividade } from "./api";

const MAX_TENTATIVAS = 8;
// Só passo de agora: o plugin guarda as últimas 400 ações, e conversa carregada do navegador não deve disparar busca.
const JANELA_MS = 5 * 60_000;

/** Passos que já terminaram e ainda não têm a frase do que fizeram (o plugin a grava logo depois do fim da ferramenta). */
export function idsSemResultado(conversations: Conversation[], tentativas: Map<string, number>, agora = Date.now()): string[] {
  const ids: string[] = [];
  for (const conversation of conversations) {
    for (const message of conversation.messages) {
      for (const tool of message.tools) {
        if (
          tool.status !== "running" &&
          !tool.result &&
          !tool.id.startsWith("anon-") &&
          (tentativas.get(tool.id) ?? 0) < MAX_TENTATIVAS &&
          tool.endedAt !== undefined &&
          agora - tool.endedAt < JANELA_MS
        ) {
          ids.push(tool.id);
        }
      }
    }
  }
  return ids;
}

/**
 * Pergunta à API o que cada passo terminado fez (a busca achou quantos sites, o que a despensa tem, quanto custou a conta) e entrega a
 * frase a `aoAchar`. A ferramenta termina antes de o plugin gravar a frase, então tenta de novo com espera crescente; sem a API, o passo
 * segue só com o que o streaming mandou.
 */
export function useAtividade(conversations: Conversation[], aoAchar: (achados: Record<string, ToolResult>) => void): void {
  const tentativas = useRef(new Map<string, number>());
  const entregar = useRef(aoAchar);
  const [rodada, setRodada] = useState(0);
  const chave = idsSemResultado(conversations, tentativas.current).join(",");

  useEffect(() => {
    entregar.current = aoAchar;
  });

  useEffect(() => {
    if (!chave) return;
    const ids = chave.split(",");
    const espera = 300 + Math.min(...ids.map((id) => tentativas.current.get(id) ?? 0)) * 400;
    let cancelado = false;
    const timer = window.setTimeout(async () => {
      let achados: Record<string, ToolResult> = {};
      try {
        achados = (await getAtividade(ids)).atividade ?? {};
      } catch {
        // Sem a API o passo fica com o que o streaming mandou.
      }
      if (cancelado) return;
      for (const id of ids) if (!achados[id]) tentativas.current.set(id, (tentativas.current.get(id) ?? 0) + 1);
      if (Object.keys(achados).length > 0) entregar.current(achados);
      setRodada((n) => n + 1);
    }, espera);
    return () => {
      cancelado = true;
      window.clearTimeout(timer);
    };
  }, [chave, rodada]);
}
