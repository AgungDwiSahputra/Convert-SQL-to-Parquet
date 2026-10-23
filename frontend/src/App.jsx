import { Navigate, Route, Routes, useLocation } from 'react-router'
import Layout from './components/Layout'
import { LoadingBlock } from './components/ui'
import { hasRole, useAuth } from './lib/auth'
import AdhocPage from './pages/AdhocPage'
import DashboardPage from './pages/DashboardPage'
import SchedulesPage from './pages/SchedulesPage'
import SettingsPage from './pages/SettingsPage'
import JobDetailPage from './pages/JobDetailPage'
import JobFormPage from './pages/JobFormPage'
import JobsPage from './pages/JobsPage'
import LoginPage from './pages/LoginPage'
import NewRunPage from './pages/NewRunPage'
import RunDetailPage from './pages/RunDetailPage'
import RunsPage from './pages/RunsPage'
import UsersPage from './pages/UsersPage'

function RequireAuth({ children }) {
  const { user } = useAuth()
  const location = useLocation()
  if (user === undefined) return <LoadingBlock label="Memeriksa session…" />
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  return children
}

function RequireRole({ role, children }) {
  const { user } = useAuth()
  return hasRole(user, role) ? children : <Navigate to="/" replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/new" element={<RequireRole role="operator"><NewRunPage /></RequireRole>} />
        <Route path="runs/:id" element={<RunDetailPage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="jobs/new" element={<RequireRole role="operator"><JobFormPage /></RequireRole>} />
        <Route path="jobs/:name" element={<JobDetailPage />} />
        <Route path="jobs/:name/edit" element={<RequireRole role="operator"><JobFormPage key="edit" /></RequireRole>} />
        <Route path="schedules" element={<SchedulesPage />} />
        <Route path="export" element={<AdhocPage />} />
        <Route path="users" element={<RequireRole role="admin"><UsersPage /></RequireRole>} />
        <Route path="settings" element={<RequireRole role="admin"><SettingsPage /></RequireRole>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
