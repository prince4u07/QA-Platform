import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Mail, Lock, Eye, EyeOff, AlertCircle, Loader2, ShieldCheck } from 'lucide-react';
import axios from '../api/axiosConfig';
import AuthBackground from '../components/AuthBackground';

const Login = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState({
    email: '',
    password: '',
    general: ''
  });
  const navigate = useNavigate();

  const isEmailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  const isValid = isEmailValid && password.length > 0;

  const handleLogin = async (e) => {
    e.preventDefault();
    setErrors({ email: '', password: '', general: '' });
    setLoading(true);

    try {
      const response = await axios.post('/auth/login', {
        email,
        password
      });
      localStorage.setItem('token', response.data.access_token);
      localStorage.setItem('user', JSON.stringify(response.data.user));
      const next = sessionStorage.getItem('redirectAfterLogin'); sessionStorage.removeItem('redirectAfterLogin'); navigate(next ? next : '/dashboard');
    } catch (err) {
      const data = err.response?.data || {};
      const field = data.field;
      const message = data.error || 'Something went wrong. Please try again.';

      if (field === 'email') {
        setErrors({ email: message, password: '', general: '' });
      } else if (field === 'password') {
        setErrors({ email: '', password: message, general: '' });
      } else {
        setErrors({ email: '', password: '', general: message });
      }
    } finally {
      setLoading(false);
    }
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
            <p className="text-xs text-slate-500">Sign in to your account</p>
          </div>
        </div>

        {errors.general && (
          <div className="flex items-start gap-2 bg-red-500/10 border border-red-500/25 text-red-300 px-3 py-2.5 rounded-md mb-4 text-[13px]">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{errors.general}</span>
          </div>
        )}

        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label className="block text-slate-300 text-[13px] font-medium mb-1">Email</label>
            <div className="relative">
              <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input
                type="email"
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  if (errors.email) setErrors({ ...errors, email: '' });
                }}
                disabled={loading}
                className={`${inputBase} ${
                  errors.email || (email && !isEmailValid)
                    ? 'border-red-500/50'
                    : 'border-white/10'
                }`}
                placeholder="you@company.com"
                required
              />
            </div>
            {errors.email ? (
              <p className="text-red-400 text-[13px] mt-1">
                {errors.email}{' '}
                {errors.email.includes('No account') && (
                  <Link to="/register" className="underline font-medium">Register</Link>
                )}
              </p>
            ) : email && !isEmailValid ? (
              <p className="text-red-400 text-[13px] mt-1">Enter a valid email address.</p>
            ) : null}
          </div>

          <div>
            <label className="block text-slate-300 text-[13px] font-medium mb-1">Password</label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                  if (errors.password) setErrors({ ...errors, password: '' });
                }}
                disabled={loading}
                className={`${inputBase} pr-10 ${
                  errors.password ? 'border-red-500/50' : 'border-white/10'
                }`}
                placeholder="••••••••"
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
            {errors.password && (
              <p className="text-red-400 text-[13px] mt-1">{errors.password}</p>
            )}
          </div>

          <button
            type="submit"
            disabled={!isValid || loading}
            className="btn-primary w-full py-2 text-sm flex items-center justify-center gap-2"
          >
            {loading && <Loader2 className="w-4 h-4 animate-spin" />}
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="mt-5 text-center text-slate-500 text-[13px]">
          No account yet?{' '}
          <Link to="/register" className="text-[#7aa8ff] hover:underline font-medium">Create one</Link>
        </p>
      </div>
    </div>
  );
};

export default Login;
