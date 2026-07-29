import { useHydratedAdoConfig } from '../lib/adoConfig';

interface Props {
  /** Compact = 1-line summary; expanded = full editable form. */
  variant?: 'compact' | 'full';
}

/**
 * Shared ADO credentials form. Hydrates from local/sessionStorage on mount
 * and writes through on every change so other pages see updates immediately.
 */
export default function AdoConfigPanel({ variant = 'full' }: Props) {
  const cfg = useHydratedAdoConfig();
  const configured = Boolean(cfg.orgUrl && cfg.pat);

  if (variant === 'compact') {
    return (
      <div className="flex items-center gap-2 text-2xs text-fg-subtle">
        <span className={`h-2 w-2 rounded-full ${configured ? 'bg-teal-500' : 'bg-amber-500'}`} />
        <span className="font-mono">
          ADO {configured ? 'configured' : 'not configured'}
          {cfg.orgUrl && ` · ${new URL(cfg.orgUrl).host}`}
          {cfg.project && ` · ${cfg.project}`}
        </span>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-6">
      <h2 className="mb-1 text-lg font-semibold">Azure DevOps connection</h2>
      <p className="mb-4 text-xs text-slate-400">
        Stored locally in this browser. Org/Project persist across sessions; the PAT lives only
        until you close this tab.
      </p>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div>
          <label htmlFor="ado-org-url" className="mb-1 block font-mono text-xs text-slate-400">
            Organization URL
          </label>
          <input
            id="ado-org-url"
            type="text"
            value={cfg.orgUrl}
            onChange={(e) => cfg.setField('orgUrl', e.target.value)}
            placeholder="https://dev.azure.com/your-org"
            className="w-full rounded-lg border border-slate-700 bg-[#0a0b0f] px-3 py-2 text-sm focus:border-[#0078d4] focus:outline-none"
          />
        </div>
        <div>
          <label htmlFor="ado-project" className="mb-1 block font-mono text-xs text-slate-400">
            Project
          </label>
          <input
            id="ado-project"
            type="text"
            value={cfg.project}
            onChange={(e) => cfg.setField('project', e.target.value)}
            placeholder="MyProject"
            className="w-full rounded-lg border border-slate-700 bg-[#0a0b0f] px-3 py-2 text-sm focus:border-[#0078d4] focus:outline-none"
          />
        </div>
        <div>
          <label htmlFor="ado-pat" className="mb-1 block font-mono text-xs text-slate-400">
            Personal Access Token
          </label>
          <input
            id="ado-pat"
            type="password"
            value={cfg.pat}
            onChange={(e) => cfg.setField('pat', e.target.value)}
            placeholder="••••••••"
            autoComplete="off"
            className="w-full rounded-lg border border-slate-700 bg-[#0a0b0f] px-3 py-2 text-sm focus:border-[#0078d4] focus:outline-none"
          />
        </div>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <span
          className={`inline-flex items-center gap-2 rounded-md border px-2 py-1 text-2xs ${
            configured
              ? 'border-teal-500/30 bg-teal-500/10 text-teal-300'
              : 'border-amber-500/30 bg-amber-500/10 text-amber-300'
          }`}
        >
          <span className={`h-2 w-2 rounded-full ${configured ? 'bg-teal-400' : 'bg-amber-400'}`} />
          {configured ? 'Ready' : 'Org URL and PAT required'}
        </span>
        {cfg.pat && (
          <button
            onClick={cfg.clearPat}
            className="text-2xs text-slate-400 underline hover:text-slate-200"
          >
            Clear PAT
          </button>
        )}
      </div>
    </div>
  );
}
