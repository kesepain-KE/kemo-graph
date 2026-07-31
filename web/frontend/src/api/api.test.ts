import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

function response<T>(data: T): Response {
  return new Response(JSON.stringify({ ok: true, data, error: null }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("management API", () => {
  it("loads documents and document content from the real endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ documents: [] }))
      .mockResolvedValueOnce(
        response({ source_id: "s1", relative_path: "a.md", content: "# A" }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.getDocuments()).resolves.toEqual({ documents: [] });
    await expect(api.getDocumentContent("s/1")).resolves.toMatchObject({ content: "# A" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/documents");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/documents/s%2F1/content");
  });

  it("uploads Markdown content as the JSON contract requires", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({ source_id: "s1", filename: "a.md", path: "a.md", size: 3 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["# A"], "a.md", { type: "text/markdown" });

    await api.uploadFile(file);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ filename: "a.md", content: "# A" });
  });

  it("imports binary documents through multipart without forcing a JSON content type", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({
        source_id: "s1",
        original_filename: "guide.pdf",
        detected_format: "pdf",
        markdown_relative_path: "guide-stable.md",
        conversion_status: "completed",
        ingest_status: "completed",
        size: 4,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File([new Uint8Array([1, 2, 3, 4])], "guide.pdf");

    await api.importFile(file);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/import?ingest=true");
    expect(init.body).toBeInstanceOf(FormData);
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });

  it("empties the recycle bin through the destructive maintenance endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({ deleted: 3, forced: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.emptyRecycle()).resolves.toEqual({ deleted: 3, forced: true });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/maintenance/recycle");
    expect(init.method).toBe("DELETE");
  });

  it("normalizes connection failures into a friendly error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    await expect(api.getConfig()).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      status: 0,
    });
  });
});
