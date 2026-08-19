import { Navigate, Route, Routes } from 'react-router-dom';
import ReviewPage from './pages/ReviewPage';
import TaskDetailPage from './pages/TaskDetailPage';
import AdminPortalPage from './pages/AdminPortalPage';
import LoginPage from './pages/LoginPage';
import TeacherPortalPage from './pages/TeacherPortalPage';
import StudentRecoverPage from './pages/StudentRecoverPage';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/student" replace />} />
      <Route path="/student" element={<ReviewPage />} />
      <Route path="/student/tasks/:taskId" element={<TaskDetailPage />} />
      <Route path="/student/recover" element={<StudentRecoverPage />} />
      <Route path="/teacher/login" element={<LoginPage expectedRole="teacher" />} />
      <Route path="/admin/login" element={<LoginPage expectedRole="admin" />} />
      <Route path="/teacher" element={<TeacherPortalPage />} />
      <Route path="/admin" element={<AdminPortalPage />} />
      <Route path="*" element={<Navigate to="/student" replace />} />
    </Routes>
  );
}
