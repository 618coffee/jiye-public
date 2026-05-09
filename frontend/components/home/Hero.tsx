'use client';

import { motion } from 'framer-motion';
import { Sparkles } from 'lucide-react';

interface Props {
  totalCities: number;
  totalNotes: number;
}

export function Hero({ totalCities, totalNotes }: Props) {
  return (
    <section className="relative overflow-hidden rounded-3xl border border-ink-200/70 bg-gradient-to-br from-white via-white to-rouge-50 shadow-soft p-8 sm:p-12">
      <motion.div
        initial={{ opacity: 0, scale: 0.85 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.6, ease: [0.2, 0.8, 0.2, 1] }}
        className="absolute -top-12 -right-10 w-64 h-64 rounded-full bg-gradient-to-br from-rouge-200 to-rouge-400 blur-3xl opacity-50 pointer-events-none"
      />
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.2, 0.8, 0.2, 1] }}
        className="relative max-w-2xl"
      >
        <div className="inline-flex items-center gap-2 mb-4 px-3 py-1 rounded-full bg-white/70 border border-ink-200 text-xs text-ink-600">
          <Sparkles className="w-3.5 h-3.5 text-rouge-500" />
          <span>本地版 · 已索引 {totalNotes} 条收藏 · 覆盖 {totalCities} 个目的地</span>
        </div>
        <h1 className="text-3xl sm:text-4xl font-semibold tracking-tight text-ink-900 leading-tight">
          你的小红书收藏，<br className="hidden sm:block" />
          按 <span className="bg-gradient-to-r from-rouge-500 to-rouge-700 bg-clip-text text-transparent">目的地</span> 一目了然。
        </h1>
        <p className="mt-4 text-ink-500 text-base sm:text-lg leading-relaxed">
          AI 自动按城市归档，按需验证 POI 是否还活着。<br />
          下次出发前，所有相关收藏都已为你整理好。
        </p>
      </motion.div>
    </section>
  );
}
