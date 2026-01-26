import os
import requests
import yfinance as yf
import pandas as pd
import numpy as np
import math
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

def adjust_tw_price(price):
    if pd.isna(price): return 0
    if price < 10: tick = 0.01
    elif price < 50: tick = 0.05
    elif price < 100: tick = 0.1
    elif price < 500: tick = 0.5
    elif price < 1000: tick = 1.0
    else: tick = 5.0
    return math.ceil(price / tick) * tick

# --- V13.0 新增：七級情緒分析模組 ---
def get_sentiment_analysis(score):
    """
    輸入分數 (0-100)，回傳 (等級圖示, 狀態描述, 市場狀況簡述)
    """
    if score <= 10:
        return "💀 崩盤 (極度恐慌)", "血流成河，這是上帝的禮物，閉眼買。"
    elif score <= 25:
        return "🔴 熊市 (恐慌)", "市場悲觀，別人恐懼我貪婪，分批接。"
    elif score <= 40:
        return "🟠 焦慮 (緊張)", "信心動搖，尋找支撐，耐心等待。"
    elif score <= 59:
        return "⚪ 中立 (觀望)", "多空不明，不要隨意出手，保留現金。"
    elif score <= 74:
        return "🟢 回升 (貪婪)", "趨勢轉好，手上籌碼續抱，暫不加碼。"
    elif score <= 89:
        return "🚀 過熱 (極度貪婪)", "情緒高昂，風險劇增，絕對禁止追價。"
    else:
        return "🔥 泡沫 (瘋狂)", "最後煙火，人聲鼎沸，準備隨時閃人。"

# --- 核心邏輯 ---
def calculate_metrics(df_daily, is_crypto=False):
    df_daily = df_daily.dropna()
    if len(df_daily) < 20: return None

    # 1. 取得即時資訊 (用於緊急通知 & RSI計算)
    current_price = df_daily['Close'].iloc[-1]
    prev_close = df_daily['Close'].iloc[-2]
    daily_change_pct = (current_price - prev_close) / prev_close * 100
    
    # RSI (即時)
    delta = df_daily['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    daily_rsi = 100 - (100 / (1 + rs)).iloc[-1]

    # 2. 取得週線資訊 (用於定錨)
    df_weekly = df_daily.resample('W-FRI').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'
    }).dropna()
    
    if len(df_weekly) < 2: 
        ref_idx = -1 
        use_weekly = False
    else:
        ref_idx = -2 
        use_weekly = True

    close_series = df_weekly['Close'] if use_weekly else df_daily['Close']
    
    w_ma20 = close_series.rolling(window=20).mean().iloc[ref_idx]
    w_ma60 = close_series.rolling(window=60).mean().iloc[ref_idx]
    
    std20 = close_series.rolling(window=20).std().iloc[ref_idx]
    w_lower_bb = w_ma20 - (std20 * 2.0)
    
    if use_weekly:
        high_low = df_weekly['High'] - df_weekly['Low']
    else:
        high_low = (df_daily['High'] - df_daily['Low']) * 5
        
    w_atr = high_low.rolling(window=14).mean().iloc[ref_idx]

    # 3. 緊急訊號
    emergency = None
    if daily_change_pct < -5:
        emergency = f"📉 <b>閃崩警報 (單日跌 {daily_change_pct:.1f}%)</b>"
    elif daily_change_pct > 8:
        emergency = f"🚀 <b>噴出警報 (單日漲 {daily_change_pct:.1f}%)</b>"
    elif daily_rsi < 20:
        emergency = "🩸 <b>RSI 超賣 (恐慌極致)</b>"
    
    is_bear_market = current_price < w_ma60

    return current_price, w_ma20, w_ma60, w_lower_bb, w_atr, daily_rsi, is_bear_market, emergency

def analyze_target(name, ticker, max_rate, crypto_fng_val):
    try:
        df = yf.Ticker(ticker).history(period="2y") 
        if df.empty: return None, None

        is_crypto = "USD" in ticker
        data = calculate_metrics(df, is_crypto)
        if not data: return None, None
        
        current, w_ma20, w_ma60, w_lower_bb, w_atr, rsi, is_bear, emergency = data
        
        # --- 情緒指標計算 ---
        # Crypto 使用 API 抓到的 FNG 值
        # 台股 使用 RSI 模擬 FNG 值 (RSI 30=恐慌, 70=貪婪)
        if is_crypto and crypto_fng_val is not None:
            sentiment_score = crypto_fng_val
        else:
            sentiment_score = int(rsi) # 台股用 RSI 當作情緒分數
            
        sentiment_level, sentiment_desc = get_sentiment_analysis(sentiment_score)

        # --- 策略價格計算 ---
        strategies = []
        if is_bear:
            p1, d1, l1 = current - (w_atr * 0.5), "熊市反彈 (ATR)", "合理"
            p2, d2, l2 = min(w_lower_bb, w_ma60 - w_atr), "價值區 (布林下緣)", "便宜"
            discount = 0.90 if is_crypto else 0.95
            p3, d3, l3 = w_lower_bb * discount, "恐慌拋售 (破底價)", "超跌"
        else:
            p1, d1, l1 = w_ma20, "多頭回檔 (週MA20)", "合理"
            p2, d2, l2 = w_ma60, "牛熊分界 (週MA60)", "便宜"
            p3, d3, l3 = w_lower_bb, "統計極值 (布林下緣)", "超跌"

        valid_strategies = []
        for strat in [{"price": p1, "desc": d1, "label": l1},
                      {"price": p2, "desc": d2, "label": l2},
                      {"price": p3, "desc": d3, "label": l3}]:
            price = strat["price"]
            if not is_crypto: price = adjust_tw_price(price)
            if price >= current:
                if strat["label"] == "合理": buffer = 0.99
                elif strat["label"] == "便宜": buffer = 0.95
                else: buffer = 0.90
                price = current * buffer
                if not is_crypto: price = adjust_tw_price(price)
                strat["desc"] += " (修正)"
            strat["price"] = price
            valid_strategies.append(strat)

        valid_strategies.sort(key=lambda x: x["price"], reverse=True)

        # AI 推薦
        if is_bear: best_idx = 2
        elif rsi > 70: best_idx = 2
        else: best_idx = 1
        best_strat = valid_strategies[best_idx]
        
        colors = {"合理": "🟢", "便宜": "🟡", "超跌": "🔴"}
        
        # --- 輸出報表 ---
        report = f"<b>{name}</b>\n"
        
        # 價格與匯率
        if is_crypto:
            price_txt = f"{current:.2f} U"
            if max_rate: price_txt += f" (約 {current*max_rate:.0f} NT)"
            rec_str = f"{best_strat['price']:.2f} U"
            if "SOL" in ticker or "RENDER" in ticker:
                 if max_rate: rec_str += f" ({best_strat['price']*max_rate:.0f} NT)"
        else:
            price_txt = f"{current:.0f}"
            rec_str = f"{best_strat['price']:.0f}"
            
        report += f"現價：<code>{price_txt}</code>\n"
        
        # 情緒顯示
        report += f"情緒：{sentiment_level} ({sentiment_score})\n"
        report += f"💡 <i>{sentiment_desc}</i>\n\n"
        
        if emergency:
            report += f"{emergency}\n"
            report += f"⚠️ <b>建議暫停掛單，觀察支撐！</b>\n"
        else:
            report += f"🏆 首選：{colors[best_strat['label']]} <b><code>{rec_str}</code></b>\n"
        
        for item in valid_strategies:
            label = item['label']
            if is_crypto:
                 p_str = f"{item['price']:.2f} U"
                 if ("SOL" in ticker or "RENDER" in ticker) and max_rate:
                     p_str += f" ({item['price']*max_rate:.0f} NT)"
            else:
                 p_str = f"{item['price']:.0f}"
            report += f"• {colors[label]} {label}：<code>{p_str}</code>\n"
            
        report += "--------------------\n"
        return report, emergency

    except Exception as e:
        print(f"Error {name}: {e}")
        return None, None

def main():
    now = datetime.now(TW_TZ)
    print(f"V13.0 執行時間: {now}")
    
    max_rate = get_max_usdt_rate()
    c_val = get_crypto_fng()
    
    reports = []
    is_emergency_global = False
    
    for name, ticker in TARGETS.items():
        rep, emerg = analyze_target(name, ticker, max_rate, c_val)
        if rep:
            reports.append(rep)
            if emerg: is_emergency_global = True
    
    if is_emergency_global:
        header = "🚨🚨 <b>緊急：資產訊號警報</b> 🚨🚨\n"
        header += "<i>偵測到劇烈波動，請檢查下方紅字警示！</i>\n\n"
    else:
        today = datetime.now()
        days_ahead = 4 - today.weekday()
        if days_ahead < 0: days_ahead += 7
        next_fri = (today + timedelta(days=days_ahead)).strftime('%m/%d')
        
        header = f"📊 <b>週線囤貨日報 ({now.strftime('%m/%d')})</b>\n"
        if max_rate: header += f"🇹🇼 MAX 匯率：{max_rate:.2f}\n"
        header += f"📅 <b>本週掛單有效至：{next_fri} (週五)</b>\n"
        header += "✅ 結構穩健，無需頻繁改單。\n\n"

    final_msg = header + "".join(reports)
    send_telegram(final_msg)

if __name__ == "__main__":
    main()
