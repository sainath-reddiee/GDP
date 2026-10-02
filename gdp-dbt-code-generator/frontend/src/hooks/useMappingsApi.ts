import { useState, useCallback } from 'react';

// API Base URL - can be configured via environment variable
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export interface UploadCsvRequest {
  project_name: string;
  description: string;
  csv_file: File;
}

export interface UploadCsvResponse {
  success: boolean;
  payload: {
    message: string;
    project_id: number;
    project_name: string;
    rows_count: number;
  };
}

export interface ProjectItem {
  id: number;
  project_name: string;
  description: string | null;
  status: 'draft' | 'completed';
}

export interface GetProjectsResponse {
  success: boolean;
  payload: ProjectItem[];
}

export interface MappingRow {
  id: number;
  source_schema: string;
  target_schema: string;
  source_table_name: string;
  target_table_name: string;
  source_column: string;
  target_column: string;
  source_description: string;
  target_description: string;
  source_data_type: string;
  target_data_type: string;
  mapping_similarity: number | null;
  transformation_logic: string;
  cleaning_logic: string;
  merge_strategy: string;
  macros: string;
}

export interface GetProjectResponse {
  success: boolean;
  payload: {
    id: number;
    project_name: string;
    description: string | null;
    status?: 'draft' | 'completed';
    mapping_rows: Record<string, MappingRow[]>;
    rows_count: number;
    source_tables_count: number;
  };
}

export interface GenerateDBTResponse {
  success: boolean;
  payload: {
    message: string;
    project_id: number;
  };
}

export interface DBTStatusResponse {
  success: boolean;
  payload: {
    project_id: number;
    status: string;
    files_count: number;
  };
}

export interface DBTFile {
  id: number;
  file_name: string;
  file_content: string;
  file_path: string;
}

export interface GetDBTFilesResponse {
  success: boolean;
  payload: DBTFile[];
}

interface UseUploadCsvReturn {
  uploadCsv: (data: UploadCsvRequest) => Promise<UploadCsvResponse>;
  isLoading: boolean;
  error: string | null;
}

interface UseGetProjectsReturn {
  getProjects: () => Promise<GetProjectsResponse>;
  isLoading: boolean;
  error: string | null;
}

interface UseGetProjectReturn {
  getProject: (projectId: number) => Promise<GetProjectResponse>;
  isLoading: boolean;
  error: string | null;
}

interface UseGenerateDBTReturn {
  generateDBT: (projectId: number) => Promise<GenerateDBTResponse>;
  isLoading: boolean;
  error: string | null;
}

interface UseGetDBTStatusReturn {
  getDBTStatus: (projectId: number) => Promise<DBTStatusResponse>;
  isLoading: boolean;
  error: string | null;
}

interface UseGetDBTFilesReturn {
  getDBTFiles: (projectId: number) => Promise<GetDBTFilesResponse>;
  isLoading: boolean;
  error: string | null;
}

interface UseDownloadDBTZipReturn {
  downloadDBTZip: (projectId: number) => Promise<Blob>;
  isLoading: boolean;
  error: string | null;
}

export interface DeleteProjectResponse {
  success: boolean;
  payload: {
    message: string;
    project_id: number;
  };
}

interface UseDeleteProjectReturn {
  deleteProject: (projectId: number) => Promise<DeleteProjectResponse>;
  isLoading: boolean;
  error: string | null;
}

/**
 * Hook for uploading CSV file to the mappings API
 * 
 * @returns {UseUploadCsvReturn} Object containing uploadCsv function, loading state, and error state
 */
export function useUploadCsv(): UseUploadCsvReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const uploadCsv = useCallback(async (data: UploadCsvRequest): Promise<UploadCsvResponse> => {
    setIsLoading(true);
    setError(null);

    try {
      // Create FormData for multipart/form-data request
      const formData = new FormData();
      formData.append('project_name', data.project_name);
      formData.append('description', data.description);
      formData.append('csv_file', data.csv_file);

      // Make POST request to the API
      const response = await fetch(`${API_BASE_URL}/mappings/csv/upload`, {
        method: 'POST',
        body: formData,
        // Note: Don't set Content-Type header - browser will set it automatically with boundary for multipart/form-data
      });

      // Check if response is ok
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({
          detail: `HTTP error! status: ${response.status}`,
        }));
        throw new Error(errorData.detail || errorData.message || 'Failed to upload CSV');
      }

      // Parse and return response
      const result: UploadCsvResponse = await response.json();
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    uploadCsv,
    isLoading,
    error,
  };
}

/**
 * Hook for fetching all projects from the mappings API
 * 
 * @returns {UseGetProjectsReturn} Object containing getProjects function, loading state, and error state
 */
export function useGetProjects(): UseGetProjectsReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const getProjects = useCallback(async (): Promise<GetProjectsResponse> => {
    setIsLoading(true);
    setError(null);

    try {
      // Make GET request to the API
      const response = await fetch(`${API_BASE_URL}/mappings/csv/projects`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      // Check if response is ok
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({
          detail: `HTTP error! status: ${response.status}`,
        }));
        throw new Error(errorData.detail || errorData.message || 'Failed to fetch projects');
      }

      // Parse and return response
      const result: GetProjectsResponse = await response.json();
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    getProjects,
    isLoading,
    error,
  };
}

/**
 * Hook for fetching a single project by ID from the mappings API
 * 
 * @returns {UseGetProjectReturn} Object containing getProject function, loading state, and error state
 */
export function useGetProject(): UseGetProjectReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const getProject = useCallback(async (projectId: number): Promise<GetProjectResponse> => {
    setIsLoading(true);
    setError(null);

    try {
      // Make GET request to the API
      const response = await fetch(`${API_BASE_URL}/mappings/csv/projects/${projectId}`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      // Check if response is ok
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({
          detail: `HTTP error! status: ${response.status}`,
        }));
        throw new Error(errorData.detail || errorData.message || 'Failed to fetch project');
      }

      // Parse and return response
      const result: GetProjectResponse = await response.json();
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    getProject,
    isLoading,
    error,
  };
}

/**
 * Hook for generating DBT code for a project
 * 
 * @returns {UseGenerateDBTReturn} Object containing generateDBT function, loading state, and error state
 */
export function useGenerateDBT(): UseGenerateDBTReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generateDBT = useCallback(async (projectId: number): Promise<GenerateDBTResponse> => {
    setIsLoading(true);
    setError(null);

    try {
      // Make POST request to the API
      const response = await fetch(`${API_BASE_URL}/dbt-offline/csv/projects/${projectId}/generate-dbt`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      // Check if response is ok
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({
          detail: `HTTP error! status: ${response.status}`,
        }));
        throw new Error(errorData.detail || errorData.message || 'Failed to generate DBT code');
      }

      // Parse and return response
      const result: GenerateDBTResponse = await response.json();
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    generateDBT,
    isLoading,
    error,
  };
}

/**
 * Hook for fetching DBT generation status for a project
 * 
 * @returns {UseGetDBTStatusReturn} Object containing getDBTStatus function, loading state, and error state
 */
export function useGetDBTStatus(): UseGetDBTStatusReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const getDBTStatus = useCallback(async (projectId: number): Promise<DBTStatusResponse> => {
    setIsLoading(true);
    setError(null);

    try {
      // Make GET request to the API
      const response = await fetch(`${API_BASE_URL}/dbt-offline/csv/projects/${projectId}/dbt/status`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      // Check if response is ok
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({
          detail: `HTTP error! status: ${response.status}`,
        }));
        throw new Error(errorData.detail || errorData.message || 'Failed to fetch DBT status');
      }

      // Parse and return response
      const result: DBTStatusResponse = await response.json();
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    getDBTStatus,
    isLoading,
    error,
  };
}

/**
 * Hook for fetching DBT files for a project
 * 
 * @returns {UseGetDBTFilesReturn} Object containing getDBTFiles function, loading state, and error state
 */
export function useGetDBTFiles(): UseGetDBTFilesReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const getDBTFiles = useCallback(async (projectId: number): Promise<GetDBTFilesResponse> => {
    setIsLoading(true);
    setError(null);

    try {
      // Make GET request to the API
      const response = await fetch(`${API_BASE_URL}/dbt-offline/csv/projects/${projectId}/dbt/files`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      // Check if response is ok
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({
          detail: `HTTP error! status: ${response.status}`,
        }));
        throw new Error(errorData.detail || errorData.message || 'Failed to fetch DBT files');
      }

      // Parse and return response
      const result: GetDBTFilesResponse = await response.json();
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    getDBTFiles,
    isLoading,
    error,
  };
}

/**
 * Hook for downloading DBT files zip for a project
 *
 * Returns a Blob that can be saved as a .zip file
 */
export function useDownloadDBTZip(): UseDownloadDBTZipReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const downloadDBTZip = useCallback(async (projectId: number): Promise<Blob> => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/dbt-offline/csv/projects/${projectId}/dbt/download`, {
        method: 'GET',
      });

      if (!response.ok) {
        let message = `HTTP error! status: ${response.status}`;
        try {
          const errorData = await response.json();
          message = errorData.detail || errorData.message || message;
        } catch (_) {
          // ignore json parse error
        }
        throw new Error(message);
      }

      const blob = await response.blob();
      return blob;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    downloadDBTZip,
    isLoading,
    error,
  };
}

/**
 * Hook for deleting a project
 * 
 * @returns {UseDeleteProjectReturn} Object containing deleteProject function, loading state, and error state
 */
export function useDeleteProject(): UseDeleteProjectReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const deleteProject = useCallback(async (projectId: number): Promise<DeleteProjectResponse> => {
    setIsLoading(true);
    setError(null);

    try {
      // Make DELETE request to the API
      const response = await fetch(`${API_BASE_URL}/mappings/csv/projects/${projectId}`, {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      // Check if response is ok
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({
          detail: `HTTP error! status: ${response.status}`,
        }));
        throw new Error(errorData.detail || errorData.message || 'Failed to delete project');
      }

      // Parse and return response
      const result: DeleteProjectResponse = await response.json();
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    deleteProject,
    isLoading,
    error,
  };
}

export interface TransformMappingResponse {
  success: boolean;
  payload: {
    mapping_row_id: number;
    target_table: string;
    sql: string;
  };
}

export interface UpdateTransformationRuleResponse {
  success: boolean;
  payload: {
    mapping_row_id: number;
    transformation_logic: string;
  };
}

interface UseTransformMappingReturn {
  transformMapping: (projectId: number, mappingRowId: number, prompt: string) => Promise<TransformMappingResponse>;
  isLoading: boolean;
  error: string | null;
}

interface UseUpdateTransformationRuleReturn {
  updateTransformationLogic: (projectId: number, mappingRowId: number, transformationLogic: string) => Promise<UpdateTransformationRuleResponse>;
  isLoading: boolean;
  error: string | null;
}

/**
 * Hook for transforming a mapping row using AI
 * 
 * @returns {UseTransformMappingReturn} Object containing transformMapping function, loading state, and error state
 */
export function useTransformMapping(): UseTransformMappingReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const transformMapping = useCallback(async (projectId: number, mappingRowId: number, prompt: string): Promise<TransformMappingResponse> => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/mappings/csv/projects/${projectId}/dbt/transform/${mappingRowId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ prompt }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({
          detail: `HTTP error! status: ${response.status}`,
        }));
        throw new Error(errorData.detail || errorData.message || 'Failed to transform mapping');
      }

      const result: TransformMappingResponse = await response.json();
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    transformMapping,
    isLoading,
    error,
  };
}

/**
 * Hook for updating transformation logic for a mapping row
 * 
 * @returns {UseUpdateTransformationRuleReturn} Object containing updateTransformationLogic function, loading state, and error state
 */
export function useUpdateTransformationLogic(): UseUpdateTransformationRuleReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateTransformationLogic = useCallback(async (projectId: number, mappingRowId: number, transformationLogic: string): Promise<UpdateTransformationRuleResponse> => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/mappings/csv/projects/${projectId}/mapping_rows/${mappingRowId}/transformation`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ transformation_logic: transformationLogic }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({
          detail: `HTTP error! status: ${response.status}`,
        }));
        throw new Error(errorData.detail || errorData.message || 'Failed to update transformation rule');
      }

      const result: UpdateTransformationRuleResponse = await response.json();
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    updateTransformationLogic,
    isLoading,
    error,
  };
}

export interface UpdateDBTFileRequest {
  file_content: string;
}

export interface UpdateDBTFileResponse {
  success: boolean;
  payload: DBTFile;
}

interface UseUpdateDBTFileReturn {
  updateDBTFile: (projectId: number, fileId: number, fileContent: string) => Promise<UpdateDBTFileResponse>;
  isLoading: boolean;
  error: string | null;
}

/**
 * Hook for updating DBT file content
 * 
 * @returns {UseUpdateDBTFileReturn} Object containing updateDBTFile function, loading state, and error state
 */
export function useUpdateDBTFile(): UseUpdateDBTFileReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateDBTFile = useCallback(async (projectId: number, fileId: number, fileContent: string): Promise<UpdateDBTFileResponse> => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/dbt-offline/csv/projects/${projectId}/dbt/files/${fileId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ file_content: fileContent }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({
          detail: `HTTP error! status: ${response.status}`,
        }));
        throw new Error(errorData.detail || errorData.message || 'Failed to update DBT file');
      }

      const result: UpdateDBTFileResponse = await response.json();
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    updateDBTFile,
    isLoading,
    error,
  };
}

export interface AIEditDBTFileRequest {
  prompt: string;
  max_tokens: number;
}

export interface AIEditDBTFileResponse {
  success: boolean;
  payload: DBTFile;
}

interface UseAIEditDBTFileReturn {
  aiEditDBTFile: (projectId: number, fileId: number, prompt: string) => Promise<AIEditDBTFileResponse>;
  isLoading: boolean;
  error: string | null;
}

/**
 * Hook for AI editing DBT file content
 * 
 * @returns {UseAIEditDBTFileReturn} Object containing aiEditDBTFile function, loading state, and error state
 */
export function useAIEditDBTFile(): UseAIEditDBTFileReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const aiEditDBTFile = useCallback(async (projectId: number, fileId: number, prompt: string): Promise<AIEditDBTFileResponse> => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/dbt-offline/csv/projects/${projectId}/dbt/files/${fileId}/ai-edit`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ prompt, max_tokens: 1024 }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({
          detail: `HTTP error! status: ${response.status}`,
        }));
        throw new Error(errorData.detail || errorData.message || 'Failed to AI edit DBT file');
      }

      const result: AIEditDBTFileResponse = await response.json();
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'An unknown error occurred';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    aiEditDBTFile,
    isLoading,
    error,
  };
}

export interface Macro {
  id: number;
  name: string;
  description?: string;
  sql_content: string;
}

export interface MacroCreateRequest {
  name: string;
  description?: string;
  sql_content: string;
}

export interface MacroResponse {
  success: boolean;
  payload: Macro;
}

export interface MacrosListResponse {
  success: boolean;
  payload: Macro[];
}

export interface AIGenerateMacroResponse {
  success: boolean;
  payload: {
    name: string;
    description?: string;
    sql: string;
  };
}

interface UseGetMacrosReturn {
  getMacros: () => Promise<MacrosListResponse>;
  isLoading: boolean;
  error: string | null;
}

interface UseCreateMacroReturn {
  createMacro: (data: MacroCreateRequest) => Promise<MacroResponse>;
  isLoading: boolean;
  error: string | null;
}

interface UseAIGenerateMacroReturn {
  aiGenerateMacro: (prompt: string) => Promise<AIGenerateMacroResponse>;
  isLoading: boolean;
  error: string | null;
}

interface UseDeleteMacroReturn {
  deleteMacro: (macroId: number) => Promise<{ success: boolean }>;
  isLoading: boolean;
  error: string | null;
}

/**
 * Hook for fetching all macros
 */
export function useGetMacros(): UseGetMacrosReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const getMacros = useCallback(async (): Promise<MacrosListResponse> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/mappings/macros`);
      if (!response.ok) throw new Error('Failed to fetch macros');
      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { getMacros, isLoading, error };
}

/**
 * Hook for creating or updating a macro
 */
export function useCreateMacro(): UseCreateMacroReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const createMacro = useCallback(async (data: MacroCreateRequest): Promise<MacroResponse> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/mappings/macros`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!response.ok) throw new Error('Failed to save macro');
      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { createMacro, isLoading, error };
}

/**
 * Hook for AI generating a macro code
 */
export function useAIGenerateMacro(): UseAIGenerateMacroReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const aiGenerateMacro = useCallback(async (prompt: string): Promise<AIGenerateMacroResponse> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/mappings/macros/ai-generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      });
      if (!response.ok) throw new Error('Failed to generate macro');
      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { aiGenerateMacro, isLoading, error };
}

export interface UseAIGenerateTransformationReturn {
  aiGenerateTransformation: (prompt: string) => Promise<{ success: boolean; payload: { sql: string } }>;
  isLoading: boolean;
  error: string | null;
}

export function useAIGenerateTransformation(): UseAIGenerateTransformationReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const aiGenerateTransformation = useCallback(async (prompt: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/mappings/transformations/ai-generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      });
      if (!response.ok) throw new Error('Failed to generate transformation');
      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { aiGenerateTransformation, isLoading, error };
}

/**
 * Hook for deleting a macro
 */
export function useDeleteMacro(): UseDeleteMacroReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const deleteMacro = useCallback(async (macroId: number): Promise<{ success: boolean }> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/mappings/macros/${macroId}`, {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error('Failed to delete macro');
      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { deleteMacro, isLoading, error };
}

export interface SuggestionResult {
  id: number;
  cleaning_logic: string;
  macros: string;
  transformation_logic: string;
  confidence: number;
  reasoning: string;
}

export interface BulkSuggestionResponse {
  success: boolean;
  payload: SuggestionResult[];
  message?: string;
}

export interface RowUpdate {
  id: number;
  cleaning_logic?: string;
  macros?: string;
  transformation_logic?: string;
}

export interface BulkUpdateResponse {
  success: boolean;
  payload: {
    updated_count: number;
  };
}

/**
 * Hook for generating bulk AI suggestions
 */
export function useBulkSuggestTransformations() {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const bulkSuggest = useCallback(async (projectId: number, mappingRowIds: number[]): Promise<BulkSuggestionResponse> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/mappings/csv/projects/${projectId}/suggest-bulk`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mapping_row_ids: mappingRowIds }),
      });
      if (!response.ok) throw new Error('Failed to fetch AI suggestions');
      return await response.json();
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { bulkSuggest, isLoading, error };
}

/**
 * Hook for bulk updating mapping rows
 */
export function useBulkUpdateMappings() {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const bulkUpdate = useCallback(async (projectId: number, updates: RowUpdate[]): Promise<BulkUpdateResponse> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/mappings/csv/projects/${projectId}/bulk-update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates }),
      });
      if (!response.ok) throw new Error('Bulk update failed');
      return await response.json();
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { bulkUpdate, isLoading, error };
}
