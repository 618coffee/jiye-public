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
        target_clusters: int = 12,
    ) -> list[dict[str, Any]]:
        """Group a free-form list of note titles into ~target_clusters thematic
        buckets. Used by the inbox-filtering UI so the user can decide which
        category to spend processing budget on.

        Returns: list of {id, name, description, sample_titles}
          - id: short slug ('food-jp', 'cafe-sh', ...)
          - name: human-friendly Chinese label (8-12 字)
          - description: one-line explanation (≤30 字)
          - sample_titles: 2-4 representative titles drawn from the input
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

    def cluster_titles(self, titles: list[str], *, target_clusters: int = 12) -> list[dict[str, Any]]:
        """Heuristic mock: bucket by purpose/city hints, then 'Other'."""
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
        return out[:target_clusters]


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

    def cluster_titles(self, titles: list[str], *, target_clusters: int = 12) -> list[dict[str, Any]]:
        return MockLLMProvider().cluster_titles(titles, target_clusters=target_clusters)


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

    def cluster_titles(self, titles: list[str], *, target_clusters: int = 12) -> list[dict[str, Any]]:
        if not titles:
            return []
        # Truncate each title to 60 chars and cap total count to be safe with
        # context windows; the caller is expected to sample if it has more.
        sample = [t[:60] for t in titles[:300]]
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(sample))
        prompt = (
            f"以下是用户从小红书收藏的笔记标题（共 {len(sample)} 条）：\n"
            f"{numbered}\n\n"
            f"请把它们按主题/地点/类型聚成 {target_clusters} 个中文簇，输出严格 JSON 数组：\n"
            "[\n"
            '  {\n'
            '    "id": "短英文 slug，如 food-jp / cafe-sh / fitness / fashion-outfit",\n'
            '    "name": "中文标签（6-12 字，例如「京都美食」「上海咖啡」「健身打卡」）",\n'
            '    "description": "一句话说明这个簇收什么样的帖子（≤30 字）",\n'
            '    "sample_titles": ["挑 8-12 条最有代表性的原标题（多一些样本能让后续 EM 更稳健）"]\n'
            '  },\n'
            "  ...\n"
            "]\n"
            "注意：\n"
            f"1) 簇数控制在 {max(target_clusters - 4, 6)}-{target_clusters + 4} 之间，太多就合并；\n"
            "2) sample_titles 必须是上面列表里出现过的原文，每个簇 8-12 条；\n"
            "3) 每条标题尽量只归到一个簇，但允许 ≤10% 标题不属于任何明显簇——把它们归到 id='other' / name='其他';\n"
            "4) 不要返回 markdown，只返回 JSON 数组。"
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
                # Some models wrap in {"clusters": [...]}; try to unwrap
                for k in ("clusters", "groups", "categories", "data"):
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
