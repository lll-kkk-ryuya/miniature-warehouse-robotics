export function Panel({
  title,
  children,
  className = "",
  accessory,
  scroll = true,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
  accessory?: React.ReactNode;
  // false when the child manages its own scroll (e.g. a virtualized list) — avoids nesting two
  // scroll containers.
  scroll?: boolean;
}) {
  return (
    <section
      className={`flex h-full min-h-0 flex-col rounded-lg border border-slate-700 bg-surface-panel ${className}`}
    >
      <div className="flex items-center justify-between border-b border-slate-700 px-3 py-1.5">
        <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
        {accessory}
      </div>
      <div className={`min-h-0 flex-1 ${scroll ? "overflow-auto p-3" : "overflow-hidden"}`}>
        {children}
      </div>
    </section>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center text-sm text-slate-500">{children}</div>
  );
}
