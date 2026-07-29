import { useEffect, useState, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useHydratedAdoConfig, adoCredsBody } from '../lib/adoConfig';

interface AgentStep {
  name: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress: number;
  startedAt?: string;
  completedAt?: string;
  confidence?: number;
  error?: string;
}

interface PipelineEvent {
  event: string;
  progress: number;
  status: string;
  current_agent?: string;
  error?: string;
}

interface SprintOption {
  sprint: string;
  start: string;
  end: string;
  iteration_path: string;
  is_current: boolean;
}

// Ordered pipeline stages. Keys must match the backend ``agent_name`` values
// broadcast over the WebSocket so progress events line up with the stepper.
const AGENT_STEPS: string[] = [
  'ingestion',
  'extraction',
  'conflict',
  'prioritization',
  'planning',
  'traceability',
  'feedback',
];

// Plain-English labels for the stepper + live logs (the raw agent names are
// jargon). Falls back to a de-underscored name for anything unmapped.
const STEP_LABELS: Record<string, string> = {
  ingestion: 'Ingest & Prepare',
  extraction: 'Extract Requirements',
  conflict: 'Detect Conflicts',
  prioritization: 'Prioritize Requirements',
  planning: 'Generate Work Items',
  traceability: 'Build Traceability',
  feedback: 'Record Feedback',
};

const STEP_DESCRIPTIONS: Record<string, string> = {
  ingestion: 'Parse & chunk uploaded documents',
  extraction: 'Identify requirements via LLM',
  conflict: 'Find contradictions or overlaps',
  prioritization: 'Score by MoSCoW & business value',
  planning: 'Generate stories, tasks & estimates',
  traceability: 'Link requirements to work items',
  feedback: 'Record outcomes for improvement',
};
const stepLabel = (name: string): string => STEP_LABELS[name] ?? name.replace(/_/g, ' ');

export default function PipelineMonitor() {
  const { sessionId = '' } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [steps, setSteps] = useState<AgentStep[]>(
    AGENT_STEPS.map((name) => ({
      name,
      status: 'pending',
      progress: 0,
    })),
  );
  const [connected, setConnected] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [running, setRunning] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<string[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);
  // Work-item import: an alternative pipeline input to document upload. Users
  // paste comma-separated ids or ADO URLs; we pull them in as context.
  const [wiRefs, setWiRefs] = useState<string>('');
  const [importing, setImporting] = useState(false);
  const [importedItems, setImportedItems] = useState<number[]>([]);
  const cfg = useHydratedAdoConfig();
  // Sprint picker: 'auto' resolves by date on the backend; otherwise the
  // selected sprint's start date is sent as sprint_start.
  const [sprints, setSprints] = useState<SprintOption[]>([]);
  const [selectedSprint, setSelectedSprint] = useState<string>('auto');
  // Optional free-text guidance threaded into extraction + planning prompts.
  const [instructions, setInstructions] = useState<string>('');
  const [showInstructions, setShowInstructions] = useState(false);
  // Drives the "View requirements" CTA. True once the backend says the
  // pipeline finished or the session is past the awaiting-review gate.
  const [reviewReady, setReviewReady] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const apiUrl = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

  // Load the configured sprint timeline so the user can pick a target sprint.
  useEffect(() => {
    const ctrl = new AbortController();
    (async () => {
      try {
        const res = await fetch(`${apiUrl}/sprints`, { signal: ctrl.signal });
        if (!res.ok) return;
        const data = await res.json();
        const list: SprintOption[] = data.sprints ?? [];
        setSprints(list);
        const current = list.find((s) => s.is_current);
        if (current) setSelectedSprint(current.sprint);
      } catch {
        /* sprint picker is optional; ignore fetch errors */
      }
    })();
    return () => ctrl.abort();
  }, [apiUrl]);

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0 || !sessionId) return;
    setUploading(true);
    setActionError(null);
    try {
      for (const file of Array.from(files)) {
        const fd = new FormData();
        fd.append('file', file);
        const res = await fetch(`${apiUrl}/sessions/${sessionId}/upload`, {
          method: 'POST',
          body: fd,
        });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`Upload failed (${res.status}): ${text || res.statusText}`);
        }
        setUploadedFiles((prev) => [...prev, file.name]);
      }
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleImportWorkItems = async () => {
    if (!sessionId || !wiRefs.trim()) return;
    setImporting(true);
    setActionError(null);
    try {
      const res = await fetch(`${apiUrl}/sessions/${sessionId}/import-work-items`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refs: wiRefs.trim(), ...adoCredsBody(cfg) }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Import failed (${res.status}): ${text || res.statusText}`);
      }
      const data = await res.json();
      const ids: number[] = data.work_item_ids ?? [];
      setImportedItems((prev) => [...prev, ...ids]);
      setWiRefs('');
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setImporting(false);
    }
  };

  const handleRun = async () => {
    if (!sessionId) return;
    setRunning(true);
    setActionError(null);
    try {
      const sprintStart =
        selectedSprint === 'auto'
          ? undefined
          : sprints.find((s) => s.sprint === selectedSprint)?.start;
      const runBody: Record<string, string> = {};
      if (sprintStart) runBody.sprint_start = sprintStart;
      if (instructions.trim()) runBody.instructions = instructions.trim();
      const res = await fetch(`${apiUrl}/sessions/${sessionId}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(runBody),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Run failed (${res.status}): ${text || res.statusText}`);
      }
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setRunning(false);
    }
  };

  // Seed reviewReady from the backend so the CTA appears even when the user
  // navigates back to a finished session and missed the WS event.
  useEffect(() => {
    if (!sessionId) return;
    const ctrl = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${apiUrl}/sessions/${sessionId}/status`, {
          signal: ctrl.signal,
        });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled && ['awaiting_review', 'approved', 'completed'].includes(data.status)) {
          setReviewReady(true);
        }
      } catch {
        /* non-fatal — aborted unmount or transient network blip */
      }
    })();
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [sessionId, apiUrl]);

  useEffect(() => {
    if (!sessionId) return;

    const apiUrl = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';
    const wsUrl = apiUrl.replace(/^http/, 'ws');
    let attempt = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const connect = () => {
      const ws = new WebSocket(`${wsUrl}/ws/${sessionId}`);
      wsRef.current = ws;

      ws.onopen = () => {
        attempt = 0;
        setConnected(true);
      };
      ws.onclose = () => {
        setConnected(false);
        if (cancelled) return;
        // Exponential backoff: 1s, 2s, 4s, ... capped at 30s.
        const delay = Math.min(1000 * 2 ** attempt, 30000);
        attempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
      ws.onerror = () => ws.close();

      ws.onmessage = (event) => {
        const data: PipelineEvent = JSON.parse(event.data);
        const label = stepLabel(data.current_agent || '');
        const pct = Number.isFinite(data.progress) ? `${(data.progress * 100).toFixed(0)}%` : '';
        const verb =
          data.event === 'agent_completed'
            ? 'finished'
            : data.event === 'agent_started'
              ? 'started'
              : data.event.replace(/_/g, ' ');
        const detail = data.current_agent ? `${label} — ${verb}` : verb;
        setLogs((prev) => [
          ...prev,
          `[${new Date().toLocaleTimeString()}] ${detail}${pct ? ` · ${pct}` : ''}`,
        ]);

        if (
          data.event === 'pipeline_complete' ||
          data.status === 'completed' ||
          data.status === 'awaiting_review' ||
          data.status === 'approved'
        ) {
          setReviewReady(true);
        }

        // Terminal events override the per-agent logic. ``pipeline_complete``
        // covers the review-gated path where the later stages (planning →
        // feedback) actually run during approval on the Work Items page, not
        // here — so we light the whole stepper rather than leave it half-grey.
        if (data.event === 'pipeline_complete') {
          setSteps((prev) => prev.map((s) => ({ ...s, status: 'completed', progress: 1 })));
          return;
        }
        if (data.event === 'pipeline_failed') {
          setSteps((prev) =>
            prev.map((s) => (s.status === 'running' ? { ...s, status: 'failed' } : s)),
          );
          return;
        }

        // Drive completion off stage ORDER, not the backend's arbitrary
        // progress floats: everything before the active stage is done, the
        // active stage is running (or done on ``agent_completed``).
        const activeIdx = AGENT_STEPS.indexOf(data.current_agent || '');
        if (activeIdx === -1) return;
        // Backend reports OVERALL pipeline progress (e.g. 0.25 after
        // ingestion). Convert to per-step progress so each step's mini
        // bar fills 0→100% within its own slice.
        const stepShare = 1 / AGENT_STEPS.length; // 0.25 for 4 steps
        const overall = Number.isFinite(data.progress) ? data.progress : 0;
        const within = Math.min(1, Math.max(0, (overall - activeIdx * stepShare) / stepShare));
        setSteps((prev) =>
          prev.map((step, i) => {
            if (i < activeIdx) return { ...step, status: 'completed', progress: 1 };
            if (i === activeIdx) {
              return data.event === 'agent_completed'
                ? { ...step, status: 'completed', progress: 1 }
                : { ...step, status: 'running', progress: within || step.progress };
            }
            return step;
          }),
        );
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, [sessionId]);

  useEffect(() => {
    // Pin the logs panel to its newest entry without yanking the whole page
    // down to the logs section. Walk to the nearest scrollable ancestor and
    // set its scrollTop directly instead of using scrollIntoView (which
    // also scrolls every parent).
    const end = logsEndRef.current;
    if (!end) return;
    let node: HTMLElement | null = end.parentElement;
    while (node && node.scrollHeight <= node.clientHeight) {
      node = node.parentElement;
    }
    if (node) node.scrollTop = node.scrollHeight;
  }, [logs]);

  const getStatusColor = (status: string): string => {
    switch (status) {
      case 'completed':
        return 'bg-teal-500';
      case 'running':
        return 'bg-amber-500 animate-pulse';
      case 'failed':
        return 'bg-red-500';
      default:
        return 'bg-slate-700';
    }
  };

  const getStatusIcon = (status: string): string => {
    switch (status) {
      case 'completed':
        return '✓';
      case 'running':
        return '⟳';
      case 'failed':
        return '✗';
      default:
        return '○';
    }
  };

  return (
    <div className="p-6 md:p-10">
      <div className="mx-auto max-w-6xl">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <h1 className="font-display text-2xl font-bold tracking-tight">Pipeline Monitor</h1>
          <div
            className="flex items-center gap-2"
            title={
              connected
                ? 'Live updates: streaming pipeline events from the backend.'
                : 'Live updates offline — pipeline events will not stream until reconnected.'
            }
          >
            <div className={`h-2 w-2 rounded-full ${connected ? 'bg-teal-500' : 'bg-red-500'}`} />
            <span className="font-['Geist_Mono'] text-sm text-slate-400">
              {connected ? 'Live: on' : 'Live: off'}
            </span>
          </div>
        </div>

        {/* Review-ready CTA — nudges the user to the next step in the funnel */}
        {reviewReady && (
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-teal-500/40 bg-teal-500/10 px-5 py-4">
            <div>
              <div className="text-sm font-semibold text-teal-200">Pipeline complete</div>
              <div className="text-xs text-teal-200/70">
                Requirements have been extracted. Review the report and approve.
              </div>
            </div>
            <button
              onClick={() => navigate(`/requirements/${sessionId}`)}
              className="rounded-lg bg-teal-500 px-4 py-2 text-sm font-medium text-bg transition-colors hover:bg-teal-400"
            >
              Review & Approve →
            </button>
          </div>
        )}

        {/* Upload + Run */}
        <div className="mb-6 rounded-xl border border-slate-800 bg-slate-900 p-6">
          <h2 className="mb-4 text-lg font-semibold">Documents</h2>
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor="doc-upload" className="sr-only">
              Upload requirements documents
            </label>
            <input
              id="doc-upload"
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.docx,.xlsx,.txt,.eml,.png,.jpg,.jpeg,.tif,.tiff"
              onChange={(e) => handleUpload(e.target.files)}
              disabled={uploading || !sessionId}
              className="block w-full max-w-xs cursor-pointer rounded-lg border border-slate-700 bg-[#0a0b0f] text-sm text-slate-300 file:mr-3 file:cursor-pointer file:rounded-l-lg file:border-0 file:bg-teal-500/20 file:px-4 file:py-2 file:text-sm file:font-medium file:text-teal-300 hover:file:bg-teal-500/30 disabled:opacity-50"
            />
            <div className="flex items-center gap-2">
              <label htmlFor="sprint-select" className="text-sm text-slate-400">
                Target sprint
              </label>
              <select
                id="sprint-select"
                value={selectedSprint}
                onChange={(e) => setSelectedSprint(e.target.value)}
                disabled={running || uploading || !sessionId}
                className="rounded-lg border border-slate-700 bg-[#0a0b0f] px-3 py-2 text-sm text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 disabled:opacity-50"
              >
                <option value="auto">Auto (by date)</option>
                {sprints.map((s) => (
                  <option key={s.sprint} value={s.sprint}>
                    {s.sprint}
                    {s.is_current ? ' (current)' : ''}
                  </option>
                ))}
              </select>
            </div>
            {uploading && (
              <span className="font-['Geist_Mono'] text-xs text-slate-400">Uploading…</span>
            )}
          </div>
          {/* ADO work-item import — an alternative input to document upload */}
          <div className="mt-4 rounded-lg border border-slate-800 bg-[#0a0b0f]/40 p-4">
            <label htmlFor="wi-refs" className="mb-1 block text-sm text-slate-300">
              Import Azure DevOps work items{' '}
              <span className="text-slate-600">(alternative to uploading a document)</span>
            </label>
            <p className="mb-2 text-xs text-slate-500">
              Paste work item IDs or links, comma-separated — e.g. <code>1234, 1236</code> or{' '}
              <code>https://dev.azure.com/org/proj/_workitems/edit/1234</code>. Either a document
              upload or imported work items is enough to run the pipeline.
            </p>
            <div className="flex flex-wrap items-start gap-3">
              <textarea
                id="wi-refs"
                value={wiRefs}
                onChange={(e) => setWiRefs(e.target.value)}
                disabled={importing || running || uploading || !sessionId}
                rows={2}
                placeholder="1234, 1236, https://dev.azure.com/org/proj/_workitems/edit/1240"
                className="min-w-0 flex-1 resize-y rounded-lg border border-slate-700 bg-[#0a0b0f] p-3 text-sm text-slate-200 placeholder:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 disabled:opacity-50"
              />
              <button
                onClick={handleImportWorkItems}
                disabled={importing || running || uploading || !wiRefs.trim() || !sessionId}
                className="rounded-lg border border-teal-500/40 bg-teal-500/10 px-4 py-2 text-sm font-medium text-teal-300 transition-colors hover:bg-teal-500/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {importing ? 'Importing…' : 'Import work items'}
              </button>
            </div>
            {importedItems.length > 0 && (
              <p className="mt-2 font-['Geist_Mono'] text-xs text-teal-300/80">
                ✓ Imported {importedItems.length} work item{importedItems.length === 1 ? '' : 's'}:{' '}
                {importedItems.map((id) => `#${id}`).join(', ')}
              </p>
            )}
          </div>
          {/* Optional run instructions — collapsible to save space */}
          <div className="mt-4">
            <label className="inline-flex cursor-pointer items-center gap-3 text-sm text-slate-400">
              <span className="text-slate-400">
                Additional Instructions <span className="text-slate-600">(optional)</span>
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={showInstructions}
                onClick={() => setShowInstructions((v) => !v)}
                className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 ${showInstructions ? 'bg-teal-500' : 'bg-slate-700'}`}
              >
                <span
                  className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${showInstructions ? 'translate-x-[18px]' : 'translate-x-[3px]'}`}
                />
              </button>
            </label>
            {showInstructions && (
              <div className="mt-2">
                <textarea
                  id="run-instructions"
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  disabled={running || uploading || !sessionId}
                  rows={3}
                  maxLength={2000}
                  placeholder="Optional guidance for this run — e.g. 'focus on security requirements', 'treat this as a brownfield change', 'split large stories aggressively'."
                  className="w-full resize-y rounded-lg border border-slate-700 bg-[#0a0b0f] p-3 text-sm text-slate-200 placeholder:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 disabled:opacity-50"
                />
                <p className="mt-1 text-right font-['Geist_Mono'] text-xs text-slate-600">
                  {instructions.length}/2000
                </p>
              </div>
            )}
          </div>
          {/* Run pipeline */}
          <div className="mt-4">
            <button
              onClick={handleRun}
              disabled={
                running ||
                uploading ||
                importing ||
                (uploadedFiles.length === 0 && importedItems.length === 0) ||
                !sessionId
              }
              className="rounded-lg bg-teal-500 px-4 py-2 text-sm font-medium text-slate-900 transition-colors hover:bg-teal-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-300 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-500"
            >
              {running ? 'Starting…' : 'Run pipeline →'}
            </button>
          </div>
          {uploadedFiles.length > 0 && (
            <ul className="mt-3 space-y-1 font-['Geist_Mono'] text-xs text-slate-400">
              {uploadedFiles.map((name, i) => (
                <li key={`${name}-${i}`}>✓ {name}</li>
              ))}
            </ul>
          )}
          {actionError && (
            <p className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
              {actionError}
            </p>
          )}
        </div>

        {/* Agent Pipeline + Live Logs side-by-side to avoid scrolling */}
        <div className="flex flex-col gap-4 lg:flex-row">
          {/* Agent Stepper (compact, hugs its content) */}
          <div className="w-full shrink-0 rounded-xl border border-slate-800 bg-slate-900 p-4 lg:w-fit lg:max-w-xs">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-300">
              Agent Pipeline
            </h2>
            <div className="space-y-2">
              {steps.map((step) => (
                <div key={step.name} className="flex items-center gap-3">
                  <div
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${getStatusColor(step.status)}`}
                  >
                    {getStatusIcon(step.status)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-3 whitespace-nowrap">
                      <span className="font-mono text-xs">{stepLabel(step.name)}</span>
                      {step.confidence !== undefined && (
                        <span className="shrink-0 text-2xs text-slate-400">
                          {(step.confidence * 100).toFixed(0)}%
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-2xs text-slate-500">{STEP_DESCRIPTIONS[step.name]}</p>
                    {step.status === 'running' && (
                      <div className="mt-1 h-0.5 overflow-hidden rounded-full bg-slate-800">
                        <div
                          className="h-full bg-teal-500 transition-all duration-500"
                          style={{ width: `${step.progress * 100}%` }}
                        />
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Live Logs — fills the remaining space */}
          <div className="min-w-0 flex-1 rounded-xl border border-slate-800 bg-slate-900 p-4">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-300">
              Live Logs
            </h2>
            <div className="h-[22rem] overflow-y-auto rounded-lg bg-[#0a0b0f] p-3 font-mono text-xs">
              {logs.length === 0 ? (
                <p className="text-slate-600">Waiting for pipeline events...</p>
              ) : (
                logs.map((log, idx) => (
                  <div key={idx} className="py-0.5 text-slate-300">
                    {log}
                  </div>
                ))
              )}
              <div ref={logsEndRef} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
