import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { DocumentsPage } from "./pages/DocumentsPage";
import { GraphPage } from "./pages/GraphPage";
import { SearchPage } from "./pages/SearchPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StatusPage } from "./pages/StatusPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/documents" replace />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="graph" element={<GraphPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="status" element={<StatusPage />} />
        <Route path="*" element={<Navigate to="/documents" replace />} />
      </Route>
    </Routes>
  );
}
