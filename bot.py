import os
import requests
import yfinance as yf
import pandas as pd
import mplfinance as mpf
from datetime import datetime, timedelta, timezone

# --- 1. 基礎設定區 ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
STOCK_ID = "2330.TW"        # 股票代碼
CSV_FILE = "trading_journal.csv" # 記憶檔案名稱

# 設定台灣時區 (UTC+8)
TW_TZ = timezone(timedelta(hours=8))

# --- 2. Telegram 通訊函式 ---
def send_msg(msg):
    if not TOKEN or not CHAT_ID:
        print("缺少 Token 或 Chat ID，跳過傳送訊息")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"訊息傳送失敗: {e}")

def send_photo(photo_path):
    if not TOKEN or not CHAT_ID:
        print("缺少 Token 或 Chat ID，跳過傳送圖片")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    try:
        with open(photo_path, 'rb') as f:
            payload = {"chat_id": CHAT_ID}
            files = {"photo": f}
            requests.post(url, data=payload, files=files)
    except Exception as e:
        print(f"圖片傳送失敗: {e}")

# --- 3. CSV 讀寫功能 (記憶體) ---
def load_record():
    # 如果檔案存在就讀取，不存在就建立一個空的
    if os.path.exists(CSV_FILE):
        return pd.read_csv(CSV_FILE)
    return pd.DataFrame(columns=["Date", "Predicted_Dir", "Open_Price", "Close_Price", "Result"])

def save_record(df):
    df.to_csv(CSV_FILE, index=False)

# --- 4. 核心邏輯：指標計算與教學 ---
def analyze_indicators(df):
    close = df['Close'].iloc[-1]
    
    # A. 計算 RSI (相對強弱指標)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1]

    # B. 計算 MACD
    exp12 = df['Close'].ewm(span=12, adjust=False).mean()
    exp26 = df['Close'].ewm(span=26, adjust=False).mean()
    macd = exp12 - exp26
    signal = macd.ewm(span=9, adjust=False).mean()
    macd_val = macd.iloc[-1]
    signal_val = signal.iloc[-1]

    # C. 計算均線
    ma20 = df['Close'].rolling(window=20).mean().iloc[-1]
    ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
    
    # --- 生成教學文字 ---
    explanation = f"\n<b>📊 指標教學與判斷：</b>\n"
    
    # RSI 判斷
    explanation += f"1. <b>RSI ({rsi:.1f})</b>: "
    if rsi > 70: explanation += "🔴 過熱 (>70)，短線有回檔風險。\n"
    elif rsi < 30: explanation += "🟢 超賣 (<30)，短線醞釀反彈。\n"
    else: explanation += "⚪ 中性區間，順勢操作。\n"

    # MACD 判斷
    explanation += f"2. <b>MACD</b>: "
    if macd_val > signal_val: explanation += "🟢 黃金交叉 (柱狀體翻紅)，趨勢偏多。\n"
    else: explanation += "🔴 死亡交叉 (柱狀體翻綠)，趨勢偏空。\n"

    # MA 判斷
    explanation += f"3. <b>月線 (MA20)</b>: "
    if close > ma20: explanation += "🟢 站上月線，支撐轉強。\n"
    else: explanation += "🔴 跌破月線，上方有壓。\n"

    # 綜合評分預測 (簡單投票制)
    score = 0
    if rsi < 30: score += 1      # 超賣加分
    if macd_val > signal_val: score += 1 # 金叉加分
    if close > ma20: score += 1  # 站上月線加分
    
    # 如果 RSI 太高反而要扣分(看跌)
    if rsi > 75: score -= 1

    prediction = "漲" if score >= 2 else "跌"
    
    return explanation, prediction, close

# --- 5. 繪圖功能 ---
def plot_stock(df, filename="chart.png"):
    # 只取最近 60 天 (約3個月) 來畫圖
    plot_df = df.iloc[-60:]
    
    # 設定外觀 (台股慣例：紅漲綠跌)
    mc = mpf.make_marketcolors(up='r', down='g', inherit=True)
    s  = mpf.make_mpf_style(marketcolors=mc)
    
    # 準備均線資料
    plot_df['MA20'] = plot_df['Close'].rolling(window=20).mean()
    plot_df['MA60'] = plot_df['Close'].rolling(window=60).mean()

    apds = [
        mpf.make_addplot(plot_df['MA20'], color='orange', width=1.5), # 橘色月線
        mpf.make_addplot(plot_df['MA60'], color='blue', width=1.5)    # 藍色季線
    ]
    
    mpf.plot(
        plot_df, 
        type='candle', 
        style=s, 
        addplot=apds, 
        volume=True, 
        title=f"{STOCK_ID} Analysis",
        savefig=filename
    )

# --- 6. 主程式進入點 ---
def main():
    now = datetime.now(TW_TZ)
    # --- 測試模式 (測試完記得改回下一行) ---
    hour = 8   # <--- 強制假裝現在是早上 8 點 (會觸發 D1 預測)
    # hour = 14 # <--- 想測收盤就改成 14 (會觸發 D5 結算)
    # hour = now.hour # <--- 這是原本的，測試正常後再改回來
    today_str = now.strftime('%Y-%m-%d')
    
    print(f"目前時間 (台灣): {now} (Hour: {hour})")
    print(f"執行目標股票: {STOCK_ID}")

    # 抓取近半年資料 (確保有足夠資料算 MA60)
    df = yf.Ticker(STOCK_ID).history(period="6mo")
    if df.empty:
        print("錯誤：抓不到股價資料")
        return

    # 讀取交易日記
    record_df = load_record()

    # ====== 任務 A: 盤前預測 (台灣早上 08:00 - 09:00) ======
    if 8 <= hour < 9:
        print("執行：D1 盤前預測任務")
        explain_text, pred_dir, last_close = analyze_indicators(df)
        
        msg = f"<b>🌅 {today_str} 盤前 AI 預測</b>\n"
        msg += f"昨日收盤：{last_close:.1f}\n"
        msg += explain_text
        msg += f"\n🤖 <b>綜合判斷：今日看{pred_dir}</b>\n"
        msg += "(收盤後將自動驗證此預測)"

        # 寫入 CSV (如果今天已有紀錄則更新，沒有則新增)
        new_row = {"Date": today_str, "Predicted_Dir": pred_dir, "Open_Price": 0, "Close_Price": 0, "Result": "Pending"}
        
        # 檢查是否已存在
        if today_str in record_df['Date'].astype(str).values:
            record_df.loc[record_df['Date'] == today_str, "Predicted_Dir"] = pred_dir
        else:
            record_df = pd.concat([record_df, pd.DataFrame([new_row])], ignore_index=True)
        
        save_record(record_df)
        send_msg(msg)

    # ====== 任務 B: 盤後檢討 (台灣下午 13:00 - 18:00) ======
    elif 13 <= hour < 18:
        print("執行：D5 收盤結算任務")
        
        current_close = df['Close'].iloc[-1]
        open_price = df['Open'].iloc[-1]
        
        # 計算實際漲跌
        prev_close = df['Close'].iloc[-2]
        change_val = current_close - prev_close
        real_dir = "漲" if change_val > 0 else "跌"
        
        # 準備畫圖
        chart_file = "chart.png"
        plot_stock(df, chart_file)
        
        # 準備訊息
        msg = f"<b>🌛 {today_str} 收盤結算</b>\n"
        msg += f"開盤：{open_price:.1f} | 收盤：{current_close:.1f}\n"
        msg += f"漲跌：{change_val:.1f} ({real_dir})\n"
        msg += "--------------------\n"

        # 對答案：讀取早上的預測
        res_str = "無紀錄"
        if today_str in record_df['Date'].astype(str).values:
            pred = record_df.loc[record_df['Date'] == today_str, "Predicted_Dir"].values[0]
            msg += f"🎯 早上預測：看<b>{pred}</b>\n"
            
            if pred == real_dir:
                res_str = "Win"
                msg += "🏆 <b>恭喜！預測正確！</b>\n"
            elif pred == "Pending":
                res_str = "Missed"
                msg += "⚠️ 早上未成功執行預測。\n"
            else:
                res_str = "Loss"
                msg += "💩 <b>預測失敗</b>，市場走勢與指標背離。\n"
            
            # 更新資料庫結果
            record_df.loc[record_df['Date'] == today_str, "Open_Price"] = open_price
            record_df.loc[record_df['Date'] == today_str, "Close_Price"] = current_close
            record_df.loc[record_df['Date'] == today_str, "Result"] = res_str
            save_record(record_df)
        else:
            msg += "⚠️ 今日無盤前預測紀錄，無法驗證。\n"

        # 發送
        send_photo(chart_file)
        send_msg(msg)
        
        # 清除暫存圖片
        if os.path.exists(chart_file):
            os.remove(chart_file)

    else:
        print(f"現在是非任務時間 ({hour}點)，待機中...")

if __name__ == "__main__":
    main()
