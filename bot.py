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

# --- V12.0 核心：週線鎖定 + 緊急偵測 ---
def calculate_metrics(df_daily, is_crypto=False):
    df_daily = df_daily.dropna()
    if len(df_daily) < 20: return None

    # 1. 取得「即時」資訊 (用於緊急偵測)
    current_price = df_daily['Close'].iloc[-1]
    prev_close = df_daily['Close'].iloc[-2]
    daily_change_pct = (current_price - prev_close) / prev_close * 100
    
    # 日線 RSI (即時情緒)
    delta = df_daily['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    daily_rsi = 100 - (100 / (1 + rs)).iloc[-1]

    # 2. 取得「週線」資訊 (用於定錨掛單)
    # 強制鎖定：不管今天是星期幾，都只看「上週五」收盤的數據
    # 這樣確保週一到週五算出來的掛單價完全一樣
    df_weekly = df_daily.resample('W-FRI').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'
    }).dropna()
    
    # 確保有上週的資料
    if len(df_weekly) < 2: 
        # 資料不足時降級使用日線
        ref_idx = -1 
        use_weekly = False
    else:
        # 關鍵：取 -2 (上週五) 作為定錨點
        ref_idx = -2 
        use_weekly = True

    # 計算定錨指標
    close_series = df_weekly['Close'] if use_weekly else df_daily['Close']
    
    w_ma20 = close_series.rolling(window=20).mean().iloc[ref_idx]
    w_ma60 = close_series.rolling(window=60).mean().iloc[ref_idx]
    
    # 布林通道
    std20 = close_series.rolling(window=20).std().iloc[ref_idx]
    w_lower_bb = w_ma20 - (std20 * 2.0)
    
    # ATR
    if use_weekly:
        high_low = df_weekly['High'] - df_weekly['Low']
    else:
        high_low = (df_daily['High'] - df_daily['Low']) * 5 # 日轉週估算
        
    w_atr = high_low.rolling(window=14).mean().iloc[ref_idx]

    # 3. 判斷緊急訊號 (Emergency Trigger)
    emergency = None
    if daily_change_pct < -5:
        emergency = f"📉 <b>閃崩警報 (單日跌 {daily_change_pct:.1f}%)</b>"
    elif daily_change_pct > 8:
        emergency = f"🚀 <b>噴出警報 (單日漲 {daily_change_pct:.1f}%)</b>"
    elif daily_rsi < 20:
        emergency = "🩸 <b>RSI 超賣 (恐慌極致)</b>"
    
    # 判斷大趨勢 (牛/熊)
    is_bear_market = current_price < w_ma60

    return current_price, w_ma20, w_ma60, w_lower_bb, w_atr, daily_rsi, is_bear_market, emergency

def analyze_target(name, ticker, max_rate, crypto_fng_val):
    try:
        df = yf.Ticker(ticker).history(period="2y") 
        if df.empty: return None, None # 改回傳 Tuple

        is_crypto = "USD" in ticker
        data = calculate_metrics(df, is_crypto)
        if not data: return None, None
        
        current, w_ma20, w_ma60, w_lower_bb, w_atr, rsi, is_bear, emergency = data
        
        # --- 策略價格計算 (週線鎖定) ---
        strategies = []

        if is_bear:
            # 熊市掛 ATR 與 布林
            p1 = current - (w_atr * 0.5) # 這裡稍微需要參考現價，因為是接刀
            d1 = "熊市反彈 (ATR)"
            l1 = "合理"

            p2 = min(w_lower_bb, w_ma60 - w_atr) # 確保比均線低
            d2 = "價值區 (布林下緣)"
            l2 = "便宜"

            discount = 0.90 if is_crypto else 0.95
            p3 = w_lower_bb * discount
            d3 = "恐慌拋售 (破底價)"
            l3 = "超跌"
        else:
            # 牛市掛均線 (這些都是定錨的，一週不變)
            p1 = w_ma20
            d1 = "多頭回檔 (週MA20)"
            l1 = "合理"

            p2 = w_ma60
            d2 = "牛熊分界 (週MA60)"
            l2 = "便宜"

            p3 = w_lower_bb
            d3 = "統計極值 (布林下緣)"
            l3 = "超跌"

        # --- 價格校正 ---
        valid_strategies = []
        for strat in [
            {"price": p1, "desc": d1, "label": l1},
            {"price": p2, "desc": d2, "label": l2},
            {"price": p3, "desc": d3, "label": l3}
        ]:
            price = strat["price"]
            if not is_crypto: price = adjust_tw_price(price)
            
            # 防呆：掛單不能高於現價
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
        
        # 產生報告文字
        colors = {"合理": "🟢", "便宜": "🟡", "超跌": "🔴"}
        
        report = f"<b>{name}</b>\n"
        if is_crypto:
            price_txt = f"{current:.2f} U"
            if max_rate: price_txt += f" (約 {current*max_rate:.0f} NT)"
            rec_str = f"{best_strat['price']:.2f} U"
            if "SOL" in ticker or "RENDER" in ticker:
                 if max_rate: rec_str += f" ({best_strat['price']*max_rate:.0f} NT)"
        else:
            price_txt = f"{current:.0f}"
            rec_str = f"{best_strat['price']:.0f}"
            
        report += f"現價：<code>{price_txt}</code> (RSI: {rsi:.0f})\n"
        
        # 顯示緊急訊號
        if emergency:
            report += f"{emergency}\n"
            report += f"💡 <i>建議：暫停掛單，觀察 {best_strat['price']:.1f} 是否有撐！</i>\n"
        else:
            report += f"🏆 首選：{colors[best_strat['label']]} <b><code>{rec_str}</code></b>\n"
        
        # 列表
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
        
        return report, emergency # 回傳報告與緊急狀態

    except Exception as e:
        print(e)
        return None, None

def main():
    now = datetime.now(TW_TZ)
    print(f"V12.0 執行時間: {now}")
    
    max_rate = get_max_usdt_rate()
    c_val = get_crypto_fng()
    
    # 收集所有標的報告
    reports = []
    is_emergency_global = False
    
    for name, ticker in TARGETS.items():
        rep, emerg = analyze_target(name, ticker, max_rate, c_val)
        if rep:
            reports.append(rep)
            if emerg: is_emergency_global = True
    
    # --- V12.0 決定標題 (Header Logic) ---
    if is_emergency_global:
        header = "🚨🚨 <b>緊急：資產訊號警報</b> 🚨🚨\n"
        header += "<i>偵測到劇烈波動，請檢查下方紅字警示！</i>\n\n"
    else:
        # 計算下週五
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
