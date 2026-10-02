import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import ProjectManagement from '../components/ProjectManagement';
import { useGetProjects, useGetProject, type ProjectItem } from '../hooks';
import { convertBackendProjectToFrontend } from '../utils/projectConverter';
import { Project } from '../types';

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loadingError, setLoadingError] = useState<string | null>(null);
  const navigate = useNavigate();
  const { getProjects, isLoading, error } = useGetProjects();
  const { getProject } = useGetProject();

  const loadProjects = async () => {
    try {
      setLoadingError(null);
      const response = await getProjects();
      const projectsData: Project[] = response.payload.map((item: ProjectItem) => {
        // Map API status values to frontend status values
        let status: 'Draft' | 'Code Generated' | 'Completed' = 'Draft';
        if (item.status === 'completed') {
          status = 'Completed';
        } else if (item.status === 'draft') {
          status = 'Draft';
        }

        return {
          projectId: item.id.toString(),
          projectName: item.project_name,
          description: item.description || '',
          createdDate: new Date().toISOString().split('T')[0], // API doesn't provide date, using current date
          status,
          csvFileName: undefined,
          mappingData: [],
          generatedCode: null,
        };
      });
      setProjects(projectsData);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load projects';
      setLoadingError(errorMessage);
      console.error('Failed to load projects:', err);
    }
  };

  useEffect(() => {
    loadProjects();
  }, []);

  const handleCreateProject = async (project: Project) => {
    setProjects((prev: Project[]) => [project, ...prev]);

    // Navigate to mapping page
    navigate(`/project/${project.projectId}/mapping`);
  };



  const handleOpenProject = async (project: Project) => {
    try {
      // For completed projects, redirect directly to code page (step 3)
      if (project.status === 'Completed') {
        navigate(`/project/${project.projectId}/code`);
        return;
      }

      const projectIdNum = parseInt(project.projectId, 10);
      if (isNaN(projectIdNum)) {
        throw new Error('Invalid project ID');
      }
      const response = await getProject(projectIdNum);
      if (response.success) {
        const projectWithMappings = convertBackendProjectToFrontend(response.payload);
        // Navigate to appropriate page based on status
        if (projectWithMappings.status === 'Code Generated' || projectWithMappings.status === 'Completed') {
          navigate(`/project/${project.projectId}/code`);
        } else {
          navigate(`/project/${project.projectId}/mapping`);
        }
      }
    } catch (err) {
      console.error('Failed to load project mappings:', err);
      // For draft projects, navigate to mapping page
      navigate(`/project/${project.projectId}/mapping`);
    }
  };

  const handleDeleteProject = (projectId: string) => {
    setProjects((prev: Project[]) => prev.filter((p: Project) => p.projectId !== projectId));
  };

  const handleUpdateProject = (project: Project) => {
    setProjects((prev: Project[]) =>
      prev.map((p: Project) => (p.projectId === project.projectId ? project : p))
    );
  };

  // Show loading state
  if (isLoading && projects.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4"></div>
          <p className="text-gray-600">Loading projects...</p>
        </div>
      </div>
    );
  }

  // Show error state
  if (error || loadingError) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center max-w-md">
          <div className="bg-red-50 border border-red-200 rounded-lg p-6">
            <h3 className="text-lg font-semibold text-red-800 mb-2">Error Loading Projects</h3>
            <p className="text-red-600 mb-4">{error || loadingError}</p>
            <button
              onClick={loadProjects}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <ProjectManagement
      projects={projects}
      onCreateProject={handleCreateProject}
      onOpenProject={handleOpenProject}
      onDeleteProject={handleDeleteProject}
      onUpdateProject={handleUpdateProject}
      onRefreshProjects={loadProjects}
    />
  );
}

