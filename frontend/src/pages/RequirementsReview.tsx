import { useEffect, useState, useRef } from 'react';
import { useParams } from 'react-router-dom';

export default function RequirementsReview() {
  const { sessionId = '' } = useParams<{ sessionId: string }>();
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const apiUrl = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';
  const reportUrl = `${apiUrl}/sessions/${sessionId}/report/html`;

  // State
  const [approved, setApproved] = useState(false);
  const [approving, setApproving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showRevokeDialog, setShowRevokeDialog] = useState(false);
  const [revoking, setRevoking] = useState(false);

  // Check status on mount — only hydrate approval if user explicitly approved
  useEffect(() => {
    if (!sessionId) return;
    const ctrl = new AbortController();
    (async () => {
      try {
        const res = await fetch(`${apiUrl}/sessions/${sessionId}/status`, { signal: ctrl.signal });
        if (!res.ok) return;
        const data = await res.json();
        if (data.approved_for_push) setApproved(true);
      } catch {
        /* non-fatal */
      }
    })();
    return () => ctrl.abort();
  }, [sessionId, apiUrl]);

  // Approve
  const handleApprove = async () => {
    setApproving(true);
    setError(null);
    try {
      const reqsRes = await fetch(`${apiUrl}/sessions/${sessionId}/requirements`);
      if (reqsRes.ok) {
        const reqs = await reqsRes.json();
        const ids = reqs.map((r: { id: string }) => r.id);
        if (ids.length) {
          await fetch(`${apiUrl}/sessions/${sessionId}/requirements/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ requirement_ids: ids }),
          });
        }
      }
      const res = await fetch(`${apiUrl}/sessions/${sessionId}/approve-for-push`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Approval failed (${res.status}): ${text}`);
      }
      const data = await res.json();
      setApproved(true);
      if (data.generating_work_items) {
        setGenerating(true);
        pollForWorkItems();
      } else {
        refreshReport();
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setApproving(false);
    }
  };

  const pollForWorkItems = () => {
    let attempts = 0;
    const interval = setInterval(async () => {
      attempts++;
      try {
        const res = await fetch(`${apiUrl}/sessions/${sessionId}/workitems`);
        if (res.ok) {
          const items = await res.json();
          if (items.length > 0) {
            clearInterval(interval);
            setGenerating(false);
            refreshReport();
          }
        }
      } catch {
        /* keep polling */
      }
      if (attempts >= 60) {
        clearInterval(interval);
        setGenerating(false);
        setError('Work item generation is taking longer than expected. Refresh in a minute.');
      }
    }, 5000);
  };

  const refreshReport = () => {
    if (iframeRef.current) {
      iframeRef.current.src = reportUrl + '?t=' + Date.now();
    }
  };

  const handleRevoke = async () => {
    setRevoking(true);
    setError(null);
    try {
      const res = await fetch(`${apiUrl}/sessions/${sessionId}/revoke-push-approval`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Revoke failed (${res.status}): ${text}`);
      }
      setApproved(false);
      setShowRevokeDialog(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRevoking(false);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Top bar — Approve/Approved only */}
      <div className="shrink-0 border-b border-slate-800 bg-slate-900 px-6 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <div>
            <h1 className="font-display text-lg font-bold tracking-tight">Review & Approve</h1>
            <p className="mt-0.5 text-xs text-slate-400">
              Review the AI-generated plan below, then approve.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {generating && (
              <span className="flex items-center gap-2 text-sm text-amber-400">
                <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-400" />
                Generating work items…
              </span>
            )}
            {!approved && (
              <button
                onClick={handleApprove}
                disabled={approving || !sessionId}
                className="rounded-lg bg-teal-500 px-5 py-2.5 text-sm font-medium text-slate-900 transition-colors hover:bg-teal-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-500"
              >
                {approving ? 'Approving…' : '✓ Approve Plan'}
              </button>
            )}
            {approved && (
              <button
                onClick={() => setShowRevokeDialog(true)}
                className="flex items-center gap-2 rounded-lg border border-teal-500/30 bg-teal-500/10 px-4 py-2 text-sm font-medium text-teal-300 transition-colors hover:border-red-500/30 hover:bg-red-500/10 hover:text-red-300"
                title="Click to revoke approval"
              >
                ✓ Approved
              </button>
            )}
            <button
              onClick={refreshReport}
              className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-700"
              title="Refresh report"
            >
              ⟳
            </button>
          </div>
        </div>
        {error && (
          <div className="mx-auto mt-3 max-w-7xl rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-300">
            {error}
            <button
              onClick={() => setError(null)}
              className="ml-3 text-red-200/60 hover:text-red-100"
            >
              ✕
            </button>
          </div>
        )}
      </div>

      {/* Report iframe */}
      <div className="min-h-[50vh] flex-1">
        <iframe
          ref={iframeRef}
          src={reportUrl}
          className="h-full w-full border-0"
          style={{ minHeight: '50vh' }}
          title="Session Report"
          sandbox="allow-same-origin"
        />
      </div>

      {/* Revoke approval confirmation dialog */}
      {showRevokeDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-full max-w-sm rounded-xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
            <h3 className="text-base font-semibold text-slate-100">Revoke Approval?</h3>
            <p className="mt-2 text-sm text-slate-400">
              This will remove your approval and reset the plan status. You will need to approve
              again before proceeding.
            </p>
            <div className="mt-5 flex justify-end gap-3">
              <button
                onClick={() => setShowRevokeDialog(false)}
                className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-sm text-slate-300 hover:bg-slate-700"
              >
                Cancel
              </button>
              <button
                onClick={handleRevoke}
                disabled={revoking}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {revoking ? 'Revoking…' : 'Revoke Approval'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
