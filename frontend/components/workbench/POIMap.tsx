'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { ExternalLink, LocateFixed, MapPin } from 'lucide-react';
import type { POI } from '@/lib/api';
import { api } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { freshnessLabel } from '@/lib/utils';

type MappedPOI = POI & { lat: number; lng: number };

declare global {
  interface Window {
    AMap?: any;
    _AMapSecurityConfig?: { securityJsCode?: string };
  }
}

function hasCoords(poi: POI): poi is MappedPOI {
  return typeof poi.lat === 'number' && typeof poi.lng === 'number';
}

function outOfChina(lat: number, lng: number) {
  return !(lng >= 72.004 && lng <= 137.8347 && lat >= 0.8293 && lat <= 55.8271);
}

function transformLat(x: number, y: number) {
  let ret = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y;
  ret += 0.2 * Math.sqrt(Math.abs(x));
  ret += ((20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2) / 3;
  ret += ((20 * Math.sin(y * Math.PI) + 40 * Math.sin((y / 3) * Math.PI)) * 2) / 3;
  ret += ((160 * Math.sin((y / 12) * Math.PI) + 320 * Math.sin((y * Math.PI) / 30)) * 2) / 3;
  return ret;
}

function transformLng(x: number, y: number) {
  let ret = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y;
  ret += 0.1 * Math.sqrt(Math.abs(x));
  ret += ((20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2) / 3;
  ret += ((20 * Math.sin(x * Math.PI) + 40 * Math.sin((x / 3) * Math.PI)) * 2) / 3;
  ret += ((150 * Math.sin((x / 12) * Math.PI) + 300 * Math.sin((x / 30) * Math.PI)) * 2) / 3;
  return ret;
}

function wgs84ToGcj02(lat: number, lng: number): [number, number] {
  if (outOfChina(lat, lng)) return [lat, lng];
  const a = 6378245.0;
  const ee = 0.006693421622965943;
  let dLat = transformLat(lng - 105, lat - 35);
  let dLng = transformLng(lng - 105, lat - 35);
  const radLat = (lat / 180) * Math.PI;
  let magic = Math.sin(radLat);
  magic = 1 - ee * magic * magic;
  const sqrtMagic = Math.sqrt(magic);
  dLat = (dLat * 180) / (((a * (1 - ee)) / (magic * sqrtMagic)) * Math.PI);
  dLng = (dLng * 180) / ((a / sqrtMagic) * Math.cos(radLat) * Math.PI);
  return [lat + dLat, lng + dLng];
}

function amapUri(poi: MappedPOI) {
  const [lat, lng] = wgs84ToGcj02(poi.lat, poi.lng);
  const name = encodeURIComponent(poi.canonical_name);
  return `https://uri.amap.com/marker?position=${lng},${lat}&name=${name}&src=xhskb&coordinate=gaode`;
}

function loadAmap(key: string, securityJsCode?: string) {
  if (window.AMap) return Promise.resolve(window.AMap);
  if (securityJsCode) {
    window._AMapSecurityConfig = { securityJsCode };
  }
  const existing = document.querySelector<HTMLScriptElement>('script[data-amap-js="true"]');
  if (existing) {
    return new Promise<any>((resolve, reject) => {
      existing.addEventListener('load', () => resolve(window.AMap));
      existing.addEventListener('error', reject);
    });
  }
  return new Promise<any>((resolve, reject) => {
    const script = document.createElement('script');
    script.dataset.amapJs = 'true';
    script.async = true;
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(key)}`;
    script.onload = () => resolve(window.AMap);
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

export function POIMap({ city, pois }: { city: string; pois: POI[] }) {
  const mapRef = useRef<HTMLDivElement | null>(null);
  const mapInstanceRef = useRef<any>(null);
  const markerRef = useRef<any>(null);
  const mappedPois = useMemo(() => pois.filter(hasCoords), [pois]);
  const [selectedId, setSelectedId] = useState<number | null>(mappedPois[0]?.id ?? null);
  const [amapKey, setAmapKey] = useState('');
  const [securityCode, setSecurityCode] = useState('');
  const [mapReady, setMapReady] = useState(false);
  const [mapError, setMapError] = useState(false);
  const selected = mappedPois.find((p) => p.id === selectedId) ?? mappedPois[0];
  const missing = pois.length - mappedPois.length;
  const geojsonUrl = `/api/cities/${encodeURIComponent(city)}/pois.geojson`;

  useEffect(() => {
    let alive = true;
    api.settings()
      .then((settings) => {
        if (!alive) return;
        setAmapKey(settings.maps?.amap_js_api_key || '');
        setSecurityCode(settings.maps?.amap_security_js_code || '');
      })
      .catch(() => setMapError(true));
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!selected || !amapKey || !mapRef.current) return;
    let cancelled = false;
    setMapError(false);
    loadAmap(amapKey, securityCode)
      .then((AMap) => {
        if (cancelled || !mapRef.current) return;
        const [lat, lng] = wgs84ToGcj02(selected.lat, selected.lng);
        const center = [lng, lat];
        if (!mapInstanceRef.current) {
          mapInstanceRef.current = new AMap.Map(mapRef.current, {
            center,
            zoom: 15,
            viewMode: '2D',
            mapStyle: 'amap://styles/normal',
          });
        } else {
          mapInstanceRef.current.setZoomAndCenter(15, center);
        }
        if (!markerRef.current) {
          markerRef.current = new AMap.Marker({ position: center, title: selected.canonical_name });
          mapInstanceRef.current.add(markerRef.current);
        } else {
          markerRef.current.setPosition(center);
          markerRef.current.setTitle(selected.canonical_name);
        }
        setMapReady(true);
      })
      .catch(() => {
        if (!cancelled) setMapError(true);
      });
    return () => { cancelled = true; };
  }, [amapKey, securityCode, selected]);

  if (mappedPois.length === 0) {
    return (
      <Card className="p-5">
        <div className="flex items-center gap-2 text-sm font-medium text-ink-700">
          <MapPin className="h-4 w-4" />
          高德地图
        </div>
        <div className="mt-2 text-sm text-ink-500">
          暂时没有已验证坐标。刷新该城市后，可定位的地点会出现在这里。
        </div>
      </Card>
    );
  }

  const f = freshnessLabel(selected.freshness_status);

  return (
    <section>
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">高德地图</h2>
          <div className="mt-0.5 text-sm text-ink-400">
            {city} · {mappedPois.length} 个坐标{missing > 0 ? ` · ${missing} 个待验证` : ''}
          </div>
        </div>
        <a
          href={geojsonUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-8 items-center justify-center gap-2 rounded-lg border border-ink-300 bg-transparent px-3 text-sm font-medium text-ink-800 transition-all duration-200 ease-out hover:bg-ink-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-rouge-400 focus-visible:ring-offset-2"
        >
          <LocateFixed className="h-4 w-4" />
          GeoJSON
        </a>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Card className="overflow-hidden">
          <div className="relative aspect-[16/10] min-h-[320px] bg-ink-100">
            <div ref={mapRef} className="absolute inset-0" />
            {(!amapKey || mapError) && (
              <div className="absolute inset-0 flex items-center justify-center bg-ink-50 p-6 text-center">
                <a
                  href={amapUri(selected)}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-2 rounded-lg border border-ink-300 bg-white px-4 py-2 text-sm font-medium text-ink-800 shadow-soft hover:bg-ink-50"
                >
                  <MapPin className="h-4 w-4 text-rouge-500" />
                  在高德地图打开
                </a>
              </div>
            )}
            {amapKey && !mapReady && !mapError && (
              <div className="absolute inset-0 flex items-center justify-center text-sm text-ink-400">
                加载高德地图…
              </div>
            )}
          </div>
        </Card>

        <Card className="flex min-h-[320px] flex-col overflow-hidden">
          <div className="border-b border-ink-100 p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate font-semibold">{selected.canonical_name}</div>
                <div className="mt-1 text-xs text-ink-400">
                  {selected.lat.toFixed(5)}, {selected.lng.toFixed(5)}
                </div>
              </div>
              <Badge tone={f.tone}>{f.text}</Badge>
            </div>
            {selected.address && (
              <div className="mt-2 line-clamp-2 text-xs text-ink-500">{selected.address}</div>
            )}
            <a
              href={amapUri(selected)}
              target="_blank"
              rel="noreferrer"
              className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-ink-700 hover:text-rouge-600"
            >
              打开高德地图
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>

          <div className="flex-1 overflow-y-auto p-2">
            {mappedPois.map((poi) => {
              const active = poi.id === selected.id;
              return (
                <button
                  key={poi.id}
                  type="button"
                  onClick={() => setSelectedId(poi.id)}
                  className={
                    active
                      ? 'flex w-full items-start gap-3 rounded-lg bg-ink-900 p-3 text-left text-white'
                      : 'flex w-full items-start gap-3 rounded-lg p-3 text-left text-ink-700 hover:bg-ink-50'
                  }
                >
                  <MapPin className={active ? 'mt-0.5 h-4 w-4 text-white' : 'mt-0.5 h-4 w-4 text-rouge-500'} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{poi.canonical_name}</span>
                    <span className={active ? 'mt-0.5 block truncate text-xs text-white/65' : 'mt-0.5 block truncate text-xs text-ink-400'}>
                      {poi.poi_type || 'place'}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </Card>
      </div>
    </section>
  );
}
