import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Copy,
  Loader2,
  X,
} from "lucide-react";
import { api, errorMessage } from "./api";

export function useResource<T>(path: string | null) {
  const [data, setData] = useState<T>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    const request = ++sequence.current;
    controller.current?.abort();
    if (!path) {
      setData(undefined);
      setError("");
      setLoading(false);
      return;
    }
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    try {
      const result = await api<T>(path, { signal: current.signal });
      if (request === sequence.current) {
        setData(result);
        setError("");
      }
    } catch (e) {
      if (request === sequence.current && !current.signal.aborted)
        setError(errorMessage(e));
    } finally {
      if (request === sequence.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    // A new query must not show rows belonging to the previous filter or page.
    setData(undefined);
    setError("");
    void refresh();
    return () => {
      sequence.current++;
      controller.current?.abort();
    };
  }, [refresh]);
  return { data, loading, error, refresh, setData };
}
export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span className="loading-inline" role="status">
      <Loader2 size={16} className="spin" />
      {label}
    </span>
  );
}
export function ErrorBox({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="notice danger" role="alert">
      <AlertCircle size={18} />
      <span>{message}</span>
      {retry && (
        <button type="button" className="text-button" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}
export function Empty({
  icon,
  title,
  children,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-icon">{icon}</div>}
      <h3>{title}</h3>
      <div className="muted">{children}</div>
    </div>
  );
}
export function Badge({
  children,
  tone = "",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return (
    <span className={`badge ${tone}`}>
      <span className="status-dot" />
      {children}
    </span>
  );
}
export function CopyButton({
  value,
  label = "Copy",
  className = "button secondary small",
}: {
  value: string;
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    setCopied(false);
    setError(false);
    return () => clearTimeout(timer.current);
  }, [value]);
  return (
    <span className="copy-wrap">
      <button
        type="button"
        className={className}
        onClick={async (event) => {
          try {
            if (navigator.clipboard && window.isSecureContext)
              await navigator.clipboard.writeText(value);
            else {
              const textarea = document.createElement("textarea");
              textarea.value = value;
              textarea.style.position = "fixed";
              textarea.style.opacity = "0";
              const trigger = event.currentTarget;
              (trigger.closest("dialog") ?? document.body).appendChild(
                textarea,
              );
              try {
                textarea.select();
                if (!document.execCommand("copy"))
                  throw new Error("Clipboard unavailable");
              } finally {
                textarea.remove();
                trigger.focus();
              }
            }
            setCopied(true);
            setError(false);
            clearTimeout(timer.current);
            timer.current = setTimeout(() => setCopied(false), 2500);
          } catch {
            setError(true);
          }
        }}
      >
        {copied ? <Check size={15} /> : <Copy size={15} />}{" "}
        {copied ? "Copied" : label}
      </button>
      {error && (
        <span className="field-hint" role="status">
          Select and copy the text manually.
        </span>
      )}
    </span>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
  busy = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const trigger = useRef(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );
  const id = useId();
  useEffect(() => {
    const node = ref.current;
    node?.showModal();
    return () => {
      node?.close();
      queueMicrotask(() => {
        // React may remove the dialog before native focus restoration runs.
        // Preserve a subsequent dialog or a caller's intentional focus change.
        if (
          trigger.current?.isConnected &&
          document.activeElement === document.body &&
          !document.querySelector("dialog[open]")
        )
          trigger.current.focus({ preventScroll: true });
      });
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`modal ${wide ? "wide" : ""}`}
      aria-labelledby={id}
      aria-busy={busy}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-heading">
        <h2 id={id}>{title}</h2>
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          disabled={busy}
          aria-label="Close dialog"
        >
          <X size={19} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function ConfirmModal({
  title,
  description,
  label,
  onConfirm,
  onClose,
  danger = true,
}: {
  title: string;
  description: string;
  label: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
  danger?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Modal
      title={title}
      busy={busy}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <p className="muted">{description}</p>
      {error && <ErrorBox message={error} />}
      <div className="modal-actions">
        <button className="button secondary" disabled={busy} onClick={onClose}>
          Cancel
        </button>
        <button
          className={`button ${danger ? "danger-button" : "primary"}`}
          disabled={busy}
          onClick={async () => {
            if (busy) return;
            setBusy(true);
            setError("");
            try {
              await onConfirm();
              onClose();
            } catch (e) {
              setError(errorMessage(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? <Spinner label="Working" /> : label}
        </button>
      </div>
    </Modal>
  );
}
export function Toast({
  message,
  error = false,
  close,
}: {
  message: string;
  error?: boolean;
  close: () => void;
}) {
  useEffect(() => {
    if (error) return;
    const timer = setTimeout(close, 4500);
    return () => clearTimeout(timer);
  }, [close, message, error]);
  return (
    <div
      className={`toast${error ? " danger" : ""}`}
      role={error ? "alert" : "status"}
    >
      {error ? <AlertCircle size={18} /> : <CheckCircle2 size={18} />}
      {message}
      <button
        className="icon-button"
        aria-label="Dismiss notification"
        onClick={close}
      >
        <X size={16} />
      </button>
    </div>
  );
}
export function PageHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {action}
    </div>
  );
}
export function Logo({ small = false }: { small?: boolean }) {
  return (
    <span className={`brand ${small ? "brand-small" : ""}`}>
      <svg
        className="brand-mark"
        width="37"
        height="37"
        viewBox="0 0 40 40"
        fill="none"
        aria-hidden="true"
      >
        <rect width="40" height="40" rx="12" fill="currentColor" />
        <path
          d="M13 12v16m14-16v16M13 20h14"
          stroke="#f3f2ed"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
      </svg>
      <span>Harbor</span>
    </span>
  );
}
