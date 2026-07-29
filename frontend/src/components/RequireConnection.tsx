import { Navigate, useParams } from 'react-router-dom';
import { useHydratedAdoConfig, useAdoConfig } from '../lib/adoConfig';

interface Props {
  children: React.ReactNode;
}

/**
 * Wraps a workspace route so it can only render when there's an active
 * connection. If the user lands on a deep link (or types the URL directly)
 * without first going through Home, we bounce them to Home so they can
 * connect. The hydration check prevents a flash-redirect on first paint.
 */
export default function RequireConnection({ children }: Props) {
  const cfg = useHydratedAdoConfig();
  const { sessionId } = useParams<{ sessionId: string }>();
  const hydrated = useAdoConfig((s) => s.hydrated);

  // Wait for hydration before making any routing decisions — otherwise
  // activeSessionId is '' and we'd flash-redirect to Home.
  if (!hydrated) return null;

  if (!cfg.activeSessionId) {
    return <Navigate to="/" replace />;
  }
  // If the URL points at a stale session id, snap to the active one so the
  // sidebar's "Session" pill and the page params stay consistent.
  if (sessionId && sessionId !== cfg.activeSessionId) {
    const path = window.location.pathname.split('/').slice(1, 2).join('/');
    return <Navigate to={`/${path}/${cfg.activeSessionId}`} replace />;
  }
  return <>{children}</>;
}
