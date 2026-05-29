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


# =========================================================
# 页面设置
# =========================================================
APP_VERSION = "手动输入 + Streamlit Secrets + Excel/CSV + AI分批CSV压缩包版 v7"

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
# 工具函数
# =========================================================
def clean_for_api(keyword: str) -> str:
    """去掉空格，给 Naver API 使用"""
    return re.sub(r"\s+", "", str(keyword))


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


def get_related_keywords(main_keyword: str, retry: int = 3) -> list[dict]:
    """查询一个关键词的相关关键词数据"""
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


def make_excel_bytes(df: pd.DataFrame) -> bytes:
    """生成 Excel 文件"""
    output = io.BytesIO()

    try:
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="result")
    except ModuleNotFoundError:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="result")

    return output.getvalue()


def make_csv_bytes(df: pd.DataFrame) -> bytes:
    """生成 CSV 文件。utf-8-sig 对中文、韩文、Excel 更友好"""
    return df.to_csv(index=False).encode("utf-8-sig")


def make_ai_batch_zip_bytes(df: pd.DataFrame, batch_size: int = 500) -> tuple[bytes, int, int]:
    """
    生成 AI 分析用分批 CSV 压缩包。
    - 以当前筛选后的结果为准。
    - 每 batch_size 条生成 1 个 CSV。
    - 使用 UTF-8-SIG，避免中文/韩文乱码。
    - 表头改为中文，方便 GPTs / Project 直接分析。
    """
    df_ai = ensure_result_columns(df.copy())

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

    df_ai = df_ai.rename(columns=rename_map)

    keep_cols = [
        "母词",
        "关键词",
        "是否母词",
        "PC搜索量",
        "移动搜索量",
        "月搜索量",
        "NAVER广告竞争",
        "错误信息",
    ]
    df_ai = df_ai[[col for col in keep_cols if col in df_ai.columns]]

    total_rows = len(df_ai)
    batch_count = (total_rows + batch_size - 1) // batch_size if total_rows else 1

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        readme = (
            "AI分析用分批CSV说明\n"
            "====================\n"
            f"总词条数：{total_rows}\n"
            f"每个CSV最大词条数：{batch_size}\n"
            f"CSV文件数量：{batch_count}\n\n"
            "说明：\n"
            "1. 本压缩包按当前页面筛选后的结果生成。\n"
            "2. 每个CSV最多500条，适合逐个上传给GPTs或Project分析。\n"
            "3. 字段已改为中文，关键词保留韩文原文。\n"
            "4. NAVER广告竞争仅代表广告侧参考，不等于真实商品竞争。\n"
        )
        zip_file.writestr("使用说明.txt", readme.encode("utf-8-sig"))

        if total_rows == 0:
            empty_csv = df_ai.to_csv(index=False).encode("utf-8-sig")
            zip_file.writestr("AI分析用关键词_第001批_共001批_空数据.csv", empty_csv)
        else:
            for batch_index, start in enumerate(range(0, total_rows, batch_size), start=1):
                end = min(start + batch_size, total_rows)
                batch_df = df_ai.iloc[start:end].copy()

                # 加上很轻量的追踪字段，方便后续合并
                batch_df.insert(0, "批次", f"{batch_index}/{batch_count}")
                batch_df.insert(1, "原始序号", range(start + 1, end + 1))

                csv_bytes = batch_df.to_csv(index=False).encode("utf-8-sig")
                csv_name = f"AI分析用关键词_第{batch_index:03d}批_共{batch_count:03d}批_{start + 1}-{end}.csv"
                zip_file.writestr(csv_name, csv_bytes)

    zip_buffer.seek(0)
    return zip_buffer.getvalue(), batch_count, total_rows


def ensure_result_columns(df: pd.DataFrame) -> pd.DataFrame:
    """确保结果表字段齐全，避免筛选时报错"""
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

    df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0).astype(int)
    df["pc"] = pd.to_numeric(df["pc"], errors="coerce").fillna(0).astype(int)
    df["mobile"] = pd.to_numeric(df["mobile"], errors="coerce").fillna(0).astype(int)

    return df[required_columns]


# =========================================================
# Session State
# =========================================================
if "data" not in st.session_state:
    st.session_state.data = None

if "keyword_text" not in st.session_state:
    st.session_state.keyword_text = ""

if "last_file_timestamp" not in st.session_state:
    st.session_state.last_file_timestamp = ""


def clear_all():
    st.session_state.data = None
    st.session_state.keyword_text = ""
    st.session_state.last_file_timestamp = ""


# =========================================================
# 页面 UI
# =========================================================
st.title("🇰🇷 Naver 关键词挖掘工具")
st.caption(f"当前版本：{APP_VERSION}")
st.markdown("输入关键词，每行一个。查询完成后可以下载 Excel、完整 CSV，以及 AI 分析用的分批 CSV 压缩包。")

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
        keywords = [line.strip() for line in input_text.splitlines() if line.strip()]
        keywords = list(dict.fromkeys(keywords))  # 去重，并保留原顺序

        st.info(f"正在查询 {len(keywords)} 个关键词...")

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
        df_result = ensure_result_columns(df_result)

        st.session_state.data = df_result
        st.session_state.last_file_timestamp = time.strftime("%Y%m%d_%H%M%S")

        st.success("🎉 查询完成！")


# =========================================================
# 结果展示、筛选、下载
# =========================================================
if st.session_state.data is not None:
    df = st.session_state.data.copy()
    df = ensure_result_columns(df)

    st.divider()
    st.markdown("### 🔍 结果筛选与导出")

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([1.3, 1.1, 1.1, 1.1])

    with filter_col1:
        keyword_filter = st.text_input(
            "关键词包含",
            value="",
            placeholder="例如：거치대 / 노트북 / 스탠드",
        )

    with filter_col2:
        core_options = sorted(df["is_core"].fillna("-").astype(str).unique().tolist())
        selected_core = st.multiselect(
            "核心词匹配",
            options=core_options,
            default=core_options,
        )

    with filter_col3:
        comp_options = sorted(df["competition"].fillna("-").astype(str).unique().tolist())
        selected_comp = st.multiselect(
            "竞争程度",
            options=comp_options,
            default=comp_options,
        )

    with filter_col4:
        min_total = st.number_input(
            "最低搜索量",
            min_value=0,
            value=0,
            step=100,
        )

    df_filtered = df.copy()

    if keyword_filter.strip():
        kw = keyword_filter.strip()
        mask_main = df_filtered["main_keyword"].astype(str).str.contains(kw, case=False, na=False)
        mask_rel = df_filtered["rel_keyword"].astype(str).str.contains(kw, case=False, na=False)
        df_filtered = df_filtered[mask_main | mask_rel]

    df_filtered = df_filtered[
        df_filtered["is_core"].fillna("-").astype(str).isin(selected_core)
        & df_filtered["competition"].fillna("-").astype(str).isin(selected_comp)
        & (df_filtered["total"].fillna(0).astype(int) >= int(min_total))
    ]

    df_filtered = df_filtered.sort_values(by="total", ascending=False).reset_index(drop=True)

    st.dataframe(df_filtered, use_container_width=True, height=420)

    st.markdown("---")

    file_timestamp = st.session_state.last_file_timestamp or time.strftime("%Y%m%d_%H%M%S")

    excel_data = make_excel_bytes(df_filtered)
    csv_data = make_csv_bytes(df_filtered)
    ai_zip_data, ai_batch_count, ai_total_rows = make_ai_batch_zip_bytes(df_filtered, batch_size=500)

    col_excel, col_csv, col_ai_zip, col_count = st.columns([1.1, 1.1, 1.6, 2.7])

    with col_excel:
        st.download_button(
            label="📥 下载 Excel",
            data=excel_data,
            file_name=f"naver_kws_{file_timestamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
            key="download_excel",
        )

    with col_csv:
        st.download_button(
            label="📄 下载完整 CSV",
            data=csv_data,
            file_name=f"naver_kws_{file_timestamp}.csv",
            mime="text/csv",
            use_container_width=True,
            key="download_csv",
        )

    with col_ai_zip:
        st.download_button(
            label="🤖 下载AI分批CSV ZIP",
            data=ai_zip_data,
            file_name=f"AI分析用关键词分批_{file_timestamp}_{ai_total_rows}条_{ai_batch_count}份.zip",
            mime="application/zip",
            use_container_width=True,
            key="download_ai_zip",
        )

    with col_count:
        st.markdown(
            f"#### 📊 筛选后数量： <span style='color:red; font-size:1.2em'>{len(df_filtered)}</span> 个",
            unsafe_allow_html=True,
        )
        st.caption(f"AI分批CSV：每500条一份，共 {ai_batch_count} 份。")

    st.markdown("### 🤖 给 AI 分析用")
    with st.expander("📋 展开复制 CSV 文本预览", expanded=False):
        st.caption("这里只显示前500条预览。完整分批文件请点击“下载AI分批CSV ZIP”。")
        preview_df = df_filtered.head(500)
        st.code(preview_df.to_csv(index=False), language="csv")

else:
    st.info("输入关键词后点击“开始查询”，查询完成后这里会显示结果和下载按钮。")
