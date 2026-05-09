import { cn } from '@/lib/utils';
import { HTMLAttributes } from 'react';

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      {...props}
      className={cn(
        'bg-white/80 backdrop-blur-sm border border-ink-200/70 rounded-2xl shadow-soft',
        className,
      )}
    />
  );
}
