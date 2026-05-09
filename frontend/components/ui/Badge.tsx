import { cn } from '@/lib/utils';
import { HTMLAttributes } from 'react';

type Tone = 'good' | 'warn' | 'bad' | 'idle' | 'accent';

interface Props extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

const toneClasses: Record<Tone, string> = {
  good:   'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  warn:   'bg-amber-50  text-amber-700  ring-amber-600/20',
  bad:    'bg-rose-50   text-rose-700   ring-rose-600/20',
  idle:   'bg-ink-100   text-ink-600    ring-ink-300/40',
  accent: 'bg-rouge-50  text-rouge-700  ring-rouge-500/20',
};

export function Badge({ className, tone = 'idle', ...rest }: Props) {
  return (
    <span
      {...rest}
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full ring-1 ring-inset',
        toneClasses[tone],
        className,
      )}
    />
  );
}
