import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../contexts/useAuth";

/**
 * Keeps signed-in users away from /login and /register so they never see a
 * form they do not need, and remembers where they were headed before.
 */
const PublicOnlyRoute = ({ children }) => {
  const { isAuthenticated } = useAuth();
  const location = useLocation();

  if (isAuthenticated) {
    const from = location.state?.from?.pathname;
    return <Navigate to={from || "/dashboard"} replace />;
  }

  return children;
};

export default PublicOnlyRoute;
