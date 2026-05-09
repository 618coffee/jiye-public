import { api } from '@/lib/api';
import { notFound } from 'next/navigation';
import { Workbench } from '@/components/workbench/Workbench';

export const dynamic = 'force-dynamic';

interface PageProps { params: Promise<{ city: string }> }

export default async function CityPage({ params }: PageProps) {
  const { city: raw } = await params;
  const city = decodeURIComponent(raw);
  try {
    const data = await api.workbench(city);
    return <Workbench data={data} />;
  } catch {
    notFound();
  }
}
