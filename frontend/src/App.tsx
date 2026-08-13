import { Navigate, Route, Routes } from 'react-router-dom';
import ReviewPage from './pages/ReviewPage';
import TaskDetailPage from './pages/TaskDetailPage';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<ReviewPage />} />
      <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
