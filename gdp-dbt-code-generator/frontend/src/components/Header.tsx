import { Code2 } from 'lucide-react';

export default function Header() {
  return (
    <header className="bg-white shadow-sm border-b border-gray-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="py-4 flex items-center gap-3">
          <div className="bg-blue-600 p-2 rounded-lg">
            <Code2 className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-gray-900">🤖 DBT AI Transformation Code Generator</h1>
            <p className="text-sm text-gray-500">Source to Target DBT Transformation Pipeline</p>
          </div>
        </div>
      </div>
    </header>
  );
}
