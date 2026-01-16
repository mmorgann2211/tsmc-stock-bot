import os
import requests
import yfinance as yf
import pandas as pd
import mplfinance as mpf
from datetime import datetime, timedelta, timezone

# --- 1. 設定區 ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
STOCK_ID = "2330.TW"
# 設定台灣時區
TW_TZ = timezone(timedelta(hours=8))

# --- 2. 通訊函式 ---
def send_telegram(msg, photo_path=None):
    if not TOKEN or not CHAT_ID: return
    
    # 傳文字
    url_msg = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload_msg = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    requests.post(url_msg, data=payload_msg)
    
    # 傳圖片
    if photo_path and os.path.exists(photo_path):
        url_img = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        with open(photo_path, 'rb') as f:
            payload_img = {"chat_id": CHAT_ID}
            files = {"photo": f}
            requests.post(url_img, data=payload_img, files=files)

# --- 3. 核心邏輯：價值投資評估 ---
def analyze_value_investing(df):
    current_close = df['Close'].iloc[-1]
    
    # A. 計算過去 1 年 (約 250 天) 的高低點
    # 我們用 1 年的數據來判斷現在是不是「相對便宜」
    period_high = df['High'].max()
    period_low = df['Low'].min()
    
    # 計算位階 (0% 代表一年最低價，100% 代表一年最高價)
    position_rank = (current_close - period_low) / (period_high - period_low) * 100
    
    # B. 計算 RSI (協助判斷是否超賣)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1]
    
    # C. 均線 (月線 MA20, 季線 MA60, 半年線 MA120)
    ma20 = df['Close'].rolling(window=20).mean().iloc[-1]
    ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
    
    return current_close, period_low, period_high, position_rank, rsi, ma20, ma60

# --- 4. 繪圖功能 ---
def plot_chart(df, filename="value_chart.png"):
    # 畫圖只畫最近 4 個月，不然手機看不清楚
    plot_df = df.iloc[-80:]
    
    mc = mpf.make_marketcolors(up='r', down='g', inherit=True)
    s  = mpf.make_mpf_style(marketcolors=mc)
    
    # 加入均線
    plot_df['MA20'] = plot_df['Close'].rolling(window=20).mean()
    plot_df['MA60'] = plot_df['Close'].rolling(window=60).mean()
    
    apds = [
        mpf.make_addplot(plot_df['MA20'], color='orange', width=1.5),
        mpf.make_addplot(plot_df['MA60'], color='blue', width=1.5)
    ]
    
    mpf.plot(plot_df, type='candle', style=s, addplot=apds, volume=True, 
             title=f"{STOCK_ID} Value Analysis", savefig=filename)

# --- 5. 主程式 ---
def main():
    now = datetime.now(TW_TZ)
    hour = now.hour
    today_str = now.strftime('%Y-%m-%d')
    
    print(f"目前時間: {now} (Hour: {hour})")

    # 為了容錯，我們設定下午 13:00 ~ 23:00 之間任何時間執行都算數
    # 這樣就不用擔心 GitHub Action 延遲的問題
    if 13 <= hour < 23:
        print("執行：價值投資掃描任務")
        
        # 抓取 1 年資料 (價值投資看長一點)
        df = yf.Ticker(STOCK_ID).history(period="1y")
        if df.empty: return

        # 分析
        price, low_1y, high_1y, rank, rsi, ma20, ma60 = analyze_value_investing(df)
        
        # 繪圖
        chart_file = "value_chart.png"
        plot_chart(df, chart_file)
        
        # 準備訊息
        msg = f"<b>💎 {today_str} 台積電價值快篩</b>\n"
        msg += f"目前股價：<b>{price:.0f}</b>\n"
        msg += f"一年內最低：{low_1y:.0f}\n"
        msg += f"一年內最高：{high_1y:.0f}\n"
        msg += f"📊 <b>目前位階：{rank:.1f}%</b> (0%為最便宜)\n"
        msg += f"📉 <b>RSI 強弱：{rsi:.1f}</b>\n"
        msg += "------------------------\n"
        
        # === 核心策略：紅綠燈判斷 ===
        # 條件 1: 位階低於 25% (超便宜)
        # 條件 2: RSI 低於 35 (市場恐慌)
        # 條件 3: 跌破季線 (中線回檔)
        
        suggestion = ""
        action_emoji = ""
        
        if rank < 20 or rsi < 30:
            action_emoji = "🟢"
            msg += f"{action_emoji} <b>【進場訊號：強烈買進】</b>\n"
            msg += "理由：股價進入一年來的底部區 (或RSI超賣)。\n"
            msg += "💡 <b>操作建議：</b> 將手邊累積的資金，分批買入零股！\n"
            
        elif rank < 40:
            action_emoji = "🟡"
            msg += f"{action_emoji} <b>【訊號：相對低檔】</b>\n"
            msg += "理由：股價低於中間值，雖非最低但可接受。\n"
            msg += "💡 <b>操作建議：</b> 可投入本月預算，或繼續觀望更低點。\n"
            
        else:
            action_emoji = "🔴"
            msg += f"{action_emoji} <b>【訊號：存錢觀望】</b>\n"
            msg += "理由：股價處於中高位階，不便宜。\n"
            msg += "💡 <b>操作建議：</b> 忍住！把本月的 2,500 元存下來，等待大跌再出手。\n"
            
        send_telegram(msg, chart_file)
        
        # 清除圖片
        if os.path.exists(chart_file):
            os.remove(chart_file)
            
    else:
        print(f"非執行時間 ({hour}點)，休息中...")

if __name__ == "__main__":
    main()
