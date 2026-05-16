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
from html import unescape
from email.mime.text import MIMEText
from email.header import Header
from datetime import datetime
from typing import List, Dict, Tuple
import requests

# --- 配置区域 ---
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
MODEL_NAME = os.environ.get("DASHSCOPE_MODEL", "deepseek-v3")
# 每条早报最多生成多少条 AI 摘要（避免 Actions 超时）
MAX_AI_SUMMARIES = 15
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
    # 调用阿里云百炼API
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}]}
    try:
        response = requests.post(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
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
        "https://news.ycombinator.com/rss",                      # Hacker News
        "https://openai.com/news/rss.xml",                       # OpenAI
        "https://ai.meta.com/blog/feed/",                        # Meta AI
        "https://machinelearning.apple.com/rss/",                # Apple ML
        "https://feeds.reuters.com/reuters/technologyNews",      # 路透科技
    ],
    "金融财经": [
        "https://feeds.reuters.com/reuters/businessNews",        # 路透商业
        "https://www.bloomberg.com/feed/podcast/technology.xml", # 彭博科技
    ],
    "国际军事": [
        "http://www.people.com.cn/rss/military.xml",              # 人民网军事
        "http://www.people.com.cn/rss/world.xml",                 # 人民网国际
        "https://www.aljazeera.com/xml/rss/all.xml",             # 半岛电视台
    ]
}

# 每天最多保留多少条新闻（防止邮件太长）
MAX_ITEMS_PER_SOURCE = 8
# ================================================================

def get_article_id(entry) -> str:
    """生成文章唯一ID，用于去重"""
    unique_str = entry.get('link', '') + entry.get('title', '')
    return hashlib.md5(unique_str.encode('utf-8')).hexdigest()

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

def fetch_news() -> Tuple[Dict[str, List[Dict]], set]:
    """从所有 RSS 源抓取新闻"""
    all_news = {}
    sent_ids = load_sent_ids()
    new_ids = set()

    for category, feeds in RSS_SOURCES.items():
        all_news[category] = []
        for feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:MAX_ITEMS_PER_SOURCE]:
                    article_id = get_article_id(entry)
                    if article_id in sent_ids:
                        continue  # 已经发过了，跳过
                    new_ids.add(article_id)
                    all_news[category].append({
                        "title": entry.get("title", "无标题"),
                        "link": entry.get("link", "#"),
                        "published": entry.get("published", "日期未知"),
                        "content": get_entry_content(entry),
                    })
            except Exception as e:
                print(f"读取RSS失败 {feed_url}: {e}")
                continue
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


def fetch_stock_quotes(stock_codes):
    # 调用腾讯财经接口
    results = {}
    if not stock_codes:
        return results
    symbols = [format_tencent_code(c) for c in stock_codes]
    url = f"https://web.sqt.gtimg.cn/q={','.join(symbols)}"
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = 'gbk'
        data_lines = response.text.strip().split(';')
        for line in data_lines:
            if not line: continue
            content = line.split('~')
            if len(content) > 37:
                symbol = content[2] # 股票代码
                results[symbol] = {
                    "name": content[1],
                    "price": float(content[3]),
                    "change_percent": float(content[32]) # 涨跌幅
                }
    except Exception as e:
        print(f"获取行情失败: {e}")
    return results

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
    for symbol, q in stock_data.items():
        pct = q["change_percent"]
        color = "#e74c3c" if pct > 0 else "#27ae60" if pct < 0 else "#7f8c8d"
        sign = "+" if pct > 0 else ""
        rows += f"""
        <tr>
            <td>{q['name']} ({symbol})</td>
            <td style="text-align:right">{q['price']:.3f}</td>
            <td style="text-align:right;color:{color}">{sign}{pct:.2f}%</td>
        </tr>"""
    return f"""
    <table class="stock-table">
        <thead><tr><th>名称</th><th>现价</th><th>涨跌幅</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>"""


def generate_html(news_dict: Dict[str, List[Dict]], stock_data: Dict[str, dict]) -> str:
    """生成漂亮的 HTML 邮件内容"""
    today = datetime.now().strftime("%Y年%m月%d日")
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
        <h2>📈 自选股行情</h2>
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
        html += "<p>今天没有找到新的新闻，请检查RSS源或稍后再试。</p>"
    html += f"""
        <div class="footer">
            此邮件由您的AI早报系统自动生成 | 数据采集时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        </div>
    </div>
    </body>
    </html>
    """
    return html

def send_email(html_content: str):
    """发送邮件"""
    subject = f"每日早报 - {datetime.now().strftime('%Y-%m-%d')}"
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
    print("开始采集新闻...")
    news_dict, new_ids = fetch_news()
    if not news_dict or not any(news_dict.values()):
        print("没有新新闻，今日不发送邮件。")
        return
    print(f"采集到新文章: {sum(len(v) for v in news_dict.values())} 条")

    print("获取自选股行情...")
    stock_data = fetch_stock_quotes(get_watchlist())
    print(f"行情数据: {len(stock_data)} 只")

    print("生成 AI 摘要...")
    enrich_news_with_summaries(news_dict)

    html_content = generate_html(news_dict, stock_data)
    send_email(html_content)
    # 保存已发送ID
    old_ids = load_sent_ids()
    all_ids = old_ids.union(new_ids)
    save_sent_ids(all_ids)
    print("完成！")

if __name__ == "__main__":
    main()