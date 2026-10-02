import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AuthProvider from "./contexts/AuthProvider";
import PublicOnlyRoute from "./components/PublicOnlyRoute";
import SessionExpiredToast from "./components/SessionExpiredToast";

import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";
import TestCases from "./pages/TestCases";
import BugTracker from "./pages/BugTracker";
import AIAssistant from "./pages/AIAssistant";
import Reports from "./pages/Reports";
import Admin from "./pages/Admin";

// The app is browsable, so the entry point goes to the dashboard for everyone.
// A guest lands in read-only mode and is asked to sign in when they use a
// feature; sending them to /login instead would make the whole app look gated.
function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <SessionExpiredToast />
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route
            path="/login"
            element={
              <PublicOnlyRoute>
                <Login />
              </PublicOnlyRoute>
            }
          />
          <Route
            path="/register"
            element={
              <PublicOnlyRoute>
                <Register />
              </PublicOnlyRoute>
            }
          />
          {/* Public: everyone can read these. Requiring a token happens at the
              point of action, not at the point of navigation. */}
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/testcases" element={<TestCases />} />
          <Route path="/bugs" element={<BugTracker />} />
          <Route path="/ai-assistant" element={<AIAssistant />} />
          <Route path="/reports" element={<Reports />} />
          {/* Admin is role-gated inside the page; the server enforces it too. */}
          <Route path="/admin" element={<Admin />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
