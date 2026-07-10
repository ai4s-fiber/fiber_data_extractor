import { Navigate, Route, Routes } from 'react-router-dom';
import { ProjectProvider } from './stores/project';
import { ExtractionProvider } from './contexts/ExtractionContext';
import WorkspaceLayout from './pages/WorkspaceLayout';
import ProjectsPage from './pages/ProjectsPage';
import PapersPage from './pages/PapersPage';
import ReviewPage from './pages/ReviewPage';
import ExportPage from './pages/ExportPage';
import SettingsPage from './pages/SettingsPage';

function AppRoutes() {
  return (
    <ProjectProvider>
      <ExtractionProvider>
        <Routes>
          <Route path="/" element={<WorkspaceLayout />}>
            <Route index element={<ProjectsPage />} />
            <Route path="projects" element={<ProjectsPage />} />
            <Route path="papers" element={<PapersPage />} />
            <Route path="review" element={<ReviewPage />} />
            <Route path="export" element={<ExportPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </ExtractionProvider>
    </ProjectProvider>
  );
}

export default function App() {
  return <AppRoutes />;
}
