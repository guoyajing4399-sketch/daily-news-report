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
from email.mime.text import MIMEText
from email.header import Header
from datetime import datetime
from typing import List, Dict

# ================== 配置区域（请修改成你自己的）==================
# 邮箱配置
SMTP_SERVER = "smtp.qq.com"      # QQ邮箱SMTP服务器，163邮箱用 smtp.163.com
SMTP_PORT = 465                   # SSL端口
SENDER_EMAIL = "380972017@qq.com"     # 你的邮箱地址
SENDER_PASSWORD = "qiaxmcweqchnbhah"  # 第2步拿到的授权码
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

def fetch_news() -> Dict[str, List[Dict]]:
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
                        'title': entry.get('title', '无标题'),
                        'link': entry.get('link', '#'),
                        'published': entry.get('published', '日期未知')
                    })
            except Exception as e:
                print(f"读取RSS失败 {feed_url}: {e}")
                continue
    # 保存新发送的ID（实际会在发送后保存，这里先收集）
    return all_news, new_ids

def generate_html(news_dict: Dict[str, List[Dict]]) -> str:
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
            .news-item {{ margin: 15px 0; padding: 10px; background: #fafafa; border-radius: 5px; }}
            .news-title {{ font-size: 16px; font-weight: bold; }}
            .news-title a {{ color: #2980b9; text-decoration: none; }}
            .news-title a:hover {{ text-decoration: underline; }}
            .news-meta {{ font-size: 12px; color: #7f8c8d; margin-top: 5px; }}
            .footer {{ margin-top: 30px; text-align: center; font-size: 12px; color: #95a5a6; }}
        </style>
    </head>
    <body>
    <div class="container">
        <h1>📰 每日资讯早报 - {today}</h1>
    """
    total_count = 0
    for category, items in news_dict.items():
        if not items:
            continue
        total_count += len(items)
        html += f"<h2>🔹 {category}</h2>"
        for item in items:
            html += f"""
            <div class="news-item">
                <div class="news-title">
                    <a href="{item['link']}" target="_blank">{item['title']}</a>
                </div>
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
    html_content = generate_html(news_dict)
    send_email(html_content)
    # 保存已发送ID
    old_ids = load_sent_ids()
    all_ids = old_ids.union(new_ids)
    save_sent_ids(all_ids)
    print("完成！")

if __name__ == "__main__":
    main()