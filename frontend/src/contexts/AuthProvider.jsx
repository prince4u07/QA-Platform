import { useEffect, useMemo, useState } from "react";
import { AuthContext, readStoredUser } from "./authStore";

export const AuthProvider = ({ children }) => {
  // Read synchronously from storage on first render: there is no async step,
  // so the app never has to render a "checking session" flash.
  const [user, setUser] = useState(readStoredUser);
  const [token, setToken] = useState(() => localStorage.getItem("token") || null);

  useEffect(() => {
    const syncStoredSession = () => {
      setToken(localStorage.getItem("token") || null);
      setUser(readStoredUser());
    };

    const handleSessionExpired = () => {
      setToken(null);
      setUser(null);
    };

    window.addEventListener("storage", syncStoredSession);
    window.addEventListener("qa:session-expired", handleSessionExpired);

    return () => {
      window.removeEventListener("storage", syncStoredSession);
      window.removeEventListener("qa:session-expired", handleSessionExpired);
    };
  }, []);

  const login = (accessToken, userData) => {
    localStorage.setItem("token", accessToken);
    localStorage.setItem("user", JSON.stringify(userData));
    setToken(accessToken);
    setUser(userData);
  };

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    setToken(null);
    setUser(null);
  };

  const isAuthenticated = useMemo(() => !!token && !!user, [token, user]);

  const value = useMemo(
    () => ({ user, token, isAuthenticated, login, logout, setUser }),
    [user, token, isAuthenticated]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export default AuthProvider;
