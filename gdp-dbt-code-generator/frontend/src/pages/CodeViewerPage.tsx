import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import DBTCodeViewer from '../components/DBTCodeViewer';
import { useGetProject, useGetDBTStatus, useGetDBTFiles, type DBTFile } from '../hooks/useMappingsApi';
import { convertBackendProjectToFrontend } from '../utils/projectConverter';
import { Project, DBTCode } from '../types';

export default function CodeViewerPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [generatedCodes, setGeneratedCodes] = useState<DBTCode[]>([]);
  const [loading, setLoading] = useState(true);
  const [isPolling, setIsPolling] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string>('Generating DBT code...');
  const [dbtGenerationFailed, setDbtGenerationFailed] = useState(false);
  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const hasCompletedRef = useRef<boolean>(false);
  const currentProjectIdRef = useRef<string | undefined>(projectId);
  const { getProject, isLoading, error } = useGetProject();
  const { getDBTStatus } = useGetDBTStatus();
  const { getDBTFiles } = useGetDBTFiles();

  useEffect(() => {
    const loadProject = async () => {
      if (!projectId) {
        navigate('/');
        return;
      }

      // Reset completion flag if project ID changed
      if (currentProjectIdRef.current !== projectId) {
        hasCompletedRef.current = false;
        currentProjectIdRef.current = projectId;
        // Clear any existing interval
        if (pollingIntervalRef.current) {
          clearInterval(pollingIntervalRef.current);
          pollingIntervalRef.current = null;
        }
      }

      // If we've already completed loading files for this project, don't restart polling
      if (hasCompletedRef.current) {
        return;
      }

      try {
        const projectIdNum = parseInt(projectId, 10);
        if (isNaN(projectIdNum)) {
          throw new Error('Invalid project ID');
        }

        // Load project info
        const projectResponse = await getProject(projectIdNum);
        if (projectResponse.success) {
          const projectData = convertBackendProjectToFrontend(projectResponse.payload);
          setProject(projectData);
        } else {
          console.error('Project not found');
          navigate('/');
          return;
        }

        // Check status first before setting up polling
        const statusResponse = await getDBTStatus(projectIdNum);
        if (statusResponse.success) {
          const status = statusResponse.payload.status;
          
          if (status === 'COMPLETED') {
            // Status is already completed, load files and don't start polling
            hasCompletedRef.current = true;
            setIsPolling(false);
            setStatusMessage('Loading DBT files...');
            
            // Fetch files
            const filesResponse = await getDBTFiles(projectIdNum);
            if (filesResponse.success) {
              // Convert API response to DBTCode format
              const codes: DBTCode[] = filesResponse.payload.map((file: DBTFile) => {
                // Extract model name from file_path for SQL files
                let modelName = file.file_name.replace('.sql', '').replace('.yml', '');
                if (file.file_path.includes('/')) {
                  const parts = file.file_path.split('/');
                  modelName = parts[parts.length - 1].replace('.sql', '').replace('.yml', '');
                }
                
                return {
                  id: file.id,
                  fileName: file.file_name,
                  content: file.file_content,
                  modelName: modelName,
                  filePath: file.file_path,
                };
              });
              
              setGeneratedCodes(codes);
              setStatusMessage('');
            } else {
              throw new Error('Failed to fetch DBT files');
            }
            setLoading(false);
            return;
          } else if (status === 'FAILED') {
            // Status is already failed, don't start polling
            hasCompletedRef.current = true;
            setIsPolling(false);
            setDbtGenerationFailed(true);
            setStatusMessage('');
            setLoading(false);
            return;
          }
        }

        // Start polling for status only if not completed or failed
        setIsPolling(true);
        setStatusMessage('Checking DBT generation status...');
        
        const checkStatus = async () => {
          // Don't check if we've already completed
          if (hasCompletedRef.current) {
            if (pollingIntervalRef.current) {
              clearInterval(pollingIntervalRef.current);
              pollingIntervalRef.current = null;
            }
            return;
          }

          try {
            const statusResponse = await getDBTStatus(projectIdNum);
            if (statusResponse.success) {
              const status = statusResponse.payload.status;
              
              if (status === 'COMPLETED') {
                // Stop polling
                hasCompletedRef.current = true;
                if (pollingIntervalRef.current) {
                  clearInterval(pollingIntervalRef.current);
                  pollingIntervalRef.current = null;
                }
                setIsPolling(false);
                setStatusMessage('Loading DBT files...');
                
                // Fetch files
                const filesResponse = await getDBTFiles(projectIdNum);
                if (filesResponse.success) {
                  // Convert API response to DBTCode format
                  const codes: DBTCode[] = filesResponse.payload.map((file: DBTFile) => {
                    // Extract model name from file_path for SQL files
                    let modelName = file.file_name.replace('.sql', '').replace('.yml', '');
                    if (file.file_path.includes('/')) {
                      const parts = file.file_path.split('/');
                      modelName = parts[parts.length - 1].replace('.sql', '').replace('.yml', '');
                    }
                    
                    return {
                      id: file.id,
                      fileName: file.file_name,
                      content: file.file_content,
                      modelName: modelName,
                      filePath: file.file_path,
                    };
                  });
                  
                  setGeneratedCodes(codes);
                  setStatusMessage('');
                } else {
                  throw new Error('Failed to fetch DBT files');
                }
              } else if (status === 'FAILED') {
                // Stop polling
                hasCompletedRef.current = true;
                if (pollingIntervalRef.current) {
                  clearInterval(pollingIntervalRef.current);
                  pollingIntervalRef.current = null;
                }
                setIsPolling(false);
                setDbtGenerationFailed(true);
                setStatusMessage('');
              } else {
                // Status is not COMPLETED or FAILED, continue polling
                setStatusMessage(`Generating DBT code...`);
              }
            }
          } catch (err) {
            console.error('Error checking status:', err);
            setStatusMessage('Error checking status. Retrying...');
          }
        };

        // Check status immediately
        await checkStatus();

        // Set up polling interval (15 seconds) only if not completed
        if (!hasCompletedRef.current) {
          pollingIntervalRef.current = setInterval(checkStatus, 15000);
        }

      } catch (err) {
        console.error('Failed to load project:', err);
        navigate('/');
      } finally {
        setLoading(false);
      }
    };

    loadProject();

    // Cleanup interval on unmount
    return () => {
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current);
        pollingIntervalRef.current = null;
      }
    };
  }, [projectId, navigate, getProject, getDBTStatus, getDBTFiles]);

  const handleBackToProjects = () => {
    navigate('/');
  };

  const handleStepClick = (step: number) => {
    if (!projectId) return;
    
    if (step === 1) {
      navigate('/');
    } else if (step === 2) {
      navigate(`/project/${projectId}/mapping`);
    } else if (step === 3) {
      // Already on code page
      return;
    }
  };

  const handleCodeUpdate = (updatedCode: DBTCode) => {
    setGeneratedCodes(prevCodes => 
      prevCodes.map(code => 
        code.id === updatedCode.id ? updatedCode : code
      )
    );
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

  // Show error screen if DBT generation failed
  if (dbtGenerationFailed) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center max-w-md">
          <div className="bg-red-50 border border-red-200 rounded-lg p-6">
            <div className="flex justify-center mb-4">
              <svg className="w-16 h-16 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-red-800 mb-2">DBT Code Generation Failed</h3>
            <p className="text-red-600 mb-4">
              An error occurred while generating the DBT code. Please try again or check the project configuration.
            </p>
            <div className="flex gap-3 justify-center">
              <button
                onClick={() => navigate(`/project/${projectId}/mapping`)}
                className="px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 transition-colors"
              >
                Back to Mapping
              </button>
              <button
                onClick={() => navigate('/')}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
              >
                Back to Projects
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Show loader if polling or if codes are not loaded yet
  const isLoadingDBT = isPolling || (generatedCodes.length === 0 && statusMessage !== '');

  return (
    <DBTCodeViewer
      projectName={project.projectName}
      projectId={projectId ? parseInt(projectId, 10) : 0}
      codes={generatedCodes}
      onBackToProjects={handleBackToProjects}
      onStepClick={handleStepClick}
      isLoading={isLoadingDBT}
      statusMessage={statusMessage}
      onCodeUpdate={handleCodeUpdate}
    />
  );
}

