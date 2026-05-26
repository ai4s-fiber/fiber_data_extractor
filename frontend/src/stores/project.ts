/**
 * Simple project context for current selected project.
 */
import React, { createContext, useContext, useState, ReactNode } from 'react';

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

const ProjectContext = createContext<ProjectContextType>({
  currentProject: null,
  setCurrentProject: () => {},
});

export const useProject = () => useContext(ProjectContext);

export const ProjectProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [currentProject, setCurrentProject] = useState<Project | null>(null);
  return React.createElement(
    ProjectContext.Provider,
    { value: { currentProject, setCurrentProject } },
    children
  );
};
