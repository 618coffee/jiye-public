"""Seed the local DB with realistic-looking sample notes so UI is fully usable.

Images use Unsplash random URLs (deterministic by seed) so they load on first run
when online. If offline, the frontend falls back to gradient placeholders.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .services.ingest import ingest_note
from .services.seed_images import url_for as _sample_url
from .store.base import KnowledgeStore


def _img(seed: str, w: int = 800, h: int = 800) -> str:
    return f"https://picsum.photos/seed/{seed}/{w}/{h}"


def _sample_notes() -> list[dict[str, Any]]:
    base = datetime(2026, 4, 15, tzinfo=timezone.utc)
    samples: list[tuple[str, str, str, str, list[str], list[str]]] = [
        # (id, title, body, author, image_seeds, [hint城市/类型])
        (
            "kyt001",
            "京都祇园｜藏在巷子里的『茶寮都路里』抹茶圣代",
            "京都的下午一定要留给抹茶。祇园四条这家『茶寮都路里』排了 40 分钟，但值。\n"
            "必点：特选都路里圣代，浓郁抹茶冰淇淋 + 白玉 + 红豆，¥1430。\n"
            "出片机位在二楼靠窗那张小桌，下午 3 点光线最柔。",
            "kyoto_lover",
            ["kyoto-1", "kyoto-2", "matcha-1"],
            ["京都"],
        ),
        (
            "kyt002",
            "京都｜伏见稻荷大社凌晨 6 点空无一人",
            "想拍千本鸟居全空镜的话，必须早起。地铁第一班到达稻荷站时刚好天微亮。\n"
            "推荐路线：稻荷站 → 楼门 → 千本鸟居 → 奥社 → 折返。全程 1 小时，避开人潮。\n"
            "拍照机位：千本鸟居入口 + 山顶展望台俯瞰京都。",
            "early_bird",
            ["kyoto-3", "torii-1", "torii-2"],
            ["京都"],
        ),
        (
            "kyt003",
            "京都｜『一兰拉面』和『天下一品』哪家更值",
            "在京都吃了 5 家拉面馆，最爱『天下一品』总本店。\n"
            "必点：浓厚拉面 ¥870，汤底浓得能挂筷子。\n"
            "一兰更出名但更贵，本地人都去天下一品。",
            "ramen_review",
            ["ramen-1", "ramen-2"],
            ["京都"],
        ),
        (
            "tk001",
            "东京｜银座咖啡『茑屋书店 GINZA SIX』店",
            "东京最喜欢的一家咖啡书店。落地窗对着银座大街。\n"
            "推荐：浅烘单品 ¥780 + 限定芝士蛋糕。\n"
            "工作日下午 2-4 点最空。",
            "tokyo_cafe",
            ["tokyo-1", "cafe-1"],
            ["东京"],
        ),
        (
            "tk002",
            "东京｜筑地市场金枪鱼大腩",
            "筑地外市场的『鮨大』，6 点开门 9 点排队 1 小时起。\n"
            "套餐 ¥4500，10 贯，金枪鱼大腩入口即化。\n"
            "提示：现金，不接受刷卡。",
            "sushi_hunter",
            ["sushi-1", "tuna-1"],
            ["东京"],
        ),
        (
            "tk003",
            "东京｜涩谷 SHIBUYA SKY 黄昏机位",
            "涩谷天空 SHIBUYA SKY，46 楼露天展望台。\n"
            "黄金时段：日落前 30 分钟入场，能拍到城市灯火渐次亮起。\n"
            "门票 ¥2500，建议官网预约时段。",
            "skyline_pic",
            ["shibuya-1", "skyline-1"],
            ["东京"],
        ),
        (
            "sh001",
            "上海｜安福路咖啡地图｜10 家小众",
            "在安福路住了 3 年，整理一份本地人咖啡地图。\n"
            "Top 1：『O.P.S.』创意 espresso，人均 80。\n"
            "Top 2：『Manner』性价比之王，10 元自带杯减 5 元。\n"
            "Top 3：『% Arabica』五原路店，出片必去。",
            "shanghai_local",
            ["shanghai-1", "coffee-1"],
            ["上海"],
        ),
        (
            "sh002",
            "上海｜外滩 18 号『MR&MRS BUND』法餐",
            "情人节去过两次的店，景观位需提前 2 周订。\n"
            "套餐：人均 ¥880，6 道；周末加 ¥120。\n"
            "靠窗位置正对东方明珠，灯光秀 8 点准时。",
            "fine_dining",
            ["bund-1", "french-1"],
            ["上海"],
        ),
        (
            "hz001",
            "杭州｜龙井村喝茶｜避开御牌找『记记茶』",
            "龙井村本地人推荐，比御牌村安静。\n"
            "『记记茶』当家明前龙井 ¥600/斤，老板娘会现场炒。\n"
            "搭配：山核桃 + 桂花年糕，¥35/份。",
            "tea_addict",
            ["hangzhou-1", "tea-1"],
            ["杭州"],
        ),
        (
            "hz002",
            "杭州｜河坊街隐藏巷子的『记记馄饨坊』",
            "河坊街主路别吃，往北 200 米的小巷子里有家『记记馄饨坊』。\n"
            "必点：松茸鲜肉馄饨 ¥35，汤底用老母鸡 + 火腿吊。\n"
            "本地老饕的秘密基地。",
            "noodles_pro",
            ["wonton-1", "noodle-1"],
            ["杭州"],
        ),
        (
            "la001",
            "Los Angeles｜Venice Beach 周末市集",
            "Venice 周末市集，周六 10am-6pm。\n"
            "推荐：Sidewalk Cafe 早午餐人均 $35，Eggs Benedict 招牌。\n"
            "拍照机位：彩绘墙 + 滑板公园。",
            "la_weekend",
            ["la-1", "venice-1"],
            ["洛杉矶"],
        ),
        (
            "la002",
            "Los Angeles｜Griffith Observatory 看星",
            "格里菲斯天文台免费开放，建议日落前 1 小时上山。\n"
            "拍照机位：Hollywood Sign 远景 + 城市灯火 panorama。\n"
            "停车注意：山下停车场免费但满得早，建议 4 点前。",
            "stargazer",
            ["la-2", "griffith-1"],
            ["洛杉矶"],
        ),
        (
            "gz001",
            "广州｜越秀区『陶陶居』百年老店饮茶",
            "广州早茶天花板，要排号但很值。\n"
            "必点：荔湾艇仔粥 ¥18 + 流沙包 ¥28 + 凤爪 ¥22。\n"
            "建议：早上 7 点到，避开 10 点高峰。",
            "yumcha_master",
            ["guangzhou-1", "dimsum-1"],
            ["广州"],
        ),
        (
            "gz002",
            "广州｜珠江新城『JIA·上海菜』高端粤菜",
            "出差请客首选，环境氛围都到位。\n"
            "招牌：黑松露蒸蛋白 + 脆皮乳鸽 + 杨枝甘露。\n"
            "人均 ¥600，提前订靠窗位置看珠江。",
            "biz_dining",
            ["guangzhou-2", "cantonese-1"],
            ["广州"],
        ),
        (
            "pa001",
            "巴黎｜玛黑区『L'As du Fallafel』排队也要吃",
            "巴黎玛黑区中东街上的传奇店，排队 30 分钟起。\n"
            "招牌：Falafel Special €11，分量大到吃不完。\n"
            "店里座位少，建议外带去孚日广场吃。",
            "paris_eats",
            ["paris-1", "falafel-1"],
            ["巴黎"],
        ),
    ]
    notes: list[dict[str, Any]] = []
    # For 3 representative notes we replace the 1st image with a synthetic
    # menu/poster image so the OCR pipeline has real text to recognise.
    OCR_DEMO = {
        "kyt001": "kyoto-menu.png",
        "tk001": "tokyo-cafe.png",
        "la001": "la-venice.png",
    }
    for i, (nid, title, body, author, img_seeds, _hint) in enumerate(samples):
        images = [_img(s) for s in img_seeds]
        if nid in OCR_DEMO and images:
            images[0] = _sample_url(OCR_DEMO[nid])
        cover = images[0] if images else None
        notes.append({
            "id": nid,
            "url": f"https://www.xiaohongshu.com/explore/{nid}",
            "title": title,
            "body": body,
            "author": author,
            "cover": cover,
            "images": images,
            "saved_at": (base - timedelta(days=i * 3)).isoformat(),
            "published_at": (base - timedelta(days=i * 3 + 5)).isoformat(),
        })
    return notes


def seed(store: KnowledgeStore) -> int:
    notes = _sample_notes()
    for n in notes:
        ingest_note(store, n)
    return len(notes)
