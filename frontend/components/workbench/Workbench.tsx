'use client';

import { useState, useTransition } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import Link from 'next/link';
import { RefreshCw, ArrowLeft } from 'lucide-react';
import type { WorkbenchData, POI, FreshnessRefreshResponse } from '@/lib/api';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { CategoryTabs } from './CategoryTabs';
import { NoteCard } from './NoteCard';
import { POIRail } from './POIRail';
import { ClusterRail } from './ClusterRail';

interface Props {
  data: WorkbenchData;
}

export function Workbench({ data }: Props) {
  const [active, setActive] = useState<string>('all');
  const [pois, setPois] = useState<POI[]>(data.pois);
  const [refreshedAt, setRefreshedAt] = useState<number | null>(null);
  const [pending, startTransition] = useTransition();

  const filteredNotes = active === 'all'
    ? data.notes
    : data.notes.filter((n) => n.trip_purpose.includes(active));

  function refresh() {
    startTransition(async () => {
      try {
        const r: FreshnessRefreshResponse = await api.refresh(data.city);
        setPois(r.pois);
        setRefreshedAt(Date.now());
      } catch (e) {
        console.error(e);
      }
    });
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <header className="relative overflow-hidden rounded-3xl border border-ink-200/70 bg-white/80 backdrop-blur shadow-soft">
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.5 }}
          className="absolute -top-20 -right-20 w-72 h-72 rounded-full bg-gradient-to-br from-rouge-200 to-rouge-500 blur-3xl opacity-25 pointer-events-none"
        />
        <div className="relative p-6 sm:p-8">
          <Link href="/" className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-ink-800 transition-colors">
            <ArrowLeft className="w-3.5 h-3.5" />
            所有目的地
          </Link>
          <div className="mt-3 flex flex-wrap items-end gap-x-6 gap-y-2">
            <h1 className="text-3xl sm:text-4xl font-semibold tracking-tight flex items-center gap-3">
              <span className="text-4xl sm:text-5xl">{data.flag}</span>
              {data.city}
            </h1>
            <p className="text-ink-500 text-sm sm:text-base">
              你收藏过 <strong className="text-ink-900">{data.notes.length}</strong> 条
              · <strong className="text-ink-900">{pois.length}</strong> 个 POI
              · <strong className="text-emerald-700">{pois.filter(p => p.freshness_status === 'alive').length}</strong> 已验证
            </p>
            <div className="flex-1" />
            <Button onClick={refresh} disabled={pending} variant="primary" size="md">
              <RefreshCw className={pending ? 'w-4 h-4 animate-spin' : 'w-4 h-4'} />
              {pending ? '验证中…' : '刷新该城市鲜活度'}
            </Button>
          </div>
          <AnimatePresence>
            {refreshedAt && (
              <motion.div
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className="mt-3"
              >
                <Badge tone="accent">刚刚刷新完成 ✓</Badge>
              </motion.div>
            )}
          </AnimatePresence>
          <CategoryTabs
            categories={data.categories}
            totalNotes={data.notes.length}
            active={active}
            onSelect={setActive}
          />
        </div>
      </header>

      {/* POI rail (with freshness badges) */}
      {pois.length > 0 && <POIRail pois={pois} />}

      {/* AI clusters */}
      {data.clusters && <ClusterRail clusters={data.clusters} notes={data.notes} />}

      {/* Notes grid */}
      <section>
        <header className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold tracking-tight">
            收藏 {active !== 'all' && (
              <span className="text-ink-400 font-normal">
                · {data.categories.find(c => c.key === active)?.label}
              </span>
            )}
          </h2>
          <span className="text-sm text-ink-400">{filteredNotes.length} 条</span>
        </header>

        <AnimatePresence mode="popLayout">
          {filteredNotes.length === 0 ? (
            <motion.div
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="rounded-2xl border border-dashed border-ink-200 p-12 text-center text-ink-500"
            >
              这个分类暂时没有收藏。
            </motion.div>
          ) : (
            <motion.div
              key={active}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"
            >
              {filteredNotes.map((n, i) => (
                <motion.div
                  key={n.id}
                  layout
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.03, ease: [0.2, 0.8, 0.2, 1] }}
                >
                  <NoteCard note={n} />
                </motion.div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>
      </section>
    </div>
  );
}
