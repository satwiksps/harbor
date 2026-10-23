import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import Markdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  ArrowUpRight,
  Bot,
  Check,
  ChevronDown,
  CircleAlert,
  Clock3,
  FileText,
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

const messageComponents: Components = {
  h1: ({ children }) => <h3>{children}</h3>,
  h2: ({ children }) => <h3>{children}</h3>,
  a: ({ href, title, children }) =>
    href && !href.startsWith("#") ? (
      <a href={href} title={title} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  // Model responses can offer image links without automatically fetching remote content.
  img: ({ src, alt }) =>
    typeof src === "string" && src ? (
      <a href={src} target="_blank" rel="noopener noreferrer">
        {alt || "View image"}
      </a>
    ) : (
      <span>{alt}</span>
    ),
  table: ({ children }) => (
    <div
      className="message-table-scroll"
      role="region"
      aria-label="Assistant response table"
      tabIndex={0}
    >
      <table>{children}</table>
    </div>
  ),
  pre: ({ children }) => <pre tabIndex={0}>{children}</pre>,
};

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function chatResponse(value: unknown): ChatResponse {
  const invalid = () => {
    throw new Error(
      "The workspace returned an invalid chat response. Please retry.",
    );
  };
  if (!isRecord(value) || typeof value.message !== "string") return invalid();
  const calls = value.tool_calls ?? [];
  if (
    !Array.isArray(calls) ||
    calls.some(
      (call) =>
        !isRecord(call) ||
        typeof call.name !== "string" ||
        !isRecord(call.arguments) ||
        typeof call.is_error !== "boolean" ||
        typeof call.duration_ms !== "number" ||
        !Number.isFinite(call.duration_ms),
    )
  )
    return invalid();
  const confirmation = value.confirmation;
  if (
    confirmation != null &&
    (!isRecord(confirmation) ||
      typeof confirmation.id !== "string" ||
      !confirmation.id ||
      typeof confirmation.summary !== "string" ||
      typeof confirmation.tool_name !== "string" ||
      !isRecord(confirmation.arguments))
  )
    return invalid();
  return {
    ...value,
    tool_calls: calls,
    confirmation: confirmation ?? undefined,
  } as unknown as ChatResponse;
}

export function Chat({
  user,
  health,
  servers,
  serversLoading,
  serversError,
  refreshServers,
}: {
  user: User;
  health?: Health;
  servers: Server[];
  serversLoading: boolean;
  serversError: string;
  refreshServers: () => Promise<void>;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [failedRequest, setFailedRequest] = useState<Message[]>();
  const [recentContext, setRecentContext] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation>();
  const [clear, setClear] = useState(false);
  const [showLatest, setShowLatest] = useState(false);
  const [retryAt, setRetryAt] = useState(0);
  const [retryRemaining, setRetryRemaining] = useState(0);
  const [pendingDecision, setPendingDecision] = useState<
    "approve" | "cancel"
  >();
  const conversation = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const approvalHeading = useRef<HTMLHeadingElement>(null);
  const followConversation = useRef(true);
  const focusNewChat = useRef(false);
  const inFlight = useRef(false);
  const wasBusy = useRef(false);
  const request = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (!retryAt) return;
    const update = () => {
      const remaining = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000));
      setRetryRemaining(remaining);
      if (!remaining) setRetryAt(0);
    };
    update();
    const timer = setInterval(update, 250);
    return () => clearInterval(timer);
  }, [retryAt]);
  function respectRetryAfter(error: unknown) {
    if (error instanceof ApiError && error.retryAfterSeconds) {
      setRetryAt(Date.now() + error.retryAfterSeconds * 1000);
      setRetryRemaining(Math.ceil(error.retryAfterSeconds));
    }
  }
  useEffect(() => {
    if (
      wasBusy.current &&
      !busy &&
      !confirmation &&
      textarea.current?.getClientRects().length &&
      (document.activeElement === document.body ||
        document.activeElement === textarea.current)
    )
      textarea.current.focus({ preventScroll: true });
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
    const node = conversation.current;
    if (!node) return;
    const empty = messages.length === 0;
    if (empty || followConversation.current) {
      node.scrollTo({ top: empty ? 0 : node.scrollHeight, behavior: "auto" });
      setShowLatest(false);
    } else {
      setShowLatest(
        node.scrollHeight - node.scrollTop - node.clientHeight > 80,
      );
    }
  }, [messages, busy, confirmation]);
  useEffect(() => {
    if (confirmation && approvalHeading.current?.getClientRects().length)
      approvalHeading.current.focus({ preventScroll: true });
  }, [confirmation]);
  useEffect(() => {
    if (!clear && focusNewChat.current) {
      focusNewChat.current = false;
      textarea.current?.focus({ preventScroll: true });
    }
  }, [clear]);
  function apply(result: ChatResponse) {
    if (result.confirmation) followConversation.current = true;
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
    if (
      inFlight.current ||
      confirmation ||
      Date.now() < retryAt ||
      health?.chat_configured === false
    )
      return;
    inFlight.current = true;
    const controller = new AbortController();
    request.current = controller;
    const context = chatContext(next);
    setRecentContext(
      context.length < next.length ||
        next.some((message) => message.content.length > 16000),
    );
    setBusy(true);
    setError("");
    setFailedRequest(undefined);
    try {
      const result = chatResponse(
        await api<unknown>("/chat", {
          method: "POST",
          signal: controller.signal,
          body: {
            messages: context,
          },
        }),
      );
      if (!controller.signal.aborted) apply(result);
    } catch (e) {
      if (controller.signal.aborted) return;
      respectRetryAfter(e);
      setError(errorMessage(e));
      setFailedRequest(next);
    } finally {
      inFlight.current = false;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  async function send(e?: FormEvent) {
    e?.preventDefault();
    if (
      !input.trim() ||
      inFlight.current ||
      confirmation ||
      Date.now() < retryAt ||
      health?.chat_configured === false
    )
      return;
    const next: Message[] = [
      ...messages,
      { role: "user", content: input.trim() },
    ];
    followConversation.current = true;
    setMessages(next);
    setInput("");
    await sendRequest(next);
  }
  async function decide(approve: boolean) {
    if (!confirmation || inFlight.current || Date.now() < retryAt) return;
    inFlight.current = true;
    const controller = new AbortController();
    request.current = controller;
    setPendingDecision(approve ? "approve" : "cancel");
    setBusy(true);
    setError("");
    try {
      const result = chatResponse(
        await api<unknown>("/chat/confirm", {
          method: "POST",
          signal: controller.signal,
          body: { id: confirmation.id, approve },
        }),
      );
      if (controller.signal.aborted) return;
      setConfirmation(undefined);
      apply(result);
    } catch (e) {
      if (controller.signal.aborted) return;
      respectRetryAfter(e);
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
      if (!controller.signal.aborted) {
        setPendingDecision(undefined);
        setBusy(false);
      }
    }
  }
  return (
    <div className="chat-layout">
      <div className="chat-workspace">
        <div className="chat-heading">
          <div>
            <p className="eyebrow">WORKSPACE CHAT</p>
            <h1>Your Harbor assistant.</h1>
            <p className="chat-heading-description">
              {user.role === "admin"
                ? "Query your store and review proposed changes."
                : "Find answers with your team's connected tools."}
            </p>
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
                {user.role === "admin"
                  ? "Configure the Gemini API key to enable chat."
                  : "Ask your administrator to configure the Gemini API key."}{" "}
                Connections, keys, and activity are still available.
              </span>
            </div>
          </div>
        )}
        {serversError && (
          <ErrorBox
            message={`Connection status is unavailable. ${serversError}`}
            retry={refreshServers}
          />
        )}
        <div
          ref={conversation}
          className="conversation"
          role="log"
          aria-label="Conversation"
          aria-live="polite"
          onScroll={(event) => {
            const node = event.currentTarget;
            const atLatest =
              node.scrollHeight - node.scrollTop - node.clientHeight <= 80;
            followConversation.current = atLatest;
            setShowLatest(!atLatest && messages.length > 0);
          }}
        >
          {messages.length === 0 ? (
            <div className="chat-welcome">
              <p className="welcome-kicker">
                HELLO, {user.name.split(" ")[0].toUpperCase()}
              </p>
              <h2>
                Ask about <span>your store.</span>
              </h2>
              <p>
                {user.role === "admin"
                  ? "Find products, look up policies, or plan a change."
                  : "Find products, look up policies, and check shipping."}
                <br className="desktop-only" />{" "}
                {user.role === "admin"
                  ? "Review write actions before they run."
                  : "Your account can use read tools only."}
              </p>
              <div className="suggestions">
                {suggestions.map((s) => (
                  <button
                    key={s.title}
                    aria-label={s.title}
                    disabled={health?.chat_configured === false}
                    onClick={() => {
                      setInput(s.text);
                      textarea.current?.focus();
                    }}
                  >
                    <s.icon size={18} />
                    <span className="suggestion-copy">
                      <strong>{s.title}</strong>
                      <span>{s.text}</span>
                    </span>
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
                    {message.role === "assistant" ? (
                      <div className="message-text assistant-markdown">
                        <Markdown
                          remarkPlugins={[remarkGfm]}
                          components={messageComponents}
                          skipHtml
                        >
                          {message.content}
                        </Markdown>
                      </div>
                    ) : (
                      <div className="message-text">{message.content}</div>
                    )}
                    {message.tool_calls?.map((call, n) => (
                      <details className="tool-call" key={`${call.name}-${n}`}>
                        <summary>
                          <span
                            className={`tool-outcome ${call.is_error ? "failed" : ""}`}
                          >
                            <span className="sr-only">
                              {call.is_error ? "Failed: " : "Completed: "}
                            </span>
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
              <Spinner
                label={
                  pendingDecision === "approve"
                    ? "Applying the approved change"
                    : pendingDecision === "cancel"
                      ? "Cancelling the proposed change"
                      : "Working with your connected tools"
                }
              />
            </div>
          )}
          {confirmation && (
            <section
              className="write-confirmation"
              aria-labelledby="approval-title"
            >
              <div className="confirmation-title">
                <ShieldCheck size={20} />
                <div>
                  <h3 id="approval-title" ref={approvalHeading} tabIndex={-1}>
                    Review this action
                  </h3>
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
                  disabled={busy || retryRemaining > 0}
                  onClick={() => void decide(false)}
                >
                  Cancel action
                </button>
                <button
                  className="button primary"
                  disabled={busy || retryRemaining > 0}
                  onClick={() => void decide(true)}
                >
                  <Check size={16} />
                  Approve & run
                </button>
              </div>
            </section>
          )}
        </div>
        <div className="composer-area">
          {showLatest && (
            <div className="chat-scroll-latest">
              <button
                type="button"
                className="button secondary small"
                onClick={() => {
                  followConversation.current = true;
                  conversation.current?.scrollTo({
                    top: conversation.current.scrollHeight,
                    behavior: "auto",
                  });
                  setShowLatest(false);
                  if (!confirmation && !busy)
                    textarea.current?.focus({ preventScroll: true });
                }}
              >
                <ArrowDown size={15} />
                Jump to latest
              </button>
            </div>
          )}
          {error && <ErrorBox message={error} />}
          {retryRemaining > 0 && (
            <p className="field-hint">
              Try again in {retryRemaining}{" "}
              {retryRemaining === 1 ? "second" : "seconds"}. Your message will
              not be sent automatically.
            </p>
          )}
          {failedRequest && (
            <button
              type="button"
              className="text-button"
              disabled={
                busy ||
                !!confirmation ||
                retryRemaining > 0 ||
                health?.chat_configured === false
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
              aria-describedby="chat-composer-hint"
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
                  retryRemaining > 0 ||
                  health?.chat_configured === false
                }
                aria-label="Send message"
              >
                <ArrowUp size={19} />
              </button>
            </div>
          </form>
          <div className="composer-note" id="chat-composer-hint">
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
        <h2>Connected tools</h2>
        <p className="context-description">
          Your assistant works with the tools available to your account.
        </p>
        <div className="context-connections">
          {serversLoading ? (
            <Spinner label="Checking connections" />
          ) : serversError ? (
            <p className="context-empty">
              Connection status could not be checked. Retry to see the current
              tools.
            </p>
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
                        ? `${availableTools(server).length} ${
                            availableTools(server).length === 1
                              ? "tool"
                              : "tools"
                          } available`
                        : "Connection unavailable"}
                  </span>
                </div>
                <span
                  className={`status-dot ${healthy(server) ? "is-good" : "is-warning"}`}
                  aria-hidden="true"
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
          <ArrowUpRight size={14} />
        </a>
        <div className="context-divider" />
        <div className="context-access">
          <ShieldCheck size={18} />
          <div>
            <strong>
              {user.role === "admin" ? "Administrator access" : "Member access"}
            </strong>
            <p>
              {user.role === "admin"
                ? "Write actions wait for your approval."
                : "Your role allows read tools only."}
            </p>
            <a href="#audit">
              View tool activity <ArrowRight size={13} />
            </a>
          </div>
        </div>
        <div className="context-bottom">
          <Badge
            tone={
              !serversError && !serversLoading && toolCount
                ? "good"
                : "warning-badge"
            }
          >
            {serversError
              ? "Tool status unavailable"
              : serversLoading
                ? "Checking tools"
                : `${toolCount} ${toolCount === 1 ? "tool" : "tools"} available`}
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
            followConversation.current = true;
            focusNewChat.current = true;
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
