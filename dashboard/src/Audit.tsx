import { useEffect, useState } from "react";
import {
  Activity,
  ChevronLeft,
  ChevronRight,
  Clock3,
  RefreshCw,
  Search,
} from "lucide-react";
import { date } from "./api";
import type { Audit, Member, Server, User } from "./api";
import {
  Badge,
  CopyButton,
  Empty,
  ErrorBox,
  Modal,
  PageHeading,
  Spinner,
  useResource,
} from "./ui";

export function AuditPage({
  user,
  servers,
}: {
  user: User;
  servers: Server[];
}) {
  const [outcome, setOutcome] = useState("");
  const [server, setServer] = useState("");
  const [person, setPerson] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Audit>();
  const params = new URLSearchParams({
    limit: "25",
    offset: String(offset),
    ...(outcome ? { outcome } : {}),
    ...(server ? { server_id: server } : {}),
    ...(person ? { user_id: person } : {}),
  });
  const records = useResource<{ items: Audit[]; total: number }>(
    `/audit?${params}`,
  );
  const members = useResource<Member[]>(
    user.role === "admin" ? "/members" : null,
  );
  useEffect(() => {
    if (!records.data || records.loading) return;
    const lastPage = Math.max(0, Math.ceil(records.data.total / 25) - 1) * 25;
    if (offset > lastPage) setOffset(lastPage);
  }, [records.data, records.loading, offset]);
  function filter(setter: (v: string) => void, value: string) {
    setter(value);
    setOffset(0);
  }
  return (
    <>
      <PageHeading
        eyebrow="TOOL CALL HISTORY"
        title="Activity"
        description={
          user.role === "admin"
            ? "A clear trail of tool calls across your team. See what ran, who ran it, and how it went."
            : "Your tool calls, in one place. See what ran, when it ran, and how it went."
        }
        action={
          <button
            className="button secondary"
            onClick={() => void records.refresh()}
            disabled={records.loading}
          >
            <RefreshCw size={16} className={records.loading ? "spin" : ""} />
            Refresh
          </button>
        }
      />
      <section className="panel audit-panel">
        <div className="audit-filters">
          <div>
            <Search size={16} />
            <strong>Filter activity</strong>
          </div>
          <label>
            <span className="sr-only">Filter by outcome</span>
            <select
              value={outcome}
              onChange={(e) => filter(setOutcome, e.target.value)}
            >
              <option value="">All outcomes</option>
              <option value="success">Successful</option>
              <option value="error">Connection error</option>
              <option value="tool_error">Tool error</option>
              <option value="timeout">Timed out</option>
              <option value="invalid_arguments">Invalid arguments</option>
              <option value="cancelled">Cancelled</option>
              <option value="pending">In progress</option>
              <option value="denied">Denied</option>
            </select>
          </label>
          <label>
            <span className="sr-only">Filter by connection</span>
            <select
              value={server}
              onChange={(e) => filter(setServer, e.target.value)}
            >
              <option value="">All connections</option>
              {servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          {user.role === "admin" && (
            <label>
              <span className="sr-only">Filter by teammate</span>
              <select
                value={person}
                disabled={members.loading || !!members.error}
                onChange={(e) => filter(setPerson, e.target.value)}
              >
                <option value="">All teammates</option>
                {members.data?.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {(outcome || server || person) && (
            <button
              className="text-button"
              onClick={() => {
                setOutcome("");
                setServer("");
                setPerson("");
                setOffset(0);
              }}
            >
              Clear
            </button>
          )}
        </div>
        {records.error && (
          <ErrorBox message={records.error} retry={records.refresh} />
        )}{" "}
        {members.error && (
          <ErrorBox
            message={`Teammate filters are unavailable. ${members.error}`}
            retry={members.refresh}
          />
        )}
        {records.loading && !records.data ? (
          <div className="loading-panel">
            <Spinner label="Loading activity" />
          </div>
        ) : records.data?.items.length === 0 ? (
          <Empty
            icon={<Activity size={28} />}
            title={
              outcome || server || person
                ? "No matching activity."
                : "No tool calls yet."
            }
          >
            <p>
              {outcome || server || person
                ? "Try another filter to find the calls you are looking for."
                : "Tool calls appear here after you use chat or an external MCP client."}
            </p>
          </Empty>
        ) : (
          <div
            className="table-scroll"
            tabIndex={0}
            role="region"
            aria-label="Tool call history"
          >
            <table className="audit-table">
              <thead>
                <tr>
                  <th>Tool call</th>
                  <th>Initiated by</th>
                  <th>Outcome</th>
                  <th>Duration</th>
                  <th>When</th>
                  <th>
                    <span className="sr-only">Details</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {records.data?.items.map((record) => (
                  <tr key={record.id}>
                    <td>
                      <code className="audit-tool">{record.tool_name}</code>
                      <span className="cell-subtitle">
                        {record.server_name || "Gateway"}
                      </span>
                    </td>
                    <td>
                      <strong>{record.user_name || "Unknown user"}</strong>
                      <span className="cell-subtitle">{record.user_email}</span>
                    </td>
                    <td>
                      <Badge
                        tone={
                          record.outcome === "success"
                            ? "good"
                            : ["denied", "pending"].includes(record.outcome)
                              ? "warning-badge"
                              : record.outcome === "cancelled"
                                ? "neutral"
                                : "bad"
                        }
                      >
                        {outcomeLabel(record.outcome)}
                      </Badge>
                    </td>
                    <td className="duration-cell">
                      <Clock3 size={13} />
                      {record.duration_ms.toLocaleString()} ms
                    </td>
                    <td className="nowrap">{date(record.timestamp)}</td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => setSelected(record)}
                        aria-label={`View details for ${record.tool_name}`}
                      >
                        Details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pagination">
          <span>
            {records.loading && !records.data
              ? "Loading activity..."
              : !records.data
                ? "Activity unavailable"
                : records.data.total
                  ? `${offset + 1} to ${Math.min(offset + 25, records.data.total)} of ${records.data.total} calls`
                  : "0 calls"}
          </span>
          <div>
            <button
              className="button secondary small"
              disabled={offset === 0 || records.loading}
              onClick={() => setOffset(Math.max(0, offset - 25))}
            >
              <ChevronLeft size={15} />
              Previous
            </button>
            <button
              className="button secondary small"
              disabled={
                !records.data ||
                offset + 25 >= records.data.total ||
                records.loading
              }
              onClick={() => setOffset(offset + 25)}
            >
              Next
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      </section>
      <p className="quiet-note">
        Activity reflects executed or denied gateway calls. Conversation text
        and secret credentials are not shown here.
      </p>
      {selected && (
        <Modal title="Tool call details" onClose={() => setSelected(undefined)}>
          <dl className="detail-list">
            <div>
              <dt>Tool</dt>
              <dd>
                <code>{selected.tool_name}</code>
              </dd>
            </div>
            <div>
              <dt>Connection</dt>
              <dd>{selected.server_name || "Gateway"}</dd>
            </div>
            <div>
              <dt>Initiated by</dt>
              <dd>
                {selected.user_name}
                <br />
                <span className="muted">{selected.user_email}</span>
              </dd>
            </div>
            <div>
              <dt>Time</dt>
              <dd>{date(selected.timestamp, true)}</dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd>{selected.duration_ms} ms</dd>
            </div>
            <div>
              <dt>Outcome</dt>
              <dd>{outcomeLabel(selected.outcome)}</dd>
            </div>
          </dl>
          {selected.error && <ErrorBox message={selected.error} />}
          <div className="request-id">
            <span className="small-label">REQUEST ID</span>
            <code>{selected.request_id}</code>
            <CopyButton value={selected.request_id} />
          </div>
        </Modal>
      )}
    </>
  );
}

function outcomeLabel(outcome: string) {
  return (
    (
      {
        success: "Success",
        denied: "Denied",
        error: "Connection error",
        tool_error: "Tool error",
        timeout: "Timed out",
        invalid_arguments: "Invalid arguments",
        cancelled: "Cancelled",
        pending: "In progress",
      } as Record<string, string>
    )[outcome] ?? outcome
  );
}
