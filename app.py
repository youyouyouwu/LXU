import streamlit as st
import pandas as pd
import time
import requests
import hashlib
import hmac
import base64import streamlit as st
import pandas as pd
import time
import requests
import hashlib
import hmac
import base64
import io
import re

# ==========================================
# ⚠️ 这里的 Key 一定要填对
# ==========================================
API_KEY = "01000000002600ac5cb082cca74dcce7979c36b40f0ea607061eb8454f02026c998129ab03" 
SECRET_KEY = "AQAAAAAmAKxcsILMp03M55ecNrQPWZjIxDBbElbfr1p+wtqBTw==".encode("utf-8")
CUSTOMER_ID = "4197574"
# ==========================================

API_URL = "https://api.searchad.naver.com/keywordstool"

def clean_for_api(keyword: str) -> str:
    """去掉空格，给 API 用"""
    return re.sub(r"\s+", "", str(keyword))

def make_signature(method: str, uri: str, timestamp: str) -> str:
    """按官方要求生成签名"""
    message = f"{timestamp}.{method}.{uri}".encode("utf-8")
    signature = hmac.new(SECRET_KEY, message, hashlib.sha256).digest()
    return base64.b64encode(signature).decode("utf-8")

def normalize_count(raw):
    """把 Naver 返回的数据转成整数"""
    if isinstance(raw, int): return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("<"): return 5
        if s.startswith(">"): return int(s[1:].strip()) if s[1:].strip().isdigit() else 0
        s = s.replace(",", "")
        if s.isdigit(): return int(s)
    return 0

def get_related_keywords(main_keyword: str, retry: int = 3):
    """核心请求函数"""
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
                "X-Signature": signature,
            }
            params = {"hintKeywords": query_kw, "showDetail": 1}
            res = requests.get(API_URL, headers=headers, params=params)

            if not res.text or not res.text.strip():
                time.sleep(1)
                continue
            if res.status_code != 200:
                time.sleep(1)
                continue

            data = res.json()
            if "keywordList" not in data or len(data["keywordList"]) == 0:
                results.append({
                    "main_keyword": main_keyword, "rel_keyword": "", "is_core": "Y",
                    "pc": 0, "mobile": 0, "total": 0, "competition": "-", "error": "No Data"
                })
                return results

            cleaned_main = clean_for_api(main_keyword)
            for item in data["keywordList"]:
                rel_kw = item.get("relKeyword", "")
                pc_raw = item.get("monthlyPcQcCnt", 0)
                mobile_raw = item.get("monthlyMobileQcCnt", 0)
                total = normalize_count(pc_raw) + normalize_count(mobile_raw)
                comp = item.get("compIdx", "-")
                is_core = "Y" if clean_for_api(rel_kw) == cleaned_main else "N"

                results.append({
                    "main_keyword": main_keyword, "rel_keyword": rel_kw, "is_core": is_core,
                    "pc": pc_raw, "mobile": mobile_raw, "total": total,
                    "competition": comp, "error": ""
                })
            return results
        except Exception:
            time.sleep(1)

    results.append({
        "main_keyword": main_keyword, "rel_keyword": "", "is_core": "Y",
        "pc": 0, "mobile": 0, "total": 0, "competition": "-", "error": "Failed"
    })
    return results

# --- 网页界面 ---
st.set_page_config(page_title="Naver 挖掘工具", layout="centered")
st.title("🇰🇷 Naver 关键词挖掘工具")
st.markdown("支持上传 .xlsx 或 .csv 文件，自动查询 Naver 官方数据。")

uploaded_file = st.file_uploader("📂 上传 Excel/CSV 文件", type=['xlsx', 'csv'])

if uploaded_file:
    try:
        if uploaded_file.name.endswith('.csv'):
            df_input = pd.read_csv(uploaded_file)
        else:
            df_input = pd.read_excel(uploaded_file)
        
        # 默认取第一列
        keywords_list = df_input.iloc[:, 0].dropna().astype(str).tolist()
        keywords_list = [k.strip() for k in keywords_list if k.strip()]
        
        st.info(f"✅ 识别到 {len(keywords_list)} 个关键词")

        if st.button("🚀 开始查询", type="primary"):
            all_rows = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for i, mk in enumerate(keywords_list):
                status_text.text(f"正在处理 ({i+1}/{len(keywords_list)}): {mk}")
                progress_bar.progress((i + 1) / len(keywords_list))
                all_rows.extend(get_related_keywords(mk))
                time.sleep(0.3) 

            df_result = pd.DataFrame(all_rows)
            st.success("🎉 完成！")
            
            # 简单的结果展示
            st.dataframe(df_result.head())

            # 下载：同时支持 Excel 和 CSV
            excel_output = io.BytesIO()
            with pd.ExcelWriter(excel_output, engine='xlsxwriter') as writer:
                df_result.to_excel(writer, index=False, sheet_name="result")
            excel_data = excel_output.getvalue()

            # CSV 用 utf-8-sig，中文/韩文在 Excel 中打开更不容易乱码，也适合后续上传给 GPT 分析
            csv_data = df_result.to_csv(index=False).encode("utf-8-sig")

            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    "📥 下载结果 Excel",
                    data=excel_data,
                    file_name="naver_result.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            with col2:
                st.download_button(
                    "📄 下载结果 CSV",
                    data=csv_data,
                    file_name="naver_result.csv",
                    mime="text/csv"
                )

    except Exception as e:
        st.error(f"错误: {e}")
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
    if isinstance(raw, int): return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("<"): return 5
        if s.startswith(">"): return int(s[1:].strip()) if s[1:].strip().isdigit() else 0
        s = s.replace(",", "")
        if s.isdigit(): return int(s)
    return 0

def get_related_keywords(main_keyword: str, retry: int = 3):
    query_kw = clean_for_api(main_keyword)
    results = []
    for attempt in range(1, retry + 1):
        try:
            timestamp = str(int(time.time() * 1000))
            signature = make_signature("GET", "/keywordstool", timestamp)
            headers = {"X-Timestamp": timestamp, "X-API-KEY": API_KEY, "X-Customer": CUSTOMER_ID, "X-Signature": signature}
            params = {"hintKeywords": query_kw, "showDetail": 1}
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
    return [{"main_keyword": main_keyword, "error": "Failed", "rel_keyword":"", "pc":0,"mobile":0,"total":0,"competition":"-"}]

# ==========================================
# 界面 UI
# ==========================================
st.set_page_config(page_title="Naver 快速挖词", layout="wide")

st.title("🇰🇷 Naver 关键词挖掘工具")

if 'data' not in st.session_state:
    st.session_state.data = None

input_text = st.text_area("请输入关键词 (每行一个)", height=150, placeholder="例如：\n连衣裙\niphone case")

if st.button("开始查询 🚀", type="primary"):
    if not input_text.strip():
        st.warning("⚠️ 请先输入关键词！")
    else:
        kws = [line.strip() for line in input_text.split('\n') if line.strip()]
        if len(kws) > 0:
            st.info(f"正在查询 {len(kws)} 个关键词...")
            bar = st.progress(0)
            status_text = st.empty()
            all_res = []
            
            for i, k in enumerate(kws):
                status_text.text(f"正在处理: {k} ({i+1}/{len(kws)})")
                bar.progress((i+1)/len(kws))
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
        unique_core = df['is_core'].unique().tolist()
        sel_core = st.multiselect("核心词匹配 (is_core)", unique_core, default=unique_core)
    with col_f2:
        unique_comp = df['competition'].unique().tolist()
        sel_comp = st.multiselect("竞争程度 (competition)", unique_comp, default=unique_comp)
    with col_f3:
        min_total = st.number_input("最低搜索量 (total >)", min_value=0, value=0, step=100)

    # 执行筛选
    df_filtered = df[
        (df['is_core'].isin(sel_core)) &
        (df['competition'].isin(sel_comp)) &
        (df['total'] >= min_total)
    ]
    
    # 显示表格
    st.dataframe(df_filtered, use_container_width=True, height=400)
    
    # -------------------------------------------------------
    # 底部操作区：下载 Excel + 数据统计 + AI 复制
    # -------------------------------------------------------
    st.markdown("---")
    
    # 第一行：下载按钮 和 数量显示
    col_dl, col_count = st.columns([1, 4])
    
    with col_dl:
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='xlsxwriter') as writer:
            df_filtered.to_excel(writer, index=False)
        st.download_button(
            label="📥 下载 Excel",
            data=out.getvalue(),
            file_name=f"naver_kws_{int(time.time())}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary"
        )
        
    with col_count:
        st.markdown(f"#### 📊 筛选后数量： <span style='color:red; font-size:1.2em'>{len(df_filtered)}</span> 个", unsafe_allow_html=True)

    # -------------------------------------------------------
    # 新增功能：一键复制给 Gemini
    # -------------------------------------------------------
    st.markdown("### 🤖 发送给 AI 分析")
    with st.expander("📋 点击展开，一键复制数据 (Paste to Gemini)", expanded=False):
        st.caption("👇 点击代码块右上角的 'Copy' 图标，然后直接粘贴给 Gemini 即可。")
        # 将 DataFrame 转为 CSV 文本供复制
        csv_text = df_filtered.to_csv(index=False)
        st.code(csv_text, language='csv')
