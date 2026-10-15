import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Bot,
  Check,
  ChevronDown,
  CircleAlert,
  Clock3,
  FileText,
  Package,
  Plus,
  ShieldCheck,
  ShoppingBag,
  Truck,
  X,
} from "lucide-react";
import { api, ApiError, chatContext, errorMessage, healthy } from "./api";
import type {
  ChatResponse,
  Confirmation,
  Health,
  Message,
  Server,
  User,
} from "./api";
import { Badge, ConfirmModal, ErrorBox, Spinner } from "./ui";

const suggestions = [
  {
    icon: ShoppingBag,
    title: "Explore the catalog",
    text: "Show me the products in our store, including prices and stock.",
  },
  {
    icon: FileText,
    title: "Look up store policies",
    text: "What is our return policy?",
  },
  {
    icon: Truck,
    title: "Check shipping options",
    text: "What shipping options are available?",
  },
];
export function Chat({
  user,
  health,
  servers,
  serversLoading,
}: {
  user: User;
  health?: Health;
  servers: Server[];
  serversLoading: boolean;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [failedRequest, setFailedRequest] = useState<Message[]>();
  const [recentContext, setRecentContext] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation>();
  const [clear, setClear] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const inFlight = useRef(false);
  const wasBusy = useRef(false);
  useEffect(() => {
    if (
      wasBusy.current &&
      !busy &&
      !confirmation &&
      textarea.current?.getClientRects().length
    )
      textarea.current.focus();
    wasBusy.current = busy;
  }, [busy, confirmation]);
  const availableTools = (server: Server) =>
    (server.tools ?? []).filter(
      (tool) => user.role === "admin" || tool.permission === "read",
    );
  const connected = servers.filter(healthy);
  const toolCount = connected.reduce(
    (sum, server) => sum + availableTools(server).length,
    0,
  );
  useEffect(() => {
    scroll.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy, confirmation]);
  function apply(result: ChatResponse) {
    setMessages((prev) => [
      ...prev,
      {
        role: "assistant",
        content: result.message,
        tool_calls: result.tool_calls ?? [],
      },
    ]);
    setConfirmation(result.confirmation);
  }
  async function sendRequest(next: Message[]) {
    if (inFlight.current || confirmation || health?.chat_configured === false)
      return;
    inFlight.current = true;
    const context = chatContext(next);
    setRecentContext(
      context.length < next.length ||
        next.some((message) => message.content.length > 16000),
    );
    setBusy(true);
    setError("");
    setFailedRequest(undefined);
    try {
      apply(
        await api<ChatResponse>("/chat", {
          method: "POST",
          body: {
            messages: context,
          },
        }),
      );
    } catch (e) {
      setError(errorMessage(e));
      setFailedRequest(next);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  async function send(e?: FormEvent) {
    e?.preventDefault();
    if (
      !input.trim() ||
      inFlight.current ||
      confirmation ||
      health?.chat_configured === false
    )
      return;
    const next: Message[] = [
      ...messages,
      { role: "user", content: input.trim() },
    ];
    setMessages(next);
    setInput("");
    await sendRequest(next);
  }
  async function decide(approve: boolean) {
    if (!confirmation || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api<ChatResponse>("/chat/confirm", {
        method: "POST",
        body: { id: confirmation.id, approve },
      });
      setConfirmation(undefined);
      apply(result);
    } catch (e) {
      if (e instanceof ApiError && [403, 404, 409, 410].includes(e.status)) {
        setConfirmation(undefined);
        setError(
          `${errorMessage(e)}. Check Activity before requesting this change again.`,
        );
      } else {
        setError(
          `${errorMessage(e)} Check Activity if the result of this change is uncertain.`,
        );
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return (
    <div className="chat-layout">
      <div className="chat-workspace">
        <div className="chat-heading">
          <div>
            <p className="eyebrow">WORKSPACE CHAT</p>
            <h1>Your Harbor assistant.</h1>
          </div>
          <button
            className="button secondary small"
            disabled={messages.length === 0 || busy || !!confirmation}
            onClick={() => setClear(true)}
          >
            <Plus size={16} />
            New chat
          </button>
        </div>
        {health && !health.chat_configured && (
          <div className="notice warning">
            <CircleAlert size={18} />
            <div>
              <strong>Chat is waiting for a model connection.</strong>
              <span>
                Your administrator needs to configure the Gemini API key.
                Connections, keys, and activity are still available.
              </span>
            </div>
          </div>
        )}
        <div
          className="conversation"
          role="log"
          aria-label="Conversation"
          aria-live="polite"
        >
          {messages.length === 0 ? (
            <div className="chat-welcome">
              <div className="assistant-emblem">
                <div className="emblem-ring" />
                <Package size={31} strokeWidth={1.3} />
                <span className="emblem-point" />
              </div>
              <p className="welcome-kicker">
                HELLO, {user.name.split(" ")[0].toUpperCase()}
              </p>
              <h2>
                Ask about
                <br />
                <span>your store.</span>
              </h2>
              <p>
                Find products, look up policies, or plan a change.
                <br className="desktop-only" /> Review write actions before they
                run.
              </p>
              <div className="suggestions">
                {suggestions.map((s) => (
                  <button
                    key={s.title}
                    disabled={health?.chat_configured === false}
                    onClick={() => {
                      setInput(s.text);
                      textarea.current?.focus();
                    }}
                  >
                    <s.icon size={18} />
                    <span>{s.title}</span>
                    <ArrowRight size={16} />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="messages">
              {messages.map((message, i) => (
                <article key={i} className={`message ${message.role}`}>
                  <div className="message-avatar">
                    {message.role === "assistant" ? (
                      <Bot size={17} />
                    ) : (
                      user.name.slice(0, 1)
                    )}
                  </div>
                  <div className="message-body">
                    <div className="message-label">
                      {message.role === "assistant"
                        ? "Harbor assistant"
                        : user.name}
                      {message.role === "assistant" && (
                        <span>via your gateway</span>
                      )}
                    </div>
                    <div className="message-text">{message.content}</div>
                    {message.tool_calls?.map((call, n) => (
                      <details className="tool-call" key={`${call.name}-${n}`}>
                        <summary>
                          <span
                            className={`tool-outcome ${call.is_error ? "failed" : ""}`}
                          >
                            {call.is_error ? (
                              <X size={13} />
                            ) : (
                              <Check size={13} />
                            )}
                          </span>
                          <code>{call.name}</code>
                          <span className="tool-time">
                            {call.duration_ms} ms
                          </span>
                          <ChevronDown size={14} />
                        </summary>
                        <div className="tool-details">
                          <span className="small-label">ARGUMENTS</span>
                          <pre>{JSON.stringify(call.arguments, null, 2)}</pre>
                          <span className="small-label">RESULT</span>
                          <pre>
                            {typeof call.result === "string"
                              ? call.result
                              : JSON.stringify(call.result, null, 2)}
                          </pre>
                        </div>
                      </details>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          )}
          {busy && (
            <div className="thinking">
              <span className="message-avatar">
                <Bot size={17} />
              </span>
              <Spinner label="Working with your connected tools" />
            </div>
          )}
          {confirmation && (
            <div className="write-confirmation">
              <div className="confirmation-title">
                <ShieldCheck size={20} />
                <div>
                  <h3>Review this action</h3>
                  <p>This will make a change using your access.</p>
                </div>
              </div>
              <p>{confirmation.summary}</p>
              <details>
                <summary>
                  View tool and arguments <ChevronDown size={14} />
                </summary>
                <code>{confirmation.tool_name}</code>
                <pre>{JSON.stringify(confirmation.arguments, null, 2)}</pre>
              </details>
              <div className="confirmation-actions">
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void decide(false)}
                >
                  Cancel action
                </button>
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={() => void decide(true)}
                >
                  <Check size={16} />
                  Approve & run
                </button>
              </div>
            </div>
          )}
          <div ref={scroll} />
        </div>
        <div className="composer-area">
          {error && <ErrorBox message={error} />}
          {failedRequest && (
            <button
              type="button"
              className="text-button"
              disabled={
                busy || !!confirmation || health?.chat_configured === false
              }
              onClick={() => void sendRequest(failedRequest)}
            >
              Retry message
            </button>
          )}
          <form className="composer" onSubmit={send}>
            <label className="sr-only" htmlFor="chat-message">
              Message the Harbor assistant
            </label>
            <textarea
              id="chat-message"
              ref={textarea}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                confirmation
                  ? "Review the pending action to continue"
                  : "Ask about products, orders, policies, or shipping..."
              }
              rows={2}
              maxLength={12000}
              disabled={
                busy || !!confirmation || health?.chat_configured === false
              }
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="composer-footer">
              <span>
                <ShieldCheck size={14} />
                Your permissions. Every action logged.
              </span>
              <button
                className="send-button"
                type="submit"
                disabled={
                  !input.trim() ||
                  busy ||
                  !!confirmation ||
                  health?.chat_configured === false
                }
                aria-label="Send message"
              >
                <ArrowUp size={19} />
              </button>
            </div>
          </form>
          <div className="composer-note">
            <span>
              {recentContext
                ? "Only recent messages are sent. Earlier messages remain visible here."
                : "AI can make mistakes. Review important results."}
            </span>
            <span>Shift + Enter for a new line</span>
          </div>
        </div>
      </div>
      <aside className="context-panel">
        <div className="context-heading">
          <span className="small-label">WORKSPACE CONTEXT</span>
          <span className="live-label">Auto refresh</span>
        </div>
        <h2>
          Your connected
          <br />
          tools.
        </h2>
        <p className="context-description">
          Your assistant works with the tools available to your account.
        </p>
        <div className="context-connections">
          {serversLoading ? (
            <Spinner label="Checking connections" />
          ) : servers.length ? (
            servers.map((server) => (
              <div className="context-connection" key={server.id}>
                <span className="connection-symbol">
                  {server.slug.includes("woo") ? (
                    <ShoppingBag size={18} />
                  ) : server.slug.includes("shipping") ? (
                    <Truck size={18} />
                  ) : (
                    <FileText size={18} />
                  )}
                </span>
                <div>
                  <strong>{server.name}</strong>
                  <span>
                    {!server.enabled
                      ? "Paused"
                      : healthy(server)
                        ? `${availableTools(server).length} tools available`
                        : "Connection unavailable"}
                  </span>
                </div>
                <span
                  className={`status-dot ${healthy(server) ? "is-good" : "is-warning"}`}
                />
              </div>
            ))
          ) : (
            <div className="context-empty">
              No tools connected yet.
              <br />
              <a href="#connections">
                {user.role === "admin"
                  ? "Set up a connection"
                  : "View connections"}{" "}
                <ArrowRight size={13} />
              </a>
            </div>
          )}
        </div>
        <a className="context-manage" href="#connections">
          View connections
          <ArrowUpRightIcon />
        </a>
        <div className="context-divider" />
        <span className="small-label">HOW IT WORKS</span>
        <div className="context-step">
          <span>01</span>
          <div>
            <strong>Ask in plain language</strong>
            <p>Start with a question or a task.</p>
          </div>
        </div>
        <div className="context-step">
          <span>02</span>
          <div>
            <strong>Review before a change</strong>
            <p>Write actions wait for your approval.</p>
          </div>
        </div>
        <div className="context-step">
          <span>03</span>
          <div>
            <strong>Follow the result</strong>
            <p>Tool calls stay visible in activity.</p>
          </div>
        </div>
        <div className="context-bottom">
          <Badge tone={connected.length ? "good" : "warning-badge"}>
            {serversLoading
              ? "Checking tools"
              : `${toolCount} tools discovered`}
          </Badge>
          <span>
            <Clock3 size={12} />
            Status refreshes every 30s
          </span>
        </div>
      </aside>
      {clear && (
        <ConfirmModal
          title="Start a new conversation?"
          description="This clears the conversation from this browser tab. Completed tool calls remain in activity."
          label="Start new chat"
          onConfirm={async () => {
            setMessages([]);
            setError("");
            setInput("");
            setFailedRequest(undefined);
            setRecentContext(false);
          }}
          onClose={() => setClear(false)}
        />
      )}
    </div>
  );
}
function ArrowUpRightIcon() {
  return <ArrowDown size={14} className="diagonal-arrow" />;
}
