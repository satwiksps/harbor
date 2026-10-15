import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  CircleHelp,
  KeyRound,
  Layers3,
  LockKeyhole,
  LogOut,
  Menu,
  MessageSquare,
  Network,
  ShieldCheck,
  Store,
  Users,
  X,
} from "lucide-react";
import { api, ApiError, errorMessage, healthy, setCsrf } from "./api";
import type { AuthResponse, Health, Role, Server, User } from "./api";
import { Badge, ErrorBox, Logo, Spinner, Toast, useResource } from "./ui";
import { Chat } from "./Chat";
import { Connections } from "./Connections";
import { Team } from "./Team";
import { AuditPage } from "./Audit";

type Page = "chat" | "connections" | "team" | "audit";
const sessionChangeKey = "commerce-console:session-change";
function announceSessionChange() {
  try {
    localStorage.setItem(sessionChangeKey, crypto.randomUUID());
  } catch {
    // Focus refresh still checks the current cookie when storage is unavailable.
  }
}
const nav = [
  { id: "chat", title: "Chat", icon: MessageSquare },
  { id: "connections", title: "Connections", icon: Network },
  { id: "team", title: "Team & keys", icon: Users },
  { id: "audit", title: "Activity", icon: Activity },
] as const;
function currentPage(): Page {
  const hash = location.hash.slice(1);
  return nav.some((n) => n.id === hash) ? (hash as Page) : "chat";
}

export function App() {
  const [auth, setAuth] = useState<AuthResponse | null>();
  const [authError, setAuthError] = useState("");
  const [page, setPage] = useState<Page>(currentPage);
  const [toast, setToast] = useState("");
  const [mobile, setMobile] = useState(false);
  const authRequest = useRef(0);
  const closeToast = useCallback(() => setToast(""), []);
  const acceptToken =
    new URLSearchParams(location.search).get("token") ??
    (location.pathname.startsWith("/invite/")
      ? location.pathname.slice("/invite/".length)
      : null);
  const isInvite = location.pathname.includes("invite") && !!acceptToken;
  const isSignIn = location.pathname === "/login";
  const loadAuth = useCallback(async () => {
    const request = ++authRequest.current;
    setAuthError("");
    try {
      const response = await api<AuthResponse>("/auth/me");
      if (request !== authRequest.current) return;
      setCsrf(response.csrf_token);
      setAuth(response);
    } catch (e) {
      if (request !== authRequest.current) return;
      if (e instanceof ApiError && e.status === 401) {
        setCsrf("");
        setAuth(null);
      } else {
        setAuth((current) => current ?? null);
        setAuthError(errorMessage(e));
      }
    }
  }, []);
  const enterWorkspace = useCallback((response: AuthResponse) => {
    authRequest.current++;
    history.replaceState({}, "", "/");
    setCsrf(response.csrf_token);
    setAuth(response);
    setAuthError("");
    setPage("chat");
    setMobile(false);
    setToast(`Signed in as ${response.user.email}.`);
    announceSessionChange();
  }, []);
  useEffect(() => {
    if (isInvite || isSignIn) return;
    void loadAuth();
    const onStorage = (event: StorageEvent) => {
      if (event.key !== sessionChangeKey) return;
      setCsrf("");
      setAuth(undefined);
      setToast("");
      setMobile(false);
      void loadAuth();
    };
    const onFocus = () => void loadAuth();
    const onVisible = () => {
      if (document.visibilityState === "visible") void loadAuth();
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    const timer = setInterval(onVisible, 30000);
    return () => {
      clearInterval(timer);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
      authRequest.current++;
    };
  }, [loadAuth, isInvite, isSignIn]);
  useEffect(() => {
    const fn = () => {
      authRequest.current++;
      setAuth(null);
      setCsrf("");
      setAuthError("Your session has expired. Sign in to continue.");
    };
    window.addEventListener("session-expired", fn);
    return () => window.removeEventListener("session-expired", fn);
  }, []);
  useEffect(() => {
    const fn = () => {
      setPage(currentPage());
      setMobile(false);
    };
    window.addEventListener("hashchange", fn);
    return () => window.removeEventListener("hashchange", fn);
  }, []);
  if (isInvite)
    return (
      <InviteAcceptance token={acceptToken!} onAccepted={enterWorkspace} />
    );
  if (auth === undefined && !isSignIn)
    return (
      <div className="boot-screen">
        <Logo />
        <Spinner label="Opening your workspace" />
      </div>
    );
  if (!auth || isSignIn)
    return (
      <Login
        error={authError}
        switchingAccount={isSignIn}
        onLogin={enterWorkspace}
      />
    );
  return (
    <Workspace
      key={`${auth.user.id}:${auth.user.role}:${auth.csrf_token}`}
      user={auth.user}
      authError={authError}
      retryAuth={loadAuth}
      page={page}
      mobile={mobile}
      setMobile={setMobile}
      notify={setToast}
      onLogout={async () => {
        try {
          await api("/auth/logout", { method: "POST" });
          authRequest.current++;
          setCsrf("");
          setAuth(null);
          setToast("");
          announceSessionChange();
        } catch (e) {
          setToast(errorMessage(e));
        }
      }}
    >
      {toast && <Toast message={toast} close={closeToast} />}
    </Workspace>
  );
}

function Workspace({
  user,
  authError,
  retryAuth,
  page,
  mobile,
  setMobile,
  notify,
  onLogout,
  children,
}: {
  user: User;
  authError: string;
  retryAuth: () => Promise<void>;
  page: Page;
  mobile: boolean;
  setMobile: (v: boolean) => void;
  notify: (v: string) => void;
  onLogout: () => void;
  children: React.ReactNode;
}) {
  const health = useResource<Health>("/health");
  const servers = useResource<Server[]>("/servers");
  const sidebar = useRef<HTMLElement>(null);
  const navigationTrigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!mobile) return;
    const desktop = window.matchMedia("(min-width: 761px)");
    const onResize = () => {
      if (desktop.matches) setMobile(false);
    };
    const node = sidebar.current;
    node?.querySelector<HTMLButtonElement>("button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMobile(false);
      } else if (event.key === "Tab" && node) {
        const focusable = Array.from(
          node.querySelectorAll<HTMLElement>(
            'a[href], button:not([disabled]), [tabindex="0"]',
          ),
        ).filter((element) => element.getClientRects().length > 0);
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    onResize();
    desktop.addEventListener("change", onResize);
    node?.addEventListener("keydown", onKey);
    return () => {
      desktop.removeEventListener("change", onResize);
      node?.removeEventListener("keydown", onKey);
      if (navigationTrigger.current?.getClientRects().length)
        navigationTrigger.current.focus();
      else document.getElementById("main")?.focus();
    };
  }, [mobile, setMobile]);
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") {
        void health.refresh();
        void servers.refresh();
      }
    }, 30000);
    return () => clearInterval(timer);
  }, [health.refresh, servers.refresh]);
  const active = servers.data?.filter(healthy).length ?? 0;
  const enabled = servers.data?.filter((s) => s.enabled).length ?? 0;
  return (
    <div className="workspace">
      <a
        className="skip-link"
        href="#main"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        Skip to content
      </a>
      <aside
        ref={sidebar}
        className={`sidebar ${mobile ? "is-open" : ""}`}
        role={mobile ? "dialog" : undefined}
        aria-modal={mobile || undefined}
        aria-label={mobile ? "Workspace navigation" : undefined}
      >
        <div className="sidebar-brand">
          <Logo />
          <button
            className="icon-button mobile-only"
            aria-label="Close navigation"
            onClick={() => setMobile(false)}
          >
            <X size={20} />
          </button>
        </div>
        <div className="workspace-label">
          <span className="workspace-avatar">C</span>
          <div>
            <strong>Commerce workspace</strong>
            <span>Team environment</span>
          </div>
          <Layers3 size={16} />
        </div>
        <p className="nav-label">WORKSPACE</p>
        <nav aria-label="Main navigation">
          {nav.map((item) => (
            <a
              key={item.id}
              className={`nav-item ${page === item.id ? "active" : ""}`}
              href={`#${item.id}`}
              aria-current={page === item.id ? "page" : undefined}
              onClick={() => setMobile(false)}
            >
              <item.icon size={19} />
              {item.id === "team" && user.role !== "admin"
                ? "My API keys"
                : item.title}
              {item.id === "connections" && servers.data && (
                <span className="nav-count">{servers.data.length}</span>
              )}
            </a>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="gateway-card">
            <span className="small-label">GATEWAY STATUS</span>
            <div>
              <span
                className={`status-dot ${servers.error ? "is-warning" : active === enabled && enabled > 0 ? "is-good" : "is-warning"}`}
              />
              <strong>
                {servers.error
                  ? "Status unavailable"
                  : servers.data
                    ? `${active} of ${enabled} connected`
                    : "Checking connections"}
              </strong>
            </div>
            <a href="#connections" onClick={() => setMobile(false)}>
              {user.role === "admin"
                ? "Manage connections"
                : "View connections"}{" "}
              <ArrowUpRight size={14} />
            </a>
          </div>
          <div className="sidebar-note">
            <ShieldCheck size={16} />
            <span>Actions follow your team permissions.</span>
          </div>
          <button
            className="user-profile"
            onClick={onLogout}
            title={`Sign out of ${user.email}`}
            aria-label="Sign out"
          >
            <span className="avatar">
              {user.name.slice(0, 1).toUpperCase()}
            </span>
            <span>
              <strong>{user.name}</strong>
              <span className="profile-email" title={user.email}>
                {user.email}
              </span>
              <span>
                {user.role === "admin" ? "Administrator" : "Team member"}
              </span>
            </span>
            <LogOut size={17} />
          </button>
        </div>
      </aside>
      {mobile && (
        <button
          className="sidebar-overlay"
          aria-label="Close navigation backdrop"
          onClick={() => setMobile(false)}
        />
      )}
      <div className="main-shell" inert={mobile}>
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-only"
              ref={navigationTrigger}
              aria-label="Open navigation"
              aria-expanded={mobile}
              onClick={() => setMobile(true)}
            >
              <Menu size={20} />
            </button>
            <span>Workspace</span>
            <span className="breadcrumb-divider">/</span>
            <strong>
              {page === "team" && user.role !== "admin"
                ? "My API keys"
                : nav.find((n) => n.id === page)?.title}
            </strong>
          </div>
          <div className="topbar-actions">
            <Badge tone={user.role === "admin" ? "neutral" : "muted-badge"}>
              {user.role === "admin" ? "Admin access" : "Member access"}
            </Badge>
            {health.data?.store_url && (
              <a
                className="button secondary small"
                aria-label="Open store"
                href={health.data.store_url}
                target="_blank"
                rel="noreferrer"
              >
                <Store size={15} />
                <span>Open store</span>
                <ArrowUpRight size={14} />
              </a>
            )}
          </div>
        </header>
        <main
          id="main"
          tabIndex={-1}
          className={
            page === "chat" ? "main-content chat-main" : "main-content"
          }
        >
          {authError && (
            <ErrorBox
              message={`Could not verify your session. ${authError}`}
              retry={retryAuth}
            />
          )}
          {health.error && (
            <ErrorBox
              message={`Workspace health is unavailable. ${health.error}`}
              retry={health.refresh}
            />
          )}
          <div hidden={page !== "chat"} className="chat-container">
            <Chat
              user={user}
              health={health.data}
              servers={servers.data ?? []}
              serversLoading={servers.loading && !servers.data}
            />
          </div>
          {page === "connections" && (
            <Connections
              user={user}
              servers={servers.data}
              loading={servers.loading}
              error={servers.error}
              refresh={servers.refresh}
              notify={notify}
            />
          )}
          {page === "team" && (
            <Team user={user} health={health.data} notify={notify} />
          )}
          {page === "audit" && (
            <AuditPage user={user} servers={servers.data ?? []} />
          )}
        </main>
      </div>
      {children}
    </div>
  );
}

function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="auth-layout">
      <div className="auth-intro">
        <Logo />
        <div className="auth-story">
          <p className="eyebrow">CONNECTED COMMERCE</p>
          <h1>
            Your store. <br />
            Your tools. <br />
            <span>One workspace.</span>
          </h1>
          <p>
            Manage WooCommerce tools, teammate access, and AI actions in one
            workspace.
          </p>
          <div className="auth-principles">
            <span>
              <Network size={18} />
              One gateway for connected tools
            </span>
            <span>
              <ShieldCheck size={18} />
              Permission to act, built in
            </span>
            <span>
              <Activity size={18} />A clear record of every action
            </span>
          </div>
        </div>
        <div className="auth-footnote">
          Independent engineering project <span>Commerce console</span>
        </div>
      </div>
      <div className="auth-form-side">
        {children}
        <p className="auth-support">
          <CircleHelp size={15} />
          Need access? Ask your workspace administrator.
        </p>
      </div>
    </div>
  );
}

function Login({
  error: initialError,
  switchingAccount = false,
  onLogin,
}: {
  error: string;
  switchingAccount?: boolean;
  onLogin: (auth: AuthResponse) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      onLogin(
        await api<AuthResponse>("/auth/login", {
          method: "POST",
          body: { email, password },
        }),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthLayout>
      <form className="auth-form" onSubmit={submit}>
        <div className="auth-icon">
          <LockKeyhole size={22} />
        </div>
        <p className="eyebrow">WORKSPACE SIGN IN</p>
        <h2>Welcome back.</h2>
        <p className="muted">
          {switchingAccount
            ? "Sign in with the account you want to use in this browser. Other open workspace tabs will use this account too."
            : "Sign in to your commerce workspace."}
        </p>
        {(error || initialError) && (
          <ErrorBox message={error || initialError} />
        )}
        <label>
          Email address
          <input
            type="email"
            required
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            autoFocus
          />
        </label>
        <label>
          Password
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Enter your password"
          />
        </label>
        <button className="button primary full" disabled={busy}>
          {busy ? (
            <Spinner label="Signing in" />
          ) : (
            <>
              Sign in <ArrowRight size={17} />
            </>
          )}
        </button>
        <p className="form-note">
          Your session is protected. Your access stays yours.
        </p>
      </form>
    </AuthLayout>
  );
}

function InviteAcceptance({
  token,
  onAccepted,
}: {
  token: string;
  onAccepted: (auth: AuthResponse) => void;
}) {
  const invite = useResource<{ email: string; role: Role; expires_at: string }>(
    `/invites/${encodeURIComponent(token)}`,
  );
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await api<AuthResponse>("/auth/accept-invite", {
        method: "POST",
        body: { token, name: name.trim(), password },
      });
      setPassword("");
      onAccepted(response);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthLayout>
      <form className="auth-form" onSubmit={submit}>
        <div className="auth-icon">
          <KeyRound size={22} />
        </div>
        <p className="eyebrow">TEAM INVITATION</p>
        <h2>Join your workspace.</h2>
        {invite.loading && <Spinner label="Checking invitation" />}
        {invite.error && <ErrorBox message={invite.error} />}{" "}
        {invite.data && (
          <>
            <p className="muted">
              You have been invited as{" "}
              {invite.data.role === "admin"
                ? "an administrator"
                : "a team member"}
              .
            </p>
            <p className="notice invitation-session-note">
              Accepting signs you in as {invite.data.email} in this browser,
              including other open workspace tabs.
            </p>
            <label>
              Email address
              <input type="email" value={invite.data.email} disabled />
            </label>
            <label>
              Your name
              <input
                required
                minLength={2}
                maxLength={100}
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </label>
            <label>
              Create a password
              <input
                type="password"
                required
                minLength={12}
                maxLength={128}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <span className="field-hint">Use at least 12 characters.</span>
            </label>
            {error && <ErrorBox message={error} />}
            <button className="button primary full" disabled={busy}>
              {busy ? (
                <Spinner label="Creating account" />
              ) : (
                <>
                  Accept invitation
                  <ArrowRight size={17} />
                </>
              )}
            </button>
          </>
        )}
        <a className="text-button invitation-sign-in" href="/login">
          Already joined? Sign in to your account <ArrowRight size={15} />
        </a>
      </form>
    </AuthLayout>
  );
}
