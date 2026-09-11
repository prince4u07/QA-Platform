import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Mail, Lock, Eye, EyeOff, LogIn, AlertCircle, Loader2, ShieldCheck } from 'lucide-react';
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
      const next = sessionStorage.getItem('redirectAfterLogin');
      sessionStorage.removeItem('redirectAfterLogin');
      navigate(next ? next : '/dashboard');
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
    'w-full rounded-xl bg-white/5 border pl-11 pr-4 py-2.5 text-slate-100 placeholder:text-slate-500 ' +
    'focus:outline-none focus:ring-2 transition disabled:opacity-60 disabled:cursor-not-allowed';

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
            <ShieldCheck className="w-6 h-6 text-white" strokeWidth={2.2} />
          </div>
          <h1 className="text-2xl font-display font-bold text-white">
            Welcome back to <span className="text-gradient">QA Platform</span>
          </h1>
          <p className="text-slate-400 text-sm mt-1.5">Sign in to continue testing</p>
        </div>

        {errors.general && (
          <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 text-red-300 px-4 py-3 rounded-xl mb-5 text-sm">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{errors.general}</span>
          </div>
        )}

        <form onSubmit={handleLogin} className="space-y-5">
          {/* Email */}
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1.5">Email</label>
            <div className="relative">
              <Mail className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input
                type="email"
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  if (errors.email) setErrors({ ...errors, email: '' });
                }}
                disabled={loading}
                className={`${inputBase} ${
                  errors.email
                    ? 'border-red-500/60 focus:ring-red-500/50'
                    : email && isEmailValid
                    ? 'border-brand-teal/60 focus:ring-brand-teal/50'
                    : email
                    ? 'border-red-500/60 focus:ring-red-500/50'
                    : 'border-white/10 focus:ring-brand-sky/60'
                }`}
                placeholder="your.email@example.com"
                required
              />
            </div>
            {errors.email ? (
              <p className="text-red-400 text-sm mt-1.5 flex items-center gap-1">
                <AlertCircle className="w-3.5 h-3.5" /> {errors.email}
                {errors.email.includes('No account') && (
                  <Link to="/register" className="ml-1 underline font-medium text-brand-sky">Register</Link>
                )}
              </p>
            ) : email && !isEmailValid ? (
              <p className="text-red-400 text-sm mt-1.5">Please enter a valid email</p>
            ) : null}
          </div>

          {/* Password */}
          <div>
            <label className="block text-slate-300 text-sm font-medium mb-1.5">Password</label>
            <div className="relative">
              <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                  if (errors.password) setErrors({ ...errors, password: '' });
                }}
                disabled={loading}
                className={`${inputBase} pr-11 ${
                  errors.password
                    ? 'border-red-500/60 focus:ring-red-500/50'
                    : 'border-white/10 focus:ring-brand-sky/60'
                }`}
                placeholder="Enter your password"
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
            {errors.password && (
              <p className="text-red-400 text-sm mt-1.5 flex items-center gap-1">
                <AlertCircle className="w-3.5 h-3.5" /> {errors.password}
              </p>
            )}
            <div className="text-right mt-2">
              <Link to="/forgot-password" className="text-sm text-slate-400 hover:text-brand-sky transition">
                Forgot password?
              </Link>
            </div>
          </div>

          {/* Submit */}
          <motion.button
            type="submit"
            disabled={!isValid || loading}
            whileTap={isValid && !loading ? { scale: 0.98 } : undefined}
            whileHover={isValid && !loading ? { scale: 1.01 } : undefined}
            className={`w-full py-3 rounded-xl font-semibold transition-all flex items-center justify-center gap-2 ${
              !isValid || loading
                ? 'bg-white/5 text-slate-500 border border-white/10 cursor-not-allowed'
                : 'bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal'
            }`}
          >
            {loading ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                Logging in...
              </>
            ) : (
              <>
                <LogIn className="w-5 h-5" />
                {isValid ? 'Login' : 'Fill in your credentials'}
              </>
            )}
          </motion.button>
        </form>

        <p className="mt-6 text-center text-slate-400 text-sm">
          Don&apos;t have an account?{' '}
          <Link to="/register" className="text-brand-sky hover:underline font-medium">Register</Link>
        </p>
      </motion.div>
    </div>
  );
};

export default Login;
