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

# 標的清單
TARGETS = {
    "🇹🇼 台積電": "2330.TW",
    "🇹🇼 保德信市值": "009803.TW",
    "🪙 Solana": "SOL-USD",
    "🪙 Render": "RENDER-USD"
}

TW_TZ = timezone(timedelta(hours=8))

# --- 基礎工具 ---
def send_telegram(msg):
    if not TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try: requests.post(url, data=payload)
    except Exception as e: print(f"Telegram Error: {e}")

def get_crypto_fng():
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=5)
        return int(r.json()['data'][0]['value'])
    except: return None

def get_max_usdt_rate():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get("https://max-api.maicoin.com/api/v2/tickers/usdttwd", headers=headers, timeout=5)
        return float(r.json()['sell'])
    except:
        try: return float(yf.Ticker("USDTWD=X").history(period="1d")['Close'].iloc[-1])
        except: return 32.5

def get_sentiment_analysis(score):
    if score <= 10: return "💀 崩盤", "血流成河"
    elif score <= 25: return "🔴 熊市", "極度恐慌"
    elif score <= 40: return "🟠 焦慮", "恐慌"
    elif score <= 59: return "⚪ 中立", "觀望"
    elif score <= 74: return "🟢 回升", "貪婪"
    elif score <= 89: return "🚀 過熱", "極度貪婪"
    else: return "🔥 泡沫", "快逃"

# --- V32 核心演算法 ---
def calculate_indicators(df):
    # 計算日線均線
    ma_list = [7, 25, 50, 60, 99]
    for w in ma_list:
        df[f'MA{w}'] = df['Close'].rolling(w).mean()
    
    # 計算 3日線均線 (近似值)
    # 3D_MA7 ≈ 日線 MA21
    # 3D_MA12 ≈ 日線 MA36
    df['3D_MA7'] = df['Close'].rolling(21).mean()
    df['3D_MA12'] = df['Close'].rolling(36).mean()
    
    # 計算均線糾結 (Squeeze)
    def check_squeeze(row):
        values = []
        for w in ma_list:
            v = row.get(f'MA{w}')
            if pd.notna(v): values.append(v)
        
        if not values: return 0, False
        
        max_ma = max(values)
        min_ma = min(values)
        squeeze_rate = (max_ma - min_ma) / min_ma
        return squeeze_rate, squeeze_rate < 0.05 # 5%內視為糾結

    # 應用到最後一筆資料
    last_idx = df.index[-1]
    sq_rate, is_sq = check_squeeze(df.loc[last_idx])
    
    # RSI 計算 (輔助判斷)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1]
    
    return df, rsi, is_sq, sq_rate

def get_dynamic_ma(row, primary_window, fallback_windows):
    val = row.get(f'MA{primary_window}')
    if pd.notna(val): return val
    for w in fallback_windows:
        val = row.get(f'MA{w}')
        if pd.notna(val): return val
    return None

def analyze_target(name, ticker, max_rate, crypto_fng):
    try:
        # 下載數據 (2年以確保 MA99 有值)
        df = yf.Ticker(ticker).history(period="2y")
        if df.empty: return None
        
        is_crypto = "USD" in ticker
        df, rsi, is_squeeze, squeeze_rate = calculate_indicators(df)
        
        current_price = df['Close'].iloc[-1]
        row = df.iloc[-1]
        
        # 取得情緒分數
        if is_crypto and crypto_fng: score = crypto_fng
        else: score = int(rsi)
        sent_lv, sent_short = get_sentiment_analysis(score)
        
        # --- 策略判定 (V32) ---
        today = datetime.now(TW_TZ)
        is_early_month = today.day <= 10
        
        strategy_note = ""
        target_price = 0
        label = "觀望"
        emerg_msg = None
        
        # 1. 優先檢查變盤訊號
        if is_squeeze:
            label = "變盤"
            target_price = row['MA25'] # 糾結時掛 MA25 吸籌
            strategy_note = f"均線糾結{(squeeze_rate*100):.1f}%"
            # 變盤訊號視為緊急通知
            emerg_msg = f"⚠️ 變盤訊號 (收斂{(squeeze_rate*100):.1f}%)"
        
        else:
            # 2. 趨勢判斷
            trend_ma = get_dynamic_ma(row, 99, [60, 50, 25])
            
            if trend_ma and current_price > trend_ma:
                # 🐂 牛市策略
                if is_early_month:
                    target_price = row['MA25']
                    label = "牛市(月)"
                else:
                    # 月中掛 MA50，若無則掛 MA25*0.95
                    ma50 = row.get('MA50')
                    if pd.notna(ma50):
                        target_price = ma50
                        label = "牛市(中)"
                    else:
                        target_price = row['MA25'] * 0.95
                        label = "牛市(中)"
            else:
                # 🐻 熊市策略 (3D均線)
                if is_early_month:
                    target_price = row['3D_MA7']
                    label = "熊市(月)"
                else:
                    target_price = row['3D_MA12']
                    label = "熊市(中)"

        # 價格防呆 (若資料不足)
        if pd.isna(target_price) or target_price == 0:
            target_price = current_price * 0.9
            strategy_note = "資料不足保底"

        # 計算跌幅需求
        drop_pct = (target_price - current_price) / current_price * 100
        
        # 跌幅過小(或已經跌破)的處理
        note_color = "green"
        if drop_pct >= 0:
            note_str = "已達標"
            note_color = "red" # 價格低於掛單價，強力買進
        else:
            note_str = f"({drop_pct:.1f}%)"
            # 如果跌幅需求 > 20%，標記難達
            if drop_pct < -20: note_color = "gray" 
        
        if strategy_note == "":
            strategy_note = f"目標: {label}"

        return {
            "name": name, 
            "ticker": ticker, 
            "is_crypto": is_crypto,
            "current": current_price, 
            "score": score, 
            "sent_lv": sent_lv, 
            "sent_short": sent_short,
            "emerg": emerg_msg,
            "best": {
                "price": target_price,
                "label": label,
                "note": note_str,
                "strategy": strategy_note,
                "color": note_color
            }
        }
    except Exception as e:
        print(f"Error {name}: {e}")
        return None

def generate_telegram_report(data, max_rate):
    if data['is_crypto']:
        p_txt = f"{data['current']:.2f} U"
        if max_rate: p_txt += f" (≈{data['current']*max_rate:.0f})"
        t_price = f"{data['best']['price']:.2f} U"
    else:
        p_txt = f"{data['current']:.0f}"
        t_price = f"{data['best']['price']:.0f}"

    msg = f"<b>{data['name']}</b>\n"
    msg += f"現價：<code>{p_txt}</code>\n"
    msg += f"情緒：{data['sent_lv']} ({data['score']})\n"
    
    if data['emerg']:
        msg += f"🚨 <b>{data['emerg']}</b>\n"
    
    msg += f"🎯 策略：<b>{data['best']['strategy']}</b>\n"
    msg += f"🛒 掛單：<code>{t_price}</code> {data['best']['note']}\n"
    msg += "--------------------\n"
    return msg

def load_previous_data():
    try:
        with open('widget_data.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except: return None

def check_if_changed(old_json, new_results, global_emerg):
    if not old_json: return True
    if global_emerg: return True
    
    # 這裡簡化判斷：只要有資料就更新，因為 V32 策略每天價格都會微調
    # 為了避免頻繁跳通知，我們只在「策略標籤改變」或「緊急狀態」時通知
    # 但中午 12 點會強制通知 (在 main 控制)
    return False 

def save_widget_data(results, valid_until, max_rate, global_emerg):
    widget_data = []
    for item in results:
        if not item: continue
        
        # 決定顏色
        lbl = item['best']['label']
        if "變盤" in lbl: color = "alert"
        elif "牛" in lbl: color = "red"  # 牛市掛單通常是紅字(追價/回檔)
        elif "熊" in lbl: color = "green" # 熊市掛單通常是綠字(低接)
        else: color = "yellow"

        # 若已達標(現價低於掛單價)，顯示紅色強力買進
        if "已達標" in item['best']['note']:
            color = "red"
        
        if item['is_crypto']:
            p_str = f"{item['current']:.2f}"
            sig_p = f"{item['best']['price']:.2f}"
        else:
            p_str = f"{item['current']:.0f}"
            sig_p = f"{item['best']['price']:.0f}"
            
        # 處理備註
        final_note = item['best']['note']
        if item['emerg']: final_note = "變盤訊號"

        icon = item['sent_lv'].split(" ")[0]
        
        widget_data.append({
            "name": item['name'].replace("🇹🇼 ", "").replace("🪙 ", ""),
            "price": p_str,
            "score": item['score'],
            "sent_icon": icon,
            "sent_text": item['sent_short'],
            "signal_label": lbl,
            "signal_price": sig_p,
            "signal_note": final_note,
            "signal_color": color,
            "is_crypto": item['is_crypto'],
            "emerg": item['emerg']
        })
        
    output = {
        "updated_at": datetime.now(TW_TZ).strftime('%m/%d %H:%M'),
        "valid_until": valid_until,
        "max_rate": max_rate,
        "global_emerg": global_emerg,
        "data": widget_data
    }
    
    with open('widget_data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

def main():
    now = datetime.now(TW_TZ)
    print(f"V32.0 Production: {now}")
    
    max_rate = get_max_usdt_rate()
    c_val = get_crypto_fng()
    
    # 計算有效期 (週五)
    days = 4 - now.weekday()
    if days < 0: days += 7
    next_fri = (now + timedelta(days=days)).strftime('%m/%d')
    
    results = []
    global_emerg = False
    
    for n, t in TARGETS.items():
        d = analyze_target(n, t, max_rate, c_val)
        if d:
            results.append(d)
            if d['emerg']: global_emerg = True
            
    # 存檔
    save_widget_data(results, next_fri, max_rate, global_emerg)
    
    # 通知邏輯
    # 1. 緊急訊號 (均線糾結) -> 通知
    # 2. 中午 12 點 (強制日報) -> 通知
    is_noon = (now.hour == 12)
    
    if global_emerg or is_noon:
        if global_emerg:
            header = "🚨 <b>【變盤警報】均線極度收斂</b> 🚨\n"
        else:
            header = f"☀️ <b>午間定時報告 ({now.strftime('%m/%d')})</b>\n有效至：{next_fri}\n\n"
            
        msgs = [generate_telegram_report(d, max_rate) for d in results]
        send_telegram(header + "".join(msgs))
    else:
        print("Silent Update (Not noon & No emergency)")

if __name__ == "__main__":
    main()
