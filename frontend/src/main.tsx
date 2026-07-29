import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import './index.css';

import AppLayout from './components/AppLayout';
import ErrorBoundary from './components/ErrorBoundary';
import RequireConnection from './components/RequireConnection';
import Home from './pages/Home';
import PipelineMonitor from './pages/PipelineMonitor';
import RequirementsReview from './pages/RequirementsReview';
import ArtifactTree from './pages/ArtifactTree';
import PushToADO from './pages/PushToADO';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1 },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route element={<AppLayout />}>
              <Route
                path="/pipeline/:sessionId"
                element={
                  <RequireConnection>
                    <PipelineMonitor />
                  </RequireConnection>
                }
              />
              <Route
                path="/requirements/:sessionId"
                element={
                  <RequireConnection>
                    <RequirementsReview />
                  </RequireConnection>
                }
              />
              <Route
                path="/artifacts/:sessionId"
                element={
                  <RequireConnection>
                    <ArtifactTree />
                  </RequireConnection>
                }
              />
              <Route
                path="/push/:sessionId"
                element={
                  <RequireConnection>
                    <PushToADO />
                  </RequireConnection>
                }
              />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ErrorBoundary>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
