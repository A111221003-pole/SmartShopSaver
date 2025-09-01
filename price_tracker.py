# complete_price_tracker_system.py - 完整優化的價格追蹤系統
import os
import json
import re
import requests
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from urllib.parse import quote, urlparse, urljoin
import threading
import time
from bs4 import BeautifulSoup
import cloudscraper
from difflib import SequenceMatcher
import random
import psycopg2
from psycopg2.extras import RealDictCursor
import sqlite3

# 配置日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class PriceTracker:
    """價格追蹤器數據結構"""
    user_id: str
    product_name: str
    target_price: float
    platforms: List[str]
    created_at: datetime
    last_checked: Optional[datetime] = None
    last_price: Optional[float] = None
    is_active: bool = True
    track_mode: str = "below_price"
    tracker_id: Optional[str] = None

@dataclass
class ConversationContext:
    """對話上下文"""
    user_id: str
    last_product: Optional[str] = None
    last_action: Optional[str] = None
    last_price: Optional[float] = None
    conversation_history: List[Dict] = None
    
    def __post_init__(self):
        if self.conversation_history is None:
            self.conversation_history = []

class ProductFilterEngine:
    """商品過濾引擎 - 自動過濾配件和不相關商品"""
    
    def __init__(self):
        # 定義配件關鍵字（中英文）
        self.accessory_keywords = {
            'zh': [
                '保護套', '保護殼', '手機殼', '螢幕保護貼', '保護貼', '鋼化膜',
                '充電器', '充電線', '傳輸線', 'USB', '轉接頭', '轉接器',
                '耳機套', '耳機殼', '耳機塞', '耳塞', '海綿套',
                '支架', '車架', '桌架', '手機架',
                '貼紙', '裝飾貼', '彩繪', '背貼',
                '清潔', '清潔劑', '清潔布', '拭鏡布',
                '配件包', '組合包', '套裝',
                '維修', '更換', '零件', '適用於', '相容'
            ],
            'en': [
                'case', 'cover', 'protector', 'screen', 'tempered', 'glass',
                'charger', 'cable', 'cord', 'adapter', 'usb',
                'tips', 'foam', 'silicone', 'rubber',
                'stand', 'holder', 'mount', 'dock',
                'skin', 'decal', 'sticker',
                'cleaning', 'cleaner', 'cloth',
                'kit', 'bundle', 'package',
                'replacement', 'spare', 'repair', 'compatible', 'for'
            ]
        }
        
        # 定義品牌排除詞
        self.brand_exclusions = {
            'iphone': ['samsung', 'xiaomi', 'oppo', 'vivo', 'huawei', 'sony', 'htc'],
            'ps5': ['xbox', 'switch', 'nintendo', 'pc'],
            'airpods': ['sony', 'bose', 'sennheiser', 'jbl', 'beats'],
            'macbook': ['dell', 'hp', 'asus', 'acer', 'lenovo', 'msi'],
            'viper': ['logitech', 'steelseries', 'corsair', 'roccat']
        }
    
    def is_accessory(self, product_name: str) -> bool:
        """判斷是否為配件"""
        product_lower = product_name.lower()
        
        # 檢查中文配件關鍵字
        for keyword in self.accessory_keywords['zh']:
            if keyword in product_lower:
                return True
        
        # 檢查英文配件關鍵字
        for keyword in self.accessory_keywords['en']:
            if keyword in product_lower:
                return True
        
        return False
    
    def has_brand_conflict(self, product_name: str, target_name: str) -> bool:
        """檢查品牌衝突"""
        product_lower = product_name.lower()
        target_lower = target_name.lower()
        
        # 找出目標商品的品牌
        target_brand = None
        for brand in self.brand_exclusions.keys():
            if brand in target_lower:
                target_brand = brand
                break
        
        if not target_brand:
            return False
        
        # 檢查是否包含衝突品牌
        conflicting_brands = self.brand_exclusions[target_brand]
        for brand in conflicting_brands:
            if brand in product_lower:
                return True
        
        return False
    
    def calculate_relevance_score(self, product_name: str, target_name: str) -> float:
        """計算商品相關性分數（0-1）"""
        product_clean = re.sub(r'[^\w\s]', ' ', product_name.lower()).strip()
        target_clean = re.sub(r'[^\w\s]', ' ', target_name.lower()).strip()
        
        # 基礎文字匹配
        if target_clean in product_clean:
            base_score = 0.8
        else:
            base_score = SequenceMatcher(None, product_clean, target_clean).ratio()
        
        # 關鍵字匹配加分
        target_words = set(target_clean.split())
        product_words = set(product_clean.split())
        
        if target_words:
            overlap = len(target_words.intersection(product_words))
            keyword_score = overlap / len(target_words)
            base_score = max(base_score, keyword_score * 0.7)
        
        # 配件扣分
        if self.is_accessory(product_name):
            base_score *= 0.3
        
        # 品牌衝突大幅扣分
        if self.has_brand_conflict(product_name, target_name):
            base_score *= 0.1
        
        return base_score
    
    def is_relevant_product(self, product_name: str, target_name: str, 
                           min_score: float = 0.65, allow_accessories: bool = False) -> bool:
        """判斷商品是否相關（改進版）"""
        
        # 如果不允許配件，先檢查是否為配件
        if not allow_accessories and self.is_accessory(product_name):
            return False
        
        # 計算相關性分數
        score = self.calculate_relevance_score(product_name, target_name)
        
        return score >= min_score

class EnhancedNaturalLanguageParser:
    """增強的自然語言解析器"""
    
    def __init__(self):
        # 擴展意圖關鍵字
        self.intent_patterns = {
            'price_query': [
                '多少錢', '價格', '查價', '比價', '查詢', '搜尋',
                '賣多少', '現在多少', '目前價格', '市價', '行情',
                '值多少', '要花多少', '成本', '售價'
            ],
            'track_product': [
                '追蹤', '監控', '通知', '提醒', '關注', '盯著',
                '降價', '便宜', '打折', '特價', '優惠',
                '等', '等到', '到時候', '的話', '如果', '當'
            ],
            'price_expressions': [
                '低於', '少於', '小於', '不超過', '以下', '以內',
                '便宜到', '降到', '跌到', '掉到', '下降到',
                '在.*以下', '不要超過', '最多', '頂多'
            ]
        }
        
        # 數字表達映射
        self.number_mapping = {
            '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
            '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
            '百': 100, '千': 1000, '萬': 10000, '十萬': 100000,
            '兩': 2, '倆': 2, '幾': 5, '多': 0
        }
        
        # 常見商品別名
        self.product_aliases = {
            'iphone': ['蘋果手機', '愛鳳', 'i鳳', '蘋果機'],
            'airpods': ['蘋果耳機', '無線耳機', 'airpod'],
            'ps5': ['PlayStation 5', 'playstation5', 'ps5主機', '遊戲機'],
            'switch': ['任天堂', 'nintendo switch', 'ns'],
            'macbook': ['蘋果筆電', 'mac筆電', 'macbook pro', 'macbook air'],
            'viper': ['viper v3', 'viper v2', 'viper v3pro', 'viper v2pro']
        }
    
    def normalize_product_name(self, text: str) -> str:
        """標準化商品名稱"""
        text = text.lower()
        
        # 處理商品別名
        for standard, aliases in self.product_aliases.items():
            for alias in aliases:
                if alias in text:
                    text = text.replace(alias, standard)
        
        return text.strip()
    
    def extract_numbers_from_text(self, text: str) -> List[float]:
        """從文字中提取數字（支援中文數字）"""
        numbers = []
        
        # 提取阿拉伯數字
        arabic_numbers = re.findall(r'[0-9,]+', text)
        for num in arabic_numbers:
            try:
                numbers.append(float(num.replace(',', '')))
            except ValueError:
                pass
        
        # 處理中文數字表達
        chinese_patterns = [
            r'([一二三四五六七八九十百千萬]+)',
            r'(幾[千萬])',
            r'([0-9]+[千萬])'
        ]
        
        for pattern in chinese_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                try:
                    num = self.chinese_to_number(match)
                    if num:
                        numbers.append(num)
                except:
                    pass
        
        return numbers
    
    def chinese_to_number(self, chinese_num: str) -> Optional[float]:
        """中文數字轉換為阿拉伯數字"""
        if not chinese_num:
            return None
            
        # 簡單的中文數字處理
        result = 0
        
        # 處理萬
        if '萬' in chinese_num:
            parts = chinese_num.split('萬')
            if len(parts) == 2:
                left = parts[0]
                right = parts[1] if parts[1] else '0'
                
                left_num = 0
                for char in left:
                    if char in self.number_mapping:
                        if char in ['十', '百', '千']:
                            left_num = max(1, left_num) * self.number_mapping[char]
                        else:
                            left_num = left_num * 10 + self.number_mapping[char]
                
                right_num = 0
                for char in right:
                    if char in self.number_mapping:
                        if char in ['十', '百', '千']:
                            right_num = max(1, right_num) * self.number_mapping[char]
                        else:
                            right_num = right_num * 10 + self.number_mapping[char]
                
                result = left_num * 10000 + right_num
        
        # 處理千
        elif '千' in chinese_num:
            parts = chinese_num.split('千')
            if len(parts) == 2:
                left = parts[0] if parts[0] else '一'
                right = parts[1] if parts[1] else '0'
                
                left_num = self.number_mapping.get(left, 1) if left != '十' else 10
                right_num = 0
                for char in right:
                    if char in self.number_mapping:
                        right_num = right_num * 10 + self.number_mapping[char]
                
                result = left_num * 1000 + right_num
        
        return float(result) if result > 0 else None
    
    def extract_intent_with_context(self, message: str, context: ConversationContext = None) -> Dict:
        """基於上下文的意圖提取"""
        message = message.strip()
        
        # 如果訊息很簡短，嘗試利用上下文
        if len(message) < 10 and context:
            if any(word in message for word in ['這個', '它', '那個', '同樣']):
                if context.last_product:
                    # "追蹤這個" -> 使用上次查詢的商品
                    if any(word in message for word in self.intent_patterns['track_product']):
                        return {
                            'action': 'track_product_need_price',
                            'product_name': context.last_product,
                            'confidence': 0.8,
                            'context_used': True
                        }
        
        # 檢查是否為價格查詢
        if self.contains_intent(message, 'price_query'):
            product_name = self.extract_product_name(message)
            if product_name:
                return {
                    'action': 'query_price',
                    'product_name': product_name,
                    'confidence': 0.9
                }
        
        # 檢查是否為追蹤請求
        if self.contains_intent(message, 'track_product'):
            product_name = self.extract_product_name(message)
            prices = self.extract_numbers_from_text(message)
            
            if product_name and prices:
                return {
                    'action': 'track_product',
                    'product_name': product_name,
                    'target_price': str(int(prices[0])),
                    'confidence': 0.95,
                    'track_mode': 'below_price'
                }
            elif product_name:
                return {
                    'action': 'track_product_need_price',
                    'product_name': product_name,
                    'confidence': 0.8
                }
        
        # 檢查設定相關
        if any(keyword in message for keyword in ['設定', '偏好', '配件', '過濾']):
            return {
                'action': 'user_settings',
                'message': message,
                'confidence': 0.8
            }
        
        # 檢查清單查詢
        if any(keyword in message for keyword in ['清單', '列表', '我的追蹤']):
            return {
                'action': 'list_trackers',
                'confidence': 0.9
            }
        
        # 檢查說明請求
        if any(keyword in message for keyword in ['說明', '幫助', 'help', '使用方法']):
            return {
                'action': 'show_help',
                'confidence': 0.9
            }
        
        # 模糊匹配
        return self.fuzzy_intent_matching(message, context)
    
    def contains_intent(self, message: str, intent_type: str) -> bool:
        """檢查訊息是否包含特定意圖"""
        keywords = self.intent_patterns.get(intent_type, [])
        message_lower = message.lower()
        
        return any(keyword in message_lower for keyword in keywords)
    
    def extract_product_name(self, message: str) -> Optional[str]:
        """智能提取商品名稱"""
        # 移除常見的查詢詞
        cleaning_patterns = [
            r'(請|幫我|給我|我要|想要|想買|想查|查詢|查看|搜尋|比價)',
            r'(價格|多少錢|賣多少|值多少|要多少)',
            r'(追蹤|監控|通知|提醒|關注)',
            r'(低於|少於|小於|不超過|以下|以內)',
            r'(的|之|、|，|。|！|？)',
            r'([0-9,]+元?)'
        ]
        
        cleaned = message
        for pattern in cleaning_patterns:
            cleaned = re.sub(pattern, ' ', cleaned, flags=re.IGNORECASE)
        
        # 移除多餘空格
        cleaned = ' '.join(cleaned.split())
        
        # 如果清理後太短，嘗試更保守的清理
        if len(cleaned) < 3:
            # 只移除最明顯的查詢詞
            conservative_patterns = [
                r'^(請|幫我|給我|我要)',
                r'(多少錢|價格)$'
            ]
            
            cleaned = message
            for pattern in conservative_patterns:
                cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE).strip()
        
        # 標準化商品名稱
        if cleaned:
            cleaned = self.normalize_product_name(cleaned)
        
        return cleaned if len(cleaned) >= 2 else None
    
    def fuzzy_intent_matching(self, message: str, context: ConversationContext = None) -> Dict:
        """模糊意圖匹配"""
        message_lower = message.lower()
        
        # 檢查是否包含商品關鍵字但意圖不明
        potential_products = []
        
        # 常見3C產品關鍵字
        product_keywords = [
            'iphone', 'ipad', 'macbook', 'airpods', 'apple',
            'samsung', 'xiaomi', 'oppo', 'vivo', 'huawei',
            'ps5', 'ps4', 'xbox', 'switch', 'nintendo',
            'viper', 'razer', 'logitech', 'corsair',
            '手機', '筆電', '電腦', '平板', '耳機', '滑鼠', '鍵盤'
        ]
        
        for keyword in product_keywords:
            if keyword in message_lower:
                potential_products.append(keyword)
        
        if potential_products:
            # 如果包含商品關鍵字，嘗試猜測意圖
            if any(word in message_lower for word in ['便宜', '降', '低', '少', '打折']):
                return {
                    'action': 'track_product_need_price',
                    'product_name': potential_products[0],
                    'confidence': 0.6,
                    'suggestion': f"看起來您想追蹤 {potential_products[0]}，請告訴我目標價格"
                }
            elif any(word in message_lower for word in ['多少', '價格', '錢']):
                return {
                    'action': 'query_price',
                    'product_name': potential_products[0],
                    'confidence': 0.6
                }
        
        # 完全無法理解
        return {
            'action': 'unknown',
            'confidence': 0.0,
            'suggestion': "我不太理解您的需求，可以說得更具體一些嗎？"
        }
    
    def generate_clarification_question(self, intent: Dict) -> str:
        """生成澄清問題"""
        if intent['action'] == 'track_product_need_price':
            product = intent.get('product_name', '這個商品')
            return f"您想追蹤 {product}，請告訴我當價格低於多少時要通知您？"
        
        elif intent['action'] == 'query_price' and intent['confidence'] < 0.8:
            return "您想查詢哪個具體商品的價格？可以說得更詳細一些"
        
        elif intent.get('suggestion'):
            return intent['suggestion']
        
        return "請問您想要做什麼？可以說「查價格」或「設定追蹤」"

class DatabaseManager:
    """資料庫管理器 - 支援 SQLite 和 PostgreSQL"""
    
    def __init__(self, database_type: str = "sqlite"):
        self.database_type = database_type
        self.connection = None
        
        if database_type == "postgresql":
            self.setup_postgresql()
        else:
            self.setup_sqlite()
    
    def setup_sqlite(self):
        """設定本地 SQLite 資料庫"""
        try:
            self.connection = sqlite3.connect('price_tracker.db', check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self.create_tables_sqlite()
            logger.info("SQLite 資料庫初始化完成")
        except Exception as e:
            logger.error(f"SQLite 初始化失敗: {e}")
    
    def setup_postgresql(self):
        """設定 Render PostgreSQL 資料庫"""
        try:
            database_url = os.getenv('DATABASE_URL')
            
            if not database_url:
                raise ValueError("未設定 DATABASE_URL 環境變數")
            
            self.connection = psycopg2.connect(database_url)
            self.create_tables_postgresql()
            logger.info("PostgreSQL 資料庫初始化完成")
        except Exception as e:
            logger.error(f"PostgreSQL 初始化失敗: {e}")
            logger.info("回退使用 SQLite 資料庫")
            self.database_type = "sqlite"
            self.setup_sqlite()
    
    def create_tables_sqlite(self):
        """建立 SQLite 資料表"""
        cursor = self.connection.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_trackers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                target_price REAL NOT NULL,
                platforms TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMP,
                last_price REAL,
                is_active BOOLEAN DEFAULT TRUE,
                track_mode TEXT DEFAULT 'below_price'
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker_id INTEGER,
                product_name TEXT NOT NULL,
                price REAL NOT NULL,
                platform TEXT NOT NULL,
                product_link TEXT,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tracker_id) REFERENCES price_trackers (id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                allow_accessories BOOLEAN DEFAULT FALSE,
                min_relevance_score REAL DEFAULT 0.65,
                preferred_platforms TEXT,
                notification_settings TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        self.connection.commit()
    
    def create_tables_postgresql(self):
        """建立 PostgreSQL 資料表"""
        cursor = self.connection.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_trackers (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(100) NOT NULL,
                product_name VARCHAR(200) NOT NULL,
                target_price DECIMAL(10,2) NOT NULL,
                platforms TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMP,
                last_price DECIMAL(10,2),
                is_active BOOLEAN DEFAULT TRUE,
                track_mode VARCHAR(50) DEFAULT 'below_price'
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id SERIAL PRIMARY KEY,
                tracker_id INTEGER REFERENCES price_trackers(id),
                product_name VARCHAR(200) NOT NULL,
                price DECIMAL(10,2) NOT NULL,
                platform VARCHAR(50) NOT NULL,
                product_link TEXT,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id VARCHAR(100) PRIMARY KEY,
                allow_accessories BOOLEAN DEFAULT FALSE,
                min_relevance_score DECIMAL(3,2) DEFAULT 0.65,
                preferred_platforms TEXT,
                notification_settings TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_trackers ON price_trackers(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_product_history ON price_history(product_name)")
        
        self.connection.commit()
    
    def save_tracker(self, tracker: PriceTracker) -> str:
        """保存追蹤器到資料庫"""
        cursor = self.connection.cursor()
        
        try:
            if self.database_type == "postgresql":
                cursor.execute("""
                    INSERT INTO price_trackers 
                    (user_id, product_name, target_price, platforms, created_at, 
                     last_checked, last_price, is_active, track_mode)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    tracker.user_id, tracker.product_name, tracker.target_price,
                    ','.join(tracker.platforms), tracker.created_at,
                    tracker.last_checked, tracker.last_price, 
                    tracker.is_active, tracker.track_mode
                ))
                tracker_id = cursor.fetchone()[0]
            else:
                cursor.execute("""
                    INSERT INTO price_trackers 
                    (user_id, product_name, target_price, platforms, created_at, 
                     last_checked, last_price, is_active, track_mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    tracker.user_id, tracker.product_name, tracker.target_price,
                    ','.join(tracker.platforms), tracker.created_at,
                    tracker.last_checked, tracker.last_price, 
                    tracker.is_active, tracker.track_mode
                ))
                tracker_id = cursor.lastrowid
            
            self.connection.commit()
            return str(tracker_id)
            
        except Exception as e:
            self.connection.rollback()
            logger.error(f"保存追蹤器失敗: {e}")
            raise
    
    def load_user_trackers(self, user_id: str) -> List[PriceTracker]:
        """從資料庫載入用戶的追蹤器"""
        cursor = self.connection.cursor()
        
        try:
            if self.database_type == "postgresql":
                cursor.execute("""
                    SELECT * FROM price_trackers 
                    WHERE user_id = %s AND is_active = TRUE
                    ORDER BY created_at DESC
                """, (user_id,))
            else:
                cursor.execute("""
                    SELECT * FROM price_trackers 
                    WHERE user_id = ? AND is_active = TRUE
                    ORDER BY created_at DESC
                """, (user_id,))
            
            rows = cursor.fetchall()
            trackers = []
            
            for row in rows:
                tracker = PriceTracker(
                    user_id=row['user_id'],
                    product_name=row['product_name'],
                    target_price=float(row['target_price']),
                    platforms=row['platforms'].split(',') if row['platforms'] else ['all'],
                    created_at=row['created_at'] if isinstance(row['created_at'], datetime) else datetime.fromisoformat(str(row['created_at'])),
                    last_checked=row['last_checked'] if row['last_checked'] and isinstance(row['last_checked'], datetime) else (datetime.fromisoformat(str(row['last_checked'])) if row['last_checked'] else None),
                    last_price=float(row['last_price']) if row['last_price'] else None,
                    is_active=bool(row['is_active']),
                    track_mode=row['track_mode'] or 'below_price',
                    tracker_id=str(row['id'])
                )
                trackers.append(tracker)
            
            return trackers
            
        except Exception as e:
            logger.error(f"載入追蹤器失敗: {e}")
            return []
    
    def update_tracker_price(self, tracker_id: str, current_price: float):
        """更新追蹤器的當前價格"""
        cursor = self.connection.cursor()
        
        try:
            if self.database_type == "postgresql":
                cursor.execute("""
                    UPDATE price_trackers 
                    SET last_price = ?, last_checked = ?
                    WHERE id = ?
                """, (current_price, datetime.now(), tracker_id))
            
            self.connection.commit()
            
        except Exception as e:
            logger.error(f"更新追蹤器失敗: {e}")
    
    def get_user_preferences(self, user_id: str) -> Dict:
        """獲取用戶偏好設定"""
        cursor = self.connection.cursor()
        
        try:
            if self.database_type == "postgresql":
                cursor.execute("""
                    SELECT * FROM user_preferences WHERE user_id = %s
                """, (user_id,))
            else:
                cursor.execute("""
                    SELECT * FROM user_preferences WHERE user_id = ?
                """, (user_id,))
            
            row = cursor.fetchone()
            
            if row:
                return {
                    'allow_accessories': bool(row['allow_accessories']),
                    'min_relevance_score': float(row['min_relevance_score']),
                    'preferred_platforms': row['preferred_platforms'],
                    'notification_settings': row['notification_settings']
                }
            else:
                return self.create_default_preferences(user_id)
                
        except Exception as e:
            logger.error(f"獲取用戶偏好失敗: {e}")
            return {
                'allow_accessories': False,
                'min_relevance_score': 0.65,
                'preferred_platforms': None,
                'notification_settings': None
            }
    
    def create_default_preferences(self, user_id: str) -> Dict:
        """創建預設用戶偏好"""
        cursor = self.connection.cursor()
        
        default_prefs = {
            'allow_accessories': False,
            'min_relevance_score': 0.65,
            'preferred_platforms': None,
            'notification_settings': None
        }
        
        try:
            if self.database_type == "postgresql":
                cursor.execute("""
                    INSERT INTO user_preferences (user_id, allow_accessories, min_relevance_score)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO NOTHING
                """, (user_id, False, 0.65))
            else:
                cursor.execute("""
                    INSERT OR IGNORE INTO user_preferences 
                    (user_id, allow_accessories, min_relevance_score)
                    VALUES (?, ?, ?)
                """, (user_id, False, 0.65))
            
            self.connection.commit()
            return default_prefs
            
        except Exception as e:
            logger.error(f"創建預設偏好失敗: {e}")
            return default_prefs

class ImprovedPriceSearchAgent:
    """改進的價格搜尋系統"""
    
    def __init__(self, db_manager: DatabaseManager = None):
        self.scraper = cloudscraper.create_scraper()
        self.filter_engine = ProductFilterEngine()
        self.db_manager = db_manager
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
        ]
    
    def get_headers(self):
        """獲取隨機的請求標頭"""
        return {
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Upgrade-Insecure-Requests': '1',
            'Connection': 'keep-alive'
        }
    
    def clean_price(self, price_text: str) -> Optional[int]:
        """清理價格文字並轉換為數字"""
        if not price_text:
            return None
            
        price_str = re.sub(r'[^\d,]', '', price_text)
        price_str = price_str.replace(',', '')
        
        if not price_str:
            return None
            
        try:
            price = int(price_str)
            if 1 <= price <= 50000000:
                return price
        except ValueError:
            pass
        
        return None
    
    def filter_relevant_products(self, products: List[Dict], target_name: str, 
                               user_id: str = None) -> List[Dict]:
        """過濾相關商品"""
        
        user_prefs = {'allow_accessories': False, 'min_relevance_score': 0.65}
        if self.db_manager and user_id:
            user_prefs.update(self.db_manager.get_user_preferences(user_id))
        
        filtered_products = []
        
        for product in products:
            relevance_score = self.filter_engine.calculate_relevance_score(
                product['name'], target_name
            )
            
            if self.filter_engine.is_relevant_product(
                product['name'], 
                target_name,
                min_score=user_prefs['min_relevance_score'],
                allow_accessories=user_prefs['allow_accessories']
            ):
                product['relevance_score'] = relevance_score
                filtered_products.append(product)
        
        filtered_products.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
        
        return filtered_products
    
    def search_findprice(self, product_name: str, user_id: str = None) -> Tuple[List[Dict], str]:
        """搜尋 FindPrice 比價網站"""
        encoded_name = quote(product_name)
        search_url = f"https://www.findprice.com.tw/g/{encoded_name}"
        
        try:
            response = self.scraper.get(search_url, headers=self.get_headers(), timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                prices = []
                
                items = soup.select('.item, .product-item, [class*="item"]')
                
                for item in items[:30]:
                    try:
                        name_elem = item.select_one('.name, .title, .product-name, h3, a[title]')
                        name = ""
                        if name_elem:
                            name = name_elem.get_text(strip=True) or name_elem.get('title', '')
                        
                        price_elem = item.select_one('.price, .money, [class*="price"]')
                        if price_elem:
                            price = self.clean_price(price_elem.get_text())
                            if price and name:
                                link_elem = item.select_one('a[href*="http"]')
                                link = link_elem.get('href') if link_elem else search_url
                                
                                prices.append({
                                    'name': name[:100],
                                    'price': price,
                                    'link': link,
                                    'platform': 'FindPrice'
                                })
                    except Exception as e:
                        logger.debug(f"FindPrice 項目解析錯誤: {e}")
                        continue
                
                filtered_prices = self.filter_relevant_products(prices, product_name, user_id)
                return filtered_prices, search_url
                
        except Exception as e:
            logger.error(f"FindPrice 搜尋失敗: {e}")
        
        return [], search_url
    
    def search_biggo(self, product_name: str, user_id: str = None) -> Tuple[List[Dict], str]:
        """搜尋 BigGo 比價網站"""
        encoded_name = quote(product_name)
        search_url = f"https://biggo.com.tw/s/{encoded_name}/"
        
        try:
            response = self.scraper.get(search_url, headers=self.get_headers(), timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                prices = []
                
                items = soup.select('.track-click, .product, [data-track*="product"]')
                
                for item in items[:20]:
                    try:
                        name_elem = item.select_one('.name, .title, h3, [class*="title"]')
                        name = name_elem.get_text(strip=True) if name_elem else ""
                        
                        price_elem = item.select_one('.price, .money, [class*="price"]')
                        if price_elem:
                            price = self.clean_price(price_elem.get_text())
                            if price and name:
                                link_elem = item.select_one('a[href]')
                                link = urljoin('https://biggo.com.tw', link_elem.get('href')) if link_elem else search_url
                                
                                prices.append({
                                    'name': name[:100],
                                    'price': price,
                                    'link': link,
                                    'platform': 'BigGo'
                                })
                    except Exception as e:
                        logger.debug(f"BigGo 項目解析錯誤: {e}")
                        continue
                
                filtered_prices = self.filter_relevant_products(prices, product_name, user_id)
                return filtered_prices, search_url
                
        except Exception as e:
            logger.error(f"BigGo 搜尋失敗: {e}")
        
        return [], search_url
    
    def search_comprehensive_prices(self, product_name: str, user_id: str = None) -> Dict:
        """綜合價格搜尋"""
        logger.info(f"開始綜合搜尋: {product_name}")
        
        all_prices = []
        search_results = {}
        
        search_functions = [
            ('FindPrice', self.search_findprice),
            ('BigGo', self.search_biggo),
        ]
        
        for source_name, search_func in search_functions:
            try:
                logger.info(f"搜尋 {source_name}...")
                prices, url = search_func(product_name, user_id)
                
                if prices:
                    search_results[source_name] = {
                        'prices': prices,
                        'url': url,
                        'count': len(prices)
                    }
                    all_prices.extend(prices)
                    logger.info(f"{source_name} 過濾後找到 {len(prices)} 個相關結果")
                else:
                    logger.info(f"{source_name} 無相關結果")
                    
            except Exception as e:
                logger.error(f"{source_name} 搜尋失敗: {e}")
        
        if not all_prices:
            return self._create_empty_result(product_name)
        
        all_prices.sort(key=lambda x: x['price'])
        cheapest = all_prices[0]
        
        return {
            'product_name': product_name,
            'cheapest_item': cheapest,
            'min_price': cheapest['price'],
            'max_price': max(p['price'] for p in all_prices),
            'avg_price': sum(p['price'] for p in all_prices) / len(all_prices),
            'total_results': len(all_prices),
            'all_results': all_prices[:20],
            'search_results': search_results,
            'summary': f"過濾後找到 {len(all_prices)} 個相關商品，最低價 NT${cheapest['price']:,}",
            'filter_info': f"已自動過濾配件和不相關商品"
        }
    
    def _create_empty_result(self, product_name: str) -> Dict:
        """創建空結果"""
        return {
            'product_name': product_name,
            'cheapest_item': None,
            'min_price': 0,
            'max_price': 0,
            'avg_price': 0,
            'total_results': 0,
            'all_results': [],
            'summary': f"未找到 {product_name} 的相關主商品",
            'filter_info': "建議檢查商品名稱拼寫或嘗試更通用的關鍵字"
        }

class ContextAwarePriceTracker:
    """具備上下文感知的價格追蹤器"""
    
    def __init__(self, line_bot_api=None, use_database: bool = True, database_type: str = "sqlite"):
        self.db_manager = DatabaseManager(database_type) if use_database else None
        self.price_agent = ImprovedPriceSearchAgent(self.db_manager)
        self.nlp_parser = EnhancedNaturalLanguageParser()
        self.user_trackers = {}
        self.user_contexts = {}
        self._alert_thread = None
        self._is_running = False
        self.line_bot_api = line_bot_api
        
        if self.db_manager:
            self.load_all_trackers()
        
        logger.info("上下文感知價格追蹤代理人初始化完成")
    
    def load_all_trackers(self):
        """從資料庫載入所有用戶的追蹤器"""
        try:
            cursor = self.db_manager.connection.cursor()
            
            if self.db_manager.database_type == "postgresql":
                cursor.execute("SELECT DISTINCT user_id FROM price_trackers WHERE is_active = TRUE")
            else:
                cursor.execute("SELECT DISTINCT user_id FROM price_trackers WHERE is_active = TRUE")
            
            user_ids = [row[0] for row in cursor.fetchall()]
            
            for user_id in user_ids:
                self.user_trackers[user_id] = self.db_manager.load_user_trackers(user_id)
            
            logger.info(f"載入了 {len(user_ids)} 個用戶的追蹤器")
            
        except Exception as e:
            logger.error(f"載入追蹤器失敗: {e}")
    
    def get_user_context(self, user_id: str) -> ConversationContext:
        """獲取或創建用戶上下文"""
        if user_id not in self.user_contexts:
            self.user_contexts[user_id] = ConversationContext(user_id=user_id)
        return self.user_contexts[user_id]
    
    def update_context(self, user_id: str, action: str, product: str = None, price: float = None):
        """更新用戶上下文"""
        context = self.get_user_context(user_id)
        context.last_action = action
        if product:
            context.last_product = product
        if price:
            context.last_price = price
        
        if len(context.conversation_history) > 10:
            context.conversation_history = context.conversation_history[-10:]
    
    def handle_price_query(self, product_name: str, user_id: str = None) -> str:
        """處理價格查詢"""
        try:
            logger.info(f"查詢商品價格: {product_name}")
            
            result = self.price_agent.search_comprehensive_prices(product_name, user_id)
            
            if not result['cheapest_item']:
                response = f"找不到 {product_name} 的相關主商品\n\n"
                response += f"{result.get('filter_info', '')}\n\n"
                response += "建議您：\n"
                response += "• 檢查商品名稱拼寫\n"
                response += "• 使用更通用的關鍵字\n"
                response += "• 嘗試品牌 + 型號的組合"
                
                return response
            
            cheapest = result['cheapest_item']
            
            response = f"{result['product_name']} 比價結果：\n\n"
            response += f"最佳選擇：\n"
            response += f"商品：{cheapest['name']}\n"
            response += f"價格：NT${cheapest['price']:,}\n"
            response += f"平台：{cheapest['platform']}\n"
            response += f"購買連結：{cheapest['link']}\n\n"
            
            if result['total_results'] > 1:
                response += f"價格統計（{result['total_results']} 個相關商品）：\n"
                response += f"最低價：NT${result['min_price']:,}\n"
                response += f"最高價：NT${result['max_price']:,}\n"
                response += f"平均價：NT${result['avg_price']:,.0f}\n\n"
            
            other_choices = result['all_results'][1:3]
            if other_choices:
                response += f"其他優質選擇：\n"
                for i, item in enumerate(other_choices, 2):
                    response += f"{i}. {item['name'][:50]}{'...' if len(item['name']) > 50 else ''}\n"
                    response += f"   NT${item['price']:,} ({item['platform']})\n"
                
                response += "\n"
            
            response += f"想追蹤此商品？輸入：\n"
            response += f"「{product_name} 低於 {cheapest['price']} 元時通知我」\n\n"
            response += f"{result.get('filter_info', '已自動過濾不相關商品')}"
            
            return response
            
        except Exception as e:
            logger.error(f"查詢價格失敗: {e}")
            return f"查詢 {product_name} 價格時發生錯誤，請稍後再試"
    
    def handle_track_product(self, user_id: str, intent: Dict) -> str:
        """處理商品追蹤"""
        try:
            if intent['action'] == 'track_product_need_price':
                return (
                    "請告訴我您想追蹤的目標價格\n\n"
                    "範例格式：\n"
                    "• iPhone 15 低於 25000 元時通知我\n"
                    "• 當 PS5 價格少於 12000 就提醒我\n\n"
                    "系統會自動過濾配件，只追蹤主商品價格！"
                )
            
            product_name = intent['product_name']
            target_price = float(intent['target_price'])
            
            if user_id not in self.user_trackers:
                self.user_trackers[user_id] = []
                if self.db_manager:
                    self.user_trackers[user_id] = self.db_manager.load_user_trackers(user_id)
            
            existing_tracker = None
            for tracker in self.user_trackers[user_id]:
                if tracker.product_name.lower() == product_name.lower():
                    existing_tracker = tracker
                    break
            
            if existing_tracker:
                old_price = existing_tracker.target_price
                existing_tracker.target_price = target_price
                existing_tracker.is_active = True
                existing_tracker.created_at = datetime.now()
                
                if self.db_manager:
                    cursor = self.db_manager.connection.cursor()
                    try:
                        if self.db_manager.database_type == "postgresql":
                            cursor.execute("""
                                UPDATE price_trackers 
                                SET target_price = %s, is_active = %s, created_at = %s
                                WHERE id = %s
                            """, (target_price, True, datetime.now(), existing_tracker.tracker_id))
                        else:
                            cursor.execute("""
                                UPDATE price_trackers 
                                SET target_price = ?, is_active = ?, created_at = ?
                                WHERE id = ?
                            """, (target_price, True, datetime.now(), existing_tracker.tracker_id))
                        
                        self.db_manager.connection.commit()
                    except Exception as e:
                        logger.error(f"更新資料庫失敗: {e}")
                
                response = f"追蹤設定已更新！\n\n"
                response += f"商品：{product_name}\n"
                response += f"價格調整：NT${old_price:,} → NT${target_price:,}\n"
                response += f"追蹤模式：低於目標價格時通知\n\n"
            else:
                new_tracker = PriceTracker(
                    user_id=user_id,
                    product_name=product_name,
                    target_price=target_price,
                    platforms=['all'],
                    created_at=datetime.now(),
                    track_mode=intent.get('track_mode', 'below_price')
                )
                
                if self.db_manager:
                    tracker_id = self.db_manager.save_tracker(new_tracker)
                    new_tracker.tracker_id = tracker_id
                
                self.user_trackers[user_id].append(new_tracker)
                
                response = f"追蹤設定成功！\n\n"
                response += f"商品：{product_name}\n"
                response += f"目標價格：NT${target_price:,} 以下\n"
                response += f"通知模式：低於目標價格時立即通知\n\n"
            
            current_result = self.price_agent.search_comprehensive_prices(product_name, user_id)
            if current_result and current_result.get('cheapest_item'):
                current_price = current_result['min_price']
                
                tracker = existing_tracker or new_tracker
                tracker.last_price = current_price
                tracker.last_checked = datetime.now()
                
                if self.db_manager and tracker.tracker_id:
                    self.db_manager.update_tracker_price(tracker.tracker_id, current_price)
                
                if current_price <= target_price:
                    response += f"好消息！已找到符合條件的商品：\n"
                    response += f"當前最低價：NT${current_price:,}\n"
                    response += f"節省金額：NT${target_price - current_price:,}\n"
                    response += f"最佳平台：{current_result['cheapest_item']['platform']}\n"
                    response += f"立即購買：{current_result['cheapest_item']['link']}\n\n"
                    response += f"限時優惠，建議立即下單！"
                else:
                    response += f"當前價格分析：\n"
                    response += f"目前最低價：NT${current_price:,}\n"
                    response += f"距離目標：NT${current_price - target_price:,}\n"
                    response += f"需要降價：{((current_price - target_price) / current_price * 100):.1f}%\n\n"
                    response += f"持續監控中，降價時立即通知您！"
                
                response += f"\n\n{current_result.get('filter_info', '已自動過濾配件和不相關商品')}"
            else:
                response += f"追蹤已啟動，正在收集價格資訊..."
            
            return response
            
        except ValueError:
            return "價格格式錯誤，請輸入有效的數字金額"
        except Exception as e:
            logger.error(f"設定追蹤失敗: {e}")
            return "設定商品追蹤時發生錯誤，請稍後再試"
    
    def handle_user_settings(self, user_id: str, message: str) -> str:
        """處理用戶設定"""
        try:
            response = "用戶偏好設定\n\n"
            
            if '允許配件' in message or '包含配件' in message:
                if self.db_manager:
                    cursor = self.db_manager.connection.cursor()
                    try:
                        if self.db_manager.database_type == "postgresql":
                            cursor.execute("""
                                INSERT INTO user_preferences (user_id, allow_accessories)
                                VALUES (%s, %s)
                                ON CONFLICT (user_id) 
                                DO UPDATE SET allow_accessories = EXCLUDED.allow_accessories
                            """, (user_id, True))
                        else:
                            cursor.execute("""
                                INSERT OR REPLACE INTO user_preferences 
                                (user_id, allow_accessories)
                                VALUES (?, ?)
                            """, (user_id, True))
                        
                        self.db_manager.connection.commit()
                        response += "已設定為允許搜尋配件商品\n\n"
                    except Exception as e:
                        logger.error(f"更新偏好失敗: {e}")
                        response += "設定更新失敗\n\n"
            
            elif '不要配件' in message or '過濾配件' in message or '排除配件' in message:
                if self.db_manager:
                    cursor = self.db_manager.connection.cursor()
                    try:
                        if self.db_manager.database_type == "postgresql":
                            cursor.execute("""
                                INSERT INTO user_preferences (user_id, allow_accessories)
                                VALUES (%s, %s)
                                ON CONFLICT (user_id) 
                                DO UPDATE SET allow_accessories = EXCLUDED.allow_accessories
                            """, (user_id, False))
                        else:
                            cursor.execute("""
                                INSERT OR REPLACE INTO user_preferences 
                                (user_id, allow_accessories)
                                VALUES (?, ?)
                            """, (user_id, False))
                        
                        self.db_manager.connection.commit()
                        response += "已設定為自動過濾配件商品\n\n"
                    except Exception as e:
                        logger.error(f"更新偏好失敗: {e}")
                        response += "設定更新失敗\n\n"
            
            if self.db_manager:
                prefs = self.db_manager.get_user_preferences(user_id)
                response += f"當前設定：\n"
                response += f"• 配件過濾：{'關閉（允許配件）' if prefs['allow_accessories'] else '開啟（只顯示主商品）'}\n\n"
            
            response += "可用設定指令：\n"
            response += "• 「允許配件」- 搜尋結果包含配件\n"
            response += "• 「過濾配件」- 只顯示主商品（推薦）"
            
            return response
            
        except Exception as e:
            logger.error(f"處理設定失敗: {e}")
            return "處理設定時發生錯誤"
    
    def handle_list_trackers(self, user_id: str) -> str:
        """處理查看追蹤清單"""
        try:
            if self.db_manager:
                self.user_trackers[user_id] = self.db_manager.load_user_trackers(user_id)
            
            if user_id not in self.user_trackers or not self.user_trackers[user_id]:
                return (
                    "您目前沒有任何商品追蹤\n\n"
                    "開始追蹤範例：\n"
                    "• iPhone 15 低於 25000 元時通知我\n"
                    "• 當 PS5 價格少於 12000 就提醒我\n\n"
                    "系統會自動過濾配件，只追蹤主商品！"
                )
            
            active_trackers = [t for t in self.user_trackers[user_id] if t.is_active]
            
            response = f"您的商品追蹤清單\n"
            response += f"更新時間：{datetime.now().strftime('%m/%d %H:%M')}\n\n"
            
            if active_trackers:
                response += f"進行中追蹤 ({len(active_trackers)} 項)：\n\n"
                
                for i, tracker in enumerate(active_trackers, 1):
                    response += f"#{i} {tracker.product_name}\n"
                    response += f"   目標：NT${tracker.target_price:,} 以下\n"
                    
                    if tracker.last_price:
                        if tracker.last_price <= tracker.target_price:
                            response += f"   當前：NT${tracker.last_price:,} (已達標！)\n"
                        else:
                            diff = tracker.last_price - tracker.target_price
                            response += f"   當前：NT${tracker.last_price:,} (還需降 NT${diff:,})\n"
                    else:
                        response += f"   狀態：等待價格檢查中...\n"
                    
                    if tracker.last_checked:
                        response += f"   更新：{tracker.last_checked.strftime('%m/%d %H:%M')}\n"
                    
                    response += "\n"
            
            response += "想修改追蹤設定？重新輸入相同商品名稱即可更新"
            
            return response
            
        except Exception as e:
            logger.error(f"查詢追蹤清單失敗: {e}")
             price_trackers 
                    SET last_price = %s, last_checked = %s
                    WHERE id = %s
                """, (current_price, datetime.now(), tracker_id))
            else:
                cursor.execute("""
                    UPDATE
