import streamlit as st
import pandas as pd
import time
import requests
import hashlib
import hmac
import base64
import io
import re
import zipfile
from collections import Counter


# =========================================================
# 页面设置
# =========================================================
APP_VERSION = "来源数 + 黄金词评分 + 来源关系保留版 v8"

st.set_page_config(page_title="Naver 快速挖词", layout="wide")


# =========================================================
# API 配置：只从 Streamlit Secrets 读取
#
# Streamlit Cloud → App settings → Secrets 里填写：
#
# API_KEY = "你的 Naver API Key"
# SECRET_KEY = "你的 Naver Secret Key"
# CUSTOMER_ID = "你的 Customer ID"
#
# 也兼容下面这种命名：
#
# NAVER_API_KEY = "你的 Naver API Key"
# NAVER_SECRET_KEY = "你的 Naver Secret Key"
# NAVER_CUSTOMER_ID = "你的 Customer ID"
# =========================================================
def read_secret(*names: str) -> str:
    """从 Streamlit Secrets 读取配置，不在代码里保存任何真实 API 信息"""
    for name in names:
        try:
            value = st.secrets.get(name, "")
            if value:
                return str(value)
        except Exception:
            pass

    return ""


API_KEY = read_secret("API_KEY", "NAVER_API_KEY")
SECRET_KEY_TEXT = read_secret("SECRET_KEY", "NAVER_SECRET_KEY")
CUSTOMER_ID = read_secret("CUSTOMER_ID", "NAVER_CUSTOMER_ID")

API_URL = "https://api.searchad.naver.com/keywordstool"


# =========================================================
# 基础工具函数
# =========================================================
def safe_str(value) -> str:
    """安全转字符串，避免 None / NaN 变成脏文本"""
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    return str(value).strip()


def clean_for_api(keyword: str) -> str:
    """去掉空格，给 Naver API 使用，也用于关键词归一化"""
    return re.sub(r"\s+", "", safe_str(keyword))


def normalize_keyword_key(keyword: str) -> str:
    """用于去重/分组的关键词 key"""
    return clean_for_api(keyword).casefold()


def dedupe_preserve_order(values) -> list[str]:
    """按清洗后的 keyword key 去重，同时保留原顺序"""
    seen = set()
    result = []

    for value in values:
        text = safe_str(value)
        key = normalize_keyword_key(text)

        if not key:
            continue

        if key not in seen:
            seen.add(key)
            result.append(text)

    return result


def most_common_text(values, default: str = "-") -> str:
    """取出现次数最多的文本，比如竞争程度"""
    cleaned = [safe_str(v) for v in values if safe_str(v)]

    if not cleaned:
        return default

    return Counter(cleaned).most_common(1)[0][0]


def make_signature(method: str, uri: str, timestamp: str) -> str:
    """生成 Naver SearchAd API 签名"""
    secret_key_bytes = SECRET_KEY_TEXT.encode("utf-8")
    message = f"{timestamp}.{method}.{uri}".encode("utf-8")
    signature = hmac.new(secret_key_bytes, message, hashlib.sha256).digest()
    return base64.b64encode(signature).decode("utf-8")


def normalize_count(raw) -> int:
    """把 Naver 返回的搜索量转成整数"""
    if raw is None:
        return 0

    try:
        if pd.isna(raw):
            return 0
    except Exception:
        pass

    if isinstance(raw, int):
        return raw

    if isinstance(raw, float):
        return int(raw)

    if isinstance(raw, str):
        s = raw.strip().replace(",", "")

        # Naver 有时返回 "< 10"
        if s.startswith("<"):
            return 5

        # 兼容 "> 1000" 或 ">1000"
        if s.startswith(">"):
            nums = re.sub(r"\D", "", s)
            return int(nums) if nums else 0

        if s.isdigit():
            return int(s)

    return 0


# =========================================================
# Naver API 查询
# =========================================================
def get_related_keywords(main_keyword: str, retry: int = 3) -> list[dict]:
    """查询一个母词的相关关键词数据，并保留 main_keyword → rel_keyword 的来源关系"""
    query_kw = clean_for_api(main_keyword)

    if not query_kw:
        return [
            {
                "main_keyword": main_keyword,
                "rel_keyword": "",
                "is_core": "Y",
                "pc": 0,
                "mobile": 0,
                "total": 0,
                "competition": "-",
                "error": "Empty keyword",
            }
        ]

    last_error = ""

    for attempt in range(1, retry + 1):
        try:
            timestamp = str(int(time.time() * 1000))
            signature = make_signature("GET", "/keywordstool", timestamp)

            headers = {
                "X-Timestamp": timestamp,
                "X-API-KEY": API_KEY,
                "X-Customer": CUSTOMER_ID,
                "X-Signature": signature,
            }

            params = {
                "hintKeywords": query_kw,
                "showDetail": 1,
            }

            res = requests.get(API_URL, headers=headers, params=params, timeout=15)

            if not res.text or not res.text.strip():
                last_error = "Empty API response"
                time.sleep(1)
                continue

            if res.status_code != 200:
                last_error = f"HTTP {res.status_code}: {res.text[:150]}"
                time.sleep(1)
                continue

            data = res.json()

            if "keywordList" not in data or len(data["keywordList"]) == 0:
                return [
                    {
                        "main_keyword": main_keyword,
                        "rel_keyword": "",
                        "is_core": "Y",
                        "pc": 0,
                        "mobile": 0,
                        "total": 0,
                        "competition": "-",
                        "error": "No data",
                    }
                ]

            rows = []
            cleaned_main = clean_for_api(main_keyword)

            for item in data["keywordList"]:
                rel_kw = item.get("relKeyword", "")
                pc_raw = item.get("monthlyPcQcCnt", 0)
                mobile_raw = item.get("monthlyMobileQcCnt", 0)

                pc_num = normalize_count(pc_raw)
                mobile_num = normalize_count(mobile_raw)
                total = pc_num + mobile_num

                rows.append(
                    {
                        "main_keyword": main_keyword,
                        "rel_keyword": rel_kw,
                        "is_core": "Y" if clean_for_api(rel_kw) == cleaned_main else "N",
                        "pc": pc_num,
                        "mobile": mobile_num,
                        "total": total,
                        "competition": item.get("compIdx", "-"),
                        "error": "",
                    }
                )

            return rows

        except Exception as e:
            last_error = str(e)[:150]
            time.sleep(1)

    return [
        {
            "main_keyword": main_keyword,
            "rel_keyword": "",
            "is_core": "Y",
            "pc": 0,
            "mobile": 0,
            "total": 0,
            "competition": "-",
            "error": last_error or "Failed",
        }
    ]


# =========================================================
# 数据结构整理
# =========================================================
def ensure_raw_result_columns(df: pd.DataFrame) -> pd.DataFrame:
    """确保原始结果表字段齐全，避免筛选时报错"""
    required_columns = [
        "main_keyword",
        "rel_keyword",
        "is_core",
        "pc",
        "mobile",
        "total",
        "competition",
        "error",
    ]

    for col in required_columns:
        if col not in df.columns:
            df[col] = ""

    df["main_keyword"] = df["main_keyword"].apply(safe_str)
    df["rel_keyword"] = df["rel_keyword"].apply(safe_str)
    df["is_core"] = df["is_core"].apply(lambda x: safe_str(x) or "N")
    df["competition"] = df["competition"].apply(lambda x: safe_str(x) or "-")
    df["error"] = df["error"].apply(safe_str)

    df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0).astype(int)
    df["pc"] = pd.to_numeric(df["pc"], errors="coerce").fillna(0).astype(int)
    df["mobile"] = pd.to_numeric(df["mobile"], errors="coerce").fillna(0).astype(int)

    return df[required_columns]


def empty_aggregate_df() -> pd.DataFrame:
    columns = [
        "rel_keyword",
        "pc",
        "mobile",
        "total",
        "competition",
        "source_count",
        "source_keywords",
        "source_ratio",
        "source_coverage_rate",
        "keyword_value_score",
        "keyword_type",
        "is_seed_keyword",
        "is_core",
        "duplicate_row_count",
        "rel_key",
    ]
    return pd.DataFrame(columns=columns)


def ensure_aggregate_columns(df: pd.DataFrame) -> pd.DataFrame:
    """确保汇总表字段齐全"""
    required_columns = [
        "rel_keyword",
        "pc",
        "mobile",
        "total",
        "competition",
        "source_count",
        "source_keywords",
        "source_ratio",
        "source_coverage_rate",
        "keyword_value_score",
        "keyword_type",
        "is_seed_keyword",
        "is_core",
        "duplicate_row_count",
        "rel_key",
    ]

    for col in required_columns:
        if col not in df.columns:
            df[col] = ""

    for col in ["pc", "mobile", "total", "source_count", "keyword_value_score", "duplicate_row_count"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["source_coverage_rate"] = pd.to_numeric(df["source_coverage_rate"], errors="coerce").fillna(0.0)
    df["competition"] = df["competition"].apply(lambda x: safe_str(x) or "-")
    df["keyword_type"] = df["keyword_type"].apply(safe_str)
    df["source_keywords"] = df["source_keywords"].apply(safe_str)
    df["source_ratio"] = df["source_ratio"].apply(safe_str)
    df["is_seed_keyword"] = df["is_seed_keyword"].apply(lambda x: safe_str(x) or "N")
    df["is_core"] = df["is_core"].apply(lambda x: safe_str(x) or "N")
    df["rel_keyword"] = df["rel_keyword"].apply(safe_str)
    df["rel_key"] = df["rel_key"].apply(safe_str)

    return df[required_columns]


def classify_keyword(total: int, source_count: int, high_volume_threshold: int, high_source_threshold: int) -> str:
    """
    关键词类型：
    - 核心黄金词：搜索量高 + 来源数高
    - 大流量独立/属性词：搜索量高 + 来源数低
    - 高关联补充词：搜索量低 + 来源数高
    - 普通长尾词：搜索量低 + 来源数低
    """
    high_volume_threshold = max(0, int(high_volume_threshold))
    high_source_threshold = max(1, int(high_source_threshold))

    is_high_volume = int(total) >= high_volume_threshold
    is_high_source = int(source_count) >= high_source_threshold

    if is_high_volume and is_high_source:
        return "核心黄金词"
    if is_high_volume and not is_high_source:
        return "大流量独立/属性词"
    if not is_high_volume and is_high_source:
        return "高关联补充词"
    return "普通长尾词"


def aggregate_related_keywords(
    raw_df: pd.DataFrame,
    seed_keywords: list[str],
    high_volume_threshold: int = 1000,
    high_source_threshold: int = 2,
) -> pd.DataFrame:
    """
    把原始 main_keyword → rel_keyword 关系表，汇总成关键词池。

    核心逻辑：
    1. 同一个 rel_keyword 只保留一条汇总记录；
    2. source_count = 这个 rel_keyword 被多少个不同母词扩展出来；
    3. source_keywords = 它来自哪些母词；
    4. keyword_value_score = 月搜索量 × 来源数；
    5. 月搜索量不重复相加，取同一关键词在不同来源中的最大值，避免重复放大搜索量。
    """
    df = ensure_raw_result_columns(raw_df.copy())

    if df.empty:
        return empty_aggregate_df()

    valid_df = df[df["rel_keyword"].apply(lambda x: bool(normalize_keyword_key(x)))].copy()

    if valid_df.empty:
        return empty_aggregate_df()

    valid_df["rel_key"] = valid_df["rel_keyword"].apply(normalize_keyword_key)

    clean_seed_keywords = dedupe_preserve_order(seed_keywords)
    seed_count = len(clean_seed_keywords)
    seed_keys = {normalize_keyword_key(k) for k in clean_seed_keywords if normalize_keyword_key(k)}

    rows = []

    for rel_key, group in valid_df.groupby("rel_key", sort=False):
        group = group.copy()

        # 展示关键词：同一个 rel_key 可能有轻微格式差异，优先选搜索量最高的展示名
        display_group = group.sort_values(by=["total", "mobile", "pc"], ascending=False)
        rel_keyword = safe_str(display_group.iloc[0]["rel_keyword"]) if not display_group.empty else rel_key

        source_keywords_list = dedupe_preserve_order(group["main_keyword"].tolist())
        source_count = len(source_keywords_list)

        pc = int(group["pc"].max()) if not group.empty else 0
        mobile = int(group["mobile"].max()) if not group.empty else 0
        total = int(group["total"].max()) if not group.empty else 0

        competition = most_common_text(group["competition"].tolist(), default="-")
        is_core = "Y" if (group["is_core"].astype(str).str.upper() == "Y").any() else "N"
        is_seed_keyword = "Y" if rel_key in seed_keys else "N"

        source_coverage_rate = round((source_count / seed_count) * 100, 2) if seed_count else 0.0
        source_ratio = f"{source_count}/{seed_count}" if seed_count else str(source_count)

        keyword_value_score = int(total) * int(source_count)

        keyword_type = classify_keyword(
            total=total,
            source_count=source_count,
            high_volume_threshold=high_volume_threshold,
            high_source_threshold=high_source_threshold,
        )

        rows.append(
            {
                "rel_keyword": rel_keyword,
                "pc": pc,
                "mobile": mobile,
                "total": total,
                "competition": competition,
                "source_count": source_count,
                "source_keywords": ", ".join(source_keywords_list),
                "source_ratio": source_ratio,
                "source_coverage_rate": source_coverage_rate,
                "keyword_value_score": keyword_value_score,
                "keyword_type": keyword_type,
                "is_seed_keyword": is_seed_keyword,
                "is_core": is_core,
                "duplicate_row_count": int(len(group)),
                "rel_key": rel_key,
            }
        )

    result_df = pd.DataFrame(rows)
    result_df = ensure_aggregate_columns(result_df)

    result_df = result_df.sort_values(
        by=["keyword_value_score", "source_count", "total"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    return result_df


# =========================================================
# 导出格式
# =========================================================
def to_chinese_aggregate_df(df: pd.DataFrame) -> pd.DataFrame:
    """汇总表导出/展示：英文字段转中文字段"""
    df = ensure_aggregate_columns(df.copy())

    rename_map = {
        "rel_keyword": "关键词",
        "pc": "PC搜索量",
        "mobile": "移动搜索量",
        "total": "月搜索量",
        "competition": "NAVER广告竞争",
        "source_count": "来源数",
        "source_keywords": "来源母词",
        "source_ratio": "来源覆盖",
        "source_coverage_rate": "来源覆盖率%",
        "keyword_value_score": "黄金词评分",
        "keyword_type": "关键词类型",
        "is_seed_keyword": "是否输入母词",
        "is_core": "是否API母词匹配",
        "duplicate_row_count": "原始出现次数",
    }

    df = df.drop(columns=["rel_key"], errors="ignore").rename(columns=rename_map)

    ordered_cols = [
        "关键词",
        "月搜索量",
        "来源数",
        "黄金词评分",
        "关键词类型",
        "来源母词",
        "来源覆盖",
        "来源覆盖率%",
        "PC搜索量",
        "移动搜索量",
        "NAVER广告竞争",
        "是否输入母词",
        "是否API母词匹配",
        "原始出现次数",
    ]

    return df[[col for col in ordered_cols if col in df.columns]]


def to_chinese_raw_df(df: pd.DataFrame) -> pd.DataFrame:
    """原始来源关系表导出/展示：英文字段转中文字段"""
    df = ensure_raw_result_columns(df.copy())

    rename_map = {
        "main_keyword": "母词",
        "rel_keyword": "关键词",
        "is_core": "是否母词",
        "pc": "PC搜索量",
        "mobile": "移动搜索量",
        "total": "月搜索量",
        "competition": "NAVER广告竞争",
        "error": "错误信息",
    }

    df = df.rename(columns=rename_map)

    ordered_cols = [
        "母词",
        "关键词",
        "是否母词",
        "PC搜索量",
        "移动搜索量",
        "月搜索量",
        "NAVER广告竞争",
        "错误信息",
    ]

    return df[[col for col in ordered_cols if col in df.columns]]


def make_excel_bytes(df_aggregate: pd.DataFrame, df_raw: pd.DataFrame) -> bytes:
    """生成 Excel 文件，包含：黄金词汇总 + 原始来源关系"""
    output = io.BytesIO()

    agg_export = to_chinese_aggregate_df(df_aggregate)
    raw_export = to_chinese_raw_df(df_raw)

    try:
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            agg_export.to_excel(writer, index=False, sheet_name="黄金词汇总")
            raw_export.to_excel(writer, index=False, sheet_name="原始来源关系")
    except ModuleNotFoundError:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            agg_export.to_excel(writer, index=False, sheet_name="黄金词汇总")
            raw_export.to_excel(writer, index=False, sheet_name="原始来源关系")

    return output.getvalue()


def make_csv_bytes(df: pd.DataFrame) -> bytes:
    """生成 CSV 文件。utf-8-sig 对中文、韩文、Excel 更友好"""
    return df.to_csv(index=False).encode("utf-8-sig")


def make_ai_batch_zip_bytes(df_aggregate: pd.DataFrame, batch_size: int = 500) -> tuple[bytes, int, int]:
    """
    生成 AI 分析用分批 CSV 压缩包。
    - 以当前筛选后的黄金词汇总表为准。
    - 每 batch_size 条生成 1 个 CSV。
    - 使用 UTF-8-SIG，避免中文/韩文乱码。
    """
    df_ai = to_chinese_aggregate_df(df_aggregate.copy())

    keep_cols = [
        "关键词",
        "月搜索量",
        "来源数",
        "黄金词评分",
        "关键词类型",
        "来源母词",
        "来源覆盖",
        "来源覆盖率%",
        "PC搜索量",
        "移动搜索量",
        "NAVER广告竞争",
        "是否输入母词",
        "是否API母词匹配",
        "原始出现次数",
    ]
    df_ai = df_ai[[col for col in keep_cols if col in df_ai.columns]]

    total_rows = len(df_ai)
    batch_count = (total_rows + batch_size - 1) // batch_size if total_rows else 1

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        readme = (
            "AI分析用黄金关键词分批CSV说明\n"
            "============================\n"
            f"总关键词数：{total_rows}\n"
            f"每个CSV最大关键词数：{batch_size}\n"
            f"CSV文件数量：{batch_count}\n\n"
            "字段解释：\n"
            "1. 关键词：Naver API 返回的关联关键词。\n"
            "2. 月搜索量：PC搜索量 + 移动搜索量；同一关键词被多个母词关联时，不重复累加。\n"
            "3. 来源数：同一个关键词被多少个不同输入母词关联出来。\n"
            "4. 来源母词：这个关键词分别来自哪些输入母词。\n"
            "5. 黄金词评分：月搜索量 × 来源数，用于排序优先级，不代表绝对投放结果。\n"
            "6. 关键词类型：按搜索量阈值和来源数阈值自动分类。\n\n"
            "分析建议：\n"
            "- 高搜索量 + 高来源数：优先看，通常更接近类目核心词。\n"
            "- 高搜索量 + 低来源数：可能是大流量属性词或独立类目词。\n"
            "- 低搜索量 + 高来源数：可能是类目强相关补充词。\n"
            "- 低搜索量 + 低来源数：普通长尾词，适合补充覆盖。\n"
        )
        zip_file.writestr("使用说明.txt", readme.encode("utf-8-sig"))

        if total_rows == 0:
            empty_csv = df_ai.to_csv(index=False).encode("utf-8-sig")
            zip_file.writestr("AI分析用黄金关键词_第001批_共001批_空数据.csv", empty_csv)
        else:
            for batch_index, start in enumerate(range(0, total_rows, batch_size), start=1):
                end = min(start + batch_size, total_rows)
                batch_df = df_ai.iloc[start:end].copy()

                batch_df.insert(0, "批次", f"{batch_index}/{batch_count}")
                batch_df.insert(1, "原始序号", range(start + 1, end + 1))

                csv_bytes = batch_df.to_csv(index=False).encode("utf-8-sig")
                csv_name = f"AI分析用黄金关键词_第{batch_index:03d}批_共{batch_count:03d}批_{start + 1}-{end}.csv"
                zip_file.writestr(csv_name, csv_bytes)

    zip_buffer.seek(0)
    return zip_buffer.getvalue(), batch_count, total_rows


# =========================================================
# Session State
# =========================================================
if "data" not in st.session_state:
    st.session_state.data = None

if "keyword_text" not in st.session_state:
    st.session_state.keyword_text = ""

if "last_file_timestamp" not in st.session_state:
    st.session_state.last_file_timestamp = ""

if "input_keywords" not in st.session_state:
    st.session_state.input_keywords = []


def clear_all():
    st.session_state.data = None
    st.session_state.keyword_text = ""
    st.session_state.last_file_timestamp = ""
    st.session_state.input_keywords = []


# =========================================================
# 页面 UI
# =========================================================
st.title("🇰🇷 Naver 关键词挖掘工具")
st.caption(f"当前版本：{APP_VERSION}")
st.markdown(
    "输入关键词，每行一个。这个版本会保留「母词 → 关联词」关系，并自动计算："
    "**来源数、来源母词、黄金词评分、关键词类型**。"
)

st.info(
    "核心逻辑：同一个关联关键词如果被多个不同母词同时扩展出来，说明它和这个类目的关联中心度更高。"
    "本工具用「来源数」记录这种重复关联，用「黄金词评分 = 月搜索量 × 来源数」做优先级排序。"
)

if not API_KEY or not SECRET_KEY_TEXT or not CUSTOMER_ID:
    st.error("缺少 API 配置。请在 Streamlit Cloud 的 Secrets 里添加 API_KEY、SECRET_KEY、CUSTOMER_ID。")
    st.code(
        """
API_KEY = "你的 Naver API Key"
SECRET_KEY = "你的 Naver Secret Key"
CUSTOMER_ID = "你的 Customer ID"
""".strip(),
        language="toml",
    )
    st.stop()


input_text = st.text_area(
    "请输入关键词（每行一个）",
    height=160,
    key="keyword_text",
    placeholder="例如：\n노트북거치대\n자전거거치대\n아이패드거치대",
)

col_start, col_clear = st.columns([1, 1])

with col_start:
    start_clicked = st.button("开始查询 🚀", type="primary", use_container_width=True)

with col_clear:
    st.button("清空结果", use_container_width=True, on_click=clear_all)


if start_clicked:
    if not input_text.strip():
        st.warning("请先输入关键词。")
    else:
        input_lines = [line.strip() for line in input_text.splitlines() if line.strip()]

        # 这里按清洗后的 key 去重，避免同一个母词重复输入后，虚增来源数
        keywords = dedupe_preserve_order(input_lines)

        st.info(f"正在查询 {len(keywords)} 个去重后的母词...")

        progress_bar = st.progress(0)
        status_text = st.empty()
        all_rows = []

        for i, keyword in enumerate(keywords):
            status_text.text(f"正在处理：{keyword} ({i + 1}/{len(keywords)})")
            all_rows.extend(get_related_keywords(keyword))
            progress_bar.progress((i + 1) / len(keywords))

            # 避免请求太密集
            time.sleep(0.3)

        status_text.text("查询完成！")
        progress_bar.progress(1.0)

        df_result = pd.DataFrame(all_rows)
        df_result = ensure_raw_result_columns(df_result)

        st.session_state.data = df_result
        st.session_state.input_keywords = keywords
        st.session_state.last_file_timestamp = time.strftime("%Y%m%d_%H%M%S")

        st.success("🎉 查询完成！")


# =========================================================
# 结果展示、筛选、下载
# =========================================================
if st.session_state.data is not None:
    df_raw_all = ensure_raw_result_columns(st.session_state.data.copy())
    seed_keywords = st.session_state.input_keywords or []

    st.divider()
    st.markdown("### ⚙️ 黄金词计算设置")

    seed_count = len(seed_keywords)
    default_high_source = 2 if seed_count >= 2 else 1

    setting_col1, setting_col2, setting_col3 = st.columns([1, 1, 2])

    with setting_col1:
        high_source_threshold = st.number_input(
            "分类：高来源数阈值",
            min_value=1,
            max_value=max(1, seed_count),
            value=min(default_high_source, max(1, seed_count)),
            step=1,
            help="来源数达到这个值，才会被认为是高关联词。输入母词较少时建议设为 2；母词很多时可以设为 3 或更高。",
        )

    with setting_col2:
        high_volume_threshold = st.number_input(
            "分类：高搜索量阈值",
            min_value=0,
            value=1000,
            step=100,
            help="月搜索量达到这个值，才会被认为是高搜索量词。",
        )

    with setting_col3:
        st.caption(
            "关键词类型由两个阈值决定："
            "高搜索量 + 高来源数 = 核心黄金词；"
            "高搜索量 + 低来源数 = 大流量独立/属性词；"
            "低搜索量 + 高来源数 = 高关联补充词。"
        )

    df_aggregate_all = aggregate_related_keywords(
        raw_df=df_raw_all,
        seed_keywords=seed_keywords,
        high_volume_threshold=int(high_volume_threshold),
        high_source_threshold=int(high_source_threshold),
    )

    st.markdown("### 📊 查询概览")

    valid_raw_count = int(df_raw_all["rel_keyword"].apply(lambda x: bool(normalize_keyword_key(x))).sum())
    multi_source_count = int((df_aggregate_all["source_count"] >= 2).sum()) if not df_aggregate_all.empty else 0
    core_gold_count = int((df_aggregate_all["keyword_type"] == "核心黄金词").sum()) if not df_aggregate_all.empty else 0

    metric_col1, metric_col2, metric_col3, metric_col4, metric_col5 = st.columns(5)
    metric_col1.metric("输入母词", seed_count)
    metric_col2.metric("原始关联行", valid_raw_count)
    metric_col3.metric("去重后关键词", len(df_aggregate_all))
    metric_col4.metric("多来源关键词", multi_source_count)
    metric_col5.metric("核心黄金词", core_gold_count)

    st.divider()
    st.markdown("### 🔍 结果筛选与导出")

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([1.4, 1, 1, 1])

    with filter_col1:
        keyword_filter = st.text_input(
            "关键词 / 来源母词包含",
            value="",
            placeholder="例如：거치대 / 노트북 / 스탠드",
        )

    with filter_col2:
        min_total = st.number_input(
            "最低搜索量",
            min_value=0,
            value=0,
            step=100,
        )

    with filter_col3:
        min_source_count = st.number_input(
            "最低来源数",
            min_value=1,
            max_value=max(1, seed_count),
            value=1,
            step=1,
        )

    with filter_col4:
        sort_label = st.selectbox(
            "排序方式",
            options=[
                "黄金词评分（推荐）",
                "来源数",
                "月搜索量",
                "来源覆盖率",
                "关键词A-Z",
            ],
            index=0,
        )

    filter_col5, filter_col6 = st.columns([1, 1])

    with filter_col5:
        comp_options = sorted(df_aggregate_all["competition"].fillna("-").astype(str).unique().tolist())
        selected_comp = st.multiselect(
            "竞争程度",
            options=comp_options,
            default=comp_options,
        )

    with filter_col6:
        type_options = sorted(df_aggregate_all["keyword_type"].fillna("-").astype(str).unique().tolist())
        selected_types = st.multiselect(
            "关键词类型",
            options=type_options,
            default=type_options,
        )

    df_view = df_aggregate_all.copy()

    if keyword_filter.strip():
        kw = keyword_filter.strip()
        mask_keyword = df_view["rel_keyword"].astype(str).str.contains(kw, case=False, na=False)
        mask_source = df_view["source_keywords"].astype(str).str.contains(kw, case=False, na=False)
        df_view = df_view[mask_keyword | mask_source]

    df_view = df_view[
        df_view["competition"].fillna("-").astype(str).isin(selected_comp)
        & df_view["keyword_type"].fillna("-").astype(str).isin(selected_types)
        & (df_view["total"].fillna(0).astype(int) >= int(min_total))
        & (df_view["source_count"].fillna(0).astype(int) >= int(min_source_count))
    ]

    sort_map = {
        "黄金词评分（推荐）": "keyword_value_score",
        "来源数": "source_count",
        "月搜索量": "total",
        "来源覆盖率": "source_coverage_rate",
        "关键词A-Z": "rel_keyword",
    }

    sort_col = sort_map.get(sort_label, "keyword_value_score")

    if sort_col == "rel_keyword":
        df_view = df_view.sort_values(by=["rel_keyword", "total"], ascending=[True, False]).reset_index(drop=True)
    else:
        df_view = df_view.sort_values(by=[sort_col, "total"], ascending=[False, False]).reset_index(drop=True)

    # 根据当前筛选后的汇总关键词，反查对应原始来源关系
    df_raw_with_key = df_raw_all.copy()
    df_raw_with_key["rel_key"] = df_raw_with_key["rel_keyword"].apply(normalize_keyword_key)
    matched_rel_keys = set(df_view["rel_key"].tolist()) if not df_view.empty else set()
    df_raw_view = df_raw_with_key[df_raw_with_key["rel_key"].isin(matched_rel_keys)].drop(columns=["rel_key"], errors="ignore")

    tab_summary, tab_raw, tab_error = st.tabs(["🏆 黄金词汇总表", "🔗 原始来源关系表", "⚠️ 错误记录"])

    with tab_summary:
        st.caption(
            "汇总表中，同一个关键词只保留一条；来源数表示它被多少个不同母词扩展出来。"
            "月搜索量不重复相加，避免同一个词被多个母词关联后搜索量被放大。"
        )
        st.dataframe(to_chinese_aggregate_df(df_view), use_container_width=True, height=460)

    with tab_raw:
        st.caption("原始来源关系表会保留每一条「母词 → 关联词」记录，用来追踪某个词到底来自哪些母词。")
        st.dataframe(to_chinese_raw_df(df_raw_view), use_container_width=True, height=460)

    with tab_error:
        df_error = df_raw_all[df_raw_all["error"].astype(str).str.strip() != ""].copy()
        if df_error.empty:
            st.success("没有错误记录。")
        else:
            st.dataframe(to_chinese_raw_df(df_error), use_container_width=True, height=300)

    st.markdown("---")

    file_timestamp = st.session_state.last_file_timestamp or time.strftime("%Y%m%d_%H%M%S")

    excel_data = make_excel_bytes(df_view, df_raw_view)
    aggregate_csv_data = make_csv_bytes(to_chinese_aggregate_df(df_view))
    raw_csv_data = make_csv_bytes(to_chinese_raw_df(df_raw_view))
    ai_zip_data, ai_batch_count, ai_total_rows = make_ai_batch_zip_bytes(df_view, batch_size=500)

    col_excel, col_agg_csv, col_raw_csv, col_ai_zip, col_count = st.columns([1.1, 1.2, 1.2, 1.6, 2.4])

    with col_excel:
        st.download_button(
            label="📥 下载 Excel",
            data=excel_data,
            file_name=f"naver_gold_keywords_{file_timestamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
            key="download_excel",
        )

    with col_agg_csv:
        st.download_button(
            label="🏆 下载汇总 CSV",
            data=aggregate_csv_data,
            file_name=f"naver_gold_keywords_summary_{file_timestamp}.csv",
            mime="text/csv",
            use_container_width=True,
            key="download_aggregate_csv",
        )

    with col_raw_csv:
        st.download_button(
            label="🔗 下载来源 CSV",
            data=raw_csv_data,
            file_name=f"naver_gold_keywords_sources_{file_timestamp}.csv",
            mime="text/csv",
            use_container_width=True,
            key="download_raw_csv",
        )

    with col_ai_zip:
        st.download_button(
            label="🤖 下载AI分批CSV ZIP",
            data=ai_zip_data,
            file_name=f"AI分析用黄金关键词分批_{file_timestamp}_{ai_total_rows}条_{ai_batch_count}份.zip",
            mime="application/zip",
            use_container_width=True,
            key="download_ai_zip",
        )

    with col_count:
        st.markdown(
            f"#### 📊 当前筛选后： <span style='color:red; font-size:1.2em'>{len(df_view)}</span> 个关键词",
            unsafe_allow_html=True,
        )
        st.caption(f"AI分批CSV：每500条一份，共 {ai_batch_count} 份。")

    st.markdown("### 🤖 给 AI 分析用")
    with st.expander("📋 展开复制 CSV 文本预览", expanded=False):
        st.caption("这里只显示当前筛选后黄金词汇总表的前500条。完整分批文件请点击“下载AI分批CSV ZIP”。")
        preview_df = to_chinese_aggregate_df(df_view.head(500))
        st.code(preview_df.to_csv(index=False), language="csv")

else:
    st.info("输入关键词后点击“开始查询”，查询完成后这里会显示结果和下载按钮。")
