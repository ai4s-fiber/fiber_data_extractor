import { Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './stores/auth';
import { ProjectProvider } from './stores/project';
import LoginPage from './pages/LoginPage';
import WorkspaceLayout from './pages/WorkspaceLayout';
import ProjectsPage from './pages/ProjectsPage';
import PapersPage from './pages/PapersPage';
import ReviewPage from './pages/ReviewPage';
import ExportPage from './pages/ExportPage';
import MembersPage from './pages/MembersPage';
import UserManagementPage from './pages/UserManagementPage';
import SettingsPage from './pages/SettingsPage';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <ProjectProvider>
              <WorkspaceLayout />
            </ProjectProvider>
          </ProtectedRoute>
        }
      >
        <Route index element={<ProjectsPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="papers" element={<PapersPage />} />
        <Route path="review" element={<ReviewPage />} />
        <Route path="export" element={<ExportPage />} />
        <Route path="members" element={<MembersPage />} />
        <Route path="users" element={<UserManagementPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}


function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}

export default App;
