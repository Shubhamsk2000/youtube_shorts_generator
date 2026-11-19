export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
  get offline() {
    return this.status === 0;
  }
}

const STATUS_TEXT: Record<number, string> = {
  400: "The request wasn't accepted",
  404: "Not found",
  409: "Busy: try again when the current step is done",
  413: "File too large",
  422: "Some input wasn't valid",
  500: "Server error: check the Studio log",
  502: "Studio isn't reachable (proxy error)",
  503: "Studio is starting or overloaded, try again",
  504: "Studio took too long to answer",
  507: "Not enough disk space",
};

/** JSON request with a timeout, readable errors, and 204 support. */
export async function request<T>(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<T> {
  const { timeoutMs = 30000, ...rest } = init;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(path, {
      ...rest,
      headers: rest.body && !(rest.body instanceof Blob) ? { "Content-Type": "application/json", ...rest.headers } : rest.headers,
      signal: rest.signal ?? ctrl.signal,
    });
  } catch (e) {
    const aborted = e instanceof DOMException && e.name === "AbortError";
    throw new ApiError(
      aborted ? "Studio didn't answer in time. It may be busy: try again." : "Can't reach Studio. Is the server running?",
      0,
    );
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let msg = STATUS_TEXT[res.status] ?? `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") msg = body.detail;
      else if (Array.isArray(body?.detail) && body.detail[0]?.msg) msg = body.detail[0].msg;
    } catch {
      /* not JSON (proxy page etc.) */
    }
    throw new ApiError(msg, res.status);
  }
  if (res.status === 204) return undefined as T;
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError("Studio sent an unreadable answer", res.status);
  }
}

export const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});
