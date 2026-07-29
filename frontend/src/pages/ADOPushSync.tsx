import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useHydratedAdoConfig } from '../lib/adoConfig';
import { apiJson } from '../lib/api';

interface PushLogEntry {
  id: string;
  tool_name: string;
  ado_work_item_id: number | null;
  success: boolean;
  error_message: string | null;
  latency_ms: number | null;
  timestamp: string;
}

interface WorkItemNode {
  work_item_type: string;
  children?: WorkItemNode[];
}

// The hierarchy levels we surface in the by-type breakdown, in display order.
const WORK_ITEM_TYPES = ['Epic', 'Feature', 'User Story', 'Task', 'Test Case'] as const;

function countByType(nodes: WorkItemNode[]): Record<string, number> {
  const counts: Record<string, number> = {};
  const walk = (list: WorkItemNode[]) => {
    for (const n of list) {
      counts[n.work_item_type] = (counts[n.work_item_type] || 0) + 1;
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes);
  return counts;
}

export default function ADOPushSync() {
  const { sessionId = '' } = useParams<{ sessionId: string }>();
  const queryClient = useQueryClient();
  const config = useHydratedAdoConfig();
  // Project is required server-side as well — keep the gate in lockstep so
  // the user gets a clear "fill in project" hint before the request is sent.
  const ready = Boolean(config.orgUrl && config.pat && config.project);
  const [pushError, setPushError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const [approved, setApproved] = useState(false);
  const [approving, setApproving] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const apiUrl = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

  // Hydrate approved state from backend session status on mount
  useEffect(() => {
    if (!sessionId) return;
    fetch(`${apiUrl}/sessions/${sessionId}/status`)
      .then((r) => r.json())
      .then((data) => {
        if (data.approved_for_push) {
          setApproved(true);
        }
      })
      .catch(() => {});
  }, [sessionId, apiUrl]);

  const { data: pushLogs = [] } = useQuery<PushLogEntry[]>({
    queryKey: ['ado-push-log', sessionId],
    queryFn: () => apiJson<PushLogEntry[]>(`/sessions/${sessionId}/push-log`),
    // Re-poll while a push is in flight so newly-created log rows surface
    // without the user having to refresh.
    refetchInterval: polling ? 3000 : false,
  });

  // Work items to be pushed, used for the by-type breakdown.
  const { data: workItems = [] } = useQuery<WorkItemNode[]>({
    queryKey: ['workitems', sessionId],
    queryFn: () => apiJson<WorkItemNode[]>(`/sessions/${sessionId}/workitems`),
    enabled: Boolean(sessionId),
    // Only poll during an active push so log rows appear live.
    refetchInterval: polling ? 3000 : false,
  });
  const workItemCounts = countByType(workItems);
  const totalWorkItems = Object.values(workItemCounts).reduce((a, b) => a + b, 0);

  const pushMutation = useMutation({
    mutationFn: () =>
      apiJson(`/sessions/${sessionId}/push`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          // Backend now treats `ado_org_url` as the canonical field; the old
          // `ado_org` alias is kept for back-compat but no longer sent.
          ado_project: config.project || undefined,
          ado_org_url: config.orgUrl || undefined,
          ado_pat: config.pat || undefined,
        }),
      }),
    onSuccess: () => {
      setPushError(null);
      setPolling(true);
      queryClient.invalidateQueries({ queryKey: ['ado-push-log', sessionId] });
      // Stop auto-polling after ~2min so we don't loop forever if nothing
      // ever shows up; the user can manually refresh after that.
      if (pollTimer.current) clearTimeout(pollTimer.current);
      pollTimer.current = setTimeout(() => setPolling(false), 120_000);
    },
    onError: (err: Error) => {
      setPushError(err.message);
      setPolling(false);
    },
  });

  // Cleanup the polling timer on unmount so we don't leak setTimeout handles.
  useEffect(() => {
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, []);

  const successCount = pushLogs.filter((l) => l.success).length;
  const failCount = pushLogs.filter((l) => !l.success).length;
  const avgLatency =
    pushLogs.length > 0
      ? Math.round(pushLogs.reduce((acc, l) => acc + (l.latency_ms || 0), 0) / pushLogs.length)
      : 0;

  return (
    <div className="p-6 md:p-10">
      <div className="mx-auto max-w-6xl">
        {/* Header */}
        <h1 className="mb-8 font-display text-2xl font-bold tracking-tight">
          Azure DevOps Push & Stats
        </h1>

        {/* Push trigger */}
        <div className="mb-6 rounded-xl border border-slate-800 bg-slate-900 p-6">
          <h2 className="mb-4 text-lg font-semibold">Push to Azure DevOps</h2>

          {/* Approve toggle + Push button on one row */}
          <div className="mb-5 flex flex-wrap items-center gap-4">
            {/* Toggle: Approve / Approved */}
            <button
              onClick={async () => {
                if (approved) {
                  setApproved(false);
                  return;
                }
                setApproving(true);
                setPushError(null);
                try {
                  const res = await fetch(`${apiUrl}/sessions/${sessionId}/approve-for-push`, {
                    method: 'POST',
                  });
                  if (!res.ok) {
                    const body = await res.json().catch(() => ({}));
                    throw new Error(body.detail || `Approval failed (HTTP ${res.status})`);
                  }
                  setApproved(true);
                  queryClient.invalidateQueries({ queryKey: ['workitems', sessionId] });
                } catch (err) {
                  setPushError((err as Error).message);
                } finally {
                  setApproving(false);
                }
              }}
              disabled={approving}
              className={`rounded-lg px-5 py-2.5 text-sm font-medium transition-colors ${
                approved
                  ? 'border border-teal-500/40 bg-teal-500/10 text-teal-300 hover:border-red-500/30 hover:bg-red-500/10 hover:text-red-300'
                  : 'bg-teal-500 text-slate-900 hover:bg-teal-400'
              } disabled:cursor-not-allowed disabled:opacity-50`}
            >
              {approving ? 'Approving…' : approved ? '✓ Approved' : 'Approve for Push'}
            </button>

            {/* Push button */}
            <button
              onClick={() => pushMutation.mutate()}
              disabled={pushMutation.isPending || !ready || !approved || totalWorkItems === 0}
              title={
                !approved
                  ? 'Approve first'
                  : totalWorkItems === 0
                    ? 'No work items to push'
                    : !ready
                      ? 'Missing ADO credentials'
                      : undefined
              }
              className="rounded-lg bg-[#0078d4] px-6 py-2.5 text-sm font-medium transition-colors hover:bg-[#106ebe] disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-500"
            >
              {pushMutation.isPending || polling
                ? 'Pushing…'
                : `Push ${totalWorkItems} items to ADO`}
            </button>

            {/* Report link */}
            <a
              href={`${apiUrl}/sessions/${sessionId}/report/html`}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg border border-slate-700 px-4 py-2.5 text-sm text-slate-300 transition-colors hover:border-slate-500 hover:text-white"
            >
              View Report
            </a>
          </div>

          {/* Work items by type */}
          <div className="rounded-lg border border-slate-800 bg-[#0a0b0f] p-4">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-sm font-medium text-slate-300">Work items by type</span>
              <span className="text-2xs text-slate-500">{totalWorkItems} total</span>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              {WORK_ITEM_TYPES.map((type) => (
                <div
                  key={type}
                  className="rounded-lg border border-slate-800 bg-slate-900 p-3 text-center"
                >
                  <div className="font-['Geist_Mono'] text-2xl font-bold text-white">
                    {workItemCounts[type] ?? 0}
                  </div>
                  <div className="mt-1 text-2xs text-slate-400">{type}</div>
                </div>
              ))}
            </div>
          </div>

          {pushError && (
            <div className="mt-4 flex items-start justify-between gap-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              <div>
                <div className="font-semibold">Push failed</div>
                <div className="mt-0.5 text-red-300/80">{pushError}</div>
              </div>
              <button
                onClick={() => setPushError(null)}
                className="text-red-200/70 hover:text-red-100"
                aria-label="Dismiss error"
              >
                ✕
              </button>
            </div>
          )}
        </div>

        {/* Stats Bar */}
        <div className="mb-6 grid grid-cols-4 gap-4">
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 text-center">
            <div className="font-['Geist_Mono'] text-2xl font-bold text-white">
              {pushLogs.length}
            </div>
            <div className="text-xs text-slate-400">Total Pushes</div>
          </div>
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 text-center">
            <div className="font-['Geist_Mono'] text-2xl font-bold text-teal-400">
              {successCount}
            </div>
            <div className="text-xs text-slate-400">Successful</div>
          </div>
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 text-center">
            <div className="font-['Geist_Mono'] text-2xl font-bold text-red-400">{failCount}</div>
            <div className="text-xs text-slate-400">Failed</div>
          </div>
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 text-center">
            <div className="font-['Geist_Mono'] text-2xl font-bold text-amber-400">
              {avgLatency}ms
            </div>
            <div className="text-xs text-slate-400">Avg Latency</div>
          </div>
        </div>

        {/* Push Log Table */}
        <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900">
          <div className="border-b border-slate-800 p-4">
            <h2 className="text-lg font-semibold">Push Log</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-[#0a0b0f] text-slate-400">
                <tr>
                  <th className="px-4 py-3 text-left font-['Geist_Mono'] text-xs">Timestamp</th>
                  <th className="px-4 py-3 text-left font-['Geist_Mono'] text-xs">Tool</th>
                  <th className="px-4 py-3 text-left font-['Geist_Mono'] text-xs">ADO ID</th>
                  <th className="px-4 py-3 text-left font-['Geist_Mono'] text-xs">Status</th>
                  <th className="px-4 py-3 text-left font-['Geist_Mono'] text-xs">Latency</th>
                  <th className="px-4 py-3 text-left font-['Geist_Mono'] text-xs">Error</th>
                </tr>
              </thead>
              <tbody>
                {pushLogs.map((log) => (
                  <tr key={log.id} className="border-t border-slate-800 hover:bg-slate-800/50">
                    <td className="px-4 py-3 font-['Geist_Mono'] text-xs text-slate-400">
                      {new Date(log.timestamp).toLocaleString()}
                    </td>
                    <td className="px-4 py-3 font-['Geist_Mono'] text-xs">{log.tool_name}</td>
                    <td className="px-4 py-3">
                      {log.ado_work_item_id ? (
                        <a
                          href={`${config.orgUrl}/${config.project}/_workitems/edit/${log.ado_work_item_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-['Geist_Mono'] text-xs text-[#0078d4] hover:underline"
                        >
                          #{log.ado_work_item_id}
                        </a>
                      ) : (
                        <span className="text-xs text-slate-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-bold ${
                          log.success
                            ? 'bg-teal-500/20 text-teal-400'
                            : 'bg-red-500/20 text-red-400'
                        }`}
                      >
                        {log.success ? 'OK' : 'FAIL'}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-['Geist_Mono'] text-xs text-slate-400">
                      {log.latency_ms ? `${log.latency_ms}ms` : '—'}
                    </td>
                    <td className="max-w-xs truncate px-4 py-3 text-xs text-red-400">
                      {log.error_message || '—'}
                    </td>
                  </tr>
                ))}
                {pushLogs.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-slate-600">
                      No push operations yet. Configure ADO and push work items.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
