import { Card } from '@/components/ui/Card';

export default function Loading() {
  return (
    <div className="space-y-6">
      <div className="skeleton h-48 rounded-3xl" />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <Card key={i} className="overflow-hidden">
            <div className="aspect-[4/3] skeleton rounded-none" />
            <div className="p-4 space-y-2">
              <div className="skeleton h-4 w-3/4" />
              <div className="skeleton h-3 w-1/2" />
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
