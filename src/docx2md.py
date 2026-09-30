"""官方 docx 法律文本 → Markdown + YAML frontmatter 转换器

flk 官方 docx 的排版规律（实测《监察工作信息公开条例》等）：
  段1  标题
  段2  通过/公布信息（含"通过""公布"字样与日期）
  段3+ 条文正文（第X条 …，编/章/节标题独立成段）
  末段 施行日期（"本条例自XXXX年X月X日起施行"）

输出格式与仓库现有 laws/ 文件保持一致（YAML frontmatter + Markdown 正文）。
"""

import re
import json
import struct
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from docx import Document

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # 遗留 .doc (OLE2)

DATE_RE = re.compile(r"(19|20)\d{2}年\d{1,2}月\d{1,2}日")
BIAN_RE = re.compile(r"^第[一二三四五六七八九十百千零]+编(\s|　)*.*$")
ZHANG_RE = re.compile(r"^第[一二三四五六七八九十百千零]+章(\s|　)*.*$")
JIE_RE = re.compile(r"^第[一二三四五六七八九十百千零]+节(\s|　)*.*$")

# Windows 文件名非法字符
ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')


def _to_iso(cn_date_str: str) -> Optional[str]:
    """'2025年12月29日' → '2025-12-29'"""
    m = DATE_RE.search(cn_date_str)
    if not m:
        return None
    frag = m.group(0)
    y, mo, d = re.search(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日", frag
    ).groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def safe_filename(title: str) -> str:
    """标题 → 安全文件名"""
    name = ILLEGAL_CHARS.sub("", title).strip()
    return name or "未命名"


def _best_decode(raw: bytes) -> str:
    """按中日韩字符密度选择最优解码（utf-16-le / gbk）"""
    best_txt, best_score = "", -1
    for enc in ("utf-16-le", "gbk"):
        try:
            txt = raw.decode(enc, errors="ignore")
        except Exception:
            continue
        score = sum(1 for ch in txt if "\u4e00" <= ch <= "\u9fff")
        if score > best_score:
            best_txt, best_score = txt, score
    return best_txt


def _extract_doc_text_by_clx(wd: bytes, table: bytes) -> str:
    """按 Word 97 二进制规范的 piece table (CLX) 提取正文

    FIB.fcClx/lcbClx (偏移 0x01A2/0x01A6) → 表流中的 CLX 结构：
      [Prc: 0x01+cb+grpprl]*  Pcdt: 0x02 + lcb(4B) + PlcPcd
      PlcPcd = CPs[(n+1)*4B] + PCDs[n*8B]
      PCD.fc bit30=1 → 压缩片段（cp1252，偏移 fc/2）；否则 UTF-16LE（偏移 fc）
    """
    if len(table) == 0:
        raise ValueError("表流为空")
    fcClx, lcbClx = struct.unpack("<II", wd[0x01A2:0x01AA])
    if lcbClx <= 0 or fcClx + lcbClx > len(table):
        raise ValueError(f"CLX 越界 fcClx={fcClx} lcbClx={lcbClx}")
    clx = table[fcClx:fcClx + lcbClx]

    # 跳过 Prc 段，定位 Pcdt (0x02)
    i = 0
    pcdt = None
    while i < len(clx):
        tag = clx[i]
        if tag == 0x01:
            cb = struct.unpack("<H", clx[i+1:i+3])[0]
            i += 3 + cb
        elif tag == 0x02:
            lcb = struct.unpack("<I", clx[i+1:i+5])[0]
            pcdt = clx[i+5:i+5+lcb]
            break
        else:
            raise ValueError(f"CLX 非法标签 0x{tag:02x}")
    if not pcdt:
        raise ValueError("未找到 Pcdt")

    n_pcd = (len(pcdt) - 4) // 12
    if n_pcd <= 0:
        raise ValueError("PlcPcd 为空")
    cps = struct.unpack(f"<{n_pcd + 1}I", pcdt[:4 * (n_pcd + 1)])
    parts: List[str] = []
    for k in range(n_pcd):
        cch = cps[k+1] - cps[k]
        if cch <= 0:
            continue
        fc_raw = struct.unpack("<I", pcdt[4*(n_pcd+1) + 8*k + 2 : 4*(n_pcd+1) + 8*k + 6])[0]
        if fc_raw & 0x40000000:  # 压缩片段：单字节 cp1252，偏移 fc/2
            off = (fc_raw & 0x3FFFFFFF) // 2
            parts.append(wd[off:off+cch].decode("cp1252", errors="ignore"))
        else:                     # UTF-16LE
            off = fc_raw & 0x3FFFFFFF
            parts.append(wd[off:off+2*cch].decode("utf-16-le", errors="ignore"))
    return "".join(parts)


def extract_text_from_doc(path: Path) -> List[str]:
    """遗留 .doc (OLE2 Word 97-2003) 纯文本提取

    无系统依赖。优先按规范的 piece table (CLX) 定位正文（真正的 Word 97 文件），
    失败时回退 fcMin:fcMac 连续区段启发式（部分转换器生成的 .doc 有效）。
    """
    import olefile

    with olefile.OleFileIO(str(path)) as ole:
        wd = ole.openstream("WordDocument").read()
    if len(wd) < 0x20:
        raise ValueError("WordDocument 流过短")

    flags = struct.unpack("<H", wd[0x0A:0x0C])[0]
    tbl_name = "1Table" if flags & 0x0200 else "0Table"

    text = ""
    try:
        with olefile.OleFileIO(str(path)) as ole:
            streams = {"/".join(s) for s in ole.listdir()}
            table = ole.openstream(tbl_name).read() if tbl_name in streams else b""
        text = _extract_doc_text_by_clx(wd, table)
    except Exception:
        # 回退：连续存储区段（部分非 Word 生成器保留 fcMin/fcMac 有效值）
        fcMin, fcMac = struct.unpack("<II", wd[0x18:0x20])
        if not (0 < fcMin < fcMac <= len(wd)):
            raise ValueError(f"CLX 提取失败且 fcMin/fcMac 非法: {fcMin}/{fcMac}")
        text = _best_decode(wd[fcMin:fcMac])

    if sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff") < 30:
        raise ValueError(".doc 正文提取过短，疑似加密或扫描件")

    # 清理控制字符：\x07 表格单元格分隔符 → 全角空格；退格/字段符等剔除
    text = text.replace("\x07", "　")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x13\x14\x15]", "", text)
    paras = [p.strip() for p in re.split(r"[\r\n]+", text) if p.strip()]
    # 剔除公文头噪音（页码、密级、缓急等独立短行）
    noise = re.compile(r"^(\d{4,8}|机密.*|特急|加急|紧急|第\s*\d+\s*页|共\s*\d+\s*页)$")
    paras = [p for p in paras if not noise.match(p)]
    return paras
    if sum(1 for p in paras if "\u4e00" <= p[0] <= "\u9fff") < 3:
        raise ValueError(".doc 正文提取过短，疑似复杂格式")
    return paras


def _clean_para(s: str) -> str:
    """段内清洗：软换行/回车折叠为空（中文法律文本不依赖空白），去首尾空白"""
    return re.sub(r"[\r\n\u000b\u000c]+", "", s).strip()


def _yaml_str(s: str) -> str:
    """字符串 → 合法 YAML 双引号标量（JSON 字符串语法是合法 YAML）"""
    return json.dumps(s, ensure_ascii=False)


def parse_docx(path: Path) -> Dict:
    """解析官方 docx/doc → {title, body_md, publish_date, implement_date}"""
    if Path(path).read_bytes()[:8] == OLE2_MAGIC:
        paras = extract_text_from_doc(Path(path))
    else:
        doc = Document(str(path))
        paras = [p.text.strip().replace("\u3000", "　") for p in doc.paragraphs]
        paras = [p for p in paras if p]
    paras = [_clean_para(p) for p in paras]
    paras = [p for p in paras if p]

    meta: Dict = {
        "title": None,
        "body_md": "",
        "publish_date": None,
        "implement_date": None,
    }
    if not paras:
        return meta

    # --- 标题：第一个非空段落 ---
    meta["title"] = paras[0].strip()

    # --- 日期信息：扫描前 8 段 ---
    for p in paras[:8]:
        iso = _to_iso(p)
        if not iso:
            continue
        if "施行" in p:
            meta["implement_date"] = iso
        elif ("通过" in p or "公布" in p) and not meta["publish_date"]:
            meta["publish_date"] = iso
    # 兜底：头部第一个日期当公布日期
    if not meta["publish_date"]:
        for p in paras[:6]:
            iso = _to_iso(p)
            if iso:
                meta["publish_date"] = iso
                break

    # --- 正文：标题后的所有段落 → Markdown ---
    body_lines: List[str] = []
    for p in paras[1:]:
        if BIAN_RE.match(p):
            body_lines.append(f"## {p}")
        elif ZHANG_RE.match(p):
            body_lines.append(f"### {p}")
        elif JIE_RE.match(p):
            body_lines.append(f"#### {p}")
        else:
            body_lines.append(p)
    meta["body_md"] = "\n\n".join(body_lines)

    return meta


def generate_frontmatter(
    meta: Dict,
    category: str,
    subcategory: Optional[str] = None,
    status: str = "现行有效",
    npc_status: Optional[str] = None,
    issuing_authority: Optional[str] = None,
    api_publish_date: Optional[str] = None,
) -> str:
    """生成与现有 laws/ 一致的 YAML frontmatter

    api_publish_date: 官方 API 返回的公布日期(gbrq)，优先于 docx 解析结果
    （docx 段落中的日期可能是“通过”日期，与官方口径的公布日期不一致）
    """
    if npc_status:
        status = npc_status
    if api_publish_date:
        meta = dict(meta, publish_date=api_publish_date)
    title = meta.get("title") or "未知法律法规"
    tags = ["法律法规", category]
    if subcategory:
        tags.append(subcategory)

    fm = ["---"]
    fm.append(f"title: {_yaml_str(title)}")
    if meta.get("publish_date"):
        fm.append(f'publish_date: "{meta["publish_date"]}"')
    if meta.get("implement_date"):
        fm.append(f'implement_date: "{meta["implement_date"]}"')
    fm.append(f"status: {_yaml_str(status)}")
    fm.append(f"category: {_yaml_str(category)}")
    if subcategory:
        fm.append(f"subcategory: {_yaml_str(subcategory)}")
    if issuing_authority:
        fm.append(f"issuing_authority: {_yaml_str(issuing_authority)}")
    fm.append('source: "国家法律法规数据库 (flk.npc.gov.cn)"')
    fm.append(f'synced: "{datetime.now().strftime("%Y-%m-%d")}"')
    fm.append("tags:")
    for t in tags:
        fm.append(f"  - {t}")
    fm.append("---")
    fm.append("")
    return "\n".join(fm)


def convert_docx_to_law_md(
    docx_path: Path,
    category: str,
    subcategory: Optional[str] = None,
    npc_status: Optional[str] = None,
    issuing_authority: Optional[str] = None,
    api_publish_date: Optional[str] = None,
) -> Dict:
    """docx → 完整 Markdown 文件内容

    返回: {"content": str, "title": str, "publish_date": str|None}
    """
    meta = parse_docx(docx_path)
    fm = generate_frontmatter(
        meta, category, subcategory, npc_status=npc_status,
        issuing_authority=issuing_authority,
        api_publish_date=api_publish_date,
    )
    content = fm + "\n" + meta["body_md"] + "\n"
    return {
        "content": content,
        "title": meta.get("title") or safe_filename(Path(docx_path).stem),
        "publish_date": meta.get("publish_date"),
    }
