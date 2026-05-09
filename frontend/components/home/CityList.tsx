'use client';

import Link from 'next/link';
import { motion } from 'framer-motion';
import { ArrowRight, AlertCircle, CheckCircle2 } from 'lucide-react';
import type { CitySummary } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { formatRelativeDate } from '@/lib/utils';

export function CityList({ cities }: { cities: CitySummary[] }) {
  if (!cities.length) {
    return (
      <Card className="p-12 text-center text-ink-500">
        还没有索引任何收藏。运行 <code className="px-2 py-1 bg-ink-100 rounded">scripts/import.sh</code> 导入数据。
      </Card>
    );
  }
  return (
    <section>
      <header className="flex items-end justify-between mb-4">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">目的地</h2>
          <p className="text-sm text-ink-500 mt-1">点击城市进入工作台，按需触发 POI 鲜活度刷新</p>
        </div>
      </header>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {cities.map((c, i) => (
          <motion.div
            key={c.city + c.country}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, delay: i * 0.04, ease: [0.2, 0.8, 0.2, 1] }}
          >
            <CityCard city={c} />
          </motion.div>
        ))}
      </div>
    </section>
  );
}

function CityCard({ city }: { city: CitySummary }) {
  const isStale = city.unverified > 0;
  return (
    <Link href={`/city/${encodeURIComponent(city.city)}`} className="block group">
      <Card className="p-5 card-lift overflow-hidden relative">
        <div
          className="absolute -top-10 -right-10 w-32 h-32 rounded-full opacity-25 group-hover:opacity-40 transition-opacity"
          style={{
            background: `radial-gradient(circle at center, ${gradientFor(city.city)}, transparent 70%)`,
          }}
        />
        <div className="relative">
          <div className="flex items-center gap-3">
            <span className="text-3xl leading-none">{city.flag}</span>
            <div className="flex-1 min-w-0">
              <div className="text-lg font-semibold tracking-tight truncate">{city.city}</div>
              <div className="text-xs text-ink-500">{city.country}</div>
            </div>
            <ArrowRight className="w-4 h-4 text-ink-300 group-hover:text-ink-700 group-hover:translate-x-0.5 transition-all" />
          </div>
          <div className="mt-4 flex items-center gap-2 text-sm text-ink-600">
            <span className="font-semibold text-ink-900">{city.note_count}</span> 条收藏
            <span className="text-ink-300">·</span>
            <span className="font-semibold text-ink-900">{city.poi_count}</span> 个 POI
          </div>
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            {isStale ? (
              <Badge tone="warn">
                <AlertCircle className="w-3 h-3" />
                {city.unverified} 个未验证
              </Badge>
            ) : (
              <Badge tone="good">
                <CheckCircle2 className="w-3 h-3" />
                已最新
              </Badge>
            )}
            <span className="text-xs text-ink-400 ml-auto">
              {formatRelativeDate(city.last_refreshed_at)}
            </span>
          </div>
        </div>
      </Card>
    </Link>
  );
}

function gradientFor(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360} 80% 70%)`;
}
