import { api } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';

export const dynamic = 'force-dynamic';

export default async function SettingsPage() {
  const cfg = await api.settings();
  return (
    <div className="max-w-2xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">设置</h1>
        <p className="text-sm text-ink-500 mt-1">本地版 · 一次性配置后忘记</p>
      </header>

      <Card className="p-6">
        <h2 className="font-semibold mb-3">外部服务</h2>
        <p className="text-sm text-ink-500 mb-4">
          没有 API Key 时自动落 Mock，UI 与流程不变；想用真实 LLM/POI 数据，在 <code className="px-1.5 py-0.5 bg-ink-100 rounded">backend/.env</code> 中填入对应 Key 后重启 backend。
        </p>
        <div className="space-y-3">
          {Object.entries(cfg.providers).map(([k, v]) => {
            const value = String(v);
            const configured = value !== 'mock' && !value.startsWith('mock');
            const labels: Record<string, string> = {
              llm: 'LLM · 摘要 / 抽取 / 搜索解释',
              embedding: 'Embedding · 向量检索',
              amap: '高德 POI · 国内地点验证',
              google_places: 'Google Places · 海外地点验证',
            };
            return (
              <div key={k} className="flex items-center justify-between p-3 rounded-xl bg-ink-50 gap-3">
                <div className="min-w-0">
                  <div className="font-medium uppercase tracking-wide text-xs text-ink-500">{k}</div>
                  <div className="text-sm text-ink-700 mt-0.5 truncate">{labels[k] ?? k}</div>
                </div>
                <Badge tone={configured ? 'good' : 'idle'}>
                  {configured ? `✓ ${value}` : 'Mock'}
                </Badge>
              </div>
            );
          })}
        </div>
      </Card>

      <Card className="p-6">
        <h2 className="font-semibold mb-3">数据存储</h2>
        <div className="text-sm text-ink-600 space-y-1.5">
          <div className="flex justify-between"><span className="text-ink-400">DB 路径</span><code className="text-ink-700">{cfg.data_dir}/index.db</code></div>
          <div className="flex justify-between"><span className="text-ink-400">Mock 模式</span><code className="text-ink-700">{cfg.use_mock}</code></div>
        </div>
      </Card>

      <Card className="p-6">
        <h2 className="font-semibold mb-3">采集</h2>
        <p className="text-sm text-ink-500 leading-relaxed">
          当前为 Phase 1 本地版。Phase 2 将集成 MediaCrawler 做"我的收藏"周期同步 + iOS Shortcut 实时入库。<br />
          目前可用 <code className="px-1.5 py-0.5 bg-ink-100 rounded">scripts/import.sh path/to/notes.jsonl</code> 手动导入数据。
        </p>
      </Card>
    </div>
  );
}
