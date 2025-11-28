import streamlit as st
import requests
import pandas as pd
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import time

# ==========================================
# 1. 頁面設定 (必須是第一行)
# ==========================================
st.set_page_config(page_title="Solana 狙擊指揮中心", layout="wide", page_icon="🎯")

# 除錯標記：如果你能看到這行字，代表 App 活著
st.write("✅ 系統連線正常 | 等待指令...")

st.sidebar.title("⚙️ 設定中心")
st.sidebar.markdown("請先在此輸入 Key 才能使用 👇")
HELIUS_KEY = st.sidebar.text_input("Helius API Key", type="password")
TG_TOKEN = st.sidebar.text_input("Telegram Bot Token (選填)", type="password")
TG_CHAT_ID = st.sidebar.text_input("Telegram Chat ID (選填)")

RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

# ==========================================
# 2. 核心功能
# ==========================================
def send_rpc(method, params):
    try:
        res = requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=10)
        return res.json()
    except: return {}

def trace_funder(wallet):
    """追查資金來源"""
    time.sleep(0.1) 
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
    if not HELIUS_KEY: return None, "請先在左側輸入 Helius API Key"
    if token_address.startswith("0x"): return None, "這是以太坊地址，Helius 只能查 Solana"

    res = send_rpc("getTokenLargestAccounts", [token_address])
    
    if 'error' in res: return None, f"API 錯誤: {res['error']['message']}"
    if 'result' not in res: return None, "無效的代幣地址或查無數據"
    
    accounts = res['result']['value'][:10]
    whales = []
    
    for acc in accounts:
        info = send_rpc("getAccountInfo", [acc['address'], {"encoding": "jsonParsed"}])
        try:
            owner = info['result']['value']['data']['parsed']['info']['owner']
            whales.append(owner)
        except: continue
    
    unique_whales = list(set(whales))
    
    # 畫圖
    G = nx.DiGraph()
    short_token = token_address[:4] + "..."
    G.add_node(token_address, label=f"Token\n{short_token}", color="#ffd700", size=25, shape="star")
    
    risk_score = 0
    funder_map = {}
    
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    for i, whale in enumerate(unique_whales):
        status_text.text(f"正在調查大戶 {i+1}/{len(unique_whales)}: {whale[:4]}...")
        progress_bar.progress((i + 1) / len(unique_whales))
        
        G.add_node(whale, label=f"Holder\n{whale[:4]}...", color="#97c2fc", size=15)
        G.add_edge(whale, token_address, color="#cccccc")
        
        funder = trace_funder(whale)
        if funder:
            if funder not in G:
                G.add_node(funder, label=f"🚨 SOURCE\n{funder[:4]}...", color="#ff4b4b", size=20, shape="box")
            G.add_edge(funder, whale, color="#ff0000")
            
            funder_map[funder] = funder_map.get(funder, 0) + 1
            if funder_map[funder] > 1:
                risk_score += 10

    status_text.empty()
    progress_bar.empty()
    
    return G, risk_score

# ==========================================
# 3. 輔助功能
# ==========================================
def send_telegram_msg(msg):
    if not TG_TOKEN or not TG_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": msg}, timeout=5)
    except: pass

def scan_new_pairs():
    """
    雙重策略掃描：確保一定有幣可以看
    策略 1: 抓 'pump' 關鍵字的新幣 (最優先)
    策略 2: 抓 'sol' 關鍵字的熱門幣 (保底)
    """
    # 知名老幣地址黑名單 (只擋地址，不擋名字，以免誤殺 'Baby Solana')
    BLACKLIST_ADDR = [
        "So11111111111111111111111111111111111111112", # Wrapped SOL
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", # USDT
        "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So", # mSOL
    ]

    def fetch_and_filter(keyword, max_hours=24):
        try:
            url = f"https://api.dexscreener.com/latest/dex/search?q={keyword}"
            res = requests.get(url, timeout=10).json()
            raw_pairs = res.get('pairs', [])
            valid = []
            current_time = time.time() * 1000 
            
            for p in raw_pairs:
                # 1. 鏈檢查
                if p.get('chainId') != 'solana': continue
                
                # 2. 地址黑名單
                addr = p.get('baseToken', {}).get('address', '')
                if addr in BLACKLIST_ADDR: continue
                
                # 3. 流動性過濾 (太低無法交易)
                if p.get('liquidity', {}).get('usd', 0) < 500: continue
                
                # 4. 時間過濾 (如果有設定)
                created_at = p.get('pairCreatedAt', 0)
                if max_hours > 0 and created_at > 0:
                    age_hours = (current_time - created_at) / (1000 * 60 * 60)
                    if age_hours > max_hours: continue
                
                valid.append(p)
            
            # 按時間倒序 (最新的在前面)
            valid.sort(key=lambda x: x.get('pairCreatedAt', 0), reverse=True)
            return valid
        except: return []

    # --- 執行策略 ---
    
    # 策略 1: 找剛出爐的 Pump 幣 (限制 24 小時內)
    st.toast("正在搜尋 24h 內的新幣...")
    results = fetch_and_filter("pump", max_hours=24)
    
    # 策略 2: 如果沒東西，找最近熱門的 SOL 相關幣 (放寬到 7 天)
    if not results:
        st.toast("新幣過濾太嚴格，切換至熱門幣模式...")
        results = fetch_and_filter("sol", max_hours=168)
        
    # 策略 3: 如果還是沒東西，隨便抓 (不限時間，只求有數據)
    if not results:
        results = fetch_and_filter("sol", max_hours=0)

    # 回傳前 5 名
    return results[:5]
# ==========================================
# 4. 主介面 (UI)
# ==========================================
st.title("🚀 Solana 老鼠倉獵人 (Helius Pro)")

if not HELIUS_KEY:
    st.warning("⚠️ 請先在左側欄位輸入 Helius API Key！")

tab1, tab2 = st.tabs(["🔍 手動查幣", "🤖 自動掃描新幣"])

# --- TAB 1 ---
with tab1:
    target = st.text_input("輸入代幣地址", "2zMMhcVQhZkJeb4h5Rpp47aZPaej4XMs75c8V4Jkpump")
    if st.button("開始分析", key="btn1"):
        with st.spinner("🕵️‍♂️ 正在進行鏈上肉搜..."):
            G, risk_or_error = analyze_token(target)
            if G is None:
                st.error(f"分析失敗：{risk_or_error}")
            else:
                risk = risk_or_error
                if risk > 0:
                    st.error(f"🚨 警告！偵測到老鼠倉集團！風險指數: {risk}")
                else:
                    st.success("✅ 籌碼結構相對健康。")
                
                # 🔥 關鍵修正：加入 cdn_resources='in_line'
                net = Network(height="500px", width="100%", bgcolor="#222222", font_color="white", directed=True, cdn_resources='in_line')
                net.from_nx(G)
                net.save_graph("graph.html")
                with open("graph.html", "r", encoding="utf-8") as f:
                    components.html(f.read(), height=520)

# --- TAB 2 ---
with tab2:
    st.write("自動抓取 DexScreener Solana 熱門新幣。")
    if st.button("🛡️ 掃描市場新幣", key="btn2"):
        if not HELIUS_KEY:
             st.error("❌ 缺少 Helius API Key")
        else:
            pairs = scan_new_pairs()
            if not pairs:
                st.warning("暫無數據。")
            else:
                for pair in pairs:
                    name = pair.get('baseToken', {}).get('name', 'Unknown')
                    addr = pair.get('baseToken', {}).get('address', '')
                    price = pair.get('priceUsd', '0')
                    
                    st.markdown(f"**檢查代幣：{name}**")
                    st.code(addr)
                    st.write(f"Price: ${price}")
                    
                    G, risk_or_error = analyze_token(addr)
                    
                    if G is None:
                        st.warning(f"⚠️ 無法分析: {risk_or_error}")
                    else:
                        risk = risk_or_error
                        if risk > 0:
                            st.error(f"❌ 風險 (Risk: {risk})")
                            send_telegram_msg(f"🚨 危險新幣：{name}\n地址：{addr}\n風險：老鼠倉活躍！")
                        else:
                            st.success("✅ 安全")
                        
                        # 同樣加入 in_line 修正
                        net = Network(height="400px", width="100%", bgcolor="#222222", font_color="white", directed=True, cdn_resources='in_line')
                        net.from_nx(G)
                        fname = f"graph_{addr[:4]}.html"
                        net.save_graph(fname)
                        with open(fname, "r", encoding="utf-8") as f:
                            components.html(f.read(), height=420)
                    
                    st.divider()
