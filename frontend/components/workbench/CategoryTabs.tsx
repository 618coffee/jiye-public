'use client';

import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import type { CategoryGroup } from '@/lib/api';

interface Props {
  categories: CategoryGroup[];
  totalNotes: number;
  active: string;
  onSelect: (key: string) => void;
}

export function CategoryTabs({ categories, totalNotes, active, onSelect }: Props) {
  const items = [
    { key: 'all', label: '全部', icon: '🗂️', count: totalNotes },
    ...categories.filter((c) => c.count > 0),
  ];
  return (
    <div className="mt-6 flex flex-wrap gap-2">
      {items.map((it) => {
        const isActive = active === it.key;
        return (
          <button
            key={it.key}
            onClick={() => onSelect(it.key)}
            className={cn(
              'relative px-3 h-9 rounded-xl text-sm transition-colors flex items-center gap-1.5',
              isActive ? 'text-ink-900' : 'text-ink-500 hover:text-ink-800',
            )}
          >
            {isActive && (
              <motion.span
                layoutId="cat-pill"
                className="absolute inset-0 rounded-xl bg-ink-900/95 -z-10"
                transition={{ type: 'spring', stiffness: 400, damping: 32 }}
              />
            )}
            <span className={cn(isActive && 'text-white relative')}>{it.icon}</span>
            <span className={cn('font-medium', isActive && 'text-white relative')}>{it.label}</span>
            <span className={cn(
              'inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5 rounded-md text-xs',
              isActive ? 'bg-white/20 text-white relative' : 'bg-ink-100 text-ink-600',
            )}>
              {it.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
