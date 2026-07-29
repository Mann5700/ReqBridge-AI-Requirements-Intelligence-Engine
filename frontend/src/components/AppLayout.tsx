import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { cn } from '../lib/utils';
import { useHydratedAdoConfig } from '../lib/adoConfig';

const NAV = [
  { to: 'pipeline', label: 'Ingest & Run', icon: PipelineIcon },
  { to: 'requirements', label: 'Review & Approve', icon: ListIcon },
  { to: 'artifacts', label: 'Artifact Tree', icon: TreeIcon },
  { to: 'push', label: 'Push & Sync', icon: CloudIcon },
] as const;

export default function AppLayout() {
  const cfg = useHydratedAdoConfig();
  const navigate = useNavigate();
  const sessionId = cfg.activeSessionId;
  // Mobile drawer is open by user gesture only; close it whenever the route
  // changes so navigating to a new page doesn't leave a dimming overlay up.
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  const handleDisconnect = () => {
    cfg.disconnect();
    navigate('/', { replace: true });
  };

  return (
    <div className="flex min-h-screen">
      {/* Mobile top bar — only shown <md */}
      <div className="sticky top-0 z-30 flex w-full items-center justify-between gap-3 border-b border-border-subtle bg-bg-elevated/80 px-4 py-3 backdrop-blur md:hidden">
        <div className="flex items-center gap-2">
          <Logo />
          <span className="font-display text-sm font-semibold tracking-tight">ReqBridge</span>
        </div>
        <button
          type="button"
          aria-label={mobileOpen ? 'Close navigation' : 'Open navigation'}
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen((v) => !v)}
          className="rounded-md border border-border-subtle px-2 py-1 text-fg-muted hover:bg-surface-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
        >
          <svg
            viewBox="0 0 24 24"
            className="h-5 w-5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            {mobileOpen ? <path d="M6 6l12 12M6 18L18 6" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
          </svg>
        </button>
      </div>

      {/* Mobile drawer overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/50 md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Sidebar — visible at md+, slides in on mobile */}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-30 flex h-screen w-64 shrink-0 flex-col border-r border-border-subtle bg-bg-elevated/95 backdrop-blur transition-transform md:sticky md:top-0 md:z-auto md:translate-x-0 md:bg-bg-elevated/60',
          mobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
        )}
      >
        <div className="flex items-center gap-2 px-6 py-5">
          <Logo />
          <div className="leading-tight">
            <div className="font-display text-base font-semibold tracking-tight">ReqBridge</div>
            <div className="text-2xs text-fg-subtle">Requirements Intelligence</div>
          </div>
        </div>

        <div className="px-3">
          <div className="rounded-lg border border-teal-500/30 bg-teal-500/10 px-3 py-3 text-teal-200">
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-teal-400" />
              <span className="font-mono text-xs">Connected</span>
            </div>
            <div className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-2xs text-teal-100/80">
              <span className="text-teal-100/60">user</span>
              <span className="truncate" title={cfg.userName || undefined}>
                {cfg.userName || <span className="text-teal-100/40">—</span>}
              </span>
              <span className="text-teal-100/60">project</span>
              <span className="truncate" title={cfg.project}>
                {cfg.project || '—'}
              </span>
            </div>
          </div>
        </div>

        <nav className="mt-4 flex-1 space-y-1 px-3">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={`/${to}/${sessionId}`}
              className={({ isActive }) =>
                cn(
                  'group flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand',
                  isActive
                    ? 'bg-brand/10 text-brand'
                    : 'text-fg-muted hover:bg-surface-muted hover:text-fg',
                )
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="px-3 pb-4">
          <button
            type="button"
            onClick={handleDisconnect}
            className="flex w-full items-center gap-2 rounded-lg border border-rose-500/30 px-3 py-2 text-2xs text-rose-300 transition-colors hover:bg-rose-500/10"
          >
            <svg
              viewBox="0 0 24 24"
              className="h-3.5 w-3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
            </svg>
            Disconnect
          </button>
          <div className="mt-3 flex items-center gap-2 px-3 text-2xs text-fg-subtle">
            <span className="pulse-dot text-brand" />
            <span>Local · v0.1.0</span>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 animate-fade-in">
        <Outlet />
      </main>
    </div>
  );
}

/* ----------------------------- Icons (inline) ----------------------------- */
type IconProps = { className?: string };

function Logo() {
  return (
    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand to-accent-violet shadow-glow">
      <svg
        viewBox="0 0 24 24"
        className="h-4 w-4 text-bg"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
      >
        <path d="M4 7h10M4 12h16M4 17h10" strokeLinecap="round" />
      </svg>
    </div>
  );
}
function PipelineIcon({ className }: IconProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="5" cy="12" r="2" />
      <circle cx="19" cy="12" r="2" />
      <circle cx="12" cy="5" r="2" />
      <circle cx="12" cy="19" r="2" />
      <path d="M7 12h3M14 12h3M12 7v3M12 14v3" />
    </svg>
  );
}
function ListIcon({ className }: IconProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
    </svg>
  );
}
function CloudIcon({ className }: IconProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" />
      <path d="M12 13v6M9 16l3 3 3-3" />
    </svg>
  );
}
function TreeIcon({ className }: IconProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3v6M12 9l-4 4M12 9l4 4M8 13v4M16 13v4M8 17H5M16 17h3M5 17v4M19 17v4" />
    </svg>
  );
}
