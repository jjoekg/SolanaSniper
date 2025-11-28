import streamlit as st
import requests
import pandas as pd
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import time

# ==========================================
# 1. 頁面設定
# ==========================================
st.set_page_config(page_title="Solana 狙擊指揮中心", layout="wide", page_icon="🎯")

st.sidebar.title("⚙️ 設定中心")
HELIUS_KEY = st.sidebar.text_input("Helius API Key", type="password")
TG_TOKEN = st.sidebar.text_input("Telegram Bot Token", type="password")
TG_CHAT_ID = st.sidebar.text_input("Telegram Chat ID")

RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

# ==========================================
# 2. 核心功能：Helius 資金溯源
# ==========================================
def send_rpc(method, params):
    try:
        res = requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=10)
        return res.json()
    except: return {}

def trace_funder(wallet):
    """追查資金來源"""
    time.sleep(0.1) # 避免 API 限制
    data = send_rpc("getSignaturesForAddress", [wallet, {"limit": 5}])
    sigs = [tx['signature'] for tx in data.get('result', [])]
    
    for sig in sigs:
        tx_res = send_rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        try:
            instrs = tx_res['result']['transaction']['message']['instructions']
            for i in instrs:
                if i.get('program') == 'system' and i.get('parsed', {}).get('type') == 'transfer':
                    info = i['parsed']['info']
                    if info['destination'] == wallet and info['lamports'] > 500000000: # > 0.5 SOL
                        return info['source']
        except: continue
    return None

def analyze_token(token_address):
    """分析代幣並回傳 Graph 對象與風險評級"""
    if not HELIUS_KEY: return None, "No Key"
    
    # 1. 抓前 10 大股東
    res = send_rpc("getTokenLargestAccounts", [token_address])
    if 'result' not in res: return None, "Invalid Token"
    
    accounts = res['result']['value'][:10]
    whales = []
    
    # 解析真實錢包
    for acc in accounts:
        info = send_rpc("getAccountInfo", [acc['address'], {"encoding": "jsonParsed"}])
        try:
            owner = info['result']['value']['data']['parsed']['info']['owner']
            whales.append(owner)
        except: continue
    
    unique_whales = list(set(whales))
    
    # 2. 畫圖 & 偵測
    G = nx.DiGraph()
    G.add_node(token_address, label="Token", color="#ffd700", size=25, shape="star")
    
    risk_score = 0
    funder_map = {}
    
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    for i, whale in enumerate(unique_whales):
        status_text.text(f"正在調查大戶 {i+1}/{len(unique_whales)}: {whale[:4]}...")
        progress_bar.progress((i + 1) / len(unique_whales))
        
        G.add_node(whale, label=f"Holder\n{whale[:4]}...", color="#97c2fc", size=15)
        G.add_edge(whale, token_address, color="#cccccc")
        
        # 查金主
        funder = trace_funder(whale)
        if funder:
            # 標記金主
            if funder not in G:
                G.add_node(funder, label=f"🚨 SOURCE\n{funder[:4]}...", color="#ff4b4b", size=20, shape="box")
            G.add_edge(funder, whale, color="#ff0000")
            
            # 累計風險：如果同一個金主資助多人
            funder_map[funder] = funder_map.get(funder, 0) + 1
            if funder_map[funder] > 1:
                risk_score += 10 # 發現集團！

    status_text.text("分析完成！")
    progress_bar.empty()
    
    return G, risk_score

# ==========================================
# 3. 輔助功能：Telegram & DexScreener
# ==========================================
def send_telegram_msg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TG_CHAT_ID, "text": msg})

def scan_new_pairs():
    """掃描 DexScreener 最新上架的 Solana 幣"""
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    try:
        # 注意：DexScreener API 變動較快，這裡抓最新代幣列表
        # 如果這個 API 失效，可以用 requests.get("https://api.dexscreener.com/latest/dex/tokens/SOL")
        res = requests.get("https://api.dexscreener.com/latest/dex/search?q=Solana").json()
        pairs = res.get('pairs', [])[:5] # 抓前 5 個
        return pairs
    except: return []

# ==========================================
# 4. 主介面 (UI)
# ==========================================
st.title("🚀 Solana 老鼠倉獵人 (Helius + AI)")

tab1, tab2 = st.tabs(["🔍 手動查幣", "🤖 自動掃描新幣"])

# --- TAB 1: 手動查詢 ---
with tab1:
    target = st.text_input("輸入代幣地址 (Contract Address)", "2zMMhcVQhZkJeb4h5Rpp47aZPaej4XMs75c8V4Jkpump")
    
    if st.button("開始分析", key="analyze_btn"):
        if not HELIUS_KEY:
            st.error("請先在左側輸入 Helius API Key！")
        else:
            with st.spinner("🕵️‍♂️ 正在進行鏈上肉搜..."):
                G, risk = analyze_token(target)
                
                if G:
                    # 顯示結果
                    if risk > 0:
                        st.error(f"🚨 警告！偵測到老鼠倉集團！風險指數: {risk}")
                        send_telegram_msg(f"🚨 警告：代幣 {target} 發現老鼠倉！風險指數 {risk}。請小心！")
                    else:
                        st.success("✅ 籌碼結構相對健康，未發現明顯關聯資金。")
                    
                    # 畫圖
                    net = Network(height="500px", width="100%", bgcolor="#222222", font_color="white", directed=True)
                    net.from_nx(G)
                    net.save_graph("graph.html")
                    
                    # 讀取 HTML 並顯示
                    with open("graph.html", "r", encoding="utf-8") as f:
                        components.html(f.read(), height=520)

# --- TAB 2: 自動掃描 ---
with tab2:
    st.write("點擊下方按鈕，自動從 DexScreener 抓取熱門新幣並進行快篩。")
    if st.button("🛡️ 掃描市場新幣"):
        pairs = scan_new_pairs()
        if not pairs:
            st.warning("暫時抓不到新幣數據。")
        else:
            for pair in pairs:
                name = pair.get('baseToken', {}).get('name', 'Unknown')
                addr = pair.get('baseToken', {}).get('address', '')
                price = pair.get('priceUsd', '0')
                
                st.markdown(f"**檢查代幣：{name}** (`{addr}`)")
                st.write(f"當前價格: ${price}")
                
                # 簡單掃描
                G, risk = analyze_token(addr)
                if risk > 0:
                    st.error(f"❌ 發現風險！(Risk: {risk})")
                    send_telegram_msg(f"🚨 發現危險新幣：{name}\n地址：{addr}\n風險：老鼠倉集團活躍！")
                else:
                    st.success("✅ 通過檢測 (無明顯關聯)")
                    send_telegram_msg(f"✅ 發現潛力新幣：{name}\n地址：{addr}\n狀態：籌碼分散，無明顯老鼠倉。")
                
                st.divider()
