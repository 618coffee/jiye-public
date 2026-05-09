'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { Search, Settings, BookMarked } from 'lucide-react';
import { useState, useEffect, KeyboardEvent } from 'react';
import { cn } from '@/lib/utils';

export function TopBar() {
  const path = usePathname();
  const router = useRouter();
  const [q, setQ] = useState('');

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        document.getElementById('global-search')?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  function go(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && q.trim()) {
      router.push(`/search?q=${encodeURIComponent(q.trim())}`);
    }
  }

  return (
    <header className="sticky top-0 z-30 backdrop-blur-xl bg-white/70 border-b border-ink-200/60">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center gap-4">
        <Link href="/" className="flex items-center gap-2 font-semibold text-ink-900 group">
          <span className="inline-flex w-8 h-8 rounded-xl bg-gradient-to-br from-rouge-500 to-rouge-700 text-white items-center justify-center text-base shadow-soft transition-transform group-hover:rotate-[-6deg]">
            <BookMarked className="w-4 h-4" />
          </span>
          <span className="tracking-tight">小本本</span>
          <span className="text-ink-300 text-xs ml-1 hidden sm:inline">XHS-KB</span>
        </Link>

        <nav className="hidden md:flex items-center gap-1 ml-4">
          <NavLink href="/" active={path === '/'}>目的地</NavLink>
          <NavLink href="/inbox" active={path?.startsWith('/inbox') ?? false}>收件箱</NavLink>
          <NavLink href="/search" active={path?.startsWith('/search') ?? false}>搜索</NavLink>
          <NavLink href="/settings" active={path?.startsWith('/settings') ?? false}>设置</NavLink>
        </nav>

        <div className="flex-1" />

        <div className="relative w-full max-w-sm hidden sm:block">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-ink-400 pointer-events-none" />
          <input
            id="global-search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={go}
            placeholder="搜你的收藏…  ⌘K"
            className="h-9 w-full pl-9 pr-3 text-sm rounded-xl bg-white/80 border border-ink-200 placeholder:text-ink-400 focus:outline-none focus:border-rouge-400 focus:ring-4 focus:ring-rouge-500/10 transition-all"
          />
        </div>

        <Link
          href="/settings"
          className="md:hidden inline-flex items-center justify-center w-9 h-9 rounded-xl text-ink-500 hover:bg-ink-100"
        >
          <Settings className="w-4 h-4" />
        </Link>
      </div>
    </header>
  );
}

function NavLink({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className={cn(
        'px-3 h-9 inline-flex items-center text-sm rounded-lg transition-colors',
        active ? 'text-ink-900 bg-ink-100' : 'text-ink-500 hover:text-ink-800 hover:bg-ink-100/60',
      )}
    >
      {children}
    </Link>
  );
}
