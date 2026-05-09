import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatRelativeDate(iso?: string | null): string {
  if (!iso) return '从未刷新';
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (days < 1) return '今天刚刷新';
  if (days < 7) return `${days} 天前`;
  if (days < 30) return `${Math.floor(days / 7)} 周前`;
  return `${Math.floor(days / 30)} 月前`;
}

export function freshnessLabel(status?: string | null): { text: string; tone: 'good' | 'warn' | 'bad' | 'idle' } {
  switch (status) {
    case 'alive': return { text: '已验证', tone: 'good' };
    case 'suspect': return { text: '存疑', tone: 'warn' };
    case 'closed': return { text: '可能已关', tone: 'bad' };
    default: return { text: '未验证', tone: 'idle' };
  }
}
