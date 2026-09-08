import { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { User, Mail, Lock, Eye, EyeOff, Check, AlertCircle, Loader2, ShieldCheck } from 'lucide-react';
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
      navigate('/login');
    } catch (_) {
      setSubmitError(_.response?.data?.error || 'Something went wrong. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // ===== Helper: border color =====
  const getBorderColor = (check, formatValid, value) => {
    if (!value) return 'border-white/10';
    if (!formatValid) return 'border-red-500/50';
    if (check.status === 'available') return 'border-emerald-600/60';
    if (check.status === 'taken' || check.status === 'invalid') return 'border-red-500/50';
    return 'border-white/10';
  };

  const inputBase =
    'w-full rounded-md bg-[#0f1419] border pl-10 pr-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 ' +
    'focus:outline-none focus:border-[#4c8dff] transition disabled:opacity-60';

  return (
    <div className="relative min-h-screen flex items-center justify-center p-4">
      <AuthBackground />

      <div className="card w-full max-w-[400px] p-6">
        <div className="flex items-center gap-2.5 mb-6">
          <div className="grid place-items-center w-8 h-8 rounded-md bg-[#2f6fed]">
            <ShieldCheck className="w-4 h-4 text-white" strokeWidth={2.2} />
          </div>
          <div>
            <p className="text-[15px] font-semibold text-slate-100 leading-tight">QA Platform</p>
            <p className="text-xs text-slate-500">Create an account</p>
          </div>
        </div>

        {submitError && (
          <div className="flex items-start gap-2 bg-red-500/10 border border-red-500/25 text-red-300 px-3 py-2.5 rounded-md mb-4 text-[13px]">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{submitError}</span>
          </div>
        )}

        <form onSubmit={handleRegister} className="space-y-4">
          {/* Username */}
          <div>
            <label className="block text-slate-300 text-[13px] font-medium mb-1">Username</label>
            <div className="relative">
              <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
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
            <label className="block text-slate-300 text-[13px] font-medium mb-1">Email</label>
            <div className="relative">
              <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
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
                <a href="/login" className="text-brand-sky hover:underline font-medium">Login instead?</a>
              </p>
            )}
          </div>

          {/* Password */}
          <div>
            <label className="block text-slate-300 text-[13px] font-medium mb-1">Password</label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={loading}
                className={`${inputBase} pr-10 border-white/10`}
                placeholder="Min. 8 characters"
                required
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition"
                tabIndex={-1}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          {/* Password Rules */}
          {password && (
            <div className="bg-white/[0.02] border border-white/10 p-3.5 rounded-md">
              <p className="text-[13px] font-medium text-slate-300 mb-2">Password requirements</p>
              <div className="space-y-1.5">
                <Rule met={passwordRules.minLength} text="At least 8 characters" />
                <Rule met={passwordRules.hasUppercase} text="One uppercase letter" />
                <Rule met={passwordRules.hasLowercase} text="One lowercase letter" />
                <Rule met={passwordRules.hasNumber} text="One number" />
                <Rule met={passwordRules.hasSpecial} text="One special character" />
              </div>
            </div>
          )}

          {/* Submit Button */}
          <button
            type="submit"
            disabled={!canSubmit}
            className="btn-primary w-full py-2 text-sm flex items-center justify-center gap-2"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Creating account…
              </>
            ) : (
              <>Create account</>
            )}
          </button>
        </form>

        <p className="mt-5 text-center text-slate-500 text-[13px]">
          Already have an account?{' '}
          <Link to="/login" className="text-[#7aa8ff] hover:underline font-medium">Sign in</Link>
        </p>
      </div>
    </div>
  );
};

// ===== Helper: message under field =====
const FieldMessage = ({ check }) => {
  if (check.status === 'idle') return null;
  if (check.status === 'checking') {
    return (
      <p className="text-slate-500 text-[13px] mt-1 flex items-center gap-1">
        <Loader2 className="w-3.5 h-3.5 animate-spin" /> {check.message}
      </p>
    );
  }
  if (check.status === 'available') {
    return (
      <p className="text-emerald-400 text-[13px] mt-1 flex items-center gap-1">
        <Check className="w-3.5 h-3.5" /> {check.message}
      </p>
    );
  }
  return (
    <p className="text-red-400 text-[13px] mt-1 flex items-center gap-1">
      <AlertCircle className="w-3.5 h-3.5" /> {check.message}
    </p>
  );
};

const Rule = ({ met, text }) => (
  <div className="flex items-center gap-2">
    <div
      className={`w-4 h-4 rounded-full flex items-center justify-center ${
        met ? 'bg-emerald-600' : 'bg-white/10'
      }`}
    >
      {met && <Check className="w-3 h-3 text-white" strokeWidth={3} />}
    </div>
    <span className={`text-[13px] ${met ? 'text-slate-300' : 'text-slate-500'}`}>{text}</span>
  </div>
);

export default Register;
