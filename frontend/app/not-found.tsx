import Link from 'next/link';
import { Button } from '@/components/ui/Button';

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="text-7xl mb-6">🍃</div>
      <h1 className="text-2xl font-semibold tracking-tight">没找到这个页面</h1>
      <p className="text-ink-500 mt-2">可能是 URL 拼错了，或这条收藏还没索引。</p>
      <Link href="/" className="mt-6">
        <Button>回到目的地</Button>
      </Link>
    </div>
  );
}
