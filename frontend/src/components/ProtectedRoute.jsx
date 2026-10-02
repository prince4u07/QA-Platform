import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../contexts/useAuth";

/**
 * Gate for routes that need a signed-in user.
 *
 * Browsing is public, so this is only applied to routes where anonymous access
 * makes no sense at all (e.g. an admin console). Most pages render for anyone
 * and prompt for sign-in when an action needs credentials.
 */
const ProtectedRoute = ({ children }) => {
  const { isAuthenticated } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return children;
};

export default ProtectedRoute;
