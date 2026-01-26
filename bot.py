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
    if pd.isna(price): return 0 # 防呆 NaN
    if price < 10: tick = 0.01
    elif price < 50: tick = 0.05
    elif price < 100: tick = 0.1
    elif price < 500: tick = 0.5
    elif price < 1000: tick = 1.0
    else: tick = 5.0
    return math.ceil(price / tick) * tick

# --- V11.1 核心：智能囤貨邏輯 (含熊市修正) ---
def calculate_metrics(df_daily, is_crypto=False):
    # 1. 資料預處理 (防呆 NaN)
    df_daily = df_daily.dropna()
    if len(df_daily) < 20: return None

    current_price = df_daily['Close'].iloc[-1]
    
    # 2. 嘗試轉換週線
    # 如果資料不足 60週 (約420天)，則降級使用日線分析
    use_weekly = len(df_daily) > 420
    
    if use_weekly:
        df_weekly = df_daily.resample('W-FRI').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'
        }).dropna()
        
        # 定錨點：上一週收盤
        ref_idx = -2 if len(df_weekly) >= 2 else -1
        
        w_ma20 = df_weekly['Close'].rolling(window=20).mean().iloc[ref_idx]
        w_ma60 = df_weekly['Close'].rolling(window=60).mean().iloc[ref_idx]
        
        # 週線布林通道
        w_std20 = df_weekly['Close'].rolling(window=20).std().iloc[ref_idx]
        w_lower_bb = w_ma20 - (w_std20 * 2.0)
        
        # 週線 ATR (用來計算熊市支撐)
        w_high_low = df_weekly['High'] - df_weekly['Low']
        w_atr = w_high_low.rolling(window=14).mean().iloc[ref_idx]
        
        # 週線 RSI
        delta = df_weekly['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        w_rsi = 100 - (100 / (1 + rs)).iloc[ref_idx]
        
        # 判斷趨勢：如果現價 < 週MA60，視為熊市
        is_bear_market = current_price < w_ma60
        
    else:
        # 資料不足 (針對 009803 等新股)，降級為日線邏輯
        w_ma20 = df_daily['Close'].rolling(window=20).mean().iloc[-1]
        w_ma60 = df_daily['Close'].rolling(window=60).mean().iloc[-1]
        
        std20 = df_daily['Close'].rolling(window=20).std().iloc[-1]
        w_lower_bb = w_ma20 - (std20 * 2.0)
        
        high_low = df_daily['High'] - df_daily['Low']
        w_atr = high_low.rolling(window=14).mean().iloc[-1] * 5 # 日ATR x 5 約等於週波動
        
        delta = df_daily['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        w_rsi = 100 - (100 / (1 + rs)).iloc[-1]
        
        is_bear_market = current_price < w_ma60

    # 判斷位階文字
    if is_bear_market: status = "🐻 熊市 (空頭排列)"
    elif current_price < w_ma20: status = "🟡 回檔 (整理區)"
    elif w_rsi > 75: status = "🔴 過熱 (慎入)"
    else: status = "🐂 多頭 (趨勢向上)"

    return current_price, w_ma20, w_ma60, w_lower_bb, w_atr, w_rsi, status, is_bear_market

def analyze_target(name, ticker, max_rate, crypto_fng_val):
    try:
        # 抓取 2年 資料
        df = yf.Ticker(ticker).history(period="2y") 
        if df.empty: return f"⚠️ {name}: 無資料\n"

        is_crypto = "USD" in ticker
        data = calculate_metrics(df, is_crypto)
        if not data: return f"⚠️ {name}: 資料不足 (太新或無成交)\n"
        
        current, w_ma20, w_ma60, w_lower_bb, w_atr, w_rsi, status, is_bear = data
        
        # --- V11.1 策略：區分 牛市 vs 熊市 ---
        strategies = []

        if is_bear:
            # === 熊市邏輯 (Bear Market) ===
            # 均線在頭上是壓力，不能買。改用 ATR 往下找支撐。
            
            # 1. 合理價：現價 - 0.5倍週波動 (搶反彈)
            p1 = current - (w_atr * 0.5)
            d1 = "熊市反彈 (ATR支撐)"
            l1 = "合理"

            # 2. 便宜價：布林下緣 (統計低點)
            # 如果布林下緣比 ATR 還高 (盤整時)，取較低者
            p2 = min(w_lower_bb, current - w_atr)
            d2 = "價值區 (布林下緣)"
            l2 = "便宜"

            # 3. 超跌價：布林下緣再打折 (恐慌底)
            # Render 等高波動幣種，折扣不要打太深，否則買不到，改 0.9
            discount = 0.90 if is_crypto else 0.95
            p3 = w_lower_bb * discount
            d3 = "恐慌拋售 (破底價)"
            l3 = "超跌"

        else:
            # === 牛市邏輯 (Bull Market) ===
            # 均線是支撐，回測均線買進。
            
            # 1. 合理價：週 MA20
            p1 = w_ma20
            d1 = "多頭回檔 (週MA20)"
            l1 = "合理"

            # 2. 便宜價：週 MA60
            p2 = w_ma60
            d2 = "牛熊分界 (週MA60)"
            l2 = "便宜"

            # 3. 超跌價：布林下緣
            p3 = w_lower_bb
            d3 = "統計極值 (布林下緣)"
            l3 = "超跌"
            
        strategies = [
            {"price": p1, "desc": d1, "label": l1},
            {"price": p2, "desc": d2, "label": l2},
            {"price": p3, "desc": d3, "label": l3}
        ]

        # --- 價格校正與防呆 ---
        valid_strategies = []
        for strat in strategies:
            price = strat["price"]
            
            # 台股校正
            if not is_crypto: price = adjust_tw_price(price)
            
            # 防呆：絕對不能高於現價
            if price >= current:
                # 依據標籤給予不同程度的下修
                if strat["label"] == "合理": buffer = 0.98
                elif strat["label"] == "便宜": buffer = 0.95
                else: buffer = 0.90
                
                price = current * buffer
                if not is_crypto: price = adjust_tw_price(price)
                strat["desc"] = "修正接刀 (趨勢向下)"

            strat["price"] = price
            valid_strategies.append(strat)

        # 排序
        valid_strategies.sort(key=lambda x: x["price"], reverse=True)

        # --- AI 推薦 ---
        # 熊市推超跌，牛市推便宜
        if is_bear:
            best_idx = 2 # 熊市只買超跌
            ai_reason = "處於空頭趨勢，嚴格執行「超跌價」掛單。"
        elif w_rsi > 70:
            best_idx = 2 # 過熱等超跌
            ai_reason = "短線過熱，耐心等待回測地板。"
        else:
            best_idx = 1 # 正常多頭買便宜 (MA60)
            ai_reason = "趨勢向上，掛「便宜價」分批佈局。"
            
        best_strat = valid_strategies[best_idx]
        
        # --- 輸出報表 ---
        colors = {"合理": "🟢", "便宜": "🟡", "超跌": "🔴"}
        
        report = f"<b>{name}</b>\n"
        if is_crypto:
            price_txt = f"{current:.2f} U"
            # 顯示 MAX 匯率換算
            if max_rate: 
                price_txt += f" (約 {current*max_rate:.0f} NT)"
            
            rec_str = f"{best_strat['price']:.2f} U"
            if "SOL" in ticker and max_rate:
                 rec_str += f" ({best_strat['price']*max_rate:.0f} NT)"
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
                 # 如果是 SOL，列表也顯示台幣
                 if "SOL" in ticker and max_rate:
                     p_str += f" ({item['price']*max_rate:.0f} NT)"
            else:
                 p_str = f"{item['price']:.0f}"
            report += f"• {colors[label]} {label}：<code>{p_str}</code> [{item['desc']}]\n"
            
        report += "--------------------\n"
        return report

    except Exception as e:
        return f"⚠️ {name} 分析錯誤: {e}\n"

def main():
    now = datetime.now(TW_TZ)
    print(f"V11.1 執行時間: {now}")
    
    max_rate = get_max_usdt_rate()
    c_val = get_crypto_fng()
    
    msg = f"<b>📊 資產監控 V11.1 (空頭修正版)</b>\n📅 {now.strftime('%Y-%m-%d')}\n"
    if max_rate: msg += f"🇹🇼 MAX 匯率：{max_rate:.2f}\n\n"
    
    for name, ticker in TARGETS.items():
        msg += analyze_target(name, ticker, max_rate, c_val)
        
    msg += "\n💡 <i>Fix: 修正台積電 NaN 錯誤與熊市掛單邏輯。SOL 價格已加入 MAX 匯率換算。</i>"
    
    send_telegram(msg)

if __name__ == "__main__":
    main()
