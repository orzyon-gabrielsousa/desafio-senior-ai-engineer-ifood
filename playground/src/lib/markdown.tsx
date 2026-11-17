import type { ReactNode } from "react";

type Block =
  | { type: "p"; lines: string[] }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[] }
  | { type: "pre"; text: string }
  | { type: "table"; headers: string[]; rows: string[][] }
  | { type: "h"; level: number; text: string };

const INLINE = /(\*\*[^*]+?\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g;
const HEADING = /^(#{1,6})\s+(.*)$/;

function inline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = new RegExp(INLINE.source, "g");
  let last = 0;
  let index = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text))) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${index}`;
    index += 1;
    if (token.startsWith("**")) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else {
      const label = token.slice(1, token.indexOf("]"));
      const href = token.slice(token.indexOf("(") + 1, -1);
      nodes.push(
        <a key={key} href={href} target="_blank" rel="noreferrer noopener">
          {label}
        </a>,
      );
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function tableCells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "")
    .split(/(?<!\\)\|/).map((cell) => cell.trim().replaceAll("\\|", "|"));
}

function tableHeaders(lines: string[], index: number): string[] | undefined {
  if (!lines[index].includes("|") || !lines[index + 1]?.includes("|")) return;
  const headers = tableCells(lines[index]);
  const separators = tableCells(lines[index + 1]);
  if (headers.length === separators.length && separators.every((cell) => /^:?-{3,}:?$/.test(cell))) {
    return headers;
  }
}

function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const buffer: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith("```")) {
        buffer.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      blocks.push({ type: "pre", text: buffer.join("\n") });
      continue;
    }
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ type: "h", level: heading[1].length, text: heading[2] });
      i += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i += 1;
      }
      blocks.push({ type: "ul", items });
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
        i += 1;
      }
      blocks.push({ type: "ol", items });
      continue;
    }
    const headers = tableHeaders(lines, i);
    if (headers) {
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && !HEADING.test(lines[i]) && !lines[i].startsWith("```")) {
        rows.push(tableCells(lines[i]));
        i += 1;
      }
      blocks.push({ type: "table", headers, rows });
      continue;
    }
    const paragraph: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].startsWith("```") &&
      !HEADING.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+[.)]\s+/.test(lines[i]) &&
      !tableHeaders(lines, i)
    ) {
      paragraph.push(lines[i]);
      i += 1;
    }
    blocks.push({ type: "p", lines: paragraph });
  }
  return blocks;
}

function renderBlock(block: Block, index: number): ReactNode {
  const key = `b${index}`;
  if (block.type === "pre") {
    return (
      <pre key={key}>
        <code>{block.text}</code>
      </pre>
    );
  }
  if (block.type === "h") {
    const Heading = (["h2", "h3", "h4", "h5", "h6", "h6"] as const)[block.level - 1];
    return <Heading key={key}>{inline(block.text, key)}</Heading>;
  }
  if (block.type === "table") {
    return (
      <div className="md-table" key={key} role="region" aria-label="Tabela da conversa" tabIndex={0}>
        <table>
          <thead>
            <tr>
              {block.headers.map((cell, column) => <th key={column} scope="col">{inline(cell, `${key}-header-${column}`)}</th>)}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, index) => (
              <tr key={index}>
                {block.headers.map((_, column) => <td key={column}>{inline(row[column] ?? "", `${key}-${index}-${column}`)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (block.type === "ul" || block.type === "ol") {
    const List = block.type === "ul" ? "ul" : "ol";
    return (
      <List key={key}>
        {block.items.map((item, itemIndex) => (
          <li key={`${key}-${itemIndex}`}>{inline(item, `${key}-${itemIndex}`)}</li>
        ))}
      </List>
    );
  }
  return (
    <p key={key}>
      {block.lines.map((line, lineIndex) => (
        <span key={`${key}-${lineIndex}`}>
          {lineIndex > 0 ? <br /> : null}
          {inline(line, `${key}-${lineIndex}`)}
        </span>
      ))}
    </p>
  );
}

export function Markdown({ text }: { text: string }) {
  const blocks = parseBlocks(text);
  if (blocks.length === 0) return null;
  return <div className="md">{blocks.map(renderBlock)}</div>;
}
