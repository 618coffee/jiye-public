import { api } from '@/lib/api';
import { notFound } from 'next/navigation';
import { NoteDetail } from '@/components/note/NoteDetail';

export const dynamic = 'force-dynamic';

interface PageProps { params: Promise<{ id: string }> }

export default async function NotePage({ params }: PageProps) {
  const { id } = await params;
  try {
    const note = await api.note(id);
    return <NoteDetail note={note} />;
  } catch {
    notFound();
  }
}
