'use client';

import Link from 'next/link';
import { motion } from 'framer-motion';
import { Flame, Snowflake, UtensilsCrossed, AlertTriangle } from 'lucide-react';
import type { Note, WorkbenchClusters, ClusterItem } from '@/lib/api';

interface Props {
  clusters: WorkbenchClusters;
  notes: Note[];
}

const SECTION_META: Record<keyof WorkbenchClusters, { title: string; icon: React.ComponentType<{ className?: string }>; tone: string; ring: string; chip: string }> = {
  hot: {
    title: '🔥 当下值得',
    icon: Flame,
    tone: 'from-rouge-100 to-rouge-50',
    ring: 'border-rouge-300/70',
    chip: 'bg-rouge-100 text-rouge-700',
  },
  dish_radar: {
    title: '🍱 口味雷达',
    icon: UtensilsCrossed,
    tone: 'from-amber-100 to-amber-50',
    ring: 'border-amber-300/70',
    chip: 'bg-amber-100 text-amber-700',
  },
  cold: {
    title: '🌙 沉睡收藏',
    icon: Snowflake,
    tone: 'from-sky-100 to-sky-50',
    ring: 'border-sky-300/70',
    chip: 'bg-sky-100 text-sky-700',
  },
  blind_spots: {
    title: '⚠️ 可能塌房',
    icon: AlertTriangle,
    tone: 'from-stone-200 to-stone-50',
    ring: 'border-stone-300/70',
    chip: 'bg-stone-200 text-stone-700',
  },
};

export function ClusterRail({ clusters, notes }: Props) {
  const noteById = new Map(notes.map((n) => [n.id, n]));
  const order: (keyof WorkbenchClusters)[] = ['hot', 'dish_radar', 'cold', 'blind_spots'];
  const visible = order.filter((k) => (clusters[k]?.length || 0) > 0);
  if (visible.length === 0) return null;

  return (
    <section>
      <header className="mb-3 flex items-baseline justify-between">
        <h2 className="text-lg font-semibold tracking-tight">AI 工作台</h2>
        <span className="text-xs text-ink-400">基于你的收藏自动聚类 · 缓存 24h</span>
      </header>
      <div className="grid gap-4 sm:grid-cols-2">
        {visible.map((key) => {
          const meta = SECTION_META[key];
          const Icon = meta.icon;
          const items = clusters[key];
          return (
            <motion.div
              key={key}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3 }}
              className={`rounded-2xl border ${meta.ring} bg-gradient-to-br ${meta.tone} p-4 sm:p-5 backdrop-blur-sm`}
            >
              <div className="mb-3 flex items-center gap-2">
                <Icon className="h-4 w-4 text-ink-700" />
                <h3 className="text-sm font-semibold tracking-tight text-ink-800">{meta.title}</h3>
                <span className="ml-auto text-xs text-ink-500">{items.length} 簇</span>
              </div>
              <div className="space-y-3">
                {items.map((c: ClusterItem, idx: number) => (
                  <div key={idx} className="rounded-xl bg-white/70 p-3 ring-1 ring-white/60">
                    <div className="text-sm font-medium text-ink-900">{c.label || '（未命名）'}</div>
                    {c.reason && (
                      <div className="mt-0.5 text-xs text-ink-500">{c.reason}</div>
                    )}
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {c.note_ids.map((id) => {
                        const n = noteById.get(id);
                        if (!n) return null;
                        return (
                          <Link
                            key={id}
                            href={`/note/${id}`}
                            className={`inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${meta.chip} hover:opacity-80 transition`}
                            title={n.title}
                          >
                            <span className="truncate max-w-[14rem]">{n.title}</span>
                          </Link>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </motion.div>
          );
        })}
      </div>
    </section>
  );
}
