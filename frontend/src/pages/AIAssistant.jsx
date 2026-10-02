import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bot, Send, AlertTriangle, MessageSquare, Loader2, Menu } from 'lucide-react';
import Sidebar from '../components/Sidebar';
import AmbientBackground from '../components/AmbientBackground';
import AuthGateBanner from '../components/AuthGateBanner';
import { useAuth } from '../contexts/useAuth';
import { useRequireAuth } from '../hooks/useRequireAuth';
import { chatWithAi, checkAiHealth } from '../api/ai';

const AIAssistant = () => {
    const navigate = useNavigate();
    const { isAuthenticated } = useAuth();
    const requireAuth = useRequireAuth();
    const [sidebarOpen, setSidebarOpen] = useState(false);
  const messagesEndRef = useRef(null);

  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: "Hi! I'm your QA testing assistant. I can help you understand bugs, suggest tests, explain technical issues in plain English, and answer questions about your projects. What would you like to know?"
    }
  ]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [includeContext, setIncludeContext] = useState(false);
  const [aiAvailable, setAiAvailable] = useState(true);
  const [aiCheckLoading, setAiCheckLoading] = useState(() => !!localStorage.getItem('token'));

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

    useEffect(() => {
      if (!localStorage.getItem('token')) {
        return;
      }

    const checkHealth = async () => {
      try {
        const res = await checkAiHealth();
        setAiAvailable(res.data.available);
      } catch {
        setAiAvailable(false);
      } finally {
        setAiCheckLoading(false);
      }
    };

    checkHealth();
  }, [navigate]);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = async (e) => {
      if (e) e.preventDefault();
      const trimmed = input.trim();
      if (!trimmed || sending) return;
      if (!requireAuth()) return;
  
    const newMessages = [...messages, { role: 'user', content: trimmed }];
    setMessages(newMessages);
    setInput('');
    setSending(true);

    try {
      const history = newMessages
        .slice(1, -1)
        .map((m) => ({ role: m.role, content: m.content }));

      const res = await chatWithAi(trimmed, history, includeContext);
      setMessages([...newMessages, { role: 'assistant', content: res.data.reply }]);
    } catch (_) {
      const errorMsg = _.response?.data?.error || 'AI failed to respond. Please try again.';
      setMessages([
        ...newMessages,
        { role: 'assistant', content: errorMsg, isError: true }
      ]);
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const clearChat = () => {
    if (!window.confirm('Clear chat history?')) return;
    setMessages([
      {
        role: 'assistant',
        content: "Chat cleared. How can I help you?"
      }
    ]);
  };

  const suggestedQuestions = [
    'How can I write better test cases?',
    'What is the difference between manual and automated testing?',
    'Explain SEO issues in simple terms',
    'What are security headers?',
  ];

  const applyQuestion = (q) => {
    setInput(q);
  };

  // Renders message text with basic markdown support (bold, code)
  const renderMessage = (content) => {
    const lines = content.split('\n');
    return lines.map((line, idx) => {
      const parts = [];
      let remaining = line;
      let key = 0;

      while (remaining.length > 0) {
        const boldMatch = remaining.match(/\*\*(.+?)\*\*/);
        const codeMatch = remaining.match(/`([^`]+)`/);

        let nextMatch = null;
        let nextType = null;
        if (boldMatch && (!codeMatch || boldMatch.index < codeMatch.index)) {
          nextMatch = boldMatch;
          nextType = 'bold';
        } else if (codeMatch) {
          nextMatch = codeMatch;
          nextType = 'code';
        }

        if (!nextMatch) {
          parts.push(<span key={key}>{remaining}</span>);
          break;
        }

        if (nextMatch.index > 0) {
          parts.push(<span key={key++}>{remaining.slice(0, nextMatch.index)}</span>);
        }

        if (nextType === 'bold') {
          parts.push(<strong key={key++}>{nextMatch[1]}</strong>);
        } else {
          parts.push(
            <code key={key++} className="bg-white/10 text-brand-teal px-1 py-0.5 rounded text-xs font-mono">
              {nextMatch[1]}
            </code>
          );
        }

        remaining = remaining.slice(nextMatch.index + nextMatch[0].length);
      }

  return (
        <div key={idx} className={line.trim() === '' ? 'h-2' : ''}>
          {parts}
        </div>
      );
    });
  };

return (
    <div className="relative flex h-screen overflow-hidden text-slate-200">
      <AmbientBackground />
      <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex-1 flex flex-col h-screen overflow-hidden lg:ml-64">
        {!isAuthenticated && (
          <div className="px-8 pt-4 max-w-7xl mx-auto w-full">
            <AuthGateBanner message="The assistant uses your project context. Sign in to ask it about your runs." />
          </div>
        )}
        {/* Header */}
        <div className="glass-strong border-b border-white/10 px-8 py-4 flex justify-between items-center gap-4 max-w-7xl mx-auto w-full">
          <div className="flex items-center gap-4">
            <button
              className="lg:hidden p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open menu"
            >
              <Menu className="w-6 h-6" />
            </button>
            <div>
              <h1 className="text-2xl font-display font-bold text-white flex items-center gap-2">
                <span className="grid place-items-center w-8 h-8 rounded-lg bg-brand-gradient shadow-glow">
                  <Bot className="w-4 h-4 text-white" />
                </span>
                AI Assistant
              </h1>
              <p className="text-sm text-slate-400 mt-0.5">Powered by Gemini · Ask anything about testing & QA</p>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={includeContext}
                onChange={(e) => setIncludeContext(e.target.checked)}
                className="w-4 h-4 accent-brand-indigo"
              />
              <span>Use my data for context</span>
            </label>

            <button onClick={clearChat} className="text-sm text-slate-400 hover:text-slate-200 transition">
              Clear chat
            </button>
          </div>
        </div>

        {/* AI status warning */}
        {!aiCheckLoading && !aiAvailable && (
          <div className="bg-amber-500/10 border-b border-amber-500/30 px-8 py-3 text-sm text-amber-200 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            AI service is not configured. Please check that your API key is set in the backend .env file.
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-8 py-6">
          <div className="max-w-3xl mx-auto space-y-4">
            {messages.map((msg, idx) => (
              <div key={idx} className={'flex ' + (msg.role === 'user' ? 'justify-end' : 'justify-start')}>
                <div
                  className={
                    'max-w-2xl rounded-2xl px-4 py-3 ' +
                    (msg.role === 'user'
                      ? 'bg-brand-gradient text-white shadow-glow'
                      : msg.isError
                      ? 'bg-red-500/10 border border-red-500/30 text-red-200'
                      : 'glass text-slate-200')
                  }
                >
                  {msg.role === 'assistant' && !msg.isError && (
                    <div className="text-xs font-semibold text-slate-400 mb-1 flex items-center gap-1">
                      <Bot className="w-3.5 h-3.5 text-brand-sky" /> AI Assistant
                    </div>
                  )}
                  {msg.isError && (
                    <div className="text-xs font-semibold text-red-300 mb-1 flex items-center gap-1">
                      <AlertTriangle className="w-3.5 h-3.5" /> Error
                    </div>
                  )}
                  <div className="text-sm leading-relaxed whitespace-pre-wrap break-words">
                    {renderMessage(msg.content)}
                  </div>
                </div>
              </div>
            ))}

            {sending && (
              <div className="flex justify-start">
                <div className="glass rounded-2xl px-4 py-3">
                  <div className="text-xs font-semibold text-slate-400 mb-1 flex items-center gap-1">
                    <Bot className="w-3.5 h-3.5 text-brand-sky" /> AI Assistant
                  </div>
                  <div className="flex gap-1">
                    <span className="w-2 h-2 bg-brand-sky rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></span>
                    <span className="w-2 h-2 bg-brand-sky rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></span>
                    <span className="w-2 h-2 bg-brand-sky rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></span>
                  </div>
                </div>
              </div>
            )}

            {/* Suggested questions (shown only at start) */}
            {messages.length === 1 && !sending && (
              <div className="mt-6">
                <p className="text-xs text-slate-500 mb-2 uppercase font-semibold tracking-wide">Try asking:</p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  {suggestedQuestions.map((q, i) => (
                    <button
                      key={i}
                      onClick={() => applyQuestion(q)}
                      className="flex items-center gap-2 text-left text-sm glass hover:bg-white/[0.08] hover:border-brand-indigo/40 rounded-xl px-4 py-3 transition"
                    >
                      <MessageSquare className="w-4 h-4 text-brand-sky shrink-0" /> {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input bar */}
        <div className="glass-strong border-t border-white/10 px-8 py-4">
          <form onSubmit={handleSend} className="max-w-3xl mx-auto">
            <div className="flex gap-3 items-end">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask anything about testing, bugs, or your platform..."
                rows="2"
                disabled={sending || !aiAvailable}
                className="flex-1 px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-brand-sky/60 resize-none disabled:opacity-60 disabled:cursor-not-allowed"
              />
              <button
                type="submit"
                disabled={!input.trim() || sending || !aiAvailable}
                className={
                  'px-6 py-3 rounded-xl font-medium transition flex items-center gap-2 ' +
                  (input.trim() && !sending && aiAvailable
                    ? 'bg-brand-gradient text-white shadow-glow hover:shadow-glow-teal'
                    : 'bg-white/5 text-slate-500 border border-white/10 cursor-not-allowed')
                }
              >
                {sending ? <><Loader2 className="w-4 h-4 animate-spin" /> Sending...</> : <><Send className="w-4 h-4" /> Send</>}
              </button>
            </div>
            <p className="text-xs text-slate-500 mt-2">Press Enter to send, Shift+Enter for new line</p>
          </form>
        </div>
      </div>
    </div>
  );
};

export default AIAssistant;
