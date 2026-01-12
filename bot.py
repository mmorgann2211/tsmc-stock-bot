import os
import requests
import yfinance as yf
import mplfinance as mpf
import pandas as pd
from datetime import datetime

# --- 從 GitHub Secrets 讀取設定 ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
STOCK_ID = "2330.TW"  # 台積電

# --- Telegram 傳送函式 ---
def send_telegram_msg(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML" # 支援簡單的粗體格式
    }
    requests.post(url, data=payload)

def send_telegram_photo(photo_path):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    with open(photo_path, 'rb') as f:
        payload = {"chat_id": CHAT_ID}
        files = {"photo": f}
        requests.post(url, data=payload, files=files)

# --- 主分析邏輯 ---
def analyze_and_run():
    print(f"啟動分析：{STOCK_ID}...")
    
    # 1. 抓取資料 (半年)
    df = yf.Ticker(STOCK_ID).history(period="6mo")
    
    if df.empty:
        print("錯誤：抓不到股價資料")
        return

    # 2. 計算均線
    df['MA20'] = df['Close'].rolling(window=20).mean()
    df['MA60'] = df['Close'].rolling(window=60).mean()

    # 3. 策略：近3個月低點偵測
    recent_df = df.iloc[-60:] # 取近60天
    current_close = recent_df['Close'].iloc[-1]
    lowest_in_3m = recent_df['Low'].min()
    highest_in_3m = recent_df['High'].max()
    
    # 計算位階 (0~100%)
    position = (current_close - lowest_in_3m) / (highest_in_3m - lowest_in_3m) * 100

    # 4. 繪圖
    mc = mpf.make_marketcolors(up='r', down='g', inherit=True)
    s  = mpf.make_mpf_style(marketcolors=mc)
    
    apds = [
        mpf.make_addplot(recent_df['MA20'], color='orange', width=1.5),
        mpf.make_addplot(recent_df['MA60'], color='blue', width=1.5)
    ]
    
    chart_file = "chart.png"
    mpf.plot(
        recent_df, 
        type='candle', 
        style=s, 
        addplot=apds, 
        volume=True, 
        title=f"{STOCK_ID} Analysis",
        savefig=chart_file
    )

    # 5. 產生分析文字
    today_date = datetime.now().strftime('%Y-%m-%d')
    
    # 使用 HTML 格式讓 Telegram 顯示粗體
    msg = f"<b>【{today_date} 台積電日報】</b>\n"
    msg += f"收盤價：{current_close:.1f}\n"
    msg += f"近三月最低：{lowest_in_3m:.1f}\n"
    msg += f"目前位階：{position:.1f}% (0為最低)\n"
    msg += "----------------\n"

    # 策略判斷
    if current_close <= lowest_in_3m * 1.05:
        msg += "🟢 <b>【機會】</b>股價逼近三個月新低，留意支撐！\n"
    elif position < 20:
        msg += "🔵 <b>【觀察】</b>位於相對低檔區。\n"
    elif position > 80:
        msg += "🔴 <b>【過熱】</b>位於相對高檔區，小心回檔。\n"
    else:
        msg += "⚪ <b>【盤整】</b>價格位於中間區間。\n"

    # 6. 發送
    send_telegram_photo(chart_file) # 先傳圖
    send_telegram_msg(msg)          # 再傳文字
    print("推播完成！")

if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        print("錯誤：找不到 Token 或 Chat ID")
    else:
        analyze_and_run()
