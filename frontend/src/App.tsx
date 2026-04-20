/**
 * 应用路由配置
 */
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { AuthProvider } from './contexts/AuthContext';
import RequireAuth from './components/RequireAuth';
import MainLayout from './layouts/MainLayout';
import LoginPage from './pages/LoginPage';
import KnowledgePage from './pages/KnowledgePage';
import DocumentsPage from './pages/DocumentsPage';
import UploadPage from './pages/UploadPage';
import ChatPage from './pages/ChatPage';

export default function App() {
  return (
    <ConfigProvider locale={zhCN}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            {/* 公开路由 */}
            <Route path="/login" element={<LoginPage />} />

            {/* 受保护路由 */}
            <Route
              path="/"
              element={
                <RequireAuth>
                  <MainLayout />
                </RequireAuth>
              }
            >
              <Route index element={<Navigate to="/knowledge" replace />} />
              <Route path="knowledge" element={<KnowledgePage />} />
              <Route path="knowledge/:kbId/documents" element={<DocumentsPage />} />
              <Route path="upload" element={<UploadPage />} />
              <Route path="chat" element={<ChatPage />} />
            </Route>

            {/* 404 兜底 */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </ConfigProvider>
  );
}
