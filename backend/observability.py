"""Bounded, database-side aggregates over the gateway's existing audit trail."""

from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import and_, case, func, literal, select, union_all

from backend.models import Audit, Upstream

AuditWindow = Literal["1h", "24h", "7d"]
WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
OUTCOMES = (
    "success",
    "tool_error",
    "error",
    "timeout",
    "pending",
    "denied",
    "invalid_arguments",
    "cancelled",
)
COMPLETED = ("success", "tool_error", "error", "timeout")
FAILED = ("tool_error", "error", "timeout")


def summary_query(conditions):
    """Return only aggregate rows, using SQL supported by PostgreSQL and SQLite.

    Completed execution outcomes determine the failure denominator and latency
    population. Denied, invalid, cancelled and pending requests remain separate.
    Rank-based interpolation matches percentile_cont, including singleton sets.
    """
    scoped = (
        select(
            Audit.id,
            Audit.server_id,
            Audit.server_name,
            Audit.duration_ms,
            Audit.outcome,
            func.row_number()
            .over(
                partition_by=Audit.server_id,
                order_by=(Audit.timestamp.desc(), Audit.id.desc()),
            )
            .label("name_rank"),
        )
        .where(*conditions)
        .cte("summary_scope")
    )
    # A second partition computes overall percentiles directly from observations.
    # Combining per-server percentiles would not produce the overall percentile.
    expanded = union_all(
        select(literal(0).label("is_total"), *scoped.c),
        select(
            literal(1),
            scoped.c.id,
            literal(None),
            scoped.c.server_name,
            scoped.c.duration_ms,
            scoped.c.outcome,
            scoped.c.name_rank,
        ),
    ).cte("summary_groups")
    eligible = and_(expanded.c.outcome.in_(COMPLETED), expanded.c.duration_ms >= 0)
    partition = (expanded.c.is_total, expanded.c.server_id)
    ranked = select(
        *expanded.c,
        case((eligible, 1), else_=0).label("eligible"),
        func.sum(case((eligible, 1), else_=0)).over(partition_by=partition).label("samples"),
        func.row_number()
        .over(
            partition_by=partition,
            order_by=(
                case((eligible, 0), else_=1),
                expanded.c.duration_ms,
                expanded.c.id,
            ),
        )
        .label("latency_rank"),
    ).cte("summary_ranked")

    def percentile(fraction):
        position = 1 + (ranked.c.samples - 1) * fraction
        below = and_(ranked.c.eligible == 1, ranked.c.latency_rank <= position)
        above = and_(ranked.c.eligible == 1, ranked.c.latency_rank >= position)
        lower = func.max(case((below, ranked.c.duration_ms)))
        upper = func.min(case((above, ranked.c.duration_ms)))
        lower_rank = func.max(case((below, ranked.c.latency_rank)))
        return lower + (upper - lower) * (func.max(position) - lower_rank)

    metrics = (
        select(
            ranked.c.is_total,
            ranked.c.server_id,
            func.max(case((ranked.c.name_rank == 1, ranked.c.server_name))).label("snapshot_name"),
            func.count().label("calls"),
            func.sum(case((ranked.c.outcome.in_(COMPLETED), 1), else_=0)).label("completed_calls"),
            func.sum(case((ranked.c.outcome.in_(FAILED), 1), else_=0)).label("failed_calls"),
            func.max(ranked.c.samples).label("latency_samples"),
            percentile(0.5).label("p50_latency_ms"),
            percentile(0.95).label("p95_latency_ms"),
            *[
                func.sum(case((ranked.c.outcome == outcome, 1), else_=0)).label(outcome)
                for outcome in OUTCOMES
            ],
            func.sum(case((ranked.c.outcome.not_in(OUTCOMES), 1), else_=0)).label("other"),
        )
        .group_by(ranked.c.is_total, ranked.c.server_id)
        .cte("summary_metrics")
    )
    return (
        select(
            *metrics.c,
            Upstream.name.label("current_name"),
            Upstream.id.label("current_id"),
        )
        .select_from(metrics.outerjoin(Upstream, metrics.c.server_id == Upstream.id))
        .order_by(metrics.c.is_total.desc(), metrics.c.calls.desc(), metrics.c.server_id)
    )


def metrics_payload(row=None):
    def value(name):
        return int(row[name]) if row else 0

    completed, failed = value("completed_calls"), value("failed_calls")
    p50 = row["p50_latency_ms"] if row else None
    p95 = row["p95_latency_ms"] if row else None
    return {
        "calls": value("calls"),
        "completed_calls": completed,
        "failed_calls": failed,
        "failure_rate": failed / completed if completed else None,
        "latency_samples": value("latency_samples"),
        "p50_latency_ms": float(p50) if p50 is not None else None,
        "p95_latency_ms": float(p95) if p95 is not None else None,
        "outcomes": {outcome: value(outcome) for outcome in (*OUTCOMES, "other")},
    }


def audit_summary(
    db, principal, window: AuditWindow, until: datetime, server_id=None, user_id=None
):
    start = until - WINDOWS[window]
    effective_user = principal.id if principal.role != "admin" else user_id or None
    conditions = [Audit.timestamp >= start, Audit.timestamp < until]
    if effective_user:
        conditions.append(Audit.user_id == effective_user)
    if server_id:
        conditions.append(Audit.server_id == server_id)
    result = {
        "window": window,
        "from": start.isoformat() + "Z",
        "to": until.isoformat() + "Z",
        "scope": "team" if principal.role == "admin" else "self",
        "user_id": effective_user,
        "server_id": server_id or None,
        "totals": metrics_payload(),
        "servers": [],
    }
    # One statement keeps counts, distributions and percentile populations at
    # the same database snapshot while in-flight audit rows are being completed.
    with db.session() as session:
        for row in session.execute(summary_query(conditions)).mappings():
            metrics = metrics_payload(row)
            if row["is_total"]:
                result["totals"] = metrics
            else:
                result["servers"].append(
                    {
                        "server_id": row["server_id"],
                        "server_name": (
                            row["current_name"] or row["snapshot_name"]
                            if row["server_id"]
                            else "Unattributed"
                        ),
                        "server_deleted": row["server_id"] is not None
                        and row["current_id"] is None,
                        **metrics,
                    }
                )
    return result
