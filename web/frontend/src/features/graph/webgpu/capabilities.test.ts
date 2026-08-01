import { describe, expect, it } from "vitest";

import { evaluateWebGpuCapability } from "./capabilities";

describe("WebGPU capability policy", () => {
  it("accepts supported HTTPS and localhost contexts", () => {
    expect(evaluateWebGpuCapability({
      hasGpu: true,
      isSecureContext: true,
      protocol: "https:",
      hostname: "example.test",
    }).available).toBe(true);
    expect(evaluateWebGpuCapability({
      hasGpu: true,
      isSecureContext: false,
      protocol: "http:",
      hostname: "127.0.0.1",
    }).available).toBe(true);
  });

  it("explains insecure contexts and unavailable adapters", () => {
    expect(evaluateWebGpuCapability({
      hasGpu: true,
      isSecureContext: false,
      protocol: "http:",
      hostname: "example.test",
    }).reason).toContain("HTTPS");
    expect(evaluateWebGpuCapability({
      hasGpu: false,
      isSecureContext: true,
      protocol: "https:",
      hostname: "example.test",
    }).reason).toContain("未提供 WebGPU");
  });
});
