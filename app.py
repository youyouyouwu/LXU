import streamlit as st
import pandas as pd
import time
import requests
import hashlib
import hmac
import base64
import io
import re


# =========================================================
# 页面设置
# =========================================================
APP_VERSION = "手动输入 + Excel/CSV 下载版 v4"

st.set_page_config(page_title="Naver 快速挖词", layout="wide")


# =========================================================
# API 配置：建议放在 Streamlit Secrets
# Secrets 里需要有：
# API_KEY = "你的 Naver API Key"
# SECRET_KEY = "你的 Naver Secret Key"
# CUSTOMER_ID = "你的 Customer ID"
# =========================================================
def get_secret(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
        return str(value) if value else ""
    except Exception:
        return ""


API_KEY = get_secret("API_KEY")
SECRET_KEY_TEXT = get_secret("SECRET_KEY")
CUSTOMER_ID = get_secret("CUSTOMER_ID")

if not API_KEY or not SECRET_KEY_TEXT or not CUSTOMER_ID:
    st.title("🇰🇷 Naver 关键词挖掘工具")
    st.caption(f"当前版本：{APP_VERSION}")
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

SECRET_KEY = SECRET_KEY_TEXT.encode("utf-8")
API_URL = "https://api.searchad.naver.com/keywordstool"


# =========================================================
# 核心函数
# =========================================================
def clean_for_api(keyword: str) -> str:
    """去掉空格，给 Naver API 使用"""
    return re.sub(r"\s+", "", str(keyword))


def make_signature(method: str, uri: str, timestamp: str) -> str:
    """生成 Naver SearchAd API 签名"""
    message = f"{timestamp}.{method}.{uri}".encode("utf-8")
    signature = hmac.new(SECRET_KEY, message, hashlib.sha256).digest()
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

        # 兼容 "> 1000"
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
                last_error = f"HTTP {res.status_code}: {res.text[:120]}"
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


# =========================================================
# Session State
# =========================================================
if "data" not in st.session_state:
    st.session_state.data = None

if "last_input" not in st.session_state:
    st.session_state.last_input = ""


# =========================================================
# 页面 UI
# =========================================================
st.title("🇰🇷 Naver 关键词挖掘工具")
st.caption(f"当前版本：{APP_VERSION}")
st.markdown("输入关键词，每行一个。查询完成后可以下载 Excel 或 CSV。")

input_text = st.text_area(
    "请输入关键词（每行一个）",
    height=160,
    value=st.session_state.last_input,
    placeholder="例如：\n노트북거치대\n자전거거치대\n아이패드거치대",
)

col_start, col_clear = st.columns([1, 1])

with col_start:
    start_clicked = st.button("开始查询 🚀", type="primary", use_container_width=True)

with col_clear:
    clear_clicked = st.button("清空结果", use_container_width=True)

if clear_clicked:
    st.session_state.data = None
    st.session_state.last_input = ""
    st.rerun()

if start_clicked:
    if not input_text.strip():
        st.warning("请先输入关键词。")
    else:
        st.session_state.last_input = input_text

        keywords = [line.strip() for line in input_text.splitlines() if line.strip()]
        keywords = list(dict.fromkeys(keywords))  # 去重并保留顺序

        st.info(f"正在查询 {len(keywords)} 个关键词...")

        progress_bar = st.progress(0)
        status_text = st.empty()
        all_rows = []

        for i, keyword in enumerate(keywords):
            status_text.text(f"正在处理：{keyword} ({i + 1}/{len(keywords)})")
            all_rows.extend(get_related_keywords(keyword))
            progress_bar.progress((i + 1) / len(keywords))
            time.sleep(0.3)

        status_text.text("查询完成！")
        progress_bar.progress(1.0)

        st.session_state.data = pd.DataFrame(all_rows)
        st.success("🎉 查询完成！")


# =========================================================
# 结果展示、筛选、下载
# =========================================================
if st.session_state.data is not None:
    df = st.session_state.data.copy()

    st.divider()
    st.markdown("### 🔍 结果筛选与导出")

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([1.2, 1.1, 1.1, 1.2])

    with filter_col1:
        keyword_filter = st.text_input(
            "关键词包含",
            value="",
            placeholder="例如：거치대",
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

    st.dataframe(df_filtered, use_container_width=True, height=420)

    st.markdown("---")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    excel_data = make_excel_bytes(df_filtered)
    csv_data = make_csv_bytes(df_filtered)

    col_excel, col_csv, col_count = st.columns([1.1, 1.1, 3])

    with col_excel:
        st.download_button(
            label="📥 下载 Excel",
            data=excel_data,
            file_name=f"naver_kws_{timestamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )

    with col_csv:
        st.download_button(
            label="📄 下载 CSV",
            data=csv_data,
            file_name=f"naver_kws_{timestamp}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with col_count:
        st.markdown(
            f"#### 📊 筛选后数量： <span style='color:red; font-size:1.2em'>{len(df_filtered)}</span> 个",
            unsafe_allow_html=True,
        )

    st.markdown("### 🤖 给 AI 分析用")
    with st.expander("📋 展开复制 CSV 文本", expanded=False):
        st.caption("也可以直接下载上面的 CSV 文件，再上传给 GPT 分析。")
        st.code(df_filtered.to_csv(index=False), language="csv")
