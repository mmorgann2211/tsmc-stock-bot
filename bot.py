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
    """台股價格校正 (無條件進位)"""
    if price < 10: tick = 0.01
    elif price < 50: tick = 0.05
    elif price < 100: tick = 0.1
    elif price < 500: tick = 0.5
    elif price < 1000: tick = 1.0
    else: tick = 5.0
    return math.ceil(price / tick) * tick

# --- V11.0 核心：低價囤貨邏輯 (Weekly Value Investing) ---
def calculate_metrics(df_daily, is_crypto=False):
    # 1. 轉週線 (Weekly Resample) - 過濾短線雜訊
    df_weekly = df_daily.resample('W-FRI').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last'
    })
    
    # 確保資料足夠
    if len(df_weekly) < 60: return None

    current_price = df_daily['Close'].iloc[-1]
    
    # 定錨點：參考「上一週收盤」的數值 (本週固定)
    ref_idx = -1 
    
    # 週線均線 (大趨勢)
    w_ma20 = df_weekly['Close'].rolling(window=20).mean().iloc[ref_idx] # 週月線 (中線成本)
    w_ma60 = df_weekly['Close'].rolling(window=60).mean().iloc[ref_idx] # 週季線 (長線成本)
    
    # 週線布林通道 (統計學超跌區)
    w_std20 = df_weekly['Close'].rolling(window=20).std().iloc[ref_idx]
    w_lower_bb = w_ma20 - (w_std20 * 2) # 布林下緣
    
    # 週線 RSI (判斷是否過熱)
    delta = df_weekly['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    w_rsi = 100 - (100 / (1 + rs)).iloc[ref_idx]

    # 判斷目前價格位階
    status = "盤整區"
    if current_price < w_ma60: status = "🟢 低估區 (熊市)"
    elif current_price < w_ma20: status = "🟡 合理區 (回檔)"
    elif w_rsi > 75: status = "🔴 過熱區 (慎入)"
    else: status = "📈 趨勢向上"

    return current_price, w_ma20, w_ma60, w_lower_bb, w_rsi, status

def analyze_target(name, ticker, max_rate, crypto_fng_val):
    try:
        # 抓取長週期資料
        df = yf.Ticker(ticker).history(period="2y") 
        if df.empty: return f"⚠️ {name}: 無資料\n"

        is_crypto = "USD" in ticker
        data = calculate_metrics(df, is_crypto)
        if not data: return f"⚠️ {name}: 資料不足\n"
        
        current, w_ma20, w_ma60, w_lower_bb, w_rsi, status = data
        
        # --- V11.0 囤貨策略：只買便宜，不追高 ---
        strategies = []

        # 策略A (合理價)：週MA20
        # 這是多頭市場回檔的第一個支撐，雖然不夠便宜，但適合不想空手的人
        p1, d1 = w_ma20, "合理估值 (週MA20)"
        strategies.append({"price": p1, "desc": d1, "label": "合理"})

        # 策略B (便宜價)：週MA60
        # 這是長線牛熊分界，買在這裡通常長期勝率極高
        p2, d2 = w_ma60, "價值投資 (週MA60)"
        strategies.append({"price": p2, "desc": d2, "label": "便宜"})

        # 策略C (超跌價)：週布林下緣
        # 這是統計學上的極端低點，通常伴隨恐慌，是囤貨最佳時機
        # 如果現在已經是「低估區 (熊市)」，我們要在布林下緣再打折，確保接到血流成河的籌碼
        if "低估區" in status:
            discount = 0.90 if is_crypto else 0.95
            p3 = w_lower_bb * discount
            d3 = "恐慌拋售 (破底價)"
        else:
            p3 = w_lower_bb
            d3 = "統計低點 (布林下緣)"
        strategies.append({"price": p3, "desc": d3, "label": "超跌"})

        # --- 價格校正與防呆 ---
        valid_strategies = []
        for strat in strategies:
            price = strat["price"]
            
            # 台股校正
            if not is_crypto: price = adjust_tw_price(price)
            
            # 防呆：因為是囤貨，絕對不買貴
            # 如果算出來的價格 > 現價，代表現在價格比均線還低
            # 這時候直接掛「現價」往下打一點點，確保買得比現在更便宜
            if price >= current:
                if strat["label"] == "合理": buffer = 0.99
                elif strat["label"] == "便宜": buffer = 0.95
                else: buffer = 0.90
                
                price = current * buffer
                if not is_crypto: price = adjust_tw_price(price)
                strat["desc"] += " (修正接刀)"

            valid_strategies.append(strat)

        # 排序：由高到低 (合理 -> 便宜 -> 超跌)
        valid_strategies.sort(key=lambda x: x["price"], reverse=True)

        # --- AI 囤貨推薦 ---
        # 邏輯：囤貨者最喜歡買綠色的 (超跌)，但如果沒跌那麼深，就分批買黃色的 (便宜)
        # 基本上不推薦買合理的 (太貴)，除非大牛市怕買不到
        
        best_pick_idx = 1 # 預設推薦「便宜價」
        ai_reason = "價格進入價值區，適合分批建倉。"

        if "低估區" in status:
            best_pick_idx = 2 # 推薦「超跌價」
            ai_reason = "市場恐慌，請貪婪！掛超跌價接血籌碼。"
        elif "過熱" in status:
            best_pick_idx = 2 # 推薦「超跌價」
            ai_reason = "目前過熱，耐心等待回測地板再買。"
        
        best_strat = valid_strategies[best_pick_idx]
        
        # --- 輸出報表 ---
        colors = {"合理": "🟢", "便宜": "🟡", "超跌": "🔴"} # 顏色代表價格高低
        
        report = f"<b>{name}</b>\n"
        if is_crypto:
            price_txt = f"{current:.2f} U"
            if max_rate: price_txt += f" (約 {current*max_rate:.0f} NT)"
            rec_str = f"{best_strat['price']:.2f} U"
        else:
            price_txt = f"{current:.0f}"
            rec_str = f"{best_strat['price']:.0f}"
            
        report += f"現價：<code>{price_txt}</code>\n"
        report += f"位階：{status} (週RSI: {w_rsi:.0f})\n"
        
        report += f"🏆 <b>囤貨首選：{colors[best_strat['label']]} <code>{rec_str}</code></b> ({best_strat['label']})\n"
        report += f"💡 <i>{ai_reason}</i>\n\n"
        
        # 計算下週五
        today = datetime.now()
        days_ahead = 4 - today.weekday()
        if days_ahead < 0: days_ahead += 7
        next_fri = (today + timedelta(days=days_ahead)).strftime('%m/%d')
        
        report += f"📅 <b>本週掛單 (至 {next_fri})：</b>\n"
        for item in valid_strategies:
            label = item['label']
            if is_crypto:
                 p_str = f"{item['price']:.2f} U"
            else:
                 p_str = f"{item['price']:.0f}"
            report += f"• {colors[label]} {label}：<code>{p_str}</code> [{item['desc']}]\n"
            
        report += "--------------------\n"
        return report

    except Exception as e:
        return f"⚠️ {name} 分析錯誤: {e}\n"

def main():
    now = datetime.now(TW_TZ)
    print(f"V11.0 執行時間: {now}")
    
    max_rate = get_max_usdt_rate()
    c_val = get_crypto_fng()
    
    msg = f"<b>📊 資產監控 V11.0 (低價囤貨版)</b>\n📅 {now.strftime('%Y-%m-%d')}\n"
    if max_rate: msg += f"🇹🇼 MAX 匯率：{max_rate:.2f}\n\n"
    
    for name, ticker in TARGETS.items():
        msg += analyze_target(name, ticker, max_rate, c_val)
        
    msg += "\n💡 <i>V11 策略調整：\n專注於「週線級別」的低價籌碼。\n🟢 合理 = 週MA20\n🟡 便宜 = 週MA60\n🔴 超跌 = 布林下緣</i>"
    
    send_telegram(msg)

if __name__ == "__main__":
    main()
