'use client';

import { useEffect, useMemo, useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import {
  Inbox as InboxIcon,
  RefreshCw,
  DownloadCloud,
  Trash2,
  ExternalLink,
  Search,
  X,
  CheckSquare,
  Square,
  Loader2,
  ArrowUpCircle,
  AlertTriangle,
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Input } from '@/components/ui/Input';
import { SmartImage } from '@/components/ui/SmartImage';
import { cn } from '@/lib/utils';
import {
  api,
  type InboxItem,
  type InboxCluster,
  type InboxStats,
  type InboxClusterRunResult,
  type InboxPromoteResult,
  type InboxDetailTag,
} from '@/lib/api';

type StatusFilter = 'pending' | 'discarded' | 'promoted' | 'all';

const STATUS_TONE: Record<InboxItem['status'], 'idle' | 'good' | 'warn' | 'bad' | 'accent'> = {
  pending: 'idle',
  promoted: 'good',
  discarded: 'warn',
  failed: 'bad',
  processing: 'accent',
};

const STATUS_LABEL: Record<InboxItem['status'], string> = {
  pending: '待处理',
  promoted: '已入库',
  discarded: '已丢弃',
  failed: '失败',
  processing: '处理中',
};

const PAGE_SIZE = 200;

function useDebounced<T>(value: T, delay = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return v;
}

export default function InboxPage() {
  const [stats, setStats] = useState<InboxStats | null>(null);
  const [detailTags, setDetailTags] = useState<InboxDetailTag[]>([]);
  const [items, setItems] = useState<InboxItem[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set());
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('promoted');
  const [titleQuery, setTitleQuery] = useState('');
  const debouncedQuery = useDebounced(titleQuery, 300);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const [reclusterRunning, setReclusterRunning] = useState(false);
  const [lastRun, setLastRun] = useState<InboxClusterRunResult | null>(null);

  const [promoting, setPromoting] = useState(false);
  const [promoteProgress, setPromoteProgress] = useState<{ done: number; total: number } | null>(null);
  const [lastPromote, setLastPromote] = useState<InboxPromoteResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [lastScan, setLastScan] = useState<{
    scanned: number;
    inserted: number;
    updated: number;
    early_stopped?: boolean;
    incremental?: boolean;
    auto_cluster?: { assigned: number; unclustered: number; multi_tagged?: number; reason?: string };
  } | null>(null);
  const selectedTagList = useMemo(() => [...selectedTags].sort(), [selectedTags]);
  const selectedTagKey = selectedTagList.join('\u0001');

  // Initial + after-discard stats refresh
  async function loadStats() {
    try {
      const s = await api.inboxStats();
      setStats(s);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'stats load failed';
      setError(msg);
    }
  }

  async function loadTagStats() {
    try {
      const r = await api.inboxDetailTags();
      setDetailTags(r.tags);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'tag load failed';
      setError(msg);
    }
  }

  useEffect(() => {
    loadStats();
    loadTagStats();
  }, []);

  // Load items when filter changes
  useEffect(() => {
    setOffset(0);
    setSelected(new Set());
    void loadItems(0, /* append */ false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTagKey, statusFilter, debouncedQuery]);

  async function loadItems(off: number, append: boolean) {
    setLoading(true);
    setError(null);
    try {
      const r = await api.inboxList({
        status: statusFilter === 'all' ? undefined : statusFilter,
        detail_tags: selectedTagList,
        title_query: debouncedQuery.trim() || undefined,
        limit: PAGE_SIZE,
        offset: off,
      });
      setItems((prev) => (append ? [...prev, ...r.items] : r.items));
      setHasMore(r.items.length >= PAGE_SIZE);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'load failed';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAllVisible() {
    setSelected(new Set(items.map((it) => it.note_id)));
  }

  async function discardSelected() {
    if (selected.size === 0) return;
    const ids = [...selected];
    if (!confirm(`确认丢弃这 ${ids.length} 条收藏？此操作可在「已丢弃」状态下查看，但不会再参与聚类。`)) return;
    try {
      await api.inboxDiscard(ids);
      // Optimistic remove
      setItems((prev) => prev.filter((it) => !selected.has(it.note_id)));
      setSelected(new Set());
      await loadStats();
      await loadTagStats();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '丢弃失败';
      setError(msg);
    }
  }

  // Server hard-caps per request; we chunk and show progress.
  const PROMOTE_CHUNK = 10;

  async function promoteSelected() {
    if (selected.size === 0 || promoting) return;
    // Skip notes already in the main library — re-promote would no-op or duplicate.
    const promotedSet = new Set(
      items.filter((it) => it.status === 'promoted').map((it) => it.note_id),
    );
    const ids = [...selected].filter((id) => !promotedSet.has(id));
    if (ids.length === 0) {
      alert('所选笔记都已经在主库里了，无需再次升级。');
      return;
    }
    if (ids.length < selected.size) {
      const skipped = selected.size - ids.length;
      if (!confirm(`已自动跳过 ${skipped} 条已入库笔记。继续升级剩余 ${ids.length} 条？`)) return;
    } else if (!confirm(
      `确认把这 ${ids.length} 条加工进知识库？\n\n` +
      `每条会自动打开详情页 → LLM 摘要 → 抽取 POI → 写入主库。` +
      `预计每条 5-10 秒，期间请保持小红书 Chrome 标签页打开且已登录。`
    )) return;

    setPromoting(true);
    setError(null);
    setLastPromote(null);
    setPromoteProgress({ done: 0, total: ids.length });

    const aggregate: InboxPromoteResult = {
      results: [], promoted: 0, failed: 0, skipped: 0, duration_s: 0,
    };
    try {
      for (let i = 0; i < ids.length; i += PROMOTE_CHUNK) {
        const slice = ids.slice(i, i + PROMOTE_CHUNK);
        const r = await api.inboxPromote(slice, PROMOTE_CHUNK);
        aggregate.results.push(...r.results);
        aggregate.promoted += r.promoted;
        aggregate.failed += r.failed;
        aggregate.skipped += r.skipped;
        aggregate.duration_s += r.duration_s;
        // Optimistic: remove successfully promoted items from the list.
        const promotedIds = new Set(
          r.results.filter((o) => o.status === 'promoted').map((o) => o.note_id)
        );
        if (promotedIds.size) {
          setItems((prev) => prev.filter((it) => !promotedIds.has(it.note_id)));
          setSelected((prev) => {
            const next = new Set(prev);
            promotedIds.forEach((id) => next.delete(id));
            return next;
          });
        }
        setPromoteProgress({ done: Math.min(i + slice.length, ids.length), total: ids.length });
      }
      setLastPromote(aggregate);
      await loadStats();
      await loadTagStats();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '升级失败';
      setError(msg);
    } finally {
      setPromoting(false);
      setPromoteProgress(null);
    }
  }

  async function runScan(opts?: { full?: boolean }) {
    if (scanning) return;
    const full = !!opts?.full;
    const promptMsg = full
      ? '确认开始**全量**扫描小红书收藏夹？\n\n' +
        '会从头滚到底，可能需要 2-5 分钟。一般情况只在你怀疑漏掉了笔记时再用。'
      : '确认开始扫描小红书收藏夹？\n\n' +
        '会增量更新：xhs 收藏页按收藏时间倒序，扫到连续两屏都是已知笔记就停。' +
        '如果是上次扫描后新增的笔记不多，几秒就能跑完。';
    if (!confirm(promptMsg)) return;
    setScanning(true);
    setError(null);
    setLastScan(null);
    try {
      const r = await api.inboxScan(full ? { full: true } : {});
      setLastScan({
        scanned: r.scanned,
        inserted: r.inserted,
        updated: r.updated,
        early_stopped: r.early_stopped,
        incremental: r.incremental,
        auto_cluster: r.auto_cluster,
      });
      await loadStats();
      await loadTagStats();
      startTransition(() => {
        void loadItems(0, false);
        setOffset(0);
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '扫描失败';
      setError(msg);
    } finally {
      setScanning(false);
    }
  }

  async function runRecluster() {
    setReclusterRunning(true);
    setError(null);
    try {
      // No tunables: backend + LLM decide cluster count, sampling and
      // thresholds. Users shouldn't need to know any of that.
      const r = await api.inboxRecluster({});
      setLastRun(r);
      await loadStats();
      // Refresh current view
      startTransition(() => {
        void loadItems(0, false);
        setOffset(0);
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '聚类失败';
      setError(msg);
    } finally {
      setReclusterRunning(false);
    }
  }

  const pendingCount = stats?.by_status?.pending ?? 0;
  const discardedCount = stats?.by_status?.discarded ?? 0;
  const promotedCount = stats?.by_status?.promoted ?? 0;
  const allClusters = stats?.clusters ?? [];

  const selectedFilterName = useMemo(() => {
    if (selectedTagList.length === 0) return '全部';
    if (selectedTagList.length === 1) return `#${selectedTagList[0]}`;
    return `${selectedTagList.length} 个标签`;
  }, [selectedTagList]);

  function toggleDetailTag(tag: string) {
    setSelectedTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }

  return (
    <div className="space-y-6">
      {/* Header strip */}
      <Card className="p-5 sm:p-6">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-rouge-50 text-rouge-600">
            <InboxIcon className="w-5 h-5" />
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="text-lg sm:text-xl font-semibold tracking-tight">我的收藏</h1>
            <p className="text-xs sm:text-sm text-ink-500 mt-0.5">
              {stats ? (
                <>
                  <span className="font-medium text-ink-700">{pendingCount}</span> 待处理
                  <span className="mx-2 text-ink-300">·</span>
                  <span>{discardedCount}</span> 已丢弃
                  <span className="mx-2 text-ink-300">·</span>
                  <span>{promotedCount}</span> 已入库
                  <span className="mx-2 text-ink-300">·</span>
                  <span>{detailTags.length}</span> 个标签
                </>
              ) : (
                <span className="text-ink-400">载入中…</span>
              )}
            </p>
          </div>
          <Button
            variant="soft"
            size="md"
            onClick={(e) => runScan({ full: e.shiftKey })}
            disabled={scanning}
            title="点击：增量扫描（只抓新增的）；按住 Shift 点击：全量扫描"
          >
            {scanning ? <Loader2 className="w-4 h-4 animate-spin" /> : <DownloadCloud className="w-4 h-4" />}
            {scanning ? '扫描中…' : '扫描收藏夹'}
          </Button>
          <Button
            variant="soft"
            size="md"
            onClick={runRecluster}
            disabled={reclusterRunning}
            title="让 AI 重新读一遍你的收藏并重新分类"
          >
            {reclusterRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            {reclusterRunning ? '聚类中…' : '重新聚类'}
          </Button>
        </div>

        {lastRun && (
          <div className="mt-3 text-xs text-ink-500">
            上次重新聚类：分配 <b className="text-ink-700">{lastRun.assigned}</b> 条 ·
            生成 <b className="text-ink-700">{lastRun.clusters?.length ?? 0}</b> 个聚类 ·
            低置信 <b className="text-ink-700">{lastRun.low_confidence ?? 0}</b> ·
            多标签 <b className="text-ink-700">{lastRun.multi_tagged ?? 0}</b> ·
            用时 <b className="text-ink-700">{lastRun.duration_s}s</b>
          </div>
        )}
      </Card>

      {/* Two-column main */}
      <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-6">
        {/* Sidebar (desktop) */}
        <aside className="hidden lg:block">
          <TagSidebar
            tags={detailTags}
            selected={selectedTags}
            onToggle={toggleDetailTag}
            onClear={() => setSelectedTags(new Set())}
            total={promotedCount}
          />
        </aside>

        {/* Mobile tag chips */}
        <div className="lg:hidden -mx-4 px-4">
          <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-thin">
            <ClusterChip
              label="全部"
              count={promotedCount}
              active={selectedTags.size === 0}
              onClick={() => setSelectedTags(new Set())}
            />
            {detailTags.slice(0, 80).map((t) => (
              <ClusterChip
                key={t.tag}
                label={`#${t.tag}`}
                count={t.count}
                active={selectedTags.has(t.tag)}
                onClick={() => toggleDetailTag(t.tag)}
              />
            ))}
          </div>
        </div>

        {/* Right column */}
        <section className="space-y-4 min-w-0">
          {/* Filter strip */}
          <Card className="p-3 flex flex-col sm:flex-row sm:items-center gap-3">
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <Search className="w-4 h-4 text-ink-400 flex-shrink-0" />
              <Input
                value={titleQuery}
                onChange={(e) => setTitleQuery(e.target.value)}
                placeholder={`在「${selectedFilterName}」中搜索标题…`}
                className="h-9 text-sm border-0 shadow-none focus:ring-0 px-1"
              />
              {titleQuery && (
                <button
                  onClick={() => setTitleQuery('')}
                  className="p-1 rounded hover:bg-ink-100 text-ink-400"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
            <div className="flex items-center gap-1.5 flex-wrap">
              <StatusPill
                label="待处理"
                count={pendingCount}
                active={statusFilter === 'pending'}
                tone="default"
                onClick={() => setStatusFilter('pending')}
              />
              <StatusPill
                label="已入库"
                count={promotedCount}
                active={statusFilter === 'promoted'}
                tone="good"
                onClick={() => setStatusFilter('promoted')}
              />
              <StatusPill
                label="已丢弃"
                count={discardedCount}
                active={statusFilter === 'discarded'}
                tone="warn"
                onClick={() => setStatusFilter('discarded')}
              />
              <StatusPill
                label="全部"
                count={pendingCount + promotedCount + discardedCount}
                active={statusFilter === 'all'}
                tone="default"
                onClick={() => setStatusFilter('all')}
              />
              <span className="text-xs text-ink-400 ml-2 whitespace-nowrap">
                {loading ? '…' : `${items.length} 条`}
              </span>
            </div>
          </Card>

          {/* Bulk action bar */}
          <AnimatePresence>
            {selected.size > 0 && (
              <motion.div
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                className="sticky top-16 z-20"
              >
                <div className="rounded-2xl bg-ink-900 text-white shadow-lg px-4 py-3 flex items-center gap-3">
                  <CheckSquare className="w-4 h-4" />
                  <span className="text-sm font-medium">已选 {selected.size} 条</span>
                  <div className="flex-1" />
                  <button
                    onClick={selectAllVisible}
                    className="text-xs px-2.5 py-1 rounded-lg bg-white/10 hover:bg-white/20 transition"
                  >
                    全选当前页
                  </button>
                  <button
                    onClick={() => setSelected(new Set())}
                    className="text-xs px-2.5 py-1 rounded-lg bg-white/10 hover:bg-white/20 transition"
                  >
                    取消
                  </button>
                  <button
                    onClick={promoteSelected}
                    disabled={promoting}
                    className="text-xs px-3 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-600 disabled:opacity-60 disabled:cursor-not-allowed transition inline-flex items-center gap-1.5 font-medium"
                  >
                    {promoting ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <ArrowUpCircle className="w-3.5 h-3.5" />
                    )}
                    {promoting && promoteProgress
                      ? `升级中 ${promoteProgress.done}/${promoteProgress.total}`
                      : '升级到主库'}
                  </button>
                  <button
                    onClick={discardSelected}
                    disabled={promoting}
                    className="text-xs px-3 py-1.5 rounded-lg bg-rose-500 hover:bg-rose-600 disabled:opacity-60 disabled:cursor-not-allowed transition inline-flex items-center gap-1.5 font-medium"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    丢弃
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Error */}
          {error && (
            <Card className="p-4 border-rose-200 bg-rose-50/80 text-rose-700 text-sm">
              {error}
            </Card>
          )}

          {/* Scan result banner */}
          {lastScan && (
            <Card className="p-3 border-sky-200 bg-sky-50/80 text-sm flex items-center gap-2 text-sky-800">
              <DownloadCloud className="w-4 h-4" />
              <span>
                {lastScan.incremental ? '增量扫描' : '全量扫描'}完成：本次见到 {lastScan.scanned} 条 ·
                新增 {lastScan.inserted} · 更新 {lastScan.updated}
                {lastScan.early_stopped ? ' · 命中已知笔记后提前结束' : ''}
                {lastScan.auto_cluster && (lastScan.auto_cluster.assigned > 0 || lastScan.auto_cluster.unclustered > 0)
                  ? ` · 自动归类 ${lastScan.auto_cluster.assigned}` +
                    (lastScan.auto_cluster.unclustered > 0
                      ? ` · ${lastScan.auto_cluster.unclustered} 条未聚类`
                      : '') +
                    (lastScan.auto_cluster.multi_tagged && lastScan.auto_cluster.multi_tagged > 0
                      ? ` · ${lastScan.auto_cluster.multi_tagged} 条多标签`
                      : '')
                  : ''}
              </span>
              <button
                onClick={() => setLastScan(null)}
                className="ml-auto p-1 rounded hover:bg-sky-100 text-sky-700"
                aria-label="关闭"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </Card>
          )}

          {/* Promote result banner */}
          {lastPromote && (
            <Card className="p-4 border-emerald-200 bg-emerald-50/80 text-sm">
              <div className="flex items-center gap-2 text-emerald-800 font-medium mb-1">
                <ArrowUpCircle className="w-4 h-4" />
                <span>
                  升级完成：成功 {lastPromote.promoted} · 失败 {lastPromote.failed}
                  {lastPromote.skipped ? ` · 跳过 ${lastPromote.skipped}` : ''}
                  · 用时 {lastPromote.duration_s.toFixed(1)}s
                </span>
                <button
                  onClick={() => setLastPromote(null)}
                  className="ml-auto p-1 rounded hover:bg-emerald-100 text-emerald-700"
                  aria-label="关闭"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              {lastPromote.failed > 0 && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-rose-700 inline-flex items-center gap-1.5">
                    <AlertTriangle className="w-3.5 h-3.5" />
                    查看失败明细（{lastPromote.failed}）
                  </summary>
                  <ul className="mt-2 space-y-1 text-xs text-rose-700/90 max-h-48 overflow-auto">
                    {lastPromote.results
                      .filter((r) => r.status === 'failed')
                      .map((r) => (
                        <li key={r.note_id} className="font-mono">
                          <span className="text-rose-900">{r.note_id}</span>
                          {r.title ? <span className="text-ink-600"> · {r.title}</span> : null}
                          {r.error ? <span className="text-rose-600"> — {r.error}</span> : null}
                        </li>
                      ))}
                  </ul>
                </details>
              )}
            </Card>
          )}

          {/* Grid */}
          {loading && items.length === 0 ? (
            <SkeletonGrid />
          ) : items.length === 0 ? (
            <EmptyState clusterName={selectedFilterName} />
          ) : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3 sm:gap-4">
                <AnimatePresence initial={false}>
                  {items.map((it) => (
                    <motion.div
                      key={it.note_id}
                      layout
                      initial={{ opacity: 0, scale: 0.96 }}
                      animate={{ opacity: 1, scale: 1 }}
                      exit={{ opacity: 0, scale: 0.92 }}
                      transition={{ duration: 0.18 }}
                    >
                      <InboxCard
                        item={it}
                        selected={selected.has(it.note_id)}
                        onToggle={() => toggle(it.note_id)}
                        clusters={allClusters}
                      />
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>

              {hasMore && (
                <div className="flex justify-center pt-2">
                  <Button
                    variant="soft"
                    size="md"
                    disabled={loading}
                    onClick={() => {
                      const next = offset + PAGE_SIZE;
                      setOffset(next);
                      void loadItems(next, true);
                    }}
                  >
                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                    加载更多
                  </Button>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

// -------------------------- Subcomponents --------------------------

function TagSidebar({
  tags,
  selected,
  onToggle,
  onClear,
  total,
}: {
  tags: InboxDetailTag[];
  selected: Set<string>;
  onToggle: (tag: string) => void;
  onClear: () => void;
  total: number;
}) {
  const [tagQuery, setTagQuery] = useState('');
  const visibleTags = useMemo(() => {
    const q = tagQuery.trim().toLowerCase();
    return tags
      .filter((tag) => !q || tag.tag.toLowerCase().includes(q))
      .sort((a, b) => {
        const activeDelta = Number(selected.has(b.tag)) - Number(selected.has(a.tag));
        return activeDelta || b.count - a.count || a.tag.localeCompare(b.tag);
      });
  }, [tags, tagQuery, selected]);
  return (
    <div className="sticky top-24 max-h-[calc(100vh-7rem)] overflow-y-auto pr-1 space-y-1">
      <button
        onClick={onClear}
        className={cn(
          'w-full text-left px-3 py-2.5 rounded-xl transition-all border',
          selected.size === 0
            ? 'bg-rouge-50/80 border-rouge-200 ring-1 ring-rouge-200/60'
            : 'bg-transparent border-transparent hover:bg-ink-100/60',
        )}
      >
        <div className="flex items-center gap-2">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-ink-900 line-clamp-1">全部标签</div>
            <div className="text-[11px] text-ink-400 line-clamp-1 mt-0.5">
              多选标签时显示任一匹配的收藏
            </div>
          </div>
          <span className={cn(
            'text-[11px] tabular-nums px-1.5 py-0.5 rounded-md',
            selected.size === 0 ? 'bg-rouge-100 text-rouge-700' : 'bg-ink-100 text-ink-500',
          )}>
            {total}
          </span>
        </div>
      </button>
      {selected.size > 0 && (
        <button
          onClick={onClear}
          className="w-full text-left px-3 py-2 text-xs rounded-xl text-ink-500 hover:bg-ink-100/60 transition"
        >
          清除已选的 {selected.size} 个标签
        </button>
      )}
      <div className="relative py-1">
        <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
        <input
          value={tagQuery}
          onChange={(event) => setTagQuery(event.target.value)}
          placeholder="筛选标签…"
          className="w-full h-8 rounded-lg border border-ink-200 bg-white pl-8 pr-2 text-xs text-ink-700 placeholder:text-ink-400 focus:outline-none focus:border-rouge-300"
        />
      </div>
      {visibleTags.map((tag) => {
        const active = selected.has(tag.tag);
        return (
          <button
            key={tag.tag}
            onClick={() => onToggle(tag.tag)}
            className={cn(
              'w-full text-left px-3 py-2 rounded-xl transition-all border',
              active
                ? 'bg-ink-900 text-white border-ink-900'
                : 'bg-transparent border-transparent hover:bg-ink-100/60 text-ink-800',
            )}
          >
            <div className="flex items-center gap-2">
              <span className="flex-1 min-w-0 text-sm font-medium line-clamp-1">#{tag.tag}</span>
              <span className={cn(
                'text-[11px] tabular-nums px-1.5 py-0.5 rounded-md',
                active ? 'bg-white/15 text-white/80' : 'bg-ink-100 text-ink-500',
              )}>
                {tag.count}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

function ClusterSidebar({
  clusters,
  selectedId,
  onSelect,
  totalPending,
}: {
  clusters: InboxCluster[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  totalPending: number;
}) {
  return (
    <div className="sticky top-24 max-h-[calc(100vh-7rem)] overflow-y-auto pr-1 space-y-1">
      <SidebarRow
        active={selectedId === null}
        onClick={() => onSelect(null)}
        name="全部"
        count={totalPending}
        emphasis
      />
      {clusters.map((c) => (
        <SidebarRow
          key={c.id}
          active={selectedId === c.id}
          onClick={() => onSelect(c.id)}
          name={c.name}
          description={c.description}
          count={c.count}
          isCatchAll={c.id === '__low_conf__'}
          earliestDiscoveredAt={c.earliest_discovered_at}
          latestDiscoveredAt={c.latest_discovered_at}
          notesAddedLast7d={c.notes_added_last_7d}
        />
      ))}
    </div>
  );
}

function SidebarRow({
  active,
  onClick,
  name,
  description,
  count,
  emphasis,
  isCatchAll,
  earliestDiscoveredAt,
  latestDiscoveredAt,
  notesAddedLast7d,
}: {
  active: boolean;
  onClick: () => void;
  name: string;
  description?: string | null;
  count: number;
  emphasis?: boolean;
  isCatchAll?: boolean;
  earliestDiscoveredAt?: string | null;
  latestDiscoveredAt?: string | null;
  notesAddedLast7d?: number | null;
}) {
  // Build a single-line freshness string when we have any time data.
  const freshness = formatClusterFreshness(
    earliestDiscoveredAt,
    latestDiscoveredAt,
    notesAddedLast7d,
  );
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full text-left px-3 py-2.5 rounded-xl transition-all border',
        active
          ? 'bg-rouge-50/80 border-rouge-200 ring-1 ring-rouge-200/60'
          : 'bg-transparent border-transparent hover:bg-ink-100/60',
      )}
    >
      <div className="flex items-center gap-2">
        <div className="flex-1 min-w-0">
          <div
            className={cn(
              'text-sm line-clamp-1',
              emphasis ? 'font-semibold text-ink-900' : 'font-medium text-ink-800',
              isCatchAll && 'italic text-ink-600',
            )}
          >
            {name}
          </div>
          {description && (
            <div className="text-[11px] text-ink-400 line-clamp-1 mt-0.5">{description}</div>
          )}
          {freshness && (
            <div className="text-[10.5px] text-ink-400 line-clamp-1 mt-0.5">{freshness}</div>
          )}
        </div>
        <span
          className={cn(
            'text-[11px] tabular-nums px-1.5 py-0.5 rounded-md',
            active ? 'bg-rouge-100 text-rouge-700' : 'bg-ink-100 text-ink-500',
          )}
        >
          {count}
        </span>
      </div>
    </button>
  );
}

/** Render a compact "earliest ~ latest · last-7d" string for cluster cards. */
function formatClusterFreshness(
  earliest: string | null | undefined,
  latest: string | null | undefined,
  last7d: number | null | undefined,
): string | null {
  const parts: string[] = [];
  if (earliest && latest) {
    const fmt = (iso: string) => new Date(iso).toLocaleDateString('zh-CN', {
      month: 'numeric', day: 'numeric',
    });
    const a = fmt(earliest);
    const b = fmt(latest);
    parts.push(a === b ? `🕐 ${a}` : `🕐 ${a} – ${b}`);
  }
  if (last7d && last7d > 0) {
    parts.push(`近7天 ${last7d}`);
  }
  return parts.length ? parts.join(' · ') : null;
}

/** Compact relative-time string in Chinese: 刚刚 / N分钟前 / N小时前 / N天前 / N月前. */
function formatRelativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return '';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return '刚刚';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}小时前`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}天前`;
  const mo = Math.floor(day / 30);
  if (mo < 12) return `${mo}个月前`;
  return `${Math.floor(mo / 12)}年前`;
}

function ClusterChip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex-shrink-0 inline-flex items-center gap-1.5 h-8 px-3 rounded-full text-xs transition',
        active
          ? 'bg-ink-900 text-white'
          : 'bg-white border border-ink-200 text-ink-700 hover:border-ink-300',
      )}
    >
      <span>{label}</span>
      <span className={cn('tabular-nums', active ? 'text-white/70' : 'text-ink-400')}>
        {count}
      </span>
    </button>
  );
}

function StatusPill({
  label,
  count,
  active,
  tone,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  tone: 'default' | 'good' | 'warn';
  onClick: () => void;
}) {
  const activeCls =
    tone === 'good'
      ? 'bg-emerald-500 text-white border-emerald-500'
      : tone === 'warn'
        ? 'bg-amber-500 text-white border-amber-500'
        : 'bg-ink-900 text-white border-ink-900';
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1.5 h-8 px-3 rounded-full text-xs border transition',
        active ? activeCls : 'bg-white border-ink-200 text-ink-700 hover:border-ink-300',
      )}
    >
      <span>{label}</span>
      <span className={cn('tabular-nums', active ? 'text-white/80' : 'text-ink-400')}>
        {count}
      </span>
    </button>
  );
}

function InboxCard({
  item,
  selected,
  onToggle,
  clusters,
}: {
  item: InboxItem;
  selected: boolean;
  onToggle: () => void;
  clusters: InboxCluster[];
}) {
  const router = useRouter();
  const tone = STATUS_TONE[item.status];
  const label = STATUS_LABEL[item.status];
  const isPromoted = item.status === 'promoted';
  const tags = (item.tags ?? []).filter(
    (t) => t.cluster_id !== '__unclustered__' && t.cluster_id !== '__low_conf__',
  );
  const detailTags = (item.detail_tags ?? []).slice(0, 6);
  const showTagChips = tags.length > 1;
  const clusterName = (cid: string) =>
    clusters.find((c) => c.id === cid)?.name ?? cid;
  return (
    <Card
      className={cn(
        'overflow-hidden card-lift cursor-pointer relative group flex flex-col',
        selected && 'ring-2 ring-rouge-400 ring-offset-2 ring-offset-mesh',
      )}
      onClick={() => {
        if (isPromoted) router.push(`/note/${item.note_id}`);
        else onToggle();
      }}
      title={isPromoted ? '点击查看主库正文' : undefined}
    >
      <div className="relative aspect-[4/5] overflow-hidden bg-ink-100">
        <SmartImage
          src={item.cover_url}
          alt={item.title ?? ''}
          fallbackSeed={item.note_id}
          className="w-full h-full transition-transform duration-700 ease-out group-hover:scale-[1.04]"
        />
        {/* Checkbox overlay — still works on promoted cards (for discard / multi-select) */}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          aria-label={selected ? '取消选择' : '选择'}
          className={cn(
            'absolute top-2 left-2 w-6 h-6 rounded-md flex items-center justify-center transition',
            selected
              ? 'bg-rouge-500 text-white'
              : 'bg-white/85 text-ink-500 opacity-0 group-hover:opacity-100',
          )}
        >
          {selected ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />}
        </button>
        {item.status !== 'pending' && (
          <div className="absolute top-2 right-2">
            <Badge tone={tone}>{label}</Badge>
          </div>
        )}
      </div>
      <div className="p-3 flex-1 flex flex-col gap-1.5">
        <div
          className="text-[13px] font-medium text-ink-800 leading-snug line-clamp-2"
          title={item.title ?? ''}
        >
          {item.title || <span className="text-ink-400">（无标题）</span>}
        </div>
        {showTagChips && (
          <div className="flex flex-wrap gap-1 mt-0.5">
            {tags.map((t) => (
              <span
                key={t.cluster_id}
                title={`${clusterName(t.cluster_id)} · ${t.score.toFixed(2)}`}
                className={cn(
                  'inline-flex items-center rounded-full px-1.5 leading-tight',
                  t.is_primary
                    ? 'bg-rouge-100 text-rouge-700 text-[10px] py-0.5 font-medium'
                    : 'bg-ink-100 text-ink-500 text-[9.5px] py-[1px]',
                )}
              >
                {clusterName(t.cluster_id)}
              </span>
            ))}
          </div>
        )}
        {detailTags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-0.5">
            {detailTags.map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center rounded-full bg-white border border-ink-200 px-1.5 py-[1px] text-[9.5px] leading-tight text-ink-500"
              >
                #{tag}
              </span>
            ))}
          </div>
        )}
        <div className="flex items-center justify-between gap-2 mt-auto pt-1">
          <div className="text-[11px] text-ink-400 line-clamp-1 flex-1 min-w-0">
            {item.author || '匿名'}
            {item.discovered_at && (
              <span className="ml-1.5 text-ink-300">
                · {formatRelativeTime(item.discovered_at)}发现
              </span>
            )}
          </div>
          {item.url && (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="p-1 rounded hover:bg-ink-100 text-ink-400 hover:text-ink-700 transition"
              title="在小红书打开"
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          )}
        </div>
      </div>
    </Card>
  );
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3 sm:gap-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <Card key={i} className="overflow-hidden">
          <div className="skeleton aspect-[4/5]" />
          <div className="p-3 space-y-2">
            <div className="skeleton h-3.5 w-full" />
            <div className="skeleton h-3.5 w-2/3" />
          </div>
        </Card>
      ))}
    </div>
  );
}

function EmptyState({ clusterName }: { clusterName: string }) {
  return (
    <Card className="p-12 text-center text-ink-500">
      <div className="inline-flex w-12 h-12 rounded-full bg-ink-100 items-center justify-center mb-3">
        <InboxIcon className="w-5 h-5 text-ink-400" />
      </div>
      <div className="text-sm">「{clusterName}」中没有匹配的收藏</div>
      <div className="text-xs text-ink-400 mt-1">换个状态、换个聚类，或清空搜索试试</div>
    </Card>
  );
}
