import { Navigate, Route, Routes } from 'react-router-dom';
import ReviewPage from './pages/ReviewPage';
import TaskDetailPage from './pages/TaskDetailPage';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/student" replace />} />
      <Route path="/student" element={<ReviewPage />} />
      <Route path="/student/tasks/:taskId" element={<TaskDetailPage />} />







      <Route path="*" element={<Navigate to="/student" replace />} />
    </Routes>
  );
}
