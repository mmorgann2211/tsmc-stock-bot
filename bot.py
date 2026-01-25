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

# --- 核心邏輯 ---
def calculate_metrics(df, is_crypto=False):
    current = df['Close'].iloc[-1]
    
    ma10 = df['Close'].rolling(window=10).mean().iloc[-1]
    ma20 = df['Close'].rolling(window=20).mean().iloc[-1]
    ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
    
    high_low = df['High'] - df['Low']
    tr = pd.concat([high_low, (df['High']-df['Close'].shift()).abs(), (df['Low']-df['Close'].shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]
    
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1]

    period_low = df['Low'].iloc[-60:].min()
    gap_percent = (ma20 - ma60) / ma60 * 100
    
    status = "盤整"
    if current > ma60:
        if gap_percent > 20: status = "🚀 超級火箭"
        elif gap_percent > 8: status = "🔥 強多頭"
        else: status = "🐂 多頭"
    else:
        if gap_percent < -8: status = "🩸 崩盤"
        else: status = "🐻 空頭"
            
    return current, atr, ma10, ma20, ma60, period_low, status, rsi

def analyze_target(name, ticker, max_rate, crypto_fng_val):
    try:
        df = yf.Ticker(ticker).history(period="1y")
        if df.empty: return f"⚠️ {name}: 無資料\n"

        is_crypto = "USD" in ticker
        current, atr, ma10, ma20, ma60, period_low, status, rsi = calculate_metrics(df, is_crypto)
        
        # --- 1. 計算策略價格 ---
        if "火箭" in status: p1, d1 = ma10, "攻擊型 (10日線)"
        elif "強多頭" in status: p1, d1 = ma20, "趨勢型 (月線)"
        else: p1, d1 = current - (atr * 0.5), "短線波動"

        if "火箭" in status: p2, d2 = ma20, "穩健追價 (月線)"
        elif "強多頭" in status: p2, d2 = ma60, "波段支撐 (季線)"
        elif "崩盤" in status: p2, d2 = period_low * 0.9, "崩盤觀望價"
        else:
            atr_target = current - atr
            p2, d2 = min(atr_target, ma60), "季線/ATR"

        if "超級火箭" in status: p3, d3 = ma60, "動態防守 (季線)"
        elif "崩盤" in status:
            discount = 0.85 if is_crypto else 0.92
            p3, d3 = period_low * discount, "崩盤接刀"
        else: p3, d3 = period_low * 1.01, "區間地板 (寬容)"

        # --- 2. 價格校正 (V9.6: 階梯式防呆修正) ---
        raw_strategies = [(p1, d1, "積極"), (p2, d2, "穩健"), (p3, d3, "保守")]
        safe_strategies = []
        
        for price, desc, label in raw_strategies:
            # A. 現價防呆：確保掛單 < 現價
            if price >= current:
                # 依據策略屬性，給予不同的緩衝距離
                if label == "積極":
                    buffer = 0.5 # 積極者只讓 0.5 ATR
                elif label == "穩健":
                    buffer = 1.0 # 穩健者讓 1.0 ATR
                else:
                    buffer = 1.5 # 保守者讓 1.5 ATR (跌很深才接)
                
                price = current - (atr * buffer)
                
                # 最後防線：如果 ATR 極小，還是可能 >= 現價，強制打折
                if price >= current: price = current * 0.99
                
                desc += " (跌破修正)"
            
            # B. 台股檔位修正
            if not is_crypto:
                price = adjust_tw_price(price)
                if price >= current:
                    price = adjust_tw_price(current * 0.995)

            safe_strategies.append((price, desc, label))
        
        safe_strategies.sort(key=lambda x: x[0], reverse=True)

        # --- 3. AI 推薦機制 ---
        best_pick_idx = 0 
        ai_reason = ""
        colors = {"積極": "🟢", "穩健": "🟡", "保守": "🔴"}

        if "火箭" in status or "強多頭" in status:
            for i, (p, d, l) in enumerate(safe_strategies):
                if l == "穩健":
                    best_pick_idx = i
                    ai_reason = "🚀 趨勢強勁，AI 推薦「穩健」均線，兼顧上車與安全。"
                    break
        elif "崩盤" in status or "空頭" in status:
            for i, (p, d, l) in enumerate(safe_strategies):
                if l == "保守":
                    best_pick_idx = i
                    ai_reason = "🐻 趨勢向下，AI 推薦「保守」地板價，拒絕接刀。"
                    break
        else:
            for i, (p, d, l) in enumerate(safe_strategies):
                if l == "保守":
                    best_pick_idx = i
                    ai_reason = "🐢 盤整震盪，AI 推薦「保守」區間下緣，低買高賣。"
                    break

        best_price, best_desc, best_label = safe_strategies[best_pick_idx]
        best_color = colors[best_label]

        # --- 戰術備註 ---
        note = ""
        if best_label == "穩健":
            note = "⚠️ <b>追價提醒：</b>\n1. 請<b>分批進場</b>，勿 All-in。\n2. 若 RSI > 80，請考慮暫緩。"
        elif best_label == "保守":
            note = "🛡️ <b>防守提醒：</b>\n1. <b>嚴格遵守掛單價</b>，沒買到就算了。\n2. 這是接刀操作，建議<b>預留現金</b>。"
        else:
            note = "⚡ <b>短線提醒：</b>\n1. 攻擊型操作，風險較高。\n2. 跌破 10日線 請務必停損。"

        # --- 4. 輸出報表 ---
        report = f"<b>{name}</b>\n"
        if is_crypto:
            price_txt = f"{current:.2f} U"
            if max_rate: price_txt += f" (約 {current*max_rate:.0f} NT)"
            rec_price_str = f"{best_price:.2f} U"
            if max_rate: rec_price_str += f" ({best_price*max_rate:.0f} NT)"
        else:
            price_txt = f"{current:.0f}"
            rec_price_str = f"{best_price:.2f}"
            if best_price.is_integer(): rec_price_str = f"{int(best_price)}"
            
        report += f"現價：<code>{price_txt}</code>\n"
        report += f"趨勢：{status} (RSI: {rsi:.0f})\n"
        
        report += f"🏆 <b>AI 首選：{best_color} <code>{rec_price_str}</code></b> ({best_label})\n"
        report += f"💡 <i>{ai_reason}</i>\n"
        report += f"{note}\n\n"
        
        valid_date = (datetime.now() + timedelta(days=14)).strftime('%m/%d')
        report += f"🛒 <b>完整選項 (至 {valid_date})：</b>\n"
        
        for price, desc, label in safe_strategies:
            if is_crypto:
                if max_rate:
                    p_str = f"{price:.2f} U ({price*max_rate:.0f} NT)"
                else:
                    p_str = f"{price:.2f} U"
            else:
                p_str = f"{price:.2f}"
                if price.is_integer(): p_str = f"{int(price)}"
                
            report += f"• {colors[label]} {label}：<code>{p_str}</code> [{desc}]\n"
            
        report += "--------------------\n"
        return report

    except Exception as e:
        return f"⚠️ {name} 分析錯誤: {e}\n"

def main():
    now = datetime.now(TW_TZ)
    print(f"V9.6 執行時間: {now}")
    
    max_rate = get_max_usdt_rate()
    c_val = get_crypto_fng()
    
    msg = f"<b>📊 資產監控 V9.6 (階梯式修正版)</b>\n📅 {now.strftime('%Y-%m-%d')}\n"
    if max_rate: msg += f"🇹🇼 MAX 匯率：{max_rate:.2f}\n\n"
    
    for name, ticker in TARGETS.items():
        msg += analyze_target(name, ticker, max_rate, c_val)
        
    msg += "\n💡 <i>Fix: 修正股價跌破均線時，積極/穩健/保守價格會重疊的問題。現在會自動拉開安全階梯。</i>"
    
    send_telegram(msg)

if __name__ == "__main__":
    main()
