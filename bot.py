import os
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# --- 設定區 ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

TARGETS = {
    "🇹🇼 台積電": "2330.TW",
    "🇹🇼 保德信市值": "009803.TW",
    "🪙 Solana": "SOL-USD",
    "🪙 Render": "RENDER-USD"
}

TW_TZ = timezone(timedelta(hours=8))

def send_telegram(msg):
    if not TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"傳送失敗: {e}")

def get_crypto_fng():
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=5)
        return int(r.json()['data'][0]['value'])
    except:
        return None

def get_max_usdt_rate():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get("https://max-api.maicoin.com/api/v2/tickers/usdttwd", headers=headers, timeout=5)
        return float(r.json()['sell'])
    except:
        try:
            return float(yf.Ticker("USDTWD=X").history(period="1d")['Close'].iloc[-1])
        except:
            return 32.5

# --- V9.0 核心：指標計算 ---
def calculate_metrics(df, is_crypto=False):
    current = df['Close'].iloc[-1]
    
    # 均線
    ma10 = df['Close'].rolling(window=10).mean().iloc[-1]
    ma20 = df['Close'].rolling(window=20).mean().iloc[-1]
    ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
    
    # ATR
    high_low = df['High'] - df['Low']
    tr = pd.concat([high_low, (df['High']-df['Close'].shift()).abs(), (df['Low']-df['Close'].shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]
    
    # RSI
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1]

    # 歷史區間 (近60天)
    period_high = df['High'].iloc[-60:].max()
    period_low = df['Low'].iloc[-60:].min()
    
    # 趨勢強度 (均線乖離率)
    gap_percent = (ma20 - ma60) / ma60 * 100
    
    status = "盤整"
    
    # V9.0 判定邏輯優化
    if current > ma60:
        if gap_percent > 20: # 乖離極大，超級火箭 (針對 Crypto)
            status = "🚀 超級火箭"
        elif gap_percent > 8:
            status = "🔥 強多頭"
        else:
            status = "🐂 多頭"
    else:
        if gap_percent < -8:
            status = "🩸 崩盤"
        else:
            status = "🐻 空頭"
            
    return current, atr, ma10, ma20, ma60, period_low, status, rsi

def analyze_target(name, ticker, max_rate, crypto_fng_val):
    try:
        df = yf.Ticker(ticker).history(period="1y")
        if df.empty: return f"⚠️ {name}: 無資料\n"

        is_crypto = "USD" in ticker
        current, atr, ma10, ma20, ma60, period_low, status, rsi = calculate_metrics(df, is_crypto)
        
        # --- V9.0 策略定價 ---
        
        # 1. 積極價 (Aggressive)
        if "火箭" in status:
            p1 = ma10 # 噴出時掛 10日線
            d1 = "攻擊型 (10日線)"
        elif "強多頭" in status:
            p1 = ma20 # 強多掛月線
            d1 = "趨勢型 (月線)"
        else:
            # 盤整或空頭，掛短線波動低點
            p1 = current - (atr * 0.5)
            d1 = "短線波動"

        # 2. 穩健價 (Moderate)
        if "火箭" in status:
            p2 = ma20 # 火箭時，月線就是穩健買點
            d2 = "穩健追價 (月線)"
        elif "強多頭" in status:
            p2 = ma60 # 強多時，季線是穩健買點
            d2 = "波段支撐 (季線)"
        elif "崩盤" in status:
            # 崩盤時，穩健就是不買，或者掛非常低
            p2 = period_low * 0.9
            d2 = "崩盤觀望價"
        else:
            # 盤整時，掛季線 或 ATR
            atr_target = current - atr
            p2 = min(atr_target, ma60)
            d2 = "季線/ATR"

        # 3. 保守價 (Conservative) - 重點修正區
        if "超級火箭" in status:
            # V9.0 修正：超級噴出時，保守價上移至季線 (MA60)，不再看前低
            # 因為等前低會等到天荒地老
            p3 = ma60
            d3 = "動態防守 (季線)"
        elif "崩盤" in status:
            # V9.0 修正：崩盤時，保守價打 85 折 (Crypto) 或 92 折 (Stock)
            discount = 0.85 if is_crypto else 0.92
            p3 = period_low * discount
            d3 = "崩盤接刀"
        else:
            # 盤整或普通多空頭
            # V9.0 修正：盤整時，如果不看布林，改看「區間下緣 (Period Low)」
            # 並給予一點點寬容度 (Period Low + 1% )，避免像 2018-05 那樣差一點買不到
            p3 = period_low * 1.01 
            d3 = "區間地板 (寬容)"

        # 排序
        strategies = [(p1, d1), (p2, d2), (p3, d3)]
        strategies.sort(key=lambda x: x[0], reverse=True)
        
        # 輸出
        report = f"<b>{name}</b>\n"
        if is_crypto:
            price_txt = f"{current:.2f} U"
            if max_rate: price_txt += f" (約 {current*max_rate:.0f} NT)"
        else:
            price_txt = f"{current:.0f}"
            
        report += f"現價：<code>{price_txt}</code>\n"
        report += f"趨勢：{status} (RSI: {rsi:.0f})\n"
        
        valid_date = (datetime.now() + timedelta(days=14)).strftime('%m/%d')
        report += f"🛒 <b>智能掛單 (至 {valid_date})：</b>\n"
        
        colors = ["🟢", "🟡", "🔴"]
        labels = ["積極", "穩健", "保守"]
        
        for i in range(3):
            price, desc = strategies[i]
            if is_crypto and max_rate:
                p_str = f"{price:.2f} U ({price*max_rate:.0f} NT)"
            else:
                p_str = f"{price:.1f}"
            report += f"{i+1}. {colors[i]} {labels[i]}：<code>{p_str}</code> [{desc}]\n"
            
        report += "--------------------\n"
        return report

    except Exception as e:
        return f"⚠️ {name} 分析錯誤: {e}\n"

def main():
    now = datetime.now(TW_TZ)
    print(f"V9.0 執行時間: {now}")
    
    max_rate = get_max_usdt_rate()
    c_val = get_crypto_fng()
    
    msg = f"<b>📊 資產監控 V9.0 (全地形適應版)</b>\n📅 {now.strftime('%Y-%m-%d')}\n"
    if max_rate: msg += f"🇹🇼 MAX 匯率：{max_rate:.2f}\n\n"
    
    for name, ticker in TARGETS.items():
        msg += analyze_target(name, ticker, max_rate, c_val)
        
    msg += "\n💡 <i>V9 盲測修正：\n1. 針對「盤整盤」微調地板價，增加成交率。\n2. 針對「超級火箭」大幅上調掛單價，解決踏空問題。</i>"
    
    send_telegram(msg)

if __name__ == "__main__":
    main()
