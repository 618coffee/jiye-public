'use client';

import { useState } from 'react';
import { cn } from '@/lib/utils';

interface Props {
  src?: string | null;
  alt?: string;
  className?: string;
  fallbackSeed?: string;
}

/** Image with graceful fallback to a deterministic gradient when offline. */
export function SmartImage({ src, alt = '', className, fallbackSeed }: Props) {
  const [errored, setErrored] = useState(false);
  if (!src || errored) {
    return <GradientFallback seed={fallbackSeed || alt || 'x'} className={className} />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      loading="lazy"
      className={cn('object-cover', className)}
      onError={() => setErrored(true)}
    />
  );
}

function GradientFallback({ seed, className }: { seed: string; className?: string }) {
  // Pretty deterministic 2-stop gradient.
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  const hueA = h % 360;
  const hueB = (hueA + 60 + (h >> 8) % 80) % 360;
  return (
    <div
      className={cn('relative overflow-hidden', className)}
      style={{
        background: `linear-gradient(135deg, hsl(${hueA} 70% 78%), hsl(${hueB} 70% 70%))`,
      }}
    >
      <span className="absolute inset-0 flex items-center justify-center text-3xl text-white/60 font-light">
        ✿
      </span>
    </div>
  );
}
