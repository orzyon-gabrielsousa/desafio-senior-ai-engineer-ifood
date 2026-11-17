import { useCallback, useEffect, useRef, useState } from "react";
import type { Conversation } from "../types";
import { ApiError, apagarConversa, getConversas, gravarConversa } from "./api";
import {
  asConversation, clearHistoryChange, HISTORY_GENERATION_KEY, historyChangedId, historyMigrated,
  loadHistoryGeneration, loadHistoryQueue, markHistoryMigrated, MAX_CONVERSATIONS,
  saveConversations, saveHistoryChange, setHistoryGeneration, type HistoryChange,
} from "./storage";

export type EstadoHistorico = "carregando" | "salvando" | "salvo" | "local" | "parcial";

function porData(conversas: Iterable<Conversation>): Conversation[] {
  return [...conversas].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_CONVERSATIONS);
}

function copiaPersistente(conversa: Conversation): Conversation {
  return {
    ...conversa,
    messages: conversa.messages.filter((message) => !message.pending).map((message) => ({
      ...message,
      pending: false,
      tools: message.tools.map((tool) => tool.status === "running" ? { ...tool, status: "error" } : tool),
    })),
  };
}

/** Fila por conversa, para editar o histórico sem regravar o restante da lista. */
export function useHistorico(initial: Conversation[], onReset: () => void, onLoaded: (conversas: Conversation[]) => void) {
  const [conversas, setConversas] = useState(initial);
  const [status, setStatus] = useState<EstadoHistorico>("carregando");
  const atuais = useRef(initial);
  const geracao = useRef(loadHistoryGeneration());
  const pendentes = useRef<Map<string, HistoryChange> | null>(null);
  if (!pendentes.current) {
    pendentes.current = new Map(loadHistoryQueue(geracao.current).map((change) => [historyChangedId(change), change]));
  }
  const fila = pendentes.current;
  const sessoesSalvas = useRef(new Map<string, string | null>());
  const recusadas = useRef(new Set<string>());
  const mounted = useRef(false);
  const running = useRef(false);
  const reload = useRef(true);
  const wait = useRef<number | undefined>(undefined);
  const failures = useRef(0);
  const loaded = useRef(false);

  const publicar = useCallback((lista: Conversation[]) => {
    atuais.current = lista;
    setConversas(lista);
  }, []);

  const reset = useCallback((generation: string | null) => {
    if (geracao.current === generation) return;
    geracao.current = generation;
    setHistoryGeneration(generation);
    fila.clear();
    sessoesSalvas.current.clear();
    recusadas.current.clear();
    loaded.current = false;
    reload.current = true;
    publicar([]);
    onReset();
  }, [fila, onReset, publicar]);

  const enfileirar = useCallback((change: HistoryChange) => {
    const id = historyChangedId(change);
    if (change.kind === "put" && fila.get(id)?.kind === "delete") return;
    fila.set(id, change);
    recusadas.current.delete(id);
    saveHistoryChange(geracao.current, change);
  }, [fila]);

  const sincronizar = useCallback(async function sync() {
    if (!mounted.current || running.current) return;
    running.current = true;
    window.clearTimeout(wait.current);
    wait.current = undefined;
    setStatus((anterior) => anterior === "carregando" ? anterior : "salvando");
    try {
      if (reload.current) {
        const antes = geracao.current;
        const resposta = await getConversas();
        if (!mounted.current || antes !== geracao.current) return;
        if (!Array.isArray(resposta.conversas)) throw new Error("Histórico indisponível");
        reset(resposta.geracao_dados);
        const servidor = new Map<string, Conversation>();
        for (const item of resposta.conversas) {
          const conversa = asConversation(item);
          if (conversa) servidor.set(conversa.id, conversa);
        }
        sessoesSalvas.current = new Map([...servidor.values()].map(({ id, sessionId }) => [id, sessionId]));
        if (!historyMigrated(geracao.current)) {
          // Só migra as conversas desta mesma geração, uma vez por navegador.
          for (const conversa of atuais.current) {
            if (!servidor.has(conversa.id) && !fila.has(conversa.id)) {
              enfileirar({ kind: "put", conversa: copiaPersistente(conversa) });
            }
          }
          markHistoryMigrated(geracao.current);
        }
        for (const change of fila.values()) {
          if (change.kind === "delete") servidor.delete(change.id);
          else servidor.set(change.conversa.id, change.conversa);
        }
        for (const conversa of atuais.current) {
          if (conversa.messages.some((message) => message.pending) && fila.get(conversa.id)?.kind !== "delete") {
            servidor.set(conversa.id, conversa);
          }
        }
        const lista = porData(servidor.values());
        publicar(lista);
        reload.current = false;
        if (!loaded.current) {
          loaded.current = true;
          onLoaded(lista);
        }
      }

      for (const [id, change] of fila) {
        if (recusadas.current.has(id)) continue;
        const antes = geracao.current;
        let resposta: { conversa: Conversation } | undefined;
        try {
          if (change.kind === "delete") {
            if (change.checkpoint && sessoesSalvas.current.get(id) !== change.checkpoint.sessionId) {
              // Uma sessão criada enquanto a API estava fora ainda precisa ser associada ao histórico antes da exclusão.
              await gravarConversa(change.checkpoint, antes);
            }
            await apagarConversa(id, antes);
            sessoesSalvas.current.delete(id);
          } else {
            resposta = await gravarConversa(change.conversa, antes);
            if (antes === geracao.current) sessoesSalvas.current.set(id, resposta.conversa?.sessionId ?? change.conversa.sessionId);
          }
        } catch (error) {
          if (antes !== geracao.current) return;
          if (error instanceof ApiError && error.status === 409) reload.current = true;
          if (error instanceof ApiError && [400, 413, 422].includes(error.status)) {
            if (fila.get(id) === change) recusadas.current.add(id);
            continue;
          }
          if (!(error instanceof ApiError && error.status === 410)) throw error;
          // O servidor já registrou essa exclusão, inclusive para cópias antigas do navegador.
          publicar(atuais.current.filter((conversa) => conversa.id !== id));
        }
        if (!mounted.current || antes !== geracao.current) return;
        if (fila.get(id) !== change) continue;
        fila.delete(id);
        clearHistoryChange(antes, change);
        const salva = asConversation(resposta?.conversa);
        if (salva) {
          publicar(atuais.current.map((conversa) => conversa.id === id && !conversa.messages.some((m) => m.pending)
            && conversa.updatedAt <= salva.updatedAt ? salva : conversa));
        }
      }
      failures.current = 0;
      setStatus(recusadas.current.size > 0 ? "parcial" : "salvo");
    } catch {
      if (!mounted.current) return;
      setStatus("local");
      const atraso = Math.min(2 ** failures.current++ * 1_000, 30_000);
      wait.current = window.setTimeout(() => void sync(), atraso);
    } finally {
      running.current = false;
      const temEnvio = [...fila.keys()].some((id) => !recusadas.current.has(id));
      if (mounted.current && wait.current === undefined && (reload.current || temEnvio)) {
        wait.current = window.setTimeout(() => void sync(), 0);
      }
    }
  }, [enfileirar, fila, onLoaded, publicar, reset]);

  const adotarGeracao = useCallback((generation: string | null) => {
    reset(generation);
    if (reload.current) void sincronizar();
  }, [reset, sincronizar]);

  const editar = useCallback((atualizar: (lista: Conversation[]) => Conversation[], gravarIds?: string | string[]) => {
    const lista = atualizar(atuais.current);
    if (lista !== atuais.current) publicar(lista);
    const ids = typeof gravarIds === "string" ? [gravarIds] : gravarIds ?? [];
    for (const id of ids) {
      const conversa = lista.find((item) => item.id === id);
      if (conversa) enfileirar({ kind: "put", conversa: copiaPersistente(conversa) });
    }
    if (ids.length) void sincronizar();
  }, [enfileirar, publicar, sincronizar]);

  const apagar = useCallback((id: string) => {
    const conversa = atuais.current.find((item) => item.id === id);
    // Basta associar a sessão à exclusão. Nenhuma mensagem precisa ser gravada nessa etapa.
    const checkpoint = conversa?.sessionId && sessoesSalvas.current.get(id) !== conversa.sessionId
      ? { ...conversa, messages: [] } : undefined;
    enfileirar({ kind: "delete", id, checkpoint });
    publicar(atuais.current.filter((conversa) => conversa.id !== id));
    void sincronizar();
  }, [enfileirar, publicar, sincronizar]);

  useEffect(() => {
    const timer = window.setTimeout(() => saveConversations(atuais.current, geracao.current), 400);
    return () => window.clearTimeout(timer);
  }, [conversas]);

  useEffect(() => {
    mounted.current = true;
    void sincronizar();
    const aoVoltar = () => {
      if (document.visibilityState !== "visible") return;
      reload.current = true;
      void sincronizar();
    };
    const outraAba = (event: StorageEvent) => {
      if (event.key === HISTORY_GENERATION_KEY) adotarGeracao(event.newValue);
    };
    const flush = () => {
      saveConversations(atuais.current, geracao.current);
      const emAndamento = atuais.current.find((c) => c.messages.some((message) => message.pending));
      if (!emAndamento) return;
      const conversa = copiaPersistente(emAndamento);
      enfileirar({ kind: "put", conversa });
      if (new TextEncoder().encode(JSON.stringify(conversa)).length < 60_000) {
        void gravarConversa(conversa, geracao.current, true).catch(() => {});
      }
    };
    window.addEventListener("pagehide", flush);
    window.addEventListener("storage", outraAba);
    window.addEventListener("online", aoVoltar);
    document.addEventListener("visibilitychange", aoVoltar);
    return () => {
      mounted.current = false;
      window.clearTimeout(wait.current);
      window.removeEventListener("pagehide", flush);
      window.removeEventListener("storage", outraAba);
      window.removeEventListener("online", aoVoltar);
      document.removeEventListener("visibilitychange", aoVoltar);
    };
  }, [adotarGeracao, enfileirar, sincronizar]);

  return { conversas, atuais, geracao, status, adotarGeracao, editar, apagar };
}
