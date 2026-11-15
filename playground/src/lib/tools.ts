import type { ToolStatus, ToolTrace } from "../types";

const LABELS: Record<string, string> = {
  despensa_listar: "Abrindo a despensa",
  despensa_buscar: "Procurando na despensa",
  despensa_atualizar: "Anotando na despensa",
  perfil_cozinha_consultar: "Vendo o que a cozinha tem",
  perfil_cozinha_salvar: "Guardando o que você contou",
  receita_conferir_ingredientes: "Conferindo os ingredientes",
  prato_calcular_cmv: "Calculando o CMV",
  preco_sugerir: "Montando os preços",
  cardapio_registrar: "Registrando no cardápio",
  cardapio_remover: "Tirando do cardápio",
  cardapio_consultar: "Consultando o cardápio",
  carrinho_consultar: "Abrindo o carrinho",
  carrinho_adicionar: "Colocando no carrinho",
  carrinho_adicionar_prato: "Abrindo o prato no carrinho",
  carrinho_remover: "Tirando do carrinho",
  carrinho_montar_da_receita: "Montando o carrinho da receita",
  salvos_salvar: "Salvando em Meus pratos",
  analytics_consultar: "Vendo os mais vendidos",
  web_search: "Pesquisando receitas",
  web_extract: "Lendo a receita",
  memory: "Guardando na memória",
  skill_view: "Abrindo o playbook",
  skills_list: "Listando os playbooks",
  skill_manage: "Organizando o playbook",
  todo: "Anotando o próximo passo",
  todo_list: "Anotando o próximo passo",
  todo_write: "Anotando o próximo passo",
};

export type Stage = {
  id: string;
  title: string;
  hint: string;
  test: (name: string) => boolean;
};

export const STAGES: Stage[] = [
  {
    id: "despensa",
    title: "Despensa",
    hint: "o que ela tem e quanto pagou",
    test: (name) => name.startsWith("despensa_"),
  },
  {
    id: "mais-vendidos",
    title: "Mais vendidos",
    hint: "o que vende na sua zona e cidade",
    test: (name) => name === "analytics_consultar",
  },
  {
    id: "receitas",
    title: "Receitas",
    hint: "pesquisa na web e leitura da página",
    test: (name) => name.startsWith("web_"),
  },
  {
    id: "cozinha",
    title: "Cozinha",
    hint: "equipamentos, técnicas e tempo",
    test: (name) => name.startsWith("perfil_"),
  },
  {
    id: "ingredientes",
    title: "Ingredientes",
    hint: "o que tem, o que falta e se cabe nos R$ 80",
    test: (name) => name === "receita_conferir_ingredientes",
  },
  {
    id: "carrinho",
    title: "Carrinho",
    hint: "o prato aberto, ingrediente por ingrediente, dentro do saldo",
    test: (name) => name.startsWith("carrinho_") || name === "salvos_salvar",
  },
  {
    id: "preco",
    title: "CMV e preço",
    hint: "conta aberta — ela escolhe",
    test: (name) => name === "prato_calcular_cmv" || name === "preco_sugerir",
  },
  {
    id: "cardapio",
    title: "Cardápio",
    hint: "o registro reserva estoque e orçamento",
    test: (name) => name.startsWith("cardapio_"),
  },
];

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

  return {
    id: explicit || `anon-${seq}-${name}-${status}`,
    name,
    label: toolLabel(name),
    status,
    preview: friendlyPreview(flat.preview ?? flat.output ?? flat.delta ?? flat.arguments),
  };
}

export function applyTool(tools: ToolTrace[], incoming: ToolTrace): ToolTrace[] {
  const byId = tools.findIndex((tool) => tool.id === incoming.id);
  if (byId >= 0) {
    return tools.map((tool, index) =>
      index === byId
        ? { ...tool, status: incoming.status, label: incoming.label, preview: incoming.preview ?? tool.preview }
        : tool,
    );
  }
  if (incoming.status !== "running") {
    for (let index = tools.length - 1; index >= 0; index -= 1) {
      const tool = tools[index];
      if (tool.name === incoming.name && tool.status === "running") {
        return tools.map((item, itemIndex) =>
          itemIndex === index
            ? { ...item, status: incoming.status, preview: incoming.preview ?? item.preview }
            : item,
        );
      }
    }
  }
  return [...tools, incoming];
}

export function settleTools(tools: ToolTrace[], status: ToolStatus): ToolTrace[] {
  return tools.map((tool) => (tool.status === "running" ? { ...tool, status } : tool));
}

export function stageStatus(names: string[], runningNames: string[], stage: Stage): "idle" | "now" | "done" {
  if (runningNames.some(stage.test)) return "now";
  if (names.some(stage.test)) return "done";
  return "idle";
}
