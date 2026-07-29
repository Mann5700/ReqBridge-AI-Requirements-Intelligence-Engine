import { useEffect } from 'react';
import { create } from 'zustand';

/**
 * Per-browser ADO credentials store.
 *
 * - `orgUrl` and `project` persist to localStorage (low-sensitivity).
 * - `pat` lives in sessionStorage only — wiped when the browser tab closes —
 *   so we never leave a Personal Access Token sitting on disk.
 *
 * The Pipeline page and the ADO Sync page both read from this store and ship
 * the values along with every request that touches Azure DevOps.
 */

const LS_KEY = 'reqbridge.adoConfig';
const SS_PAT_KEY = 'reqbridge.adoPat';
const LS_SESSION_KEY = 'reqbridge.activeSessionId';

export interface AdoConfig {
  orgUrl: string;
  project: string;
  pat: string;
}

interface AdoConfigStore extends AdoConfig {
  hydrated: boolean;
  /** ID of the currently-connected backend session, '' when disconnected. */
  activeSessionId: string;
  /** Display name of the authenticated ADO user, '' when unknown. In-memory
   *  only — we re-fetch it whenever the user reconnects. */
  userName: string;
  setField: (key: keyof AdoConfig, value: string) => void;
  setAll: (next: Partial<AdoConfig>) => void;
  hydrate: () => void;
  clearPat: () => void;
  setActiveSessionId: (id: string) => void;
  setUserName: (name: string) => void;
  disconnect: () => void;
}

const safeRead = (storage: Storage, key: string): string => {
  try {
    return storage.getItem(key) ?? '';
  } catch {
    return '';
  }
};

const safeWrite = (storage: Storage, key: string, value: string) => {
  try {
    if (value) storage.setItem(key, value);
    else storage.removeItem(key);
  } catch {
    /* quota / availability — ignore, in-memory still works */
  }
};

export const useAdoConfig = create<AdoConfigStore>((set, get) => ({
  orgUrl: '',
  project: '',
  pat: '',
  hydrated: false,
  activeSessionId: '',
  userName: '',

  hydrate: () => {
    if (get().hydrated) return;
    let orgUrl = '';
    let project = '';
    try {
      const saved = localStorage.getItem(LS_KEY);
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<AdoConfig>;
        orgUrl = parsed.orgUrl ?? '';
        project = parsed.project ?? '';
      }
    } catch {
      /* corrupt blob — fall through to defaults */
    }
    const pat = safeRead(sessionStorage, SS_PAT_KEY);
    const activeSessionId = safeRead(localStorage, LS_SESSION_KEY);
    set({ orgUrl, project, pat, activeSessionId, hydrated: true });
  },

  setField: (key, value) => {
    set({ [key]: value } as Partial<AdoConfig>);
    const { orgUrl, project } = get();
    if (key === 'orgUrl' || key === 'project') {
      safeWrite(localStorage, LS_KEY, JSON.stringify({ orgUrl, project }));
    } else if (key === 'pat') {
      safeWrite(sessionStorage, SS_PAT_KEY, value);
    }
  },

  setAll: (next) => {
    set(next);
    const { orgUrl, project, pat } = get();
    safeWrite(localStorage, LS_KEY, JSON.stringify({ orgUrl, project }));
    safeWrite(sessionStorage, SS_PAT_KEY, pat);
  },

  clearPat: () => {
    set({ pat: '' });
    safeWrite(sessionStorage, SS_PAT_KEY, '');
  },

  setActiveSessionId: (id) => {
    set({ activeSessionId: id });
    safeWrite(localStorage, LS_SESSION_KEY, id);
  },

  setUserName: (name) => {
    set({ userName: name });
  },

  disconnect: () => {
    // Clear the live PAT and session pointer; keep org/project so the user
    // doesn't have to retype them on the next connect.
    set({ pat: '', activeSessionId: '', userName: '' });
    safeWrite(sessionStorage, SS_PAT_KEY, '');
    safeWrite(localStorage, LS_SESSION_KEY, '');
  },
}));

/**
 * Hook helper: ensures the store is hydrated from storage on first render.
 * Components that need the credentials should call this once.
 */
export const useHydratedAdoConfig = (): AdoConfig & {
  setField: AdoConfigStore['setField'];
  clearPat: AdoConfigStore['clearPat'];
  activeSessionId: string;
  userName: string;
  setActiveSessionId: AdoConfigStore['setActiveSessionId'];
  setUserName: AdoConfigStore['setUserName'];
  disconnect: AdoConfigStore['disconnect'];
} => {
  const store = useAdoConfig();
  useEffect(() => {
    store.hydrate();
    // Intentionally run once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return {
    orgUrl: store.orgUrl,
    project: store.project,
    pat: store.pat,
    setField: store.setField,
    clearPat: store.clearPat,
    activeSessionId: store.activeSessionId,
    userName: store.userName,
    setActiveSessionId: store.setActiveSessionId,
    setUserName: store.setUserName,
    disconnect: store.disconnect,
  };
};

/** Convenience: serialise creds for an HTTP request body. */
export const adoCredsBody = (cfg: AdoConfig) => ({
  ado_org_url: cfg.orgUrl || undefined,
  ado_project: cfg.project || undefined,
  ado_pat: cfg.pat || undefined,
});
