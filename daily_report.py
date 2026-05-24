#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日早报自动生成与发送脚本
使用 RSS 源采集信息，生成 HTML 邮件并发送
"""

import feedparser
import smtplib
import hashlib
import os
import re
from html import escape, unescape
from email.mime.text import MIMEText
from email.header import Header
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import List, Dict, Optional, Tuple
from zoneinfo import ZoneInfo
import requests

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def now_beijing() -> datetime:
    """当前北京时间（邮件标题、日期展示统一使用）"""
    return datetime.now(BEIJING_TZ)

# --- 配置区域 ---
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
MODEL_NAME = os.environ.get("DASHSCOPE_MODEL", "deepseek-v3")
# 每条早报最多生成多少条 AI 摘要（避免 Actions 超时）
MAX_AI_SUMMARIES = 50
# 自选股行业分析（独立调用，不计入新闻摘要上限）
ENABLE_STOCK_AI_ANALYSIS = os.environ.get("ENABLE_STOCK_AI_ANALYSIS", "1") != "0"
# ---------------

def strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def get_entry_content(entry) -> str:
    if entry.get("summary"):
        return strip_html(entry.summary)
    if entry.get("description"):
        return strip_html(entry.description)
    if entry.get("content"):
        return strip_html(entry.content[0].get("value", ""))
    return ""


def call_dashscope(prompt: str, timeout: int = 60) -> str:
    """调用阿里云百炼 Chat Completions"""
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}]}
    response = requests.post(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def get_ai_summary(title: str, content: str) -> str:
    if not API_KEY:
        return "（未配置 DASHSCOPE_API_KEY，跳过摘要）"

    if content and len(content) >= 50:
        prompt = f"""请为下面的新闻写一段中文摘要，要求简洁、客观、信息完整，控制在100字以内。

标题：{title}
新闻内容：{content[:2000]}
摘要："""
    else:
        prompt = f"""请根据以下新闻标题推断并写一段中文摘要，要求简洁、客观，控制在80字以内。

标题：{title}
摘要："""
    try:
        return call_dashscope(prompt, timeout=60)
    except Exception as e:
        print(f"AI 摘要生成失败 [{title[:30]}...]: {e}")
        return "（摘要生成失败）"

# ================== 配置区域（请修改成你自己的）==================
# 邮箱配置
SMTP_SERVER = "smtp.qq.com"      # QQ邮箱SMTP服务器，163邮箱用 smtp.163.com
SMTP_PORT = 465                   # SSL端口
SENDER_EMAIL = "380972017@qq.com"     # 你的邮箱地址
SENDER_PASSWORD = os.environ.get('EMAIL_PASSWORD', '你的默认授权码(仅本地测试用)')  # 第2步拿到的授权码
RECEIVER_EMAIL = "380972017@qq.com"     # 接收早报的邮箱（可以是同一个）

# RSS 源列表（可以自己增删）
RSS_SOURCES = {
    "AI/科技": [
        "https://blog.google/technology/ai/rss/",                      # Hacker News
        "https://openai.com/news/rss.xml",                       # OpenAI
        "https://ai.meta.com/blog/feed/",                        # Meta AI
        "https://machinelearning.apple.com/rss/",                # Apple ML
        "https://feeds.reuters.com/reuters/technologyNews",      # 路透科技
        "https://www.microsoft.com/en-us/research/feed/",        #微博研究院博客
        "https://blogs.nvidia.com/feed/",                         #英伟达博客   
        "https://www.anthropic.com/news/feed.xml",                #Anthropic博客
    ],
    "金融财经": [
        "https://feeds.reuters.com/reuters/businessNews",        # 路透商业
        "https://www.bloomberg.com/feed/podcast/technology.xml", # 彭博科技
        "https://feeds.a.dj.com/rss/RSSWSJBusiness.xml",         # 华尔街日报商业
        "https://www.ft.com/?format=rss",                        # 金融时报
    ],
    "国际军事": [
        "http://www.people.com.cn/rss/military.xml",              # 人民网军事
        "http://www.people.com.cn/rss/world.xml",                 # 人民网国际
        "https://www.aljazeera.com/xml/rss/all.xml",             # 半岛电视台
        "http://feeds.bbci.co.uk/news/world/rss.xml",            # 英国广播公司世界新闻
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", # 纽约时报世界新闻
        "https://www.theguardian.com/world/rss",                   # 卫报世界新闻
        "https://www.defensenews.com/arc/outboundfeeds/rss/",      # 国防新闻
    ]
}

# 每天最多保留多少条新闻（防止邮件太长）
MAX_ITEMS_PER_SOURCE = 10
# ================================================================

def get_article_id(entry) -> str:
    """生成文章唯一ID，用于去重（链接 + 标题）"""
    unique_str = entry.get("link", "") + entry.get("title", "")
    return hashlib.md5(unique_str.encode("utf-8")).hexdigest()


def get_entry_beijing_date(entry) -> Optional[date]:
    """解析 RSS 条目发布时间，转为北京时间日期"""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        dt_utc = datetime(*parsed[:6], tzinfo=timezone.utc)
        return dt_utc.astimezone(BEIJING_TZ).date()
    except (ValueError, TypeError):
        return None


def is_entry_today(entry, today: Optional[date] = None) -> bool:
    """是否为北京时间当日的文章"""
    entry_date = get_entry_beijing_date(entry)
    if entry_date is None:
        return False
    if today is None:
        today = now_beijing().date()
    return entry_date == today

def load_sent_ids(state_file: str = "sent_ids.txt") -> set:
    """加载历史已发送的文章ID"""
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            return set(line.strip() for line in f)
    return set()

def save_sent_ids(sent_ids: set, state_file: str = "sent_ids.txt"):
    """保存本次发送的文章ID"""
    with open(state_file, 'w') as f:
        for aid in sent_ids:
            f.write(aid + '\n')

def fetch_news(ignore_sent: bool = False) -> Tuple[Dict[str, List[Dict]], set]:
    """从所有 RSS 源抓取新闻：仅当日（北京时间）、跨源去重、跳过历史已推送"""
    all_news = {category: [] for category in RSS_SOURCES}
    sent_ids = set() if ignore_sent else load_sent_ids()
    seen_ids: set = set()  # 本次采集跨分类、跨源去重
    new_ids: set = set()
    today = now_beijing().date()
    stats = defaultdict(int)

    for category, feeds in RSS_SOURCES.items():
        for feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url)
                per_source_count = 0
                for entry in feed.entries:
                    if per_source_count >= MAX_ITEMS_PER_SOURCE:
                        break
                    if not is_entry_today(entry, today):
                        stats["not_today"] += 1
                        continue
                    article_id = get_article_id(entry)
                    if article_id in seen_ids:
                        stats["duplicate"] += 1
                        continue
                    if not ignore_sent and article_id in sent_ids:
                        stats["already_sent"] += 1
                        continue
                    seen_ids.add(article_id)
                    new_ids.add(article_id)
                    per_source_count += 1
                    entry_date = get_entry_beijing_date(entry)
                    published_display = entry.get("published") or (
                        entry_date.strftime("%Y-%m-%d") if entry_date else "日期未知"
                    )
                    all_news[category].append({
                        "title": entry.get("title", "无标题"),
                        "link": entry.get("link", "#"),
                        "published": published_display,
                        "content": get_entry_content(entry),
                    })
            except Exception as e:
                print(f"读取RSS失败 {feed_url}: {e}")
                continue

    print(
        f"过滤统计（北京时间 {today}）："
        f" 非当日 {stats['not_today']} 条，"
        f"历史已推送 {stats['already_sent']} 条，"
        f"跨源重复 {stats['duplicate']} 条"
    )
    return all_news, new_ids

def get_watchlist(watchlist_file: str = "watchlist.txt"):
    if os.path.exists(watchlist_file):
        with open(watchlist_file, "r", encoding="utf-8") as f:
            codes = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
            if codes:
                return codes
    return ["sh000985", "sh516650", "sh517520", "sh513330"]


def format_tencent_code(code: str) -> str:
    """将股票代码转为腾讯行情接口所需格式（如 sh600519、sz000001）"""
    c = code.strip().lower()
    if c.startswith(("sh", "sz", "hk", "bj")):
        return c
    if c.startswith(("5", "6", "9")):
        return f"sh{c}"
    if len(c) == 6 and c.startswith("000"):
        return f"sh{c}"  # 上证综指、中证全指等指数
    if c.startswith(("0", "1", "2", "3")):
        return f"sz{c}"
    return f"sh{c}"


def _tencent_field_float(fields: List[str], index: int) -> Optional[float]:
    if index >= len(fields):
        return None
    raw = fields[index].strip()
    if not raw or raw in ('-', '0', '0.00'):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _infer_instrument_kind(symbol: str, name: str) -> str:
    code = symbol.lower().replace("sh", "").replace("sz", "")
    name_u = name.upper()
    if "ETF" in name_u or "基金" in name or "LOF" in name_u:
        return "ETF"
    if code.startswith(("000", "399")) or "指数" in name or "综指" in name or "全指" in name:
        return "指数"
    if code.startswith(("51", "15", "16", "56", "58")):
        return "ETF"
    return "股票"


def fetch_stock_quotes(stock_codes):
    """调用腾讯财经接口，附带估值等字段（ETF/指数部分字段可能为空）"""
    results = {}
    if not stock_codes:
        return results
    symbols = [format_tencent_code(c) for c in stock_codes]
    url = f"https://web.sqt.gtimg.cn/q={','.join(symbols)}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = "gbk"
        data_lines = response.text.strip().split(";")
        for line in data_lines:
            if not line:
                continue
            eq = line.find('="')
            if eq == -1:
                continue
            payload = line[eq + 2 :].strip('"')
            fields = payload.split("~")
            if len(fields) <= 32:
                continue
            symbol = fields[2]
            results[symbol] = {
                "name": fields[1],
                "price": float(fields[3]),
                "change_percent": float(fields[32]),
                "pe": _tencent_field_float(fields, 39),
                "pb": _tencent_field_float(fields, 46),
                "turnover_rate": _tencent_field_float(fields, 38),
                "market_cap_yi": _tencent_field_float(fields, 45),
                "kind": _infer_instrument_kind(symbol, fields[1]),
                "analysis": "",
            }
    except Exception as e:
        print(f"获取行情失败: {e}")
    return results


def _fmt_metric(value: Optional[float], suffix: str = "") -> str:
    if value is None:
        return "暂无"
    return f"{value:.2f}{suffix}"


def _build_stock_analysis_prompt(stock_data: Dict[str, dict]) -> str:
    lines = []
    for symbol, q in stock_data.items():
        lines.append(
            f"- {q['name']}（{symbol}，类型：{q.get('kind', '未知')}）"
            f" 现价 {q['price']:.3f}，涨跌幅 {q['change_percent']:+.2f}%"
            f"，市盈率 {_fmt_metric(q.get('pe'))}，市净率 {_fmt_metric(q.get('pb'))}"
            f"，换手率 {_fmt_metric(q.get('turnover_rate'), '%')}"
            f"，总市值 {_fmt_metric(q.get('market_cap_yi'), ' 亿元')}"
        )
    today = now_beijing().strftime("%Y-%m-%d")
    symbols_hint = "、".join(stock_data.keys())
    return f"""你是资深的 A 股与 ETF 行业研究分析师。请根据以下自选股今日行情（数据日期：{today}），对每只标的的**所属行业或跟踪板块**做研究性分析。

【行情数据】
{chr(10).join(lines)}

【输出格式】（必须覆盖全部标的：{symbols_hint}）
对每一只标的单独输出一个区块，标题行格式固定为：
### 【股票代码】标的名称

区块内须包含且仅包含以下四行（每行以加粗标签开头）：
**行业/板块：**
**估值水平：**
**走势展望：**
**主要风险：**

要求：
1. 股票按所属申万/中信一级行业分析；ETF、指数请分析其跟踪指数或重仓行业，勿当成单一公司。
2. 结合市盈率、市净率或同类板块做估值判断；字段为「暂无」时做定性说明并注明数据缺失。
3. 走势展望写短期至中期，语气客观中性；风险列 2～4 条要点。
4. 单区块总字数 120～180 字；全文勿荐股、勿保证收益。"""


def _normalize_symbol_code(code: str) -> str:
    c = code.strip().lower()
    if c.startswith(("sh", "sz", "bj", "hk")):
        return c[2:]
    return c


def _parse_stock_analysis_blocks(text: str, stock_data: Dict[str, dict]) -> None:
    """将模型输出按 ### 【代码】 拆分到各标的 analysis 字段"""
    pattern = re.compile(
        r"###\s*【?([a-zA-Z0-9]+)】?\s*[^\n]*\n(.*?)(?=\n###\s*|\Z)",
        re.DOTALL,
    )
    symbol_map = {_normalize_symbol_code(s): s for s in stock_data}
    matched = set()
    for code, body in pattern.findall(text):
        key = symbol_map.get(_normalize_symbol_code(code))
        if key:
            stock_data[key]["analysis"] = body.strip()
            matched.add(key)
    if matched:
        return
    # 解析失败：整段作为共用分析
    fallback = text.strip() or "（未能解析行业分析结构）"
    for symbol in stock_data:
        stock_data[symbol]["analysis"] = fallback


def enrich_stocks_with_analysis(stock_data: Dict[str, dict]) -> None:
    """为自选股生成行业/估值/展望/风险分析（原地修改）"""
    if not stock_data:
        return
    if not ENABLE_STOCK_AI_ANALYSIS:
        for q in stock_data.values():
            q["analysis"] = "（已关闭自选股 AI 分析）"
        return
    if not API_KEY:
        for q in stock_data.values():
            q["analysis"] = "（未配置 DASHSCOPE_API_KEY，暂无行业分析）"
        return

    print("生成自选股行业分析...")
    try:
        raw = call_dashscope(_build_stock_analysis_prompt(stock_data), timeout=120)
        _parse_stock_analysis_blocks(raw, stock_data)
    except Exception as e:
        print(f"自选股行业分析失败: {e}")
        msg = "（行业分析生成失败，请稍后重试）"
        for q in stock_data.values():
            q["analysis"] = msg

    for symbol, q in stock_data.items():
        if not q.get("analysis"):
            q["analysis"] = "（未生成该标的的分析内容）"


def _analysis_to_html(text: str) -> str:
    """将分析文本转为安全 HTML"""
    parts = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"\*\*(.+?)\*\*:?\s*(.*)", line)
        if m:
            label, rest = m.group(1), m.group(2)
            rest_html = escape(rest) if rest else ""
            parts.append(
                f"<div class='stock-analysis-line'><strong>{escape(label)}</strong>"
                f"{(': ' + rest_html) if rest_html else ''}</div>"
            )
        else:
            parts.append(f"<div class='stock-analysis-line'>{escape(line)}</div>")
    return "".join(parts) if parts else escape(text)

def enrich_news_with_summaries(news_dict: Dict[str, List[Dict]]) -> None:
    """为新闻条目生成 AI 摘要（原地修改）"""
    ai_count = 0
    for items in news_dict.values():
        for item in items:
            if ai_count >= MAX_AI_SUMMARIES:
                item["ai_summary"] = "（已达今日 AI 摘要上限，仅显示标题）"
                continue
            print(f"生成摘要: {item['title'][:40]}...")
            item["ai_summary"] = get_ai_summary(item["title"], item.get("content", ""))
            ai_count += 1


def render_stock_section(stock_data: Dict[str, dict]) -> str:
    if not stock_data:
        return "<p>（未能获取自选股行情，请检查 watchlist.txt 或网络）</p>"
    rows = ""
    cards = ""
    for symbol, q in stock_data.items():
        pct = q["change_percent"]
        color = "#e74c3c" if pct > 0 else "#27ae60" if pct < 0 else "#7f8c8d"
        sign = "+" if pct > 0 else ""
        pe = _fmt_metric(q.get("pe"))
        pb = _fmt_metric(q.get("pb"))
        kind = escape(q.get("kind", ""))
        rows += f"""
        <tr>
            <td>{escape(q['name'])} ({symbol})<br><span class="stock-kind">{kind}</span></td>
            <td style="text-align:right">{q['price']:.3f}</td>
            <td style="text-align:right;color:{color}">{sign}{pct:.2f}%</td>
            <td style="text-align:right">{pe}</td>
            <td style="text-align:right">{pb}</td>
        </tr>"""
        analysis_html = _analysis_to_html(q.get("analysis", ""))
        cards += f"""
        <div class="stock-card">
            <div class="stock-card-title">{escape(q['name'])} ({symbol})</div>
            <div class="stock-card-analysis">{analysis_html}</div>
        </div>"""
    return f"""
    <table class="stock-table">
        <thead><tr><th>名称</th><th>现价</th><th>涨跌幅</th><th>市盈率</th><th>市净率</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
    <p class="stock-disclaimer">以下行业分析由 AI 根据公开行情与常识生成，仅供参考，不构成投资建议。</p>
    <h3 class="stock-subtitle">行业与估值分析</h3>
    {cards}"""


def generate_html(news_dict: Dict[str, List[Dict]], stock_data: Dict[str, dict]) -> str:
    """生成漂亮的 HTML 邮件内容"""
    today = now_beijing().strftime("%Y年%m月%d日")
    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 20px; background: #f4f4f4; }}
            .container {{ max-width: 800px; margin: auto; background: white; padding: 20px; border-radius: 10px; }}
            h1 {{ color: #2c3e50; border-left: 5px solid #3498db; padding-left: 15px; }}
            h2 {{ color: #e67e22; margin-top: 25px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
            .stock-table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
            .stock-table th, .stock-table td {{ padding: 8px 12px; border-bottom: 1px solid #eee; }}
            .stock-table th {{ background: #f8f9fa; text-align: left; color: #555; }}
            .stock-kind {{ font-size: 11px; color: #95a5a6; }}
            .stock-subtitle {{ font-size: 15px; color: #34495e; margin: 18px 0 8px; }}
            .stock-disclaimer {{ font-size: 12px; color: #95a5a6; margin: 12px 0 0; }}
            .stock-card {{ margin: 12px 0; padding: 12px 14px; background: #f8fafc; border-left: 4px solid #3498db; border-radius: 4px; }}
            .stock-card-title {{ font-weight: bold; color: #2c3e50; margin-bottom: 8px; }}
            .stock-card-analysis {{ font-size: 13px; color: #34495e; line-height: 1.55; }}
            .stock-analysis-line {{ margin: 4px 0; }}
            .news-item {{ margin: 15px 0; padding: 10px; background: #fafafa; border-radius: 5px; }}
            .news-title {{ font-size: 16px; font-weight: bold; }}
            .news-title a {{ color: #2980b9; text-decoration: none; }}
            .news-title a:hover {{ text-decoration: underline; }}
            .news-summary {{ font-size: 14px; color: #34495e; margin-top: 8px; line-height: 1.5; }}
            .news-meta {{ font-size: 12px; color: #7f8c8d; margin-top: 5px; }}
            .footer {{ margin-top: 30px; text-align: center; font-size: 12px; color: #95a5a6; }}
        </style>
    </head>
    <body>
    <div class="container">
        <h1>📰 每日资讯早报 - {today}</h1>
        <h2>📈 自选股行情与行业分析</h2>
        {render_stock_section(stock_data)}
    """
    total_count = 0
    for category, items in news_dict.items():
        if not items:
            continue
        total_count += len(items)
        html += f"<h2>🔹 {category}</h2>"
        for item in items:
            summary = item.get("ai_summary", "")
            summary_html = f'<div class="news-summary">📝 {summary}</div>' if summary else ""
            html += f"""
            <div class="news-item">
                <div class="news-title">
                    <a href="{item['link']}" target="_blank">{item['title']}</a>
                </div>
                {summary_html}
                <div class="news-meta">📅 {item['published']}</div>
            </div>
            """
    if total_count == 0:
        html += "<p>今天（北京时间）暂无新的待推送新闻，可能已全部发送或 RSS 尚无当日更新。</p>"
    html += f"""
        <div class="footer">
            此邮件由您的AI早报系统自动生成 | 数据采集时间: {now_beijing().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)
        </div>
    </div>
    </body>
    </html>
    """
    return html

def send_email(html_content: str):
    """发送邮件"""
    subject = f"每日早报 - {now_beijing().strftime('%Y-%m-%d')}"
    msg = MIMEText(html_content, 'html', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, [RECEIVER_EMAIL], msg.as_string())
        print("邮件发送成功！")
    except Exception as e:
        print(f"邮件发送失败: {e}")

def main():
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="每日资讯早报")
    parser.add_argument(
        "--resend",
        action="store_true",
        help="忽略已发送记录，重新生成并推送（不更新 sent_ids.txt）",
    )
    args = parser.parse_args()

    if args.resend:
        print("重新推送模式：忽略 sent_ids 去重")

    print("开始采集新闻...")
    news_dict, new_ids = fetch_news(ignore_sent=args.resend)
    if not news_dict or not any(news_dict.values()):
        print("没有符合条件的当日新新闻，今日不发送邮件。")
        return
    print(f"采集到文章: {sum(len(v) for v in news_dict.values())} 条")

    print("获取自选股行情...")
    stock_data = fetch_stock_quotes(get_watchlist())
    print(f"行情数据: {len(stock_data)} 只")
    enrich_stocks_with_analysis(stock_data)

    print("生成 AI 摘要...")
    enrich_news_with_summaries(news_dict)

    html_content = generate_html(news_dict, stock_data)
    send_email(html_content)
    if args.resend:
        print("重新推送完成（未更新 sent_ids.txt）")
    else:
        old_ids = load_sent_ids()
        save_sent_ids(old_ids.union(new_ids))
        print("完成！")

if __name__ == "__main__":
    main()