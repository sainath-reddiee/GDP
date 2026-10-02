import { useState, useMemo } from 'react';
import { Database, Layers, ShieldCheck, ArrowRight, Share2, ChevronDown, ChevronRight, Wand2, Info } from 'lucide-react';
import { TableMapping } from '../types';

interface LineageGraphProps {
    mappings: TableMapping[];
}

export default function LineageGraph({ mappings }: LineageGraphProps) {
    const [hoveredEntity, setHoveredEntity] = useState<string | null>(null);
    const [expandedTables, setExpandedTables] = useState<string[]>([]);

    // 1. Process Data Logic
    const data = useMemo(() => {
        const sources = Array.from(new Set(mappings.flatMap(m =>
            m.columns.map(c => `${c.sourceSchema}.${c.sourceTable}`)
        ))).filter(Boolean).map(id => ({
            id,
            schema: id.split('.')[0],
            table: id.split('.')[1],
            type: 'source'
        }));

        const targets = mappings.map(m => ({
            id: m.targetTable,
            table: m.targetTable,
            type: 'target',
            columns: m.columns,
            sources: Array.from(new Set(m.columns.map(c => `${c.sourceSchema}.${c.sourceTable}`)))
        }));

        return { sources, targets };
    }, [mappings]);

    const toggleExpand = (id: string) => {
        setExpandedTables(prev =>
            prev.includes(id) ? prev.filter(t => t !== id) : [...prev, id]
        );
    };

    const isHighlighted = (id: string, type: 'source' | 'target') => {
        if (!hoveredEntity) return true;

        if (type === 'source') {
            // Highlight if this source is the hovered entity or if the hovered target uses this source
            if (id === hoveredEntity) return true;
            const target = data.targets.find(t => t.id === hoveredEntity);
            return target?.sources.includes(id) || false;
        } else {
            // Highlight if this target is the hovered entity or if it uses the hovered source
            if (id === hoveredEntity) return true;
            return data.targets.find(t => t.id === id)?.sources.includes(hoveredEntity) || false;
        }
    };

    return (
        <div className="h-full flex flex-col bg-gray-50/50 p-8 overflow-y-auto scrollbar-thin">
            <div className="max-w-7xl mx-auto w-full">
                {/* Header */}
                <div className="flex items-center justify-between mb-12">
                    <div className="flex items-center gap-4">
                        <div className="p-3 bg-gradient-to-tr from-blue-600 to-indigo-600 text-white rounded-2xl shadow-xl shadow-blue-100">
                            <Share2 className="w-6 h-6" />
                        </div>
                        <div>
                            <h2 className="text-2xl font-extrabold text-gray-900 tracking-tight">Interactive Lineage</h2>
                            <p className="text-sm text-gray-500 font-medium">Hover over nodes to trace data dependencies across the medallion layers</p>
                        </div>
                    </div>
                    <div className="flex gap-2">
                        <div className="flex items-center gap-2 px-3 py-1.5 bg-blue-50 border border-blue-100 rounded-full">
                            <span className="w-2 h-2 bg-blue-500 rounded-full" />
                            <span className="text-[10px] font-bold text-blue-700 uppercase">Interactive</span>
                        </div>
                    </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-3 gap-12 relative items-start">

                    {/* LAYER 1: RAW SOURCES */}
                    <div className="space-y-6">
                        <div className="flex items-center justify-between px-2 mb-2">
                            <h3 className="text-[11px] font-black text-gray-400 uppercase tracking-[0.2em]">Layer 1: Raw Sources</h3>
                            <div className="text-[10px] font-bold text-gray-400">{data.sources.length} Tables</div>
                        </div>

                        <div className="space-y-3 relative">
                            {data.sources.map(src => (
                                <div
                                    key={src.id}
                                    onMouseEnter={() => setHoveredEntity(src.id)}
                                    onMouseLeave={() => setHoveredEntity(null)}
                                    className={`
                    relative group bg-white border-2 p-4 rounded-2xl transition-all duration-300 transform
                    ${isHighlighted(src.id, 'source') ? 'border-gray-100 shadow-sm' : 'opacity-20 grayscale scale-95'}
                    ${hoveredEntity === src.id ? 'border-blue-500 shadow-blue-100 shadow-lg -translate-y-1 scale-105 z-20' : 'hover:border-blue-200'}
                  `}
                                >
                                    <div className="flex items-center gap-3">
                                        <div className={`p-2 rounded-xl transition-colors ${hoveredEntity === src.id ? 'bg-blue-600 text-white' : 'bg-blue-50 text-blue-600'}`}>
                                            <Database className="w-5 h-5" />
                                        </div>
                                        <div>
                                            <div className="font-bold text-sm text-gray-900">{src.table}</div>
                                            <div className="text-[10px] font-mono text-gray-400 uppercase">{src.schema}</div>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* LAYER 2: BRONZE (STAGING) */}
                    <div className="space-y-6">
                        <div className="flex items-center justify-between px-2 mb-2">
                            <h3 className="text-[11px] font-black text-gray-400 uppercase tracking-[0.2em]">Layer 2: Bronze</h3>
                        </div>

                        <div className="space-y-3">
                            {data.sources.map(src => (
                                <div
                                    key={`stg-${src.id}`}
                                    className={`
                    p-4 rounded-2xl border-2 transition-all duration-300 relative
                    ${isHighlighted(src.id, 'source') ? 'bg-orange-50/30 border-orange-100 shadow-sm text-orange-900' : 'opacity-20 grayscale border-transparent scale-95'}
                    ${hoveredEntity === src.id ? 'border-orange-400 bg-orange-50 shadow-orange-100 shadow-lg z-20 scale-105' : ''}
                  `}
                                >
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-3">
                                            <div className={`p-2 rounded-xl ${hoveredEntity === src.id ? 'bg-orange-500 text-white' : 'bg-orange-100 text-orange-600'}`}>
                                                <Layers className="w-5 h-5" />
                                            </div>
                                            <span className="font-bold text-sm">stg_{src.table}</span>
                                        </div>
                                        {(hoveredEntity === src.id || (hoveredEntity && data.targets.find(t => t.id === hoveredEntity)?.sources.includes(src.id))) && (
                                            <div className="absolute -right-6 top-1/2 -translate-y-1/2 text-orange-400 animate-pulse hidden lg:block">
                                                <ArrowRight className="w-5 h-5" />
                                            </div>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* LAYER 3: SILVER (CONSOLIDATED) */}
                    <div className="space-y-6">
                        <div className="flex items-center justify-between px-2 mb-2">
                            <h3 className="text-[11px] font-black text-gray-400 uppercase tracking-[0.2em]">Layer 3: Silver</h3>
                        </div>

                        <div className="space-y-4">
                            {data.targets.map(target => (
                                <div
                                    key={target.id}
                                    onMouseEnter={() => setHoveredEntity(target.id)}
                                    onMouseLeave={() => setHoveredEntity(null)}
                                    className={`
                    relative bg-white border-2 rounded-2xl transition-all duration-300 overflow-hidden
                    ${isHighlighted(target.id, 'target') ? 'border-gray-100 shadow-md' : 'opacity-20 grayscale border-transparent scale-95'}
                    ${hoveredEntity === target.id ? 'border-green-500 shadow-green-100 shadow-xl z-20' : 'hover:border-green-200'}
                  `}
                                >
                                    {/* Header */}
                                    <div
                                        className={`p-5 flex items-center justify-between cursor-pointer ${expandedTables.includes(target.id) ? 'bg-gray-50' : ''}`}
                                        onClick={() => toggleExpand(target.id)}
                                    >
                                        <div className="flex items-center gap-4">
                                            <div className={`p-2 rounded-xl transition-colors shadow-lg ${hoveredEntity === target.id ? 'bg-green-600 text-white shadow-green-200' : 'bg-green-50 text-green-600'}`}>
                                                <ShieldCheck className="w-6 h-6" />
                                            </div>
                                            <div>
                                                <div className="font-extrabold text-gray-900">{target.id}</div>
                                                <div className="flex gap-1 mt-1">
                                                    {target.sources.map(s => (
                                                        <span key={s} className="text-[8px] font-bold px-1.5 py-0.5 bg-white border border-gray-100 text-gray-400 rounded-sm">
                                                            {s.split('.')[1]}
                                                        </span>
                                                    ))}
                                                </div>
                                            </div>
                                        </div>
                                        {expandedTables.includes(target.id) ? <ChevronDown className="w-5 h-5 text-gray-400" /> : <ChevronRight className="w-5 h-5 text-gray-400" />}
                                    </div>

                                    {/* Expanded Columns */}
                                    {expandedTables.includes(target.id) && (
                                        <div className="border-t border-gray-100 bg-gray-50/50 p-2 space-y-1 animate-in slide-in-from-top-2 duration-200">
                                            <div className="px-3 py-1 text-[9px] font-black text-gray-400 uppercase tracking-widest flex justify-between">
                                                <span>Column & Mapping</span>
                                                <span>Logic</span>
                                            </div>
                                            {target.columns.map(col => (
                                                <div key={col.id} className="group/col flex items-center justify-between p-2.5 bg-white border border-gray-100 rounded-xl hover:border-green-300 hover:shadow-sm transition-all">
                                                    <div className="flex flex-col">
                                                        <span className="text-xs font-bold text-gray-800">{col.target}</span>
                                                        <span className="text-[10px] text-gray-400 font-mono">← {col.source}</span>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        {(col.cleaningLogic || col.transformationLogic) && (
                                                            <div className="relative group/tip">
                                                                <Wand2 className="w-3.5 h-3.5 text-purple-400" />
                                                                <div className="absolute bottom-full right-0 mb-2 invisible group-hover/tip:visible bg-gray-900 text-white text-[10px] p-2 rounded-lg whitespace-nowrap z-50">
                                                                    {col.cleaningLogic || col.transformationLogic}
                                                                </div>
                                                            </div>
                                                        )}
                                                        <Info className="w-3.5 h-3.5 text-gray-300 hover:text-blue-500 cursor-help" />
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                </div>

                {/* Legend / Strategy Card */}
                <div className="mt-16 grid grid-cols-1 md:grid-cols-2 gap-8">
                    <div className="p-8 bg-white border-2 border-dashed border-gray-200 rounded-[2.5rem] flex items-center gap-6">
                        <div className="p-4 bg-purple-50 text-purple-600 rounded-2xl">
                            <Wand2 className="w-8 h-8" />
                        </div>
                        <div>
                            <h4 className="text-lg font-bold text-gray-900 mb-1">AI-Assisted Mapping</h4>
                            <p className="text-sm text-gray-500 leading-relaxed italic">
                                "Magic wand" icons indicate columns where <b>Snowflake Cortex</b> has automatically suggested standardized cleaning logic based on your company's best practices.
                            </p>
                        </div>
                    </div>

                    <div className="p-8 bg-gradient-to-br from-gray-900 to-indigo-950 rounded-[2.5rem] shadow-2xl relative overflow-hidden group">
                        <div className="absolute top-0 right-0 w-32 h-32 bg-blue-500/10 rounded-full blur-3xl group-hover:bg-blue-500/20 transition-all duration-700" />
                        <div className="relative z-10">
                            <h4 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
                                <ShieldCheck className="w-6 h-6 text-green-400" /> System Integrity
                            </h4>
                            <p className="text-gray-300 text-sm leading-relaxed mb-6">
                                Our architecture enforces <span className="text-white font-bold">strictly typed silver tables</span>. Every transformation is immutable, versioned, and automatically tested during the build phase.
                            </p>
                            <div className="flex gap-4">
                                <div className="flex flex-col">
                                    <span className="text-2xl font-bold text-white">100%</span>
                                    <span className="text-[10px] text-gray-500 font-bold uppercase tracking-widest">Test Coverage</span>
                                </div>
                                <div className="w-[1px] h-8 bg-gray-800 self-center" />
                                <div className="flex flex-col">
                                    <span className="text-2xl font-bold text-white">Ref</span>
                                    <span className="text-[10px] text-gray-500 font-bold uppercase tracking-widest">Integrity</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
