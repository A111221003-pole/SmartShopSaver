# SmartShopSaver 多代理人系統
import os
import json
import logging
import re
from typing import Dict
from flask import Flask, request, abort

# LINE Bot SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# smolagents
from smolagents import CodeAgent, LiteLLMModel, tool

# ===== 日誌 =====
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ===== 環境變數（Render 設定）=====
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    logger.warning("⚠️ LINE 金鑰尚未設定（LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET）")
if not OPENAI_API_KEY:
    logger.warning("⚠️ OPENAI_API_KEY 尚未設定")

# ===== 初始化 SDK =====
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN) if CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(CHANNEL_SECRET) if CHANNEL_SECRET else None

# ===== Flask App =====
app = Flask(__name__)

# ===== 系統提示 =====
MAIN_AGENT_SYSTEM_PROMPT = """
你是SmartShopSaver主控制代理人，負責理解用戶需求並分派任務給適當的子代理人。
（以下略，保留你的原文規則與回應原則）
"""

# ===== 工具 =====
@tool
def analyze_user_intent(message: str) -> Dict:
    message_lower = message.lower()
    non_shopping_indicators = [
        "天氣","新聞","股票","股市","政治","選舉","運動","比賽",
        "遊戲攻略","遊戲","電玩","料理","食譜","做菜","烹飪",
        "健康","醫療","看病","醫生","藥","症狀","疾病",
        "教育","學習","考試","作業","功課","學校",
        "程式","編程","代碼","coding","python","java",
        "數學","物理","化學","生物","科學","歷史","地理",
        "音樂","歌曲","歌詞","電影","影片","電視","追劇",
        "書籍","小說","詩詞","文學","作文","寫作",
        "笑話","故事","聊天","閒聊","你是誰","你好嗎","早安","晚安","謝謝","再見","拜拜"
    ]
    for indicator in non_shopping_indicators:
        if indicator in message_lower:
            return {'message': message, 'intents': [], 'primary_intent': None, 'is_shopping_related': False}

    review_keywords = ['評價','評論','好不好','好用','推薦','建議','分析','優點','缺點','心得','開箱','值得買','品質','耐用','商品資訊','產品介紹','規格','特色','功能','如何','怎麼樣','怎樣','好嗎','評測','測評','使用心得','用戶評價','買家評價','真實評價','網友評價','值不值得','適合','好壞','優劣','比較','差異','選擇']
    price_keywords  = ['價格','多少錢','比價','追蹤','監控','通知','降價','便宜','特價','折扣','優惠','目標價','低於','售價','報價','賣多少','現在什麼價','幾元','幾塊','nt$','成本','定價','市價','行情','價位','預算','貴不貴','划算','cp值','性價比']

    import re as _re
    review_patterns = [r'(.+)(?:的)?評[價論](?:如何|怎[麼樣])?', r'(.+)好不好[用買]?', r'(.+)值[不得]?[得的]買[嗎?]?', r'(.+)推薦[嗎]?', r'(.+)怎麼樣', r'想?[買購](.+)', r'(.+)(?:跟|和|與)(.+)(?:哪個|那個)好', r'請?(?:分析|介紹|說明)(?:一下)?(.+)', r'(.+)(?:有什麼|有哪些)(?:優點|缺點)', r'(.+)適合(?:我|什麼人)?[嗎?]?']
    price_patterns  = [r'(.+)(?:的)?價[格錢](?:是)?(?:多少|幾元)?', r'(.+)(?:要)?多少錢', r'(.+)賣(?:多少|幾元)', r'查(?:詢)?(.+)(?:的)?價[格錢]', r'比價(.+)', r'(.+)現在(?:什麼)?價[格位]', r'追蹤(.+)(?:的)?(?:價格|降價)', r'(.+)(?:降價|特價|優惠)(?:了[嗎?]|通知)', r'(.+)(?:在)?哪[裡裏](?:買)?(?:比較)?便宜', r'(.+)(?:貴不貴|划算[嗎?])']

    review_score = 10 if any(_re.search(p, message, _re.IGNORECASE) for p in review_patterns) else sum(2 for k in review_keywords if k in message_lower)
    price_score  = 10 if any(_re.search(p, message, _re.IGNORECASE) for p in price_patterns) else sum(2 for k in price_keywords  if k in message_lower)

    intents = []
    if review_score > 0:
        intents.append({'type': 'review', 'score': review_score, 'agent': 'ProductReviewAgent'})
    if price_score > 0:
        intents.append({'type': 'price', 'score': price_score, 'agent': 'PriceTrackerAgent'})

    if not intents:
        for indicator in ['iphone','samsung','sony','apple','nike','adidas','asus','msi','acer','lenovo','hp','dell','lg','xiaomi','小米','oppo','vivo','huawei','華為','ps5','ps4','xbox','switch','nintendo','macbook','ipad','airpods','apple watch','手機','電腦','筆電','平板','耳機','滑鼠','鍵盤','螢幕','顯卡','主機','相機','手錶','電視','razer','雷蛇','viper','logitech','羅技','steelseries']:
            if indicator in message_lower:
                intents.append({'type': 'review', 'score': 5, 'agent': 'ProductReviewAgent', 'inferred': True})
                break

    return {
        'message': message,
        'intents': intents,
        'primary_intent': max(intents, key=lambda x: x['score']) if intents else None,
        'is_shopping_related': len(intents) > 0
    }

@tool
def invoke_product_review_agent(user_id: str, message: str) -> str:
    try:
        from agents.product_review_agent import ProductReviewAgent
        agent = ProductReviewAgent()
        return agent.process_message(user_id, message)
    except Exception as e:
        logger.error(f"商品評論代理人失敗: {e}", exc_info=True)
        return "❌ 商品評論分析暫時無法使用，請稍後再試"

@tool
def invoke_price_tracker_agent(user_id: str, message: str) -> str:
    try:
        from agents.price_tracker_agent import PriceTrackerAgent
        agent = PriceTrackerAgent()
        return agent.process_message(user_id, message)
    except Exception as e:
        logger.error(f"價格追蹤代理人失敗: {e}", exc_info=True)
        return "❌ 價格查詢功能暫時無法使用，請稍後再試"

@tool
def send_line_reply(reply_token: str, message: str) -> bool:
    if not line_bot_api:
        logger.error("LINE Bot API 未初始化（缺少金鑰）")
        return False
    try:
        if len(message) > 5000:
            message = message[:4900] + "\n\n⚠️ 內容過長已截斷"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=message))
        return True
    except Exception as e:
        logger.error(f"發送LINE訊息失敗: {e}", exc_info=True)
        return False

@tool
def generate_help_message() -> str:
    return ("🤖 SmartShopSaver 智能購物助手\n\n"
            "📊【商品評價分析】與 💰【價格查詢追蹤】…（略，保留你的原文）")

def create_main_agent() -> CodeAgent:
    model = LiteLLMModel(model_id=os.getenv("S2S_MODEL_ID", "gpt-4o-mini"),
                         api_key=OPENAI_API_KEY)
    tools = [analyze_user_intent, invoke_product_review_agent, invoke_price_tracker_agent, send_line_reply, generate_help_message]
    return CodeAgent(tools=tools, model=model, additional_authorized_imports=["re", "json"])

def process_with_main_agent(user_id: str, message: str, reply_token: str):
    try:
        agent = create_main_agent()
        prompt = f"""
{MAIN_AGENT_SYSTEM_PROMPT}

現在需要處理以下用戶訊息：
- 用戶ID: {user_id}
- 訊息內容: {message}
- 回覆令牌: {reply_token}

執行步驟：
1. 使用 analyze_user_intent 分析用戶意圖
2. 若 is_shopping_related 為 False，使用 send_line_reply 回覆拒答文案
3. 否則依 primary_intent.agent 呼叫對應工具
4. 用 send_line_reply 回覆用戶
"""
        agent.run(prompt)
    except Exception as e:
        logger.error(f"主代理人處理失敗: {e}", exc_info=True)
        try:
            send_line_reply(reply_token, "❌ 系統處理過程中發生錯誤，請稍後再試")
        except Exception:
            pass

# ===== 路由 =====
@app.route("/", methods=["GET"])
def home():
    return "SmartShopSaver Multi-Agent System is running!"

@app.route("/callback", methods=["POST"])
def callback():
    if not handler:
        abort(500, description="LINE handler 未初始化（缺少金鑰）")
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logging.error("Invalid signature")
        abort(400)
    except Exception as e:
        logging.error(f"Handler error: {e}", exc_info=True)
        abort(500)
    return "OK"

@handler.add(MessageEvent, message=TextMessage) if handler else (lambda f: f)
def handle_message(event):
    try:
        user_id = event.source.user_id
        message_text = event.message.text.strip()
        reply_token = event.reply_token
        process_with_main_agent(user_id, message_text, reply_token)
    except Exception as e:
        logger.error(f"訊息處理失敗: {e}", exc_info=True)

# ===== 可選：在 Render 啟動時開背景任務 =====
@app.before_first_request
def _maybe_start_background_tasks():
    if os.getenv("ENABLE_PRICE_TRACKER_BG", "").lower() == "true":
        try:
            from agents.price_tracker_agent import PriceTrackerAgent
            PriceTrackerAgent(line_bot_api).start_background_tasks()
            logger.info("價格追蹤背景任務啟動成功")
        except Exception as e:
            logger.error(f"背景任務啟動失敗: {e}", exc_info=True)
