/**
 * api.ts — Typed wrapper around the Baymax FastAPI server.
 */

import * as https from "https";
import * as http from "http";

export interface ChatResponse {
  session_id: string;
  message: string;
  history: Array<{ role: string; content: string }>;
}

export interface HealthResponse {
  status: string;
  chat_model: string;
  use_graph: boolean;
}

export class BaymaxApiClient {
  constructor(private baseUrl: string) {}

  private request<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<T> {
    return new Promise((resolve, reject) => {
      const url = new URL(path, this.baseUrl);
      const payload = body ? JSON.stringify(body) : undefined;
      const options: http.RequestOptions = {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname + url.search,
        method,
        headers: {
          "Content-Type": "application/json",
          ...(payload
            ? { "Content-Length": Buffer.byteLength(payload) }
            : {}),
        },
      };

      console.log(`[Baymax API] ${method} ${url.href} (hostname=${url.hostname}, port=${url.port}, protocol=${url.protocol})`);
      const lib = url.protocol === "https:" ? https : http;
      const req = lib.request({ ...options, agent: false }, (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
            try {
              resolve(JSON.parse(data) as T);
            } catch {
              reject(new Error(`Invalid JSON: ${data}`));
            }
          } else {
            reject(new Error(`HTTP ${res.statusCode}: ${data}`));
          }
        });
      });

      req.on("error", (err) => {
        console.error(`[Baymax API] Request error: ${err.message}`);
        reject(err);
      });
      if (payload) {
        req.write(payload);
      }
      req.end();
    });
  }

  /** POST /chat */
  chat(message: string, sessionId?: string): Promise<ChatResponse> {
    return this.request<ChatResponse>("POST", "/chat", {
      message,
      session_id: sessionId,
    });
  }

  /** GET /health */
  health(): Promise<HealthResponse> {
    return this.request<HealthResponse>("GET", "/health");
  }

  /** DELETE /history/:sessionId */
  clearHistory(sessionId: string): Promise<void> {
    return this.request<void>("DELETE", `/history/${sessionId}`);
  }
}
