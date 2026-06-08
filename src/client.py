"""国家法律法规数据库 API 客户端

与 https://flk.npc.gov.cn 的 JSON API 交互，
获取法律分类、元数据和最新立法信息。
"""

import time
import json
import requests
from pathlib import Path
from typing import Optional, Dict, List

BASE_URL = "https://flk.npc.gov.cn"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
}


class NPCClient:
    """国家法律法规数据库 JSON API 客户端"""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.cache_dir = cache_dir or Path(".cache/npc")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._delay = 1.0  # 请求间隔（礼貌性延迟）

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict = None) -> dict:
        """带错误处理的 GET 请求"""
        url = f"{BASE_URL}{path}"
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            raise RuntimeError(
                f"API 返回错误 (code={data.get('code')}): {data.get('msg')}"
            )
        time.sleep(self._delay)
        return data.get("data", {})

    def _post(self, path: str, body: dict) -> dict:
        """带错误处理的 POST 请求"""
        url = f"{BASE_URL}{path}"
        resp = self.session.post(url, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            raise RuntimeError(
                f"API 返回错误 (code={data.get('code')}): {data.get('msg')}"
            )
        time.sleep(self._delay)
        return data.get("data", {})

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def get_aggregate_data(self) -> dict:
        """获取首页汇总数据：分类统计 + 最新法律（新法速递）

        返回：
            flflCount: [{"key": "法律", "count": 310}, ...]
            xfsd: [{"bbbs": "id", "title": "名称", "gbrq": "日期", "flxz": "类型"}, ...]
        """
        return self._get("/law-search/index/aggregateData")

    def get_category_tree(self) -> dict:
        """获取完整分类树（flfgfl）和制定机关树（zdjgfl）

        返回：
            flfgfl: 法律法规分类树
            zdjgfl: 制定机关分类树
            sxx: 时效性选项
        """
        return self._get("/law-search/search/enumData")

    def get_law_detail(self, law_id: str) -> dict:
        """获取单部法律的完整元数据和条文内容树

        参数：
            law_id: 法律的唯一标识符（bbbs）

        返回：
            title, flxz (法律类型), zdjgName (制定机关),
            gbrq (公布日期), sxrq (施行日期), sxx (状态码),
            ossFile (文件路径), content (条文结构树)
        """
        return self._get("/law-search/search/flfgDetails", {"bbbs": law_id})

    def get_latest_laws(self, limit: int = 30) -> List[dict]:
        """获取最新立法列表（新法速递）"""
        data = self.get_aggregate_data()
        items = data.get("xfsd", [])
        return items[:limit]

    def get_category_counts(self) -> Dict[str, int]:
        """获取各类别法律数量统计"""
        data = self.get_aggregate_data()
        return {
            item["key"]: item["count"]
            for item in data.get("flflCount", [])
        }

    def get_law_detail_batch(self, law_ids: List[str]) -> List[dict]:
        """批量获取法律详情"""
        results = []
        for i, lid in enumerate(law_ids):
            try:
                detail = self.get_law_detail(lid)
                results.append(detail)
            except Exception as e:
                print(f"  ⚠️ 获取 {lid} 失败: {e}")
            # 每 10 个额外休息一下
            if (i + 1) % 10 == 0:
                time.sleep(2)
        return results

    # 各省/自治区/直辖市/省会关键词
    LOCAL_KEYWORDS = [
        "省", "市", "自治区", "自治州", "自治县", "经济特区",
        "北京", "上海", "天津", "重庆",
        "广东", "广州", "深圳", "江苏", "南京", "浙江", "杭州",
        "山东", "济南", "青岛", "河南", "郑州", "河北", "石家庄",
        "四川", "成都", "湖北", "武汉", "湖南", "长沙", "福建", "厦门",
        "安徽", "合肥", "辽宁", "沈阳", "大连", "陕西", "西安",
        "江西", "南昌", "广西", "南宁", "云南", "昆明", "贵州", "贵阳",
        "山西", "太原", "吉林", "长春", "黑龙江", "哈尔滨",
        "内蒙古", "新疆", "西藏", "宁夏", "甘肃", "兰州", "青海", "海南",
        "浦东", "海南自由贸易港",
    ]

    def get_national_latest(self, limit: int = 50) -> List[dict]:
        """获取最新国家层面立法（排除地方法规）

        只保留: 法律, 行政法规, 司法解释, 监察法规, 部门规章
        排除: 地方法规, 地方性法规, 修改/废止的决定
        """
        all_latest = self.get_latest_laws(limit=limit)
        national = []
        for law in all_latest:
            law_type = law.get("flxz", "")
            title = law.get("title", "")
            # 排除地方法规
            if "地方" in law_type:
                continue
            # 排除标题中含地方关键词的（如 XX省XX条例）
            if any(kw in title for kw in self.LOCAL_KEYWORDS):
                continue
            # 排除纯修改/废止的决定
            if "修改" in law_type and "地方" in title:
                continue
            national.append(law)
        return national

    def compare_with_local(self, local_titles: set, limit: int = 50) -> List[dict]:
        """比对 NPC 最新立法与本地库，找出缺失的法律

        参数:
            local_titles: 本地已有的法律标题集合
            limit: 检查的最新法律数量

        返回: 本地缺失的法律列表
        """
        national = self.get_national_latest(limit=limit)
        missing = []
        for law in national:
            title = law.get("title", "")
            # 模糊匹配：本地标题包含法名或法名包含本地标题
            found = False
            for t in local_titles:
                if title in t or t in title:
                    found = True
                    break
            if not found:
                missing.append(law)
        return missing


# ------------------------------------------------------------------
# 状态码映射（来自 NPC 数据库 sxx 字段）
# ------------------------------------------------------------------
STATUS_MAP = {
    1: "尚未施行",
    2: "试行",
    3: "现行有效",
    4: "已被修改",
    5: "废止或失效",
    6: "部分失效",
    7: "部分有效",
}
