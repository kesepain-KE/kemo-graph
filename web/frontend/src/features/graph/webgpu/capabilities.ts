export type WebGpuCapability = {
  available: boolean;
  secureContext: boolean;
  reason: string | null;
};

export type WebGpuEnvironment = {
  hasGpu: boolean;
  isSecureContext: boolean;
  protocol: string;
  hostname: string;
};

export function evaluateWebGpuCapability(
  environment: WebGpuEnvironment,
): WebGpuCapability {
  const localHost = environment.hostname === "localhost"
    || environment.hostname === "127.0.0.1"
    || environment.hostname === "[::1]";
  const secureContext = environment.isSecureContext
    || environment.protocol === "https:"
    || localHost;
  if (!secureContext) {
    return {
      available: false,
      secureContext: false,
      reason: "WebGPU 仅能在 HTTPS 或 localhost 安全上下文中使用",
    };
  }
  if (!environment.hasGpu) {
    return {
      available: false,
      secureContext: true,
      reason: "当前浏览器或显卡驱动未提供 WebGPU",
    };
  }
  return { available: true, secureContext: true, reason: null };
}

export function inspectWebGpuCapability(): WebGpuCapability {
  const location = globalThis.location;
  return evaluateWebGpuCapability({
    hasGpu: typeof navigator !== "undefined" && Boolean(navigator.gpu),
    isSecureContext: globalThis.isSecureContext === true,
    protocol: location?.protocol ?? "",
    hostname: location?.hostname ?? "",
  });
}
