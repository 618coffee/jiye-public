"""LLM provider — Qwen JSON-mode for extraction; mock returns canned structures."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from ..config import settings


class LLMProvider(ABC):
    @abstractmethod
    def extract_note(self, note_text: str) -> dict[str, Any]:
        """Returns {summary, primary_city, primary_country, trip_purpose, pois:[...]}"""

    @abstractmethod
    def explain_search(self, query: str, hits: list[dict[str, Any]]) -> str:
        """Short natural-language explanation summarising results."""

    @abstractmethod
    def cluster_city(self, city: str, notes: list[dict[str, Any]]) -> dict[str, Any]:
        """Cluster a city's notes into actionable insights.
        Returns {"hot":[Cluster], "cold":[Cluster], "dish_radar":[Cluster], "blind_spots":[Cluster]}
        Cluster = {label:str, reason:str, note_ids:[str]}
        """

    @abstractmethod
    def cluster_titles(
        self,
        titles: list[str],
        *,
        target_clusters: int | None = None,
    ) -> list[dict[str, Any]]:
        """Group a free-form list of note titles into thematic buckets.

        ``target_clusters`` is an optional **hint** to the LLM; ``None``
        (default) means the model picks whatever number of clusters fits
        the data — could be 1, could be 40. Used by the inbox-filtering UI
        so the user can decide which category to spend processing budget on.

        Returns: list of {id, name, description, sample_titles}
          - id: short slug ('food', 'cafe', 'hiking', ...)
          - name: human-friendly Chinese label (6-12 字)
          - description: one-line explanation (≤30 字)
          - sample_titles: 2-4 representative titles drawn from the input
        """

    @abstractmethod
    def classify_titles(
        self,
        titles: list[str],
        clusters: list[dict[str, Any]],
    ) -> list[str | None]:
        """Assign each title to one of ``clusters`` (by id), or None if the
        title clearly doesn't fit any bucket.

        ``clusters`` items must have at least ``id`` and ``name``;
        ``description`` and ``sample_titles`` are used when present to
        improve the model's grounding.

        Returns a list the same length as ``titles``. Each entry is either
        a cluster id from the input list, or ``None`` for "no good fit"
        (the caller routes those to a low-confidence bucket).
        """


_CITY_HINTS = {
    "京都": ("京都", "JP"), "Kyoto": ("京都", "JP"),
    "东京": ("东京", "JP"), "Tokyo": ("东京", "JP"),
    "大阪": ("大阪", "JP"), "Osaka": ("大阪", "JP"),
    "上海": ("上海", "CN"), "Shanghai": ("上海", "CN"),
    "杭州": ("杭州", "CN"), "Hangzhou": ("杭州", "CN"),
    "广州": ("广州", "CN"), "Guangzhou": ("广州", "CN"),
    "深圳": ("深圳", "CN"), "Shenzhen": ("深圳", "CN"),
    "成都": ("成都", "CN"), "Chengdu": ("成都", "CN"),
    "重庆": ("重庆", "CN"), "Chongqing": ("重庆", "CN"),
    "北京": ("北京", "CN"), "Beijing": ("北京", "CN"),
    "厦门": ("厦门", "CN"), "Xiamen": ("厦门", "CN"),
    "苏州": ("苏州", "CN"), "Suzhou": ("苏州", "CN"),
    "巴黎": ("巴黎", "FR"), "Paris": ("巴黎", "FR"),
    "洛杉矶": ("洛杉矶", "US"), "Los Angeles": ("洛杉矶", "US"), "LA": ("洛杉矶", "US"),
    "纽约": ("纽约", "US"), "New York": ("纽约", "US"), "NYC": ("纽约", "US"),
    "旧金山": ("旧金山", "US"), "San Francisco": ("旧金山", "US"),
    "伦敦": ("伦敦", "GB"), "London": ("伦敦", "GB"),
    "曼谷": ("曼谷", "TH"), "Bangkok": ("曼谷", "TH"),
    "首尔": ("首尔", "KR"), "Seoul": ("首尔", "KR"),
    "新加坡": ("新加坡", "SG"), "Singapore": ("新加坡", "SG"),
}

_PURPOSE_HINTS = {
    # food
    "美食": "food", "好吃": "food", "餐厅": "food", "拉面": "food", "馄饨": "food",
    "寿司": "food", "刺身": "food", "金枪鱼": "food", "饮茶": "food", "早茶": "food",
    "粤菜": "food", "法餐": "food", "套餐": "food", "必点": "food", "招牌": "food",
    "圣代": "food", "抹茶": "food", "蛋糕": "food", "Falafel": "food",
    # cafe
    "咖啡": "cafe", "拿铁": "cafe", "拉花": "cafe", "espresso": "cafe", "Arabica": "cafe",
    # scenic
    "景点": "scenic", "寺": "scenic", "神社": "scenic", "公园": "scenic",
    "鸟居": "scenic", "稻荷": "scenic", "天文台": "scenic", "Observatory": "scenic",
    # shopping
    "购物": "shopping", "商场": "shopping", "市集": "shopping",
    # photo
    "拍照": "photo", "机位": "photo", "出片": "photo", "黄昏": "photo",
    "panorama": "photo", "全景": "photo",
    # stay
    "酒店": "stay", "民宿": "stay",
}


class MockLLMProvider(LLMProvider):
    def extract_note(self, note_text: str) -> dict[str, Any]:
        city, country = None, "CN"
        for k, (c, co) in _CITY_HINTS.items():
            if k in note_text:
                city, country = c, co
                break
        purposes = sorted({v for k, v in _PURPOSE_HINTS.items() if k in note_text})
        # naive POI: first quoted name or noun phrase fallback
        m = re.search(r"《([^》]+)》|『([^』]+)』|「([^」]+)」", note_text)
        poi_name = next((g for g in (m.groups() if m else []) if g), None)
        pois: list[dict[str, Any]] = []
        if poi_name:
            pois.append({
                "name": poi_name,
                "type": "restaurant" if "food" in purposes else "scenic",
                "must_try": None,
                "price_per_person": None,
                "highlight": None,
            })
        first_line = note_text.strip().splitlines()[0] if note_text.strip() else ""
        summary = first_line[:30] or "（无摘要）"
        return {
            "summary": summary,
            "primary_city": city,
            "primary_country": country,
            "trip_purpose": purposes or ["other"],
            "pois": pois,
        }

    def explain_search(self, query: str, hits: list[dict[str, Any]]) -> str:
        if not hits:
            return f"没有命中「{query}」。试试更宽的关键词，比如城市名 + 类型。"
        cities = sorted({(h.get("note", {}).get("primary_city")) for h in hits if h.get("note", {}).get("primary_city")})
        cities = [c for c in cities if c]
        coverage = f"，覆盖 {', '.join(cities)}" if cities else ""
        return f"为「{query}」找到 {len(hits)} 条相关收藏{coverage}。"

    def cluster_city(self, city: str, notes: list[dict[str, Any]]) -> dict[str, Any]:
        """Heuristic baseline: hot=most-purposes notes, cold=oldest, dish_radar=food, blind_spots=closed POIs."""
        hot, cold, dish, blind = [], [], [], []
        food_notes = [n for n in notes if "food" in (n.get("trip_purpose") or [])]
        if food_notes:
            dish.append({
                "label": f"{city} 美食地图",
                "reason": f"按收藏频次推荐，共 {len(food_notes)} 家",
                "note_ids": [n["id"] for n in food_notes[:6]],
            })
        # hot = first 3 by saved_at desc (notes already sorted)
        if notes:
            hot.append({
                "label": "近期收藏热点",
                "reason": "最近收藏的 3 条",
                "note_ids": [n["id"] for n in notes[:3]],
            })
        # cold = bottom 3
        if len(notes) > 3:
            cold.append({
                "label": "沉睡收藏",
                "reason": "很久没看的收藏，出发前可以重温",
                "note_ids": [n["id"] for n in notes[-3:]],
            })
        # blind = notes whose pois have closed status
        closed_notes = [
            n["id"] for n in notes
            if any((p.get("poi") or {}).get("freshness_status") == "closed" for p in (n.get("pois") or []))
        ]
        if closed_notes:
            blind.append({
                "label": "可能塌房",
                "reason": "POI 鲜活度检查显示已关闭/搬迁",
                "note_ids": closed_notes,
            })
        return {"hot": hot, "cold": cold, "dish_radar": dish, "blind_spots": blind}

    def cluster_titles(self, titles: list[str], *, target_clusters: int | None = None) -> list[dict[str, Any]]:
        """Heuristic mock: bucket by purpose/city hints, then 'Other'.

        ``target_clusters`` is honoured only as a soft cap. ``None`` means
        "no cap" (free-form), which mirrors the real provider.
        """
        from collections import defaultdict
        # bucket key -> (label, list of titles)
        buckets: dict[str, list[str]] = defaultdict(list)

        def _classify(t: str) -> str:
            city_hit = next((c for c, _ in _CITY_HINTS.values() if c in t), None)
            purpose_hit = next((v for k, v in _PURPOSE_HINTS.items() if k in t), None)
            if city_hit and purpose_hit:
                return f"{city_hit}-{purpose_hit}"
            if city_hit:
                return f"{city_hit}-other"
            if purpose_hit:
                return f"any-{purpose_hit}"
            return "other-other"

        for t in titles:
            buckets[_classify(t)].append(t)

        purpose_zh = {
            "food": "美食", "cafe": "咖啡", "scenic": "景点",
            "shopping": "购物", "photo": "拍照", "stay": "住宿", "other": "其他",
        }
        out: list[dict[str, Any]] = []
        for key, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            city, purpose = key.split("-", 1)
            name = (
                f"{city}{purpose_zh.get(purpose, purpose)}" if city != "any" and city != "other"
                else (purpose_zh.get(purpose, purpose) or "其他")
            )
            out.append({
                "id": key,
                "name": name,
                "description": f"包含 {len(items)} 条相关收藏",
                "sample_titles": items[:3],
            })
        return out if target_clusters is None else out[:target_clusters]

    def classify_titles(
        self,
        titles: list[str],
        clusters: list[dict[str, Any]],
    ) -> list[str | None]:
        """Mock classifier: keyword scan against cluster name + samples."""
        if not titles or not clusters:
            return [None] * len(titles)
        # Build keyword bag per cluster: name + sample tokens
        bags: list[tuple[str, set[str]]] = []
        for c in clusters:
            cid = c.get("id") or ""
            kws: set[str] = set()
            for s in [c.get("name", "")] + list(c.get("sample_titles", []) or []):
                for tok in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", s or ""):
                    kws.add(tok.lower())
            bags.append((cid, kws))
        out: list[str | None] = []
        for t in titles:
            t_low = t.lower()
            best, best_score = None, 0
            for cid, kws in bags:
                score = sum(1 for k in kws if k in t_low)
                if score > best_score:
                    best, best_score = cid, score
            out.append(best if best_score >= 1 else None)
        return out


class QwenLLMProvider(LLMProvider):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def extract_note(self, note_text: str) -> dict[str, Any]:
        # Real implementation calls Dashscope chat with JSON mode.
        # For the local-dev scope we still always parse safely.
        import httpx
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        prompt = (
            "你是小红书内容索引助手。基于以下帖子，输出严格 JSON："
            "{\"summary\":string,\"primary_city\":string|null,\"primary_country\":string,"
            "\"trip_purpose\":[string],\"pois\":[{\"name\":string,\"type\":string,"
            "\"must_try\":string|null,\"price_per_person\":number|null,\"highlight\":string|null}]}"
            "\n---\n" + note_text
        )
        body = {
            "model": "qwen-plus",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        try:
            r = httpx.post(url, headers={"Authorization": f"Bearer {self.api_key}"}, json=body, timeout=30)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception:
            return MockLLMProvider().extract_note(note_text)

    def explain_search(self, query: str, hits: list[dict[str, Any]]) -> str:
        return MockLLMProvider().explain_search(query, hits)

    def cluster_city(self, city: str, notes: list[dict[str, Any]]) -> dict[str, Any]:
        return MockLLMProvider().cluster_city(city, notes)

    def cluster_titles(self, titles: list[str], *, target_clusters: int | None = None) -> list[dict[str, Any]]:
        return MockLLMProvider().cluster_titles(titles, target_clusters=target_clusters)

    def classify_titles(self, titles, clusters):
        return MockLLMProvider().classify_titles(titles, clusters)


_EXTRACT_PROMPT = (
    "你是小红书内容索引助手。读完帖子后，输出严格 JSON（无 markdown，无解释）：\n"
    "{\n"
    '  "summary": "用一句话总结(20-50字)：地点+亮点+用户能拿走什么(必点/机位/价位)",\n'
    '  "primary_city": "中文城市名，如 京都/东京/上海/洛杉矶/巴黎；标题里出现就直接抽，不要 null",\n'
    '  "primary_country": "ISO-2 国家码，如 CN/JP/US/FR/TH；不确定时根据城市推断",\n'
    '  "trip_purpose": ["food","cafe","scenic","shopping","photo","stay","other" 中选 1-3 个"],\n'
    '  "pois": [{"name":"店名/景点名(原文，不翻译)","type":"restaurant|cafe|scenic|shop|hotel|other","must_try":"必点/必去具体项|null","price_per_person":"人均(数字,人民币)|null","highlight":"一句话亮点|null"}]\n'
    "}\n"
    "示例输入：\n"
    "京都祇园｜茶寮都路里抹茶圣代\\n人均200，必点抹茶圣代和宇治金时刨冰，店里小排队30分钟。\n"
    "示例输出：\n"
    '{"summary":"京都祇园茶寮都路里，必点抹茶圣代和宇治金时刨冰，人均200，需排队30分钟。","primary_city":"京都","primary_country":"JP","trip_purpose":["food","photo"],"pois":[{"name":"茶寮都路里","type":"cafe","must_try":"抹茶圣代/宇治金时刨冰","price_per_person":200,"highlight":"祇园老牌抹茶店"}]}\n'
    "现在处理：\n---\n"
)


class AzureOpenAILLMProvider(LLMProvider):
    """Azure AI Foundry / Azure OpenAI (OpenAI-compatible REST)."""

    def __init__(self, endpoint: str, api_key: str, chat_deployment: str, api_version: str):
        ep = endpoint.rstrip("/")
        # Foundry project endpoints look like .../api/projects/<project>; the
        # /openai/deployments/... path is served at the *root* of the resource.
        if "/api/projects/" in ep:
            ep = ep.split("/api/projects/")[0]
        # Azure OpenAI / Foundry both expose /openai/deployments/{deployment}/chat/completions
        self.url = (
            f"{ep}/openai/deployments/{chat_deployment}/chat/completions"
            f"?api-version={api_version}"
        )
        self.api_key = api_key
        # Some routers (e.g. model-router) reject response_format=json_object.
        # We try once with it; if Azure 400s we permanently disable it.
        self._supports_json_mode = True

    def _chat(self, messages: list[dict[str, str]], *, json_mode: bool, max_tokens: int, temperature: float) -> str:
        import httpx
        body: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and self._supports_json_mode:
            body["response_format"] = {"type": "json_object"}
        r = httpx.post(
            self.url,
            headers={"api-key": self.api_key, "Content-Type": "application/json"},
            json=body,
            timeout=45,
        )
        if r.status_code == 400 and json_mode and self._supports_json_mode:
            # Probably "response_format not supported" — disable and retry once.
            self._supports_json_mode = False
            body.pop("response_format", None)
            r = httpx.post(
                self.url,
                headers={"api-key": self.api_key, "Content-Type": "application/json"},
                json=body,
                timeout=45,
            )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def extract_note(self, note_text: str) -> dict[str, Any]:
        try:
            content = self._chat(
                [
                    {"role": "system", "content": "You are a strict JSON-only assistant. Output ONLY a single JSON object, no markdown, no prose."},
                    {"role": "user", "content": _EXTRACT_PROMPT + note_text},
                ],
                json_mode=True,
                max_tokens=800,
                temperature=0.2,
            )
            return _parse_json_lenient(content) or MockLLMProvider().extract_note(note_text)
        except Exception:
            return MockLLMProvider().extract_note(note_text)

    def explain_search(self, query: str, hits: list[dict[str, Any]]) -> str:
        if not hits:
            return MockLLMProvider().explain_search(query, hits)
        digest = "\n".join(
            f"- {h.get('note',{}).get('title','')[:60]} ({h.get('note',{}).get('primary_city','?')})"
            for h in hits[:5]
        )
        try:
            return self._chat(
                [
                    {"role": "system", "content": "你是搜索结果解说员，用一两句中文总结命中情况，突出最相关的城市和品类，不要列表，不要 markdown。"},
                    {"role": "user", "content": f"查询：{query}\n命中：\n{digest}"},
                ],
                json_mode=False,
                max_tokens=160,
                temperature=0.4,
            ).strip()
        except Exception:
            return MockLLMProvider().explain_search(query, hits)

    def cluster_city(self, city: str, notes: list[dict[str, Any]]) -> dict[str, Any]:
        if not notes:
            return {"hot": [], "cold": [], "dish_radar": [], "blind_spots": []}
        # Build a compact digest the LLM can reason about.
        digest_lines: list[str] = []
        for n in notes:
            poi_names = [
                (p.get("poi") or {}).get("canonical_name", "")
                for p in (n.get("pois") or [])
            ]
            poi_status = [
                (p.get("poi") or {}).get("freshness_status") or "?"
                for p in (n.get("pois") or [])
            ]
            digest_lines.append(
                f"- id={n['id']} | {n.get('title','')[:50]} | "
                f"purpose={','.join(n.get('trip_purpose') or [])} | "
                f"saved={n.get('saved_at','')[:10]} | "
                f"pois={'/'.join(filter(None, poi_names)) or '-'} "
                f"({'/'.join(poi_status) or '-'}) | "
                f"summary={n.get('ai_summary','')[:60]}"
            )
        digest = "\n".join(digest_lines)
        prompt = (
            f"以下是用户在「{city}」收藏的小红书笔记摘要：\n"
            f"{digest}\n\n"
            "请把这些笔记按 4 个维度聚类，输出严格 JSON：\n"
            "{\n"
            '  "hot":         [{"label":"小标题(8字内)","reason":"为什么聚一起(20字内)","note_ids":["id1","id2"]}],  # 最值得优先去的 1-3 簇\n'
            '  "cold":        [{"label":"...","reason":"...","note_ids":[...]}],  # 沉睡很久或冷门但有价值的 0-2 簇\n'
            '  "dish_radar":  [{"label":"...","reason":"...","note_ids":[...]}],  # 美食按品类/价位聚 1-3 簇\n'
            '  "blind_spots": [{"label":"...","reason":"...","note_ids":[...]}]   # 已塌房/可能塌房/缺口 0-2 簇\n'
            "}\n"
            "注意：1) note_ids 必须是上面出现过的 id，至少 1 个；"
            "2) 每个维度可以为空数组；3) label/reason 都用中文；"
            "4) 一个 note 可以出现在多个簇里。"
        )
        try:
            content = self._chat(
                [
                    {"role": "system", "content": "你是个人收藏分析师，擅长把杂乱笔记聚成可执行的洞察。只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                json_mode=True,
                max_tokens=1200,
                temperature=0.4,
            )
            data = _parse_json_lenient(content) or {}
            note_ids = {n["id"] for n in notes}
            out: dict[str, list[dict[str, Any]]] = {"hot": [], "cold": [], "dish_radar": [], "blind_spots": []}
            for k in out:
                for c in data.get(k) or []:
                    if not isinstance(c, dict):
                        continue
                    ids = [i for i in (c.get("note_ids") or []) if i in note_ids]
                    if not ids:
                        continue
                    out[k].append({
                        "label": (c.get("label") or "")[:40],
                        "reason": (c.get("reason") or "")[:80],
                        "note_ids": ids,
                    })
            # If LLM produced nothing useful, fall back to heuristic.
            if not any(out.values()):
                return MockLLMProvider().cluster_city(city, notes)
            return out
        except Exception:
            return MockLLMProvider().cluster_city(city, notes)

    def cluster_titles(self, titles: list[str], *, target_clusters: int | None = None) -> list[dict[str, Any]]:
        if not titles:
            return []
        # Truncate each note item and cap total count to be safe with context
        # windows; the caller is expected to sample if it has more. Items may
        # be title-only or enriched text: title + tags + body excerpt.
        sample = [t[:260] for t in titles[:300]]
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(sample))
        # Two prompt modes:
        #  - target_clusters=None (default in the UI): let the LLM pick the
        #    natural number of clusters. Could be 1 (everything is travel),
        #    could be 30+ (very diverse hobbyist).
        #  - target_clusters=int: legacy hint mode, kept for tests / scripts.
        if target_clusters is None:
            count_directive = (
                "**\u7c07\u6570**\uff1a\u7531\u4f60\u6839\u636e\u5185\u5bb9\u81ea\u7531\u51b3\u5b9a\u3002\u53ef\u80fd\u53ea\u67091\u4e2a\u7c07\uff08\u5982\u679c\u6536\u85cf\u9ad8\u5ea6\u540c\u8d28\uff09\uff0c\n"
                "   \u4e5f\u53ef\u80fd\u667030+\u4e2a\u7c07\uff08\u5982\u679c\u6536\u85cf\u6781\u4e3a\u591a\u5143\uff09\u3002\u539f\u5219\u662f\u201c\u5408\u5e76\u540c\u8d28\u3001\u4e0d\u5f3a\u5212\u201d\uff1a\n"
                "   \u540c\u4e00\u4e3b\u9898\u4e0b\u53ea\u7559\u4e00\u4e2a\u7c07\uff0c\u4f46\u4e0d\u8981\u4e3a\u4e86\u51d1\u6570\u91cf\u628a\u4e0d\u540c\u4e3b\u9898\u5f3a\u53e0\u5728\u4e00\u8d77\u3002"
            )
        else:
            lo = max(target_clusters - 4, 2)
            hi = target_clusters + 4
            count_directive = (
                f"**\u7c07\u6570**\uff1a\u63a7\u5236\u5728 {lo}-{hi} \u4e4b\u95f4\uff0c\u592a\u591a\u5c31\u5408\u5e76\uff1b\u5b81\u5c11\u52ff\u591a\u3002"
            )
        prompt = (
            f"以下是用户从小红书收藏的笔记条目（可能包含标题、标签、正文摘要；共 {len(sample)} 条）：\n"
            f"{numbered}\n\n"
            "请把它们按**主题/活动/兴趣**聚成若干个中文簇，综合标题、标签和正文摘要判断，不要只看地名；输出严格 JSON 数组：\n"
            "[\n"
            '  {\n'
            '    "id": "\u77ed\u82f1\u6587 slug\uff0c\u5982 food / cafe / hiking / city-walk / photo-gear / fashion-outfit / car-maintenance / ticketing",\n'
            '    "name": "\u4e2d\u6587\u6807\u7b7e\uff086-12 \u5b57\uff0c\u4f8b\u5982\u300c\u5496\u5561\u63a2\u5e97\u300d\u300c\u5f92\u6b65\u767b\u5c71\u300d\u300c\u57ce\u5e02\u6f2b\u6b65\u300d\u300c\u7a7f\u642d\u53c2\u8003\u300d\u300c\u62cd\u7167\u673a\u4f4d\u300d\u300c\u6570\u7801\u8bc4\u6d4b\u300d\uff09",\n'
            '    "description": "\u4e00\u53e5\u8bdd\u8bf4\u660e\u8fd9\u4e2a\u7c07\u6536\u4ec0\u4e48\u6837\u7684\u5e16\u5b50\uff08\u226430 \u5b57\uff09",\n'
            '    "sample_titles": ["\u6311 8-12 \u6761\u6700\u6709\u4ee3\u8868\u6027\u7684\u539f\u6807\u9898\uff08\u591a\u4e00\u4e9b\u6837\u672c\u80fd\u8ba9\u540e\u7eed EM \u66f4\u7a33\u5065\uff09"]\n'
            '  },\n'
            "  ...\n"
            "]\n"
            "**\u5173\u952e\u89c4\u5219\uff08\u8fdd\u53cd\u4f1a\u5bfc\u81f4\u805a\u7c7b\u65e0\u6cd5\u4f7f\u7528\uff09\uff1a**\n"
            "1) **覆盖率优先**：你产出的簇必须能覆盖**至少 70% 的输入条目**。\n"
            "   \u5982\u679c\u67d0\u4e2a\u4e3b\u9898\uff08\u4f8b\u5982\u300c\u65c5\u884c/\u51fa\u884c\u300d\u300c\u7f8e\u98df\u300d\u300c\u7a7f\u642d\u300d\uff09\u4e0b\u7b14\u8bb0**\u5f88\u591a**\uff0c\n"
            "   \u5c31**\u5fc5\u987b**\u4e3a\u5b83\u5efa\u4e00\u4e2a\u6216\u51e0\u4e2a\u5bbd\u6876\uff0c\u4e0d\u8981\u53ea\u6311\u51fa\u5c0f\u4f17\u7ec6\u5206\u4e3b\u9898\u800c\u628a\u5927\u5934\u7559\u7ed9 'other'\u3002\n"
            "   \u53cd\u4f8b\uff1a\u53ea\u8f93\u51fa\u300c\u5c45\u5bb6\u70f9\u996a\u300d\u300c\u5496\u5561\u9986\u300d\u300c\u6444\u5f71\u540e\u671f\u300d\u4e09\u4e2a\u7a84\u6876\uff0c\u628a 800 \u6761\u65c5\u884c\u7b14\u8bb0\u5168\u6254\u8fdb 'other'\u2014\u2014\u8fd9\u662f**\u9519\u8bef**\u7684\u3002\n"
            "   \u6b63\u786e\u505a\u6cd5\uff1a\u5148\u8bc6\u522b top 3 \u5927\u4e3b\u9898\uff0c\u4e3a\u5b83\u4eec\u5404\u5efa\u5bbd\u6876\uff0c\u7136\u540e\u518d\u8865\u51e0\u4e2a\u5c0f\u4f17\u7c07\u3002\n"
            "2) **\u7981\u6b62**\u4ee5\u56fd\u5bb6/\u5730\u533a/\u5927\u6d32\u505a\u4e3b\u5206\u7c7b\u3002\u4e0d\u5141\u8bb8\u51fa\u73b0\u300c\u65e5\u672c\u65c5\u884c\u300d\u300c\u6b27\u6d32\u98ce\u5149\u300d\u300c\u56fd\u5185\u653b\u7565\u300d\u300c\u7f8e\u56fd\u751f\u6d3b\u300d\u300c\u4e1c\u5357\u4e9a\u300d\u8fd9\u7c7b\u7c07\u2014\u2014\n"
            "   \u5730\u540d\u53ea\u80fd\u662f\u526f\u7ebf\u7d22\uff0c\u4e0d\u80fd\u6210\u4e3a\u7c07\u7684\u4e3b\u8f74\u3002\u53ef\u4ee5\u6309**\u505a\u4ec0\u4e48**\u5207\u65c5\u884c\uff1a\n"
            "   \u6237\u5916\uff08\u5f92\u6b65/\u81ea\u9a7e/\u96ea\u5c71/\u9732\u8425\uff09\u3001\u6d77\u5c9b\u5ea6\u5047\u3001\u57ce\u5e02\u6f2b\u6b65/\u51fa\u7247\u3001\u535a\u7269\u9986/\u4eba\u6587\u3001\u9152\u5e97/\u6c11\u5bbf\u3001\u81ea\u9a7e/\u8def\u4e66 \u7b49\u3002\n"
            "3) \u4f18\u5148\u6309**\u505a\u4ec0\u4e48 / \u770b\u4ec0\u4e48**\u5207\uff1a\u6237\u5916\u3001\u57ce\u5e02\u6f2b\u6b65\u3001\u5496\u5561\u63a2\u5e97\u3001\u9910\u5385\u7f8e\u98df\u3001\n"
            "   \u62cd\u7167\u673a\u4f4d\u3001\u6444\u5f71\u5668\u6750\u3001\u7a7f\u642d\u3001\u5bb6\u5c45\u3001\u6570\u7801\u8bc4\u6d4b\u3001\u7968\u52a1/\u6f14\u51fa\u3001\u8f66/\u7ef4\u4fee\u3001\u5065\u8eab\u3001\u8bfb\u4e66\u3001\u5176\u4ed6\u6742\u9879 \u7b49\u3002\n"
            f"4) {count_directive}\n"
            "5) sample_titles \u5fc5\u987b\u662f\u4e0a\u9762\u5217\u8868\u91cc\u51fa\u73b0\u8fc7\u7684\u539f\u6587\uff0c\u6bcf\u4e2a\u7c07 8-12 \u6761\u3002\n"
            "6) 'other'\uff08\u5176\u4ed6\u6742\u9879\uff09\u7684\u5360\u6bd4**\u4e0d\u5e94\u8d85\u8fc7 15%**\u3002\u5982\u679c\u4f60\u53d1\u73b0 >30% \u6807\u9898\u5f52\u5230 other\uff0c\n"
            "   \u90a3\u8bf4\u660e\u4f60\u6f0f\u5efa\u4e86\u67d0\u4e2a\u4e3b\u6d41\u4e3b\u9898\u2014\u2014\u56de\u53bb\u8865\u4e00\u4e2a\u5bbd\u6876\u3002\n"
            "7) \u4e0d\u8981\u8fd4\u56de markdown\uff0c\u53ea\u8fd4\u56de JSON \u6570\u7ec4\u3002"
        )
        try:
            content = self._chat(
                [
                    {"role": "system", "content": "你是收藏分类专家，把杂乱标题聚成可执行的简洁分类。只输出 JSON 数组。"},
                    {"role": "user", "content": prompt},
                ],
                json_mode=True,
                max_tokens=8000,
                temperature=0.3,
            )
            data = _parse_json_lenient(content)
            # _parse_json_lenient returns dict; for arrays we need to handle wrapping
            if isinstance(data, dict):
                # Some models wrap in {"clusters": [...]}; try to unwrap.
                # Azure model-router in json_mode often wraps as {"array": [...]}.
                for k in ("clusters", "groups", "categories", "data", "array", "items", "result"):
                    if isinstance(data.get(k), list):
                        data = data[k]
                        break
            if not isinstance(data, list):
                # Last-ditch: try to find a JSON array in the text
                m = re.search(r"\[[\s\S]*\]", content or "")
                if m:
                    try:
                        data = json.loads(m.group(0))
                    except Exception:
                        data = None
            if not isinstance(data, list) or not data:
                print(f"[llm.cluster_titles] parse returned non-list (len(content)={len(content or '')}); falling back to mock")
                return MockLLMProvider().cluster_titles(titles, target_clusters=target_clusters)
            # Sanitise each cluster
            seen_ids: set[str] = set()
            cleaned: list[dict[str, Any]] = []
            for c in data:
                if not isinstance(c, dict):
                    continue
                cid = (c.get("id") or "").strip().lower().replace(" ", "-") or f"c{len(cleaned):02d}"
                # Dedupe ids
                base = cid
                k = 1
                while cid in seen_ids:
                    cid = f"{base}-{k}"
                    k += 1
                seen_ids.add(cid)
                name = (c.get("name") or "").strip()[:24] or "未命名"
                desc = (c.get("description") or "").strip()[:60]
                samples = [s for s in (c.get("sample_titles") or []) if isinstance(s, str)][:16]
                cleaned.append({
                    "id": cid,
                    "name": name,
                    "description": desc,
                    "sample_titles": samples,
                })
            return cleaned
        except Exception as e:
            import traceback
            print(f"[llm.cluster_titles] FAILED, falling back to mock: {e}")
            traceback.print_exc()
            return MockLLMProvider().cluster_titles(titles, target_clusters=target_clusters)

    def classify_titles(
        self,
        titles: list[str],
        clusters: list[dict[str, Any]],
    ) -> list[str | None]:
        """Batch-classify each note item into one of the proposed clusters.

        Why this exists: e5 cosine on 8-15 char Chinese titles cannot reliably
        distinguish 12 thematic buckets — short titles like "丹巴甲居藏寨"
        share nothing lexical with the cluster centroids and end up assigned
        more or less randomly. The LLM, given the title plus a labelled menu
        of buckets, picks the right one almost every time.

        Cost: ~50 titles per call, ~20 calls for 1000 titles. With
        gpt-4o-mini that's a few cents per full re-cluster.
        """
        if not titles or not clusters:
            return [None] * len(titles)

        # Compact menu: one line per cluster.
        menu_lines = []
        for c in clusters:
            cid = c.get("id") or ""
            name = c.get("name") or ""
            desc = (c.get("description") or "")[:50]
            menu_lines.append(f"  - {cid}: {name}" + (f" — {desc}" if desc else ""))
        menu = "\n".join(menu_lines)
        valid_ids = {c.get("id") for c in clusters if c.get("id")}

        BATCH = 50
        out: list[str | None] = [None] * len(titles)
        total_none = 0
        examples_none: list[str] = []

        for start in range(0, len(titles), BATCH):
            batch = titles[start : start + BATCH]
            numbered = "\n".join(f"{i+1}. {t[:360]}" for i, t in enumerate(batch))
            prompt = (
                "下面是用户从小红书收藏的笔记条目（可能包含标题、标签、正文摘要）。请把每条分到下列分类之一。\n\n"
                "**分类清单：**\n" + menu + "\n\n"
                "**笔记条目（编号从 1 开始）：**\n" + numbered + "\n\n"
                "**任务：**对每个编号，输出它最匹配的分类 id。\n"
                "- 只要标题、标签或正文摘要里出现明显的主题/活动/物品（旅行、餐厅、咖啡、穿搭、家居、相机、徒步、酒店…），就分到对应分类，不要轻易判 'none'。\n"
                "- 如果条目里只有人名/地名/标点/表情，没有任何主题信号，才输出 'none'。\n"
                "- 不要按地理位置分类（不要因为提到日本/上海就强行归到旅行；要看做的事情）。\n"
                "- 'none' 比例**不应超过 10%**。\n\n"
                "**输出**：严格 JSON，键 'assignments' 的值是数组，长度等于输入条数，"
                f"每个元素是该编号对应的 id 字符串（必须是上面分类清单里的某个 id，或字面字符串 'none'）。\n"
                "示例：{\"assignments\": [\"food-restaurant\", \"travel-scenic\", \"none\", ...]}"
            )
            try:
                content = self._chat(
                    [
                        {"role": "system", "content": "你是分类助手。只输出严格 JSON 对象，不要 markdown 不要解释。"},
                        {"role": "user", "content": prompt},
                    ],
                    json_mode=True,
                    max_tokens=2000,
                    temperature=0.0,
                )
                data = _parse_json_lenient(content)
                assignments: list[Any] | None = None
                if isinstance(data, dict):
                    for k in ("assignments", "result", "results", "labels", "data", "array", "items"):
                        if isinstance(data.get(k), list):
                            assignments = data[k]
                            break
                    # Also handle the common shape:
                    # {"1": "food", "2": "cafe", ...} (numeric-keyed dict)
                    if assignments is None and data:
                        keys = list(data.keys())
                        if all(k.isdigit() or k.startswith("title") for k in keys):
                            try:
                                ordered = sorted(data.items(), key=lambda kv: int(re.sub(r"\D", "", kv[0]) or "0"))
                                assignments = [v for _, v in ordered]
                            except Exception:
                                pass
                elif isinstance(data, list):
                    assignments = data
                if assignments is None:
                    print(f"[classify_titles] batch {start}: parse failed, content={content[:300]!r}")
                    continue
                # Map back, validating each id
                for i, raw in enumerate(assignments[: len(batch)]):
                    if not isinstance(raw, str):
                        if len(examples_none) < 8:
                            examples_none.append(f"[non-str] {batch[i]}")
                        total_none += 1
                        continue
                    cid = raw.strip()
                    if cid in valid_ids:
                        out[start + i] = cid
                    else:
                        total_none += 1
                        if len(examples_none) < 8:
                            examples_none.append(f"[{cid!r}] {batch[i]}")
                    # else leave as None (low_conf)
            except Exception as e:
                print(f"[classify_titles] batch {start} FAILED: {e}; leaving as None")
                continue
        print(f"[classify_titles] {len(titles)} titles, {total_none} returned 'none'/unknown ({100*total_none/max(1,len(titles)):.1f}%)")
        for ex in examples_none:
            print(f"  none-example: {ex[:120]}")
        return out


def _parse_json_lenient(text: str) -> dict[str, Any] | None:
    """Try strict JSON, then strip ``` fences, then regex first {...} block."""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip ```json ... ``` fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    # First balanced { ... } block
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            return None
    return None


_singleton: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    global _singleton
    if _singleton is not None:
        return _singleton
    if settings.use_mock == "true":
        _singleton = MockLLMProvider()
    elif settings.has_azure_openai:
        _singleton = AzureOpenAILLMProvider(
            settings.azure_openai_endpoint,
            settings.azure_openai_api_key,
            settings.azure_openai_chat_deployment,
            settings.azure_openai_api_version,
        )
    elif settings.qwen_api_key:
        _singleton = QwenLLMProvider(settings.qwen_api_key)
    else:
        _singleton = MockLLMProvider()
    return _singleton
