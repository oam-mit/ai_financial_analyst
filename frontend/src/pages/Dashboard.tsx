import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Search, Send, TrendingUp, BookOpen, LogOut, Loader2, Menu, X, ChevronRight, History, RefreshCw, ArrowUpRight, ArrowDownRight, Music, Play } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import apiClient from '../api/client';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';

interface Message {
    role: 'user' | 'assistant';
    content: string;
    is_analysis?: boolean;
    isTyping?: boolean;
    highlights?: Highlight[];
}

interface Session {
    id: number;
    stock_symbol: string;
    messages: Message[];
    company_name?: string;
}

interface Suggestion {
    symbol: string;
    name: string;
}

interface Price {
    price: number | null;
    change_percent?: number;
}

interface Highlight {
    start: number;
    end: number;
    label: string;
    description: string;
}

const preprocessContent = (text: string) => {
    if (!text) return '';

    let processed = text.replace(/\\\$/g, '$');
    processed = processed.replace(/\$([0-9])/g, '___CUR_TOKEN___$1');
    processed = processed.replace(/\$\$(.*?)\$\$/gs, '\\[$1\\]');
    processed = processed.replace(/\$([^\n\$]+?)\$/g, '\\($1\\)');
    processed = processed.replace(/___CUR_TOKEN___/g, '\\$');

    return processed;
};

const TypewriterMarkdown = ({ content, isTyping, speed = 5, onTyped, onComplete }: { content: string, isTyping?: boolean, speed?: number, onTyped?: () => void, onComplete?: () => void }) => {
    const [displayedContent, setDisplayedContent] = useState(isTyping ? '' : content);

    useEffect(() => {
        if (!isTyping) {
            setDisplayedContent(content);
            return;
        }
        let i = 0;
        const timer = setInterval(() => {
            i += 10; // Yield multi-chunk typing - increased for better performance
            if (i >= content.length) {
                setDisplayedContent(content);
                clearInterval(timer);
                if (onComplete) onComplete();
            } else {
                setDisplayedContent(content.substring(0, i));
            }
            if (onTyped) onTyped();
        }, speed);
        return () => clearInterval(timer);
    }, [content, isTyping, speed, onTyped, onComplete]);

    return (
        <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkMath]}
            rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
            components={{
                table: ({ node, ...props }) => (
                    <div style={{ overflowX: 'auto', margin: '2rem 0', borderRadius: '1rem', border: '1px solid var(--glass-border)', background: 'rgba(0,0,0,0.2)', padding: '0.5rem' }}>
                        <table className="markdown-table" style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.9rem' }} {...props} />
                    </div>
                ),
                thead: ({ node, ...props }) => <thead {...props} style={{ background: 'rgba(0, 200, 5, 0.1)' }} />,
                th: ({ node, ...props }) => <th {...props} style={{ borderBottom: '2px solid var(--glass-border)', padding: '16px 32px', fontWeight: '700', color: 'var(--primary)', textAlign: 'left' }} />,
                td: ({ node, ...props }) => <td {...props} style={{ borderBottom: '1px solid var(--glass-border)', padding: '14px 32px', color: 'var(--text-main)' }} />,
                p: ({ node, ...props }) => <p style={{ marginBottom: '1rem' }} {...props} />,
                ul: ({ node, ...props }) => <ul style={{ paddingLeft: '1.5rem', marginBottom: '1.5rem', listStyleType: 'disc' }} {...props} />,
                ol: ({ node, ...props }) => <ol style={{ paddingLeft: '1.5rem', marginBottom: '1.5rem', listStyleType: 'decimal' }} {...props} />,
                li: ({ node, ...props }) => <li style={{ marginBottom: '0.5rem', paddingLeft: '0.25rem' }} {...props} />
            }}
        >
            {preprocessContent(displayedContent)}
        </ReactMarkdown>
    );
};

const Dashboard = () => {
    const { user, logout } = useAuth();
    const [symbol, setSymbol] = useState('');
    const [currentStock, setCurrentStock] = useState<string | null>(null);
    const [session, setSession] = useState<Session | null>(null);
    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [isAwaitingResponse, setIsAwaitingResponse] = useState(false);
    const [isAnalyzing, setIsAnalyzing] = useState(false);
    const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
    const [isFetchingSuggestions, setIsFetchingSuggestions] = useState(false);
    const [showSuggestions, setShowSuggestions] = useState(false);
    const [isDrawerOpen, setIsDrawerOpen] = useState(false);
    const [chatHistory, setChatHistory] = useState<Session[]>([]);
    const [isLoadingHistory, setIsLoadingHistory] = useState(false);
    const [currentPrice, setCurrentPrice] = useState<Price | null>(null);
    const [isRefreshingPrice, setIsRefreshingPrice] = useState(false);
    const [modelChoice, setModelChoice] = useState<'mistral' | 'gemini'>('mistral');
    const [highlights, setHighlights] = useState<Highlight[]>([]);
    const [activeAudio, setActiveAudio] = useState<string | null>(null);
    const [activeClip, setActiveClip] = useState<{ start: number, end: number } | null>(null);
    const [isHighlightsOpen, setIsHighlightsOpen] = useState(false);
    const chatEndRef = useRef<HTMLDivElement>(null);
    const suggestionRef = useRef<HTMLDivElement>(null);
    const audioRef = useRef<HTMLAudioElement>(null);

    const scrollToBottom = useCallback(() => {
        chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, []);

    const handleTypingComplete = useCallback((index: number) => {
        setMessages(prev => prev.map((m, i) => i === index ? { ...m, isTyping: false } : m));
    }, []);

    useEffect(() => {
        scrollToBottom();
    }, [messages, isAwaitingResponse, isAnalyzing]); // Scroll on new messages or loading state

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (suggestionRef.current && !suggestionRef.current.contains(event.target as Node)) {
                setShowSuggestions(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const fetchChatHistory = async () => {
        setIsLoadingHistory(true);
        try {
            const response = await apiClient.get('chats/');
            setChatHistory(response.data);
        } catch (err) {
            console.error('Failed to fetch chat history:', err);
        } finally {
            setIsLoadingHistory(false);
        }
    };

    useEffect(() => {
        if (isDrawerOpen) {
            fetchChatHistory();
        }
    }, [isDrawerOpen]);

    useEffect(() => {
        if (currentStock) {
            fetchPrice(currentStock);
        } else {
            setCurrentPrice(null);
        }
    }, [currentStock]);

    // Ensure price is fetched if session exists but price is null
    useEffect(() => {
        if (session && !currentPrice && !isRefreshingPrice) {
            fetchPrice(session.stock_symbol);
        }
    }, [session, currentPrice, isRefreshingPrice]);

    const fetchSuggestions = async (query: string) => {
        if (query.length < 2) {
            setSuggestions([]);
            return;
        }
        setIsFetchingSuggestions(true);
        try {
            const response = await apiClient.get(`stocks/search/?q=${query}`);
            setSuggestions(response.data);
            setShowSuggestions(true);
        } catch (err) {
            console.error(err);
        } finally {
            setIsFetchingSuggestions(false);
        }
    };

    // Simple debounce
    const debounceTimeout = useRef<NodeJS.Timeout | null>(null);
    const handleSymbolChange = (val: string) => {
        setSymbol(val.toUpperCase());
        if (debounceTimeout.current) clearTimeout(debounceTimeout.current);
        debounceTimeout.current = setTimeout(() => {
            fetchSuggestions(val);
        }, 300);
    };

    const handleSelectSuggestion = (suggestion: Suggestion) => {
        setSymbol(suggestion.symbol);
        setShowSuggestions(false);
        // Automatically trigger search
        startSearch(suggestion.symbol);
    };

    const fetchPrice = async (stockSymbol: string) => {
        try {
            const response = await apiClient.get(`stocks/${stockSymbol}/price/`);
            setCurrentPrice(response.data);
        } catch (err) {
            console.error('Failed to fetch price:', err);
        }
    };

    const handleRefreshPrice = async () => {
        if (!currentStock) return;
        setIsRefreshingPrice(true);
        await fetchPrice(currentStock);
        setIsRefreshingPrice(false);
    };

    const startSearch = async (stockSymbol: string) => {
        setIsLoading(true);
        try {
            const response = await apiClient.post('chats/start_or_get_latest/', { symbol: stockSymbol });
            setSession(response.data);
            const loadedMessages = response.data.messages || [];
            setMessages(loadedMessages);

            // Extract highlights from the latest analysis message if it exists
            const analysisMsg = [...loadedMessages].reverse().find(m => m.is_analysis && m.highlights);
            if (analysisMsg && analysisMsg.highlights) {
                setHighlights(analysisMsg.highlights);
            } else {
                setHighlights([]);
            }

            setCurrentStock(stockSymbol.toUpperCase());
            // fetchPrice is now handled by useEffect on currentStock
        } catch (err) {
            console.error(err);
        } finally {
            setIsLoading(false);
        }
    };

    const handleSearch = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!symbol) return;
        setShowSuggestions(false);
        startSearch(symbol);
    };

    const handleSendMessage = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!input || !session) return;

        const userMsg: Message = { role: 'user', content: input };
        setMessages(prev => [...prev, userMsg]);
        const currentInput = input;
        setInput('');

        setIsAwaitingResponse(true);
        try {
            const token = localStorage.getItem('access_token');
            const response = await fetch(`http://localhost:8000/api/chats/${session.id}/send_message/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({ content: currentInput, model_choice: modelChoice })
            });

            if (!response.ok) throw new Error('Request failed');
            const data = await response.json();

            const assistantMsg: Message = { role: 'assistant', content: data.content, isTyping: true };
            setMessages(prev => [...prev, assistantMsg]);
        } catch (err) {
            console.error(err);
        } finally {
            setIsAwaitingResponse(false);
        }
    };

    const handleAnalyze = async () => {
        if (!session) return;
        setIsAnalyzing(true);
        try {
            const token = localStorage.getItem('access_token');
            const response = await fetch(`http://localhost:8000/api/chats/${session.id}/analyze/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({ model_choice: modelChoice })
            });

            if (!response.ok) throw new Error('Analysis request failed');
            const data = await response.json();

            if (data.highlights) {
                setHighlights(data.highlights);
                setIsHighlightsOpen(true); // Open highlights panel automatically on new analysis
            }

            const assistantMsg: Message = {
                role: 'assistant',
                content: data.content,
                is_analysis: true,
                isTyping: true,
                highlights: data.highlights
            };
            setMessages(prev => [...prev, assistantMsg]);
        } catch (err) {
            console.error(err);
        } finally {
            setIsAnalyzing(false);
        }
    };



    const playAudioClip = (start: number, end: number) => {
        if (!currentStock) return;
        const url = `http://localhost:8000/api/stocks/${currentStock}/audio_clip/?start=${start}&end=${end}`;
        setActiveAudio(url);
        setActiveClip({ start, end });
        if (audioRef.current) {
            audioRef.current.load();
        }
    };

    return (
        <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
            {/* Header */}
            <header className="glass-card" style={{ margin: '1rem', padding: '0.75rem 1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', position: 'relative', zIndex: 1000 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                    <button
                        onClick={() => setIsDrawerOpen(true)}
                        style={{ background: 'transparent', color: 'var(--text-muted)', padding: '0.5rem', borderRadius: '0.5rem', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                        className="hover-effect"
                    >
                        <Menu size={24} />
                    </button>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <TrendingUp size={24} color="var(--primary)" />
                        <h1 style={{ fontSize: '1.25rem' }}>FinAI Analyst</h1>
                    </div>
                </div>

                <form onSubmit={handleSearch} style={{ flex: 1, maxWidth: '400px', display: 'flex', gap: '0.5rem', margin: '0 2rem' }}>
                    <div style={{ position: 'relative', flex: 1 }} ref={suggestionRef}>
                        <Search size={18} style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
                        <input
                            type="text"
                            placeholder="Search Name or Symbol..."
                            style={{ paddingLeft: '2.5rem' }}
                            value={symbol}
                            onChange={(e) => handleSymbolChange(e.target.value)}
                            onFocus={() => { if (suggestions.length > 0) setShowSuggestions(true); }}
                        />
                        {isFetchingSuggestions && (
                            <div style={{ position: 'absolute', right: '12px', top: '50%', transform: 'translateY(-50%)', display: 'flex', alignItems: 'center' }}>
                                <Loader2 className="animate-spin" size={16} style={{ color: 'var(--text-muted)' }} />
                            </div>
                        )}

                        {showSuggestions && suggestions.length > 0 && (
                            <div className="glass-card" style={{
                                position: 'absolute',
                                top: 'calc(100% + 8px)',
                                left: 0,
                                right: 0,
                                zIndex: 100,
                                maxHeight: '300px',
                                overflowY: 'auto',
                                padding: '0.5rem 0',
                                background: 'var(--bg-card)',
                                boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.3), 0 8px 10px -6px rgba(0, 0, 0, 0.3)',
                                border: '1px solid var(--glass-border)'
                            }}>
                                {suggestions.map((s, i) => (
                                    <div
                                        key={i}
                                        onClick={() => handleSelectSuggestion(s)}
                                        style={{
                                            padding: '0.75rem 1rem',
                                            cursor: 'pointer',
                                            borderBottom: i === suggestions.length - 1 ? 'none' : '1px solid var(--glass-border)',
                                            transition: 'background 0.2s'
                                        }}
                                        className="suggestion-item"
                                    >
                                        <div style={{ fontWeight: '600', color: 'var(--primary)', fontSize: '0.9rem' }}>{s.symbol}</div>
                                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                    <button type="submit" className="btn-primary" disabled={isLoading}>
                        {isLoading ? <Loader2 className="animate-spin" size={20} /> : 'Search'}
                    </button>
                    <select
                        value={modelChoice}
                        onChange={(e) => setModelChoice(e.target.value as 'mistral' | 'gemini')}
                        style={{ padding: '0.5rem', borderRadius: '0.5rem', background: 'var(--bg-card)', color: 'var(--text-main)', border: '1px solid var(--glass-border)' }}
                    >
                        <option value="mistral">Mistral (Default)</option>
                        <option value="gemini">Google Gemini</option>
                    </select>
                </form>

                <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                    <span style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>{user?.username}</span>
                    <button onClick={logout} style={{ background: 'transparent', color: 'var(--text-muted)' }}>
                        <LogOut size={20} />
                    </button>
                </div>
            </header>

            {/* Main Content */}
            <main style={{ flex: 1, display: 'flex', gap: '1rem', padding: '0 1rem 1rem 1rem', overflow: 'hidden' }}>
                {session ? (
                    /* Chat Section */
                    <section className="glass-card animate-fade-in" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                        <div style={{ padding: '1rem', borderBottom: '1px solid var(--glass-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                                <div style={{ background: 'var(--primary)', padding: '0.5rem', borderRadius: '0.5rem', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                    <TrendingUp size={18} color="white" />
                                </div>
                                <div>
                                    <h3 style={{ fontSize: '1.1rem' }}>Analysis: <span style={{ color: 'var(--primary)' }}>{session.stock_symbol}</span></h3>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.25rem', minHeight: '24px' }}>
                                        {currentPrice && currentPrice.price !== null ? (
                                            <>
                                                <span style={{ fontSize: '1.1rem', fontWeight: '700', color: 'var(--text-main)' }}>
                                                    ${currentPrice.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                                </span>
                                                {currentPrice.change_percent !== undefined && (
                                                    <div style={{
                                                        display: 'flex',
                                                        alignItems: 'center',
                                                        gap: '2px',
                                                        fontSize: '0.85rem',
                                                        fontWeight: '600',
                                                        padding: '2px 6px',
                                                        borderRadius: '4px',
                                                        background: currentPrice.change_percent >= 0 ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                                                        color: currentPrice.change_percent >= 0 ? '#4ade80' : '#ef4444'
                                                    }}>
                                                        {currentPrice.change_percent >= 0 ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
                                                        {Math.abs(currentPrice.change_percent).toFixed(2)}%
                                                    </div>
                                                )}
                                            </>
                                        ) : (
                                            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                                                {isRefreshingPrice ? 'Fetching price...' : 'Price N/A'}
                                            </span>
                                        )}
                                        <button
                                            onClick={handleRefreshPrice}
                                            disabled={isRefreshingPrice}
                                            style={{ background: 'transparent', color: 'var(--text-muted)', padding: '2px', borderRadius: '4px', display: 'flex', alignItems: 'center', marginLeft: '0.25rem' }}
                                            className="hover-effect"
                                            title="Refresh Price"
                                        >
                                            <RefreshCw size={14} className={isRefreshingPrice ? 'animate-spin' : ''} />
                                        </button>
                                    </div>
                                </div>
                            </div>
                            <button
                                className="btn-primary"
                                style={{ fontSize: '0.75rem', padding: '0.6rem 1.25rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}
                                onClick={handleAnalyze}
                                disabled={isAnalyzing}
                            >
                                {isAnalyzing ? <Loader2 className="animate-spin" size={16} /> : <BookOpen size={16} />}
                                {isAnalyzing ? 'Deep Dive Analysis...' : 'New Deep Dive Analysis'}
                            </button>
                        </div>

                        <div style={{ flex: 1, overflowY: 'auto', padding: '2rem', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                            {messages.length === 0 && !isLoading && (
                                <div style={{ textAlign: 'center', marginTop: '4rem', color: 'var(--text-muted)' }}>
                                    <div className="glass-card" style={{ display: 'inline-flex', padding: '2rem', marginBottom: '1.5rem' }}>
                                        <BookOpen size={48} style={{ opacity: 0.5, color: 'var(--primary)' }} />
                                    </div>
                                    <p style={{ fontSize: '1.1rem' }}>Start by asking a question about <span style={{ color: 'var(--primary)', fontWeight: '600' }}>{session.stock_symbol}</span></p>
                                    <p style={{ fontSize: '0.9rem', marginTop: '0.5rem', opacity: 0.7 }}>Or use the Deep Dive button above for a comprehensive report.</p>
                                </div>
                            )}
                            {messages.map((m, i) => (
                                <div key={i} style={{
                                    alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                                    maxWidth: '85%',
                                    background: m.role === 'user' ? 'var(--primary)' : 'rgba(255,255,255,0.03)',
                                    padding: '1.25rem 1.75rem',
                                    borderRadius: '1.25rem',
                                    border: m.role === 'user' ? 'none' : '1px solid var(--glass-border)',
                                    boxShadow: m.role === 'user' ? '0 8px 20px rgba(0, 200, 5, 0.15)' : 'none',
                                    position: 'relative',
                                    marginLeft: m.role === 'assistant' ? '0.5rem' : '0',
                                    marginRight: m.role === 'user' ? '0.5rem' : '0'
                                }}>
                                    <div style={{ fontSize: '0.95rem', lineHeight: '1.6' }}>
                                        {m.is_analysis && (
                                            <div style={{ marginBottom: '1rem', paddingBottom: '0.5rem', borderBottom: '1px solid var(--glass-border)', fontSize: '0.8rem', fontWeight: '600', color: 'var(--primary)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                                <TrendingUp size={14} />
                                                DEEP DIVE REPORT
                                            </div>
                                        )}
                                        <TypewriterMarkdown
                                            content={m.content}
                                            isTyping={m.isTyping}
                                            speed={m.is_analysis ? 2 : 10}
                                            onTyped={scrollToBottom}
                                            onComplete={() => handleTypingComplete(i)}
                                        />
                                        {m.is_analysis && m.highlights && m.highlights.length > 0 && !m.isTyping && (
                                            <div style={{ marginTop: '1.5rem', display: 'flex', justifyContent: 'flex-start' }}>
                                                <button
                                                    onClick={() => {
                                                        setHighlights(m.highlights || []);
                                                        setIsHighlightsOpen(true);
                                                    }}
                                                    className="btn-primary"
                                                    style={{
                                                        background: 'rgba(0, 200, 5, 0.15)',
                                                        color: 'var(--primary)',
                                                        border: '1px solid var(--primary)',
                                                        fontSize: '0.85rem',
                                                        padding: '0.5rem 1rem',
                                                        display: 'flex',
                                                        alignItems: 'center',
                                                        gap: '0.5rem'
                                                    }}
                                                >
                                                    <Music size={16} />
                                                    View Transcript Highlights
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            ))}
                            {(isAwaitingResponse || isAnalyzing) && (
                                <div style={{
                                    alignSelf: 'flex-start',
                                    padding: '1rem 1.75rem',
                                    borderRadius: '1.25rem',
                                    border: '1px solid var(--glass-border)',
                                    background: 'rgba(255,255,255,0.03)',
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: '0.75rem',
                                    color: 'var(--text-muted)'
                                }}>
                                    <Loader2 size={16} className="animate-spin" color="var(--primary)" />
                                    <span style={{ fontSize: '0.9rem' }}>{isAnalyzing ? 'Conducting Deep Dive Analysis...' : 'Model is thinking...'}</span>
                                </div>
                            )}
                            <div ref={chatEndRef} />
                        </div>

                        <form onSubmit={handleSendMessage} style={{ padding: '1.25rem', borderTop: '1px solid var(--glass-border)', display: 'flex', gap: '0.75rem', background: 'rgba(255,255,255,0.02)' }}>
                            <input
                                type="text"
                                placeholder={`Ask anything about ${session.stock_symbol}...`}
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                style={{ borderRadius: '0.75rem', background: 'rgba(255,255,255,0.05)' }}
                            />
                            <button type="submit" className="btn-primary" style={{ padding: '0.75rem', borderRadius: '0.75rem' }}>
                                <Send size={20} />
                            </button>
                        </form>
                    </section>
                ) : null}

                {/* Highlights Sidebar */}
                {session && highlights.length > 0 && isHighlightsOpen && (
                    <section className="glass-card animate-fade-in" style={{ width: '380px', display: 'flex', flexDirection: 'column', overflow: 'hidden', borderLeft: '1px solid var(--glass-border)' }}>
                        <div style={{ padding: '1.25rem', borderBottom: '1px solid var(--glass-border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                                <div style={{ background: 'var(--primary)', padding: '0.5rem', borderRadius: '0.5rem', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                    <Music size={18} color="white" />
                                </div>
                                <div>
                                    <h3 style={{ fontSize: '1.1rem', fontWeight: '700' }}>Transcript Highlights</h3>
                                    <p style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Something to keep in mind</p>
                                </div>
                            </div>
                            <button
                                onClick={() => setIsHighlightsOpen(false)}
                                style={{ background: 'transparent', color: 'var(--text-muted)', padding: '0.5rem' }}
                                className="hover-effect"
                            >
                                <X size={20} />
                            </button>
                        </div>
                        <div style={{ flex: 1, overflowY: 'auto', padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
                            {highlights.map((h, i) => (
                                <div key={i} className="glass-card" style={{ padding: '1.25rem', borderLeft: '4px solid var(--primary)', background: 'rgba(0, 200, 5, 0.03)', transition: 'transform 0.2s' }}>
                                    <div style={{ fontWeight: '700', color: 'var(--primary)', marginBottom: '0.5rem', fontSize: '0.95rem' }}>{h.label}</div>
                                    <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '1rem', lineHeight: '1.5' }}>{h.description}</p>
                                    <button
                                        className="btn-primary"
                                        style={{ width: '100%', fontSize: '0.8rem', padding: '0.6rem', background: 'rgba(0, 200, 5, 0.1)', color: 'var(--primary)', border: '1px solid rgba(0, 200, 5, 0.2)' }}
                                        onClick={() => playAudioClip(h.start, h.end)}
                                    >
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem' }}>
                                            <Play size={14} />
                                            Listen ({Math.round(h.end - h.start)}s)
                                        </div>
                                    </button>
                                </div>
                            ))}
                        </div>
                        {activeAudio && (
                            <div style={{ padding: '1rem', borderTop: '1px solid var(--glass-border)' }}>
                                <audio
                                    ref={audioRef}
                                    controls
                                    autoPlay
                                    style={{ width: '100%', height: '32px' }}
                                    onLoadedMetadata={(e) => {
                                        if (activeClip) {
                                            e.currentTarget.currentTime = activeClip.start;
                                        }
                                    }}
                                    onTimeUpdate={(e) => {
                                        if (activeClip && e.currentTarget.currentTime >= activeClip.end) {
                                            e.currentTarget.pause();
                                        }
                                    }}
                                >
                                    <source src={activeAudio} type="audio/mpeg" />
                                </audio>
                            </div>
                        )}
                    </section>
                )}

                {/* Hero Banner (if no session and not loading) */}
                {!session && !isLoading && (
                    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}>
                        <div className="glass-card animate-fade-in" style={{
                            padding: '4rem 2rem',
                            textAlign: 'center',
                            maxWidth: '700px',
                            width: '100%',
                            display: 'flex',
                            flexDirection: 'column',
                            alignItems: 'center',
                            gap: '2.5rem',
                            background: 'radial-gradient(circle at top right, rgba(0, 200, 5, 0.05), transparent), var(--glass)',
                            border: '1px solid var(--glass-border)',
                            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)'
                        }}>
                            <div style={{ position: 'relative' }}>
                                <div style={{
                                    position: 'absolute',
                                    inset: '-20px',
                                    background: 'var(--primary)',
                                    borderRadius: '50%',
                                    filter: 'blur(40px)',
                                    opacity: 0.15,
                                    zIndex: 0
                                }}></div>
                                <div style={{
                                    background: 'linear-gradient(135deg, var(--primary), var(--accent))',
                                    padding: '1.5rem',
                                    borderRadius: '1.5rem',
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    position: 'relative',
                                    zIndex: 1,
                                    boxShadow: '0 10px 20px rgba(0, 200, 5, 0.2)'
                                }}>
                                    <TrendingUp size={64} color="white" />
                                </div>
                            </div>

                            <div>
                                <h1 style={{
                                    fontSize: '3.5rem',
                                    marginBottom: '1rem',
                                    background: 'linear-gradient(to bottom, #fff, #94a3b8)',
                                    WebkitBackgroundClip: 'text',
                                    WebkitTextFillColor: 'transparent',
                                    fontWeight: '800',
                                    letterSpacing: '-0.02em'
                                }}>
                                    AI Financial Hub
                                </h1>
                                <p style={{
                                    fontSize: '1.25rem',
                                    color: 'var(--text-muted)',
                                    lineHeight: '1.6',
                                    maxWidth: '500px',
                                    margin: '0 auto'
                                }}>
                                    Select a company ticker to view real-time analysis and DCF calculations.
                                </p>
                            </div>

                            <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap', justifyContent: 'center' }}>
                                {[
                                    { label: 'Real-time Analysis', icon: Search },
                                    { label: 'DCF Calculation', icon: TrendingUp },
                                    { label: 'SEC Insights', icon: BookOpen }
                                ].map((item, idx) => (
                                    <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: 'var(--text-muted)' }}>
                                        <item.icon size={18} style={{ color: 'var(--primary)' }} />
                                        <span style={{ fontSize: '0.9rem', fontWeight: '500' }}>{item.label}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                )}

                {/* Loading State (if no session and loading) */}
                {!session && isLoading && (
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}>
                        <div className="glass-card animate-fade-in" style={{
                            padding: '3rem',
                            display: 'flex',
                            flexDirection: 'column',
                            alignItems: 'center',
                            gap: '1.5rem',
                            background: 'var(--bg-card)',
                            boxShadow: '0 10px 30px rgba(0,0,0,0.5)',
                            textAlign: 'center'
                        }}>
                            <Loader2 className="animate-spin" size={48} color="var(--primary)" />
                            <div>
                                <h2 style={{ fontSize: '1.5rem', color: 'var(--text-main)', marginBottom: '0.5rem' }}>Initializing Analysis</h2>
                                <p style={{ color: 'var(--text-muted)' }}>Fetching SEC filings and market data for {symbol}...</p>
                            </div>
                        </div>
                    </div>
                )}
            </main>
            {/* Sidebar Drawer Overlay */}
            {isDrawerOpen && (
                <div
                    onClick={() => setIsDrawerOpen(false)}
                    style={{
                        position: 'fixed',
                        top: 0,
                        left: 0,
                        right: 0,
                        bottom: 0,
                        background: 'rgba(0,0,0,0.5)',
                        backdropFilter: 'blur(4px)',
                        zIndex: 2000,
                        transition: 'opacity 0.3s'
                    }}
                />
            )}

            {/* Sidebar Drawer Content */}
            <aside
                style={{
                    position: 'fixed',
                    top: 0,
                    left: 0,
                    bottom: 0,
                    width: '320px',
                    background: 'var(--bg-dark)',
                    borderRight: '1px solid var(--glass-border)',
                    boxShadow: '10px 0 25px rgba(0,0,0,0.5)',
                    transform: isDrawerOpen ? 'translateX(0)' : 'translateX(-100%)',
                    transition: 'transform 0.3s ease-in-out',
                    zIndex: 2001,
                    display: 'flex',
                    flexDirection: 'column'
                }}
            >
                <div style={{ padding: '1.5rem', borderBottom: '1px solid var(--glass-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                        <History size={20} color="var(--primary)" />
                        <h2 style={{ fontSize: '1.1rem', fontWeight: '600' }}>Analysis History</h2>
                    </div>
                    <button onClick={() => setIsDrawerOpen(false)} style={{ background: 'transparent', color: 'var(--text-muted)' }}>
                        <X size={20} />
                    </button>
                </div>

                <div style={{ flex: 1, overflowY: 'auto', padding: '1rem' }}>
                    {isLoadingHistory ? (
                        <div style={{ display: 'flex', justifyContent: 'center', padding: '2rem' }}>
                            <Loader2 className="animate-spin" size={24} color="var(--primary)" />
                        </div>
                    ) : chatHistory.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
                            <p style={{ fontSize: '0.9rem' }}>No stocks analyzed yet.</p>
                        </div>
                    ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                            {chatHistory.map((sess) => (
                                <div
                                    key={sess.id}
                                    onClick={() => {
                                        startSearch(sess.stock_symbol);
                                        setIsDrawerOpen(false);
                                    }}
                                    style={{
                                        padding: '1rem',
                                        borderRadius: '0.75rem',
                                        background: currentStock === sess.stock_symbol ? 'rgba(0, 200, 5, 0.1)' : 'transparent',
                                        border: currentStock === sess.stock_symbol ? '1px solid var(--primary)' : '1px solid transparent',
                                        cursor: 'pointer',
                                        transition: 'all 0.2s',
                                        display: 'flex',
                                        justifyContent: 'space-between',
                                        alignItems: 'center'
                                    }}
                                    className="history-item"
                                >
                                    <div style={{ overflow: 'hidden' }}>
                                        <div style={{ fontWeight: '600', color: 'var(--primary)', fontSize: '0.95rem' }}>{sess.stock_symbol}</div>
                                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                            {sess.company_name || 'Stock Analysis'}
                                        </div>
                                    </div>
                                    <ChevronRight size={16} color="var(--text-muted)" />
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                <div style={{ padding: '1rem', borderTop: '1px solid var(--glass-border)' }}>
                    <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textAlign: 'center' }}>
                        Last updated: {new Date().toLocaleTimeString()}
                    </div>
                </div>
            </aside>
        </div>
    );
};

export default Dashboard;
