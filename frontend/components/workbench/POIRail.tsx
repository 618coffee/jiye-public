'use client';

import type { POI } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { freshnessLabel, formatRelativeDate } from '@/lib/utils';
import { motion } from 'framer-motion';

export function POIRail({ pois }: { pois: POI[] }) {
  return (
    <section>
      <header className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold tracking-tight">命中的 POI</h2>
        <span className="text-sm text-ink-400">{pois.length} 个</span>
      </header>
      <div className="flex gap-3 overflow-x-auto pb-2 -mx-4 px-4 sm:mx-0 sm:px-0 snap-x snap-mandatory">
        {pois.map((p, i) => {
          const f = freshnessLabel(p.freshness_status);
          return (
            <motion.div
              key={p.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.03 }}
              className="snap-start flex-shrink-0 w-64"
            >
              <Card className="p-4 card-lift h-full flex flex-col">
                <div className="flex items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-[15px] truncate">{p.canonical_name}</div>
                    <div className="text-xs text-ink-400 mt-0.5 capitalize">{p.poi_type || 'unknown'}</div>
                  </div>
                  <Badge tone={f.tone}>{f.text}</Badge>
                </div>
                {p.freshness_score != null && (
                  <div className="mt-3">
                    <div className="flex items-center justify-between text-xs text-ink-500 mb-1">
                      <span>鲜活度</span>
                      <span className="font-medium text-ink-700">{p.freshness_score}/100</span>
                    </div>
                    <div className="h-1.5 bg-ink-100 rounded-full overflow-hidden">
                      <motion.div
                        initial={{ width: 0 }}
                        animate={{ width: `${p.freshness_score}%` }}
                        transition={{ duration: 0.7, ease: [0.2, 0.8, 0.2, 1] }}
                        className={
                          p.freshness_score >= 75 ? 'h-full bg-emerald-500'
                          : p.freshness_score >= 45 ? 'h-full bg-amber-500'
                          : 'h-full bg-rose-500'
                        }
                      />
                    </div>
                  </div>
                )}
                <div className="mt-3 text-xs text-ink-400">
                  上次验证 · {formatRelativeDate(p.last_checked_at)}
                </div>
              </Card>
            </motion.div>
          );
        })}
      </div>
    </section>
  );
}
