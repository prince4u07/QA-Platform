import { createContext } from "react";

// Kept in its own module so AuthProvider.jsx only exports components and
// useAuth.js only exports the hook (react-refresh requires that split).
export const AuthContext = createContext(null);

export const readStoredUser = () => {
  try {
    const stored = localStorage.getItem("user");
    return stored && stored !== "undefined" ? JSON.parse(stored) : null;
  } catch {
    return null;
  }
};
