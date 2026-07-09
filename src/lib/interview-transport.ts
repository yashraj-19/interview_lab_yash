/**
 * InterviewTransport — resilient WebSocket transport for the AI interview room.
 *
 * Framework-agnostic (no React) so it can be unit-tested with a mocked
 * WebSocket and fake timers. The React surface lives in
 * `src/hooks/use-interview-socket.ts`.
 *
 * Responsibilities (transport mechanics ONLY — no interview-domain logic):
 *  - socket lifecycle (open/close/error)
 *  - explicit connection state machine
 *  - bounded exponential backoff with jitter
 *  - resume handshake (client_hello → resume_ready / resume_rejected)
 *  - single-socket / single-timer guarantees + full cleanup
 *  - concise structured logging hooks
 *
 * SUCCESS MODEL (important): a raw WebSocket `open` is NOT success. The socket
 * being open only means the transport may now run the handshake. A connection
 * is "connected" — and the retry counter resets — ONLY when the server returns
 * `resume_ready`. This prevents an accept-then-reject/close server from
 * resetting backoff forever (a real failure mode). If `resume_ready` does not
 * arrive within `handshakeTimeoutMs`, the socket is torn down and the bounded
 * retry policy applies (the attempt counter is NOT reset).
 *
 * Interview-domain message handling (code edits, transcripts, voice, etc.) is
 * the consumer's job: the transport hands every non-handshake message to
 * `onMessage`. Sequence tracking is owned by the consumer and read back through
 * `getResume()` so the transport stays pure.
 */

export type ConnectionState =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "failed"
  | "completed";

/** Minimal structural type so tests can inject a mock without DOM lib types. */
export interface WebSocketLike {
  readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((ev: unknown) => void) | null;
  onclose: ((ev: { code?: number; reason?: string }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
}

export interface ResumeParams {
  session_id: string;
  /** Last protocol seq the consumer has durably applied. 0 for a fresh start. */
  last_seq: number;
  /** Stable per-browser-tab connection id; survives reconnects. */
  client_conn_id: string;
  /** This tab already had a connection (e.g. an in-tab reload). Lets the first
   *  hello request a resume even when last_seq is 0, so a reload restores the
   *  session instead of restarting it. */
  had_prior_connection?: boolean;
}

export type TransportLogEvent =
  | "connect"
  | "open"
  | "unexpected_close"
  | "handshake_timeout"
  | "reconnect_scheduled"
  | "resume_ready"
  | "resume_rejected"
  | "reconnect_failed"
  | "intentional_close"
  | "completed";

export interface TransportOptions {
  /** Full ws:// or wss:// URL to connect to. */
  url: string;
  /** Read the current resume params at connect time (consumer owns last_seq). */
  getResume: () => ResumeParams;
  /** Every non-handshake inbound message (already JSON-parsed when possible). */
  onMessage: (data: unknown) => void;
  /** Connection-state transitions. The UI must reflect these truthfully. */
  onState: (state: ConnectionState) => void;
  /**
   * Fired whenever the attempt counter changes — including consecutive failed
   * reconnects where the state stays `reconnecting`. The UI must use this for a
   * truthful attempt number; relying on `onState` alone leaves it stale.
   */
  onAttempt?: (attempts: number) => void;
  /** Server confirmed the (fresh or resumed) session: handshake complete. */
  onResumeReady?: (msg: Record<string, unknown>) => void;
  /** Server refused the handshake. The transport handles retry/fail; this is
   *  surfaced so the consumer can show a reason or reset domain state. */
  onResumeRejected?: (msg: Record<string, unknown>) => void;

  // ── policy (sensible defaults) ──
  maxAttempts?: number; // default 8
  baseDelayMs?: number; // default 1000
  maxDelayMs?: number; // default 10000
  /** How long to wait for `resume_ready` after the socket opens. default 10s. */
  handshakeTimeoutMs?: number;
  /** Keepalive ping interval once connected (ms). Proxies kill idle sockets
   *  after a few quiet minutes; < 30s stays inside common idle windows.
   *  0 disables (tests). default 25s. */
  keepaliveMs?: number;
  /** Close codes that must NOT trigger a reconnect (auth/policy/in-use). */
  nonRetryableCloseCodes?: number[];
  /** resume_rejected reasons that are recoverable; everything else is terminal. */
  retryableRejectReasons?: string[];

  // ── injectables (tests) ──
  socketFactory?: (url: string) => WebSocketLike;
  random?: () => number;
  logger?: (event: TransportLogEvent, fields: Record<string, unknown>) => void;
}

const OPEN = 1; // WebSocket.OPEN — avoid referencing DOM constant in node tests

// Auth (4401/4403), session not found (4404), session-in-use elsewhere (4409),
// and standard policy violation (1008) are terminal: retrying cannot help.
const DEFAULT_NON_RETRYABLE = [1008, 4401, 4403, 4404, 4409];

// Recoverable handshake refusals. Anything else (session_gone, completed,
// auth_failed, invalid_handshake, in_use, …) is treated as terminal so a
// server that keeps rejecting can never loop the client forever.
const DEFAULT_RETRYABLE_REJECTS = ["server_busy", "unavailable", "temporarily_unavailable"];

export class InterviewTransport {
  private readonly opts: Required<
    Pick<
      TransportOptions,
      | "maxAttempts"
      | "baseDelayMs"
      | "maxDelayMs"
      | "handshakeTimeoutMs"
      | "nonRetryableCloseCodes"
      | "retryableRejectReasons"
      | "random"
    >
  > &
    TransportOptions;

  private ws: WebSocketLike | null = null;
  private state: ConnectionState = "disconnected";
  private attempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private handshakeTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  /** True once `resume_ready` arrived for the CURRENT socket. */
  private confirmed = false;
  /** Set when the consumer deliberately tears down — suppresses reconnects. */
  private intentional = false;
  /** Set once the interview is completed — terminal, never reconnect. */
  private finished = false;
  /** Set on a terminal failure (non-retryable close / terminal reject). */
  private terminal = false;

  constructor(options: TransportOptions) {
    this.opts = {
      maxAttempts: options.maxAttempts ?? 8,
      baseDelayMs: options.baseDelayMs ?? 1000,
      maxDelayMs: options.maxDelayMs ?? 10000,
      handshakeTimeoutMs: options.handshakeTimeoutMs ?? 10000,
      nonRetryableCloseCodes: options.nonRetryableCloseCodes ?? DEFAULT_NON_RETRYABLE,
      retryableRejectReasons: options.retryableRejectReasons ?? DEFAULT_RETRYABLE_REJECTS,
      random: options.random ?? Math.random,
      ...options,
    };
  }

  getState(): ConnectionState {
    return this.state;
  }

  getAttempts(): number {
    return this.attempts;
  }

  /** Begin the first connection. Idempotent if already connecting/connected. */
  connect(): void {
    if (this.finished || this.intentional || this.terminal) return;
    if (this.ws && this.ws.readyState <= OPEN) return; // already live
    this.openSocket(false);
  }

  /** Send a JSON-serialisable payload. Returns false if the socket is not open. */
  send(payload: unknown): boolean {
    if (!this.ws || this.ws.readyState !== OPEN) return false;
    try {
      this.ws.send(JSON.stringify(payload));
      return true;
    } catch {
      return false;
    }
  }

  /** True only once the server has confirmed the handshake for the live socket. */
  isReady(): boolean {
    return this.confirmed && this.state === "connected";
  }

  /** Mark the interview completed (server-confirmed or candidate-ended). */
  markCompleted(): void {
    this.finished = true;
    this.clearTimers();
    this.setState("completed");
    this.closeSocket();
    this.log("completed", {});
  }

  /** Deliberate teardown (component unmount). No reconnect after this. */
  close(): void {
    this.intentional = true;
    this.clearTimers();
    this.log("intentional_close", {});
    this.closeSocket();
    if (!this.finished) this.setState("disconnected");
  }

  /** Manual retry after the transport reached `failed`. */
  retry(): void {
    if (this.finished || this.intentional) return;
    this.setAttempts(0);
    this.terminal = false;
    this.clearTimers();
    this.openSocket(true);
  }

  private setAttempts(n: number): void {
    if (this.attempts === n) return;
    this.attempts = n;
    this.opts.onAttempt?.(n);
  }

  // ── internals ──────────────────────────────────────────────────────────

  private openSocket(isReconnect: boolean): void {
    // Guarantee a single live socket: drop any previous one first.
    this.closeSocket();
    this.confirmed = false;
    this.setState(isReconnect ? "reconnecting" : "connecting");
    this.log("connect", { attempt: this.attempts, reconnect: isReconnect });

    const factory =
      this.opts.socketFactory ?? ((url: string) => new WebSocket(url) as unknown as WebSocketLike);
    const ws = factory(this.opts.url);
    this.ws = ws;

    ws.onopen = () => {
      if (ws !== this.ws) return; // stale socket
      // NOTE: open is NOT "connected". We send the handshake and wait for
      // resume_ready. State stays connecting/reconnecting; attempts NOT reset.
      this.log("open", {});
      this.sendHello(isReconnect);
      this.startHandshakeTimer();
    };

    ws.onmessage = (ev) => {
      if (ws !== this.ws) return;
      this.handleMessage(ev.data);
    };

    ws.onerror = () => {
      // Errors are followed by a close event; let onclose drive policy.
    };

    ws.onclose = (ev) => {
      if (ws !== this.ws) return;
      this.ws = null;
      this.clearHandshakeTimer();
      this.handleClose(ev?.code);
    };
  }

  private sendHello(isReconnect: boolean): void {
    const resume = this.opts.getResume();
    // A reconnect, any session where we've already applied events, OR a tab that
    // previously connected (in-tab reload) is a resume.
    const isResume = isReconnect || resume.last_seq > 0 || !!resume.had_prior_connection;
    // Never log PINs/secrets — only the resume bookkeeping fields.
    this.send({
      type: "client_hello",
      session_id: resume.session_id,
      last_seq: resume.last_seq,
      client_conn_id: resume.client_conn_id,
      resume: isResume,
    });
  }

  private startHandshakeTimer(): void {
    this.clearHandshakeTimer();
    this.handshakeTimer = setTimeout(() => {
      this.handshakeTimer = null;
      if (this.confirmed || this.finished || this.intentional) return;
      // Socket opened but the server never confirmed the session. Treat as a
      // failed attempt: tear down and apply bounded backoff (do NOT reset).
      this.log("handshake_timeout", {});
      this.closeSocket();
      this.scheduleReconnect();
    }, this.opts.handshakeTimeoutMs);
  }

  private handleMessage(raw: unknown): void {
    let msg: unknown = raw;
    if (typeof raw === "string") {
      try {
        msg = JSON.parse(raw);
      } catch {
        this.opts.onMessage(raw);
        return;
      }
    }

    const type =
      msg && typeof msg === "object" ? (msg as Record<string, unknown>).type : undefined;

    if (type === "resume_ready") {
      // The ONLY success signal: now we are truly connected and reset backoff.
      this.confirmed = true;
      this.setAttempts(0);
      this.clearHandshakeTimer();
      this.setState("connected");
      this.log("resume_ready", {});
      this.startKeepalive();
      this.opts.onResumeReady?.(msg as Record<string, unknown>);
      return;
    }

    if (type === "resume_rejected") {
      const reason = String((msg as Record<string, unknown>).reason ?? "unknown");
      this.clearHandshakeTimer();
      this.opts.onResumeRejected?.(msg as Record<string, unknown>);
      const retryable = this.opts.retryableRejectReasons.includes(reason);
      this.log("resume_rejected", { reason, retryable });
      this.closeSocket();
      if (retryable) {
        this.scheduleReconnect(); // bounded — attempts not reset (never confirmed)
      } else {
        this.terminal = true;
        this.setState("failed");
      }
      return;
    }

    this.opts.onMessage(msg);
  }

  private handleClose(code?: number): void {
    if (this.finished || this.intentional || this.terminal) {
      return; // expected/terminal close; state already set
    }

    if (typeof code === "number" && this.opts.nonRetryableCloseCodes.includes(code)) {
      this.log("unexpected_close", { code, retryable: false });
      this.terminal = true;
      this.setState("failed");
      return;
    }

    // Covers both an established connection dropping AND an accept-then-close
    // before resume_ready. Either way attempts only reset on resume_ready, so a
    // flapping/rejecting server still converges to `failed`.
    this.log("unexpected_close", { code, confirmed: this.confirmed, retryable: true });
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return; // never stack timers
    if (this.finished || this.intentional || this.terminal) return;
    if (this.attempts >= this.opts.maxAttempts) {
      this.log("reconnect_failed", { attempts: this.attempts });
      this.terminal = true;
      this.setState("failed");
      return;
    }

    this.setAttempts(this.attempts + 1);
    const delay = this.backoffDelay(this.attempts);
    this.setState("reconnecting");
    this.log("reconnect_scheduled", { attempt: this.attempts, delayMs: delay });

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.finished || this.intentional || this.terminal) return;
      this.openSocket(true);
    }, delay);
  }

  /** Equal-jitter backoff: ~1s, 2s, 4s, 8s, capped at maxDelayMs. */
  private backoffDelay(attempt: number): number {
    const raw = Math.min(this.opts.maxDelayMs, this.opts.baseDelayMs * 2 ** (attempt - 1));
    const half = raw / 2;
    return Math.round(half + this.opts.random() * half);
  }

  /** Keep the socket warm across long silent thinking pauses: reverse proxies
   * (Render included) kill idle WebSockets after a few quiet minutes, which
   * would force a mid-interview reconnect for no reason. The server ignores
   * unknown frame types by design, and `ping` is explicitly excluded from its
   * candidate-activity tracking so keepalives never suppress the silence
   * nudges. Interval < 30s stays inside common proxy idle windows. */
  private startKeepalive(): void {
    this.stopKeepalive();
    const every = this.opts.keepaliveMs ?? 25_000;
    if (every <= 0) return; // disabled (tests)
    this.pingTimer = setInterval(() => {
      this.send({ type: "ping" });
    }, every);
  }

  private stopKeepalive(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private closeSocket(): void {
    this.clearHandshakeTimer();
    this.stopKeepalive();
    const ws = this.ws;
    this.ws = null;
    if (!ws) return;
    // Detach handlers so a late close/message from the old socket is ignored.
    ws.onopen = null;
    ws.onmessage = null;
    ws.onerror = null;
    ws.onclose = null;
    try {
      ws.close();
    } catch {
      /* already closing */
    }
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private clearHandshakeTimer(): void {
    if (this.handshakeTimer) {
      clearTimeout(this.handshakeTimer);
      this.handshakeTimer = null;
    }
  }

  private clearTimers(): void {
    this.clearReconnectTimer();
    this.clearHandshakeTimer();
  }

  private setState(next: ConnectionState): void {
    if (this.state === next) return;
    this.state = next;
    this.opts.onState(next);
  }

  private log(event: TransportLogEvent, fields: Record<string, unknown>): void {
    this.opts.logger?.(event, fields);
  }
}
