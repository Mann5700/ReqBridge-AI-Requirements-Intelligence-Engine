import { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useHydratedAdoConfig } from '../lib/adoConfig';
import { workItemColor } from '../lib/workItemColors';

interface PushLogEntry {
  id: string;
  ado_work_item_id: number | null;
  success: boolean;
  error_message: string | null;
  latency_ms: number | null;
  timestamp: string;
}

interface WorkItemNode {
  id?: string;
  work_item_type: string;
  title: string;
  description?: string;
  acceptance_criteria?: string;
  story_points: number | null;
  children?: WorkItemNode[];
}

const WI_TYPES = ['Feature', 'User Story', 'Task', 'Test Case'] as const;

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

function totalPoints(nodes: WorkItemNode[]): number {
  let pts = 0;
  const walk = (list: WorkItemNode[]) => {
    for (const n of list) {
      if (typeof n.story_points === 'number') pts += n.story_points;
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes);
  return pts;
}

function totalCount(nodes: WorkItemNode[]): number {
  let count = 0;
  const walk = (list: WorkItemNode[]) => {
    for (const n of list) {
      count++;
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes);
  return count;
}

export default function PushToADO() {
  const { sessionId = '' } = useParams<{ sessionId: string }>();
  const config = useHydratedAdoConfig();
  const navigate = useNavigate();

  const apiUrl = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

  // State
  const [approved, setApproved] = useState(false);
  const [workItems, setWorkItems] = useState<WorkItemNode[]>([]);
  const [pushing, setPushing] = useState(false);
  const [pushed, setPushed] = useState(false);
  const [pushLogs, setPushLogs] = useState<PushLogEntry[]>([]);
  const [pushError, setPushError] = useState<string | null>(null);
  const [planningStage, setPlanningStage] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Hydrate approved state from backend
  useEffect(() => {
    if (!sessionId) return;
    const ctrl = new AbortController();
    (async () => {
      try {
        const res = await fetch(`${apiUrl}/sessions/${sessionId}/status`, { signal: ctrl.signal });
        if (!res.ok) return;
        const data = await res.json();
        if (data.approved_for_push) setApproved(true);
        if (data.status === 'completed' && pushLogs.length > 0) setPushed(true);
      } catch {
        /* non-fatal */
      }
    })();
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, apiUrl]);

  // Subscribe to the session's WebSocket so we can show "Planning is running"
  // banners in real time without polling. The post-approval pipeline emits
  // pipeline_resumed → agent_started(planning) → agent_completed(planning) →
  // agent_started(traceability) → agent_started(feedback) → pipeline_complete.
  useEffect(() => {
    if (!sessionId) return;
    const wsUrl = apiUrl.replace(/^http/, 'ws') + `/ws/${sessionId}`;
    const ws = new WebSocket(wsUrl);
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.event === 'pipeline_resumed') {
          setPlanningStage('planning');
        } else if (data.event === 'agent_started' && data.current_agent) {
          setPlanningStage(data.current_agent);
        } else if (data.event === 'pipeline_complete') {
          setPlanningStage(null);
          // Work items just landed — refetch immediately.
          fetch(`${apiUrl}/sessions/${sessionId}/workitems`)
            .then((r) => r.json())
            .then(setWorkItems)
            .catch(() => {});
        } else if (data.event === 'pipeline_failed') {
          setPlanningStage(null);
          setPushError(data.error || 'Work item generation failed');
        }
      } catch {
        /* ignore non-JSON frames */
      }
    };
    return () => ws.close();
  }, [sessionId, apiUrl]);

  // Fetch work items (once on mount + every 10s while waiting for generation
  // after approval). Stops polling once items appear or after 3 minutes.
  useEffect(() => {
    if (!sessionId) return;
    const ctrl = new AbortController();
    const fetchWI = async () => {
      try {
        const res = await fetch(`${apiUrl}/sessions/${sessionId}/workitems`, {
          signal: ctrl.signal,
        });
        if (res.ok) setWorkItems(await res.json());
      } catch {
        /* non-fatal */
      }
    };
    fetchWI();
    // Only poll when approved AND no items yet — cap at 3 minutes total
    let interval: ReturnType<typeof setInterval> | null = null;
    let timeoutHandle: ReturnType<typeof setTimeout> | null = null;
    if (approved && workItems.length === 0) {
      interval = setInterval(fetchWI, 10000);
      timeoutHandle = setTimeout(() => {
        if (interval) clearInterval(interval);
      }, 180_000);
    }
    return () => {
      ctrl.abort();
      if (interval) clearInterval(interval);
      if (timeoutHandle) clearTimeout(timeoutHandle);
    };
  }, [sessionId, apiUrl, approved, workItems.length]);

  // Fetch push logs always (so stats show even before a fresh push).
  // While a push is in flight, poll every 2s to surface new rows live.
  useEffect(() => {
    if (!sessionId) return;
    const ctrl = new AbortController();
    const fetchLogs = async () => {
      try {
        const res = await fetch(`${apiUrl}/sessions/${sessionId}/push-log`, {
          signal: ctrl.signal,
        });
        if (res.ok) setPushLogs(await res.json());
      } catch {
        /* non-fatal */
      }
    };
    fetchLogs();
    const interval = pushing ? setInterval(fetchLogs, 2000) : null;
    return () => {
      ctrl.abort();
      if (interval) clearInterval(interval);
    };
  }, [sessionId, apiUrl, pushing]);

  // Push to ADO
  const handlePush = async () => {
    setPushing(true);
    setPushError(null);
    try {
      const res = await fetch(`${apiUrl}/sessions/${sessionId}/push`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ado_project: config.project || undefined,
          ado_org_url: config.orgUrl || undefined,
          ado_pat: config.pat || undefined,
        }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Push failed (${res.status}): ${text}`);
      }
      // Poll push-log for progress
      pollRef.current = setInterval(async () => {
        try {
          const logRes = await fetch(`${apiUrl}/sessions/${sessionId}/push-log`);
          if (logRes.ok) {
            const logs: PushLogEntry[] = await logRes.json();
            setPushLogs(logs);
            const successCount = logs.filter((l) => l.success).length;
            if (successCount >= totalCount(workItems) || logs.some((l) => !l.success)) {
              if (pollRef.current) clearInterval(pollRef.current);
              setPushing(false);
              setPushed(true);
            }
          }
        } catch {
          /* keep polling */
        }
      }, 2000);
      // Safety timeout
      setTimeout(() => {
        if (pollRef.current) clearInterval(pollRef.current);
        setPushing(false);
        setPushed(true);
      }, 240_000);
    } catch (err) {
      setPushError((err as Error).message);
      setPushing(false);
    }
  };

  const wiCounts = countByType(workItems);
  const wiTotal = totalCount(workItems);
  const wiPoints = totalPoints(workItems);
  const successLogs = pushLogs.filter((l) => l.success);
  const failedLogs = pushLogs.filter((l) => !l.success);
  const avgLatencyMs =
    pushLogs.length > 0
      ? Math.round(pushLogs.reduce((acc, l) => acc + (l.latency_ms || 0), 0) / pushLogs.length)
      : 0;
  const lastPushAt =
    pushLogs.length > 0
      ? new Date(pushLogs[0].timestamp).toLocaleString(undefined, {
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        })
      : '';

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Header with Home button */}
      <div className="shrink-0 border-b border-slate-800 bg-slate-900 px-6 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <div>
            <h1 className="font-display text-lg font-bold tracking-tight">Push & Sync</h1>
            <p className="mt-0.5 text-xs text-slate-400">
              Push approved work items to Azure DevOps.
            </p>
          </div>
          <button
            onClick={() => navigate('/')}
            className="cursor-pointer rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-700 hover:text-white"
          >
            ← Home
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 px-6 py-6">
        <div className="mx-auto max-w-7xl">
          {/* Work item stats */}
          <h2 className="mb-3 text-sm font-semibold text-slate-300">Work Item Plan</h2>
          <div className="mb-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div className="rounded-lg border border-slate-700 bg-slate-800 p-4">
              <p className="font-['Geist_Mono'] text-xs text-slate-500">Total Items</p>
              <p className="mt-1 text-2xl font-bold text-white">{wiTotal}</p>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-800 p-4">
              <p className="font-['Geist_Mono'] text-xs text-slate-500">Story Points</p>
              <p className="mt-1 text-2xl font-bold text-teal-300">{wiPoints}</p>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-800 p-4">
              <p className="font-['Geist_Mono'] text-xs text-slate-500">Est. Hours</p>
              <p className="mt-1 text-2xl font-bold text-amber-300">
                {(wiPoints * 6.5).toFixed(0)}
              </p>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-800 p-4">
              <p className="font-['Geist_Mono'] text-xs text-slate-500">Project</p>
              <p className="mt-1 truncate text-base font-medium text-slate-200">
                {config.project || '—'}
              </p>
            </div>
          </div>

          {/* Type breakdown chips */}
          <div className="mb-5 flex flex-wrap gap-2">
            {WI_TYPES.map((t) => {
              const c = workItemColor(t);
              return (
                <span
                  key={t}
                  className={`rounded-lg border px-3 py-1.5 text-xs ${c.bg} ${c.text} ${c.border}`}
                >
                  <span
                    className="mr-1.5 inline-block h-2 w-2 rounded-full"
                    style={{ background: c.hex }}
                  />
                  {t} <span className="ml-1 font-bold">{wiCounts[t] ?? 0}</span>
                </span>
              );
            })}
          </div>

          {/* Push stats */}
          <h2 className="mb-3 text-sm font-semibold text-slate-300">Push Stats</h2>
          <div className="mb-5 grid grid-cols-2 gap-4 sm:grid-cols-5">
            <div className="rounded-lg border border-slate-700 bg-slate-800 p-4">
              <p className="font-['Geist_Mono'] text-xs text-slate-500">Total Pushes</p>
              <p className="mt-1 text-2xl font-bold text-white">{pushLogs.length}</p>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-800 p-4">
              <p className="font-['Geist_Mono'] text-xs text-slate-500">Successful</p>
              <p className="mt-1 text-2xl font-bold text-teal-300">{successLogs.length}</p>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-800 p-4">
              <p className="font-['Geist_Mono'] text-xs text-slate-500">Failed</p>
              <p className="mt-1 text-2xl font-bold text-red-300">{failedLogs.length}</p>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-800 p-4">
              <p className="font-['Geist_Mono'] text-xs text-slate-500">Avg Latency</p>
              <p className="mt-1 text-2xl font-bold text-amber-300">{avgLatencyMs}ms</p>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-800 p-4">
              <p className="font-['Geist_Mono'] text-xs text-slate-500">Last Push</p>
              <p className="mt-1 truncate text-sm font-medium text-slate-200">
                {lastPushAt || '—'}
              </p>
            </div>
          </div>

          {/* Connection info */}
          <div className="mb-5 rounded-lg border border-slate-700 bg-slate-800/50 p-4">
            <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 font-['Geist_Mono'] text-xs">
              <span className="text-slate-500">org</span>
              <span className="truncate text-slate-300">{config.orgUrl || '—'}</span>
              <span className="text-slate-500">project</span>
              <span className="truncate text-slate-300">{config.project || '—'}</span>
              <span className="text-slate-500">status</span>
              <span className="flex items-center gap-2 text-slate-300">
                <span
                  className={`h-2 w-2 rounded-full ${config.orgUrl && config.pat ? 'bg-teal-400' : 'bg-red-400'}`}
                />
                {config.orgUrl && config.pat ? 'Ready' : 'Not configured'}
              </span>
            </div>
          </div>

          {/* Push progress */}
          {pushing && (
            <div className="mb-5">
              <div className="flex items-center gap-3">
                <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-700">
                  <div
                    className="h-full rounded-full bg-blue-500 transition-all duration-500"
                    style={{
                      width: `${wiTotal > 0 ? (successLogs.length / wiTotal) * 100 : 0}%`,
                    }}
                  />
                </div>
                <span className="shrink-0 font-['Geist_Mono'] text-sm text-slate-400">
                  {successLogs.length}/{wiTotal}
                </span>
              </div>
              <p className="mt-2 text-xs text-slate-500">Pushing work items to Azure DevOps…</p>
            </div>
          )}

          {/* Push result */}
          {pushed && pushLogs.length > 0 && (
            <div className="mb-5 rounded-lg border border-teal-500/30 bg-teal-500/10 px-5 py-4">
              <p className="text-sm font-medium text-teal-300">
                ✓ {successLogs.length} work item{successLogs.length === 1 ? '' : 's'} pushed
                successfully
                {failedLogs.length > 0 && (
                  <span className="ml-2 text-red-300">· {failedLogs.length} failed</span>
                )}
              </p>
              {successLogs.length > 0 && (
                <p className="mt-2 font-['Geist_Mono'] text-xs text-teal-300/70">
                  ADO IDs: {successLogs.map((l) => `#${l.ado_work_item_id}`).join(', ')}
                </p>
              )}
              {failedLogs.length > 0 && (
                <div className="mt-3 space-y-1">
                  {failedLogs.map((l) => (
                    <p key={l.id} className="font-['Geist_Mono'] text-xs text-red-300/80">
                      ✕ {l.error_message || 'Unknown error'}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}

          {pushError && (
            <div className="mb-5 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
              {pushError}
            </div>
          )}

          {/* Live planning progress banner */}
          {planningStage && (
            <div className="mb-5 flex items-center gap-3 rounded-lg border border-sky-500/40 bg-sky-500/10 px-5 py-3 text-sm text-sky-200">
              <span className="inline-block h-2.5 w-2.5 animate-pulse rounded-full bg-sky-400" />
              <div>
                <div className="font-medium">
                  {planningStage === 'planning' && 'Generating work items…'}
                  {planningStage === 'traceability' && 'Building traceability graph…'}
                  {planningStage === 'feedback' && 'Recording feedback…'}
                  {planningStage === 'integration' && 'Preparing ADO push…'}
                </div>
                <div className="text-xs text-sky-300/70">
                  Sonnet 4.5 · running 5 batches in parallel (~60-90s per batch on the bridge)
                </div>
              </div>
            </div>
          )}

          {/* Action buttons — Push */}
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={handlePush}
              disabled={pushing || !approved || wiTotal === 0 || !config.orgUrl || !config.pat}
              className="rounded-lg bg-blue-600 px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-500"
              title={
                !approved
                  ? 'Approve on the Review & Approve page first'
                  : !config.orgUrl || !config.pat
                    ? 'Configure ADO credentials first'
                    : wiTotal === 0
                      ? 'No work items to push'
                      : ''
              }
            >
              {pushing ? 'Pushing to ADO…' : `Push ${wiTotal} Items to Azure DevOps`}
            </button>
            {!approved && (
              <span className="flex items-center gap-2 text-xs text-amber-300">
                <span className="inline-block h-2 w-2 rounded-full bg-amber-400" />
                Approve the report on the Review & Approve page to enable push
              </span>
            )}
            {approved && wiTotal === 0 && (
              <span className="text-xs text-slate-400">No work items to push</span>
            )}
            {approved && wiTotal > 0 && (!config.orgUrl || !config.pat) && (
              <span className="text-xs text-red-300">Configure ADO credentials in Settings</span>
            )}
            {approved && wiTotal > 0 && config.orgUrl && config.pat && !pushing && (
              <span className="text-xs text-teal-300">✓ Ready to push</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
