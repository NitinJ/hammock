const BASE = "/api";

/**
 * Thrown for non-2xx responses. Carries both the HTTP status and the
 * parsed response body so callers can surface FastAPI's structured
 * ``detail`` (compile failures, validation errors, etc.) to the user
 * instead of just "HTTP 422".
 */
export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, statusText: string, path: string, body: unknown) {
    const detail = formatDetail(body);
    super(`HTTP ${status} ${statusText} ${path}${detail ? ` — ${detail}` : ""}`);
    this.status = status;
    this.body = body;
  }
}

function formatDetail(body: unknown): string {
  if (!body || typeof body !== "object") return "";
  const detail = (body as { detail?: unknown }).detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (typeof d === "string") return d;
        if (d && typeof d === "object") {
          const o = d as Record<string, unknown>;
          if (typeof o.message === "string") return String(o.kind ?? "error") + ": " + o.message;
          if (typeof o.msg === "string") {
            const loc = Array.isArray(o.loc) ? o.loc.join(".") : "";
            return loc ? `${loc}: ${o.msg}` : String(o.msg);
          }
          return JSON.stringify(d);
        }
        return String(d);
      })
      .join("; ");
  }
  if (typeof detail === "string") return detail;
  return JSON.stringify(body);
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // not JSON; that's fine
    }
    throw new ApiError(res.status, res.statusText, `${BASE}${path}`, body);
  }
  // 204 No Content has no body — return undefined as T.
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PATCH",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
