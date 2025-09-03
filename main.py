# main.py - SmartShopSaver 多代理人系統 (Render版)
import os
import json
import logging
import time
import re
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime
from flask import Flask, request, abort

# LINE Bot SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# smolagents 和 OpenAI - 保持您的核心功能
from smolagents import CodeAgent, LiteLLMModel, tool
from openai import OpenAI

# ========== 設定區塊 ==========
# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 環境變數設置 - 從 Render 環境變數讀取
CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# 檢查必要的環境變數
if not CHANNEL_ACCESS_TOKEN:
    raise ValueError("LINE_CHANNEL_ACCESS_TOKEN 環境變數必須設定")
if not CHANNEL_SECRET:
    raise ValueError("LINE_CHANNEL_SECRET 環境變數必須設定")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY 環境變數必須設定")

# 初始化 LINE Bot API
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# 初始化 OpenAI 客戶端
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Flask 應用
app = Flask(__name__)

# ========== 系統提示詞 ==========
MAIN_AGENT_SYSTEM_PROMPT = """
你是SmartShopSaver主控制代理人，負責理解用戶需求並分派任務給適當的子代理人。

【你管理的子代理人】
1. **商品評論代理人 (ProductReviewAgent)**
   - 功能：提供商品評價分析、購物建議、商品推薦
   - 觸發關鍵字：評價、評論、好不好、推薦、建議、分析商品、商品資訊
   - 支援查詢各大購物平台的商品連結

2. **價格追蹤代理人 (PriceTrackerAgent)**
   - 功能：即時價格查詢、設定價格追蹤、管理追蹤清單
   - 觸發關鍵字：價格、多少錢、比價、追蹤、監控、通知、降價
   - 支援多平台價格比較和自動降價通知

【任務分派規則】
1. 仔細分析用戶訊息，判斷需求類型
2. 如果是詢問商品好壞、評價、推薦等，使用商品評論代理人
3. 如果是詢問價格、比價、追蹤價格等，使用價格追蹤代理人
4. 如果需求涉及多個功能，可以同時調用多個代理人
5. 如果無法判斷，詢問用戶更具體的需求

【回應原則】
- 保持友善、專業的語氣
- 提供清晰、有用的資訊
- 適時提供額外建議
- 使用繁體中文回應
"""

# ========== 工具函數定義 ==========
@tool
def analyze_user_intent(message: str) -> Dict:
    """
    分析用戶意圖，決定應該使用哪個子代理人
    
    Args:
        message: 用戶訊息
        
    Returns:
        包含意圖分析結果的字典
    """
    message_lower = message.lower()
    
    # 先檢查是否為非購物相關問題
    non_shopping_indicators = [
        "天氣", "新聞", "股票", "股市", "政治", "選舉", "運動", "比賽",
        "遊戲攻略", "遊戲", "電玩", "料理", "食譜", "做菜", "烹飪",
        "健康", "醫療", "看病", "醫生", "藥", "症狀", "疾病",
        "教育", "學習", "考試", "作業", "功課", "學校",
        "程式", "編程", "代碼", "coding", "python", "java",
        "數學", "物理", "化學", "生物", "科學", "歷史", "地理",
        "音樂", "歌曲", "歌詞", "電影", "影片", "電視", "追劇",
        "書籍", "小說", "詩詞", "文學", "作文", "寫作",
        "笑話", "故事", "聊天", "閒聊", "你是誰", "你好嗎",
        "早安", "晚安", "謝謝", "再見", "拜拜"
    ]
    
    for indicator in non_shopping_indicators:
        if indicator in message_lower:
            return {
                'message': message,
                'intents': [],
                'primary_intent': None,
                'is_shopping_related': False
            }
    
    # 商品評論相關 - 自然語言模式
    review_keywords = [
        '評價', '評論', '好不好', '好用', '推薦', '建議', '分析',
        '優點', '缺點', '心得', '開箱', '值得買', '品質', '耐用',
        '商品資訊', '產品介紹', '規格', '特色', '功能', '如何',
        '怎麼樣', '怎樣', '好嗎', '評測', '測評', '使用心得',
        '用戶評價', '買家評價', '真實評價', '網友評價', '值不值得',
        '適合', '好壞', '優劣', '比較', '差異', '選擇'
    ]
    
    # 價格追蹤相關 - 自然語言模式
    price_keywords = [
        '價格', '多少錢', '比價', '追蹤', '監控', '通知', '降價',
        '便宜', '特價', '折扣', '優惠', '目標價', '低於', '售價',
        '報價', '賣多少', '現在什麼價', '幾元', '幾塊', 'nt$',
        '成本', '定價', '市價', '行情', '價位', '預算',
        '貴不貴', '划算', 'cp值', '性價比'
    ]
    
    # 自然語言模式檢查
    review_patterns = [
        r'(.+)(?:的)?評[價論](?:如何|怎[麼樣])?',
        r'(.+)好不好[用買]?',
        r'(.+)值[不得]?[得的]買[嗎?]?',
        r'(.+)推薦[嗎?]?',
        r'(.+)怎[麼樣]?[樣]',
        r'想?[買購](.+)(?:好[嗎?]|可以[嗎?])?',
        r'(.+)(?:跟|和|與)(.+)(?:哪個|那個)好',
        r'請?(?:分析|介紹|說明)(?:一下)?(.+)',
        r'(.+)(?:有什麼|有哪些)(?:優點|缺點|特[色點])',
        r'(.+)適合(?:我|什麼人)?[嗎?]?'
    ]
    
    price_patterns = [
        r'(.+)(?:的)?價[格錢](?:是)?(?:多少|幾元)?',
        r'(.+)(?:要)?多少錢',
        r'(.+)賣(?:多少|幾元)',
        r'查(?:詢)?(.+)(?:的)?價[格錢]',
        r'比價(.+)',
        r'(.+)現在(?:什麼)?價[格位]',
        r'追蹤(.+)(?:的)?(?:價格|降價)',
        r'(.+)(?:降價|特價|優惠)(?:了[嗎?]|通知)',
        r'(.+)(?:在)?哪[裡裏](?:買)?(?:比較)?便宜',
        r'(.+)(?:貴不貴|划算[嗎?])'
    ]
    
    # 先檢查特殊模式
    review_score = 0
    price_score = 0
    
    for pattern in review_patterns:
        if re.search(pattern, message, re.IGNORECASE):
            review_score = 10
            break
    
    if review_score == 0:
        review_score = sum(2 if keyword in message_lower else 0 for keyword in review_keywords)
    
    for pattern in price_patterns:
        if re.search(pattern, message, re.IGNORECASE):
            price_score = 10
            break
    
    if price_score == 0:
        price_score = sum(2 if keyword in message_lower else 0 for keyword in price_keywords)
    
    # 判斷主要意圖
    intents = []
    
    if review_score > 0:
        intents.append({
            'type': 'review',
            'score': review_score,
            'agent': 'ProductReviewAgent'
        })
    
    if price_score > 0:
        intents.append({
            'type': 'price',
            'score': price_score,
            'agent': 'PriceTrackerAgent'
        })
    
    # 如果沒有明確意圖，但看起來像商品查詢
    if not intents:
        # 檢查是否包含品牌或商品關鍵字
        product_indicators = [
            'iphone', 'samsung', 'sony', 'apple', 'nike', 'adidas',
            'asus', 'msi', 'acer', 'lenovo', 'hp', 'dell', 'lg',
            'xiaomi', '小米', 'oppo', 'vivo', 'huawei', '華為',
            'ps5', 'ps4', 'xbox', 'switch', 'nintendo',
            'macbook', 'ipad', 'airpods', 'apple watch',
            '手機', '電腦', '筆電', '平板', '耳機', '滑鼠', '鍵盤',
            '螢幕', '顯卡', '主機', '相機', '手錶', '電視',
            'razer', '雷蛇', 'viper', 'logitech', '羅技', 'steelseries'
        ]
        
        for indicator in product_indicators:
            if indicator in message_lower:
                # 預設為評價查詢（更符合自然語言習慣）
                intents.append({
                    'type': 'review',
                    'score': 5,
                    'agent': 'ProductReviewAgent',
                    'inferred': True
                })
                break
    
    # 記錄分析結果
    logger.info(f"意圖分析 - 訊息: {message}")
    logger.info(f"評價分數: {review_score}, 價格分數: {price_score}")
    logger.info(f"識別意圖: {intents}")
    
    return {
        'message': message,
        'intents': intents,
        'primary_intent': max(intents, key=lambda x: x['score']) if intents else None,
        'is_shopping_related': len(intents) > 0
    }


@tool
def invoke_product_review_agent(user_id: str, message: str) -> str:
    """
    調用商品評論子代理人
    
    Args:
        user_id: 用戶ID
        message: 用戶訊息
        
    Returns:
        子代理人的回應
    """
    try:
        # 嘗試動態導入商品評論代理人
        try:
            from price_tracker import SmartPriceTracker
            # 如果有獨立的商品評論模組，在這裡導入
            # from agents.product_review_agent import ProductReviewAgent
            # agent = ProductReviewAgent()
            
            # 暫時使用價格追蹤系統提供商品資訊
            price_tracker = SmartPriceTracker()
            response = price_tracker.process_message(user_id, message)
            return response
            
        except ImportError:
            # 如果沒有獨立模組，使用 OpenAI 提供商品評論功能
            return generate_product_review_with_openai(user_id, message)
            
    except Exception as e:
        logger.error(f"商品評論代理人執行失敗: {e}", exc_info=True)
        return "❌ 商品評論分析暫時無法使用，請稍後再試"


@tool
def invoke_price_tracker_agent(user_id: str, message: str) -> str:
    """
    調用價格追蹤子代理人
    
    Args:
        user_id: 用戶ID
        message: 用戶訊息
        
    Returns:
        子代理人的回應
    """
    try:
        from price_tracker import SmartPriceTracker
        price_tracker = SmartPriceTracker(line_bot_api)
        response = price_tracker.process_message(user_id, message)
        return response
    except ImportError as e:
        logger.error(f"無法導入價格追蹤代理人: {e}")
        return "❌ 價格追蹤模組導入失敗，請檢查模組是否存在"
    except Exception as e:
        logger.error(f"價格追蹤代理人執行失敗: {e}", exc_info=True)
        return "❌ 價格查詢功能暫時無法使用，請稍後再試"


def generate_product_review_with_openai(user_id: str, message: str) -> str:
    """
    使用 OpenAI 生成商品評論回應
    
    Args:
        user_id: 用戶ID
        message: 用戶訊息
        
    Returns:
        AI 生成的商品評論回應
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """你是SmartShopSaver的商品評論專家。請根據用戶的商品查詢提供：
1. 商品的一般評價和特色
2. 優缺點分析
3. 購買建議
4. 適合的用戶族群
5. 建議的購買平台

請使用繁體中文回應，保持專業但友善的語調。如果是具體的商品型號，請提供更詳細的資訊。"""
                },
                {
                    "role": "user",
                    "content": message
                }
            ],
            max_tokens=1000,
            temperature=0.7
        )
        
        ai_response = response.choices[0].message.content
        
        # 添加 SmartShopSaver 特色訊息
        full_response = f"📊 商品評論分析\n\n{ai_response}\n\n"
        full_response += "💡 想查詢最新價格？\n"
        full_response += f"輸入「查詢 [商品名] 價格」或「追蹤 [商品名]」設定降價提醒！"
        
        return full_response
        
    except Exception as e:
        logger.error(f"OpenAI 商品評論生成失敗: {e}")
        return "❌ 商品評論功能暫時無法使用，建議您使用價格查詢功能"


@tool
def send_line_reply(reply_token: str, message: str) -> bool:
    """
    發送LINE回覆訊息
    
    Args:
        reply_token: 回覆令牌
        message: 要發送的訊息
        
    Returns:
        是否成功
    """
    try:
        # 如果訊息太長，分段發送
        if len(message) > 5000:
            message = message[:4900] + "\n\n⚠️ 內容過長已截斷"
        
        # 抑制 LINE Bot SDK 版本警告
        import warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        
        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text=message)
        )
        return True
    except Exception as e:
        logger.error(f"發送LINE訊息失敗: {e}")
        return False


@tool
def generate_help_message() -> str:
    """
    生成幫助訊息
    
    Returns:
        幫助訊息文字
    """
    return """🤖 SmartShopSaver 智能購物助手

我可以幫您處理以下事項：

📊 【商品評價分析】
• 分析商品真實評價
• 提供購買建議
• 比較優缺點
• 推薦購買平台

💰 【價格查詢追蹤】
• 即時比價查詢
• 設定降價提醒
• 多平台價格比較
• 追蹤歷史價格

💡 【使用範例】
• "iPhone 15 的評價如何？"
• "PS5 現在多少錢？"
• "追蹤 MacBook Air，目標價格 30000"
• "AirPods Pro 值得買嗎？"

📝 【指令說明】
• 評價查詢：[商品名] + 評價/好不好/推薦
• 價格查詢：[商品名] + 價格/多少錢
• 價格追蹤：追蹤 [商品名]，目標價格 [金額]
• 查看追蹤：我的追蹤清單

需要什麼協助嗎？直接告訴我！"""


# ========== 主代理人核心功能 ==========
def create_main_agent() -> CodeAgent:
    """
    創建主控制代理人
    
    Returns:
        配置好的主代理人
    """
    model = LiteLLMModel(
        model_id="gpt-4o-mini", 
        api_key=OPENAI_API_KEY
    )
    
    tools = [
        analyze_user_intent,
        invoke_product_review_agent,
        invoke_price_tracker_agent,
        send_line_reply,
        generate_help_message
    ]
    
    agent = CodeAgent(
        tools=tools,
        model=model,
        additional_authorized_imports=["re", "json"]
    )
    
    return agent


def process_with_main_agent(user_id: str, message: str, reply_token: str):
    """
    使用主代理人處理用戶訊息
    
    Args:
        user_id: 用戶ID
        message: 用戶訊息
        reply_token: LINE回覆令牌
    """
    try:
        # 創建主代理人
        agent = create_main_agent()
        
        logger.info(f"主代理人收到訊息 from {user_id}: {message}")
        
        # 執行主代理人邏輯
        result = agent.run(f"""
{MAIN_AGENT_SYSTEM_PROMPT}

現在需要處理以下用戶訊息：
- 用戶ID: {user_id}
- 訊息內容: {message}
- 回覆令牌: {reply_token}

執行步驟：
1. 使用 analyze_user_intent 分析用戶意圖
2. 檢查返回的 is_shopping_related 欄位：
   - 如果為 False，使用 send_line_reply 回覆："❌ 此問題與SmartShopSaver功能無關，無法回答。SmartShopSaver專注於協助您解決購物相關問題。"
   - 如果為 True，繼續下一步
3. 根據 primary_intent 的 agent 欄位決定調用哪個子代理人：
   - 如果是 'ProductReviewAgent'，調用 invoke_product_review_agent
   - 如果是 'PriceTrackerAgent'，調用 invoke_price_tracker_agent
   - 如果沒有明確意圖，使用 generate_help_message
4. 使用 send_line_reply 發送回應給用戶

特別注意：
- 所有非購物相關的問題都要拒絕回答
- 使用自然、友善的語氣
- 確保回應使用繁體中文
""")
        
        logger.info(f"主代理人處理完成: {result}")
        
    except Exception as e:
        logger.error(f"主代理人處理失敗: {e}", exc_info=True)
        try:
            send_line_reply(
                reply_token,
                "❌ 系統處理過程中發生錯誤，請稍後再試"
            )
        except:
            pass


# ========== Flask 路由處理 ==========
@app.route("/", methods=['GET'])
def home():
    """健康檢查端點"""
    return "SmartShopSaver Multi-Agent System is running on Render!"


@app.route("/callback", methods=['POST'])
def callback():
    """LINE Webhook 回調處理"""
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"Handler error: {e}")
        abort(500)
    
    return 'OK'


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """處理LINE訊息事件"""
    try:
        user_id = event.source.user_id
        message_text = event.message.text.strip()
        reply_token = event.reply_token
        
        # 使用主代理人處理
        process_with_main_agent(user_id, message_text, reply_token)
        
    except Exception as e:
        logger.error(f"訊息處理失敗: {e}", exc_info=True)


# ========== 主程式入口 ==========
if __name__ == "__main__":
    logger.info("SmartShopSaver 多代理人系統啟動中...")
    logger.info("主代理人：負責任務分派")
    logger.info("子代理人1：商品評論分析 (OpenAI支援)")
    logger.info("子代理人2：價格查詢追蹤")
    
    # 啟動價格追蹤代理人的背景任務
    try:
        from price_tracker import SmartPriceTracker
        price_agent = SmartPriceTracker(line_bot_api)
        # 如果有背景任務功能，啟動它
        # price_agent.start_background_tasks()
        logger.info("價格追蹤系統初始化完成")
    except ImportError as e:
        logger.error(f"無法導入價格追蹤代理人: {e}")
    except Exception as e:
        logger.error(f"價格追蹤系統初始化失敗: {e}")
    
    # 取得 PORT 環境變數
    port = int(os.environ.get('PORT', 5000))
    
    # 啟動Flask應用
    app.run(host='0.0.0.0', port=port, debug=False)
