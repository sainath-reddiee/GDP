import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import BronzeSilverMappingView from '../components/BronzeSilverMapping';
import { useGetProject, useGenerateDBT } from '../hooks/useMappingsApi';
import { convertBackendProjectToFrontend } from '../utils/projectConverter';
import { Project } from '../types';

export default function MappingPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const { getProject, isLoading, error } = useGetProject();
  const { generateDBT } = useGenerateDBT();

  useEffect(() => {
    const loadProject = async () => {
      if (!projectId) {
        navigate('/');
        return;
      }

      try {
        const projectIdNum = parseInt(projectId, 10);
        if (isNaN(projectIdNum)) {
          throw new Error('Invalid project ID');
        }

        const response = await getProject(projectIdNum);
        if (response.success) {
          const projectData = convertBackendProjectToFrontend(response.payload);
          setProject(projectData);
        } else {
          console.error('Project not found');
          navigate('/');
        }
      } catch (err) {
        console.error('Failed to load project:', err);
        navigate('/');
      } finally {
        setLoading(false);
      }
    };

    loadProject();
  }, [projectId, navigate, getProject]);

  const handleRefresh = async () => {
    if (!projectId) return;
    try {
      const projectIdNum = parseInt(projectId, 10);
      const response = await getProject(projectIdNum);
      if (response.success) {
        const projectData = convertBackendProjectToFrontend(response.payload);
        setProject(projectData);
      }
    } catch (err) {
      console.error('Failed to refresh project:', err);
    }
  };

  const handleGenerateCode = async () => {
    if (!project || !projectId) return;

    try {
      const projectIdNum = parseInt(projectId, 10);
      if (isNaN(projectIdNum)) {
        throw new Error('Invalid project ID');
      }

      // Call the generate-dbt API
      const response = await generateDBT(projectIdNum);

      if (response.success) {
        // Navigate to code viewer page which will handle polling
        navigate(`/project/${projectId}/code`);
      } else {
        console.error('Failed to generate DBT code');
      }
    } catch (err) {
      console.error('Failed to generate code:', err);
    }
  };

  const handleBackToProjects = () => {
    navigate('/');
  };

  const handleStepClick = async (step: number) => {
    if (!projectId) return;

    if (step === 1) {
      navigate('/');
    } else if (step === 2) {
      // Already on mapping page
      return;
    } else if (step === 3) {
      // Check the mapping API status field
      try {
        const projectIdNum = parseInt(projectId, 10);
        if (isNaN(projectIdNum)) {
          return;
        }

        const projectResponse = await getProject(projectIdNum);

        if (projectResponse.success) {
          const apiStatus = projectResponse.payload.status;

          // If status is COMPLETED, allow navigation to step 3
          if (apiStatus && apiStatus.toUpperCase() === 'COMPLETED') {
            navigate(`/project/${projectId}/code`);
          }
        }
      } catch (err) {
        console.error('Failed to check project status:', err);
      }
    }
  };

  if (loading || isLoading) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4"></div>
          <p className="text-gray-600">Loading project...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center max-w-md">
          <div className="bg-red-50 border border-red-200 rounded-lg p-6">
            <h3 className="text-lg font-semibold text-red-800 mb-2">Error Loading Project</h3>
            <p className="text-red-600 mb-4">{error}</p>
            <button
              onClick={() => navigate('/')}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
            >
              Back to Projects
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!project) {
    return null;
  }

  return (
    <BronzeSilverMappingView
      projectId={parseInt(projectId || '0', 10)}
      projectName={project.projectName}
      mappings={project.mappingData}
      onGenerateCode={handleGenerateCode}
      onBackToProjects={handleBackToProjects}
      onStepClick={handleStepClick}
      onRefresh={handleRefresh}
      currentStatus={project.status}
    />
  );
}

