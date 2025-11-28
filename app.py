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
st.set_page_config(page_title="Solana 狙擊指揮中心 (深層版)", layout="wide", page_icon="🎯")

st.sidebar.title("⚙️ 設定中心")
st.sidebar.markdown("👇 請輸入 Key 開始獵殺")
HELIUS_KEY = st.sidebar.text_input("Helius API Key", type="password")
TG_TOKEN = st.sidebar.text_input("Telegram Bot Token (選填)", type="password")
TG_CHAT_ID = st.sidebar.text_input("Telegram Chat ID (選填)")

RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

# 知名交易所清單 (用來標記綠色)
CEX_LABELS = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1": "Binance 1",
    "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfBKkTY8CJ92pA": "Binance 2",
    "AC5RDfQFmDS1deWZosYb21bfU9aMCjVZk4JipjbA71gh": "Coinbase 1",
    "H8sMJSCQxfKiFTCf97_wnBo8PH48Atn36JcZggs8ZKx": "Coinbase 2",
    "315iCQx9t9NCQF457223M6e37kG9PTc1" : "Wintermute",
}

# ==========================================
# 2. 核心功能
# ==========================================
def send_rpc(method, params):
    try:
        res = requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=15)
        return res.json()
    except: return {}

def trace_funder(wallet):
    """
    🔥 深層追查：往回查 30 筆交易
    """
    time.sleep(0.1) 
    # 擴大範圍到 30 筆 (這是關鍵！)
    data = send_rpc("getSignaturesForAddress", [wallet, {"limit": 30}])
    sigs = [tx['signature'] for tx in data.get('result', [])]
    
    # 為了節省時間，我們只查最早的 5 筆 和 最近的 5 筆
    # 通常資金來源不是在最開始(創錢包時)，就是在買幣前一刻
    check_list = sigs[-5:] + sigs[:5] if len(sigs) > 10 else sigs
    
    for sig in check_list:
        tx_res = send_rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        try:
            instrs = tx_res['result']['transaction']['message']['instructions']
            for i in instrs:
                if i.get('program') == 'system' and i.get('parsed', {}).get('type') == 'transfer':
                    info = i['parsed']['info']
                    
                    # 只要有錢轉進來，都算嫌疑犯 (放寬金額限制)
                    if info['destination'] == wallet:
                        source = info['source']
                        # 排除掉自己轉給自己，或金額太小(<0.01 SOL)的雜訊
                        if source != wallet and info['lamports'] > 10000000: 
                            return source
        except: continue
    return None

def analyze_token(token_address):
    if not HELIUS_KEY: return None, "請輸入 API Key"
    if token_address.startswith("0x"): return None, "不支援以太坊"

    # 1. 抓前 10 大股東
    res = send_rpc("getTokenLargestAccounts", [token_address])
    if 'result' not in res: return None, "查無數據"
    
    accounts = res['result']['value'][:10]
    whales = []
    
    for acc in accounts:
        info = send_rpc("getAccountInfo", [acc['address'], {"encoding": "jsonParsed"}])
        try:
            owner = info['result']['value']['data']['parsed']['info']['owner']
            whales.append(owner)
        except: continue
    
    unique_whales = list(set(whales))
    
    # 2. 畫圖
    G = nx.DiGraph()
    short_token = token_address[:4] + "..."
    G.add_node(token_address, label=f"Token\n{short_token}", color="#ffd700", size=30, shape="star")
    
    risk_score = 0
    funder_map = {}
    
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    for i, whale in enumerate(unique_whales):
        status_text.text(f"深層挖掘大戶 {i+1}/{len(unique_whales)}: {whale[:4]}...")
        progress_bar.progress((i + 1) / len(unique_whales))
        
        G.add_node(whale, label=f"Holder\n{whale[:4]}...", color="#97c2fc", size=15)
        G.add_edge(whale, token_address, color="#cccccc")
        
        # 查金主
        funder = trace_funder(whale)
        if funder:
            # 判斷是交易所(綠) 還是 老鼠倉(紅)
            if funder in CEX_LABELS:
                f_color = "#00ff00"
                f_label = f"🏦 {CEX_LABELS[funder]}"
            else:
                f_color = "#ff4b4b"
                f_label = f"🚨 SOURCE\n{funder[:4]}..."
                
                # 累計風險
                funder_map[funder] = funder_map.get(funder, 0) + 1
                if funder_map[funder] > 1:
                    risk_score += 10

            if funder not in G:
                G.add_node(funder, label=f_label, color=f_color, size=25, shape="box")
            G.add_edge(funder, whale, color=f_color)

    status_text.empty()
    progress_bar.empty()
    
    return G, risk_score

# ==========================================
# 3. 掃描策略 (雙重保險)
# ==========================================
def scan_new_pairs():
    keywords = ["pump", "meme", "cat", "dog"]
    BLACKLIST_ADDR = ["So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"]

    all_candidates = []
    try:
        for kw in keywords:
            res = requests.get(f"https://api.dexscreener.com/latest/dex/search?q={kw}", timeout=5).json()
            pairs = res.get('pairs', [])
            for p in pairs:
                if p.get('chainId') != 'solana': continue
                if p.get('baseToken', {}).get('address') in BLACKLIST_ADDR: continue
                name = p.get('baseToken', {}).get('name', '').lower()
                if name == 'solana' or name == 'wrapped sol': continue
                all_candidates.append(p)
            if len(all_candidates) > 20: break
        
        all_candidates.sort(key=lambda x: x.get('pairCreatedAt', 0), reverse=True)
        
        # 去重
        seen = set()
        final = []
        for p in all_candidates:
            addr = p.get('baseToken', {}).get('address', '')
            if addr not in seen:
                seen.add(addr)
                final.append(p)
        return final[:5]
    except: return []

# ==========================================
# 4. 主介面
# ==========================================
st.title("🚀 Solana 狙擊指揮中心 (深層掃描版)")

if not HELIUS_KEY:
    st.warning("⚠️ 請先在左側欄位輸入 Helius API Key！")

tab1, tab2 = st.tabs(["🔍 手動查幣", "🤖 自動掃描新幣"])

# TAB 1
with tab1:
    target = st.text_input("輸入代幣地址", "2zMMhcVQhZkJeb4h5Rpp47aZPaej4XMs75c8V4Jkpump")
    if st.button("開始分析", key="btn1"):
        with st.spinner("🕵️‍♂️ 正在深層挖掘 (查詢 30 筆歷史)..."):
            G, risk = analyze_token(target)
            if G is None:
                st.error(f"失敗: {risk}")
            else:
                if risk > 0:
                    st.error(f"🚨 發現老鼠倉集團！風險指數: {risk}")
                else:
                    st.success("✅ 籌碼分散 (無明顯關聯)")
                
                net = Network(height="500px", width="100%", bgcolor="#222222", font_color="white", directed=True, cdn_resources='in_line')
                net.from_nx(G)
                net.save_graph("graph.html")
                with open("graph.html", "r", encoding="utf-8") as f:
                    components.html(f.read(), height=520)

# TAB 2
with tab2:
    if st.button("🛡️ 掃描市場新幣"):
        if not HELIUS_KEY: st.error("無 Key")
        else:
            pairs = scan_new_pairs()
            if not pairs: st.warning("暫無新幣")
            else:
                for pair in pairs:
                    name = pair.get('baseToken', {}).get('name', 'Unknown')
                    addr = pair.get('baseToken', {}).get('address', '')
                    price = pair.get('priceUsd', '0')
                    st.markdown(f"**檢查代幣：{name}**")
                    st.code(addr)
                    
                    G, risk = analyze_token(addr)
                    if G:
                        if risk > 0: st.error(f"❌ 風險 (Risk: {risk})")
                        else: st.success("✅ 安全")
                        
                        net = Network(height="400px", width="100%", bgcolor="#222222", font_color="white", directed=True, cdn_resources='in_line')
                        net.from_nx(G)
                        fname = f"g_{addr[:4]}.html"
                        net.save_graph(fname)
                        with open(fname, "r", encoding="utf-8") as f:
                            components.html(f.read(), height=420)
                    st.divider()
