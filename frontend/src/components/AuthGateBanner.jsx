import { Link, useLocation } from "react-router-dom";
import { Lock, ArrowRight } from "lucide-react";

const AuthGateBanner = ({ message = "Sign in to access QA features" }) => {
  const location = useLocation();

  return (
    <div className="glass-card p-3 mb-4 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <div className="grid place-items-center w-8 h-8 rounded-lg bg-indigo-500/20 text-indigo-300">
          <Lock className="w-4 h-4" />
        </div>
        <p className="text-sm text-slate-300">{message}</p>
      </div>
      <Link
        to="/login"
        state={{ from: location }}
        className="btn-primary px-3 py-1.5 text-xs flex items-center gap-1"
      >
        Sign In
        <ArrowRight className="w-3.5 h-3.5" />
      </Link>
    </div>
  );
};

export default AuthGateBanner;
