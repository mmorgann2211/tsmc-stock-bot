import os
import requests
import yfinance as yf
import pandas as pd
import numpy as np
import math
import json
from datetime import datetime, timedelta, timezone

# --- 設定區 ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# 設定目標：名稱 vs 代號
TARGETS = {
    "🇹🇼 台積電": "2330.TW",
    "🇹🇼 保德信市值": "009803.TW",
    "🪙 Solana": "SOL-USD",
    "🪙 Render": "RENDER-USD"
}

# 設定時區 (台灣 +8)
TW_TZ = timezone(timedelta(hours=8))

# --- 基礎函式 ---

def send_telegram(msg):
    """發送 Telegram 訊息"""
    if not TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Telegram 傳送失敗: {e}")

def get_crypto_fng():
    """取得加密貨幣貪婪恐慌指數"""
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=5)
        return int(r.json()['data'][0]['value'])
    except:
        return None

def get_max_usdt_rate():
    """取得 MAX 交易所 USDT/TWD 匯率"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get("https://max-api.maicoin.com/api/v2/tickers/usdttwd", headers=headers, timeout=5)
        return float(r.json()['sell'])
    except:
        try:
            # 備用：Yahoo Finance
            return float(yf.Ticker("USDTWD=X").history(period="1d")['Close'].iloc[-1])
        except:
            return 32.5 # 最後備用值

def adjust_tw_price(price):
    """台股價格校正 (符合升降單位，並無條件進位)"""
    if pd.isna(price) or price <= 0: return 0
    
    if price < 10: tick = 0.01
    elif price < 50: tick = 0.05
    elif price < 100: tick = 0.1
    elif price < 500: tick = 0.5
    elif price < 1000: tick = 1.0
    else: tick = 5.0
    
    return math.ceil(price / tick) * tick

def get_sentiment_analysis(score):
    """七級情緒分析"""
    if score <= 10: return "💀 崩盤 (極度恐慌)", "血流成河，這是上帝的禮物，閉眼買。"
    elif score <= 25: return "🔴 熊市 (恐慌)", "市場悲觀，別人恐懼我貪婪，分批接。"
    elif score <= 40: return "🟠 焦慮 (緊張)", "信心動搖，尋找支撐，耐心等待。"
    elif score <= 59: return "⚪ 中立 (觀望)", "多空不明，不要隨意出手，保留現金。"
    elif score <= 74: return "🟢 回升 (貪婪)", "趨勢轉好，手上籌碼續抱，暫不加碼。"
    elif score <= 89: return "🚀 過熱 (極度貪婪)", "情緒高昂，風險劇增，絕對禁止追價。"
    else: return "🔥 泡沫 (瘋狂)", "最後煙火，人聲鼎沸，準備隨時閃人。"

def calculate_drop_info(current, target, is_crypto):
    """計算跌幅與台股物理限制"""
    if current <= 0: return "N/A"
    drop_pct = (target - current) / current * 100
    
    note = f"({drop_pct:.1f}%)"
    
    # 台股物理限制檢查 (漲跌幅 10%)
    if not is_crypto:
        # 計算本週還剩幾天 (包含今天)
        today = datetime.now(TW_TZ)
        weekday = today.weekday() # 0=Mon, 4=Fri
        days_left = 4 - weekday
        if days_left < 0: days_left = 0 # 週末
        
        # 連續跌停極限公式：現價 * (0.9 ^ (剩餘天數+1))
        # +1 是假設今天還沒收盤，今天也有可能跌停
        theoretical_min = current * (0.9 ** (days_left + 1))
        
        if target < theoretical_min:
            note += " ⚠️<b>本週難達</b>"
            
    return note

# --- 核心分析邏輯 ---

def calculate_metrics(df_daily, is_crypto=False):
    df_daily = df_daily.dropna()
    if len(df_daily) < 20: return None

    # 1. 取得即時資訊
    current_price = df_daily['Close'].iloc[-1]
    prev_close = df_daily['Close'].iloc[-2]
    daily_change_pct = (current_price - prev_close) / prev_close * 100
    
    # RSI (即時)
    delta = df_daily['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    daily_rsi = 100 - (100 / (1 + rs)).iloc[-1]

    # 2. 轉換週線 (定錨)
    # 強制鎖定：不管今天是星期幾，都只看「上週五」收盤的數據
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

    # 3. 緊急訊號判斷
    emergency = None
    if daily_change_pct < -5 and not is_crypto:
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
        # 抓取 2年 資料
        df = yf.Ticker(ticker).history(period="2y") 
        if df.empty: return None

        is_crypto = "USD" in ticker
        data = calculate_metrics(df, is_crypto)
        if not data: return None
        
        current, w_ma20, w_ma60, w_lower_bb, w_atr, rsi, is_bear, emergency = data
        
        # --- 情緒分數計算 ---
        if is_crypto and crypto_fng_val is not None:
            sentiment_score = crypto_fng_val
        else:
            sentiment_score = int(rsi)
        sentiment_level, sentiment_desc = get_sentiment_analysis(sentiment_score)

        # --- 策略價格計算 ---
        if is_bear:
            # 熊市邏輯
            p1 = current - (w_atr * 0.5)
            p2 = min(w_lower_bb, w_ma60 - w_atr)
            discount = 0.90 if is_crypto else 0.95
            p3 = w_lower_bb * discount
            raw_strategies = [
                {"price": p1, "label": "合理"},
                {"price": p2, "label": "便宜"},
                {"price": p3, "label": "超跌"}
            ]
        else:
            # 牛市邏輯
            raw_strategies = [
                {"price": w_ma20, "label": "合理"},
                {"price": w_ma60, "label": "便宜"},
                {"price": w_lower_bb, "label": "超跌"}
            ]

        # --- 價格優化與排序 ---
        valid_prices = []
        for s in raw_strategies:
            p = s["price"]
            # 過濾無效價格
            if pd.isna(p) or p <= 0: continue
            
            # 台股校正
            if not is_crypto: p = adjust_tw_price(p)
            
            # 防呆：掛單不能高於現價 (囤貨原則)
            if p >= current:
                p = current * 0.99
                if not is_crypto: p = adjust_tw_price(p)
            
            valid_prices.append(p)
        
        # 去重並由高到低排序 (確保 🟢 > 🟡 > 🔴)
        valid_prices = sorted(list(set(valid_prices)), reverse=True)
        
        # 重新分配標籤 (最多3個：高=合理, 中=便宜, 低=超跌)
        final_strategies = []
        labels_pool = ["合理", "便宜", "超跌"]
        
        for i, price in enumerate(valid_prices):
            if i >= 3: break
            label = labels_pool[i]
            
            # 計算跌幅文字
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
        
        # 避免空陣列
        if not final_strategies: return None

        best_strat = final_strategies[best_idx]
        
        # --- 準備回傳結構 (給主程式用) ---
        return {
            "name": name,
            "ticker": ticker,
            "is_crypto": is_crypto,
            "current": current,
            "rsi": rsi,
            "sentiment_score": sentiment_score,
            "sentiment_level": sentiment_level,
            "sentiment_desc": sentiment_desc,
            "emergency": emergency,
            "best_strat": best_strat,
            "strategies": final_strategies
        }

    except Exception as e:
        print(f"Error {name}: {e}")
        return None

# --- 產生 Telegram 訊息文字 ---
def generate_telegram_report(data, max_rate):
    if not data: return ""
    
    colors = {"合理": "🟢", "便宜": "🟡", "超跌": "🔴"}
    name = data['name']
    current = data['current']
    best = data['best_strat']
    is_crypto = data['is_crypto']
    
    report = f"<b>{name}</b>\n"
    
    # 價格顯示
    if is_crypto:
        price_txt = f"{current:.2f} U"
        if max_rate: price_txt += f" (約 {current*max_rate:.0f} NT)"
        
        rec_str = f"{best['price']:.2f} U"
        if ("SOL" in data['ticker'] or "RENDER" in data['ticker']) and max_rate:
             rec_str += f" ({best['price']*max_rate:.0f} NT)"
    else:
        price_txt = f"{current:.0f}"
        rec_str = f"{best['price']:.0f}"
        
    report += f"現價：<code>{price_txt}</code>\n"
    report += f"情緒：{data['sentiment_level']} ({data['sentiment_score']})\n"
    report += f"💡 <i>{data['sentiment_desc']}</i>\n\n"
    
    if data['emergency']:
        report += f"{data['emergency']}\n"
        report += f"⚠️ <b>建議暫停掛單，觀察 {best['price']:.0f} 是否有撐！</b>\n"
    else:
        report += f"🏆 首選：{colors[best['label']]} <b><code>{rec_str}</code></b> {best['note']}\n"
    
    # 列表
    for item in data['strategies']:
        label = item['label']
        if is_crypto:
             p_str = f"{item['price']:.2f} U"
             if ("SOL" in data['ticker'] or "RENDER" in data['ticker']) and max_rate:
                 p_str += f" ({item['price']*max_rate:.0f} NT)"
        else:
             p_str = f"{item['price']:.0f}"
        
        report += f"• {colors[label]} {label}：<code>{p_str}</code> {item['note']}\n"
        
    report += "--------------------\n"
    return report

# --- 儲存 Widget JSON ---
def save_widget_data(analyzed_list, valid_until, max_rate):
    widget_data = []
    
    for item in analyzed_list:
        if not item: continue
        
        # 轉換燈號顏色代碼
        label = item['best_strat']['label']
        if label == "合理": color = "green"
        elif label == "便宜": color = "yellow"
        else: color = "red"
        
        # 格式化價格
        if item['is_crypto']:
            price_str = f"{item['current']:.2f}"
            signal_price = f"{item['best_strat']['price']:.2f}"
        else:
            price_str = f"{item['current']:.0f}"
            signal_price = f"{item['best_strat']['price']:.0f}"

        widget_data.append({
            "name": item['name'].replace("🇹🇼 ", "").replace("🪙 ", ""),
            "price": price_str,
            "score": item['sentiment_score'],
            "signal_label": label,
            "signal_price": signal_price,
            "signal_color": color,
            "is_crypto": item['is_crypto']
        })
        
    output = {
        "updated_at": datetime.now(TW_TZ).strftime('%m/%d %H:%M'),
        "valid_until": valid_until,
        "max_rate": max_rate,
        "data": widget_data
    }
    
    with open('widget_data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Widget data saved.")

# --- 主程式 ---
def main():
    now = datetime.now(TW_TZ)
    print(f"V14.0 執行時間: {now}")
    
    max_rate = get_max_usdt_rate()
    c_val = get_crypto_fng()
    
    # 計算下週五
    today = datetime.now()
    days_ahead = 4 - today.weekday()
    if days_ahead < 0: days_ahead += 7
    next_fri = (today + timedelta(days=days_ahead)).strftime('%m/%d')
    
    analyzed_results = []
    is_emergency_global = False
    
    # 1. 執行分析
    for name, ticker in TARGETS.items():
        data = analyze_target(name, ticker, max_rate, c_val)
        if data:
            analyzed_results.append(data)
            if data['emergency']: is_emergency_global = True
            
    # 2. 產生 Telegram 訊息
    if is_emergency_global:
        header = "🚨🚨 <b>緊急：資產訊號警報</b> 🚨🚨\n"
        header += "<i>偵測到劇烈波動，請檢查下方紅字警示！</i>\n\n"
    else:
        header = f"📊 <b>週線囤貨日報 ({now.strftime('%m/%d')})</b>\n"
        if max_rate: header += f"🇹🇼 MAX 匯率：{max_rate:.2f}\n"
        header += f"📅 <b>本週掛單有效至：{next_fri} (週五)</b>\n"
        header += "✅ 結構穩健，無需頻繁改單。\n\n"
        
    reports_text = [generate_telegram_report(d, max_rate) for d in analyzed_results]
    final_msg = header + "".join(reports_text)
    
    send_telegram(final_msg)
    
    # 3. 儲存 Widget JSON
    save_widget_data(analyzed_results, next_fri, max_rate)

if __name__ == "__main__":
    main()
