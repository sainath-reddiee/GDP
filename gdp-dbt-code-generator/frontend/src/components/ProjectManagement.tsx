import { useState } from 'react';
import { Search, Upload, Trash2, Download, FolderOpen, Plus, FileText, Calendar, Edit2, X, Loader, CheckCircle } from 'lucide-react';
import { Project } from '../types';
import { validateCSVFile, parseCSV, convertCSVToTableMappings, generateSampleCSV } from '../utils/csvParser';
import { useUploadCsv, useDeleteProject } from '../hooks';
import Pagination from './Pagination';

interface ProjectManagementProps {
  projects: Project[];
  onCreateProject: (project: Project) => void;
  onOpenProject: (project: Project) => void;
  onDeleteProject: (projectId: string) => void;
  onUpdateProject: (project: Project) => void;
  onRefreshProjects: () => void;
}

export default function ProjectManagement({
  projects,
  onCreateProject,
  onOpenProject,
  onDeleteProject,
  onUpdateProject,
  onRefreshProjects,
}: ProjectManagementProps) {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingProject, setEditingProject] = useState<Project | null>(null);
  const [projectName, setProjectName] = useState('');
  const [description, setDescription] = useState('');
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvContent, setCsvContent] = useState<string>('');
  const [csvPreview, setCsvPreview] = useState<{ tables: number; columns: number; schemas: string[] } | null>(null);
  const [error, setError] = useState<string>('');
  const [searchTerm, setSearchTerm] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [deleteConfirm, setDeleteConfirm] = useState<{ projectId: string; projectName: string } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const itemsPerPage = 12;

  // API hook for uploading CSV
  const { uploadCsv, isLoading: isUploading, error: uploadError } = useUploadCsv();

  // API hook for deleting project
  const { deleteProject, isLoading: isDeletingProject, error: deleteError } = useDeleteProject();

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      setError('');
      console.log('File selected:', file.name, 'Size:', file.size);

      const content = await validateCSVFile(file);
      console.log('File validated, content length:', content.length);

      const csvRows = parseCSV(content);
      console.log('CSV parsed successfully, rows:', csvRows.length);

      const mappings = convertCSVToTableMappings(csvRows);
      console.log('Mappings created:', mappings.length);

      // Generate preview statistics
      const uniqueSchemas = new Set(csvRows.map(row => row.sourceSchema));
      setCsvPreview({
        tables: mappings.length,
        columns: csvRows.length,
        schemas: Array.from(uniqueSchemas),
      });

      setCsvFile(file);
      setCsvContent(content);
      console.log('CSV file and content saved to state');
    } catch (err) {
      console.error('Error uploading CSV file:', err);
      setError(err instanceof Error ? err.message : 'Invalid CSV file');
      setCsvFile(null);
      setCsvContent('');
      setCsvPreview(null);
    }
  };

  const handleDownloadSample = () => {
    const sample = generateSampleCSV();
    const blob = new Blob([sample], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'sample_mapping.csv';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const handleOpenForm = (project?: Project) => {
    if (project) {
      setEditingProject(project);
      setProjectName(project.projectName);
      setDescription(project.description);
      setCsvFile(null);
      setCsvContent('');
      setCsvPreview(null);
    } else {
      setEditingProject(null);
      setProjectName('');
      setDescription('');
      setCsvFile(null);
      setCsvContent('');
      setCsvPreview(null);
    }
    setError('');
    setShowCreateForm(true);
  };

  const handleCloseForm = () => {
    setShowCreateForm(false);
    setEditingProject(null);
    setProjectName('');
    setDescription('');
    setCsvFile(null);
    setCsvContent('');
    setCsvPreview(null);
    setError('');
  };

  const handleCreateOrUpdate = async () => {
    console.log('handleCreateOrUpdate called');
    console.log('Project name:', projectName);
    console.log('CSV File:', csvFile ? csvFile.name : 'null');
    console.log('CSV Content:', csvContent ? csvContent.length : 'null');

    if (!projectName.trim()) {
      setError('Project name is required');
      return;
    }

    if (!csvFile || !csvContent) {
      console.error('CSV validation failed:', { csvFile: !!csvFile, csvContent: !!csvContent });
      setError('Please upload a CSV mapping file');
      return;
    }

    setIsLoading(true);
    setError('');

    try {
      if (editingProject) {
        // Edit mode - keep existing localStorage logic for now
        console.log('Starting project update...');
        console.log('CSV Content length:', csvContent.length);

        const csvRows = parseCSV(csvContent);
        console.log('Parsed CSV rows:', csvRows.length);
        console.log('Sample row:', csvRows[0]);

        const mappingData = convertCSVToTableMappings(csvRows);
        console.log('Converted to mappings:', mappingData.length);
        console.log('Sample mapping:', mappingData[0]);

        const updated = {
          ...editingProject,
          projectName: projectName.trim(),
          description: description.trim(),
          csvFileName: csvFile.name,
          mappingData,
        };
        onUpdateProject(updated);
      } else {
        // Create mode - use API hook
        console.log('Starting project creation via API...');

        // Call the API to upload CSV and create project
        const result = await uploadCsv({
          project_name: projectName.trim(),
          description: description.trim(),
          csv_file: csvFile,
        });

        console.log('Project created successfully via API:', result.payload);

        // Parse CSV for mapping data display
        const csvRows = parseCSV(csvContent);
        const mappingData = convertCSVToTableMappings(csvRows);

        // Create project object for local state
        const projectObj: Project = {
          projectId: result.payload.project_id.toString(),
          projectName: result.payload.project_name,
          description: description.trim(),
          createdDate: new Date().toISOString().split('T')[0],
          status: 'Draft',
          csvFileName: csvFile.name,
          mappingData,
          generatedCode: null,
        };

        onCreateProject(projectObj);
      }

      handleCloseForm();
      onRefreshProjects();
    } catch (err) {
      console.error('ERROR creating/updating project:');
      console.error('Error object:', err);
      console.error('Error type:', typeof err);
      if (err instanceof Error) {
        console.error('Error message:', err.message);
        console.error('Error stack:', err.stack);
      }

      // Try to extract more details from error
      if (err && typeof err === 'object' && 'message' in err) {
        console.error('Error details:', JSON.stringify(err, null, 2));
      }

      // Use API error if available, otherwise fallback to generic error
      const errorMessage = uploadError || (err instanceof Error
        ? `Failed to create project: ${err.message}`
        : 'Failed to create project. Check console for details.');
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteClick = (projectId: string, projectName: string) => {
    setDeleteConfirm({ projectId, projectName });
  };

  const handleConfirmDelete = async () => {
    if (!deleteConfirm) return;

    try {
      setError('');
      // Call API to delete project
      const projectIdNum = parseInt(deleteConfirm.projectId, 10);
      if (isNaN(projectIdNum)) {
        throw new Error('Invalid project ID');
      }

      await deleteProject(projectIdNum);

      // Call parent handler for local state cleanup if needed
      onDeleteProject(deleteConfirm.projectId);

      // Close confirmation dialog
      setDeleteConfirm(null);

      // Refetch projects list
      onRefreshProjects();
    } catch (err) {
      // Use API error if available, otherwise fallback to generic error
      const errorMessage = deleteError || (err instanceof Error
        ? `Failed to delete project: ${err.message}`
        : 'Failed to delete project');
      setError(errorMessage);
    }
  };

  const handleCancelDelete = () => {
    setDeleteConfirm(null);
  };

  const filteredProjects = projects.filter(
    (project) =>
      project.projectName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      project.description.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const sortedProjects = [...filteredProjects].sort(
    (a, b) => new Date(b.createdDate).getTime() - new Date(a.createdDate).getTime()
  );

  const totalPages = Math.ceil(sortedProjects.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const paginatedProjects = sortedProjects.slice(startIndex, startIndex + itemsPerPage);

  const getStatusColor = (status: Project['status']) => {
    switch (status) {
      case 'Draft':
        return 'bg-gray-100 text-gray-800';
      case 'Code Generated':
        return 'bg-blue-100 text-blue-800';
      case 'Completed':
        return 'bg-green-100 text-green-800';
      default:
        return 'bg-gray-100 text-gray-800';
    }
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Projects</h2>
          <p className="text-sm text-gray-600 mt-1">
            {projects.length} project{projects.length !== 1 ? 's' : ''} in total
          </p>
        </div>
        <button
          onClick={() => handleOpenForm()}
          className="flex items-center gap-2 px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium shadow-sm transition-colors"
        >
          <Plus className="w-5 h-5" />
          Create Project
        </button>
      </div>

      <div className="flex-1 overflow-auto bg-gray-50 p-6">
        <div className="bg-white rounded-lg shadow-sm border border-gray-200">
          <div className="px-6 py-4 border-b border-gray-200">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
              <input
                type="text"
                placeholder="Search projects..."
                value={searchTerm}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                  setSearchTerm(e.target.value);
                  setCurrentPage(1);
                }}
                className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
          </div>

          <div className="p-6">
            {paginatedProjects.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {paginatedProjects.map((project) => (
                  <div
                    key={project.projectId}
                    className="border border-gray-200 rounded-lg p-4 hover:shadow-md transition-shadow flex flex-col"
                  >
                    <div className="flex-1">
                      <h4 className="font-semibold text-gray-900 mb-1">
                        {project.projectName}
                      </h4>
                      <p className="text-sm text-gray-600 line-clamp-2 mb-3">
                        {project.description || 'No description provided'}
                      </p>
                    </div>

                    <div className="flex items-center gap-2 mb-3">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${getStatusColor(
                          project.status
                        )}`}
                      >
                        {project.status}
                      </span>
                      {project.csvFileName && (
                        <span className="text-xs text-gray-500 flex items-center gap-1">
                          <FileText className="w-3 h-3" />
                          {project.csvFileName}
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-1 text-xs text-gray-500 mb-3">
                      <Calendar className="w-3 h-3" />
                      {new Date(project.createdDate).toLocaleDateString()}
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => onOpenProject(project)}
                        className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium transition-colors"
                      >
                        <FolderOpen className="w-4 h-4" />
                        Open
                      </button>
                      <button
                        onClick={() => handleOpenForm(project)}
                        className="px-3 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
                        title="Edit project"
                      >
                        <Edit2 className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleDeleteClick(project.projectId, project.projectName)}
                        className="px-3 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
                        title="Delete project"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-center py-12">
                <FolderOpen className="w-12 h-12 text-gray-400 mx-auto mb-3" />
                <p className="text-gray-500">
                  {searchTerm ? 'No projects found' : 'No projects yet. Create your first project!'}
                </p>
              </div>
            )}
          </div>

          {totalPages > 1 && (
            <Pagination
              currentPage={currentPage}
              totalPages={totalPages}
              onPageChange={setCurrentPage}
              totalItems={sortedProjects.length}
              itemsPerPage={itemsPerPage}
            />
          )}
        </div>
      </div>

      {showCreateForm && (
        <div className="fixed inset-0 bg-black bg-opacity-50 z-40" onClick={handleCloseForm} />
      )}

      {deleteConfirm && (
        <>
          <div className="fixed inset-0 bg-black bg-opacity-50 z-40" onClick={handleCancelDelete} />
          <div className="fixed inset-0 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="flex-shrink-0 w-12 h-12 rounded-full bg-red-100 flex items-center justify-center">
                  <Trash2 className="w-6 h-6 text-red-600" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-gray-900">Delete Project</h3>
                  <p className="text-sm text-gray-500">This action cannot be undone</p>
                </div>
              </div>

              <div className="mb-6">
                <p className="text-gray-700">
                  Are you sure you want to delete <span className="font-semibold">"{deleteConfirm.projectName}"</span>?
                </p>
                <p className="text-sm text-gray-600 mt-2">
                  All associated table mappings and column data will be permanently removed.
                </p>
              </div>

              {error && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                  {error}
                </div>
              )}
              <div className="flex gap-3">
                <button
                  onClick={handleCancelDelete}
                  disabled={isDeletingProject}
                  className="flex-1 px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 font-medium transition-colors disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  onClick={handleConfirmDelete}
                  disabled={isDeletingProject}
                  className="flex-1 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  {isDeletingProject ? (
                    <>
                      <Loader className="w-4 h-4 animate-spin" />
                      Deleting...
                    </>
                  ) : (
                    <>
                      <Trash2 className="w-4 h-4" />
                      Delete Project
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      <div
        className={`fixed top-0 right-0 h-full w-96 bg-white shadow-lg transform transition-transform duration-300 z-50 overflow-y-auto ${showCreateForm ? 'translate-x-0' : 'translate-x-full'
          }`}
      >
        <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-900">
            {editingProject ? 'Edit Project' : 'Create Project'}
          </h3>
          <button
            onClick={handleCloseForm}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-4">
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
              {error}
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Project Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={projectName}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setProjectName(e.target.value)}
              placeholder="e.g., Sales Pipeline Transformation"
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Description
            </label>
            <textarea
              value={description}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setDescription(e.target.value)}
              placeholder="Brief description of the transformation project..."
              rows={4}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-medium text-gray-700">
                CSV Mapping File {!editingProject && <span className="text-red-500">*</span>}
              </label>
              <button
                onClick={handleDownloadSample}
                className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700"
              >
                <Download className="w-3 h-3" />
                Sample CSV
              </button>
            </div>
            <div className="relative">
              <input
                type="file"
                accept=".csv"
                onChange={handleFileUpload}
                className="hidden"
                id="csv-upload"
                key={csvFile?.name || 'empty'}
              />
              <label
                htmlFor="csv-upload"
                className="flex items-center gap-2 px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 cursor-pointer transition-colors w-full"
              >
                <Upload className="w-5 h-5 text-gray-500" />
                <span className="text-sm text-gray-700">
                  {csvFile ? csvFile.name : 'Choose CSV file'}
                </span>
              </label>
              {csvFile && (
                <div className="mt-2 space-y-2">
                  <div className="text-xs text-gray-500">
                    Size: {(csvFile.size / 1024).toFixed(2)} KB
                  </div>
                  {csvPreview && (
                    <div className="bg-green-50 border border-green-200 rounded-md p-3 space-y-1.5">
                      <div className="flex items-center gap-1.5 text-xs font-semibold text-green-800">
                        <CheckCircle className="w-3.5 h-3.5" />
                        CSV Parsed Successfully
                      </div>
                      <div className="text-xs text-green-700 ml-5">
                        • {csvPreview.tables} table{csvPreview.tables !== 1 ? 's' : ''} found
                      </div>
                      <div className="text-xs text-green-700 ml-5">
                        • {csvPreview.columns} column mapping{csvPreview.columns !== 1 ? 's' : ''}
                      </div>
                      <div className="text-xs text-green-700 ml-5">
                        • Schema{csvPreview.schemas.length !== 1 ? 's' : ''}: {csvPreview.schemas.join(', ')}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          <div className="flex gap-3 pt-4">
            <button
              onClick={handleCloseForm}
              className="flex-1 px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 font-medium transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleCreateOrUpdate}
              disabled={isLoading || isUploading}
              className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {(isLoading || isUploading) ? (
                <>
                  <Loader className="w-4 h-4 animate-spin" />
                  {editingProject ? 'Updating...' : 'Creating...'}
                </>
              ) : editingProject ? (
                'Update'
              ) : (
                'Create'
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
