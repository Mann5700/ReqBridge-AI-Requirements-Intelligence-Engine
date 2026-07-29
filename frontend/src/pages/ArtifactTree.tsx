import { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { workItemColor } from '../lib/workItemColors';

interface WorkItemNode {
  id?: string;
  work_item_type: string;
  title: string;
  description?: string;
  acceptance_criteria?: string;
  story_points: number | null;
  effort_hours?: number | null;
  tshirt_size?: string | null;
  children?: WorkItemNode[];
}

function totalPoints(nodes: WorkItemNode[]): number {
  let pts = 0;
  const walk = (list: WorkItemNode[]) => {
    for (const n of list) {
      if (n.work_item_type === 'User Story' && typeof n.story_points === 'number')
        pts += n.story_points;
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes);
  return pts;
}

function totalHours(nodes: WorkItemNode[]): number {
  let hrs = 0;
  const walk = (list: WorkItemNode[]) => {
    for (const n of list) {
      if (n.work_item_type === 'Task' && typeof n.effort_hours === 'number') hrs += n.effort_hours;
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes);
  return hrs;
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

function buildPrintTree(nodes: WorkItemNode[], depth = 0): string {
  let html = '';
  for (const node of nodes) {
    const c = workItemColor(node.work_item_type);
    const indent = '&nbsp;&nbsp;&nbsp;'.repeat(depth);
    const connector = depth > 0 ? '├── ' : '';
    let size = '';
    if (node.work_item_type === 'Feature' && node.tshirt_size) size = `[${node.tshirt_size}]`;
    else if (node.work_item_type === 'User Story' && node.story_points)
      size = `[${node.story_points} pts]`;
    else if (node.work_item_type === 'Task' && node.effort_hours)
      size = `[${node.effort_hours} hrs]`;
    html += `<div class="node">${indent}${connector}<span class="dot" style="background:${c.hex}"></span><span class="type" style="color:${c.hex}">${node.work_item_type}:</span> ${node.title}${size ? `<span class="size">${size}</span>` : ''}</div>`;
    if (node.children?.length) html += buildPrintTree(node.children, depth + 1);
  }
  return html;
}

export default function ArtifactTree() {
  const { sessionId = '' } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const apiUrl = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

  const [workItems, setWorkItems] = useState<WorkItemNode[]>([]);
  const [expandedStoryId, setExpandedStoryId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const treeRef = useRef<HTMLDivElement>(null);

  const handleDownloadPdf = () => {
    if (!treeRef.current) return;
    const printWindow = window.open('', '_blank');
    if (!printWindow) return;
    printWindow.document.write(`
      <html><head><title>Artifact Tree - ${sessionId}</title>
      <style>
        body { font-family: 'Courier New', monospace; font-size: 12px; padding: 24px; color: #1e293b; }
        .header { font-family: sans-serif; margin-bottom: 16px; }
        .header h1 { font-size: 18px; margin: 0; }
        .header p { font-size: 12px; color: #64748b; margin: 4px 0 0; }
        .stats { font-family: sans-serif; font-size: 13px; margin-bottom: 12px; color: #475569; }
        .tree { line-height: 1.8; }
        .node { display: flex; align-items: center; gap: 6px; }
        .dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
        .type { font-weight: 600; font-size: 11px; }
        .size { margin-left: auto; font-size: 11px; color: #92400e; }
      </style></head><body>
      <div class="header">
        <h1>Artifact Tree</h1>
        <p>Session: ${sessionId}</p>
      </div>
      <div class="stats">${totalCount(workItems)} items · ${pts} pts · ${hrs.toFixed(1)} hrs</div>
      <div class="tree">${buildPrintTree(workItems)}</div>
      </body></html>
    `);
    printWindow.document.close();
    printWindow.onload = () => {
      printWindow.print();
      printWindow.close();
    };
  };

  useEffect(() => {
    if (!sessionId) return;
    const ctrl = new AbortController();
    (async () => {
      try {
        const res = await fetch(`${apiUrl}/sessions/${sessionId}/workitems`, {
          signal: ctrl.signal,
        });
        if (res.ok) setWorkItems(await res.json());
      } catch {
        /* non-fatal */
      } finally {
        setLoading(false);
      }
    })();
    return () => ctrl.abort();
  }, [sessionId, apiUrl]);

  const wiCounts = countByType(workItems);
  const pts = totalPoints(workItems);
  const hrs = totalHours(workItems);

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Header */}
      <div className="shrink-0 border-b border-slate-800 bg-slate-900 px-6 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <div>
            <h1 className="font-display text-lg font-bold tracking-tight">Artifact Tree</h1>
            <p className="mt-0.5 text-xs text-slate-400">
              Generated work item hierarchy — click a User Story for details.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate(`/requirements/${sessionId}`)}
              className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-sm text-slate-300 hover:bg-slate-700"
            >
              ← Review
            </button>
            <button
              onClick={() => navigate(`/push/${sessionId}`)}
              className="rounded-lg bg-teal-500 px-5 py-2.5 text-sm font-medium text-slate-900 transition-colors hover:bg-teal-400"
            >
              Push & Sync →
            </button>
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="mx-auto w-full max-w-7xl flex-1 px-6 py-6">
        {loading && (
          <div className="flex items-center gap-2 text-sm text-slate-400">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-teal-400" />
            Loading work items…
          </div>
        )}

        {!loading && workItems.length === 0 && (
          <p className="text-sm text-slate-500">
            No work items generated yet. Approve the plan first.
          </p>
        )}

        {workItems.length > 0 && (
          <>
            {/* Summary stats */}
            <div className="mb-5 flex flex-wrap items-center gap-4 text-sm text-slate-400">
              <span>
                <span className="font-bold text-white">{totalCount(workItems)}</span> items
              </span>
              <span>
                <span className="font-bold text-amber-300">{pts}</span> pts
              </span>
              <span>
                <span className="font-bold text-slate-200">{hrs.toFixed(1)}</span> hrs
              </span>
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

            {/* Collapsible tree */}
            <div className="mb-2 flex justify-end">
              <button
                onClick={handleDownloadPdf}
                className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-700"
              >
                ↓ Download PDF
              </button>
            </div>
            <div
              ref={treeRef}
              className="mb-6 rounded-lg border border-slate-700 bg-slate-800/60 p-5"
            >
              <div className="font-mono text-sm leading-7 text-slate-300">
                {workItems.map((root) => (
                  <TreeNode
                    key={root.id || root.title}
                    node={root}
                    depth={0}
                    onStoryClick={setExpandedStoryId}
                    expandedId={expandedStoryId}
                  />
                ))}
              </div>
            </div>

            {/* Expanded Story Detail */}
            {expandedStoryId && (
              <StoryDetail
                workItems={workItems}
                storyId={expandedStoryId}
                onBack={() => setExpandedStoryId(null)}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ─── Tree Node Component ──────────────────────────────────────────────────── */
function TreeNode({
  node,
  depth,
  onStoryClick,
  expandedId,
}: {
  node: WorkItemNode;
  depth: number;
  onStoryClick: (id: string) => void;
  expandedId: string | null;
}) {
  const hasChildren = !!(node.children && node.children.length > 0);
  const isCollapsible =
    hasChildren && (node.work_item_type === 'Feature' || node.work_item_type === 'User Story');
  const [collapsed, setCollapsed] = useState(false);

  const c = workItemColor(node.work_item_type);
  const isStory = node.work_item_type === 'User Story';
  const isFeature = node.work_item_type === 'Feature';
  const isTask = node.work_item_type === 'Task';
  const isDetailExpanded = expandedId === (node.id || node.title);
  const indent = depth * 24;

  // Sizing display per type
  let sizeLabel: string | null = null;
  if (isFeature && node.tshirt_size) {
    sizeLabel = `[${node.tshirt_size}]`;
  } else if (isStory && node.story_points) {
    sizeLabel = `[${node.story_points} pts]`;
  } else if (isTask && node.effort_hours) {
    sizeLabel = `[${node.effort_hours} hrs]`;
  }

  const handleClick = () => {
    if (isStory) {
      onStoryClick(node.id || node.title);
    } else if (isCollapsible) {
      setCollapsed(!collapsed);
    }
  };

  return (
    <>
      <div
        style={{ paddingLeft: `${indent}px` }}
        className={`flex items-center gap-2 rounded px-2 py-1 transition-all duration-150 ${
          isCollapsible || isStory
            ? 'cursor-pointer hover:translate-x-0.5 hover:bg-slate-700/60'
            : ''
        } ${isDetailExpanded ? 'bg-slate-700/40 ring-1 ring-blue-500/40' : ''}`}
        onClick={handleClick}
      >
        {isCollapsible && (
          <span className="select-none text-xs text-slate-500">{collapsed ? '▶' : '▼'}</span>
        )}
        {!isCollapsible && depth > 0 && <span className="select-none text-slate-600">├──</span>}
        <span
          className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
          style={{ background: c.hex }}
        />
        <span className="text-xs font-semibold" style={{ color: c.hex }}>
          {node.work_item_type}:
        </span>
        <span className={`text-sm ${isStory ? 'font-medium text-slate-100' : 'text-slate-300'}`}>
          {node.title}
        </span>
        {sizeLabel && (
          <span className="ml-auto shrink-0 font-mono text-xs text-amber-300/80">{sizeLabel}</span>
        )}
      </div>
      {!collapsed &&
        node.children?.map((child) => (
          <TreeNode
            key={child.id || child.title}
            node={child}
            depth={depth + 1}
            onStoryClick={onStoryClick}
            expandedId={expandedId}
          />
        ))}
    </>
  );
}

/* ─── Story Detail Panel ───────────────────────────────────────────────────── */
function StoryDetail({
  workItems,
  storyId,
  onBack,
}: {
  workItems: WorkItemNode[];
  storyId: string;
  onBack: () => void;
}) {
  const findStory = (nodes: WorkItemNode[]): WorkItemNode | null => {
    for (const n of nodes) {
      if ((n.id || n.title) === storyId && n.work_item_type === 'User Story') return n;
      if (n.children) {
        const found = findStory(n.children);
        if (found) return found;
      }
    }
    return null;
  };

  const story = findStory(workItems);
  if (!story) return null;

  const pts = story.story_points ?? 0;
  const totalTaskHrs = (story.children?.filter((c) => c.work_item_type === 'Task') ?? []).reduce(
    (sum, t) => sum + (t.effort_hours ?? 0),
    0,
  );
  const tasks = story.children?.filter((c) => c.work_item_type === 'Task') ?? [];
  const testCases = story.children?.filter((c) => c.work_item_type === 'Test Case') ?? [];

  return (
    <div className="mb-6 rounded-lg border border-sky-500/30 bg-slate-800/80 p-5 duration-200 animate-in fade-in slide-in-from-top-2">
      <button
        onClick={onBack}
        className="mb-3 flex cursor-pointer items-center gap-1.5 text-xs text-slate-400 transition-colors hover:text-white"
      >
        ← Back to tree
      </button>

      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-sky-200">{story.title}</h3>
          <p className="mt-1 font-mono text-xs text-slate-400">
            {pts} pts · {totalTaskHrs.toFixed(1)} hrs · {tasks.length} task
            {tasks.length !== 1 ? 's' : ''}
          </p>
        </div>
        <span className="shrink-0 rounded bg-sky-500/20 px-2 py-0.5 text-xs font-semibold text-sky-300">
          User Story
        </span>
      </div>

      {story.description && (
        <div className="mt-3 whitespace-pre-wrap rounded border border-slate-700 bg-slate-900/50 p-3 text-sm leading-relaxed text-slate-300">
          {story.description}
        </div>
      )}

      {story.acceptance_criteria && (
        <div className="mt-3">
          <p className="mb-1.5 text-xs font-semibold text-slate-400">Acceptance Criteria</p>
          <div className="whitespace-pre-wrap rounded border border-slate-700 bg-slate-900/50 p-3 text-sm leading-relaxed text-slate-300">
            {story.acceptance_criteria}
          </div>
        </div>
      )}

      {tasks.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-xs font-semibold text-slate-400">Tasks ({tasks.length})</p>
          <div className="overflow-hidden rounded-lg border border-slate-700">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-800 text-xs text-slate-400">
                  <th className="px-3 py-2 text-left font-medium">Task</th>
                  <th className="w-20 px-3 py-2 text-right font-medium">Hours</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {tasks.map((task) => {
                  const tHrs = task.effort_hours ?? 0;
                  return (
                    <tr
                      key={task.id || task.title}
                      className="transition-colors hover:bg-slate-700/30"
                    >
                      <td className="px-3 py-2 text-slate-200">{task.title}</td>
                      <td className="px-3 py-2 text-right font-mono text-amber-300/80">
                        {tHrs ? tHrs.toFixed(1) : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {testCases.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-xs font-semibold text-slate-400">
            Test Cases ({testCases.length})
          </p>
          <div className="space-y-1">
            {testCases.map((tc) => (
              <div
                key={tc.id || tc.title}
                className="rounded border border-slate-700 bg-slate-900/40 px-3 py-2 text-xs text-slate-300"
              >
                {tc.title}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
