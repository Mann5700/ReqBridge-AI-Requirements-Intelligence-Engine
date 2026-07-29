/**
 * Azure DevOps standard work-item-type colors.
 * Reused across the report tree, push & sync chips, and any future UI
 * surface that displays Epic/Feature/Story/Task/Test Case.
 */

export interface WorkItemColor {
  bg: string;
  text: string;
  border: string;
  hex: string;
}

const ADO_COLORS: Record<string, WorkItemColor> = {
  Epic: {
    hex: '#FF7B00',
    bg: 'bg-orange-500/15',
    text: 'text-orange-300',
    border: 'border-orange-500/40',
  },
  Feature: {
    hex: '#773B93',
    bg: 'bg-fuchsia-500/15',
    text: 'text-fuchsia-300',
    border: 'border-fuchsia-500/40',
  },
  'User Story': {
    hex: '#009CCC',
    bg: 'bg-sky-500/15',
    text: 'text-sky-300',
    border: 'border-sky-500/40',
  },
  Task: {
    hex: '#F2CB1D',
    bg: 'bg-yellow-500/15',
    text: 'text-yellow-300',
    border: 'border-yellow-500/40',
  },
  'Test Case': {
    hex: '#004B50',
    bg: 'bg-emerald-500/15',
    text: 'text-emerald-300',
    border: 'border-emerald-500/40',
  },
  Bug: {
    hex: '#CC293D',
    bg: 'bg-red-500/15',
    text: 'text-red-300',
    border: 'border-red-500/40',
  },
};

const FALLBACK: WorkItemColor = {
  hex: '#64748b',
  bg: 'bg-slate-700/40',
  text: 'text-slate-300',
  border: 'border-slate-600',
};

export function workItemColor(type: string): WorkItemColor {
  return ADO_COLORS[type] ?? FALLBACK;
}
