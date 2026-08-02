import { describe, expect, it } from "vitest";

import { normalizeSearchMarkdown } from "./SearchMarkdownContent";

describe("normalizeSearchMarkdown", () => {
  it("normalizes common block and inline LaTeX delimiters", () => {
    const normalized = normalizeSearchMarkdown(
      "前文\\[\\text{A} \\xrightarrow{关系} \\text{B}\\]后文，且 \\(x+y\\)。",
    );

    expect(normalized).toContain("$$\n\\text{A} \\xrightarrow{关系} \\text{B}\n$$");
    expect(normalized).toContain("$x+y$");
  });

  it("recognizes a bare bracketed LaTeX line returned by a model", () => {
    expect(normalizeSearchMarkdown("[\\text{A} \\xrightarrow{关系} \\text{B}]"))
      .toBe("\n$$\n\\text{A} \\xrightarrow{关系} \\text{B}\n$$\n");
  });
});
