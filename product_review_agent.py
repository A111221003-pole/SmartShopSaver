# product_review_agent.py - 商品評論子代理人（Render版本）
import os
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import logging
import time
import re
import requests
import urllib.parse
from smolagents import CodeAgent, LiteLLMModel, tool
from openai import OpenAI

logger = logging.getLogger(__name__)

# 系統提示詞
REVIEW_SYSTEM_PROMPT = """
你是SmartShopSaver商品評論分析專家，專注於提供商品評價分析和購買建議。

【核心功能】
* 分析商品真實評價
* 提供專業購買建議
* 比較商品優缺點
* 推薦最佳購買平台

【回覆原則】
- 使用繁體中文
- 保持客觀中立
- 提供實用建議
"""


# ========== 獨立工具函數（符合 smolagents 要求）==========
@tool
def get_price_range(product_name: str) -> str:
    """
    從 PChome、MOMO 取得商品價格區間
    
    Args:
        product_name: 商品名稱
        
    Returns:
        價格區間字串
    """
    prices = []
    try:
        # PChome
        url = f"https://ecshweb.pchome.com.tw/search/v3.3/all/results?q={urllib.parse.quote(product_name)}&page=1&sort=sale/dc"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            prices = [item['price'] for item in data.get('prods', [])[:10] if 'price' in item]
    except Exception as e:
        logger.error(f"從PChome獲取價格時出錯: {str(e)}")
    
    if prices:
        return f"NT${min(prices):,}~NT${max(prices):,}"
    else:
        return "無法獲取價格資訊"


@tool
def extract_keywords(text: str) -> Dict[str, str]:
    """
    從用戶輸入的文本中提取商品關鍵字
    
    Args:
        text: 用戶輸入的文本
        
    Returns:
        包含平台和關鍵字的字典
    """
    # 移除停用詞
    stopwords = [
        "我想", "我要", "想要", "請問", "請", "想", "在", "哪裡", 
        "如何", "怎麼", "可以", "購買", "買", "找", "推薦", "的", 
        "了", "嗎", "呢", "啊", "吧", "哦", "喔", "一下",
        "評價", "評論", "好不好", "好用", "值得買", "怎麼樣"
    ]
    
    cleaned_text = text
    for word in stopwords:
        cleaned_text = cleaned_text.replace(word, " ")
    
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    
    if not cleaned_text:
        cleaned_text = "商品"
    
    return {"platform": "all", "keywords": cleaned_text}


@tool
def is_shopping_related(query: str) -> bool:
    """
    檢查用戶問題是否與購物相關
    
    Args:
        query: 用戶查詢
        
    Returns:
        是否與購物相關
    """
    # 購物相關關鍵字
    shopping_keywords = [
        "購物", "買", "商品", "產品", "價格", "優惠", "比價", "評價",
        "蝦皮", "pchome", "momo", "樂天", "淘寶", "亞馬遜", "amazon",
        "價錢", "多少錢", "特價", "折扣", "促銷", "好不好", "推薦",
        "好用", "評論", "開箱", "退貨", "保固", "值得買", "便宜",
        "記帳", "消費", "支出", "花費", "預算"
    ]
    
    # 非購物相關的關鍵字（用於排除）
    non_shopping_keywords = [
        "天氣", "新聞", "股票", "政治", "運動", "遊戲攻略",
        "料理", "食譜", "健康", "醫療", "教育", "學習",
        "程式", "編程", "數學", "科學", "歷史", "地理",
        "音樂", "電影", "書籍", "小說", "詩詞", "文學",
        "笑話", "故事", "聊天", "你好", "謝謝", "再見"
    ]
    
    query_lower = query.lower()
    
    # 先檢查是否包含非購物關鍵字
    for keyword in non_shopping_keywords:
        if keyword in query_lower:
            return False
    
    # 再檢查是否包含購物關鍵字
    for keyword in shopping_keywords:
        if keyword in query_lower:
            return True
    
    # 檢查是否可能是商品名稱（包含英文+數字的組合）
    if re.search(r'[a-zA-Z]+\s*\d+|\d+\s*[a-zA-Z]+', query):
        return True
    
    # 檢查是否包含品牌名稱
    brands = [
        "apple", "iphone", "samsung", "sony", "nike", "adidas", 
        "asus", "acer", "lenovo", "dell", "hp", "lg", "xiaomi",
        "羅技", "razer", "雷蛇", "viper", "logitech", "steelseries"
    ]
    
    for brand in brands:
        if brand in query_lower:
            return True
    
    return False


@tool
def generate_product_response(product_name: str, price_range: str) -> str:
    """
    為產品生成詳細評價回應
    
    Args:
        product_name: 商品名稱
        price_range: 價格區間
        
    Returns:
        格式化的商品評價回應
    """
    encoded_keyword = urllib.parse.quote(product_name)
    
    # 使用OpenAI生成評價分析
    try:
        # 從環境變數獲取 API Key
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY 環境變數未設定")
        
        # 創建OpenAI客戶端
        openai_client = OpenAI(api_key=api_key)
        
        prompt = f"""請為「{product_name}」生成詳細的商品評價分析，使用以下格式：

【{product_name}】真實評價分析：
⭐ 評分：[根據商品類型和品質給予1-10分評分，使用星星符號]（X/10分）
🎁 好評率：[估計一個合理的百分比]%

💰 價格區間：{price_range}

✅ 真實正面評價：
[列出3-4點該商品的優點或正面評價]

❌ 真實負面評價：
[列出2-3點該商品的缺點或需要注意的地方]

💡 購買建議：
[給出專業的購買建議，包括適合的使用者群體和購買時機]

📋 推薦購買連結：
• 蝦皮：https://shopee.tw/search?keyword={encoded_keyword}
• PChome：https://ecshweb.pchome.com.tw/search/v3.3/?q={encoded_keyword}
• MOMO：https://www.momoshop.com.tw/search/searchShop.jsp?keyword={encoded_keyword}
• 樂天：https://www.rakuten.com.tw/search/{encoded_keyword}/
• Yahoo奇摩：https://tw.bid.yahoo.com/search/auction/product?p={encoded_keyword}"""
        
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "你是專業的商品評論分析師，請提供客觀、實用的商品評價。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=800
        )
        
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"生成產品回應時出錯: {str(e)}")
        return f"""【{product_name}】商品資訊：

💰 價格區間：{price_range}

📋 推薦購買連結：
• 蝦皮：https://shopee.tw/search?keyword={encoded_keyword}
• PChome：https://ecshweb.pchome.com.tw/search/v3.3/?q={encoded_keyword}
• MOMO：https://www.momoshop.com.tw/search/searchShop.jsp?keyword={encoded_keyword}

💡 詳細評價分析暫時無法提供，請直接前往購物平台查看用戶評價。"""


# ========== 商品評論代理人類別 ==========
class ProductReviewAgent:
    """商品評論分析子代理人"""
    
    def __init__(self):
        # 從環境變數獲取 API Key
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY 環境變數必須設定")
        
        self.openai_client = OpenAI(api_key=self.openai_api_key)
        self.agent = self._create_agent()
    
    def _create_agent(self) -> CodeAgent:
        """創建代理人實例"""
        model = LiteLLMModel(
            model_id="gpt-4o-mini",
            api_key=self.openai_api_key
        )
        
        # 註冊工具 - 使用全域定義的工具函數
        tools = [
            get_price_range,
            extract_keywords,
            is_shopping_related,
            generate_product_response
        ]
        
        agent = CodeAgent(
            tools=tools,
            model=model,
            additional_authorized_imports=["re", "urllib.parse", "time", "requests", "json", "os"]
        )
        
        return agent
    
    def process_message(self, user_id: str, message: str) -> str:
        """
        處理用戶訊息（供主代理人調用）
        
        Args:
            user_id: 用戶ID
            message: 用戶訊息
            
        Returns:
            處理結果
        """
        try:
            logger.info(f"商品評論代理人處理訊息: {message}")
            
            # 使用代理人執行邏輯
            result = self.agent.run(f"""
{REVIEW_SYSTEM_PROMPT}

收到用戶查詢：{message}

請執行以下步驟：
1. 使用 is_shopping_related 檢查是否與購物相關
2. 如果不相關，回覆："❌ 此問題與SmartShopSaver功能無關，無法回答。SmartShopSaver專注於協助您解決購物相關問題。"
3. 如果相關：
   - 使用 extract_keywords 提取商品關鍵字
   - 使用 get_price_range 獲取價格區間
   - 使用 generate_product_response 生成完整的商品評價分析
4. 確保回應使用繁體中文
""")
            
            return str(result)
            
        except Exception as e:
            logger.error(f"商品評論代理人處理失敗: {e}", exc_info=True)
            return "❌ 商品評價分析暫時無法使用，請稍後再試"


# 創建代理人實例的工廠函數
def create_product_review_agent():
    """創建商品評論代理人實例"""
    return ProductReviewAgent()
