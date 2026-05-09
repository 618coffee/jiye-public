'use client';

import Link from 'next/link';
import { ArrowUpRight } from 'lucide-react';
import type { Note } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { SmartImage } from '@/components/ui/SmartImage';
import { freshnessLabel } from '@/lib/utils';

const PURPOSE_META: Record<string, { label: string; icon: string }> = {
  food: { label: '美食', icon: '🍱' },
  cafe: { label: '咖啡', icon: '☕' },
  scenic: { label: '景点', icon: '⛩️' },
  shopping: { label: '购物', icon: '🛍️' },
  photo: { label: '拍照', icon: '📷' },
  stay: { label: '住宿', icon: '🏨' },
};

export function NoteCard({ note }: { note: Note }) {
  const firstPoi = note.pois[0];
  const fresh = firstPoi ? freshnessLabel(firstPoi.poi.freshness_status) : null;
  return (
    <Link href={`/note/${note.id}`} className="block group">
      <Card className="card-lift overflow-hidden h-full flex flex-col">
        <div className="relative aspect-[4/3] overflow-hidden">
          <SmartImage
            src={note.cover}
            alt={note.title}
            fallbackSeed={note.id}
            className="w-full h-full transition-transform duration-700 ease-out group-hover:scale-[1.04]"
          />
          {fresh && (
            <div className="absolute top-3 left-3">
              <Badge tone={fresh.tone}>{fresh.text}</Badge>
            </div>
          )}
          <div className="absolute top-3 right-3 inline-flex items-center justify-center w-8 h-8 rounded-full bg-white/80 backdrop-blur-md text-ink-700 opacity-0 group-hover:opacity-100 transition-opacity">
            <ArrowUpRight className="w-4 h-4" />
          </div>
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-20 bg-gradient-to-t from-black/45 to-transparent" />
          {note.images.length > 1 && (
            <div className="absolute bottom-3 right-3 text-xs text-white/90 bg-black/30 backdrop-blur px-2 py-0.5 rounded-md">
              {note.images.length} 图
            </div>
          )}
        </div>
        <div className="p-4 flex-1 flex flex-col gap-2">
          <h3 className="font-semibold text-[15px] tracking-tight leading-snug line-clamp-2">{note.title}</h3>
          {note.ai_summary && (
            <p className="text-[13px] text-ink-500 line-clamp-2 leading-relaxed">
              <span className="text-rouge-500 font-medium mr-1">AI</span>
              {note.ai_summary}
            </p>
          )}
          <div className="flex flex-wrap gap-1.5 mt-auto pt-1">
            {note.trip_purpose.slice(0, 3).map((p) => {
              const meta = PURPOSE_META[p];
              return (
                <Badge key={p} tone="idle">
                  {meta?.icon} {meta?.label || p}
                </Badge>
              );
            })}
            {firstPoi?.must_try && (
              <Badge tone="accent">必点 · {firstPoi.must_try}</Badge>
            )}
          </div>
        </div>
      </Card>
    </Link>
  );
}
