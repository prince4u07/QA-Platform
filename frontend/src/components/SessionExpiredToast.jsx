import { useEffect, useState } from "react";
import { AlertTriangle, X } from "lucide-react";

const SessionExpiredToast = () => {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const handler = () => {
      setVisible(true);
      const t = setTimeout(() => setVisible(false), 5000);
      return () => clearTimeout(t);
    };

    window.addEventListener("qa:session-expired", handler);
    return () => window.removeEventListener("qa:session-expired", handler);
  }, []);

  if (!visible) return null;

  return (
    <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50">
      <div className="glass-strong px-4 py-2 rounded-xl flex items-center gap-3 shadow-card border border-red-500/40">
        <AlertTriangle className="w-4 h-4 text-red-400" />
        <p className="text-sm text-slate-200">Your session expired. Please log in again.</p>
        <button
          onClick={() => setVisible(false)}
          className="p-1 rounded-lg hover:bg-white/10 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
};

export default SessionExpiredToast;
