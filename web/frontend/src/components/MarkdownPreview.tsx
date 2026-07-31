import type { ReactNode } from "react";

type Block =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; items: string[] }
  | { type: "code"; text: string };

function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];
  let code: string[] | null = null;

  const flush = () => {
    if (paragraph.length) {
      blocks.push({ type: "paragraph", text: paragraph.join(" ") });
      paragraph = [];
    }
    if (list.length) {
      blocks.push({ type: "list", items: list });
      list = [];
    }
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (code) {
        blocks.push({ type: "code", text: code.join("\n") });
        code = null;
      } else {
        flush();
        code = [];
      }
      continue;
    }
    if (code) {
      code.push(line);
      continue;
    }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    if (heading) {
      flush();
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      continue;
    }
    const listItem = /^\s*[-*+]\s+(.+)$/.exec(line);
    if (listItem) {
      if (paragraph.length) flush();
      list.push(listItem[1]);
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    if (list.length) flush();
    paragraph.push(line.trim());
  }
  flush();
  if (code) blocks.push({ type: "code", text: code.join("\n") });
  return blocks;
}

function inline(text: string): ReactNode {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={`${part}-${index}`}>{part.slice(1, -1)}</code>;
    }
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

export function MarkdownPreview({ content }: { content: string }) {
  const blocks = parseMarkdown(content);
  return (
    <article className="markdown-preview">
      {blocks.map((block, index) => {
        if (block.type === "heading") {
          const Heading = `h${block.level}` as "h1" | "h2" | "h3" | "h4";
          return <Heading key={index}>{inline(block.text)}</Heading>;
        }
        if (block.type === "list") {
          return (
            <ul key={index}>
              {block.items.map((item) => (
                <li key={item}>{inline(item)}</li>
              ))}
            </ul>
          );
        }
        if (block.type === "code") return <pre key={index}>{block.text}</pre>;
        return <p key={index}>{inline(block.text)}</p>;
      })}
    </article>
  );
}
