import { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { User, Mail, Lock, Eye, EyeOff, UserPlus, Check, AlertCircle, Loader2, Sparkles } from 'lucide-react';
import axios from '../api/axiosConfig';
import AuthBackground from '../components/AuthBackground';

const Register = () => {
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);

  // Real-time check states
  const [usernameCheck, setUsernameCheck] = useState({ status: 'idle', message: '' });
  const [emailCheck, setEmailCheck] = useState({ status: 'idle', message: '' });

  // Submit error state
  const [submitError, setSubmitError] = useState('');

  const navigate = useNavigate();

  // Password rules
  const passwordRules = {
    minLength: password.length >= 8,
    hasUppercase: /[A-Z]/.test(password),
    hasLowercase: /[a-z]/.test(password),
    hasNumber: /[0-9]/.test(password),
    hasSpecial: /[!@#$%^&*(),.?":{}|<>]/.test(password)
  };

  const isEmailFormatValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  const isUsernameFormatValid = username.length >= 3;
  const allPasswordRulesMet = Object.values(passwordRules).every(Boolean);

  const canSubmit =
    isUsernameFormatValid &&
    isEmailFormatValid &&
    allPasswordRulesMet &&
    usernameCheck.status !== 'taken' &&
    usernameCheck.status !== 'invalid' &&
    emailCheck.status !== 'taken' &&
    emailCheck.status !== 'invalid' &&
    !loading;

  // ===== REAL-TIME USERNAME CHECK =====
  useEffect(() => {
    const timer = setTimeout(async () => {
      if (!username) {
        setUsernameCheck({ status: 'idle', message: '' });
        return;
      }
      if (!isUsernameFormatValid) {
        setUsernameCheck({ status: 'invalid', message: 'Username must be at least 3 characters' });
        return;
      }

      setUsernameCheck({ status: 'checking', message: 'Checking...' });
      try {
        const res = await axios.post('/auth/check-username', { username });
        if (res.data.available) {
          setUsernameCheck({ status: 'available', message: 'Username available' });
        } else {
          setUsernameCheck({ status: 'taken', message: 'Username already taken' });
        }
      } catch {
        setUsernameCheck({ status: 'idle', message: '' });
      }
    }, 400);

    return () => clearTimeout(timer);
  }, [username, isUsernameFormatValid]);

  // ===== REAL-TIME EMAIL CHECK =====
  useEffect(() => {
    const timer = setTimeout(async () => {
      if (!email) {
        setEmailCheck({ status: 'idle', message: '' });
        return;
      }
      if (!isEmailFormatValid) {
        setEmailCheck({ status: 'invalid', message: 'Please enter a valid email' });
        return;
      }

      setEmailCheck({ status: 'checking', message: 'Checking...' });
      try {
        const res = await axios.post('/auth/check-email', { email });
        if (res.data.available) {
          setEmailCheck({ status: 'available', message: 'Email available' });
        } else {
          setEmailCheck({ status: 'taken', message: 'Email already registered' });
        }
      } catch {
        setEmailCheck({ status: 'idle', message: '' });
      }
    }, 400);

    return () => clearTimeout(timer);
  }, [email, isEmailFormatValid]);

  // ===== HANDLE SUBMIT =====
  const handleRegister = async (e) => {
    e.preventDefault();
    setSubmitError('');
    setLoading(true);
    try {
      await axios.post('/auth/register', {
        username,
        email,
        password
      });
      navigate('/login', { replace: true });
    } catch (_) {
      setSubmitError(_.response?.data?.error || 'Something went wrong. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // ===== Helper: border color (dark theme) =====
  const getBorderColor = (check, formatValid, value) => {
    if (!value) return 'border-white/10 focus:ring-brand-sky/60';
    if (!formatValid) return 'border-red-500/60 focus:ring-red-500/50';
    if (check.status === 'available') return 'border-brand-teal/60 focus:ring-brand-teal/50';
    if (check.status === 'taken' || check.status === 'invalid') return 'border-red-500/60 focus:ring-red-500/50';
    return 'border-white/10 focus:ring-brand-sky/60';
  };

  const inputBase =
    'w-full rounded-xl bg-white/5 border pl-11 pr-4 py-3 text-slate-100 placeholder:text-slate-500 ' +
    'focus:outline-none focus:ring-2 focus:ring-brand-sky/50 focus:border-brand-sky/50 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed';

  return (
    <div className="relative min-h-screen flex items-center justify-center p-4 overflow-hidden">
      <AuthBackground />

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: 'easeOut' }}
        className="glass-strong rounded-2xl shadow-card w-full max-w-md p-8"
      >
        {/* Brand */}
        <div className="flex flex-col items-center text-center mb-7">
          <div className="grid place-items-center w-12 h-12 rounded-xl bg-brand-gradient shadow-glow mb-4">
            <Sparkles className="w-6 h-6 text-white" strokeWidth={2.2} />
          </div>
          <h1 className="text-2xl font-display font-bold text-white tracking-tight mb-1">Create your account</h1>
          <p className="text-slate-400 text-sm mt-1.5">Create your account to start testing reliably</p>
        </div>
        {submitError && (
          <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 text-red-300 px-4 py-3 rounded-xl mb-5 text-sm">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{submitError}</span>
          </div>
        )}

        <form onSubmit={handleRegister} className="space-y-4">
          {/* Username */}
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1.5">Username</label>
            <div className="relative">
              <User className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={loading}
                className={`${inputBase} ${getBorderColor(usernameCheck, isUsernameFormatValid, username)}`}
                placeholder="Choose a username"
                required
              />
            </div>
            <FieldMessage check={usernameCheck} />
          </div>

          {/* Email */}
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1.5">Email</label>
            <div className="relative">
              <Mail className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={loading}
                className={`${inputBase} ${getBorderColor(emailCheck, isEmailFormatValid, email)}`}
                placeholder="your.email@example.com"
                required
              />
            </div>
            <FieldMessage check={emailCheck} />
            {emailCheck.status === 'taken' && (
              <p className="text-sm mt-1">
                <Link to="/login" className="text-brand-sky hover:underline font-medium">Login instead?</Link>
              </p>
            )}
          </div>

          {/* Password */}
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1.5">Password</label>
            <div className="relative">
              <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={loading}
                className={`${inputBase} pr-11 border-white/10 focus:ring-brand-sky/60`}
                placeholder="Create a strong password"
                required
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition"
                tabIndex={-1}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          {/* Password Rules */}
          {password && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              transition={{ duration: 0.3 }}
              className="bg-white/5 border border-white/10 p-4 rounded-xl"
            >
              <p className="text-sm font-medium text-slate-300 mb-2">Password must contain:</p>
              <div className="space-y-1.5">
                <Rule met={passwordRules.minLength} text="At least 8 characters" />
                <Rule met={passwordRules.hasUppercase} text="One uppercase letter" />
                <Rule met={passwordRules.hasLowercase} text="One lowercase letter" />
                <Rule met={passwordRules.hasNumber} text="One number" />
                <Rule met={passwordRules.hasSpecial} text="One special character" />
              </div>
            </motion.div>
          )}

          {/* Submit Button */}
          <motion.button
            type="submit"
            disabled={!canSubmit}
            whileTap={canSubmit ? { scale: 0.98 } : undefined}
            whileHover={canSubmit ? { scale: 1.02 } : undefined}
            className={`w-full py-3 rounded-xl font-semibold transition-all flex items-center justify-center gap-2 ${
              canSubmit
                ? 'bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal hover:-translate-y-0.5'
                : 'bg-white/5 text-slate-500 border border-white/10 cursor-not-allowed'
            }`}
          >
            {loading ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                Creating account...
              </>
            ) : (
              <>
                <UserPlus className="w-5 h-5" />
                {canSubmit ? 'Create Account' : 'Complete all requirements'}
              </>
            )}
          </motion.button>
        </form>

        <p className="mt-6 text-center text-slate-400 text-sm">
          Already have an account?{' '}
          <Link to="/login" className="text-brand-sky hover:underline font-medium">Login</Link>
        </p>
      </motion.div>
    </div>
  );
};

// ===== Helper: message under field =====
const FieldMessage = ({ check }) => {
  if (check.status === 'idle') return null;
  if (check.status === 'checking') {
    return (
      <p className="text-slate-400 text-sm mt-1.5 flex items-center gap-1">
        <Loader2 className="w-3.5 h-3.5 animate-spin" /> {check.message}
      </p>
    );
  }
  if (check.status === 'available') {
    return (
      <p className="text-brand-teal text-sm mt-1.5 flex items-center gap-1">
        <Check className="w-3.5 h-3.5" /> {check.message}
      </p>
    );
  }
  return (
    <p className="text-red-400 text-sm mt-1.5 flex items-center gap-1">
      <AlertCircle className="w-3.5 h-3.5" /> {check.message}
    </p>
  );
};

const Rule = ({ met, text }) => (
  <div className="flex items-center gap-2">
    <div
      className={`w-4 h-4 rounded-full flex items-center justify-center transition-colors ${
        met ? 'bg-brand-teal' : 'bg-white/10'
      }`}
    >
      {met && <Check className="w-3 h-3 text-white" strokeWidth={3} />}
    </div>
    <span className={`text-sm transition-colors ${met ? 'text-slate-200' : 'text-slate-500'}`}>{text}</span>
  </div>
);

export default Register;
