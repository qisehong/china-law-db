"""国家法律法规数据库 (flk.npc.gov.cn) 直连客户端

新版 Vue SPA 的 JSON API（旧版 /api/ 已下线）：
  POST /law-search/search/list          按分类分页检索
  GET  /law-search/search/flfgDetails   单部法律详情（含 ossFile 元数据）
  POST /law-search/download/batch       官方批量下载 → 返回 OSS 预签名 URL
  GET  /law-search/search/enumData      分类树（codeId 体系）
  GET  /law-search/index/aggregateData  首页汇总（分类计数 + 新法速递）

分类 codeId 体系（来自 enumData，2026-09 实测）:
  100 宪法 | 101 法律(102法律/180解释/190决定/195修正案/200修改废止, 子类110-170)
  201 行政法规 | 220 监察法规 | 221 地方法规(222地方性法规/260自治条例/270单行条例/
  290经济特区/295浦东新区/300海南自贸港/305法规性决定/310修改废止) | 311 司法解释
"""

import time
import json
import requests
from pathlib import Path
from typing import Optional, Dict, List, Tuple

BASE_URL = "https://flk.npc.gov.cn"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/search",
    "X-Requested-With": "XMLHttpRequest",
}

# ------------------------------------------------------------------
# 分类体系：顶层分类 → 检索 codeId；叶子 codeId → 输出子目录
# ------------------------------------------------------------------

# 顶层分类（用于 cli --categories）
# code 必须传完整的 codeIdList（含全部子孙节点，
# 即 enumData 树中该节点的 codeIdList 字段），
# 父节点单独作为检索值时后端返回空结果（实测）。
TOP_CATEGORIES: Dict[str, dict] = {
    "宪法":     {"code": [100], "local": False},
    "法律":     {"code": [101, 102, 110, 120, 130, 140, 150, 155, 160, 170, 180, 190, 195, 200], "local": False},
    "行政法规": {"code": [201, 210, 215], "local": False},
    "监察法规": {"code": [220], "local": False},
    "司法解释": {"code": [311, 320, 330, 340, 350], "local": False},
    "地方法规": {"code": [221, 222, 230, 260, 270, 290, 295, 300, 305, 310], "local": True},   # 按省过滤，见 LOCAL_REGIONS
}

# 地方法规：按省检索（制定机关 zdjgCodeId 服务端过滤，官方全量 1.5 万+ 部）
# 注意 zdjg 码属于制定机关树(zdjgfl)，与 flfgfl 分类码互不相干：
#   上海=250，江苏=260，浙江=270（含省内设区的市人大，如南京/杭州/宁波）
LOCAL_REGIONS: Dict[str, dict] = {
    "上海": {"zdjg": [250], "code": [221, 222, 230, 260, 270, 290, 295, 300, 305, 310]},
    "江苏": {"zdjg": [260], "code": [221, 222, 230, 260, 270, 290, 295, 300, 305, 310]},
    "浙江": {"zdjg": [270], "code": [221, 222, 230, 260, 270, 290, 295, 300, 305, 310]},
}

# 叶子 codeId → (输出大类, 子目录或 None)
LEAF_MAP: Dict[int, Tuple[str, Optional[str]]] = {
    100: ("宪法", None),
    102: ("法律", None),
    110: ("法律", "宪法相关法"),
    120: ("法律", "民法商法"),
    130: ("法律", "行政法"),
    140: ("法律", "经济法"),
    150: ("法律", "社会法"),
    155: ("法律", "生态环境法"),
    160: ("法律", "刑法"),
    170: ("法律", "诉讼与非诉讼程序法"),
    180: ("法律", "法律解释"),
    190: ("法律", "有关法律问题和重大问题的决定"),
    195: ("法律", "修正案"),
    200: ("法律", "修改、废止的决定"),
    210: ("行政法规", None),
    215: ("行政法规", None),
    220: ("监察法规", None),
    222: ("地方法规", "地方性法规"),
    260: ("地方法规", "自治条例"),
    270: ("地方法规", "单行条例"),
    290: ("地方法规", "经济特区法规"),
    295: ("地方法规", "浦东新区法规"),
    300: ("地方法规", "海南自由贸易港法规"),
    305: ("地方法规", "法规性决定"),
    310: ("地方法规", "修改、废止的决定"),
    320: ("司法解释", None),
    330: ("司法解释", None),
    340: ("司法解释", None),
    350: ("司法解释", None),
}

LEAF_NAMES: Dict[int, str] = {
    100: "宪法", 102: "法律", 110: "宪法相关法", 120: "民法商法", 130: "行政法",
    140: "经济法", 150: "社会法", 155: "生态环境法", 160: "刑法",
    170: "诉讼与非诉讼程序法", 180: "法律解释", 190: "有关法律问题和重大问题的决定",
    195: "修正案", 200: "修改、废止的决定", 210: "行政法规", 215: "修改、废止的决定",
    220: "监察法规", 222: "地方性法规", 260: "自治条例", 270: "单行条例",
    290: "经济特区法规", 295: "浦东新区法规", 300: "海南自由贸易港法规",
    305: "法规性决定", 310: "修改、废止的决定",
    320: "高法司法解释", 330: "高检司法解释", 340: "联合发布司法解释", 350: "修改、废止的决定",
}

# 时效性状态码（详情接口 sxx 字段）
STATUS_MAP = {
    1: "尚未生效",
    2: "试行",
    3: "现行有效",
    4: "已被修改",
    5: "废止或失效",
    6: "部分失效",
    7: "部分有效",
}


class FlkClient:
    """国家法律法规数据库新版 API 客户端"""

    def __init__(self, delay: float = 1.0, page_size: int = 50):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.delay = delay
        self.page_size = page_size
        self._last_request = 0.0

    # ----------------------------------------------------------------
    # 基础请求（限速 + 重试）
    # ----------------------------------------------------------------

    def _throttle(self):
        gap = time.time() - self._last_request
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last_request = time.time()

    def _request(self, method: str, path: str, retries: int = 3, **kwargs) -> dict:
        url = f"{BASE_URL}{path}"
        last_err = None
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, json.JSONDecodeError) as e:
                last_err = e
                wait = 2 ** attempt * 2
                print(f"  ⚠️ 请求失败({attempt + 1}/{retries}) {path}: {e}，{wait}s 后重试")
                time.sleep(wait)
        raise RuntimeError(f"请求最终失败 {url}: {last_err}")

    # ----------------------------------------------------------------
    # 公开 API
    # ----------------------------------------------------------------

    def aggregate(self) -> dict:
        """首页汇总：分类计数 + 新法速递"""
        data = self._request("GET", "/law-search/index/aggregateData")
        return data.get("data", {})

    def get_category_counts(self) -> Dict[str, int]:
        agg = self.aggregate()
        return {i["key"]: i["count"] for i in agg.get("flflCount", [])}

    def get_latest_laws(self, limit: int = 30) -> List[dict]:
        agg = self.aggregate()
        return agg.get("xfsd", [])[:limit]

    def search_category(
        self,
        code_ids: List[int],
        page: int = 1,
        page_size: Optional[int] = None,
        sxx: Optional[List[int]] = None,
        zdjg_ids: Optional[List[int]] = None,
    ) -> Tuple[List[dict], int]:
        """按分类分页检索。zdjg_ids 可选制定机关过滤（地方法规按省）。返回 (rows, total)。"""
        body = {
            "searchRange": 1,
            "sxrq": [],
            "gbrq": [],
            "searchType": 2,
            "sxx": sxx or [],
            "gbrqYear": [],
            "flfgCodeId": code_ids,
            "zdjgCodeId": zdjg_ids or [],
            "searchContent": "",
            "orderByParam": {"order": "-1", "sort": ""},
            "pageNum": page,
            "pageSize": page_size or self.page_size,
        }
        data = self._request(
            "POST", "/law-search/search/list", json=body
        )
        return data.get("rows", []), data.get("total", 0)

    def search_all(
        self,
        code_ids: List[int],
        sxx: Optional[List[int]] = None,
        zdjg_ids: Optional[List[int]] = None,
        progress: bool = True,
    ):
        """遍历某分类下全部条目（生成器）。"""
        page = 1
        total = None
        while True:
            rows, total = self.search_category(code_ids, page=page, sxx=sxx, zdjg_ids=zdjg_ids)
            if not rows:
                break
            for row in rows:
                yield row
            if progress:
                done = page * self.page_size
                print(f"\r  📄 检索进度: {min(done, total)}/{total}", end="", flush=True)
            if page * self.page_size >= total:
                break
            page += 1
        if progress:
            print()

    def detail(self, bbbs: str) -> dict:
        """单部法律详情（元数据 + ossFile 路径）"""
        data = self._request(
            "GET", "/law-search/search/flfgDetails", params={"bbbs": bbbs}
        )
        return data.get("data", {})

    def batch_download_urls(
        self, bbbs_list: List[str], fmt: str = "docx"
    ) -> List[dict]:
        """官方批量下载接口，返回 [{bbbs?, url, urlIn}]（URL 有效期 1 小时）"""
        body = [{"bbbs": b, "format": fmt} for b in bbbs_list]
        data = self._request("POST", "/law-search/download/batch", json=body)
        return data.get("data", [])

    def download_file(self, url: str, dest: Path, retries: int = 3) -> Path:
        """下载预签名 URL 到本地（OSS 证书异常时自动降级不校验）"""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        last_err = None
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=60, verify=True, stream=True)
                if resp.status_code == 200:
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_content(65536):
                            f.write(chunk)
                    return dest
                last_err = RuntimeError(f"HTTP {resp.status_code}")
            except requests.exceptions.SSLError:
                # OSS 节点证书链在部分环境下不完整，降级不校验（仅下载公开法律文本）
                try:
                    resp = self.session.get(url, timeout=60, verify=False, stream=True)
                    if resp.status_code == 200:
                        with open(dest, "wb") as f:
                            for chunk in resp.iter_content(65536):
                                f.write(chunk)
                        return dest
                    last_err = RuntimeError(f"HTTP {resp.status_code}")
                except requests.RequestException as e:
                    last_err = e
            except requests.RequestException as e:
                last_err = e
            time.sleep(2 ** attempt * 2)
        raise RuntimeError(f"下载失败 {dest.name}: {last_err}")

    # ----------------------------------------------------------------
    # 辅助
    # ----------------------------------------------------------------

    @staticmethod
    def leaf_of(row: dict) -> Optional[int]:
        """从检索行的 flfgCodeId 取叶子分类码（接口可能返回 int 或数组）"""
        codes = row.get("flfgCodeId")
        if isinstance(codes, int):
            return codes
        codes = codes or []
        return codes[-1] if codes else None

    @staticmethod
    def output_relpath(row: dict) -> Optional[Tuple[str, Optional[str]]]:
        """行 → (输出大类, 子目录)，无法映射返回 None"""
        leaf = FlkClient.leaf_of(row)
        return LEAF_MAP.get(leaf)


def national_latest_filtered(client: FlkClient, limit: int = 50) -> List[dict]:
    """新法速递中剔除地方法规后的国家层面立法"""
    LOCAL_KEYWORDS = (
        "省", "市", "自治区", "自治州", "自治县", "经济特区",
        "浦东", "海南自由贸易港", "联盟", "协作",
    )
    result = []
    for law in client.get_latest_laws(limit=limit):
        if "地方" in law.get("flxz", ""):
            continue
        title = law.get("title", "")
        # 标题含地级行政区关键词的粗过滤（如"深圳经济特区…"）
        if law.get("flxz") == "地方法规":
            continue
        result.append(law)
    return result[:limit]
