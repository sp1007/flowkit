import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

export type ConfirmOptions = {
  title?: string;
  message?: React.ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean; // red confirm button for destructive actions
};

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn>(() => Promise.resolve(false));

/** Imperative confirm: `const confirm = useConfirm(); if (!(await confirm({...}))) return;` */
export function useConfirm(): ConfirmFn {
  return useContext(ConfirmContext);
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((v: boolean) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>((o) => {
    setOpts(o);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  const close = useCallback((v: boolean) => {
    resolver.current?.(v);
    resolver.current = null;
    setOpts(null);
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {opts && <ConfirmDialog opts={opts} onClose={close} />}
    </ConfirmContext.Provider>
  );
}

function ConfirmDialog({
  opts,
  onClose,
}: {
  opts: ConfirmOptions;
  onClose: (v: boolean) => void;
}) {
  const okRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    okRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose(false);
      else if (e.key === "Enter") onClose(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={() => onClose(false)}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-neutral-800 bg-neutral-950 p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="alertdialog"
        aria-modal="true"
      >
        <div className="flex items-start gap-3">
          <div
            className={`mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-full text-lg ${
              opts.danger
                ? "bg-rose-950/60 text-rose-400"
                : "bg-indigo-950/60 text-indigo-300"
            }`}
          >
            {opts.danger ? "⚠" : "?"}
          </div>
          <div className="min-w-0 flex-1">
            {opts.title && (
              <h3 className="text-base font-semibold text-neutral-100">{opts.title}</h3>
            )}
            {opts.message && (
              <div className="mt-1 whitespace-pre-line text-sm leading-relaxed text-neutral-400">
                {opts.message}
              </div>
            )}
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={() => onClose(false)}
            className="rounded-lg border border-neutral-700 px-4 py-2 text-sm text-neutral-300 transition hover:bg-neutral-800"
          >
            {opts.cancelText || "Huỷ"}
          </button>
          <button
            ref={okRef}
            onClick={() => onClose(true)}
            className={`rounded-lg px-4 py-2 text-sm font-medium text-white transition focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-neutral-950 ${
              opts.danger
                ? "bg-rose-600 hover:bg-rose-500 focus:ring-rose-500"
                : "bg-indigo-600 hover:bg-indigo-500 focus:ring-indigo-500"
            }`}
          >
            {opts.confirmText || "Đồng ý"}
          </button>
        </div>
      </div>
    </div>
  );
}
