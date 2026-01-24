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

# --- 3. 獲取虛擬貨幣全市場貪婪指數 API ---
def get_crypto_fng():
    try:
        url = "https://api.alternative.me/fng/"
        r = requests.get(url)
        data = r.json()
        value = int(data['data'][0]['value'])
        status = data['data'][0]['value_classification']
        return value, status
    except:
        return None, None

# --- 4. 計算 RSI (個股情緒指標) ---
def calculate_rsi(df, window=14):
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

# --- 5. 核心分析邏輯 ---
def analyze_target(name, ticker, crypto_fng_val=None):
    try:
        # 抓取資料 (半年份，確保季線準確)
        df = yf.Ticker(ticker).history(period="6mo")
        
        if df.empty:
            return f"⚠️ <b>{name}</b>: 無法取得資料\n"

        current_price = df['Close'].iloc[-1]
        
        # --- A. 牛熊指標 (看趨勢) ---
        # 使用季線 (60MA) 當作生命線
        ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
        
        if current_price > ma60:
            trend_icon = "🐂 牛市"
            trend_desc = "多頭格局 (價在季線上)"
        else:
            trend_icon = "🐻 熊市"
            trend_desc = "空頭格局 (價在季線下)"

        # --- B. 貪婪恐懼指數 (看情緒) ---
        # 如果是虛擬貨幣，我們參考全市場的指數 + 個股 RSI
        # 如果是台股，我們用 RSI 模擬貪婪指數
        rsi = calculate_rsi(df)
        
        sentiment = ""
        sentiment_color = ""
        
        # 判斷 RSI 情緒 (適用所有標的)
        if rsi > 70:
            sentiment = "🤑 極度貪婪 (過熱)"
            sentiment_color = "🔴" # 危險
        elif rsi > 60:
            sentiment = "😈 貪婪 (強勢)"
            sentiment_color = "🟠"
        elif rsi < 30:
            sentiment = "😨 極度恐懼 (超賣)"
            sentiment_color = "🟢" # 機會
        elif rsi < 40:
            sentiment = "😰 恐懼 (弱勢)"
            sentiment_color = "🔵"
        else:
            sentiment = "😐 中立"
            sentiment_color = "⚪"

        # --- C. 計算掛單價 ---
        ma20 = df['Close'].rolling(window=20).mean().iloc[-1] # 月線
        low_3m = df.iloc[-60:]['Low'].min()                   # 3個月最低
        
        # 組合訊息
        report = f"<b>{name}</b>\n"
        report += f"現價：<code>{current_price:.2f}</code>\n"
        report += f"趨勢：{trend_icon} ({trend_desc})\n"
        report += f"情緒：{sentiment_color} <b>{sentiment}</b> (RSI: {rsi:.1f})\n"
        
        # 如果是幣圈，額外顯示全市場指數
        if "USD" in ticker and crypto_fng_val:
            report += f"幣圈大盤：指數 <b>{crypto_fng_val}</b>\n"

        report += "🛒 <b>參考掛單：</b>\n"
        report += f"1. 🟢 想早點買：<code>{ma20:.2f}</code>\n"
        report += f"2. 🟡 安心買：<code>{ma60:.2f}</code>\n"
        report += f"3. 🔴 想撿便宜：<code>{low_3m:.2f}</code>\n"
        report += "--------------------\n"
        
        return report

    except Exception as e:
        return f"⚠️ <b>{name}</b>: 分析錯誤 ({str(e)})\n"

# --- 6. 主程式 ---
def main():
    now = datetime.now(TW_TZ)
    today_str = now.strftime('%Y-%m-%d')
    print(f"執行時間: {now}")

    # 先抓取幣圈全市場貪婪指數 (只抓一次)
    c_val, c_status = get_crypto_fng()
    crypto_intro = ""
    if c_val:
        # 簡單解釋
        fng_text = f"{c_val} ({c_status})"
        crypto_intro = f"🌍 <b>今日幣圈總體貪婪指數：{fng_text}</b>\n\n"

    final_msg = f"<b>📊 資產情緒監控日報 ({today_str})</b>\n\n"
    final_msg += crypto_intro
    
    for name, ticker in TARGETS.items():
        print(f"正在分析: {name}...")
        # 傳入幣圈指數供參考
        final_msg += analyze_target(name, ticker, c_val)
    
    final_msg += "\n💡 <i>教學：\n🐂 牛市+😨 恐懼 = 絕佳回檔買點 (強勢股回檔)\n🐻 熊市+🤑 貪婪 = 逃命波 (弱勢股反彈)</i>"
    
    send_telegram(final_msg)

if __name__ == "__main__":
    main()
