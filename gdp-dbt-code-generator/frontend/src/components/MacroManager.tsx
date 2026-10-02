import { useState, useEffect } from 'react';
import { Plus, Save, Trash2, Bot, X, Search, Code } from 'lucide-react';
import {
    useGetMacros,
    useCreateMacro,
    useAIGenerateMacro,
    useDeleteMacro,
    Macro
} from '../hooks/useMappingsApi';

interface MacroManagerProps {
    onClose: () => void;
}

export default function MacroManager({ onClose }: MacroManagerProps) {
    const [searchTerm, setSearchTerm] = useState('');
    const [macros, setMacros] = useState<Macro[]>([]);
    const [selectedMacro, setSelectedMacro] = useState<Partial<Macro> | null>(null);
    const [aiPrompt, setAiPrompt] = useState('');
    const [isAiGenerating, setIsAiGenerating] = useState(false);

    const { getMacros } = useGetMacros();
    const { createMacro, isLoading: isSaving } = useCreateMacro();
    const { deleteMacro } = useDeleteMacro();
    const { aiGenerateMacro, isLoading: isGenerating } = useAIGenerateMacro();

    useEffect(() => {
        loadMacros();
    }, []);

    const loadMacros = async () => {
        try {
            const response = await getMacros();
            if (response.success) {
                setMacros(response.payload);
            }
        } catch (err) {
            console.error('Failed to load macros', err);
        }
    };

    const handleCreateNew = () => {
        setSelectedMacro({
            name: 'new_macro',
            description: '',
            sql_content: '{% macro new_macro(col) %}\n  {{ col }}\n{% endmacro %}'
        });
    };

    const handleSave = async () => {
        if (!selectedMacro?.name || !selectedMacro?.sql_content) return;
        try {
            const response = await createMacro({
                name: selectedMacro.name,
                description: selectedMacro.description || '',
                sql_content: selectedMacro.sql_content
            });
            if (response.success) {
                await loadMacros();
                setSelectedMacro(null);
            }
        } catch (err) {
            console.error('Failed to save macro', err);
        }
    };

    const handleDelete = async (id: number) => {
        if (!window.confirm('Are you sure you want to delete this macro?')) return;
        try {
            await deleteMacro(id);
            await loadMacros();
            if (selectedMacro?.id === id) setSelectedMacro(null);
        } catch (err) {
            console.error('Failed to delete macro', err);
        }
    };

    const handleAiGenerate = async () => {
        if (!aiPrompt.trim()) return;
        try {
            const response = await aiGenerateMacro(aiPrompt);
            if (response.success) {
                setSelectedMacro({
                    name: response.payload.name,
                    description: response.payload.description || aiPrompt,
                    sql_content: response.payload.sql
                });
                setAiPrompt('');
                setIsAiGenerating(false);
            }
        } catch (err) {
            console.error('AI generation failed', err);
        }
    };

    const filteredMacros = macros.filter(m =>
        m.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (m.description?.toLowerCase() || '').includes(searchTerm.toLowerCase())
    );

    return (
        <div className="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center z-50 p-4 backdrop-blur-sm">
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden border border-gray-100">

                {/* Header */}
                <div className="px-8 py-6 border-b border-gray-200 flex items-center justify-between bg-gradient-to-r from-gray-50 to-white">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-blue-100 rounded-lg text-blue-600">
                            <Code className="w-6 h-6" />
                        </div>
                        <div>
                            <h2 className="text-2xl font-bold text-gray-900Letter spacing-tight">Managed Macro Library</h2>
                            <p className="text-sm text-gray-500 font-medium">Define and maintain business-specific transformations</p>
                        </div>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-full transition-colors text-gray-400">
                        <X className="w-6 h-6" />
                    </button>
                </div>

                <div className="flex-1 flex overflow-hidden">
                    {/* Sidebar */}
                    <div className="w-80 border-r border-gray-200 flex flex-col bg-gray-50">
                        <div className="p-4 space-y-3">
                            <div className="relative">
                                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                                <input
                                    type="text"
                                    placeholder="Search macros..."
                                    className="w-full pl-9 pr-4 py-2 text-sm border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all"
                                    value={searchTerm}
                                    onChange={(e) => setSearchTerm(e.target.value)}
                                />
                            </div>
                            <button
                                onClick={handleCreateNew}
                                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all font-semibold shadow-md active:scale-95"
                            >
                                <Plus className="w-4 h-4" />
                                New Macro
                            </button>
                        </div>

                        <div className="flex-1 overflow-y-auto px-2 pb-4 scrollbar-thin">
                            {filteredMacros.map(macro => (
                                <div
                                    key={macro.id}
                                    onClick={() => setSelectedMacro(macro)}
                                    className={`group p-4 mb-2 rounded-xl cursor-pointer transition-all border-2 ${selectedMacro?.id === macro.id
                                        ? 'bg-blue-50 border-blue-500 shadow-sm'
                                        : 'border-transparent hover:bg-white hover:border-gray-200 hover:shadow-sm'
                                        }`}
                                >
                                    <div className="flex justify-between items-start mb-1">
                                        <span className="font-bold text-gray-900 truncate flex-1">{macro.name}</span>
                                        <button
                                            onClick={(e) => { e.stopPropagation(); handleDelete(macro.id); }}
                                            className="opacity-0 group-hover:opacity-100 p-1.5 text-red-500 hover:bg-red-50 rounded-lg transition-all"
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    </div>
                                    <p className="text-xs text-gray-500 line-clamp-2 leading-relaxed">{macro.description}</p>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Content Area */}
                    <div className="flex-1 flex flex-col bg-white">
                        {selectedMacro ? (
                            <div className="flex-1 flex flex-col p-8 overflow-y-auto scrollbar-thin">
                                <div className="space-y-6">
                                    <div className="grid grid-cols-2 gap-6">
                                        <div>
                                            <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Macro Name</label>
                                            <input
                                                type="text"
                                                className="w-full px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 transition-all font-mono font-bold text-blue-700 bg-blue-50/30"
                                                value={selectedMacro.name || ''}
                                                onChange={e => setSelectedMacro({ ...selectedMacro, name: e.target.value })}
                                            />
                                        </div>
                                        <div>
                                            <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Description</label>
                                            <input
                                                type="text"
                                                className="w-full px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 transition-all text-gray-700"
                                                value={selectedMacro.description || ''}
                                                onChange={e => setSelectedMacro({ ...selectedMacro, description: e.target.value })}
                                            />
                                        </div>
                                    </div>

                                    <div className="flex-1">
                                        <div className="flex items-center justify-between mb-2">
                                            <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider">SQL Source (Jinja)</label>
                                            <div className="flex gap-2">
                                                <button
                                                    onClick={() => setIsAiGenerating(!isAiGenerating)}
                                                    className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-50 text-indigo-700 rounded-lg hover:bg-indigo-100 transition-all text-xs font-bold ring-1 ring-indigo-200"
                                                >
                                                    <Bot className="w-3.5 h-3.5" />
                                                    AI Helper
                                                </button>
                                            </div>
                                        </div>

                                        {isAiGenerating && (
                                            <div className="mb-4 p-4 bg-indigo-50 rounded-xl border border-indigo-200 animate-in slide-in-from-top-2">
                                                <div className="flex gap-2">
                                                    <input
                                                        type="text"
                                                        placeholder="Describe what the macro should do... (e.g., 'SSN masking')"
                                                        className="flex-1 px-4 py-2 text-sm border border-indigo-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
                                                        value={aiPrompt}
                                                        onChange={e => setAiPrompt(e.target.value)}
                                                    />
                                                    <button
                                                        onClick={handleAiGenerate}
                                                        disabled={isGenerating || !aiPrompt}
                                                        className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 flex items-center gap-2 disabled:opacity-50 font-bold"
                                                    >
                                                        {isGenerating ? 'Generating...' : 'Go'}
                                                    </button>
                                                </div>
                                            </div>
                                        )}

                                        <textarea
                                            spellCheck={false}
                                            className="w-full h-80 px-4 py-4 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 font-mono text-sm leading-relaxed text-gray-800 bg-gray-50/50"
                                            value={selectedMacro.sql_content || ''}
                                            onChange={e => setSelectedMacro({ ...selectedMacro, sql_content: e.target.value })}
                                        />
                                    </div>

                                    <div className="flex justify-end gap-3 pt-4 border-t border-gray-100">
                                        <button
                                            onClick={() => setSelectedMacro(null)}
                                            className="px-6 py-2.5 text-gray-600 font-semibold hover:bg-gray-100 rounded-xl transition-all"
                                        >
                                            Cancel
                                        </button>
                                        <button
                                            onClick={handleSave}
                                            disabled={isSaving}
                                            className="px-8 py-2.5 bg-green-600 text-white font-bold rounded-xl hover:bg-green-700 transition-all shadow-lg active:scale-95 flex items-center gap-2"
                                        >
                                            <Save className="w-4 h-4" />
                                            {isSaving ? 'Saving...' : 'Save Macro'}
                                        </button>
                                    </div>
                                </div>
                            </div>
                        ) : (
                            <div className="flex-1 flex flex-col items-center justify-center text-gray-400 p-12 text-center">
                                <div className="w-20 h-20 bg-gray-50 rounded-full flex items-center justify-center mb-6">
                                    <Code className="w-10 h-10 text-gray-300" />
                                </div>
                                <h3 className="text-xl font-bold text-gray-900 mb-2">Manage Your Library</h3>
                                <p className="max-w-xs text-sm leading-relaxed">Select a macro from the sidebar to edit its logic, or use the AI to generate a brand new one.</p>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
