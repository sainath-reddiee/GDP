import { useState, useEffect } from 'react';
import { ArrowLeft, Copy, Download, FileCode, Check, ArrowRight, Edit, Save, X, Sparkles } from 'lucide-react';
import { DBTCode } from '../types';
import { useDownloadDBTZip, useUpdateDBTFile, useAIEditDBTFile } from '../hooks/useMappingsApi';
import Stepper from './Stepper';

interface DBTCodeViewerProps {
  projectName: string;
  projectId: number;
  codes: DBTCode[];
  onBackToProjects: () => void;
  onStepClick?: (step: number) => void;
  isLoading?: boolean;
  statusMessage?: string;
  onCodeUpdate?: (updatedCode: DBTCode) => void;
}

export default function DBTCodeViewer({
  projectName,
  projectId,
  codes,
  onBackToProjects,
  onStepClick,
  isLoading = false,
  statusMessage = '',
  onCodeUpdate,
}: DBTCodeViewerProps) {
  const [selectedFile, setSelectedFile] = useState<DBTCode | null>(codes.length > 0 ? codes[0] : null);
  const [copiedFile, setCopiedFile] = useState<string | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editedContent, setEditedContent] = useState<string>('');
  const [isAIEditModalOpen, setIsAIEditModalOpen] = useState(false);
  const [aiEditPrompt, setAIEditPrompt] = useState('');
  const [aiEditResponse, setAIEditResponse] = useState<string | null>(null);
  const { downloadDBTZip, isLoading: isDownloading } = useDownloadDBTZip();
  const { updateDBTFile, isLoading: isUpdating } = useUpdateDBTFile();
  const { aiEditDBTFile, isLoading: isAIEditing } = useAIEditDBTFile();

  // Update selected file when codes change
  useEffect(() => {
    if (codes.length > 0 && (!selectedFile || !codes.find(c => c.fileName === selectedFile.fileName))) {
      setSelectedFile(codes[0]);
    }
  }, [codes, selectedFile]);

  // Reset edit mode when selected file changes
  useEffect(() => {
    setIsEditing(false);
    setEditedContent('');
  }, [selectedFile]);

  const handleCopy = (code: DBTCode) => {
    navigator.clipboard.writeText(code.content);
    setCopiedFile(code.fileName);
    setTimeout(() => setCopiedFile(null), 2000);
  };

  const getFileExtension = (fileName: string): string => {
    const lastDot = fileName.lastIndexOf('.');
    if (lastDot === -1) return '';
    return fileName.substring(lastDot);
  };

  const handleDownload = (code: DBTCode) => {
    const blob = new Blob([code.content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = code.fileName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const onDownloadAll = async () => {
    try {
      const blob = await downloadDBTZip(projectId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${projectName}.zip`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Failed to download DBT zip:', err);
    }
  };

  const handleEdit = () => {
    if (selectedFile) {
      setEditedContent(selectedFile.content);
      setIsEditing(true);
    }
  };

  const handleCancel = () => {
    setIsEditing(false);
    setEditedContent('');
  };

  const handleSave = async () => {
    if (!selectedFile || !selectedFile.id) {
      console.error('Cannot save: file ID is missing');
      return;
    }

    try {
      await updateDBTFile(projectId, selectedFile.id, editedContent);
      
      // Update the selected file content
      const updatedFile = { ...selectedFile, content: editedContent };
      setSelectedFile(updatedFile);
      
      // Notify parent component if callback is provided
      if (onCodeUpdate) {
        onCodeUpdate(updatedFile);
      }
      
      setIsEditing(false);
      setEditedContent('');
    } catch (err) {
      console.error('Failed to save DBT file:', err);
      // Error is already handled by the hook, but we can show a user-friendly message
    }
  };

  const handleAIEdit = () => {
    if (selectedFile) {
      setAIEditPrompt('');
      setAIEditResponse(null);
      setIsAIEditModalOpen(true);
    }
  };

  const handleAIEditSubmit = async () => {
    if (!selectedFile || !selectedFile.id || !aiEditPrompt.trim()) {
      return;
    }

    try {
      const response = await aiEditDBTFile(projectId, selectedFile.id, aiEditPrompt);
      if (response.success && response.payload) {
        setAIEditResponse(response.payload.file_content);
      }
    } catch (err) {
      console.error('Failed to AI edit DBT file:', err);
    }
  };

  const handleAIEditAccept = async () => {
    if (!selectedFile || !selectedFile.id || !aiEditResponse) {
      return;
    }

    try {
      await updateDBTFile(projectId, selectedFile.id, aiEditResponse);
      
      // Update the selected file content
      const updatedFile = { ...selectedFile, content: aiEditResponse };
      setSelectedFile(updatedFile);
      
      // Notify parent component if callback is provided
      if (onCodeUpdate) {
        onCodeUpdate(updatedFile);
      }
      
      // Close modal and reset state
      setIsAIEditModalOpen(false);
      setAIEditPrompt('');
      setAIEditResponse(null);
    } catch (err) {
      console.error('Failed to accept AI edit:', err);
    }
  };

  const handleAIEditReject = () => {
    // Only clear the response, keep modal open and prompt field
    setAIEditResponse(null);
  };

  const handleAIEditClose = () => {
    // Close modal and reset state
    setIsAIEditModalOpen(false);
    setAIEditPrompt('');
    setAIEditResponse(null);
  };

  return (
    <div className="h-full flex flex-col overflow-hidden relative">
      {/* Loader Overlay */}
      {isLoading && (
        <div className="absolute inset-0 bg-white bg-opacity-90 z-50 flex items-center justify-center">
          <div className="text-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4"></div>
            <p className="text-gray-600 text-lg font-medium">{statusMessage || 'Loading DBT files...'}</p>
          </div>
        </div>
      )}

      <Stepper currentStep={3} onStepClick={onStepClick} completedSteps={[1, 2, 3]} />

      <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={onBackToProjects}
              className="flex items-center gap-2 px-3 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              Back to Projects
            </button>
            <div>
              <h2 className="text-2xl font-bold text-gray-900">{projectName}</h2>
              <p className="text-sm text-gray-600 mt-1">
                Generated DBT transformation models
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex gap-4">
              <div className="bg-blue-50 px-4 py-2 rounded-lg">
                <div className="text-sm text-gray-600">Total Files</div>
                <div className="text-2xl font-bold text-blue-600">{codes.length}</div>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={onDownloadAll}
                disabled={isDownloading || isLoading}
                className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50"
                aria-busy={isDownloading}
              >
                <Download className="w-4 h-4" />
                {isDownloading ? 'Downloading...' : 'Download All'}
              </button>
              <button
                onClick={onBackToProjects}
                className="flex items-center gap-2 px-6 py-3 bg-green-700 text-white rounded-lg hover:bg-green-800 font-medium shadow-sm transition-colors"
              >
                Complete
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-hidden flex min-h-0">
        {/* Left Sidebar - File List */}
        <div className="w-80 bg-white border-r border-gray-200 flex flex-col min-h-0">
          <div className="p-4 border-b border-gray-200 flex-shrink-0">
            <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">
              DBT Files ({codes.length})
            </h3>
          </div>

          <div className="flex-1 overflow-y-auto scrollbar-thin min-h-0">
            {codes.map((code, index) => (
              <div
                key={index}
                onClick={() => setSelectedFile(code)}
                className={`p-4 border-b border-gray-200 cursor-pointer transition-colors ${
                  selectedFile?.fileName === code.fileName
                    ? 'bg-blue-50 border-l-4 border-l-blue-600'
                    : 'hover:bg-gray-50'
                }`}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <FileCode className="w-4 h-4 text-gray-400 flex-shrink-0" />
                    <h3 className="font-medium text-gray-900 text-sm truncate">
                      {code.fileName}
                    </h3>
                  </div>
                  {selectedFile?.fileName === code.fileName && (
                    <ArrowRight className="w-4 h-4 text-blue-600 flex-shrink-0" />
                  )}
                </div>
                <p className="text-xs text-gray-600 mb-2">
                  Model: {code.modelName}
                </p>
                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-purple-100 text-purple-800">
                    {getFileExtension(code.fileName) || ''}
                  </span>
                  <span className="text-xs text-gray-500">
                    {code.content.split('\n').length} lines
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Right Content - Code Display */}
        <div className="flex-1 flex flex-col bg-gray-50 min-h-0">
          {selectedFile && codes.length > 0 ? (
            <>
              <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-lg font-bold text-gray-900 mb-1">
                      {selectedFile.filePath || selectedFile.fileName}
                    </h2>
                    <p className="text-sm text-gray-600">
                      DBT model for {selectedFile.modelName}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {isEditing ? (
                      <>
                        <button
                          onClick={handleSave}
                          disabled={isUpdating}
                          className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors disabled:opacity-50"
                        >
                          <Save className="w-4 h-4" />
                          <span>{isUpdating ? 'Saving...' : 'Save'}</span>
                        </button>
                        <button
                          onClick={handleCancel}
                          disabled={isUpdating}
                          className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50"
                        >
                          <X className="w-4 h-4" />
                          <span>Cancel</span>
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={handleAIEdit}
                          className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition-colors"
                        >
                          <Sparkles className="w-4 h-4" />
                          <span>AI Edit</span>
                        </button>
                        <button
                          onClick={handleEdit}
                          className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
                        >
                          <Edit className="w-4 h-4" />
                          <span>Edit Code</span>
                        </button>
                        <button
                          onClick={() => handleCopy(selectedFile)}
                          className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
                        >
                          {copiedFile === selectedFile.fileName ? (
                            <>
                              <Check className="w-4 h-4 text-green-600" />
                              <span>Copied!</span>
                            </>
                          ) : (
                            <>
                              <Copy className="w-4 h-4" />
                              <span>Copy Code</span>
                            </>
                          )}
                        </button>
                        <button
                          onClick={() => handleDownload(selectedFile)}
                          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                        >
                          <Download className="w-4 h-4" />
                          Download
                        </button>
                      </>
                    )}
                  </div>
                </div>
              </div>

              <div className="flex-1 overflow-hidden min-h-0 p-6">
                <div className="bg-gray-900 rounded-lg shadow-lg overflow-hidden flex flex-col h-full">
                  <div className="bg-gray-800 px-4 py-2 border-b border-gray-700 flex-shrink-0">
                    <div className="flex items-center gap-2">
                      <div className="flex gap-1.5">
                        <div className="w-3 h-3 rounded-full bg-red-500"></div>
                        <div className="w-3 h-3 rounded-full bg-yellow-500"></div>
                        <div className="w-3 h-3 rounded-full bg-green-500"></div>
                      </div>
                      <span className="text-xs text-gray-400 ml-2">{selectedFile.fileName}</span>
                      {isEditing && (
                        <span className="text-xs text-yellow-400 ml-auto">Editing mode</span>
                      )}
                    </div>
                  </div>
                  {isEditing ? (
                    <textarea
                      value={editedContent}
                      onChange={(e) => setEditedContent(e.target.value)}
                      className="flex-1 w-full p-6 text-sm text-gray-100 font-mono bg-gray-900 border-0 resize-none focus:outline-none focus:ring-0 overflow-y-auto scrollbar-thin"
                      style={{ fontFamily: 'monospace' }}
                    />
                  ) : (
                    <div className="flex-1 overflow-y-auto scrollbar-thin">
                      <pre className="p-6 text-sm text-gray-100 font-mono overflow-x-auto">
                        <code>{selectedFile.content}</code>
                      </pre>
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center text-gray-500">
                <FileCode className="w-16 h-16 mx-auto mb-4 text-gray-400" />
                <p className="text-lg">Select a file from the left to view code</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* AI Edit Modal */}
      {isAIEditModalOpen && selectedFile && (
        <div className="fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col">
            <div className="px-6 py-4 border-b border-gray-200 flex-shrink-0 flex items-center justify-between">
              <h3 className="text-lg font-bold text-gray-900">AI Edit - {selectedFile.fileName}</h3>
              <button
                onClick={handleAIEditClose}
                className="text-gray-400 hover:text-gray-600 transition-colors"
                aria-label="Close modal"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Enter your prompt for AI editing:
                </label>
                <textarea
                  value={aiEditPrompt}
                  onChange={(e) => setAIEditPrompt(e.target.value)}
                  placeholder="e.g., Add error handling, optimize the query, add comments..."
                  className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent resize-none"
                  rows={4}
                  disabled={isAIEditing}
                />
              </div>

              <div className="flex justify-end">
                <button
                  onClick={handleAIEditSubmit}
                  disabled={isAIEditing || !aiEditPrompt.trim()}
                  className="px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition-colors disabled:opacity-50"
                >
                  {isAIEditing ? 'Processing...' : 'Submit'}
                </button>
              </div>

              {aiEditResponse && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    AI Generated Content:
                  </label>
                  <div className="bg-gray-900 rounded-lg shadow-lg overflow-hidden">
                    <div className="bg-gray-800 px-4 py-2 border-b border-gray-700">
                      <div className="flex items-center gap-2">
                        <div className="flex gap-1.5">
                          <div className="w-3 h-3 rounded-full bg-red-500"></div>
                          <div className="w-3 h-3 rounded-full bg-yellow-500"></div>
                          <div className="w-3 h-3 rounded-full bg-green-500"></div>
                        </div>
                        <span className="text-xs text-gray-400 ml-2">{selectedFile.fileName}</span>
                      </div>
                    </div>
                    <div className="overflow-y-auto max-h-96">
                      <pre className="p-6 text-sm text-gray-100 font-mono overflow-x-auto">
                        <code>{aiEditResponse}</code>
                      </pre>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {aiEditResponse && (
              <div className="px-6 py-4 border-t border-gray-200 flex items-center justify-end gap-3 flex-shrink-0">
                <button
                  onClick={handleAIEditReject}
                  disabled={isUpdating}
                  className="px-4 py-2 border border-red-300 text-red-700 rounded-lg hover:bg-red-50 transition-colors disabled:opacity-50"
                >
                  Reject
                </button>
                <button
                  onClick={handleAIEditAccept}
                  disabled={isUpdating}
                  className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors disabled:opacity-50"
                >
                  {isUpdating ? 'Accepting...' : 'Accept'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
