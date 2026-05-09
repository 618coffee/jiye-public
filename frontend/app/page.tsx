import { api } from '@/lib/api';
import { CityList } from '@/components/home/CityList';
import { Hero } from '@/components/home/Hero';

export const dynamic = 'force-dynamic';

export default async function HomePage() {
  const { cities } = await api.cities();
  return (
    <div className="space-y-10">
      <Hero totalCities={cities.length} totalNotes={cities.reduce((s, c) => s + c.note_count, 0)} />
      <CityList cities={cities} />
    </div>
  );
}
