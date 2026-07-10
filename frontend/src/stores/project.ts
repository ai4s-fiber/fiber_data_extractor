/**
 * Simple project context for current selected project.
 * Persists selection to localStorage so it survives page refresh.
 */
import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface Project {
  id: number;
  name: string;
  description?: string;
  paper_count?: number;
  pending_count?: number;
  approved_count?: number;
}

interface ProjectContextType {
  currentProject: Project | null;
  setCurrentProject: (p: Project | null) => void;
}

const PROJECT_KEY = 'currentProject';

function readPersistedProject(): Project | null {
  try {
    const raw = localStorage.getItem(PROJECT_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore corrupt data */ }
  return null;
}

function persistProject(p: Project | null) {
  try {
    if (p) {
      localStorage.setItem(PROJECT_KEY, JSON.stringify(p));
    } else {
      localStorage.removeItem(PROJECT_KEY);
    }
  } catch { /* quota exceeded or private browsing */ }
}

const ProjectContext = createContext<ProjectContextType>({
  currentProject: null,
  setCurrentProject: () => {},
});

export const useProject = () => useContext(ProjectContext);

export const ProjectProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [currentProject, setCurrentProject] = useState<Project | null>(readPersistedProject);

  useEffect(() => {
    persistProject(currentProject);
  }, [currentProject]);

  return React.createElement(
    ProjectContext.Provider,
    { value: { currentProject, setCurrentProject } },
    children
  );
};
