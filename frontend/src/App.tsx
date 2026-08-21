import { Navigate, Route, Routes } from 'react-router-dom';
import ReviewPage from './pages/ReviewPage';
import TaskDetailPage from './pages/TaskDetailPage';
import AdminPortalPage from './pages/AdminPortalPage';
import LoginPage from './pages/LoginPage';
import TeacherPortalPage from './pages/TeacherPortalPage';
import { PortalRoute, WorkspaceEntry } from './components/PortalRoute';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/student" replace />} />
      <Route path="/student" element={<ReviewPage />} />
      <Route path="/student/tasks/:taskId" element={<TaskDetailPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/workspace" element={<WorkspaceEntry />} />
      <Route path="/workspace/reviews" element={<PortalRoute><TeacherPortalPage /></PortalRoute>} />
      <Route path="/workspace/admin" element={<PortalRoute adminOnly><AdminPortalPage /></PortalRoute>} />
      <Route path="/teacher/login" element={<Navigate to="/login" replace />} />
      <Route path="/admin/login" element={<Navigate to="/login" replace />} />
      <Route path="/teacher" element={<Navigate to="/workspace/reviews" replace />} />
      <Route path="/admin" element={<Navigate to="/workspace/admin" replace />} />
      <Route path="*" element={<Navigate to="/student" replace />} />
    </Routes>
  );
}
