/**
 * The one place this app talks to the network.
 *
 * Everything goes through `request()`: the base URL, the auth header, JSON
 * encoding, and — the part worth the file — turning every possible failure into
 * one `ApiError` with a message a human can read.
 *
 * **Why error normalization matters more than it looks.** A `fetch` call can
 * fail in at least five shapes: the network never connected, the server
 * answered 500 with an HTML error page, it answered 422 with FastAPI's nested
 * validation structure, it answered 401 with `{detail: "..."}`, or it answered
 * 204 with no body at all. Handled at each call site, that becomes five
 * half-right `catch` blocks and a UI that shows `[object Object]` to a user
 * roughly once a week. Handled here, every caller gets `error.message` and can
 * render it.
 */

import type { Token } from "./types";

const BASE_URL = (import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000").replace(
  /\/+$/,
  "",
);

/**
 * Where the access token lives.
 *
 * `localStorage`, with eyes open. The honest trade-off: a token in
 * `localStorage` is readable by any script that runs on this origin, so a
 * successful XSS steals the session. The usual alternative — an httpOnly
 * cookie — is immune to that but needs CSRF protection, a same-site or proxied
 * deployment, and a backend that sets cookies, none of which this API does (it
 * issues bearer tokens for an `Authorization` header).
 *
 * What makes it defensible here rather than merely convenient: the token lives
 * 30 minutes, there is no refresh token to steal alongside it, and React escapes
 * interpolated content by default so the XSS has to be introduced deliberately.
 * The right fix is a refresh-token cookie, and that is a backend milestone.
 */
const TOKEN_KEY = "finance_tracker_token";

export const tokenStore = {
  get(): string | null {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      // Safari in private mode, and any browser with site data blocked, throw
      // on access rather than returning null. An app that crashes on boot
      // because storage is unavailable is worse than one that asks for a login.
      return null;
    }
  },
  set(token: string): void {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* Session survives in memory for this tab; nothing else to do. */
    }
  },
  clear(): void {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  },
};

export class ApiError extends Error {
  readonly status: number;
  /** The parsed body, when there was one. Useful for field-level 422 display. */
  readonly body: unknown;

  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }

  /** A 401 means the token is missing, expired or forged — all "log in again". */
  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  /** No response at all: offline, DNS, CORS, or the API is down. */
  get isNetworkError(): boolean {
    return this.status === 0;
  }
}

/**
 * Pull a readable sentence out of whatever the server sent.
 *
 * FastAPI has two error shapes and this app hits both. A raised
 * `HTTPException` gives `{detail: "Account 3 not found"}`. A Pydantic
 * validation failure gives `{detail: [{loc: [...], msg: "...", ...}, ...]}` —
 * an array, where naive code renders "[object Object]" and the user learns
 * nothing about which field they got wrong.
 */
function extractMessage(body: unknown, status: number): string {
  if (typeof body === "string" && body.trim()) return body;

  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;

    if (typeof detail === "string") return detail;

    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) => {
          if (!item || typeof item !== "object") return null;
          const { loc, msg } = item as { loc?: unknown[]; msg?: string };
          if (!msg) return null;
          // `loc` is like ["body", "amount"] — the first element names the
          // request part, which the user does not care about. The rest is the
          // field path, which is the useful half.
          const field = Array.isArray(loc)
            ? loc.slice(1).filter((s) => typeof s === "string").join(".")
            : "";
          return field ? `${field}: ${msg}` : msg;
        })
        .filter((part): part is string => Boolean(part));

      if (parts.length) return parts.join("; ");
    }
  }

  return `Request failed (${status})`;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  /** Serialized as JSON. Use `form` instead for the login endpoint. */
  json?: unknown;
  /** Sent as `application/x-www-form-urlencoded` — what OAuth2 login requires. */
  form?: Record<string, string>;
  /** Sent as `multipart/form-data` — the CSV import endpoint. */
  formData?: FormData;
  /** Appended as a query string, skipping undefined/empty values. */
  params?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", json, form, formData, params, signal } = options;

  const url = new URL(`${BASE_URL}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      // Skipping empty strings as well as null/undefined is what lets the
      // filter state be a flat object with optional keys: an unset filter
      // simply doesn't appear in the URL, rather than sending `category_id=`
      // and getting a 422 for a value that isn't an integer.
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }

  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    body = new URLSearchParams(form).toString();
  } else if (formData) {
    // Deliberately no Content-Type: the browser must set it itself so it can
    // append the multipart boundary. Setting it by hand produces a request the
    // server cannot parse, and the error gives no hint why.
    body = formData;
  }

  let response: Response;
  try {
    response = await fetch(url.toString(), { method, headers, body, signal });
  } catch (error) {
    // `fetch` rejects only for network-level failures — a 500 is a *resolved*
    // promise. So this branch is genuinely "the request never got an answer":
    // offline, DNS, the API not running, or a CORS preflight the browser
    // refused. Status 0 is the conventional marker for it.
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(
      "Could not reach the server. Check that the API is running.",
      0,
      error,
    );
  }

  // 204 has no body by contract (DELETE /transactions/{id}); calling .json()
  // on it throws a parse error that looks like a server bug.
  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      // A proxy timeout or an unhandled 500 can return HTML. Keep the raw text
      // so `extractMessage` has something, rather than throwing here.
      parsed = text;
    }
  }

  if (!response.ok) {
    throw new ApiError(extractMessage(parsed, response.status), response.status, parsed);
  }

  return parsed as T;
}

/** `POST /auth/login` — form-encoded, and the field is `username`, not `email`. */
export function login(email: string, password: string): Promise<Token> {
  return request<Token>("/auth/login", {
    method: "POST",
    form: { username: email, password },
  });
}
