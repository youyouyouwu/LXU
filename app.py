import streamlit as st
import pandas as pd
import time
import requests
import hashlib
import hmac
import base64
import io
import re

# ==========================================
# 🔐 安全配置
# ==========================================
try:
    API_KEY = st.secrets["API_KEY"]
    SECRET_KEY = st.secrets["SECRET_KEY"].encode("utf-8")
    CUSTOMER_ID = st.secrets["CUSTOMER_ID"]
except:
    # 这里的 Key 仅作演示，实际部署时请在 Streamlit 后台 Secrets 填入
    API_KEY = "你的API_KEY"
    SECRET_KEY = "你的SECRET_KEY".encode("utf-8")
    CUSTOMER_ID = "你的CUSTOMER_ID"

API_URL = "https://api.searchad.naver.com/keywordstool"

# ==========================================
# 核心功能函数
# ==========================================
def clean_for_api(keyword: str) -> str:
    return re.sub(r"\s+", "", str(keyword))

def make_signature(method: str, uri: str, timestamp: str) -> str:
    message = f"{timestamp}.{method}.{uri}".encode("utf-8")
    signature = hmac.new(SECRET_KEY, message, hashlib.sha256).digest()
    return base64.b64encode(signature).decode("utf-8")

def normalize_count(raw):
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("<"):
            return 5
        if s.startswith(">"):
            return int(s[1:].strip()) if s[1:].strip().isdigit() else 0
        s = s.replace(",", "")
        if s.isdigit():
            return int(s)
    return 0

def get_related_keywords(main_keyword: str, retry: int = 3):
    query_kw = clean_for_api(main_keyword)
    results = []

    for attempt in range(1, retry + 1):
        try:
            timestamp = str(int(time.time() * 1000))
            signature = make_signature("GET", "/keywordstool", timestamp)

            headers = {
                "X-Timestamp": timestamp,
                "X-API-KEY": API_KEY,
                "X-Customer": CUSTOMER_ID,
                "X-Signature": signature
            }

            params = {
                "hintKeywords": query_kw,
                "showDetail": 1
            }

            res = requests.get(API_URL, headers=headers, params=params)

            if res.status_code == 200:
                data = res.json()

                if "keywordList" in data:
                    cleaned_main = clean_for_api(main_keyword)

                    for item in data["keywordList"]:
                        rel_kw = item.get("relKeyword", "")
                        pc_raw = item.get("monthlyPcQcCnt", 0)
                        mobile_raw = item.get("monthlyMobileQcCnt", 0)

                        results.append({
                            "rel_keyword": rel_kw,
                            "main_keyword": main_keyword,
                            "is_core": "Y" if clean_for_api(rel_kw) == cleaned_main else "N",
                            "pc": normalize_count(pc_raw),
                            "mobile": normalize_count(mobile_raw),
                            "total": normalize_count(pc_raw) + normalize_count(mobile_raw),
                            "competition": item.get("compIdx", "-"),
                            "error": ""
                        })

                    return results

            time.sleep(0.3)

        except:
            time.sleep(1)

    return [{
        "main_keyword": main_keyword,
        "error": "Failed",
        "rel_keyword": "",
        "pc": 0,
        "mobile": 0,
        "total": 0,
        "competition": "-"
    }]

# ==========================================
# 界面 UI
# ==========================================
st.set_page_config(page_title="Naver 快速挖词", layout="wide")

st.title("🇰🇷 Naver 关键词挖掘工具")

if "data" not in st.session_state:
    st.session_state.data = None

input_text = st.text_area(
    "请输入关键词 (每行一个)",
    height=150,
    placeholder="例如：\n连衣裙\niphone case"
)

if st.button("开始查询 🚀", type="primary"):
    if not input_text.strip():
        st.warning("⚠️ 请先输入关键词！")
    else:
        kws = [line.strip() for line in input_text.split("\n") if line.strip()]

        if len(kws) > 0:
            st.info(f"正在查询 {len(kws)} 个关键词...")

            bar = st.progress(0)
            status_text = st.empty()
            all_res = []

            for i, k in enumerate(kws):
                status_text.text(f"正在处理: {k} ({i + 1}/{len(kws)})")
                bar.progress((i + 1) / len(kws))
                all_res.extend(get_related_keywords(k))

            status_text.text("查询完成！")
            bar.progress(100)

            if all_res:
                st.session_state.data = pd.DataFrame(all_res)

# ==========================================
# 结果显示区
# ==========================================
if st.session_state.data is not None:
    df = st.session_state.data

    st.divider()
    st.markdown("### 🔍 结果筛选与导出")

    # 1. 筛选器
    col_f1, col_f2, col_f3 = st.columns(3)

    with col_f1:
        unique_core = df["is_core"].unique().tolist()
        sel_core = st.multiselect(
            "核心词匹配 (is_core)",
            unique_core,
            default=unique_core
        )

    with col_f2:
        unique_comp = df["competition"].unique().tolist()
        sel_comp = st.multiselect(
            "竞争程度 (competition)",
            unique_comp,
            default=unique_comp
        )

    with col_f3:
        min_total = st.number_input(
            "最低搜索量 (total >)",
            min_value=0,
            value=0,
            step=100
        )

    # 执行筛选
    df_filtered = df[
        (df["is_core"].isin(sel_core)) &
        (df["competition"].isin(sel_comp)) &
        (df["total"] >= min_total)
    ]

    # 显示表格
    st.dataframe(df_filtered, use_container_width=True, height=400)

    # -------------------------------------------------------
    # 底部操作区：下载 Excel + 下载 CSV + 数据统计 + AI 复制
    # -------------------------------------------------------
    st.markdown("---")

    file_timestamp = int(time.time())

    # 第一行：下载按钮 和 数量显示
    col_excel, col_csv, col_count = st.columns([1, 1, 4])

    with col_excel:
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
            df_filtered.to_excel(writer, index=False, sheet_name="result")

        st.download_button(
            label="📥 下载 Excel",
            data=out.getvalue(),
            file_name=f"naver_kws_{file_timestamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True
        )

    with col_csv:
        csv_data = df_filtered.to_csv(index=False).encode("utf-8-sig")

        st.download_button(
            label="📄 下载 CSV",
            data=csv_data,
            file_name=f"naver_kws_{file_timestamp}.csv",
            mime="text/csv",
            use_container_width=True
        )

    with col_count:
        st.markdown(
            f"#### 📊 筛选后数量： <span style='color:red; font-size:1.2em'>{len(df_filtered)}</span> 个",
            unsafe_allow_html=True
        )

    # -------------------------------------------------------
    # 一键复制给 Gemini / GPT
    # -------------------------------------------------------
    st.markdown("### 🤖 发送给 AI 分析")

    with st.expander("📋 点击展开，一键复制数据 (Paste to Gemini)", expanded=False):
        st.caption("👇 点击代码块右上角的 'Copy' 图标，然后直接粘贴给 Gemini / GPT 即可。")

        # 将 DataFrame 转为 CSV 文本供复制
        csv_text = df_filtered.to_csv(index=False)
        st.code(csv_text, language="csv")
