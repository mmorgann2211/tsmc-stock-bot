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
    if pd.isna(price) or price <= 0: return 0
    if price < 10: tick = 0.01
    elif price < 50: tick = 0.05
    elif price < 100: tick = 0.1
    elif price < 500: tick = 0.5
    elif price < 1000: tick = 1.0
    else: tick = 5.0
    return math.ceil(price / tick) * tick

# --- V14.0 新增：跌幅與台股限制計算 ---
def calculate_drop_info(current, target, is_crypto):
    # 1. 計算需跌幅度
    if current <= 0: return "N/A"
    drop_pct = (target - current) / current * 100
    
    note = f"({drop_pct:.1f}%)"
    
    # 2. 台股物理限制檢查 (漲跌幅 10%)
    if not is_crypto:
        # 計算本週還剩幾天 (包含今天)
        # 假設執行時間是早上，今天算一天。如果是下午，今天不算。
        # 簡單起見，我們算「剩餘交易日」
        today = datetime.now(TW_TZ)
        weekday = today.weekday() # 0=Mon, 4=Fri
        days_left = 4 - weekday
        if days_left < 0: days_left = 0 # 週末
        
        # 連續跌停極限公式：現價 * (0.9 ^ days_left)
        # 這裡加 1 是假設今天還沒收盤，今天也有可能跌停
        theoretical_min = current * (0.9 ** (days_left + 1))
        
        if target < theoretical_min:
            note += " ⚠️<b>本週難達</b>"
            
    return note

def get_sentiment_analysis(score):
    if score <= 10: return "💀 崩盤 (極度恐慌)", "血流成河，這是上帝的禮物，閉眼買。"
    elif score <= 25: return "🔴 熊市 (恐慌)", "市場悲觀，別人恐懼我貪婪，分批接。"
    elif score <= 40: return "🟠 焦慮 (緊張)", "信心動搖，尋找支撐，耐心等待。"
    elif score <= 59: return "⚪ 中立 (觀望)", "多空不明，不要隨意出手，保留現金。"
    elif score <= 74: return "🟢 回升 (貪婪)", "趨勢轉好，手上籌碼續抱，暫不加碼。"
    elif score <= 89: return "🚀 過熱 (極度貪婪)", "情緒高昂，風險劇增，絕對禁止追價。"
    else: return "🔥 泡沫 (瘋狂)", "最後煙火，人聲鼎沸，準備隨時閃人。"

def calculate_metrics(df_daily, is_crypto=False):
    df_daily = df_daily.dropna()
    if len(df_daily) < 20: return None

    current_price = df_daily['Close'].iloc[-1]
    prev_close = df_daily['Close'].iloc[-2]
    daily_change_pct = (current_price - prev_close) / prev_close * 100
    
    # RSI
    delta = df_daily['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    daily_rsi = 100 - (100 / (1 + rs)).iloc[-1]

    # 週線處理
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
    
    # 計算均線 (防呆：如果 NaN 改用短週期替代)
    w_ma20 = close_series.rolling(window=20).mean().iloc[ref_idx]
    
    # Fix 0元問題：如果資料不足算不出 MA60，改用 MA20 * 0.9 (九折) 暫代
    w_ma60 = close_series.rolling(window=60).mean().iloc[ref_idx]
    if pd.isna(w_ma60) or w_ma60 == 0:
        w_ma60 = w_ma20 * 0.9

    std20 = close_series.rolling(window=20).std().iloc[ref_idx]
    w_lower_bb = w_ma20 - (std20 * 2.0)
    
    if use_weekly:
        high_low = df_weekly['High'] - df_weekly['Low']
    else:
        high_low = (df_daily['High'] - df_daily['Low']) * 5
        
    w_atr = high_low.rolling(window=14).mean().iloc[ref_idx]

    # 緊急訊號
    emergency = None
    if daily_change_pct < -5 and not is_crypto: # 台股5%算大跌
        emergency = f"📉 <b>閃崩警報 (單日跌 {daily_change_pct:.1f}%)</b>"
    elif daily_change_pct < -8 and is_crypto:
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
        
        # 情緒分數
        if is_crypto and crypto_fng_val is not None:
            sentiment_score = crypto_fng_val
        else:
            sentiment_score = int(rsi)
        sentiment_level, sentiment_desc = get_sentiment_analysis(sentiment_score)

        # --- 策略計算 ---
        strategies = []
        if is_bear:
            p1 = current - (w_atr * 0.5)
            p2 = min(w_lower_bb, w_ma60 - w_atr)
            discount = 0.90 if is_crypto else 0.95
            p3 = w_lower_bb * discount
        else:
            p1 = w_ma20
            p2 = w_ma60
            p3 = w_lower_bb

        # --- 價格優化與排序 ---
        # 1. 收集所有價格
        raw_prices = [p1, p2, p3]
        
        # 2. 台股校正 & 防呆
        valid_prices = []
        for p in raw_prices:
            # Fix 0元問題：如果計算出來 <= 0，強制過濾
            if pd.isna(p) or p <= 0: continue
            
            if not is_crypto: p = adjust_tw_price(p)
            
            # 防呆：絕對不能高於現價 (囤貨原則)
            if p >= current:
                p = current * 0.99
                if not is_crypto: p = adjust_tw_price(p)
            
            valid_prices.append(p)
        
        # 3. 去重並由高到低排序 (確保 🟢 > 🟡 > 🔴)
        valid_prices = sorted(list(set(valid_prices)), reverse=True)
        
        # 4. 重新分配標籤 (高=合理, 中=便宜, 低=超跌)
        # 如果只有 2 個價格，就只顯示合理跟便宜
        final_strategies = []
        labels = ["合理", "便宜", "超跌"] # 對應 高 -> 低
        
        for i, price in enumerate(valid_prices):
            if i >= 3: break # 最多顯示3個
            label = labels[i]
            
            # 計算跌幅與台股限制
            drop_info = calculate_drop_info(current, price, is_crypto)
            
            final_strategies.append({
                "price": price,
                "label": label,
                "note": drop_info
            })

        # AI 推薦
        if is_bear: best_idx = len(final_strategies) - 1 # 熊市選最低
        elif rsi > 70: best_idx = len(final_strategies) - 1 # 過熱選最低
        else: best_idx = min(1, len(final_strategies) - 1) # 正常選中間(便宜)
        
        best_strat = final_strategies[best_idx]
        
        # --- 輸出 ---
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
            
        report += f"現價：<code>{price_txt}</code>\n"
        report += f"情緒：{sentiment_level} ({sentiment_score})\n"
        report += f"💡 <i>{sentiment_desc}</i>\n\n"
        
        if emergency:
            report += f"{emergency}\n"
            report += f"⚠️ <b>建議觀察 {best_strat['price']:.0f} 是否有撐！</b>\n"
        else:
            # 顯示跌幅
            report += f"🏆 首選：{colors[best_strat['label']]} <b><code>{rec_str}</code></b> {best_strat['note']}\n"
        
        for item in final_strategies:
            label = item['label']
            if is_crypto:
                 p_str = f"{item['price']:.2f} U"
                 if ("SOL" in ticker or "RENDER" in ticker) and max_rate:
                     p_str += f" ({item['price']*max_rate:.0f} NT)"
            else:
                 p_str = f"{item['price']:.0f}"
            
            report += f"• {colors[label]} {label}：<code>{p_str}</code> {item['note']}\n"
            
        report += "--------------------\n"
        return report, emergency

    except Exception as e:
        print(f"Error {name}: {e}")
        return None, None

def main():
    now = datetime.now(TW_TZ)
    print(f"V14.0 執行時間: {now}")
    
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
