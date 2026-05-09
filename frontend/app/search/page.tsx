'use client';

import { useEffect, useState, useTransition } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import { Search, Sparkles } from 'lucide-react';
import { api, type SearchResponse } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { NoteCard } from '@/components/workbench/NoteCard';

const SUGGESTIONS = [
  '京都拉面 必点',
  '上海安福路咖啡',
  '人均200以内有露台',
  '出片机位 黄昏',
  '杭州龙井茶',
  '东京寿司',
];

export default function SearchPage() {
  const params = useSearchParams();
  const router = useRouter();
  const initial = params.get('q') ?? '';
  const [q, setQ] = useState(initial);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!initial) return;
    runSearch(initial);
  }, [initial]);

  function runSearch(query: string) {
    setError(null);
    startTransition(async () => {
      try {
        const r = await api.search(query);
        setData(r);
      } catch (e: any) {
        setError(e.message || '查询失败');
      }
    });
  }

  function submit(value?: string) {
    const v = (value ?? q).trim();
    if (!v) return;
    router.push(`/search?q=${encodeURIComponent(v)}`);
    setQ(v);
    runSearch(v);
  }

  return (
    <div className="space-y-8">
      <section className="rounded-3xl bg-white/80 backdrop-blur border border-ink-200/70 shadow-soft p-6 sm:p-10">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-center gap-2 mb-4 text-rouge-600">
            <Sparkles className="w-4 h-4" />
            <span className="text-xs font-medium tracking-wide uppercase">AI 自然语言搜索</span>
          </div>
          <div className="relative">
            <Search className="w-5 h-5 absolute left-4 top-1/2 -translate-y-1/2 text-ink-400" />
            <Input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
              placeholder="比如：京都吃过最好的拉面、出片的咖啡店…"
              className="pl-11 h-14 text-base"
            />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => submit(s)}
                className="text-xs px-3 h-7 inline-flex items-center rounded-full bg-ink-100 text-ink-600 hover:bg-ink-200 transition-colors"
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </section>

      <AnimatePresence mode="wait">
        {pending && (
          <motion.div key="loading" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}>
            <Card className="p-6">
              <div className="space-y-2">
                <div className="skeleton h-6 w-1/3" />
                <div className="skeleton h-4 w-2/3" />
              </div>
            </Card>
          </motion.div>
        )}

        {!pending && data && (
          <motion.section key={data.query} initial={{opacity:0,y:6}} animate={{opacity:1,y:0}} exit={{opacity:0}} className="space-y-4">
            {data.explanation && (
              <Card className="p-4 border-rouge-200/60 bg-gradient-to-br from-rouge-50 to-white">
                <p className="text-sm text-ink-700 leading-relaxed">
                  <span className="font-medium text-rouge-700">AI</span> · {data.explanation}
                </p>
              </Card>
            )}

            {data.hits.length === 0 ? (
              <Card className="p-12 text-center text-ink-500">没找到匹配的收藏。</Card>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {data.hits.map((h, i) => (
                  <motion.div
                    key={h.note.id}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.03 }}
                  >
                    <NoteCard note={h.note} />
                  </motion.div>
                ))}
              </div>
            )}
          </motion.section>
        )}

        {error && (
          <Card className="p-6 border-rose-200 bg-rose-50 text-rose-700 text-sm">
            ⚠️ {error}
          </Card>
        )}
      </AnimatePresence>
    </div>
  );
}
