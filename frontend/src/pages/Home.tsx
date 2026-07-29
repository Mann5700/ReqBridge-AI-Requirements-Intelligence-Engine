import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useHydratedAdoConfig } from '../lib/adoConfig';

const apiUrl = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

/**
 * Single landing page that gates access to every workspace tab behind a
 * working ADO connection. Once the user clicks Connect we:
 *   1. Verify the org/project/PAT triple via /ado/test-connection
 *   2. Create a backend session and remember its id in the store
 *   3. Surface a "Disconnect" affordance so they can tear it back down
 *
 * The sidebar in AppLayout reads ``activeSessionId`` from the same store and
 * only renders the tab links once it's populated, so navigation stays in
 * lock-step with the connection state.
 */
export default function Home() {
  const navigate = useNavigate();
  const cfg = useHydratedAdoConfig();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [projectInfo, setProjectInfo] = useState<{ name?: string; description?: string } | null>(
    null,
  );

  const connected = Boolean(cfg.activeSessionId);
  const canConnect = Boolean(cfg.orgUrl && cfg.project && cfg.pat) && !busy;

  const handleConnect = async () => {
    setBusy(true);
    setError(null);
    setProjectInfo(null);
    try {
      // 1. Validate creds against ADO before we create anything backend-side.
      const testRes = await fetch(`${apiUrl}/ado/test-connection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ado_org_url: cfg.orgUrl,
          ado_project: cfg.project,
          ado_pat: cfg.pat,
        }),
      });
      if (!testRes.ok) {
        const body = await testRes.json().catch(() => ({}));
        throw new Error(body.detail || `ADO check failed (HTTP ${testRes.status})`);
      }
      const testBody = await testRes.json();
      setProjectInfo(testBody.project);
      // Stash the authenticated user's display name so the sidebar can show
      // who the session belongs to. Fine if it's missing \u2014 the property is
      // best-effort on the backend side.
      if (testBody.user?.name) {
        cfg.setUserName(testBody.user.name);
      }

      // 2. Mint a fresh session.
      const sessionRes = await fetch(`${apiUrl}/sessions/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: `${cfg.project} \u2014 ${new Date().toLocaleString()}`,
        }),
      });
      if (!sessionRes.ok) {
        throw new Error(`Failed to create session (HTTP ${sessionRes.status})`);
      }
      const session: { id: string } = await sessionRes.json();
      cfg.setActiveSessionId(session.id);
      navigate(`/pipeline/${session.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleDisconnect = () => {
    cfg.disconnect();
    setProjectInfo(null);
    setError(null);
  };

  return (
    <div className="relative min-h-screen overflow-hidden">
      <div className="pointer-events-none absolute inset-0 bg-grid-dark bg-grid opacity-30 [mask-image:radial-gradient(ellipse_at_center,black,transparent_70%)]" />
      <div className="container relative py-16">
        <div className="mx-auto max-w-2xl animate-fade-in text-center">
          <span className="badge mb-6 border-brand/30 bg-brand/10 text-brand">
            <span className="pulse-dot text-brand" />
            Local · Offline-first · MCP-enabled
          </span>
          <h1 className="font-display text-4xl font-bold tracking-tight md:text-5xl">
            <span className="bg-gradient-to-br from-fg via-fg to-brand bg-clip-text text-transparent">
              Connect to Azure DevOps
            </span>
          </h1>
          <p className="mt-4 text-base text-fg-muted">
            One PAT unlocks ingestion, requirements review, traceability and push. Your token stays
            in this browser tab — it never persists to disk.
          </p>
        </div>

        <div className="mx-auto mt-10 max-w-xl rounded-2xl border border-border-subtle bg-bg-elevated/60 p-6 shadow-xl backdrop-blur">
          {connected ? (
            // Already connected: hide the credential form entirely. The user
            // can either jump into the workspace or disconnect to come back
            // here and start over with fresh credentials.
            <div className="space-y-4">
              <div className="rounded-md border border-teal-500/30 bg-teal-500/10 px-4 py-3 text-sm text-teal-200">
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-teal-400" />
                  <span className="font-mono text-xs">Connected</span>
                </div>
                <div className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-2xs text-teal-100/80">
                  <span className="text-teal-100/60">org</span>
                  <span className="truncate">{cfg.orgUrl}</span>
                  <span className="text-teal-100/60">project</span>
                  <span className="truncate">{cfg.project}</span>
                </div>
                {projectInfo?.description && (
                  <div className="mt-2 text-2xs text-teal-100/70">{projectInfo.description}</div>
                )}
              </div>

              <div className="flex items-center justify-between gap-3">
                <button
                  onClick={() => navigate(`/pipeline/${cfg.activeSessionId}`)}
                  className="rounded-lg bg-teal-500 px-4 py-2 text-sm font-medium text-slate-900 transition-colors hover:bg-teal-400"
                >
                  Open workspace →
                </button>
                <button
                  onClick={handleDisconnect}
                  className="rounded-lg border border-rose-500/40 px-4 py-2 text-sm text-rose-300 transition-colors hover:bg-rose-500/10"
                >
                  Disconnect
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="space-y-4">
                <div>
                  <label
                    htmlFor="home-org-url"
                    className="mb-1 block font-mono text-xs text-slate-400"
                  >
                    Organization URL
                  </label>
                  <input
                    id="home-org-url"
                    type="text"
                    value={cfg.orgUrl}
                    onChange={(e) => cfg.setField('orgUrl', e.target.value)}
                    placeholder="https://dev.azure.com/your-org"
                    className="w-full rounded-lg border border-slate-700 bg-[#0a0b0f] px-3 py-2 text-sm focus:border-brand focus:outline-none"
                  />
                </div>
                <div>
                  <label
                    htmlFor="home-project"
                    className="mb-1 block font-mono text-xs text-slate-400"
                  >
                    Project
                  </label>
                  <input
                    id="home-project"
                    type="text"
                    value={cfg.project}
                    onChange={(e) => cfg.setField('project', e.target.value)}
                    placeholder="MyProject"
                    className="w-full rounded-lg border border-slate-700 bg-[#0a0b0f] px-3 py-2 text-sm focus:border-brand focus:outline-none"
                  />
                </div>
                <div>
                  <label htmlFor="home-pat" className="mb-1 block font-mono text-xs text-slate-400">
                    Personal Access Token
                  </label>
                  <input
                    id="home-pat"
                    type="password"
                    value={cfg.pat}
                    onChange={(e) => cfg.setField('pat', e.target.value)}
                    placeholder="••••••••"
                    autoComplete="off"
                    className="w-full rounded-lg border border-slate-700 bg-[#0a0b0f] px-3 py-2 text-sm focus:border-brand focus:outline-none"
                  />
                  <p className="mt-1 text-2xs text-fg-subtle">
                    Needs <span className="font-mono">Work Items: Read &amp; Write</span> for push,
                    Read for import only.
                  </p>
                </div>
              </div>

              {error && (
                <div className="mt-4 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
                  {error}
                </div>
              )}

              <div className="mt-6">
                <button
                  onClick={handleConnect}
                  disabled={!canConnect}
                  className="w-full rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-bg transition-colors hover:bg-brand/90 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-500"
                >
                  {busy ? 'Connecting…' : 'Connect'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
