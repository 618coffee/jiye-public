'use client';

import Link from 'next/link';
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ArrowLeft, ExternalLink, ChevronLeft, ChevronRight } from 'lucide-react';
import type { Note } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { SmartImage } from '@/components/ui/SmartImage';
import { freshnessLabel } from '@/lib/utils';

export function NoteDetail({ note }: { note: Note }) {
  const [idx, setIdx] = useState(0);
  const images = note.images.length > 0 ? note.images : [note.cover].filter(Boolean) as string[];
  const total = Math.max(images.length, 1);
  const ocrTexts = note.image_ocr_texts || [];
  const ocrForCurrent = ocrTexts[idx];
  const totalOcr = ocrTexts.filter(Boolean).length;
  // Prefer locally-cached thumbnail if available — proxied through Next rewrites at /thumbs.
  const thumbs = note.image_thumbnails || [];
  const displayImages = images.map((src, i) => thumbs[i] || src);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <Link
          href={note.primary_city ? `/city/${encodeURIComponent(note.primary_city)}` : '/'}
          className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-ink-800"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          返回 {note.primary_city ? note.primary_city : '首页'}
        </Link>
        {note.url && (
          <a
            href={note.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-sm text-ink-500 hover:text-rouge-600"
          >
            原帖 <ExternalLink className="w-3.5 h-3.5" />
          </a>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1.2fr_1fr] gap-6">
        <Card className="overflow-hidden">
          <div className="relative aspect-square bg-ink-100">
            <AnimatePresence mode="wait">
              <motion.div
                key={idx}
                initial={{ opacity: 0, scale: 1.02 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.4 }}
                className="absolute inset-0"
              >
                <SmartImage
                  src={displayImages[idx]}
                  fallbackSeed={`${note.id}-${idx}`}
                  className="w-full h-full"
                />
              </motion.div>
            </AnimatePresence>
            {total > 1 && (
              <>
                <button
                  onClick={() => setIdx((idx - 1 + total) % total)}
                  className="absolute left-3 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-white/80 backdrop-blur hover:bg-white shadow-soft flex items-center justify-center"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setIdx((idx + 1) % total)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-white/80 backdrop-blur hover:bg-white shadow-soft flex items-center justify-center"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
                <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex gap-1.5">
                  {images.map((_, i) => (
                    <span
                      key={i}
                      className={
                        i === idx ? 'w-5 h-1.5 bg-white rounded-full transition-all' : 'w-1.5 h-1.5 bg-white/60 rounded-full transition-all'
                      }
                    />
                  ))}
                </div>
              </>
            )}
          </div>
        </Card>

        <div className="space-y-4">
          <div>
            <div className="flex flex-wrap gap-1.5 mb-3">
              {note.primary_city && <Badge tone="accent">📍 {note.primary_city}</Badge>}
              {note.trip_purpose.map((p) => <Badge key={p}>{p}</Badge>)}
            </div>
            <h1 className="text-2xl font-semibold tracking-tight leading-tight">{note.title}</h1>
            <p className="text-sm text-ink-500 mt-2">
              {note.author && <>by @{note.author} · </>}
              {note.saved_at && <>收藏于 {new Date(note.saved_at).toLocaleDateString('zh-CN')}</>}
            </p>
          </div>

          {note.ai_summary && (
            <Card className="p-4 border-rouge-200/70 bg-gradient-to-br from-rouge-50 to-white">
              <div className="text-xs font-medium text-rouge-700 tracking-wide uppercase mb-1">AI 摘要</div>
              <p className="text-[15px] text-ink-800 leading-relaxed">{note.ai_summary}</p>
            </Card>
          )}

          {totalOcr > 0 && (
            <Card className="p-4 border-amber-200/70 bg-gradient-to-br from-amber-50 to-white">
              <div className="flex items-center justify-between mb-1">
                <div className="text-xs font-medium text-amber-700 tracking-wide uppercase">
                  图片文字 OCR · {totalOcr}/{images.length}
                </div>
                <span className="text-[10px] text-amber-600">
                  当前图 {idx + 1}/{total}
                </span>
              </div>
              <p className="text-[13px] text-ink-700 leading-relaxed whitespace-pre-line min-h-[1.5em]">
                {ocrForCurrent || <span className="text-ink-400">这张图未识别到文字</span>}
              </p>
            </Card>
          )}

          <Card className="p-4">
            <div className="text-xs font-medium text-ink-400 tracking-wide uppercase mb-2">原文</div>
            <p className="text-[14px] whitespace-pre-line leading-relaxed text-ink-700">{note.body}</p>
          </Card>

          {note.pois.length > 0 && (
            <Card className="p-4">
              <div className="text-xs font-medium text-ink-400 tracking-wide uppercase mb-3">命中 POI</div>
              <div className="space-y-3">
                {note.pois.map((np) => {
                  const f = freshnessLabel(np.poi.freshness_status);
                  return (
                    <div key={np.poi.id} className="flex items-start gap-3 pb-3 last:pb-0 last:border-0 border-b border-ink-100">
                      <div className="flex-1 min-w-0">
                        <div className="font-medium">{np.poi.canonical_name}</div>
                        <div className="text-xs text-ink-400 mt-0.5">{np.poi.poi_type} · {np.poi.city}</div>
                        {np.must_try && (
                          <div className="text-sm mt-1.5 text-ink-700">
                            <span className="text-rouge-600 font-medium">必点</span> {np.must_try}
                            {np.price_per_person && <span className="ml-2 text-ink-500">人均 ¥{np.price_per_person}</span>}
                          </div>
                        )}
                        {np.highlight && <div className="text-xs text-ink-500 mt-1 italic">「{np.highlight}」</div>}
                      </div>
                      <Badge tone={f.tone}>{f.text}</Badge>
                    </div>
                  );
                })}
              </div>
            </Card>
          )}

          <div className="flex gap-2">
            <Button variant="primary">加入行程</Button>
            <Button variant="soft">已去过</Button>
            <Button variant="ghost">不感兴趣</Button>
          </div>
        </div>
      </div>
    </div>
  );
}
