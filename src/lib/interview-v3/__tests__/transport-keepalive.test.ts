import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InterviewTransport, type WebSocketLike } from "@/lib/interview-transport";

/** Minimal scriptable socket (same shape the live-adapter tests use). */
class FakeSocket implements WebSocketLike {
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((ev?: { code?: number }) => void) | null = null;
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  receive(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

function makeTransport(socket: FakeSocket, keepaliveMs?: number) {
  return new InterviewTransport({
    url: "ws://test.local/ws/s1",
    getResume: () => ({ session_id: "s1", last_seq: 0, client_conn_id: "conn-ka-test-1" }),
    onMessage: () => {},
    onState: () => {},
    socketFactory: () => socket,
    ...(keepaliveMs !== undefined ? { keepaliveMs } : {}),
  });
}

describe("transport keepalive", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("pings every 25s once connected, so idle proxies never kill the socket", () => {
    const sock = new FakeSocket();
    const t = makeTransport(sock);
    t.connect();
    sock.open();
    sock.receive({ type: "resume_ready" });
    const before = sock.sent.length; // hello frame(s)

    vi.advanceTimersByTime(25_000);
    vi.advanceTimersByTime(25_000);
    const pings = sock.sent.slice(before).map((s) => JSON.parse(s));
    expect(pings).toEqual([{ type: "ping" }, { type: "ping" }]);
    t.close();
  });

  it("stops pinging after close (no timer leak)", () => {
    const sock = new FakeSocket();
    const t = makeTransport(sock);
    t.connect();
    sock.open();
    sock.receive({ type: "resume_ready" });
    t.close();
    const after = sock.sent.length;
    vi.advanceTimersByTime(120_000);
    expect(sock.sent.length).toBe(after);
  });

  it("keepaliveMs: 0 disables pings", () => {
    const sock = new FakeSocket();
    const t = makeTransport(sock, 0);
    t.connect();
    sock.open();
    sock.receive({ type: "resume_ready" });
    const before = sock.sent.length;
    vi.advanceTimersByTime(300_000);
    expect(sock.sent.length).toBe(before);
    t.close();
  });
});
