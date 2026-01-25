import os
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone

# --- 1. 設定區 ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# 監控清單
TARGETS = {
    "🇹🇼 台積電": "2330.TW",
    "🇹🇼 保德信市值": "009803.TW",
    "🪙 Solana": "SOL-USD",
    "🪙 Render": "RENDER-USD"
}

TW_TZ = timezone(timedelta(hours=8))

# --- 2. 通訊函式 ---
def send_telegram(msg):
    if not TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"傳送失敗: {e}")

# --- 3. 數據獲取 (MAX API & 貪婪指數) ---

def get_crypto_fng():
    """抓取幣圈貪婪指數 (0-100)"""
    try:
        url = "https://api.alternative.me/fng/"
        r = requests.get(url, timeout=5)
        data = r.json()
        return int(data['data'][0]['value'])
    except:
        return None

def get_max_usdt_rate():
    """
    抓取 MAX 交易所 USDT/TWD 即時匯率
    API: https://max-api.maicoin.com/api/v2/tickers/usdttwd
    """
    try:
        url = "https://max-api.maicoin.com/api/v2/tickers/usdttwd"
        # 模擬瀏覽器 User-Agent 避免被擋
        headers = {'User-Agent': 'Mozilla/5.0'} 
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        # 取 'last' (最新成交價) 或 'sell' (賣一價，即你買入的價格)
        # 為了保守起見，我們取 'sell' (通常比 last 高一點點，代表你當下能買到的價格)
        price = float(data['sell']) 
        return price
    except Exception as e:
        print(f"MAX API 失敗: {e}")
        # 如果 MAX 掛了，回退使用 yfinance 抓 USDT-TWD
        try:
            print("切換至備用匯率源 (Yahoo)...")
            df = yf.Ticker("USDTWD=X").history(period="1d")
            return float(df['Close'].iloc[-1])
        except:
            return None # 真的抓不到就回傳 None，後面會處理

# --- 4. 核心邏輯：情緒量表 & 價格分析 ---

def get_sentiment_label(score):
    """
    統一的 7 級情緒量表 (適用 RSI 與 貪婪指數)
    0-100 分制
    """
    if score >= 80: return "🤑 <b>極度貪婪</b> (危險)", "🔴"
    elif score >= 65: return "😈 <b>貪婪</b> (過熱)", "🟠"
    elif score >= 55: return "🙂 <b>稍微貪婪</b> (偏多)", "🟡"
    elif score >= 45: return "😐 <b>中立</b> (盤整)", "⚪"
    elif score >= 35: return "😰 <b>稍微恐懼</b> (偏空)", "🔵"
    elif score >= 20: return "😨 <b>恐懼</b> (弱勢)", "🟢"
    else: return "🥶 <b>極度恐懼</b> (絕佳買點)", "🟢🟢"

def calculate_technical(df):
    """計算技術指標"""
    current = df['Close'].iloc[-1]
    
    # RSI 計算
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1]
    
    # ATR 計算 (14日真實波動)
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=14).mean().iloc[-1]

    # 均線
    ma20 = df['Close'].rolling(window=20).mean().iloc[-1] # 月線
    ma60 = df['Close'].rolling(window=60).mean().iloc[-1] # 季線
    
    # 布林通道下緣 (保守低點)
    std20 = df['Close'].rolling(window=20).std().iloc[-1]
    b_lower = ma20 - (std20 * 2)

    return current, rsi, atr, ma20, ma60, b_lower

def analyze_target(name, ticker, max_rate, crypto_fng_val=None):
    try:
        df = yf.Ticker(ticker).history(period="6mo")
        if df.empty: return f"⚠️ <b>{name}</b>: 無法取得資料\n"

        current, rsi, atr, ma20, ma60, b_lower = calculate_technical(df)
        
        # --- A. 情緒判斷 (7級) ---
        # 如果是虛擬貨幣且有全市場指數，優先參考全市場指數，RSI 為輔
        # 如果是台股，直接用 RSI 當作情緒分數
        if "USD" in ticker and crypto_fng_val is not None:
            score = crypto_fng_val
            # 也可以做混合加權，但通常幣圈看大盤臉色，直接用 FNG 比較準
        else:
            score = rsi
            
        sentiment_text, sentiment_color = get_sentiment_label(score)
        
        # --- B. 趨勢判斷 ---
        trend = "🐂 多頭" if current > ma60 else "🐻 空頭"

        # --- C. ATR 動態掛單計算 ---
        # 1. 積極：多頭掛月線，空頭掛現價吃一個波動
        if current > ma20:
            p1 = ma20
            p1_desc = "月線支撐"
        else:
            p1 = current - (atr * 0.5)
            p1_desc = f"短線接刀 (0.5倍波動)"

        # 2. 穩健：取 (現價-1倍波動) 與 季線 的低者
        atr_support = current - atr
        if atr_support < ma60:
            p2 = atr_support
            p2_desc = f"波段修正 (1倍波動)"
        else:
            p2 = ma60
            p2_desc = "季線支撐"

        # 3. 保守：布林下緣 (統計學低點)
        p3 = b_lower
        p3_desc = "布林通道下緣 (超跌區)"

        # --- D. 輸出報表 ---
        report = f"<b>{name}</b>\n"
        
        # 價格顯示 (虛擬貨幣加上 MAX 匯率換算)
        if "USD" in ticker:
            if max_rate:
                twd_price = current * max_rate
                report += f"現價：<code>{current:.2f}</code> U (約 {twd_price:.0f} TWD)\n"
            else:
                report += f"現價：<code>{current:.2f}</code> U (⚠️ 匯率獲取失敗)\n"
        else:
            report += f"現價：<code>{current:.0f}</code>\n"

        report += f"趨勢：{trend} | 情緒：{sentiment_color} {sentiment_text}\n"
        report += f"波動：ATR <code>{atr:.2f}</code>\n"
        
        # 計算掛單有效期限 (T+14)
        valid_date = (datetime.now() + timedelta(days=14)).strftime('%m/%d')
        report += f"🛒 <b>掛單參考 (建議監控至 {valid_date})：</b>\n"

        # 顯示掛單 (含 MAX 台幣換算)
        if "USD" in ticker and max_rate:
            report += f"1. 🟢 積極：<code>{p1:.2f}</code> U ({p1*max_rate:.0f} NT) [{p1_desc}]\n"
            report += f"2. 🟡 穩健：<code>{p2:.2f}</code> U ({p2*max_rate:.0f} NT) [{p2_desc}]\n"
            report += f"3. 🔴 保守：<code>{p3:.2f}</code> U ({p3*max_rate:.0f} NT) [{p3_desc}]\n"
        else:
            report += f"1. 🟢 積極：<code>{p1:.1f}</code> [{p1_desc}]\n"
            report += f"2. 🟡 穩健：<code>{p2:.1f}</code> [{p2_desc}]\n"
            report += f"3. 🔴 保守：<code>{p3:.1f}</code> [{p3_desc}]\n"
            
        report += "--------------------\n"
        return report

    except Exception as e:
        return f"⚠️ <b>{name}</b>: 分析錯誤 {str(e)}\n"

# --- 5. 主程式 ---
def main():
    now = datetime.now(TW_TZ)
    print(f"執行時間: {now}")

    # 1. 取得全域資訊
    c_val = get_crypto_fng()
    max_rate = get_max_usdt_rate()

    # 2. 組合訊息
    final_msg = f"<b>📊 全資產掛單監控 (V5 MAX版)</b>\n"
    final_msg += f"📅 {now.strftime('%Y-%m-%d')}\n"
    
    if max_rate:
        final_msg += f"🇹🇼 MAX USDT 匯率：<b>{max_rate:.2f}</b> TWD\n"
    else:
        final_msg += f"⚠️ MAX 匯率抓取失敗，暫停台幣換算\n"
        
    if c_val is not None:
        label, icon = get_sentiment_label(c_val)
        final_msg += f"🌍 幣圈指數：{icon} {label} ({c_val})\n\n"
    
    for name, ticker in TARGETS.items():
        print(f"分析: {name}...")
        final_msg += analyze_target(name, ticker, max_rate, c_val)
    
    final_msg += "\n💡 <i>掛單說明：\n建議在 APP 設定雲端單時，將「截止日期」填寫為括號內的建議日期 (14天後)，讓程式幫你長時間盯盤。</i>"
    
    send_telegram(final_msg)

if __name__ == "__main__":
    main()
