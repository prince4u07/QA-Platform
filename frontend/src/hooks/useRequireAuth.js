import { useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/useAuth";

/**
 * Gate for actions, as opposed to pages.
 *
 * Pages are readable by anyone. Anything that creates, edits, deletes, exports
 * or runs something belongs to an account, so it needs a session first. Call
 * `requireAuth()` at the top of a click handler; it returns false and sends the
 * visitor to the login page when there is no session, and true otherwise.
 *
 *   const requireAuth = useRequireAuth();
 *   const onCreate = () => {
 *     if (!requireAuth()) return;
 *     openModal();
 *   };
 */
export const useRequireAuth = () => {
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  return useCallback(
    (destination) => {
      if (isAuthenticated) return true;

      // Remember where they were so sign-in can return them to it.
      sessionStorage.setItem("redirectAfterLogin", destination || location.pathname);
      navigate("/login", { state: { from: location } });
      return false;
    },
    [isAuthenticated, navigate, location]
  );
};

export default useRequireAuth;
