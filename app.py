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
st.sidebar.markdown("請先在此輸入 Key 才能使用 👇")
HELIUS_KEY = st.sidebar.text_input("Helius API Key", type="password")
TG_TOKEN = st.sidebar.text_input("Telegram Bot Token (選填)", type="password")
TG_CHAT_ID = st.sidebar.text_input("Telegram Chat ID (選填)")

RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

# ==========================================
# 2. 核心功能：Helius 資金溯源
# ==========================================
def send_rpc(method, params):
    try:
        # 增加 timeout 防止卡死
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
    # 1. 檢查 Key
    if not HELIUS_KEY: 
        return None, "請先在左側輸入 Helius API Key"
    
    # 2. 檢查地址格式 (簡單防呆)
    if token_address.startswith("0x"):
        return None, "這是以太坊地址，Helius 只能查 Solana"

    # 3. 抓前 10 大股東
    res = send_rpc("getTokenLargestAccounts", [token_address])
    
    if 'error' in res:
        return None, f"API 錯誤: {res['error']['message']}"
    if 'result' not in res: 
        return None, "無效的代幣地址或查無數據"
    
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
    
    # 4. 畫圖 & 偵測
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
