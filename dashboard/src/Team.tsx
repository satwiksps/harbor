import { useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowUpRight,
  Code2,
  KeyRound,
  Plus,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { api, date, errorMessage } from "./api";
import type { ApiKey, Health, Invite, Member, Role, User } from "./api";
import {
  Badge,
  ConfirmModal,
  CopyButton,
  Empty,
  ErrorBox,
  Modal,
  PageHeading,
  Spinner,
  useResource,
} from "./ui";

export function Team({
  user,
  health,
  notify,
}: {
  user: User;
  health?: Health;
  notify: (message: string) => void;
}) {
  const admin = user.role === "admin";
  const members = useResource<Member[]>(admin ? "/members" : null);
  const keys = useResource<ApiKey[]>("/keys");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const [secret, setSecret] = useState("");
  const [revoke, setRevoke] = useState<ApiKey>();
  const [memberChange, setMemberChange] = useState<{
    member: Member;
    changes: { role?: Role; active?: boolean };
  }>();
  const [inviteRevision, setInviteRevision] = useState(0);
  const [connectionHelp, setConnectionHelp] = useState(false);
  const [showDeactivated, setShowDeactivated] = useState(false);
  const [showRevoked, setShowRevoked] = useState(false);
  const activeMembers = members.data?.filter((member) => member.active) ?? [];
  const deactivatedCount = (members.data?.length ?? 0) - activeMembers.length;
  const visibleMembers = showDeactivated ? members.data : activeMembers;
  const activeKeys = keys.data?.filter((key) => !key.revoked_at) ?? [];
  const revokedCount = (keys.data?.length ?? 0) - activeKeys.length;
  const visibleKeys = showRevoked ? keys.data : activeKeys;
  return (
    <>
      <PageHeading
        eyebrow={admin ? "TEAM ACCESS" : "PERSONAL ACCESS"}
        title={admin ? "Team & keys" : "My API keys"}
        description={
          admin
            ? "Manage workspace roles, invitations, and revocable API keys."
            : "Connect your editor or another MCP client to your team's tools."
        }
        action={
          admin && (
            <button
              className="button primary"
              onClick={() => setInviteOpen(true)}
            >
              <Plus size={16} />
              Invite teammate
            </button>
          )
        }
      />
      {admin && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>
                Workspace members{" "}
                {members.data && (
                  <span className="count-bubble">{visibleMembers?.length}</span>
                )}
              </h2>
              <p>Manage who can use this workspace and what they can do.</p>
            </div>
            <button
              className="text-button"
              aria-label="Refresh teammates"
              disabled={members.loading}
              onClick={() => void members.refresh()}
            >
              <RefreshCw size={15} className={members.loading ? "spin" : ""} />
              Refresh
            </button>
          </div>
          {deactivatedCount > 0 && (
            <div className="panel-footnote">
              <button
                className="text-button"
                aria-pressed={showDeactivated}
                onClick={() => setShowDeactivated((value) => !value)}
              >
                {showDeactivated
                  ? "Hide deactivated members"
                  : `Show deactivated members (${deactivatedCount})`}
              </button>
            </div>
          )}
          {members.error && (
            <ErrorBox message={members.error} retry={members.refresh} />
          )}{" "}
          {!!visibleMembers?.length && (
            <p className="table-scroll-hint management-scroll-hint">
              Scroll sideways for roles, status and actions.
            </p>
          )}
          {members.loading && !members.data ? (
            <div className="loading-panel">
              <Spinner label="Loading teammates" />
            </div>
          ) : members.data && visibleMembers?.length === 0 ? (
            <div className="simple-empty">
              {deactivatedCount ? "No active members." : "No members found."}
            </div>
          ) : members.data ? (
            <div
              className="table-scroll"
              tabIndex={0}
              role="region"
              aria-label="Workspace members"
            >
              <table className="management-table members-table" role="table">
                <thead>
                  <tr>
                    <th>Member</th>
                    <th>Role</th>
                    <th>Status</th>
                    {admin && (
                      <th>
                        <span className="sr-only">Actions</span>
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {visibleMembers?.map((member) => (
                    <tr key={member.id}>
                      <td data-label="Member">
                        <div className="person-cell">
                          <span className="avatar" aria-hidden="true">
                            {member.name.slice(0, 1).toUpperCase()}
                          </span>
                          <span>
                            <strong>
                              {member.name}
                              {member.id === user.id && <small> (you)</small>}
                            </strong>
                            <span>{member.email}</span>
                          </span>
                        </div>
                      </td>
                      <td data-label="Role">
                        {admin && member.id !== user.id ? (
                          <select
                            aria-label={`Role for ${member.name}`}
                            className="inline-select"
                            value={member.role}
                            onChange={(e) =>
                              setMemberChange({
                                member,
                                changes: { role: e.target.value as Role },
                              })
                            }
                          >
                            <option value="member">Member</option>
                            <option value="admin">Administrator</option>
                          </select>
                        ) : (
                          <span className="role-text">
                            {member.role === "admin"
                              ? "Administrator"
                              : "Member"}
                          </span>
                        )}
                      </td>
                      <td data-label="Status">
                        <Badge tone={member.active ? "good" : "neutral"}>
                          {member.active ? "Active" : "Deactivated"}
                        </Badge>
                      </td>
                      {admin && (
                        <td className="align-right" data-label="Access">
                          {member.id !== user.id && (
                            <button
                              className="text-button"
                              aria-label={`${member.active ? "Deactivate" : "Reactivate"} ${member.name}`}
                              onClick={() =>
                                setMemberChange({
                                  member,
                                  changes: { active: !member.active },
                                })
                              }
                            >
                              {member.active ? "Deactivate" : "Reactivate"}
                            </button>
                          )}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <div className="panel-footnote">
            <ShieldCheck size={15} />
            <span>
              Members can use read tools. Administrators manage connections,
              permissions, and write actions.
            </span>
          </div>
        </section>
      )}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>
              API keys{" "}
              {keys.data && (
                <span className="count-bubble">{visibleKeys?.length}</span>
              )}
            </h2>
            <p>
              {admin
                ? "Manage keys for your team. Each key follows its owner's permissions."
                : "Create a separate key for each client. Revoke a key to stop its access."}
            </p>
          </div>
          <div className="server-header-actions">
            <button
              className="text-button"
              aria-label="Refresh API keys"
              disabled={keys.loading}
              onClick={() => void keys.refresh()}
            >
              <RefreshCw size={15} className={keys.loading ? "spin" : ""} />
              Refresh
            </button>
            <button
              className="button secondary small"
              onClick={() => setKeyOpen(true)}
            >
              <Plus size={15} />
              Create key
            </button>
          </div>
        </div>
        {revokedCount > 0 && (
          <div className="panel-footnote">
            <button
              className="text-button"
              aria-pressed={showRevoked}
              onClick={() => setShowRevoked((value) => !value)}
            >
              {showRevoked
                ? "Hide revoked keys"
                : `Show revoked keys (${revokedCount})`}
            </button>
          </div>
        )}
        {keys.error && <ErrorBox message={keys.error} retry={keys.refresh} />}{" "}
        {!!visibleKeys?.length && (
          <p className="table-scroll-hint management-scroll-hint">
            Scroll sideways for key details and actions.
          </p>
        )}
        {keys.loading && !keys.data ? (
          <div className="loading-panel">
            <Spinner label="Loading API keys" />
          </div>
        ) : keys.data && visibleKeys?.length === 0 ? (
          <Empty
            icon={<KeyRound size={26} />}
            title={revokedCount ? "No active keys." : "No keys yet."}
          >
            <p>
              Create a key to use your team's tools from an external MCP client.
            </p>
          </Empty>
        ) : keys.data ? (
          <div
            className="table-scroll"
            tabIndex={0}
            role="region"
            aria-label="API keys"
          >
            <table className="management-table keys-table" role="table">
              <thead>
                <tr>
                  <th>Key</th>
                  {admin && <th>Owner</th>}
                  <th>Created</th>
                  <th>Last used</th>
                  <th>Status</th>
                  <th>
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleKeys?.map((key) => (
                  <tr key={key.id}>
                    <td data-label="Key">
                      <div className="key-name">
                        <KeyRound size={15} />
                        <strong>{key.name}</strong>
                      </div>
                      <code className="key-prefix">{key.prefix}••••</code>
                    </td>
                    {admin && (
                      <td data-label="Owner">
                        {members.data?.find(
                          (member) => member.id === key.user_id,
                        )?.name ?? `User ${key.user_id}`}
                      </td>
                    )}
                    <td className="nowrap" data-label="Created">
                      {date(key.created_at)}
                    </td>
                    <td className="nowrap" data-label="Last used">
                      {key.last_used_at ? date(key.last_used_at) : "Never used"}
                    </td>
                    <td data-label="Status">
                      <Badge
                        tone={
                          key.revoked_at
                            ? "neutral"
                            : members.data?.some(
                                  (member) =>
                                    member.id === key.user_id && !member.active,
                                )
                              ? "warning-badge"
                              : "good"
                        }
                      >
                        {key.revoked_at
                          ? "Revoked"
                          : members.data?.some(
                                (member) =>
                                  member.id === key.user_id && !member.active,
                              )
                            ? "Owner inactive"
                            : "Active"}
                      </Badge>
                    </td>
                    <td data-label="Access">
                      {!key.revoked_at && (
                        <button
                          className="text-button danger-text"
                          aria-label={`Revoke ${key.name}`}
                          onClick={() => setRevoke(key)}
                        >
                          Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <div className="key-help">
          <Code2 size={18} />
          <div>
            <strong>Bring your own MCP client.</strong>
            <p>
              Use the same tools and permissions from your editor or another
              assistant.
            </p>
          </div>
          <button
            className="text-button"
            onClick={() => setConnectionHelp(true)}
          >
            Connection details
            <ArrowUpRight size={15} />
          </button>
        </div>
      </section>
      {admin && <Invitations revision={inviteRevision} />}
      {inviteOpen && (
        <InviteModal
          onClose={() => setInviteOpen(false)}
          onCreated={() => {
            setInviteRevision((v) => v + 1);
            notify("Invitation created. Share the link with your teammate.");
          }}
        />
      )}{" "}
      {keyOpen && (
        <KeyModal
          user={user}
          members={members.data ?? []}
          onClose={() => setKeyOpen(false)}
          onCreated={async (key) => {
            setKeyOpen(false);
            setSecret(key);
            await keys.refresh();
          }}
        />
      )}{" "}
      {secret && (
        <Modal title="Save your new API key" onClose={() => setSecret("")}>
          <div className="notice warning">
            <KeyRound size={18} />
            <span>
              This is the only time the full key is shown. Store it somewhere
              safe before closing.
            </span>
          </div>
          <div className="secret-box">
            <code>{secret}</code>
            <CopyButton value={secret} label="Copy key" />
          </div>
          <p className="muted">
            Treat this key like a password. It acts with the owner's current
            permissions.
          </p>
          <div className="modal-actions">
            <button className="button primary" onClick={() => setSecret("")}>
              I have saved my key
            </button>
          </div>
        </Modal>
      )}{" "}
      {revoke && (
        <ConfirmModal
          title={`Revoke ${revoke.name}?`}
          description="Any client using this key will lose access immediately. This cannot be undone. You can create a replacement key."
          label="Revoke key"
          onClose={() => setRevoke(undefined)}
          onConfirm={async () => {
            await api(`/keys/${revoke.id}`, { method: "DELETE" });
            await keys.refresh();
            notify("API key revoked.");
          }}
        />
      )}{" "}
      {memberChange && (
        <ConfirmModal
          danger={
            memberChange.changes.role === "member" ||
            memberChange.changes.active === false
          }
          title={
            memberChange.changes.role
              ? `Change ${memberChange.member.name}'s role?`
              : `${memberChange.changes.active ? "Reactivate" : "Deactivate"} ${memberChange.member.name}?`
          }
          description={
            memberChange.changes.role === "admin"
              ? "Administrators can manage teammates, connections, credentials, and write tools."
              : memberChange.changes.role === "member"
                ? "This member will lose administrator privileges, including access to write tools."
                : memberChange.changes.active
                  ? "This person will be able to sign in and use their assigned permissions again."
                  : "This person will lose access to the workspace and gateway."
          }
          label={
            memberChange.changes.role
              ? "Change role"
              : memberChange.changes.active
                ? "Reactivate member"
                : "Deactivate member"
          }
          onClose={() => setMemberChange(undefined)}
          onConfirm={async () => {
            await api(`/members/${memberChange.member.id}`, {
              method: "PATCH",
              body: memberChange.changes,
            });
            await members.refresh();
            notify("Team access updated.");
          }}
        />
      )}{" "}
      {connectionHelp && (
        <Modal
          title="Connect your MCP client"
          wide
          onClose={() => setConnectionHelp(false)}
        >
          <p className="muted">
            Choose a client that supports Streamable HTTP. Set its server URL
            and an Authorization header using your own API key.
          </p>
          <label>
            Gateway endpoint
            <div className="copy-field">
              <input
                readOnly
                value={health?.gateway_url || `${location.origin}/mcp`}
              />
              <CopyButton
                value={health?.gateway_url || `${location.origin}/mcp`}
              />
            </div>
          </label>
          <div className="code-card">
            <div className="code-heading">
              <span>Example MCP configuration</span>
              <CopyButton
                value={JSON.stringify(
                  {
                    mcpServers: {
                      harbor: {
                        type: "http",
                        url: health?.gateway_url || `${location.origin}/mcp`,
                        headers: { Authorization: "Bearer YOUR_API_KEY" },
                      },
                    },
                  },
                  null,
                  2,
                )}
              />
            </div>
            <pre>
              {JSON.stringify(
                {
                  mcpServers: {
                    harbor: {
                      type: "http",
                      url: health?.gateway_url || `${location.origin}/mcp`,
                      headers: { Authorization: "Bearer YOUR_API_KEY" },
                    },
                  },
                },
                null,
                2,
              )}
            </pre>
          </div>
          <p className="field-hint">
            Replace YOUR_API_KEY locally. Configuration shape can vary by
            client. Never commit your real key.
          </p>
        </Modal>
      )}
    </>
  );
}

function Invitations({ revision }: { revision: number }) {
  const invites = useResource<Invite[]>(`/invites?revision=${revision}`);
  const [showHistory, setShowHistory] = useState(false);
  const pending =
    invites.data?.filter(
      (invite) =>
        !invite.accepted_at &&
        new Date(invite.expires_at).getTime() > Date.now(),
    ) ?? [];
  const historyCount = (invites.data?.length ?? 0) - pending.length;
  const visibleInvites = showHistory ? invites.data : pending;
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>Invitations</h2>
          <p>Share invitations directly. This workspace does not send email.</p>
        </div>
        <button
          className="text-button"
          aria-label="Refresh invitations"
          disabled={invites.loading}
          onClick={() => void invites.refresh()}
        >
          <RefreshCw size={15} className={invites.loading ? "spin" : ""} />
          Refresh
        </button>
      </div>
      {historyCount > 0 && (
        <div className="panel-footnote">
          <button
            className="text-button"
            aria-pressed={showHistory}
            onClick={() => setShowHistory((value) => !value)}
          >
            {showHistory
              ? "Hide invitation history"
              : `Show invitation history (${historyCount})`}
          </button>
        </div>
      )}
      {invites.error && (
        <ErrorBox message={invites.error} retry={invites.refresh} />
      )}{" "}
      {!!visibleInvites?.length && (
        <p className="table-scroll-hint management-scroll-hint">
          Scroll sideways for invitation details.
        </p>
      )}
      {invites.loading && !invites.data ? (
        <div className="loading-panel">
          <Spinner label="Loading invitations" />
        </div>
      ) : invites.data && visibleInvites?.length === 0 ? (
        <div className="simple-empty">
          {historyCount
            ? "No pending invitations."
            : "No invitations have been created."}
        </div>
      ) : invites.data ? (
        <div
          className="table-scroll"
          tabIndex={0}
          role="region"
          aria-label="Invitations"
        >
          <table className="management-table invitations-table" role="table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th>Expires</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {visibleInvites?.map((invite) => (
                <tr key={invite.id}>
                  <td data-label="Email">{invite.email}</td>
                  <td className="role-text" data-label="Role">
                    {invite.role === "admin" ? "Administrator" : "Member"}
                  </td>
                  <td data-label="Expires">{date(invite.expires_at, true)}</td>
                  <td data-label="Status">
                    <Badge
                      tone={
                        invite.accepted_at
                          ? "good"
                          : new Date(invite.expires_at) < new Date()
                            ? "neutral"
                            : "warning-badge"
                      }
                    >
                      {invite.accepted_at
                        ? "Accepted"
                        : new Date(invite.expires_at) < new Date()
                          ? "Expired"
                          : "Pending"}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function InviteModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<{ invite_url: string }>("/invites", {
        method: "POST",
        body: { email, role },
      });
      setUrl(new URL(result.invite_url, location.origin).toString());
      onCreated();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={url ? "Your invitation is ready" : "Invite a teammate"}
      busy={busy}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      {url ? (
        <>
          <p className="muted">
            Share this private link with <strong>{email}</strong>. It lets them
            set their name and password.
          </p>
          <div className="secret-box">
            <code>{url}</code>
            <CopyButton value={url} label="Copy invitation" />
          </div>
          <div className="modal-actions">
            <button className="button primary" onClick={onClose}>
              Done
            </button>
          </div>
        </>
      ) : (
        <form onSubmit={submit}>
          <p className="muted">
            Create a personal invitation link for your teammate.
          </p>
          <label>
            Email address
            <input
              type="email"
              maxLength={254}
              required
              value={email}
              autoFocus
              onChange={(e) => setEmail(e.target.value)}
              placeholder="teammate@company.com"
            />
          </label>
          <label>
            Workspace role
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as Role)}
            >
              <option value="member">Member</option>
              <option value="admin">Administrator</option>
            </select>
            <span className="field-hint">
              {role === "member"
                ? "Can use read tools and manage their own API keys."
                : "Can manage the team, connections, and write tools."}
            </span>
          </label>
          {error && <ErrorBox message={error} />}
          <div className="modal-actions">
            <button
              type="button"
              className="button secondary"
              onClick={onClose}
              disabled={busy}
            >
              Cancel
            </button>
            <button className="button primary" disabled={busy}>
              {busy ? (
                <Spinner label="Creating invitation" />
              ) : (
                "Create invitation"
              )}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}
function KeyModal({
  user,
  members,
  onClose,
  onCreated,
}: {
  user: User;
  members: Member[];
  onClose: () => void;
  onCreated: (key: string) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [owner, setOwner] = useState(String(user.id));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const owners = members.filter((member) => member.active);
  if (!owners.some((member) => String(member.id) === String(user.id)))
    owners.unshift({ ...user, active: true, created_at: "" });
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (!name.trim()) throw new Error("Enter a key name.");
      const selectedOwner = owners.find(
        (member) => String(member.id) === owner,
      );
      if (user.role === "admin" && !selectedOwner)
        throw new Error("Choose an active key owner before continuing.");
      const result = await api<{ key: string; record: ApiKey }>("/keys", {
        method: "POST",
        body: {
          name: name.trim(),
          ...(user.role === "admin" && owner !== String(user.id)
            ? { user_id: selectedOwner!.id }
            : {}),
        },
      });
      await onCreated(result.key);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Create an API key"
      busy={busy}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <form onSubmit={submit}>
        <p className="muted">
          Use a descriptive name so you know where this key is used.
        </p>
        <label>
          Key name
          <input
            required
            minLength={2}
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My editor"
            autoFocus
          />
        </label>
        {user.role === "admin" && (
          <label>
            Key owner
            <select value={owner} onChange={(e) => setOwner(e.target.value)}>
              {owners.map((member) => (
                <option value={String(member.id)} key={member.id}>
                  {member.name}
                  {member.id === user.id ? " (you)" : ""}
                </option>
              ))}
            </select>
            <span className="field-hint">
              The key inherits this person's current role and permissions.
            </span>
          </label>
        )}
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
            {busy ? <Spinner label="Creating key" /> : "Create key"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
