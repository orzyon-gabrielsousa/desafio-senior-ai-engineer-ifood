import type { Conversation, ToolResult, ToolStatus, ToolTrace } from "../types";

const LABELS: Record<string, string> = {
  despensa_listar: "Abrindo a despensa",
  despensa_buscar: "Conferindo a despensa",
  despensa_atualizar: "Anotando na despensa",
  despensa_registrar_compras: "Registrando as compras na despensa",
  perfil_cozinha_consultar: "Vendo o que a cozinha tem",
  perfil_cozinha_salvar: "Guardando o que você contou",
  negocio_salvar: "Guardando o seu negócio",
  pratos_selecionar: "Salvando os pratos escolhidos",
  receita_conferir_ingredientes: "Comparando com a despensa e o orçamento",
  prato_calcular_cmv: "Calculando o CMV",
  preco_sugerir: "Montando os preços",
  cardapio_registrar: "Registrando no cardápio",
  cardapio_remover: "Tirando do cardápio",
  cardapio_consultar: "Consultando o cardápio",
  divulgacao_planejar: "Planejando a divulgação do cardápio",
  carrinho_consultar: "Abrindo o carrinho",
  carrinho_adicionar: "Colocando no carrinho",
  carrinho_adicionar_prato: "Abrindo o prato no carrinho",
  carrinho_remover: "Tirando do carrinho",
  carrinho_montar_da_receita: "Montando o carrinho da receita",
  salvos_salvar: "Salvando em Meus pratos",
  analytics_analises_consultar: "Consultando as análises salvas",
  vendas_registrar: "Registrando a venda realizada",
  vendas_consultar: "Consultando as vendas realizadas",
  web_search: "Pesquisando na internet",
  web_extract: "Lendo a receita",
  clarify: "Perguntando a ela",
  memory: "Guardando na memória",
  skill_view: "Abrindo o playbook",
  skills_list: "Listando os playbooks",
  skill_manage: "Organizando o playbook",
  todo: "Anotando o próximo passo",
  todo_list: "Anotando o próximo passo",
  todo_write: "Anotando o próximo passo",
};

export function canonicalToolName(name: string): string {
  const trimmed = name.trim();
  const parts = trimmed.split(/[./]/);
  return parts[parts.length - 1] || trimmed;
}

export function toolLabel(name: string): string {
  const canonical = canonicalToolName(name);
  return LABELS[canonical] ?? canonical.replaceAll("_", " ");
}

export function friendlyPreview(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const text = value.replace(/\s+/g, " ").trim();
  if (!text || text.startsWith("{") || text.startsWith("[") || text.startsWith("<")) return undefined;
  return text.length > 96 ? `${text.slice(0, 93)}…` : text;
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

export function parseToolEvent(raw: unknown, seq: number): ToolTrace | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;
  const nested = record.tool && typeof record.tool === "object" ? (record.tool as Record<string, unknown>) : null;
  const flat = nested ? { ...record, ...nested } : record;
  const name = canonicalToolName(
    asString(typeof flat.tool === "string" ? flat.name ?? flat.tool : flat.name ?? flat.tool_name ?? flat.function) ??
      "",
  );
  if (!name) return null;

  const statusRaw = (asString(flat.status ?? flat.state ?? flat.phase) ?? "").toLowerCase();
  const failed =
    statusRaw.includes("fail") || statusRaw.includes("error") || flat.error === true || flat.is_error === true;
  const done =
    !failed &&
    (statusRaw.includes("complete") ||
      statusRaw.includes("done") ||
      statusRaw === "success" ||
      statusRaw === "finished");
  const status: ToolStatus = failed ? "error" : done ? "done" : "running";
  const explicit = asString(flat.toolCallId ?? flat.tool_call_id ?? flat.call_id ?? (nested ? undefined : flat.id));

  const now = Date.now();
  return {
    id: explicit || `anon-${seq}-${name}-${status}`,
    name,
    label: toolLabel(name),
    status,
    preview: friendlyPreview(flat.preview ?? flat.output ?? flat.delta ?? flat.arguments),
    detail: detailFrom(name, asString(flat.label)),
    startedAt: status === "running" ? now : undefined,
    endedAt: status === "running" ? undefined : now,
  };
}

function hostOf(text: string): string | undefined {
  try {
    return new URL(text).hostname.replace(/^www\./, "");
  } catch {
    return undefined;
  }
}

/** O Hermes manda em `label` o argumento principal da ferramenta (a busca digitada, o endereço lido). Quando é só o nome da ferramenta, não diz nada. */
function detailFrom(name: string, label: string | undefined): string | undefined {
  if (!label || label === name) return undefined;
  const text = label.replace(/\s+/g, " ").trim();
  return name === "web_extract" ? (hostOf(text) ?? friendlyPreview(text)) : friendlyPreview(text);
}

function mergeTool(old: ToolTrace, incoming: ToolTrace): ToolTrace {
  return {
    ...old,
    status: incoming.status,
    label: incoming.label,
    preview: incoming.preview ?? old.preview,
    detail: incoming.detail ?? old.detail,
    startedAt: old.startedAt ?? incoming.startedAt,
    endedAt: incoming.status === "running" ? old.endedAt : (incoming.endedAt ?? old.endedAt),
  };
}

export function applyTool(tools: ToolTrace[], incoming: ToolTrace): ToolTrace[] {
  const byId = tools.findIndex((tool) => tool.id === incoming.id);
  if (byId >= 0) return tools.map((tool, index) => (index === byId ? mergeTool(tool, incoming) : tool));
  if (incoming.status !== "running") {
    for (let index = tools.length - 1; index >= 0; index -= 1) {
      const tool = tools[index];
      if (tool.name === incoming.name && tool.status === "running") {
        return tools.map((item, itemIndex) => (itemIndex === index ? mergeTool(item, incoming) : item));
      }
    }
  }
  return [...tools, incoming];
}

export function settleTools(tools: ToolTrace[], status: ToolStatus): ToolTrace[] {
  const now = Date.now();
  return tools.map((tool) => (tool.status === "running" ? { ...tool, status, endedAt: tool.endedAt ?? now } : tool));
}

/** O texto que o plugin põe no começo do resumo quando uma trava (cozinha incompleta, saldo) recusou o pedido: não é falha, é a regra trabalhando. */
export const PREFIXO_TRAVA = "Travado: ";

/** Junta aos passos da conversa o que cada ferramenta fez (a frase que o plugin guardou). Devolve o mesmo array se nada mudou. */
export function applyResults(conversations: Conversation[], found: Record<string, ToolResult>): Conversation[] {
  let changed = false;
  const next = conversations.map((conversation) => {
    let touched = false;
    const messages = conversation.messages.map((message) => {
      if (!message.tools.some((tool) => found[tool.id] && !tool.result)) return message;
      touched = true;
      return {
        ...message,
        tools: message.tools.map((tool) => (found[tool.id] && !tool.result ? { ...tool, result: found[tool.id] } : tool)),
      };
    });
    if (!touched) return conversation;
    changed = true;
    return { ...conversation, messages };
  });
  return changed ? next : conversations;
}

export type StepView = {
  id: string;
  status: ToolStatus;
  tone: "running" | "done" | "error" | "locked";
  title: string;
  detail?: string;
  summary?: string;
  items: string[];
  ms?: number;
};

/** O que mostrar de um passo: enquanto roda, o verbo no gerúndio e o que está sendo feito; depois, o que ela fez e achou. */
export function describeStep(tool: ToolTrace): StepView {
  const result = tool.result;
  const locked = result?.ok !== false && !!result?.resumo.startsWith(PREFIXO_TRAVA);
  const failed = tool.status === "error" || result?.ok === false;
  let title = result?.titulo ?? tool.label;
  if (tool.status === "running") {
    title = tool.label;
  } else if (failed || locked) {
    if (tool.name === "despensa_atualizar") title = "Não atualizou a despensa";
    else if (tool.name === "despensa_registrar_compras") title = "Compra não registrada";
    else title = failed ? "Não concluiu a etapa" : "Etapa pendente";
  }
  const ms = result?.duracao_ms ?? (tool.startedAt && tool.endedAt ? tool.endedAt - tool.startedAt : undefined);
  return {
    id: tool.id,
    status: tool.status,
    tone: tool.status === "running" ? "running" : failed ? "error" : locked ? "locked" : "done",
    title,
    detail: result?.entrada || tool.detail || tool.preview,
    summary: result?.resumo || undefined,
    items: result?.itens ?? [],
    ms: tool.status === "running" ? undefined : ms,
  };
}
