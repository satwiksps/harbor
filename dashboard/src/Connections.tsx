import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  ChevronDown,
  Edit3,
  FileText,
  Network,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  ShoppingBag,
  Trash2,
  Truck,
} from "lucide-react";
import { api, date, errorMessage, healthy } from "./api";
import type { Server, User } from "./api";
import {
  Badge,
  ConfirmModal,
  Empty,
  ErrorBox,
  Modal,
  PageHeading,
  Spinner,
} from "./ui";

export function Connections({
  user,
  servers,
  loading,
  error,
  refresh,
  notify,
}: {
  user: User;
  servers?: Server[];
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [editing, setEditing] = useState<Server | "new">();
  const [deleting, setDeleting] = useState<Server>();
  const [pending, setPending] = useState<Set<string>>(new Set());
  const pendingActions = useRef(new Set<string>());
  const [actionError, setActionError] = useState("");
  const admin = user.role === "admin";
  function beginAction(id: string) {
    if (!admin || pendingActions.current.has(id)) return false;
    pendingActions.current.add(id);
    setPending(new Set(pendingActions.current));
    return true;
  }
  function endAction(id: string) {
    pendingActions.current.delete(id);
    setPending(new Set(pendingActions.current));
  }
  async function update(server: Server, action: "refresh" | "toggle") {
    const id = String(server.id);
    if (!beginAction(id)) return;
    setActionError("");
    try {
      await api(
        `/servers/${server.id}${action === "refresh" ? "/refresh" : ""}`,
        {
          method: action === "refresh" ? "POST" : "PATCH",
          ...(action === "toggle"
            ? { body: { enabled: !server.enabled } }
            : {}),
        },
      );
      await refresh();
      notify(
        action === "refresh"
          ? `${server.name} refreshed.`
          : `${server.name} ${server.enabled ? "paused" : "enabled"}.`,
      );
    } catch (e) {
      setActionError(errorMessage(e));
      await refresh();
    } finally {
      endAction(id);
    }
  }
  return (
    <>
      <PageHeading
        eyebrow="CONNECTED MCP SERVERS"
        title="Connections"
        description={
          admin
            ? "Connect your tools and choose who can use them."
            : "Review your team's connections and the tools you can access."
        }
        action={
          admin ? (
            <button
              className="button primary"
              onClick={() => setEditing("new")}
            >
              <Plus size={16} />
              Add connection
            </button>
          ) : (
            <Badge tone="neutral">Read-only access</Badge>
          )
        }
      />
      <div className="section-overview">
        <div>
          <Network size={18} />
          <strong>{servers ? servers.length : "..."}</strong>
          <span>registered connections</span>
        </div>
        <div>
          <span className="status-dot is-good" />
          <strong>{servers ? servers.filter(healthy).length : "..."}</strong>
          <span>available now</span>
        </div>
        <button
          className="text-button"
          onClick={() => void refresh()}
          disabled={loading}
        >
          <RefreshCw size={14} className={loading ? "spin" : ""} />
          Refresh view
        </button>
      </div>
      {error && <ErrorBox message={error} retry={refresh} />}{" "}
      {actionError && <ErrorBox message={actionError} />}
      {loading && !servers ? (
        <div className="panel loading-panel">
          <Spinner label="Loading connections" />
        </div>
      ) : servers?.length === 0 ? (
        <div className="panel">
          <Empty icon={<Network size={30} />} title="No connections yet.">
            <p>
              {admin
                ? "Connect an MCP server to make its tools available through the gateway."
                : "Ask an administrator to connect a server so your team can start using its tools."}
            </p>
            {admin && (
              <button
                className="button primary"
                onClick={() => setEditing("new")}
              >
                <Plus size={16} />
                Add connection
              </button>
            )}
          </Empty>
        </div>
      ) : (
        <div className="server-list">
          {servers?.map((server) => (
            <article className="server-card" key={server.id}>
              <div className="server-card-header">
                <div className="server-identity">
                  <div className="server-icon">
                    {server.slug.includes("woo") ? (
                      <ShoppingBag size={22} />
                    ) : server.slug.includes("shipping") ? (
                      <Truck size={22} />
                    ) : (
                      <FileText size={22} />
                    )}
                  </div>
                  <div>
                    <h2>{server.name}</h2>
                    <code className="server-url" title={server.url}>
                      {server.url}
                    </code>
                  </div>
                </div>
                <div className="server-header-actions">
                  <Badge
                    tone={
                      !server.enabled
                        ? "neutral"
                        : healthy(server)
                          ? "good"
                          : "warning-badge"
                    }
                  >
                    {!server.enabled
                      ? "Paused"
                      : healthy(server)
                        ? "Connected"
                        : server.status || "Unknown"}
                  </Badge>
                  {admin && (
                    <>
                      <button
                        className="icon-button"
                        title="Edit connection"
                        aria-label={`Edit ${server.name}`}
                        disabled={pending.has(String(server.id))}
                        onClick={() => setEditing(server)}
                      >
                        <Edit3 size={16} />
                      </button>
                      <button
                        className="icon-button"
                        title="Remove connection"
                        aria-label={`Remove ${server.name}`}
                        disabled={pending.has(String(server.id))}
                        onClick={() => setDeleting(server)}
                      >
                        <Trash2 size={16} />
                      </button>
                    </>
                  )}
                </div>
              </div>
              {server.last_error && (
                <div className="server-error">
                  <span className="status-dot is-warning" />
                  {server.last_error}
                </div>
              )}
              <div className="server-meta">
                <span>
                  <code>{server.slug}</code>
                  <span className="meta-separator" />
                  Streamable HTTP
                </span>
                <span>
                  {server.last_checked_at
                    ? `Last checked ${date(server.last_checked_at)}`
                    : "Not checked yet"}
                </span>
              </div>
              <details className="server-tools">
                <summary>
                  <span>
                    <ShieldCheck size={16} />
                    {server.tools?.length ?? 0} discovered{" "}
                    {server.tools?.length === 1 ? "tool" : "tools"}
                  </span>
                  <span>
                    {admin ? "Permissions & schemas" : "Access & schemas"}
                    <ChevronDown size={15} />
                  </span>
                </summary>
                <ToolPermissions
                  key={`${server.id}:${server.url}`}
                  server={server}
                  admin={admin}
                  refresh={refresh}
                  notify={notify}
                  pending={pending.has(String(server.id))}
                  beginAction={beginAction}
                  endAction={endAction}
                />
              </details>
              {admin && (
                <div className="server-footer">
                  <button
                    className="text-button"
                    disabled={pending.has(String(server.id))}
                    onClick={() => void update(server, "refresh")}
                  >
                    <RefreshCw
                      size={14}
                      className={pending.has(String(server.id)) ? "spin" : ""}
                    />
                    Check connection & discover tools
                  </button>
                  <button
                    className={`switch-label ${server.enabled ? "is-on" : ""}`}
                    role="switch"
                    aria-checked={server.enabled}
                    aria-label={`Enable ${server.name}`}
                    disabled={pending.has(String(server.id))}
                    onClick={() => void update(server, "toggle")}
                  >
                    <span className="switch-track">
                      <span />
                    </span>
                    {server.enabled ? "Enabled" : "Paused"}
                  </button>
                </div>
              )}
            </article>
          ))}
        </div>
      )}
      <div className="quiet-note">
        <ShieldCheck size={17} />
        <p>
          {admin
            ? "New tools start with administrator-only access. Review each tool before granting member access."
            : "Read access tools are available to members. Ask an administrator if you need a different tool or connection."}
        </p>
      </div>
      {editing && (
        <ServerEditor
          server={
            editing === "new"
              ? undefined
              : servers?.find((server) => server.id === editing.id) ?? editing
          }
          onClose={() => setEditing(undefined)}
          onFailed={refresh}
          onSaved={async () => {
            setEditing(undefined);
            await refresh();
            notify("Connection saved.");
          }}
        />
      )}{" "}
      {deleting && (
        <ConfirmModal
          title={`Remove ${deleting.name}?`}
          description="This connection and its tools will no longer be available through the gateway. Existing activity records are retained."
          label="Remove connection"
          onClose={() => setDeleting(undefined)}
          onConfirm={async () => {
            try {
              await api(`/servers/${deleting.id}`, { method: "DELETE" });
            } finally {
              await refresh();
            }
            notify("Connection removed.");
          }}
        />
      )}
    </>
  );
}

function ToolPermissions({
  server,
  admin,
  refresh,
  notify,
  pending,
  beginAction,
  endAction,
}: {
  server: Server;
  admin: boolean;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
  pending: boolean;
  beginAction: (id: string) => boolean;
  endAction: (id: string) => void;
}) {
  const [changes, setChanges] = useState(new Map<string, string>());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const tools = server.tools ?? [];
  const query = search.trim().toLowerCase();
  const visibleTools = tools.filter((tool) =>
    `${tool.name} ${tool.description ?? ""}`.toLowerCase().includes(query),
  );
  const changeCount = changes.size;
  useEffect(() => {
    setChanges((previous) =>
      new Map(
        [...previous].filter(([name, permission]) => {
          const tool = server.tools?.find(
            (candidate) => candidate.name === name,
          );
          return tool && permission !== (tool.permission ?? "admin");
        }),
      ),
    );
  }, [server.tools]);
  async function save() {
    const id = String(server.id);
    if (busy || pending || !changes.size || !beginAction(id)) return;
    setBusy(true);
    setError("");
    try {
      await api(`/servers/${server.id}/tools/permissions`, {
        method: "PATCH",
        body: { permissions: Object.fromEntries(changes) },
      });
      setChanges(new Map());
      await refresh();
      notify("Tool permissions updated.");
    } catch (e) {
      setError(errorMessage(e));
      await refresh();
    } finally {
      setBusy(false);
      endAction(id);
    }
  }
  return (
    <div className="tools-body">
      <p className="field-hint">
        {admin
          ? "Read access lets members and administrators use a tool. Write access is for administrators and requires approval in chat."
          : "You can use tools marked Read access. Other tools are reserved for administrators."}
      </p>
      {tools.length > 0 && (
        <div className="tools-toolbar">
          <label className="tools-search">
            <span className="sr-only">Search tools in {server.name}</span>
            <Search size={16} aria-hidden="true" />
            <input
              type="search"
              placeholder="Search tools"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <span className="tools-result-count" role="status">
            {query ? `${visibleTools.length} of ${tools.length}` : tools.length}{" "}
            {tools.length === 1 ? "tool" : "tools"}
          </span>
        </div>
      )}
      {visibleTools.length ? (
        visibleTools.map((tool) => (
          <div className="tool-row" key={tool.name}>
            <div>
              <code className="tool-name">{tool.name}</code>
              <p>
                {tool.description || "No description provided by this server."}
              </p>
              <details className="schema-details">
                <summary>Input schema</summary>
                <pre>{JSON.stringify(tool.inputSchema, null, 2)}</pre>
              </details>
            </div>
            {admin ? (
              <label className="permission-label">
                <span className="permission-caption" aria-hidden="true">
                  Access
                </span>
                <span className="sr-only">Permission for {tool.name}</span>
                <select
                  aria-label={`Permission for ${tool.name}`}
                  disabled={busy || pending}
                  value={changes.get(tool.name) ?? tool.permission ?? "admin"}
                  onChange={(e) => {
                    const value = e.target.value;
                    setChanges((previous) => {
                      const next = new Map(previous);
                      if (value === (tool.permission ?? "admin"))
                        next.delete(tool.name);
                      else next.set(tool.name, value);
                      return next;
                    });
                  }}
                >
                  <option value="admin" disabled>
                    Admin only
                  </option>
                  <option value="read">Read access</option>
                  <option value="write">Write access</option>
                </select>
              </label>
            ) : (
              <Badge tone="neutral">
                {tool.permission === "read" ? "Read access" : "Admin only"}
              </Badge>
            )}
          </div>
        ))
      ) : tools.length ? (
        <div className="tools-empty">
          <p>No tools match this search.</p>
          <button className="text-button" onClick={() => setSearch("")}>
            Clear search
          </button>
        </div>
      ) : (
        <p className="muted">
          {admin
            ? "No tools discovered yet. Check the connection to retrieve its tool catalog."
            : "No tools have been discovered yet. An administrator can check this connection."}
        </p>
      )}
      {error && <ErrorBox message={error} />}{" "}
      {changeCount > 0 && (
        <div className="tools-actions">
          <span className="tools-change-count" role="status">
            {changeCount} unsaved {changeCount === 1 ? "change" : "changes"}
          </span>
          <button
            className="button secondary small"
            disabled={busy || pending}
            onClick={() => setChanges(new Map())}
          >
            Discard changes
          </button>
          <button
            className="button primary small"
            disabled={busy || pending}
            onClick={() => void save()}
          >
            {busy ? <Spinner label="Saving" /> : "Save permissions"}
          </button>
        </div>
      )}
    </div>
  );
}

function ServerEditor({
  server,
  onClose,
  onSaved,
  onFailed,
}: {
  server?: Server;
  onClose: () => void;
  onSaved: () => Promise<void>;
  onFailed: () => Promise<void>;
}) {
  const [name, setName] = useState(server?.name ?? "");
  const [slug, setSlug] = useState(server?.slug ?? "");
  const [slugTouched, setSlugTouched] = useState(false);
  const [url, setUrl] = useState(server?.url ?? "");
  const [authType, setAuthType] = useState(
    server?.auth_type ?? (server ? "" : "none"),
  );
  const [credential, setCredential] = useState("");
  const [headerName, setHeaderName] = useState(server?.header_name ?? "");
  const [clearCredential, setClearCredential] = useState(false);
  const [enabled, setEnabled] = useState(server?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [error, setError] = useState("");
  const previousServer = useRef(server);
  useEffect(() => {
    const previous = previousServer.current;
    previousServer.current = server;
    if (!server || !previous) return;
    // Keep untouched fields current after a background refresh or a partial
    // failure, while retaining the administrator's explicit edits.
    setName((value) => (value === previous.name ? server.name : value));
    setUrl((value) => (value === previous.url ? server.url : value));
    setEnabled((value) => (value === previous.enabled ? server.enabled : value));
    setAuthType((value) =>
      value === previous.auth_type ? server.auth_type ?? "" : value,
    );
    setHeaderName((value) =>
      value === (previous.header_name ?? "") ? server.header_name ?? "" : value,
    );
    if (server.url !== previous.url) {
      setCredential("");
      setClearCredential(false);
      setError(
        "The endpoint changed while this dialog was open. Review the URL and authentication before saving.",
      );
    }
  }, [server]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      if (!name.trim()) throw new Error("Enter a connection name.");
      // Send only explicit edits. A stale dialog must not overwrite another
      // administrator's pause, endpoint, or authentication change.
      const body = {
        ...(!server || name.trim() !== server.name
          ? { name: name.trim() }
          : {}),
        ...(!server || url.trim() !== server.url ? { url: url.trim() } : {}),
        ...(!server || enabled !== server.enabled ? { enabled } : {}),
        ...(!server ? { slug } : {}),
        ...(authType && authType !== server?.auth_type
          ? { auth_type: authType }
          : {}),
        ...(authType !== "none" && credential ? { credential } : {}),
        ...(authType === "header" && headerName.trim() !== server?.header_name
          ? { header_name: headerName.trim() }
          : {}),
        ...(clearCredential ? { clear_credential: true } : {}),
      };
      if (!Object.keys(body).length) {
        onClose();
        return;
      }
      await api(server ? `/servers/${server.id}` : "/servers", {
        method: server ? "PATCH" : "POST",
        body,
      });
      await onSaved();
    } catch (e) {
      setError(errorMessage(e));
      await onFailed();
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <Modal
      title={server ? "Edit connection" : "Connect an MCP server"}
      busy={busy}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <form onSubmit={submit}>
        <p className="muted">
          Connect a Streamable HTTP endpoint. Credentials are stored securely
          and never returned to the browser.
        </p>
        <label>
          Connection name
          <input
            required
            disabled={busy}
            maxLength={100}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (!server && !slugTouched)
                setSlug(
                  e.target.value
                    .toLowerCase()
                    .replace(/[^a-z0-9]+/g, "-")
                    .replace(/^-|-$/g, "")
                    .slice(0, 24),
                );
            }}
            placeholder="Store policies"
            autoFocus
          />
        </label>
        <label>
          Namespace
          <input
            required
            pattern="[a-z][a-z0-9-]*"
            title="Start with a lowercase letter. Use lowercase letters, numbers, and hyphens."
            maxLength={24}
            disabled={busy || !!server}
            value={slug}
            onChange={(e) => {
              setSlugTouched(true);
              setSlug(e.target.value);
            }}
            placeholder="store-policies"
          />
          <span className="field-hint">
            A unique prefix for this server's tools. Cannot be changed later.
          </span>
        </label>
        <label>
          MCP endpoint
          <input
            required
            type="url"
            disabled={busy}
            maxLength={2048}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://tools.example.com/mcp"
          />
        </label>
        <label>
          Authentication
          <select
            disabled={busy}
            value={authType}
            onChange={(e) => {
              setAuthType(e.target.value);
              setCredential("");
              setClearCredential(false);
            }}
          >
            {server && <option value="">Keep current configuration</option>}
            <option value="none">No authentication</option>
            <option value="bearer">Bearer token</option>
            <option value="header">Custom header</option>
          </select>
        </label>
        {authType === "header" && (
          <label>
            Header name
            <input
              required
              disabled={busy}
              maxLength={100}
              value={headerName}
              onChange={(e) => setHeaderName(e.target.value)}
              placeholder="X-API-Key"
            />
          </label>
        )}
        {(authType === "bearer" ||
          authType === "header" ||
          (server && authType === "")) && (
          <label>
            {server ? "Replace credential (optional)" : "Credential"}
            <input
              type="password"
              maxLength={8192}
              autoComplete="new-password"
              required={!server}
              value={credential}
              onChange={(e) => {
                setCredential(e.target.value);
                setClearCredential(false);
              }}
              placeholder={
                server
                  ? "Leave blank to keep current credential"
                  : "Enter server credential"
              }
              disabled={busy || clearCredential}
            />
          </label>
        )}
        {server && (
          <label className="checkbox-label">
            <input
              type="checkbox"
              disabled={busy}
              checked={clearCredential}
              onChange={(e) => {
                setClearCredential(e.target.checked);
                setCredential("");
              }}
            />
            Remove saved credential
          </label>
        )}
        <label className="checkbox-label">
          <input
            type="checkbox"
            disabled={busy}
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Enable this connection
        </label>
        {error && <ErrorBox message={error} />}
        <div className="modal-actions">
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <button className="button primary" disabled={busy}>
            {busy ? (
              <Spinner label="Saving connection" />
            ) : server ? (
              "Save changes"
            ) : (
              "Connect server"
            )}
          </button>
        </div>
      </form>
    </Modal>
  );
}
