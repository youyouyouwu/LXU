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
# 🔐 安全配置：从 Streamlit Secrets 读取 Key
# ==========================================
# 如果你在本地运行报错，请确保在 .streamlit/secrets.toml 中配置了 key
# 在云端部署时，请在后台 Settings -> Secrets 中配置
try:
    API_KEY = st.secrets["API_KEY"]
    SECRET_KEY = st.secrets["SECRET_KEY"].encode("utf-8")
    CUSTOMER_ID = st.secrets["CUSTOMER_ID"]
except:
    st.error("⚠️ 未检测到 API Key 配置！请在 Streamlit Cloud 后台设置 Secrets。")
    st.stop()

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
                            "main_keyword": main_keyword,
                            "rel_keyword": rel_kw,
                            "is_core": "Y" if clean_for_api(rel_kw) == cleaned_main else "N",
                            "pc": pc_raw,
                            "mobile": mobile_raw,
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
st.markdown("不用Excel，直接粘贴关键词，立即查询。")

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
                status_text.text(f"正在处理: {k}")
                bar.progress((i+1)/len(kws))
                all_res.extend(get_related_keywords(k))
            
            status_text.text("完成！")
            bar.progress(100)
            
            if all_res:
                res_df = pd.DataFrame(all_res)
                st.dataframe(res_df, use_container_width=True)
                
                out = io.BytesIO()
                with pd.ExcelWriter(out, engine='xlsxwriter') as writer:
                    res_df.to_excel(writer, index=False)
                
                st.download_button("📥 下载 Excel 结果", out.getvalue(), f"naver_kws_{int(time.time())}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
