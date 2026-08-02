import "katex/dist/katex.min.css";

import rehypeKatex from "rehype-katex";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

export function normalizeSearchMarkdown(content: string): string {
  return content
    .replace(/\\\[([\s\S]*?)\\\]/g, (_match, expression: string) => (
      `\n$$\n${expression.trim()}\n$$\n`
    ))
    .replace(/\\\(([\s\S]*?)\\\)/g, (_match, expression: string) => (
      `$${expression.trim()}$`
    ))
    .replace(/^\s*\[\s*((?:\\text|\\begin|\\frac|\\xrightarrow|\\mathrm)[\s\S]*?)\s*\]\s*$/gm, (
      _match,
      expression: string,
    ) => `\n$$\n${expression.trim()}\n$$\n`);
}

export function SearchMarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      components={{
        a: ({ children, ...properties }) => (
          <a {...properties} target="_blank" rel="noreferrer">
            {children}
          </a>
        ),
      }}
      remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]}
      rehypePlugins={[[rehypeKatex, { strict: "ignore", throwOnError: false }]]}
    >
      {normalizeSearchMarkdown(content)}
    </ReactMarkdown>
  );
}
