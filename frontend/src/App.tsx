import { Navigate, Route, Routes } from 'react-router-dom';
import ReviewPage from './pages/ReviewPage';
import TaskDetailPage from './pages/TaskDetailPage';
import AdminPortalPage from './pages/AdminPortalPage';
import LoginPage from './pages/LoginPage';
import TeacherPortalPage from './pages/TeacherPortalPage';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<ReviewPage />} />
      <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/teacher" element={<TeacherPortalPage />} />
      <Route path="/admin" element={<AdminPortalPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
