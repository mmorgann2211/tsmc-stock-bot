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

# --- V33 核心演算法 (真實 3日線重構) ---
def calculate_indicators(name, df):
    # 1. 計算日線均線 (MA7, 25, 50, 60, 99)
    ma_list = [7, 25, 50, 60, 99]
    for w in ma_list:
        df[f'MA{w}'] = df['Close'].rolling(w).mean()
    
    # 2. [V33 修正] 真實重採樣計算 3日線 MA
    # 將日線資料轉為 3日線 (取每3天的最後收盤價)
    df_3d = df.resample('3D').agg({'Close': 'last'})
    
    # 在 3D 資料上計算 MA7 (21天) 和 MA12 (36天)
    df_3d['3D_MA7'] = df_3d['Close'].rolling(7).mean()
    df_3d['3D_MA12'] = df_3d['Close'].rolling(12).mean()
    
    # 將 3D MA 映射回日線 (Forward Fill)
    # 這樣每一天都能抓到「當下最新的 3D MA 值」
    df = df.join(df_3d[['3D_MA7', '3D_MA12']], how='left')
    df['3D_MA7'] = df['3D_MA7'].ffill()
    df['3D_MA12'] = df['3D_MA12'].ffill()
    
    # 3. 計算均線糾結 (Squeeze) - 維持日線判斷
    def check_squeeze(row):
        values = []
        for w in ma_list:
            v = row.get(f'MA{w}')
            if pd.notna(v): values.append(v)
        
        if not values: return 0, False
        
        max_ma = max(values)
        min_ma = min(values)
        squeeze_rate = (max_ma - min_ma) / min_ma
        return squeeze_rate, squeeze_rate < 0.05

    last_idx = df.index[-1]
    sq_rate, is_sq = check_squeeze(df.loc[last_idx])
    
    # RSI 計算
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1]

    # [除錯用] 印出關鍵數值供驗證
    row = df.iloc[-1]
    print(f"🔍 {name} 驗證數據:")
    print(f"   - 現價: {row['Close']:.2f}")
    print(f"   - 日線 MA25: {row['MA25']:.2f}")
    print(f"   - 日線 MA99: {row['MA99']:.2f}")
    print(f"   - 3日線 MA7 (熊市月): {row['3D_MA7']:.2f}")
    print(f"   - 3日線 MA12 (熊市中): {row['3D_MA12']:.2f}")
    
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
        # 下載數據 (2年)
        df = yf.Ticker(ticker).history(period="2y")
        if df.empty: return None
        
        is_crypto = "USD" in ticker
        df, rsi, is_squeeze, squeeze_rate = calculate_indicators(name, df)
        
        current_price = df['Close'].iloc[-1]
        row = df.iloc[-1]
        
        if is_crypto and crypto_fng: score = crypto_fng
        else: score = int(rsi)
        sent_lv, sent_short = get_sentiment_analysis(score)
        
        # --- 策略判定 ---
        today = datetime.now(TW_TZ)
        is_early_month = today.day <= 10
        
        strategy_note = ""
        target_price = 0
        label = "觀望"
        emerg_msg = None
        
        # 1. 優先檢查變盤訊號
        if is_squeeze:
            label = "變盤"
            target_price = row['MA25'] 
            strategy_note = f"均線糾結{(squeeze_rate*100):.1f}%"
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
                    ma50 = row.get('MA50')
                    if pd.notna(ma50):
                        target_price = ma50
                        label = "牛市(中)"
                    else:
                        target_price = row['MA25'] * 0.95
                        label = "牛市(中)"
            else:
                # 🐻 熊市策略 (使用真實 3D MA)
                if is_early_month:
                    target_price = row['3D_MA7']
                    label = "熊市(月)"
                else:
                    target_price = row['3D_MA12']
                    label = "熊市(中)"

        if pd.isna(target_price) or target_price == 0:
            target_price = current_price * 0.9
            strategy_note = "資料不足保底"

        drop_pct = (target_price - current_price) / current_price * 100
        
        note_color = "green"
        if drop_pct >= 0:
            note_str = "已達標"
            note_color = "red" 
        else:
            note_str = f"({drop_pct:.1f}%)"
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

def save_widget_data(results, valid_until, max_rate, global_emerg):
    widget_data = []
    for item in results:
        if not item: continue
        
        lbl = item['best']['label']
        if "變盤" in lbl: color = "alert"
        elif "牛" in lbl: color = "red"
        elif "熊" in lbl: color = "green"
        else: color = "yellow"

        if "已達標" in item['best']['note']:
            color = "red"
        
        if item['is_crypto']:
            p_str = f"{item['current']:.2f}"
            sig_p = f"{item['best']['price']:.2f}"
        else:
            p_str = f"{item['current']:.0f}"
            sig_p = f"{item['best']['price']:.0f}"
            
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
    print(f"V33.0 True 3D Resampling: {now}")
    
    max_rate = get_max_usdt_rate()
    c_val = get_crypto_fng()
    
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
            
    save_widget_data(results, next_fri, max_rate, global_emerg)
    
    is_noon = (now.hour == 12)
    
    if global_emerg or is_noon:
        if global_emerg:
            header = "🚨 <b>【變盤警報】均線極度收斂</b> 🚨\n"
        else:
            header = f"☀️ <b>午間定時報告 ({now.strftime('%m/%d')})</b>\n有效至：{next_fri}\n\n"
            
        msgs = [generate_telegram_report(d, max_rate) for d in results]
        send_telegram(header + "".join(msgs))
    else:
        print("Silent Update")

if __name__ == "__main__":
    main()
