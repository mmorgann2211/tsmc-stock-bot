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

def adjust_tw_price(price):
    if pd.isna(price) or price <= 0: return 0
    if price < 10: tick = 0.01
    elif price < 50: tick = 0.05
    elif price < 100: tick = 0.1
    elif price < 500: tick = 0.5
    elif price < 1000: tick = 1.0
    else: tick = 5.0
    return math.ceil(price / tick) * tick

def get_sentiment_analysis(score):
    if score <= 10: return "💀 崩盤", "血流成河"
    elif score <= 25: return "🔴 熊市", "極度恐慌"
    elif score <= 40: return "🟠 焦慮", "恐慌"
    elif score <= 59: return "⚪ 中立", "觀望"
    elif score <= 74: return "🟢 回升", "貪婪"
    elif score <= 89: return "🚀 過熱", "極度貪婪"
    else: return "🔥 泡沫", "快逃"

def get_psychological_note(label, is_bear):
    if label == "超跌": return "市場恐慌，確認資金可閒置2年以上，分批接刀。"
    elif label == "便宜": return "價格進入舒適區，不求最低，耐心累積籌碼。"
    elif label == "合理": return "趨勢回檔，建立基本部位，保持平常心。"
    return "觀望為主。"

def calculate_drop_info(current, target, is_crypto):
    if current <= 0: return ""
    drop_pct = (target - current) / current * 100
    note = f"({drop_pct:.1f}%)"
    if not is_crypto:
        today = datetime.now(TW_TZ)
        days_left = max(0, 4 - today.weekday())
        theoretical_min = current * (0.9 ** (days_left + 1))
        if target < theoretical_min: note = "⚠️本週難達"
    return note

# --- 核心運算 ---
def calculate_metrics(df_daily, is_crypto=False):
    df_daily = df_daily.dropna()
    if len(df_daily) < 20: return None

    current = df_daily['Close'].iloc[-1]
    prev = df_daily['Close'].iloc[-2]
    daily_chg = (current - prev) / prev * 100
    
    delta = df_daily['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1]

    df_wk = df_daily.resample('W-FRI').agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna()
    if len(df_wk) < 2: ref_idx = -1; use_wk = False
    else: ref_idx = -2; use_wk = True

    close_s = df_wk['Close'] if use_wk else df_daily['Close']
    w_ma20 = close_s.rolling(20).mean().iloc[ref_idx]
    w_ma60 = close_s.rolling(60).mean().iloc[ref_idx]
    if pd.isna(w_ma60) or w_ma60==0: w_ma60 = w_ma20 * 0.9

    std20 = close_s.rolling(20).std().iloc[ref_idx]
    w_low_bb = w_ma20 - (std20 * 2.0)
    
    hl = (df_wk['High'] - df_wk['Low']) if use_wk else (df_daily['High'] - df_daily['Low']) * 5
    w_atr = hl.rolling(14).mean().iloc[ref_idx]

    # [V20 FIX] RSI 滯後閥值：跌破 20 警報，漲回 25 才解除
    # 這裡我們無法知道上一狀態，所以採用保守策略：
    # 如果 RSI < 22 就視為危險區（折衷方案），避免 20 邊緣跳動
    emerg = None
    if daily_chg < -5 and not is_crypto: emerg = f"📉閃崩{daily_chg:.1f}%"
    elif daily_chg < -8 and is_crypto: emerg = f"📉閃崩{daily_chg:.1f}%"
    elif daily_chg > 8: emerg = f"🚀噴出{daily_chg:.1f}%"
    elif rsi < 22: emerg = "🩸RSI超賣" # 放寬到 22，減少頻繁切換
    
    is_bear = current < w_ma60
    return current, w_ma20, w_ma60, w_low_bb, w_atr, rsi, is_bear, emerg

def analyze_target(name, ticker, max_rate, crypto_fng):
    try:
        df = yf.Ticker(ticker).history(period="2y")
        if df.empty: return None
        is_crypto = "USD" in ticker
        data = calculate_metrics(df, is_crypto)
        if not data: return None
        
        curr, ma20, ma60, low_bb, atr, rsi, bear, emerg = data
        
        if is_crypto and crypto_fng: score = crypto_fng
        else: score = int(rsi)
        sent_lv, sent_short_desc = get_sentiment_analysis(score)

        if bear:
            raw = [
                {"p": curr-(atr*0.5), "l": "合理"},
                {"p": min(low_bb, ma60-atr), "l": "便宜"},
                {"p": low_bb*(0.9 if is_crypto else 0.95), "l": "超跌"}
            ]
        else:
            raw = [
                {"p": ma20, "l": "合理"},
                {"p": ma60, "l": "便宜"},
                {"p": low_bb, "l": "超跌"}
            ]

        valid = []
        for s in raw:
            p = s["p"]
            if pd.isna(p) or p<=0: continue
            if not is_crypto: p = adjust_tw_price(p)
            if p >= curr:
                p = curr * 0.99
                if not is_crypto: p = adjust_tw_price(p)
            
            note = calculate_drop_info(curr, p, is_crypto)
            valid.append({"price": p, "label": s["l"], "note": note})

        valid.sort(key=lambda x: x["price"], reverse=True)
        final = []
        seen = set()
        for v in valid:
            if v["price"] not in seen:
                final.append(v)
                seen.add(v["price"])
        
        if not final: return None
        
        if bear or rsi>70: best_idx = len(final)-1
        else: best_idx = min(1, len(final)-1)
        best = final[best_idx]

        return {
            "name": name, "ticker": ticker, "is_crypto": is_crypto,
            "current": curr, "rsi": rsi, "score": score, 
            "sent_lv": sent_lv, "sent_short_desc": sent_short_desc,
            "emerg": emerg, "best": best, "strategies": final, "is_bear": bear
        }
    except: return None

def generate_telegram_report(data, max_rate):
    colors = {"合理":"🟢", "便宜":"🟡", "超跌":"🔴"}
    
    if data['is_crypto']:
        p_txt = f"{data['current']:.2f} U"
        if max_rate: p_txt += f" (≈{data['current']*max_rate:.0f} NT)"
        r_str = f"{data['best']['price']:.2f} U"
        if ("SOL" in data['ticker'] or "RENDER" in data['ticker']) and max_rate:
             r_str += f" (≈{data['best']['price']*max_rate:.0f} NT)"
    else:
        p_txt = f"{data['current']:.0f}"
        r_str = f"{data['best']['price']:.0f}"

    msg = f"<b>{data['name']}</b>\n"
    msg += f"現價：<code>{p_txt}</code>\n"
    msg += f"情緒：{data['sent_lv']} ({data['score']})\n"
    
    if data['emerg']:
        msg += f"🚨 <b>{data['emerg']}</b>\n⚠️ 建議暫停掛單，觀察市場反應！\n"
    else:
        mindset = get_psychological_note(data['best']['label'], data['is_bear'])
        msg += f"🏆 首選：{colors[data['best']['label']]} <b><code>{r_str}</code></b> {data['best']['note']}\n"
        msg += f"🧠 心法：<i>{mindset}</i>\n"
    
    for s in data['strategies']:
        lbl = s['label']
        if data['is_crypto']: 
            p = f"{s['price']:.2f} U"
            if ("SOL" in data['ticker'] or "RENDER" in data['ticker']) and max_rate:
                p += f" (≈{s['price']*max_rate:.0f})"
        else: 
            p = f"{s['price']:.0f}"
        msg += f"• {colors[lbl]} {lbl}：<code>{p}</code> {s['note']}\n"
    
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

    old_map = {}
    for item in old_json.get('data', []):
        old_map[item['name']] = {
            'label': item['signal_label'],
            'price': item['signal_price']
        }

    for item in new_results:
        name_key = item['name'].replace("🇹🇼 ", "").replace("🪙 ", "")
        if name_key not in old_map: return True
        old_item = old_map[name_key]
        if item['is_crypto']: new_price_str = f"{item['best']['price']:.2f}"
        else: new_price_str = f"{item['best']['price']:.0f}"
        
        if item['emerg']: return True
        if item['best']['label'] != old_item['label']: return True
        if new_price_str != old_item['price']: return True
        
    return False

def save_widget_data(results, valid_until, max_rate, global_emerg):
    widget_data = []
    for item in results:
        if not item: continue
        
        lbl = item['best']['label']
        color = "green" if lbl=="合理" else "yellow" if lbl=="便宜" else "red"
        
        if item['is_crypto']:
            p_str = f"{item['current']:.2f}"
            sig_p = f"{item['best']['price']:.2f}"
        else:
            p_str = f"{item['current']:.0f}"
            sig_p = f"{item['best']['price']:.0f}"
            
        note = item['best']['note']

        if item['emerg']:
            lbl = "警示"
            color = "alert"
            sig_p = "暫停"
            clean_emerg = item['emerg'].replace("<b>", "").replace("</b>", "").replace("🚨", "").replace("🩸", "").strip()
            note = clean_emerg

        icon = item['sent_lv'].split(" ")[0]
        
        widget_data.append({
            "name": item['name'].replace("🇹🇼 ", "").replace("🪙 ", ""),
            "price": p_str,
            "score": item['score'],
            "sent_icon": icon,
            "sent_text": item['sent_short_desc'],
            "signal_label": lbl,
            "signal_price": sig_p,
            "signal_note": note,
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
    print(f"V20.0 Fixed: {now}")
    max_rate = get_max_usdt_rate()
    c_val = get_crypto_fng()
    
    today = datetime.now(TW_TZ)
    days = 4 - today.weekday()
    if days < 0: days += 7
    next_fri = (today + timedelta(days=days)).strftime('%m/%d')
    
    results = []
    global_emerg = False
    
    for n, t in TARGETS.items():
        d = analyze_target(n, t, max_rate, c_val)
        if d:
            results.append(d)
            if d['emerg']: global_emerg = True
            
    old_json = load_previous_data()
    is_noon = (now.hour == 12)
    status_changed = check_if_changed(old_json, results, global_emerg)
    
    save_widget_data(results, next_fri, max_rate, global_emerg)
    
    if status_changed or global_emerg or is_noon:
        if global_emerg: header = "🚨 <b>緊急警報</b> 🚨\n"
        elif is_noon: header = f"☀️ <b>午間定時報告 ({today.strftime('%m/%d')})</b>\n有效至：{next_fri}\n\n"
        else: header = f"📊 <b>資產狀態變更 ({today.strftime('%H:%M')})</b>\n有效至：{next_fri}\n\n"
            
        msgs = [generate_telegram_report(d, max_rate) for d in results]
        send_telegram(header + "".join(msgs))

if __name__ == "__main__":
    main()
