import { useState, useEffect } from 'react';
import { Search, ArrowLeft, ArrowRight, X, Bot, ChevronDown } from 'lucide-react';
import { TableMapping, ColumnMapping } from '../types';
import Stepper from './Stepper';
import { useUpdateTransformationLogic } from '../hooks/useMappingsApi';
import MacroManager from './MacroManager';

interface BronzeSilverMappingProps {
  projectId: number;
  projectName: string;
  mappings: TableMapping[];
  onGenerateCode: () => void;
  onBackToProjects: () => void;
  onStepClick?: (step: number) => void;
  onRefresh?: () => void;
  currentStatus?: string;
}

import { useGetMacros, useAIGenerateTransformation, useBulkSuggestTransformations, useBulkUpdateMappings } from '../hooks/useMappingsApi';
import { Sparkles, Code, ChevronRight, Share2, List } from 'lucide-react';
import LineageGraph from './LineageGraph';

interface TransformationModalProps {
  isOpen: boolean;
  onClose: () => void;
  // REMOVED: onSubmit (replaced by internal AI hook)
  onAccept: (sql: string) => Promise<void>;
  isLoading?: boolean; // Kept for compatibility but unused
  isAccepting?: boolean;
  column: ColumnMapping | null;
  tableMapping: TableMapping | null;
}

function TransformationModal({ isOpen, onClose, onAccept, isAccepting = false, column }: TransformationModalProps) {
  const [sqlContent, setSqlContent] = useState('');
  const [aiPrompt, setAiPrompt] = useState('');
  const [activeTab, setActiveTab] = useState<'editor' | 'ai' | 'macros'>('editor');

  // Hooks
  const { getMacros } = useGetMacros();
  const { aiGenerateTransformation, isLoading: isAiLoading } = useAIGenerateTransformation();
  const [macros, setMacros] = useState<{ name: string, description?: string }[]>([]);

  // Initialize
  useEffect(() => {
    if (isOpen && column) {
      setSqlContent(column.transformationLogic || '');
      // Load macros if not loaded
      getMacros().then(res => {
        if (res.success) setMacros(res.payload);
      });
    }
  }, [isOpen, column, getMacros]);

  const handleAiWebSubmit = async () => {
    if (!aiPrompt.trim()) return;
    const finalPrompt = column ? `For column "${column.source}": ${aiPrompt}` : aiPrompt;
    try {
      const result = await aiGenerateTransformation(finalPrompt);
      if (result.success && result.payload.sql) {
        setSqlContent(result.payload.sql);
        setActiveTab('editor'); // Switch back to editor to show result
      }
    } catch (e) {
      console.error(e);
    }
  };

  const insertMacro = (macroName: string) => {
    const colName = column?.source || 'column';
    const macroCall = `{{ ${macroName}(${colName}) }}`;

    // Replace the editor content with the macro call
    setSqlContent(macroCall);
    setActiveTab('editor');
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center z-50 backdrop-blur-sm">
      <div className="bg-white rounded-xl shadow-2xl max-w-4xl w-full mx-4 overflow-hidden flex flex-col max-h-[85vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 bg-gray-50/50">
          <div>
            <h3 className="text-lg font-bold text-gray-900">Transformation Editor</h3>
            <p className="text-sm text-gray-500">Define logic for <span className="font-mono text-blue-600 bg-blue-50 px-1 rounded">{column?.source}</span> → <span className="font-mono text-green-600 bg-green-50 px-1 rounded">{column?.target}</span></p>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-200 rounded-full text-gray-400 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex flex-1 overflow-hidden">
          {/* Sidebar Tabs */}
          <div className="w-48 bg-gray-50 border-r border-gray-200 flex flex-col p-2 gap-1">
            <button
              onClick={() => setActiveTab('editor')}
              className={`flex items-center gap-2 px-3 py-2.5 text-sm font-medium rounded-lg transition-colors text-left ${activeTab === 'editor' ? 'bg-white text-blue-600 shadow-sm border border-gray-200' : 'text-gray-600 hover:bg-gray-100'}`}
            >
              <Code className="w-4 h-4" /> SQL Editor
            </button>
            <button
              onClick={() => setActiveTab('ai')}
              className={`flex items-center gap-2 px-3 py-2.5 text-sm font-medium rounded-lg transition-colors text-left ${activeTab === 'ai' ? 'bg-white text-purple-600 shadow-sm border border-gray-200' : 'text-gray-600 hover:bg-gray-100'}`}
            >
              <Sparkles className="w-4 h-4" /> AI Assistant
            </button>
            <button
              onClick={() => setActiveTab('macros')}
              className={`flex items-center gap-2 px-3 py-2.5 text-sm font-medium rounded-lg transition-colors text-left ${activeTab === 'macros' ? 'bg-white text-orange-600 shadow-sm border border-gray-200' : 'text-gray-600 hover:bg-gray-100'}`}
            >
              <Bot className="w-4 h-4" /> Pick Macro
            </button>
          </div>

          {/* Content Area */}
          <div className="flex-1 p-6 overflow-y-auto">

            {/* Editor Tab */}
            {activeTab === 'editor' && (
              <div className="h-full flex flex-col">
                <label className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Snowflake SQL Expression</label>
                <textarea
                  value={sqlContent}
                  onChange={(e) => setSqlContent(e.target.value)}
                  className="flex-1 w-full p-4 font-mono text-sm bg-gray-50 border border-gray-200 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent leading-relaxed text-gray-800"
                  placeholder={`e.g. UPPER(${column?.source || 'column'}) or CAST(${column?.source || 'column'} AS INTEGER)`}
                  spellCheck={false}
                />
              </div>
            )}

            {/* AI Tab */}
            {activeTab === 'ai' && (
              <div className="h-full flex flex-col justify-center max-w-lg mx-auto">
                <div className="text-center mb-6">
                  <div className="w-12 h-12 bg-purple-100 text-purple-600 rounded-full flex items-center justify-center mx-auto mb-3">
                    <Sparkles className="w-6 h-6" />
                  </div>
                  <h4 className="text-lg font-bold text-gray-900">Generate with AI</h4>
                  <p className="text-sm text-gray-500">Describe what you want to do, and I'll write the SQL.</p>
                </div>

                <div className="relative">
                  <input
                    type="text"
                    value={aiPrompt}
                    onChange={(e) => setAiPrompt(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleAiWebSubmit()}
                    placeholder="e.g. Remove all whitespace and convert to lower..."
                    className="w-full pl-5 pr-14 py-4 text-sm border border-gray-300 rounded-2xl shadow-sm focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                    autoFocus
                  />
                  <button
                    onClick={handleAiWebSubmit}
                    disabled={isAiLoading || !aiPrompt}
                    className="absolute right-2 top-2 p-2 bg-purple-600 text-white rounded-xl hover:bg-purple-700 disabled:opacity-50 transition-all"
                  >
                    {isAiLoading ? <span className="animate-spin block">↻</span> : <ArrowRight className="w-4 h-4" />}
                  </button>
                </div>

                <div className="mt-8 grid grid-cols-2 gap-3">
                  {['Trim whitespace', 'Convert to uppercase', 'Mask email address', 'Format phone number'].map(suggestion => (
                    <button
                      key={suggestion}
                      onClick={() => { setAiPrompt(suggestion); }}
                      className="text-xs text-gray-600 bg-gray-50 hover:bg-purple-50 hover:text-purple-700 border border-gray-200 py-2.5 px-3 rounded-lg transition-colors text-left"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Macros Tab */}
            {activeTab === 'macros' && (
              <div className="h-full flex flex-col">
                <h4 className="text-sm font-bold text-gray-900 mb-4">Available Macros</h4>
                <div className="grid grid-cols-1 gap-3">
                  {macros.map(macro => (
                    <button
                      key={macro.name}
                      onClick={() => insertMacro(macro.name)}
                      className="group flex items-center justify-between p-4 bg-white border border-gray-200 rounded-xl hover:border-orange-300 hover:shadow-sm hover:bg-orange-50/30 transition-all text-left"
                    >
                      <div>
                        <div className="font-mono text-sm font-bold text-blue-700 mb-1">{`{{ ${macro.name}(...) }}`}</div>
                        <div className="text-xs text-gray-500">{macro.description || 'No description'}</div>
                      </div>
                      <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-orange-500" />
                    </button>
                  ))}
                  {macros.length === 0 && (
                    <div className="text-center py-10 text-gray-400">No macros found in library.</div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-100 bg-gray-50 flex justify-end gap-3">
          <button onClick={onClose} className="px-5 py-2.5 text-sm font-semibold text-gray-600 hover:bg-gray-200 rounded-lg transition-colors">
            Cancel
          </button>
          <button
            onClick={() => onAccept(sqlContent)}
            disabled={isAccepting || !sqlContent}
            className="px-6 py-2.5 text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-lg shadow-sm transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isAccepting ? 'Saving...' : 'Apply Rule'}
          </button>
        </div>

      </div>
    </div>
  );
}

export default function BronzeSilverMappingView({
  projectId,
  projectName,
  mappings,
  onGenerateCode,
  onBackToProjects,
  onStepClick,
  onRefresh,
  currentStatus,
}: BronzeSilverMappingProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedTable, setSelectedTable] = useState<TableMapping | null>(
    mappings.length > 0 ? mappings[0] : null
  );
  const [transformationModal, setTransformationModal] = useState<{
    isOpen: boolean;
    column: ColumnMapping | null;
  }>({ isOpen: false, column: null });
  const [isMacroLibraryOpen, setIsMacroLibraryOpen] = useState(false);
  const [selectedRowIds, setSelectedRowIds] = useState<number[]>([]);
  const [viewMode, setViewMode] = useState<'table' | 'lineage'>('table');
  const [bulkMenuOpen, setBulkMenuOpen] = useState(false);

  const { updateTransformationLogic, isLoading: isUpdating } = useUpdateTransformationLogic();
  const { bulkSuggest, isLoading: isSuggesting } = useBulkSuggestTransformations();
  const { bulkUpdate, isLoading: isBulkUpdating } = useBulkUpdateMappings();

  const handleSuggestBestPractices = async () => {
    if (!selectedTable) return;
    try {
      const columnIds = selectedTable.columns.map(c => c.id);
      const res = await bulkSuggest(projectId, columnIds);
      if (res.success && res.payload.length > 0) {
        const updates = res.payload.map(s => ({
          id: s.id,
          cleaning_logic: s.cleaning_logic,
          macros: s.macros,
          transformation_logic: s.transformation_logic
        }));
        await bulkUpdate(projectId, updates);
        onRefresh?.();
      }
    } catch (e) {
      console.error('Failed to suggest transformations:', e);
    }
  };

  const handleBulkApplyCleaning = async (logic: string) => {
    if (selectedRowIds.length === 0) return;
    try {
      const updates = selectedRowIds.map(id => ({
        id,
        cleaning_logic: logic
      }));
      await bulkUpdate(projectId, updates);
      setSelectedRowIds([]);
      onRefresh?.();
    } catch (e) {
      console.error('Bulk cleaning apply failed:', e);
    }
  };

  /* Sync selectedTable when mappings prop changes */
  useEffect(() => {
    if (selectedTable && mappings.length > 0) {
      const updatedMapping = mappings.find(m => m.id === selectedTable.id);
      if (updatedMapping) {
        // Only update if the data actually changed
        const hasChanges = JSON.stringify(updatedMapping) !== JSON.stringify(selectedTable);
        if (hasChanges) {
          setSelectedTable(updatedMapping);
        }
      } else if (mappings.length > 0) {
        // If current selected table is not found, select the first one
        setSelectedTable(mappings[0]);
      }
    } else if (mappings.length > 0 && !selectedTable) {
      setSelectedTable(mappings[0]);
    }
    // Reset selection when switching tables
    setSelectedRowIds([]);
  }, [mappings, selectedTable?.id]);

  const toggleRowSelection = (id: number) => {
    setSelectedRowIds(prev =>
      prev.includes(id) ? prev.filter(rowId => rowId !== id) : [...prev, id]
    );
  };

  const toggleSelectAll = () => {
    if (!selectedTable) return;
    const allIds = selectedTable.columns.map(c => c.id);
    if (selectedRowIds.length === allIds.length) {
      setSelectedRowIds([]);
    } else {
      setSelectedRowIds(allIds);
    }
  };
  const handleAcceptTransformation = async (sql: string) => {
    if (!transformationModal.column) return;

    try {
      // Update the transformation logic with the generated SQL
      await updateTransformationLogic(projectId, transformationModal.column.id, sql);

      // Update local state for immediate UI feedback
      if (selectedTable) {
        const updatedColumns = selectedTable.columns.map(col =>
          col.id === transformationModal.column!.id
            ? { ...col, transformationLogic: sql }
            : col
        );
        setSelectedTable({ ...selectedTable, columns: updatedColumns });
      }

      setTransformationModal({ isOpen: false, column: null });
    } catch (error) {
      console.error('Failed to update transformation rule:', error);
    }
  };

  const filteredMappings = mappings.filter(mapping =>
    mapping.tableName.toLowerCase().includes(searchTerm.toLowerCase()) ||
    mapping.sourceSchema.toLowerCase().includes(searchTerm.toLowerCase()) ||
    mapping.targetSchema.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const totalColumns = mappings.reduce((sum, m) => sum + m.columns.length, 0);
  const totalTables = mappings.length;
  const uniqueSchemas = new Set(mappings.map(m => m.sourceSchema)).size;


  // For completed projects, include step 3 in completed steps to allow navigation
  const completedSteps = currentStatus === 'Code Generated' || currentStatus === 'Completed' ? [1, 2, 3] : [1];

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <Stepper currentStep={2} onStepClick={onStepClick} completedSteps={completedSteps} />

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
                Review AI-generated mapping suggestions between source and target fields
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex gap-4">
              <div className="bg-blue-50 px-4 py-2 rounded-lg">
                <div className="text-sm text-gray-600">Tables</div>
                <div className="text-2xl font-bold text-blue-600">{totalTables}</div>
              </div>
              <div className="bg-green-50 px-4 py-2 rounded-lg">
                <div className="text-sm text-gray-600">Total Columns</div>
                <div className="text-2xl font-bold text-green-600">{totalColumns}</div>
              </div>
              <div className="bg-orange-50 px-4 py-2 rounded-lg">
                <div className="text-sm text-gray-600">Schemas</div>
                <div className="text-2xl font-bold text-orange-600">{uniqueSchemas}</div>
              </div>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => setIsMacroLibraryOpen(true)}
                className="flex items-center gap-2 px-4 py-2 border border-blue-600 text-blue-600 rounded-lg hover:bg-blue-50 font-medium transition-colors"
                title="Manage custom dbt macros"
              >
                <Bot className="w-5 h-5" />
                Macro Library
              </button>
              <button
                onClick={handleSuggestBestPractices}
                disabled={isSuggesting || isBulkUpdating || !selectedTable}
                className="flex items-center gap-2 px-4 py-2 border border-purple-600 text-purple-600 rounded-lg hover:bg-purple-50 font-medium transition-colors disabled:opacity-50"
                title="AI analysis of columns to suggest best practices"
              >
                <Sparkles className="w-5 h-5" />
                {isSuggesting ? 'Thinking...' : isBulkUpdating ? 'Applying...' : 'Suggest Best Practices'}
              </button>
              <button
                onClick={onGenerateCode}
                className="flex items-center gap-2 px-6 py-3 bg-green-700 text-white rounded-lg hover:bg-green-800 font-medium shadow-sm transition-colors"
              >
                Generate DBT Code
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-hidden flex min-h-0">
        {/* Left Sidebar - Table List */}
        <div className="w-80 bg-white border-r border-gray-200 flex flex-col min-h-0">
          <div className="p-4 border-b border-gray-200 flex-shrink-0">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder="Search for Table Name"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full pl-9 pr-4 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
          </div>

          <div className="flex-1 overflow-y-auto scrollbar-thin min-h-0">
            {filteredMappings.map((mapping) => (
              <div
                key={mapping.id}
                onClick={() => setSelectedTable(mapping)}
                className={`p-4 border-b border-gray-200 cursor-pointer transition-colors ${selectedTable?.id === mapping.id
                  ? 'bg-blue-50 border-l-4 border-l-blue-600'
                  : 'hover:bg-gray-50'
                  }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <h3 className="font-semibold text-gray-900 text-sm">{mapping.targetTable}</h3>
                  {selectedTable?.id === mapping.id && (
                    <ArrowRight className="w-4 h-4 text-blue-600" />
                  )}
                </div>
                <p className="text-xs text-gray-600 mb-2">
                  Target Schema: {mapping.targetSchema}
                </p>
                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">
                    {mapping.columns.length} columns
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Right Content - Column Mappings */}
        <div className="flex-1 flex flex-col bg-gray-50 min-h-0">
          {selectedTable ? (
            <>
              <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-4">
                    <div className="flex bg-gray-100 p-1 rounded-lg">
                      <button
                        onClick={() => setViewMode('table')}
                        className={`flex items-center gap-2 px-3 py-1.5 text-xs font-bold rounded-md transition-all ${viewMode === 'table' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                      >
                        <List className="w-3.5 h-3.5" /> Table View
                      </button>
                      <button
                        onClick={() => setViewMode('lineage')}
                        className={`flex items-center gap-2 px-3 py-1.5 text-xs font-bold rounded-md transition-all ${viewMode === 'lineage' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                      >
                        <Share2 className="w-3.5 h-3.5" /> Visual Lineage
                      </button>
                    </div>
                    <div className="text-sm text-gray-600 px-2 border-l border-gray-200">
                      Avg Confidence: <span className="font-semibold text-blue-600">
                        {Math.round(selectedTable.columns.reduce((sum, col) => sum + col.similarityScore, 0) / selectedTable.columns.length)}%
                      </span>
                    </div>
                  </div>
                </div>
                <p className="text-sm text-gray-600">
                  {viewMode === 'table'
                    ? 'Review AI-generated mapping suggestions between source and target fields'
                    : 'Visual representation of data movement from Raw Sources to Silver Business Layer'}
                </p>
              </div>

              <div className="flex-1 overflow-y-auto scrollbar-thin min-h-0">
                {viewMode === 'lineage' ? (
                  <LineageGraph mappings={mappings} />
                ) : (
                  <div className="p-6">
                    <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-x-auto scrollbar-thin">
                      <table className="w-full min-w-full">
                        <thead className="bg-gray-50 border-b border-gray-200">
                          <tr>
                            <th className="px-4 py-3 text-left w-10">
                              <input
                                type="checkbox"
                                className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                                checked={selectedTable.columns.length > 0 && selectedRowIds.length === selectedTable.columns.length}
                                onChange={toggleSelectAll}
                              />
                            </th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                              Target Column
                            </th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                              Source Schema
                            </th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                              Source Table
                            </th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                              Source Column
                            </th>
                            {/* <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                            Confidence
                          </th> */}
                            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                              Cleaning Logic
                            </th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                              Merge Strat
                            </th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                              Transformation
                            </th>
                            {/* <th className="w-20 px-4 py-3 text-center text-xs font-semibold text-gray-700 uppercase tracking-wider">
                            Issues
                          </th>
                          <th className="w-20 px-4 py-3 text-center text-xs font-semibold text-gray-700 uppercase tracking-wider">
                            Action
                          </th> */}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-200">
                          {selectedTable.columns.map((col, idx) => (
                            <tr
                              key={idx}
                              className="hover:bg-gray-50 transition-colors"
                            >
                              <td className="px-4 py-3">
                                <input
                                  type="checkbox"
                                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                                  checked={selectedRowIds.includes(col.id)}
                                  onChange={() => toggleRowSelection(col.id)}
                                />
                              </td>
                              <td className="px-4 py-3">
                                <div className="text-sm font-medium text-gray-900">{col.target}</div>
                                {col?.targetType && (
                                  <div className="text-xs text-gray-500 mt-1" title={col?.targetType}>
                                    {col?.targetType}
                                  </div>
                                )}

                              </td>
                              <td className="px-4 py-3">
                                <div className="text-sm font-medium text-gray-900">{col.sourceSchema}</div>
                              </td>
                              <td className="px-4 py-3">
                                <div className="text-sm font-medium text-gray-900">{col.sourceTable}</div>
                              </td>
                              <td className="px-4 py-3">
                                <div className="text-sm font-medium text-gray-900">{col.source}</div>
                                <div className="text-xs text-gray-500">{col.type}</div>
                              </td>
                              <td className="px-4 py-3">
                                <div className="text-sm text-gray-700 font-mono bg-gray-50 px-2 py-1 rounded truncate max-w-[150px]" title={col.cleaningLogic}>
                                  {col.cleaningLogic || 'None'}
                                </div>
                              </td>
                              <td className="px-4 py-3">
                                <span className="px-2 py-1 text-xs font-medium bg-purple-100 text-purple-800 rounded">
                                  {col.mergeStrategy || 'UNION'}
                                </span>
                              </td>
                              {/* <td className="px-4 py-3">
                              <div className="flex items-center gap-3">
                                <div className="flex-1 bg-gray-200 rounded-full h-2.5 max-w-[120px]">
                                  <div
                                    className={`h-2.5 rounded-full transition-all ${getConfidenceColor(col.similarityScore)}`}
                                    style={{ width: `${col.similarityScore}%` }}
                                  />
                                </div>
                                <span className={`text-sm font-semibold min-w-[42px] ${getConfidenceTextColor(col.similarityScore)}`}>
                                  {Math.round(col.similarityScore)}%
                                </span>
                              </div>
                            </td> */}
                              <td className="px-4 py-3">
                                <div className="flex items-center gap-2">
                                  <input
                                    type="text"
                                    readOnly
                                    value={col.transformationLogic || ''}
                                    placeholder="No transformation"
                                    className="flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded-lg bg-gray-50 text-gray-700 focus:outline-none"
                                  />
                                  <button
                                    onClick={() => setTransformationModal({ isOpen: true, column: col })}
                                    className="p-2.5 bg-gradient-to-br from-blue-600 to-blue-700 text-white rounded-lg 
                                    hover:from-blue-700 hover:to-blue-800 
                                    active:scale-95 
                                    transform transition-all duration-200 
                                    shadow-md hover:shadow-lg 
                                    hover:scale-110 
                                    focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2
                                    flex items-center justify-center
                                    group"
                                    title="Edit transformation"
                                  >
                                    <Bot className="w-5 h-5 group-hover:rotate-12 transition-transform duration-200" />
                                  </button>
                                </div>
                              </td>
                              {/* <td className="px-4 py-3 text-center">
                              {getIssueIcon(col.similarityScore)}
                            </td> */}
                              {/* <td className="px-4 py-3 text-center">
                              <button
                                className="text-blue-600 hover:text-blue-700 transition-colors"
                                title="Edit mapping"
                              >
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                                </svg>
                              </button>
                            </td> */}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center text-gray-500">
                <p className="text-lg">Select a table from the left to view column mappings</p>
              </div>
            </div>
          )}
        </div>
      </div>

      <TransformationModal
        isOpen={transformationModal.isOpen}
        onClose={() => setTransformationModal({ isOpen: false, column: null })}
        onAccept={handleAcceptTransformation}
        isAccepting={isUpdating}
        column={transformationModal.column}
        tableMapping={selectedTable}
      />

      {isMacroLibraryOpen && (
        <MacroManager onClose={() => setIsMacroLibraryOpen(false)} />
      )}

      {selectedRowIds.length > 0 && (
        <div className="fixed bottom-8 left-1/2 -translate-x-1/2 bg-gray-900 text-white px-6 py-4 rounded-2xl shadow-2xl flex items-center gap-6 z-50 animate-in fade-in slide-in-from-bottom-4 duration-300">
          <div className="flex items-center gap-2 border-r border-gray-700 pr-6">
            <div className="bg-blue-600 text-white text-xs font-bold w-6 h-6 rounded-full flex items-center justify-center font-sans">
              {selectedRowIds.length}
            </div>
            <span className="text-sm font-medium">Columns Selected</span>
          </div>

          <div className="flex items-center gap-3">
            <div className="relative">
              <button
                onClick={() => setBulkMenuOpen(!bulkMenuOpen)}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-xl text-sm font-bold transition-all shadow-lg"
              >
                Mass Transform
                <ChevronDown className={`w-4 h-4 transition-transform ${bulkMenuOpen ? 'rotate-180' : ''}`} />
              </button>

              {bulkMenuOpen && (
                <div className="absolute bottom-full mb-4 left-0 w-64 bg-white rounded-2xl shadow-2xl border border-gray-100 overflow-hidden z-50 animate-in fade-in slide-in-from-bottom-2">
                  <div className="p-3 border-b border-gray-50 bg-gray-50/50">
                    <span className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">Select Transformation</span>
                  </div>
                  <div className="max-h-[350px] overflow-y-auto scrollbar-thin">
                    <div className="p-2 space-y-1">
                      {[
                        { label: 'TRIM Whitespace', value: 'TRIM' },
                        { label: 'UPPERCASE', value: 'UPPER' },
                        { label: 'Lowercase', value: 'LOWER' },
                        { label: 'InitCap', value: 'INITCAP' },
                        { label: 'Coalesce (0)', value: 'COALESCE(?, 0)' },
                        { label: "Coalesce ('N/A')", value: "COALESCE(?, 'N/A')" },
                        { label: 'Nullify Empty', value: "NULLIF(?, '')" },
                        { label: 'Cast to DATE', value: 'CAST(? AS DATE)' },
                        { label: 'Cast to NUMBER', value: 'CAST(? AS NUMBER)' },
                        { label: 'Extract Digits', value: "REGEXP_REPLACE(?, '[^0-9]', '')" },
                        { label: 'MD5 Hash', value: 'MD5(?)' },
                      ].map((opt) => (
                        <button
                          key={opt.value}
                          onClick={() => {
                            handleBulkApplyCleaning(opt.value);
                            setBulkMenuOpen(false);
                          }}
                          className="w-full text-left px-3 py-2.5 text-sm text-gray-700 hover:bg-blue-50 hover:text-blue-700 rounded-lg transition-colors flex items-center justify-between group"
                        >
                          {opt.label}
                          <ArrowRight className="w-3.5 h-3.5 opacity-0 group-hover:opacity-100 transition-opacity" />
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="h-6 w-px bg-gray-700 mx-1" />

            <button
              onClick={() => {
                setSelectedRowIds([]);
                setBulkMenuOpen(false);
              }}
              className="text-sm text-gray-400 hover:text-white transition-colors px-2"
            >
              Deselect All
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
