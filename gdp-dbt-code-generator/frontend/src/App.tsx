import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Header from './components/Header';
import ProjectsPage from './pages/ProjectsPage';
import MappingPage from './pages/MappingPage';
import CodeViewerPage from './pages/CodeViewerPage';

function App() {
  return (
    <Router>
      <div className="h-screen bg-gray-100 flex flex-col overflow-hidden">
        <Header />
        <div className="flex-1 overflow-hidden">
          <Routes>
          <Route path="/" element={<ProjectsPage />} />
          <Route path="/project/:projectId/mapping" element={<MappingPage />} />
          <Route path="/project/:projectId/code" element={<CodeViewerPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </div>
    </Router>
  );
}

export default App;
