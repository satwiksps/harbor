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
import type { Audit, AuditSummary, AuditWindow, Member, Server, User } from "./api";
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
  const [window, setWindow] = useState<AuditWindow>("24h");
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
  const summaryParams = new URLSearchParams({
    window,
    ...(server ? { server_id: server } : {}),
    ...(person ? { user_id: person } : {}),
  });
  const summary = useResource<AuditSummary>(`/audit/summary?${summaryParams}`);
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
        eyebrow="TOOL CALLS & PERFORMANCE"
        title="Activity"
        description={
          user.role === "admin"
            ? "A clear trail of tool calls across your team. See what ran, who ran it, and how it went."
            : "Your tool calls, in one place. See what ran, when it ran, and how it went."
        }
        action={
          <button
            className="button secondary"
            onClick={() => {
              void records.refresh();
              void summary.refresh();
            }}
            disabled={records.loading || summary.loading}
          >
            <RefreshCw
              size={16}
              className={records.loading || summary.loading ? "spin" : ""}
            />
            Refresh
          </button>
        }
      />
      <section
        className="panel activity-overview"
        aria-labelledby="activity-overview-title"
        aria-busy={summary.loading}
      >
        <div className="activity-overview-heading">
          <div>
            <h2 id="activity-overview-title">Activity overview</h2>
            <p>
              {user.role === "admin" ? "Your team's" : "Your"} gateway calls,
              failures and response times.
            </p>
          </div>
          <div
            className="activity-window"
            role="group"
            aria-label="Overview time window"
          >
            {(["1h", "24h", "7d"] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-label={windowLabel(value)}
                aria-pressed={window === value}
                onClick={() => setWindow(value)}
              >
                {{ "1h": "1 hour", "24h": "24 hours", "7d": "7 days" }[value]}
              </button>
            ))}
          </div>
        </div>
        <div className="audit-filters activity-shared-filters">
          <div>
            <Search size={16} />
            <strong>Overview & history</strong>
          </div>
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
          {(server || person) && (
            <button
              className="text-button"
              onClick={() => {
                setServer("");
                setPerson("");
                setOffset(0);
              }}
            >
              Clear filters
            </button>
          )}
        </div>
        {members.error && (
          <ErrorBox
            message={`Teammate filters are unavailable. ${members.error}`}
            retry={members.refresh}
          />
        )}
        {summary.error ? (
          <ErrorBox
            message={`Activity overview is unavailable. ${summary.error}`}
            retry={summary.refresh}
          />
        ) : summary.data ? (
          <ActivityOverview data={summary.data} updating={summary.loading} />
        ) : (
          <div className="loading-panel">
            <Spinner label="Loading activity overview" />
          </div>
        )}
      </section>
      <section
        className="panel audit-panel"
        aria-labelledby="activity-history-title"
      >
        <div className="audit-filters activity-history-heading">
          <div>
            <h2 id="activity-history-title">Tool call history</h2>
            <span>All time</span>
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
          {outcome && (
            <button
              className="text-button"
              onClick={() => filter(setOutcome, "")}
            >
              Clear outcome
            </button>
          )}
        </div>
        {records.error && (
          <ErrorBox message={records.error} retry={records.refresh} />
        )}
        {!!records.data?.items.length && (
          <p className="table-scroll-hint">
            Scroll sideways for outcomes, timing and details.
          </p>
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
                      {record.outcome === "pending"
                        ? "In progress"
                        : `${record.duration_ms.toLocaleString()} ms`}
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
        Activity records gateway tool calls and their outcomes. Conversation
        text and secret credentials are not shown here.
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
              <dd>
                {selected.outcome === "pending"
                  ? "In progress"
                  : `${selected.duration_ms.toLocaleString()} ms`}
              </dd>
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

function ActivityOverview({
  data,
  updating,
}: {
  data: AuditSummary;
  updating: boolean;
}) {
  const totals = data.totals;
  const outcomes = Object.entries(totals.outcomes).filter(([, count]) => count > 0);
  return (
    <>
      <div className="activity-summary-meta">
        <span>
          {windowLabel(data.window)} ·{" "}
          {data.scope === "self"
            ? "Your activity"
            : data.user_id !== null
              ? "Selected teammate"
              : "Team activity"}
        </span>
        <span>
          {updating ? (
            <Spinner label="Updating overview" />
          ) : (
            <>
              As of <time dateTime={data.to}>{date(data.to)}</time>
            </>
          )}
        </span>
      </div>
      <dl className="activity-metrics">
        <div>
          <dt>Total calls</dt>
          <dd>
            <span className="activity-metric-value">
              {totals.calls.toLocaleString()}
            </span>
            <span className="activity-metric-note">
              All outcomes in this window
            </span>
          </dd>
        </div>
        <div>
          <dt>Failure rate</dt>
          <dd>
            <span
              className={`activity-metric-value ${totals.failed_calls > 0 ? "has-failures" : ""}`}
            >
              {failureRate(totals.failure_rate)}
            </span>
            <span className="activity-metric-note">
              {totals.completed_calls
                ? `${totals.failed_calls.toLocaleString()} of ${totals.completed_calls.toLocaleString()} completed calls`
                : "No completed calls"}
            </span>
          </dd>
        </div>
        <div>
          <dt>Median latency</dt>
          <dd>
            <span className="activity-metric-value">
              {latency(totals.p50_latency_ms)}
            </span>
            <span className="activity-metric-note">
              {sampleLabel(totals.latency_samples)}
            </span>
          </dd>
        </div>
        <div>
          <dt>p95 latency</dt>
          <dd>
            <span className="activity-metric-value">
              {latency(totals.p95_latency_ms)}
            </span>
            <span className="activity-metric-note">
              {sampleLabel(totals.latency_samples)}
            </span>
          </dd>
        </div>
      </dl>
      {outcomes.length > 0 && (
        <div
          className="activity-outcomes"
          aria-label="Call outcomes in this window"
        >
          <span className="activity-outcomes-label">Outcomes</span>
          <ul>
            {outcomes.map(([outcome, count]) => (
              <li key={outcome}>
                <span
                  className={`activity-outcome-dot ${
                    outcome === "success"
                      ? "is-good"
                      : ["error", "tool_error", "timeout"].includes(outcome)
                        ? "is-failure"
                        : ""
                  }`}
                  aria-hidden="true"
                />
                {outcomeLabel(outcome)} <strong>{count.toLocaleString()}</strong>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="activity-connections-heading">
        <h3>By connection</h3>
        <span>End-to-end gateway latency</span>
      </div>
      {data.servers.length > 0 ? (
        <>
          <div
            className="table-scroll activity-connections"
            tabIndex={0}
            role="region"
            aria-label="Activity metrics by connection"
          >
            <table>
              <thead>
                <tr>
                  <th scope="col">Connection</th>
                  <th scope="col">Calls</th>
                  <th scope="col">Failure rate</th>
                  <th scope="col">Median</th>
                  <th scope="col">p95</th>
                  <th scope="col">Samples</th>
                </tr>
              </thead>
              <tbody>
                {data.servers.map((connection) => (
                  <tr key={connection.server_id ?? "unattributed"}>
                    <th scope="row">
                      {connection.server_name}
                      {connection.server_deleted && (
                        <span className="cell-subtitle">Removed connection</span>
                      )}
                      {connection.server_id === null && (
                        <span className="cell-subtitle">
                          No connection recorded
                        </span>
                      )}
                    </th>
                    <td>{connection.calls.toLocaleString()}</td>
                    <td>
                      <span
                        className={connection.failed_calls > 0 ? "has-failures" : ""}
                      >
                        {failureRate(connection.failure_rate)}
                      </span>
                      <span className="cell-subtitle">
                        {connection.completed_calls
                          ? `${connection.failed_calls.toLocaleString()} of ${connection.completed_calls.toLocaleString()} completed`
                          : "No completed calls"}
                      </span>
                    </td>
                    <td>{latency(connection.p50_latency_ms)}</td>
                    <td>{latency(connection.p95_latency_ms)}</td>
                    <td>{connection.latency_samples.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="activity-scroll-hint">
            Scroll sideways to see every metric.
          </p>
        </>
      ) : (
        <div className="activity-summary-empty">
          <Activity size={22} aria-hidden="true" />
          <div>
            <strong>No calls in this window.</strong>
            <p>Choose a longer window or change your filters.</p>
          </div>
        </div>
      )}
      <details className="activity-method">
        <summary>How these metrics are measured</summary>
        <div>
          <p>
            Failure rate counts connection errors, tool errors and timeouts out
            of completed calls. Completed calls include successes and these
            failures. Denied, invalid, cancelled and in-progress calls appear in
            the total but do not contribute to failure rate or latency.
          </p>
          <p>
            Latency measures the full gateway call, including connection setup
            and tool execution. It is not the upstream server&apos;s processing
            time alone. Median and p95 use completed calls with a recorded
            duration; small samples can vary widely. p95 estimates the duration
            at the 95th percentile.
          </p>
          <p>
            This overview includes calls started between{" "}
            <time dateTime={data.from}>{date(data.from, true)}</time> to{" "}
            <time dateTime={data.to}>{date(data.to, true)}</time>. The outcome
            filter applies only to the all-time history below.
          </p>
        </div>
      </details>
    </>
  );
}

function windowLabel(window: AuditWindow) {
  return { "1h": "Last hour", "24h": "Last 24 hours", "7d": "Last 7 days" }[window];
}

function failureRate(value: number | null) {
  if (value === null) return "No data";
  if (value > 0 && value < 0.001) return "<0.1%";
  return `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}

function latency(value: number | null) {
  if (value === null) return "No data";
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ms`;
}

function sampleLabel(count: number) {
  return count
    ? `${count.toLocaleString()} measured ${count === 1 ? "call" : "calls"}`
    : "No latency samples";
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
        other: "Other",
      } as Record<string, string>
    )[outcome] ?? outcome
  );
}
